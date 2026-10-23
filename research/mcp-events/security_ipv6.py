#!/usr/bin/env python3
"""Opt-in IPv6 INPUT guard. Owns one chain; never flushes existing rules.

Cloudflare prefixes: https://www.cloudflare.com/ips-v6/ (2026-10-23).
RETURN permits existing INPUT controls to decide; it never overrides a later DROP.
"""
import argparse
import fcntl
import json
import os
import shlex
import stat
import subprocess
from contextlib import contextmanager

CHAIN = 'DOTS-INGRESS-V6'
CF_PREFIXES = ('2400:cb00::/32', '2606:4700::/32', '2803:f800::/32',
               '2405:b500::/32', '2405:8100::/32', '2a06:98c0::/29', '2c0f:f248::/32')
RULES = [
    ['-i', 'lo', '-j', 'RETURN'],
    ['-m', 'conntrack', '--ctstate', 'RELATED,ESTABLISHED', '-j', 'RETURN'],
    ['-i', 'tailscale0', '-j', 'RETURN'],
    ['-p', 'ipv6-icmp', '-j', 'RETURN'],
    ['-p', 'udp', '-m', 'udp', '--dport', '41641', '-j', 'RETURN'],
    ['-p', 'tcp', '-m', 'tcp', '--dport', '22', '-j', 'RETURN'],
    ['-s', 'fe80::/10', '-p', 'udp', '-m', 'udp', '--sport', '547', '--dport', '546', '-j', 'RETURN'],
] + [['-s', prefix, '-p', 'tcp', '-m', 'tcp', '--dport', port, '-j', 'RETURN']
     for prefix in CF_PREFIXES for port in ('80', '443')] + [['-j', 'DROP']]
JUMP = ['-A', 'INPUT', '-j', CHAIN]


class Firewall:
    def __init__(self, runner=subprocess.run):
        self.runner = runner

    def command(self, *args):
        result = self.runner(['/usr/sbin/ip6tables', '-w', '5', '-t', 'filter', *args],
                             check=True, text=True, capture_output=True, timeout=10)
        return result.stdout

    def snapshot(self):
        return [shlex.split(line) for line in self.command('-S').splitlines() if line]

    def inspect(self):
        snapshot = self.snapshot()
        exists = ['-N', CHAIN] in snapshot
        own = [line[2:] for line in snapshot if line[:2] == ['-A', CHAIN]]
        references = [line for line in snapshot
                      if any(line[i] in ('-j', '-g') and line[i + 1] == CHAIN
                             for i in range(len(line) - 1))]
        inputs = [line for line in snapshot if line[:2] == ['-A', 'INPUT']]
        if (exists and own != RULES) or (not exists and own):
            raise RuntimeError('Owned chain differs from expected rules; refusing to modify it')
        # Other services can prepend INPUT rules later. Even a ts-input jump
        # may ACCEPT traffic before this guard, so any ordering drift requires
        # manual review; apply/remove must not silently accept that bypass.
        if references and (references != [JUMP] or not inputs or inputs[0] != JUMP):
            raise RuntimeError('Unexpected chain references or jump order; refusing to modify rules')
        if references and not exists:
            raise RuntimeError('Jump without owned chain')
        return exists, bool(references)

    def delete_chain(self):
        # Explicit rule deletion, never -F. Recheck exact chain and references first.
        exists, jumped = self.inspect()
        if jumped:
            raise RuntimeError('Cannot delete referenced chain')
        if exists:
            for rule in reversed(RULES):
                self.command('-D', CHAIN, *rule)
            self.command('-X', CHAIN)

    def apply(self):
        exists, jumped = self.inspect()
        if jumped:
            return False
        if not exists:
            self.command('-N', CHAIN)
            added = []
            try:
                for rule in RULES:
                    self.command('-A', CHAIN, *rule)
                    added.append(rule)
            except Exception:
                # Only undo this invocation's known prefix if nobody changed it.
                current = self.snapshot()
                own = [line[2:] for line in current if line[:2] == ['-A', CHAIN]]
                refs = [line for line in current if CHAIN in line[2:]]
                if own == added and not refs:
                    for rule in reversed(added):
                        self.command('-D', CHAIN, *rule)
                    self.command('-X', CHAIN)
                raise
        self.inspect()
        try:
            self.command('-I', 'INPUT', '1', '-j', CHAIN)
        except Exception:
            # An uncertain command result may already have installed the jump.
            _, installed = self.inspect()
            if not installed and not exists:
                self.delete_chain()
            raise
        self.inspect()
        return True

    def remove(self):
        exists, jumped = self.inspect()
        if jumped:
            self.command('-D', 'INPUT', '-j', CHAIN)
        if exists:
            self.delete_chain()
        return exists


@contextmanager
def mutation_lock():
    flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
    fd = os.open('/run/dots-events-ipv6-guard.lock', flags, 0o600)
    try:
        metadata = os.fstat(fd)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0
                or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_nlink != 1):
            raise RuntimeError('Lock file must be a root-owned regular file with mode 0600 and one link')
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--apply', action='store_true')
    modes.add_argument('--remove', action='store_true')
    args = parser.parse_args()
    if not (args.apply or args.remove):
        print(json.dumps({'mode': 'plan', 'chain': CHAIN, 'rules': RULES,
                          'insert': 'INPUT position 1', 'existing_rules': 'preserved',
                          'note': 'RETURN continues existing INPUT filtering; existing connections are preserved'}))
        return
    if os.geteuid() != 0:
        parser.error('Firewall changes require root')
    with mutation_lock():
        firewall = Firewall()
        changed = firewall.apply() if args.apply else firewall.remove()
    print(json.dumps({'mode': 'apply' if args.apply else 'remove', 'changed': changed}))


if __name__ == '__main__':
    main()
