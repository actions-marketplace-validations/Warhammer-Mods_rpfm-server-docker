import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SERVER = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("image", SERVER / "image.py")
image = importlib.util.module_from_spec(spec)
spec.loader.exec_module(image)
DIGEST = "sha256:" + "a" * 64


class ImageLockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in (*image.BUILD_INPUTS, "image.json"):
            shutil.copy2(SERVER / name, self.root / name)
        self.lock = image.make_lock(DIGEST, self.root)

    def test_resolves_to_exact_digest(self):
        self.assertEqual(image.resolve(self.lock, self.root),
                         "ghcr.io/warhammer-mods/rpfm-server-docker@" + DIGEST)

    def test_rejects_another_image_version_platform_or_repository(self):
        for key, value in (("repository", "ghcr.io/other/image"), ("version", "0.0.0"),
                           ("release", "v5.3.1-server.99"), ("platform", "linux/arm64"),
                           ("schema", 2), ("digest", "latest")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                image.resolve({**self.lock, key: value}, self.root)

    def test_changed_build_inputs_reject_old_lock(self):
        for name in image.BUILD_INPUTS:
            with self.subTest(name=name):
                path = self.root / name
                original = path.read_bytes()
                path.write_bytes(original + b"\n")
                with self.assertRaises(ValueError):
                    image.resolve(self.lock, self.root)
                path.write_bytes(original)

    def test_windows_line_endings_have_same_fingerprint(self):
        for name in image.BUILD_INPUTS:
            path = self.root / name
            path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        self.assertEqual(image.resolve(self.lock, self.root), self.lock["repository"] + "@" + DIGEST)

    def test_rejects_image_release_for_different_backend(self):
        config = json.loads((self.root / "image.json").read_text())
        config["release"] = "v0.0.0-server.1"
        (self.root / "image.json").write_text(json.dumps(config))
        with self.assertRaises(ValueError):
            image.make_lock(DIGEST, self.root)


@unittest.skipUnless(os.name == "posix", "Launcher integration uses POSIX executable fixtures")
class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.calls = self.root / "docker.jsonl"
        self.outputs = self.root / "outputs"
        self.env = {**os.environ, "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
                    "RUNNER_OS": "Linux", "RUNNER_ARCH": "X64", "RUNNER_TEMP": str(self.root),
                    "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_WORKSPACE": str(self.root),
                    "GITHUB_OUTPUT": str(self.outputs), "RPFM_ACTION_PATH": str(SERVER),
                    "RPFM_INPUT_IMAGE": "", "RPFM_BUILD_FROM_SOURCE": "false", "RPFM_INPUT_PORT": "45127",
                    "DOCKER_CALLS": str(self.calls), "LOCK_JSON": json.dumps(image.make_lock(DIGEST))}
        self.fixture("docker", '''import json, os, sys
args = sys.argv[1:]
with open(os.environ['DOCKER_CALLS'], 'a') as f:
    f.write(json.dumps(args) + '\\n')
if args[:2] == ['image', 'inspect']:
    sys.exit(0 if os.environ.get('LOCAL_IMAGE') else 1)
if args[0] == 'inspect':
    print('false' if os.environ.get('FAIL_START') else 'true')
elif args[0] == 'run':
    print('test-container')
''')
        self.fixture("curl", '''import os, pathlib, sys
if '--output' in sys.argv:
    if os.environ.get('FAIL_DOWNLOAD'):
        sys.exit(22)
    pathlib.Path(sys.argv[sys.argv.index('--output')+1]).write_text(os.environ['LOCK_JSON'])
elif os.environ.get('FAIL_START'):
    sys.exit(7)
else:
    print('{"version":"5.3.1"}')
''')

    def fixture(self, name, script):
        path = self.bin / name
        path.write_text("#!/usr/bin/env python3\n" + script)
        path.chmod(0o755)

    def run_launcher(self):
        return subprocess.run(["bash", str(SERVER / "start.sh")], env=self.env,
                              capture_output=True, text=True, timeout=15)

    def commands(self):
        return [json.loads(line) for line in self.calls.read_text().splitlines()] if self.calls.exists() else []

    def test_default_downloads_and_runs_digest_without_build(self):
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = self.commands()
        expected = "ghcr.io/warhammer-mods/rpfm-server-docker@" + DIGEST
        self.assertIn(["pull", expected], commands)
        self.assertEqual(next(c for c in commands if c[0] == "run")[-1], expected)
        self.assertFalse(any(c[0] == "build" for c in commands))
        self.assertIn("image=" + expected, self.outputs.read_text())

    def test_source_build_is_explicit(self):
        self.env["RPFM_BUILD_FROM_SOURCE"] = "true"
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any(c[0] == "build" for c in self.commands()))
        self.assertFalse(any(c[0] == "pull" for c in self.commands()))

    def test_local_image_is_not_pulled(self):
        self.env.update(RPFM_INPUT_IMAGE="local/test:one", LOCAL_IMAGE="1")
        self.assertEqual(self.run_launcher().returncode, 0)
        self.assertFalse(any(c[0] in {"build", "pull"} for c in self.commands()))

    def test_missing_metadata_does_not_silently_build(self):
        self.env["FAIL_DOWNLOAD"] = "1"
        self.assertNotEqual(self.run_launcher().returncode, 0)
        self.assertEqual(self.commands(), [])

    def test_tampered_lock_does_not_run_docker(self):
        lock = json.loads(self.env["LOCK_JSON"])
        lock["context_sha256"] = "0" * 64
        self.env["LOCK_JSON"] = json.dumps(lock)
        self.assertNotEqual(self.run_launcher().returncode, 0)
        self.assertEqual(self.commands(), [])

    def test_rejects_conflicting_inputs_and_invalid_ports(self):
        for values in ({"RPFM_INPUT_PORT": "80"}, {"RPFM_INPUT_PORT": "65536"},
                       {"RPFM_INPUT_PORT": "bad"}, {"RPFM_BUILD_FROM_SOURCE": "yes"},
                       {"RPFM_BUILD_FROM_SOURCE": "true", "RPFM_INPUT_IMAGE": "local/test"}):
            with self.subTest(values=values):
                previous = self.env.copy()
                self.env.update(values)
                self.assertNotEqual(self.run_launcher().returncode, 0)
                self.assertEqual(self.commands(), [])
                self.env = previous

    def test_failed_start_removes_its_container_and_emits_no_outputs(self):
        self.env.update(RPFM_INPUT_IMAGE="local/test", LOCAL_IMAGE="1", FAIL_START="1")
        self.assertNotEqual(self.run_launcher().returncode, 0)
        self.assertTrue(any(c[:2] == ["rm", "--force"] for c in self.commands()))
        self.assertFalse(self.outputs.exists())


if __name__ == "__main__":
    unittest.main()
