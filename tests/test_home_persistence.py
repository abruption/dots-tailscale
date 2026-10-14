"""Exercise persistence markers using isolated directories, never real HOME."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "home-persistence-probe.py"
SPEC = importlib.util.spec_from_file_location("home_persistence_probe", SOURCE)
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)
TEMP_ROOT = Path(tempfile.gettempdir())


class HomePersistenceTests(unittest.TestCase):
    def setUp(self):
        self.previous_umask = os.umask(0o077)
        self.temp = tempfile.TemporaryDirectory(prefix="codex-probe-", dir=TEMP_ROOT)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(os.umask, self.previous_umask)
        self.home = Path(self.temp.name) / "home"
        self.home.mkdir()
        self.root = Path(self.temp.name) / "workspace" / "probe"
        self.home_marker = self.home / ".dots-home-persistence-marker.json"

    def observe(self):
        return PROBE.observe(self.home, self.root)

    def test_baseline_then_healthy_repeat_and_history(self):
        first = self.observe()
        second = self.observe()
        self.assertEqual(first["assessment"], "baseline_created")
        self.assertEqual(second["assessment"], "markers_intact")
        self.assertEqual(first["home_marker_id"], second["home_marker_id"])
        self.assertFalse(second["home_marker_missing_before_check"])
        self.assertFalse(second["workspace_marker_changed_since_baseline"])
        history = [json.loads(line) for line in (self.root / "history.jsonl").read_text().splitlines()]
        self.assertEqual(history, [first, second])
        self.assertEqual(json.loads((self.root / "latest.json").read_text()), second)

    def test_deleted_home_marker_detected_without_losing_baseline(self):
        first = self.observe()
        baseline = (self.root / "baseline.json").read_bytes()
        self.home_marker.unlink()
        changed = self.observe()
        self.assertEqual(changed["assessment"], "home_marker_changed_workspace_intact")
        self.assertTrue(changed["home_marker_missing_before_check"])
        self.assertTrue(changed["home_marker_changed_since_baseline"])
        self.assertEqual(changed["workspace_marker_id"], first["workspace_marker_id"])
        self.assertEqual((self.root / "baseline.json").read_bytes(), baseline)
        later = self.observe()
        self.assertFalse(later["home_marker_missing_before_check"])
        self.assertTrue(later["home_marker_changed_since_baseline"])

    def test_deleted_workspace_marker_detected(self):
        self.observe()
        (self.root / "workspace-marker.json").unlink()
        changed = self.observe()
        self.assertEqual(changed["assessment"], "marker_continuity_changed")
        self.assertTrue(changed["workspace_marker_missing_before_check"])
        self.assertTrue(changed["workspace_marker_changed_since_baseline"])
        self.assertFalse(changed["home_marker_changed_since_baseline"])

    def test_different_home_refused_without_creating_marker(self):
        self.observe()
        other_home = Path(self.temp.name) / "other-home"
        other_home.mkdir()
        with self.assertRaisesRegex(RuntimeError, "home mismatch"):
            PROBE.observe(other_home, self.root)
        self.assertEqual(list(other_home.iterdir()), [])

    def test_corrupt_marker_refused_and_preserved(self):
        self.observe()
        for content in ("{broken", '{"id": 123}', "[]"):
            with self.subTest(content=content):
                self.home_marker.write_text(content)
                with self.assertRaises((ValueError, RuntimeError)):
                    self.observe()
                self.assertEqual(self.home_marker.read_text(), content)


if __name__ == "__main__":
    unittest.main()
