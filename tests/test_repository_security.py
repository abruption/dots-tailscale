"""Repository settings fixtures; no network mutations or fabricated reports."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("repository_security", ROOT / "scripts/repository_security.py")
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)


class RepositoryPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = json.loads((ROOT / "docs/repository-protection.json").read_text())

    def api(self, path, method="GET", value=None):
        self.calls.append((path, method))
        if method == "PUT":
            if path.endswith("/protection"):
                self.protected = True
            return None
        if path == "/branches/main":
            return {"protected": self.protected}
        if path == "/private-vulnerability-reporting":
            return {"enabled": self.protected}
        if path == "/rulesets":
            return []
        result = copy.deepcopy(self.policy)
        for name in ("enforce_admins", "required_conversation_resolution", "allow_force_pushes", "allow_deletions"):
            result[name] = {"enabled": result[name]}
        return result

    def test_owner_confirmation_and_safe_backup_before_mutation(self):
        self.calls, self.protected = [], False
        with self.assertRaisesRegex(tool.PolicyError, "owner_confirmation"):
            tool.apply_policy(self.policy, False, lambda value: None, self.api)
        self.assertEqual(self.calls, [])
        def fail_backup(value):
            raise OSError("backup unavailable")
        with self.assertRaises(OSError):
            tool.apply_policy(self.policy, True, fail_backup, self.api)
        self.assertFalse(any(method == "PUT" for _, method in self.calls))

    def test_effective_readback_and_only_the_approved_settings(self):
        self.calls, self.protected, backups = [], False, []
        result = tool.apply_policy(self.policy, True, backups.append, self.api)
        self.assertTrue(result["applied"])
        self.assertEqual(result["required_approvals"], 0)
        self.assertEqual(len(backups), 1)
        self.assertEqual([path for path, method in self.calls if method == "PUT"],
                         ["/private-vulnerability-reporting", "/branches/main/protection"])

    def test_wrong_provider_widened_policy_and_backup_overwrite_refused(self):
        for field, value in (("allow_force_pushes", True), ("allow_deletions", True), ("enforce_admins", True)):
            policy = dict(self.policy, **{field: value})
            with self.assertRaises(tool.PolicyError):
                tool.validate_policy(policy)
        wrong = copy.deepcopy(self.policy)
        wrong["required_status_checks"]["checks"][0]["app_id"] = -1
        self.assertFalse(tool.policy_matches(wrong, self.policy))
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            folder.chmod(0o700)
            tool.save_backup(folder, {"public_policy": True})
            with self.assertRaises(FileExistsError):
                tool.save_backup(folder, {})
            self.assertEqual((folder / "repository-security-before.json").stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
