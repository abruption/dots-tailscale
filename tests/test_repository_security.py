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

    def test_failed_put_or_readback_does_not_claim_no_mutation(self):
        for failure in ("private_reporting_put", "branch_protection_put", "effective_readback"):
            self.calls, self.protected = [], False
            mutation_count = 0
            def failing_api(path, method="GET", value=None):
                nonlocal mutation_count
                if method == "PUT":
                    mutation_count += 1
                    if ((failure == "private_reporting_put" and mutation_count == 1)
                            or (failure == "branch_protection_put" and mutation_count == 2)):
                        raise tool.PolicyError("timeout_after_possible_mutation")
                elif failure == "effective_readback" and mutation_count == 2:
                    raise tool.PolicyError("readback_timeout")
                return self.api(path, method, value)
            with self.assertRaises(tool.PolicyError) as caught:
                tool.apply_policy(self.policy, True, lambda value: None, failing_api)
            self.assertTrue(caught.exception.mutation_attempted)
            self.assertEqual(caught.exception.phase, failure)

    def test_readback_checks_all_selected_review_restrictions(self):
        self.calls, self.protected = [], True
        correct = self.api("/branches/main/protection")
        self.assertTrue(tool.policy_matches(correct, self.policy))
        for field in ("require_code_owner_reviews", "require_last_push_approval"):
            altered = copy.deepcopy(correct)
            altered["required_pull_request_reviews"][field] = True
            self.assertFalse(tool.policy_matches(altered, self.policy))
        altered = dict(correct, restrictions={"users": []})
        self.assertFalse(tool.policy_matches(altered, self.policy))


if __name__ == "__main__":
    unittest.main()
