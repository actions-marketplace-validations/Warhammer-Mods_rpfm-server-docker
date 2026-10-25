import base64
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("upstream", ROOT / "server/upstream.py")
upstream = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upstream)


class ReleaseResolverTests(unittest.TestCase):
    def fixtures(self):
        return {
            "/releases/latest": {"tag_name": "v5.1.2", "target_commitish": "master", "draft": False, "prerelease": False},
            "/commits/v5.1.2": {"sha": "a" * 40},
            "/contents/schemas?ref=" + "a" * 40: {"type": "submodule", "sha": "b" * 40},
            "/contents/Cargo.toml?ref=" + "a" * 40: {"content": base64.b64encode(b'[workspace.package]\nversion = "5.1.2"\n').decode()},
            "/contents/rpfm_server/Cargo.toml?ref=" + "a" * 40: {},
        }

    def test_resolves_tag_instead_of_target_commitish(self):
        fixtures = self.fixtures()
        called = []
        def fetch(path):
            called.append(path)
            return fixtures[path]
        result = upstream.resolve(fetch)
        self.assertEqual(result["rpfm_commit"], "a" * 40)
        self.assertEqual(result["schema_commit"], "b" * 40)
        self.assertIn("/commits/v5.1.2", called)
        self.assertNotIn("/commits/master", called)

    def test_rejects_prerelease(self):
        data = self.fixtures()
        data["/releases/latest"]["prerelease"] = True
        with self.assertRaises(ValueError):
            upstream.resolve(data.__getitem__)

    def test_rejects_version_tag_mismatch(self):
        data = self.fixtures()
        data["/contents/Cargo.toml?ref=" + "a" * 40]["content"] = base64.b64encode(b'[workspace.package]\nversion = "5.1.3"\n').decode()
        with self.assertRaises(ValueError):
            upstream.resolve(data.__getitem__)

    def test_rejects_changed_schema_layout(self):
        data = self.fixtures()
        data["/contents/schemas?ref=" + "a" * 40]["type"] = "dir"
        with self.assertRaises(ValueError):
            upstream.resolve(data.__getitem__)

    def test_rejects_invalid_commit(self):
        data = self.fixtures()
        data["/commits/v5.1.2"]["sha"] = "master"
        with self.assertRaises(ValueError):
            upstream.resolve(data.__getitem__)


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / "server", self.root / "server", ignore=shutil.ignore_patterns("__pycache__"))
        for name in ("action.yml", "README.md"):
            shutil.copy2(ROOT / name, self.root / name)
        self.current = json.loads((self.root / upstream.MANIFEST).read_text())
        self.release = json.loads((self.root / "server/image.json").read_text())["release"]
        self.candidate = {"version": "5.1.2", "tag": "v5.1.2", "rpfm_commit": "a" * 40, "schema_commit": "b" * 40}

    def fixture_at_revision(self, revision):
        """Exercise arbitrary server revisions without depending on the checkout's release."""
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        shutil.copytree(self.root, root, dirs_exist_ok=True)
        release = self.release.rsplit(".", 1)[0] + "." + str(revision)
        for name in ("README.md", "server/README.md", "server/image.json", "server/compose.yaml"):
            path = root / name
            old, new = (self.release[1:], release[1:]) if name.endswith("compose.yaml") else (self.release, release)
            text = path.read_text()
            self.assertEqual(text.count(old), 1, name)
            path.write_text(text.replace(old, new), newline="\n")
        return root, release

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

    def test_updates_version_pins_and_cloud_release_together(self):
        before = self.snapshot()
        upstream.apply_update(self.root, self.current, self.candidate)
        after = self.snapshot()
        changed = {p for p in before if before[p] != after[p]}
        self.assertEqual(changed, {"server/Dockerfile", "server/compose.yaml", "server/smoke_test.py", "server/image.json", upstream.MANIFEST, "README.md", "server/README.md"})
        self.assertEqual(json.loads((self.root / upstream.MANIFEST).read_text()), self.candidate)
        docker = (self.root / "server/Dockerfile").read_text()
        self.assertEqual(docker.count("ARG RPFM_COMMIT=" + "a" * 40), 2)
        self.assertEqual(json.loads((self.root / "server/image.json").read_text())["release"], "v5.1.2-server.1")

    def test_readme_examples_follow_new_version_and_preserve_other_text(self):
        for revision in (1, 2, 12):
            with self.subTest(revision=revision):
                root, release = self.fixture_at_revision(revision)
                before = {name: (root / name).read_text()
                          for name in ("README.md", "server/README.md")}
                upstream.apply_update(root, self.current, self.candidate)
                for name, text in before.items():
                    self.assertEqual((root / name).read_text(), text.replace(
                        "@" + release, "@" + self.candidate["tag"] + "-server.1"))
                image_release = json.loads((root / "server/image.json").read_text())["release"]
                self.assertEqual(image_release, self.candidate["tag"] + "-server.1")
                self.assertIn(image_release[1:], (root / "server/compose.yaml").read_text())

    def test_same_version_pin_update_increments_server_revision(self):
        for revision in (1, 2, 9, 12):
            with self.subTest(revision=revision):
                root, _ = self.fixture_at_revision(revision)
                candidate = {**self.current, "rpfm_commit": "a" * 40}
                upstream.apply_update(root, self.current, candidate)
                expected = self.current["tag"] + "-server." + str(revision + 1)
                for name in ("README.md", "server/README.md"):
                    self.assertIn("@" + expected, (root / name).read_text())
                self.assertEqual(json.loads((root / "server/image.json").read_text())["release"], expected)
                self.assertIn(expected[1:], (root / "server/compose.yaml").read_text())

    def test_readme_layout_mismatch_does_not_partially_write(self):
        for name in ("README.md", "server/README.md"):
            with self.subTest(name=name):
                path = self.root / name
                original = path.read_text()
                old = "@" + self.release
                self.assertEqual(original.count(old), 1)
                invalid = old.rsplit(".", 1)[0] + ".invalid"
                path.write_text(original.replace(old, invalid), newline="\n")
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    upstream.apply_update(self.root, self.current, self.candidate)
                self.assertEqual(self.snapshot(), before)
                path.write_text(original, newline="\n")

    def test_readme_revision_mismatch_does_not_partially_write(self):
        for name in ("README.md", "server/README.md"):
            with self.subTest(name=name):
                path = self.root / name
                original = path.read_text()
                next_revision = int(self.release.rsplit(".", 1)[1]) + 1
                mismatched = self.release.rsplit(".", 1)[0] + "." + str(next_revision)
                path.write_text(original.replace("@" + self.release, "@" + mismatched), newline="\n")
                before = self.snapshot()
                with self.assertRaisesRegex(ValueError, "README and image release revisions differ"):
                    upstream.apply_update(self.root, self.current, self.candidate)
                self.assertEqual(self.snapshot(), before)
                path.write_text(original, newline="\n")

    def test_layout_mismatch_does_not_partially_write(self):
        path = self.root / "server/Dockerfile"
        path.write_text("Layout changed upstream")
        before = self.snapshot()
        with self.assertRaises(ValueError):
            upstream.apply_update(self.root, self.current, self.candidate)
        self.assertEqual(self.snapshot(), before)

    def test_current_release_is_noop(self):
        before = self.snapshot()
        upstream.apply_update(self.root, self.current, self.current)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
