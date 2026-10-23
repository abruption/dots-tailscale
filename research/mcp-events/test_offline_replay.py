"""Synthetic offline tests; no services, credentials, or network access."""

from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location("offline_replay", Path(__file__).with_name("offline_replay.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def sample(second, good=False):
    return {
        "observed_at": (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=second)).isoformat(),
        "state": "verified_running" if good else "external_unreachable",
        "ssh": {
            "command_succeeded": good,
            "probe_valid": good,
            "error_class": "ok" if good else "timeout",
            "exit_code": 0 if good else 255,
        },
        "peer_ping": {"responded": good, "exit_code": 0 if good else 1},
    }


def run(*records):
    return list(module.replay(json.dumps(record) for record in records))


class ReplayTests(unittest.TestCase):
    def test_threshold_and_one_per_episode(self):
        self.assertEqual(run(sample(0), sample(60)), [])
        events = run(*(sample(i * 60) for i in range(20)))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["observed_at"], sample(120)["observed_at"])

    def test_new_healthy_ssh_rearms(self):
        records = [sample(i * 60, good=i == 3) for i in range(7)]
        self.assertEqual(len(run(*records)), 2)

    def test_authenticated_legacy_observation_rearms(self):
        good = sample(180, True)
        good["ssh"] = {"authenticated": True, "exit_code": 0, "error_class": "ok"}
        self.assertEqual(len(run(sample(0), sample(60), sample(120), good,
                                 sample(240), sample(300), sample(360))), 2)

    def test_peer_ping_alone_does_not_rearm(self):
        peer = sample(180)
        peer["state"] = "peer_reachable_ssh_failed"
        peer["peer_ping"] = {"responded": True, "exit_code": 0}
        self.assertEqual(len(run(sample(0), sample(60), sample(120), peer,
                                 sample(240), sample(300), sample(360))), 1)

    def test_gap_breaks_streak_but_not_emitted_episode(self):
        self.assertEqual(run(sample(0), sample(60), sample(211)), [])
        self.assertEqual(len(run(sample(0), sample(60), sample(210))), 1)
        self.assertEqual(len(run(sample(0), sample(60), sample(120),
                                 sample(1000), sample(1060), sample(1120))), 1)

    def test_duplicate_and_backward_samples_break_streak(self):
        for bad_time in (60, 30):
            with self.subTest(bad_time=bad_time):
                self.assertEqual(run(sample(0), sample(60), sample(bad_time), sample(120)), [])

    def test_stale_good_observation_does_not_rearm(self):
        self.assertEqual(len(run(sample(0), sample(60), sample(120), sample(60, True),
                                 sample(180), sample(240), sample(300))), 1)

    def test_invalid_json_breaks_streak_without_crashing(self):
        for bad in ('{bad', 'null', '[]', '{"observed_at":"bad"}',
                    '{"observed_at":"2026-01-01T00:01:30"}'):
            with self.subTest(bad=bad):
                lines = [json.dumps(sample(0)), json.dumps(sample(60)), bad,
                         json.dumps(sample(120))]
                self.assertEqual(list(module.replay(lines)), [])

    def test_invalid_json_cannot_rearm_emitted_episode(self):
        lines = [json.dumps(sample(i * 60)) for i in range(3)]
        lines += ['{bad'] + [json.dumps(sample(i * 60)) for i in range(3, 6)]
        self.assertEqual(len(list(module.replay(lines))), 1)

    def test_local_failures_and_unproven_ping_do_not_trigger(self):
        modifications = [
            ("state", "local_execution_failed"),
            ("local_execution_failed", True),
            ("peer_ping", {"responded": False, "exit_code": 127}),
            ("peer_ping", {"responded": False, "exit_code": 1, "error_class": "local_execution_failed"}),
            ("peer_ping", {"responded": False, "exit_code": 1, "error": "failed to execute"}),
            ("peer_ping", {"responded": False}),
            ("peer_ping", {"responded": "false", "exit_code": 1}),
            ("peer_ping", []),
            ("ssh", {"error_class": "connection_refused", "exit_code": 255}),
            ("ssh", None),
        ]
        for key, value in modifications:
            with self.subTest(key=key, value=value):
                record = sample(60)
                record[key] = value
                self.assertEqual(run(sample(0), record, sample(120)), [])

    def test_network_unreachable_is_supported(self):
        records = [sample(i * 60) for i in range(3)]
        for record in records:
            record["ssh"]["error_class"] = "network_unreachable"
        self.assertEqual(len(run(*records)), 1)

    def test_output_is_sanitized_and_explicitly_dry_run(self):
        records = [sample(i * 60) for i in range(3)]
        for record in records:
            record.update({"target": "private-host", "raw_log": "private data"})
            record["ssh"]["remote"] = {"boot_id": "private-boot-id"}
        event, = run(*records)
        self.assertEqual(set(event), {"dry_run", "target", "kind", "observed_at",
                                     "first_failed_at", "consecutive_failures"})
        self.assertIs(event["dry_run"], True)
        self.assertEqual(event["target"], "dots")
        self.assertNotIn("private", json.dumps(event))


if __name__ == "__main__":
    unittest.main()
