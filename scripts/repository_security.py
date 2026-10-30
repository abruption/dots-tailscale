#!/usr/bin/env python3
"""Inspect or explicitly apply the reviewed security policy to this repository."""
import argparse
import datetime
import json
import os
from pathlib import Path
import stat
import subprocess


REPOSITORY = "abruption/dots-tailscale"
CHECKS = {"Python 3.11 tests", "Python 3.13 tests", "Shell syntax and ShellCheck", "Python static checks"}


class PolicyError(Exception):
    def __init__(self, code, phase="preflight", mutation_attempted=False):
        super().__init__(code)
        self.phase = phase
        self.mutation_attempted = mutation_attempted


def github(path, method="GET", value=None):
    args = ["gh", "api", "repos/" + REPOSITORY + path, "--method", method]
    if value is not None:
        args += ["--input", "-"]
    try:
        reply = subprocess.run(args, input=None if value is None else json.dumps(value),
                               text=True, capture_output=True, timeout=30, check=True)
        if len(reply.stdout) > 1024 * 1024:
            raise PolicyError("repository_response_too_large")
        return json.loads(reply.stdout) if reply.stdout.strip() else None
    except (subprocess.SubprocessError, OSError, ValueError):
        raise PolicyError("repository_api_failed") from None


def validate_policy(value):
    expected = {"required_status_checks", "enforce_admins", "required_pull_request_reviews", "restrictions",
                "required_conversation_resolution", "allow_force_pushes", "allow_deletions"}
    try:
        checks = value["required_status_checks"]
        reviews = value["required_pull_request_reviews"]
        if (set(value) != expected or checks["strict"] is not True
                or set(checks) != {"strict", "checks"}
                or set(reviews) != {"dismiss_stale_reviews", "require_code_owner_reviews",
                                    "required_approving_review_count", "require_last_push_approval"}
                or len(checks["checks"]) != len(CHECKS)
                or {(item["context"], item["app_id"]) for item in checks["checks"]} != {(name, 15368) for name in CHECKS}
                or value["enforce_admins"] is not False or value["restrictions"] is not None
                or value["required_conversation_resolution"] is not True
                or value["allow_force_pushes"] is not False or value["allow_deletions"] is not False
                or type(reviews["required_approving_review_count"]) is not int
                or reviews["required_approving_review_count"] not in (0, 1)
                or reviews["dismiss_stale_reviews"] is not True
                or reviews["require_code_owner_reviews"] is not False
                or reviews["require_last_push_approval"] is not False):
            raise PolicyError("unexpected_policy")
    except (KeyError, TypeError):
        raise PolicyError("unexpected_policy") from None
    return value


def inspect(api=github):
    branch = api("/branches/main")
    protection = api("/branches/main/protection") if branch["protected"] else None
    return {"branch": branch, "protection": protection,
            "private_reporting": api("/private-vulnerability-reporting"), "rulesets": api("/rulesets")}


def policy_matches(protection, expected):
    try:
        actual = protection["required_status_checks"]
        return (actual["strict"] is True
                and {(item["context"], item["app_id"]) for item in actual["checks"]}
                == {(name, 15368) for name in CHECKS}
                and protection["required_pull_request_reviews"]["required_approving_review_count"]
                == expected["required_pull_request_reviews"]["required_approving_review_count"]
                and protection["required_pull_request_reviews"]["dismiss_stale_reviews"] is True
                and protection["required_pull_request_reviews"]["require_code_owner_reviews"] is False
                and protection["required_pull_request_reviews"]["require_last_push_approval"] is False
                # GitHub omits this optional field when push restrictions are unset.
                and protection.get("restrictions") is None
                and protection["enforce_admins"]["enabled"] is False
                and protection["required_conversation_resolution"]["enabled"] is True
                and protection["allow_force_pushes"]["enabled"] is False
                and protection["allow_deletions"]["enabled"] is False)
    except (KeyError, TypeError):
        return False


def apply_policy(policy, confirm, backup, api=github):
    validate_policy(policy)
    if confirm is not True:
        raise PolicyError("owner_confirmation_required")
    before = inspect(api)
    # Backup must succeed before the first repository setting is changed.
    backup({"captured_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "before": before,
            "candidate": policy, "repository": REPOSITORY})
    phase = "private_reporting_put"
    try:
        api("/private-vulnerability-reporting", "PUT")
        phase = "branch_protection_put"
        api("/branches/main/protection", "PUT", policy)
        phase = "effective_readback"
        after = inspect(api)
        if (not isinstance(after["private_reporting"], dict)
                or after["private_reporting"].get("enabled") is not True
                or not policy_matches(after["protection"], policy)):
            raise PolicyError("effective_policy_mismatch_inspect_backup", phase, True)
    except Exception as error:
        # A timeout can happen after GitHub applied a PUT. Do not claim no change.
        if isinstance(error, PolicyError) and error.mutation_attempted:
            raise
        raise PolicyError("partial_or_unknown_inspect_settings", phase, True) from None
    return {"applied": True, "private_reporting": True, "main_protected": True,
            "required_checks": sorted(CHECKS), "required_approvals":
            policy["required_pull_request_reviews"]["required_approving_review_count"],
            "admin_emergency_bypass": True}


def save_backup(folder, value):
    # Fixed file, no overwrite, no symlinks and an owner-only operator directory.
    fd = os.open(str(folder), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise PolicyError("private_backup_directory_required")
        out = os.open("repository-security-before.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                      0o600, dir_fd=fd)
        with os.fdopen(out, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(fd)
    finally:
        os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("inspect", "apply"))
    parser.add_argument("--confirm-owner", action="store_true")
    parser.add_argument("--backup", type=Path)
    options = parser.parse_args(argv)
    try:
        if options.mode == "inspect":
            result = inspect()
            print(json.dumps({"main_protected": result["branch"]["protected"],
                              "private_reporting": result["private_reporting"]["enabled"],
                              "protection": result["protection"], "rulesets": result["rulesets"]}, sort_keys=True))
        else:
            if options.backup is None:
                raise PolicyError("private_backup_directory_required")
            policy = json.loads((Path(__file__).resolve().parents[1] / "docs/repository-protection.json").read_text())
            result = apply_policy(policy, options.confirm_owner, lambda value: save_backup(options.backup, value))
            print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as error:
        attempted = isinstance(error, PolicyError) and error.mutation_attempted
        print(json.dumps({"applied": None if attempted else False,
                          "state": "partial_or_unknown" if attempted else "not_attempted",
                          "phase": error.phase if isinstance(error, PolicyError) else "preflight",
                          "error": str(error) if isinstance(error, PolicyError) else "policy_io_failed",
                          "action": "inspect_settings_and_private_backup_no_automatic_rollback"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
