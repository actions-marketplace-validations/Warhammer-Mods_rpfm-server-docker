#!/usr/bin/env python3
"""Resolve stable upstream releases and update the narrowly scoped version pins."""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import tomllib
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
UPSTREAM = "Frodo45127/rpfm"
MANIFEST = "server/upstream.json"
SHA = re.compile(r"[0-9a-f]{40}")


def api(path):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "rpfm-server-updater"}
    if os.environ.get("GH_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GH_TOKEN"]
    request = urllib.request.Request("https://api.github.com/repos/" + UPSTREAM + path,
                                     headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def validate(state):
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", state["tag"])
    if not match or int(match[1]) < 5 or state["version"] != state["tag"][1:]:
        raise ValueError("Expected an RPFM Server stable v5+ semantic version")
    for key in ("rpfm_commit", "schema_commit"):
        if not SHA.fullmatch(state[key]):
            raise ValueError(f"Invalid {key}")
    return state


def resolve(fetch=api):
    release = fetch("/releases/latest")
    if release.get("draft") or release.get("prerelease"):
        raise ValueError("Refusing a draft or prerelease")
    tag = release["tag_name"]
    if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise ValueError("Unsupported stable release tag")
    # target_commitish can be 'master': resolve the TAG, never that field.
    commit = fetch("/commits/" + urllib.parse.quote(tag, safe=""))["sha"]
    if not SHA.fullmatch(commit):
        raise ValueError("Invalid upstream commit")
    schema = fetch("/contents/schemas?ref=" + commit)
    if schema.get("type") != "submodule":
        raise ValueError("Upstream schema layout changed; review required")
    cargo = fetch("/contents/Cargo.toml?ref=" + commit)
    version = tomllib.loads(base64.b64decode(cargo["content"]).decode())["workspace"]["package"]["version"]
    # Ensure this release still contains the backend we build.
    fetch("/contents/rpfm_server/Cargo.toml?ref=" + commit)
    return validate({"version": version, "tag": tag,
                     "rpfm_commit": commit, "schema_commit": schema["sha"]})


def replace_exact(text, old, new, count=1):
    if text.count(old) != count:
        raise ValueError(f"Expected {count} copies of {old!r}; review changed file layout")
    return text.replace(old, new)


def apply_update(root, current, candidate):
    """Prepare every replacement before writing; a layout error leaves files untouched."""
    replacements = {}
    docker = (root / "server/Dockerfile").read_text()
    docker = replace_exact(docker, "ARG RPFM_COMMIT=" + current["rpfm_commit"],
                           "ARG RPFM_COMMIT=" + candidate["rpfm_commit"], 2)
    docker = replace_exact(docker, "ARG SCHEMA_COMMIT=" + current["schema_commit"],
                           "ARG SCHEMA_COMMIT=" + candidate["schema_commit"])
    docker = replace_exact(docker, 'image.version="' + current["version"] + '"',
                           'image.version="' + candidate["version"] + '"')
    replacements["server/Dockerfile"] = docker
    compose = (root / "server/compose.yaml").read_text()
    replacements["server/compose.yaml"] = replace_exact(
        compose, "local/rpfm-server:" + current["version"], "local/rpfm-server:" + candidate["version"])
    smoke = (root / "server/smoke_test.py").read_text()
    replacements["server/smoke_test.py"] = replace_exact(
        smoke, 'default="' + current["version"] + '"', 'default="' + candidate["version"] + '"')
    # README examples track the first Docker action release of each RPFM version.
    # A pin-only update within the same version retains the current server revision.
    for name, action in (("README.md", "Warhammer-Mods/rpfm-server-docker"),
                         ("server/README.md", "Warhammer-Mods/rpfm-server-docker/server")):
        text = (root / name).read_text()
        pattern = re.compile(re.escape(action) + r"@v" + re.escape(current["version"])
                             + r"-server\.([1-9][0-9]*)(?![\w.-])")
        matches = list(pattern.finditer(text))
        if len(matches) != 1:
            raise ValueError(f"Expected one current release example in {name}; review changed file layout")
        revision = matches[0][1] if candidate["version"] == current["version"] else "1"
        ref = action + "@v" + candidate["version"] + "-server." + revision
        replacements[name] = pattern.sub(lambda match: ref, text)
    replacements[MANIFEST] = json.dumps(candidate, indent=2) + "\n"
    for name, content in replacements.items():
        (root / name).write_text(content)


def outputs(values, filename):
    if filename:
        with open(filename, "a") as stream:
            for key, value in values.items():
                stream.write(f"{key}={value}\n")
    print(json.dumps(values, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["version", "check"])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args()
    current = validate(json.loads((ROOT / MANIFEST).read_text()))
    if args.command == "version":
        outputs({"version": current["version"]}, args.output)
        return
    candidate = resolve()
    if tuple(map(int, candidate["version"].split("."))) < tuple(map(int, current["version"].split("."))):
        raise ValueError("Refusing to downgrade the pinned release")
    changed = candidate != current
    if changed and args.apply:
        apply_update(ROOT, current, candidate)
    outputs({**candidate, "changed": str(changed).lower()}, args.output)


if __name__ == "__main__":
    main()
