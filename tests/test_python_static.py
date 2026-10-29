"""The static gate must include runtime entry points and reject false proof."""
import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_python_static.py"
SPEC = importlib.util.spec_from_file_location("python_static_gate", SCRIPT)
GATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GATE)


class StaticGateTests(unittest.TestCase):
    def test_source_selection_includes_extensionless_and_research(self):
        selected = GATE.source_paths([
            "dots-ts", "shim.py", "scripts/source_manifest.py", "tests/test_example.py",
            "research/mcp-events/server.py", "research/lifecycle-20/collector.py",
            "README.md", "other/unrelated.py", "shim.py",
        ])
        self.assertEqual(selected, sorted([
            "dots-ts", "shim.py", "scripts/source_manifest.py", "tests/test_example.py",
            "research/mcp-events/server.py", "research/lifecycle-20/collector.py",
        ]))

    def test_unsafe_source_paths_are_refused(self):
        for name in ("../shim.py", "/tmp/shim.py", "research/../../shim.py"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                GATE.source_paths([name])

    def test_fixture_requires_expected_diagnostic_and_failure_status(self):
        self.assertTrue(GATE.fixture_rejected(1, '[{"code":"F821"}]'))
        for status, output in (
            (0, '[{"code":"F821"}]'), (2, '[{"code":"F821"}]'), (1, '[]'),
            (1, '{"code":"F821"}'), (1, 'not JSON'), (1, '[{"code":"E902"}]'),
        ):
            with self.subTest(status=status, output=output):
                self.assertFalse(GATE.fixture_rejected(status, output))


if __name__ == "__main__":
    unittest.main()
