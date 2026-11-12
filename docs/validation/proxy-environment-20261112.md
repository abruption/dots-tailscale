# Proxy environment regression validation — 2026-11-12

This report separates the operator-assisted incident recovery from candidate tests.
No candidate source was installed over the operating Dots package during these tests.

## Observed incident and recovery

The operator supplied v0.9.0 `doctor` output with matching source integrity and
Tailscale CLI/daemon/backend 1.104.5. The daemon was responsive but remained in
`NoState`; registration reported HTTP 403. State metadata was UID 1000, mode 600,
nonempty. Daemon environment showed uppercase proxies selecting the local shim
and lowercase proxies selecting the platform proxy. The actual daemon log
confirmed the latter. Both ordinary HTTPS `/key` probes returned 200.

The operator gracefully restarted the identified daemon with lowercase aliases
removed, retaining the existing state/keys. The resulting `doctor` was healthy,
Running and authenticated. This intervention used the original v0.9.0 executable
sources, not the candidate patch. Direct SSH to the known tailnet IP subsequently
confirmed healthy v0.9.0/1.104.5. Local MagicDNS resolution was unavailable during
that check; strict SSH host-key checking was retained.

## Isolated candidate tests on Dots

Candidate sources/tests were transferred into a private `/tmp/codex-proxy-hotfix-*`
directory, separate from the operating installation. No test launched a real
Tailscale daemon or restarted the operating service. The startup fixture executes
the real Bash startup function with an inert child executable; OS-service/network
checks are replaced by fixtures. Process-control/runtime-transaction tests use
synthetic ELF/state data and mocked lifecycle operations.

- Negative control: removing the new lowercase export made the startup regression
  fail on the inherited `lower.invalid` proxy value. The source was restored in a
  `finally` block before positive tests.
- Patched startup environment: **1 test passed**.
- Proxy apply/rollback suite: **36 tests passed** on Linux.
- Runtime updater suite: **73 tests passed**.
- Full offline core suite: **550 tests passed** on Dots Linux.
- After adding the explicit runtime rollback-environment assertions, its **73 tests passed** again.
- A follow-up worker-level test confirms that an actual proxy guard rejection is
  recorded as terminal `rejected`, with no signal, binary replacement or update fence.
  The expanded runtime suite passed **74 tests** locally.
- Original source manifest check passed after the negative control was restored.

The 110 focused tests establish the environment invariant and refusal before
signals/binary replacement in the tested paths. They do not reproduce live
Internet Upgrade traffic, real binary cutover or uncontrolled process failures.
The supported-Linux startup fixture is skipped on macOS because system Bash 3.2
has different empty-array/nounset behavior; the positive and negative controls
above both ran in the actual Dots Linux environment.

## Remaining release gates

Independent static review, repository CI and the exact merged stable-tag signing
workflow are separate gates. A future release and operating rollout must verify
their own exact source and artifacts. This report does not claim that v0.9.1 is
published or installed, that authentication was newly authorized, or that VM
replacement/unattended recovery was tested.
