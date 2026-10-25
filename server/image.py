#!/usr/bin/env python3
"""Bind a public release's OCI digest to this action's bundled build inputs."""
import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
ASSET = "rpfm-image.json"
RELEASE_REPO = "Warhammer-Mods/rpfm-server-docker"
BUILD_INPUTS = ("Dockerfile", "entrypoint.sh", ".dockerignore", "upstream.json")


def metadata(root=ROOT):
    config = json.loads((root / "image.json").read_text())
    upstream = json.loads((root / "upstream.json").read_text())
    if config["repository"] != "ghcr.io/warhammer-mods/rpfm-server-docker":
        raise ValueError("Unexpected image repository")
    match = re.fullmatch(r"v(\d+\.\d+\.\d+)-server\.([1-9][0-9]*)", config["release"])
    if not match or match[1] != upstream["version"]:
        raise ValueError("Image release does not match the bundled RPFM version")
    if config["platform"] != "linux/amd64":
        raise ValueError("Unsupported prebuilt image platform")
    return config, upstream


def fingerprint(root=ROOT):
    # Normalize Git's Windows checkout line endings. All build inputs are text.
    digest = hashlib.sha256()
    for name in BUILD_INPUTS:
        data = (root / name).read_bytes().replace(b"\r\n", b"\n")
        digest.update(name.encode() + b"\0" + str(len(data)).encode() + b"\0" + data)
    return digest.hexdigest()


def make_lock(digest, root=ROOT):
    config, upstream = metadata(root)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("Expected an OCI sha256 digest")
    return {"schema": 1, **config, "digest": digest, "context_sha256": fingerprint(root),
            "version": upstream["version"], "rpfm_commit": upstream["rpfm_commit"],
            "schema_commit": upstream["schema_commit"]}


def resolve(lock, root=ROOT):
    expected = make_lock(lock["digest"], root)
    if lock != expected:
        raise ValueError("Release image lock does not match this action's build inputs")
    return lock["repository"] + "@" + lock["digest"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["url", "resolve", "lock", "plan"])
    parser.add_argument("--lock", type=Path)
    parser.add_argument("--digest")
    parser.add_argument("--release")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    config, upstream = metadata()
    if args.command == "url":
        print(f"https://github.com/{RELEASE_REPO}/releases/download/{config['release']}/{ASSET}")
    elif args.command == "resolve":
        if args.lock is None:
            parser.error("resolve requires --lock")
        print(resolve(json.loads(args.lock.read_text())))
    elif args.command == "lock":
        if args.digest is None or args.output is None:
            parser.error("lock requires --digest and --output")
        args.output.write_text(json.dumps(make_lock(args.digest), indent=2) + "\n", newline="\n")
    else:
        if args.release != config["release"]:
            parser.error("Release tag must match server/image.json; use a new server revision for changes")
        values = {**config, "version": upstream["version"], "fingerprint": fingerprint(),
                  "tag": config["release"], "image_tag": config["release"][1:]}
        if args.output:
            with args.output.open("a", newline="\n") as stream:
                for key, value in values.items():
                    stream.write(f"{key}={value}\n")
        print(json.dumps(values, indent=2))


if __name__ == "__main__":
    main()
