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
        self.candidate = {"version": "5.1.2", "tag": "v5.1.2", "rpfm_commit": "a" * 40, "schema_commit": "b" * 40}

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

    def test_updates_only_version_pins_and_keeps_historical_validation(self):
        before = self.snapshot()
        upstream.apply_update(self.root, self.current, self.candidate)
        after = self.snapshot()
        changed = {p for p in before if before[p] != after[p]}
        self.assertEqual(changed, {"server/Dockerfile", "server/compose.yaml", "server/smoke_test.py", upstream.MANIFEST, "README.md", "server/README.md"})
        self.assertEqual(json.loads((self.root / upstream.MANIFEST).read_text()), self.candidate)
        docker = (self.root / "server/Dockerfile").read_text()
        self.assertEqual(docker.count("ARG RPFM_COMMIT=" + "a" * 40), 2)
        self.assertIn("37256097535", (self.root / "server/README.md").read_text())

    def test_readme_examples_follow_new_version_and_preserve_other_text(self):
        before = {name: (self.root / name).read_text()
                  for name in ("README.md", "server/README.md")}
        upstream.apply_update(self.root, self.current, self.candidate)
        for name, text in before.items():
            self.assertEqual((self.root / name).read_text(), text.replace(
                "@v" + self.current["version"] + "-server.1", "@v5.1.2-server.1"))

    def test_same_version_pin_update_preserves_server_revision(self):
        for name in ("README.md", "server/README.md"):
            path = self.root / name
            path.write_text(path.read_text().replace("-server.1", "-server.3"))
        candidate = {**self.current, "rpfm_commit": "a" * 40}
        upstream.apply_update(self.root, self.current, candidate)
        for name in ("README.md", "server/README.md"):
            self.assertIn("@v" + self.current["version"] + "-server.3",
                          (self.root / name).read_text())

    def test_readme_layout_mismatch_does_not_partially_write(self):
        for name in ("README.md", "server/README.md"):
            with self.subTest(name=name):
                path = self.root / name
                original = path.read_text()
                path.write_text(original.replace("-server.1", "-server.invalid"))
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    upstream.apply_update(self.root, self.current, self.candidate)
                self.assertEqual(self.snapshot(), before)
                path.write_text(original)

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
