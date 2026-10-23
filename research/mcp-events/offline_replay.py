#!/usr/bin/env python3
"""Offline research only: replay observer JSONL into dry-run event candidates.

This program does not monitor, send events, or run recovery commands. Candidates
are evidence for reviewing a proposed trigger, not proof that recovery is needed.
"""

import argparse
from datetime import datetime, timezone
import json
import sys


MAX_GAP_SECONDS = 150
FAILURE_THRESHOLD = 3


def timestamp(value):
    """Accept timezone-aware observation times; never infer a local timezone."""
    if not isinstance(value, str):
        raise ValueError("missing timestamp")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timestamp requires timezone")
    return result.astimezone(timezone.utc)


def failed_sample(record):
    ssh, ping = record.get("ssh"), record.get("peer_ping")
    if not isinstance(ssh, dict) or not isinstance(ping, dict):
        return False
    return (
        record.get("state") == "external_unreachable"
        and ssh.get("error_class") in ("timeout", "network_unreachable")
        and type(ssh.get("exit_code")) is int
        and ssh["exit_code"] == 255
        and ssh.get("command_succeeded") is not True
        and ssh.get("authenticated") is not True
        and ping.get("responded") is False
        and type(ping.get("exit_code")) is int
        and ping["exit_code"] == 1
        and ping.get("error_class") in (None, "ok")
        and not ping.get("error")
        and not ping.get("execution_error")
        and not record.get("local_execution_failed")
    )


def healthy_ssh(record):
    ssh = record.get("ssh")
    if not isinstance(ssh, dict):
        return False
    return (
        record.get("state") != "local_execution_failed"
        and not record.get("local_execution_failed")
        and ssh.get("error_class") == "ok"
        and type(ssh.get("exit_code")) is int
        and ssh["exit_code"] == 0
        and (
            ssh.get("authenticated") is True
            or (ssh.get("command_succeeded") is True and ssh.get("probe_valid") is True)
        )
    )


def replay(lines):
    """Yield only sanitized candidates, once per episode until newer good SSH.

    Invalid, duplicate, and backward samples break an accumulating streak. They
    cannot advance the last accepted timestamp or rearm an emitted episode.
    A long gap breaks a streak but never rearms an already emitted episode.
    """
    previous = None
    first_failure = None
    failures = 0
    emitted = False
    for line in lines:
        try:
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError("observation must be an object")
            current = timestamp(record.get("observed_at"))
        except (ValueError, TypeError, OverflowError, RecursionError):
            failures, first_failure = 0, None
            continue

        if previous is not None and current <= previous:
            failures, first_failure = 0, None
            continue
        if previous is not None and (current - previous).total_seconds() > MAX_GAP_SECONDS:
            failures, first_failure = 0, None
        previous = current

        if healthy_ssh(record):
            emitted = False
            failures, first_failure = 0, None
        elif failed_sample(record):
            if not emitted:
                if failures == 0:
                    first_failure = current
                failures += 1
                if failures == FAILURE_THRESHOLD:
                    emitted = True
                    yield {
                        "dry_run": True,
                        "target": "dots",
                        "kind": "recovery_candidate",
                        "observed_at": current.isoformat(),
                        "first_failed_at": first_failure.isoformat(),
                        "consecutive_failures": FAILURE_THRESHOLD,
                    }
        else:
            failures, first_failure = 0, None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("observations", help="Existing JSONL file to replay, or - for stdin")
    args = parser.parse_args(argv)
    try:
        if args.observations == "-":
            for candidate in replay(sys.stdin):
                print(json.dumps(candidate, sort_keys=True))
        else:
            with open(args.observations, encoding="utf-8") as stream:
                for candidate in replay(stream):
                    print(json.dumps(candidate, sort_keys=True))
    except (OSError, UnicodeError):
        print("Unable to read the observation input.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
