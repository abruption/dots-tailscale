# Opt-in automatic package updates

These features belong to the **v0.8.0 development candidate**. The latest published
stable release is v0.7.1; publishing this branch does not upgrade an installation.

Automatic updates are off by default. Installing or updating dots-tailscale does not register a task, install cron/systemd, create a watchdog, or start this runner. This feature updates the four package sources through the existing manual transaction; it does not update Tailscale binaries or migrate their declared baseline.

This is an experimental execution path for a Linux installation with visible writable installation/runtime directories, working local components and socket, and the same Python/pidfd support required by manual `update apply`. Keep an independent cloud-computer recovery terminal available before enabling it. A connected SSH session is not an independent recovery path.

The lifecycle research in [#20](https://github.com/abruption/dots-tailscale/issues/20) shows that files, a running process and a socket can have different visibility in direct Bash and the cloud-computer terminal. A VM-local runner disappears when that execution environment stops. It cannot start itself in a replacement VM. Neither the one-shot caller nor the foreground watcher is a platform boot hook, and this implementation does not establish unattended execution across VM replacement or another account's setup.

## Prepare the trust and recovery paths

Automatic candidates must be stable releases newer than the installed package and at least v0.8.0. The installed package itself must be a stable v0.8.0-or-later package with matching source integrity. Development versions, downgrades and unsigned historical releases are not automatic targets.

Prepare the independent trusted-root file and preinstalled `gh` verifier described in the release-provenance guide. The file and verifier must be outside the installation and candidate directories; the runner never downloads a replacement verifier or silently relaxes proof checks. The trusted-root pathname is stored in automatic settings. No GitHub token is introduced by the automatic-update configuration.

Use the recovery lifecycle commands to inspect the separate watchdog/external-recovery policy. The automatic runner refuses an in-progress or unknown recovery attempt and respects a configured recovery stop. An unconfigured recovery ledger does not install or enable recovery; the operator's independent recovery-terminal requirement still applies.

## Enable, pin and schedule

Example, after the trusted-root file exists and the execution context passes preflight:

```bash
/workspace/.dots-tailscale/dots-ts update automatic enable \
  --interval-hours 24 --timezone Asia/Seoul --window 03:00-05:00 \
  --provenance-root /workspace/independent-trust/github-trusted-root.json \
  --proxy http://browser-proxy:8889 --confirm-recovery-terminal --json
/workspace/.dots-tailscale/dots-ts update automatic status --json
```

Enabling records an explicit operator confirmation; it does not prove the recovery terminal will always remain available. Each eligible tick rechecks current filesystem access, source integrity, visible components, local socket/backend, trusted-root/verifier availability, prior source/runtime transactions and recovery coordination before preparing an update. The existing transaction repeats its own readiness, identity, binary-declaration and rollback safeguards.

- Intervals are 1–720 hours, measured in epoch seconds after a check. The default is 24 hours.
- The default local-time window is 02:00–04:00 in UTC. `--window HH:MM-HH:MM` has an inclusive start and exclusive end; crossing midnight is supported. Equal start/end values are rejected rather than interpreted as all day.
- `--timezone UTC` works without a timezone database. Named IANA zones use `zoneinfo` (Python 3.9+) or an explicitly installed `backports.zoneinfo`; missing support or unknown zones are errors. No dependency is installed automatically. The inherited Linux manual-updater/pidfd requirement still applies to execution.
- Daylight-saving windows follow the configured zone's actual local time. A missing hour is skipped; repeated hours do not bypass the persistent epoch interval, attempt budget, transaction fence or newer-version check.
- `configure --pin v0.8.1` selects that exact stable tag; `configure --unpin` resumes latest-stable selection. The example tag is illustrative and must exist before a tick can use it.
- `configure --interval-hours 48 --timezone UTC --window 02:00-04:00` changes future checks. `--direct` explicitly removes the saved download proxy. Settings changes invalidate a preparing worker's generation fence.

Settings, holds, attempt history and results live in the owned private installation directory `.automatic-updates`; package code updates leave them in place. No private keys or Tailscale authentication state are copied there.

## Run it explicitly

A user-owned scheduler or approved independent caller can invoke one bounded tick:

```bash
/workspace/.dots-tailscale/dots-ts update automatic tick --json
```

A foreground observation period can call ticks repeatedly:

```bash
/workspace/.dots-tailscale/dots-ts update automatic watch \
  --seconds 3600 --poll-seconds 60 --json
```

`watch` is explicitly bounded to 1–86400 seconds, polling every 30–3600 seconds. It finishes any already-started bounded tick before exiting, so its wall time can exceed the watch duration by that tick. It creates no daemon or background service. Closing its terminal can stop the watcher while a separately committed transaction continues; reconnect and inspect status instead of resubmitting. The watcher exits after an applied update or a change to its executing/installed source-bundle or manifest bytes, so the next caller starts the current policy in a fresh process. It also stops with exit code 1 on a failed tick or manual-review result; the JSON records that result. A policy change produces `runner_restart_required` without claiming that a pending transaction succeeded.

The runner uses a nonblocking run lock and a durable attempt before invoking `update_apply` with proof verification enabled, unsigned-legacy override disabled and the stored independent trusted root. It does not contain a second updater. The source updater's common operation lock continues to serialize code/process changes with manual updates, runtime changes and proxy operations.

## Hold, stop and remove a caller

```bash
/workspace/.dots-tailscale/dots-ts update automatic hold --json
/workspace/.dots-tailscale/dots-ts update automatic unhold --json
/workspace/.dots-tailscale/dots-ts update automatic disable --json
```

`hold` pauses an enabled configuration; `unhold` does not enable a disabled configuration. `disable` is persistent and can be issued during an outage without passing network/process preflight. A later explicit `enable --confirm-recovery-terminal` is required to resume a disabled configuration. None of these commands stops Tailscale or the recovery subscription.

Cancellation has a precise boundary. A preparing worker must acquire the same short control lock as `disable`/`hold`, check the saved generation, enabled/hold state, selected tag, time window and five-minute preparation freshness, then persist `execution_committed` before the existing transaction starts. A stop that is persisted first rejects that worker without replacing sources or restarting the shim. A transaction already committed first may finish or roll back after disabling; the stop still prevents future attempts. The tool does not claim to cancel a running transaction.

The source/recovery coordination lock protects preparation, then the durable automatic attempt fences the detached gap. Worker commit reacquires the recovery guard. Recovery cannot interpret a missing acknowledgement, unknown worker or unfinished update as permission to start another operation.

To remove periodic execution, disable automatic updates first, stop the foreground watcher, and remove only the scheduler entry that you explicitly created. This feature creates no scheduler entry to uninstall. Preserve the private attempt/transaction evidence until every operation is reconciled; do not delete state to make an unknown attempt disappear.

## Results, retries and recovery

`automatic status --json` reports settings and the last durable result, plus an independently observed transaction state when an attempt is pending. It labels the execution environment as not rechecked by a status read and external SSH as not verified. A cached result is not current network health.

Status adds `warning: clock_moved_backwards` when saved observations are ahead of
the current clock; it does not rewrite them. New automatic work remains blocked
until the time evidence is consistent. Independent recovery is fenced by an
actual unfinished/unknown operation, not by old automatic history alone: an idle
or proven-terminal automatic ledger cannot disable recovery merely because a
past clock step is being corrected. Disabling auto still does not clear a real
unknown transaction.

A prepared/started transaction is not success. Reconcile the same transaction with `dots-ts update status --id ID --json`; source transaction terminal results include `applied`, `failed_rolled_back`, `rejected`, and manual-recovery states. A lost acknowledgement is reconciled against the same attempt and transaction rather than resubmitted. A missing worker, uncertain launch or failed rollback blocks new automatic execution and leaves a manual-review result, except for the narrowly proven pre-staging exit below. Clock rollback is checked against existing observed times, including pending/result/confirmation times, before reconciliation and again before persistence. It refuses the operation without rewriting the existing state bytes; a future `next_check_at` is a planned schedule, not evidence of clock rollback.

There are at most three automatic installation preparations and three recorded failures in a rolling 24-hour window. Intermittent check errors back off for 5, 10 and then 20 minutes, capped at one hour, and respect a later validated GitHub retry/reset time. The daily failure budget can delay further checks until an old failure leaves the window. Proof/policy failures and candidate execution failures hold that candidate tag instead of installing it repeatedly. Temporary network errors, a verifier timeout, operation/recovery contention or a pre-commit cancellation use backoff without permanently labelling the release faulty. These attempts still consume the configured budgets.

After inspecting the known terminal result, verifying the intended external connection with a fresh strict-host-key SSH connection and Tailscale ping, and confirming the independent terminal again, an operator can clear a known failure hold:

```bash
/workspace/.dots-tailscale/dots-ts update automatic reset-failures \
  --confirm-recovery-terminal --json
```

This takes the same nonblocking runner lock as a tick, does not erase the installation-attempt budget and refuses an unresolved/unknown attempt. If a tick is still active, wait for its result rather than racing it with a reset. It is explicit permission for a future eligible check, not a blind retry of an uncertain transaction.

### A preparer exits before staging

An interrupted download/verifier process can leave an automatic preparation marker before a transaction directory exists. The marker is not cleared just because a PID disappeared. Under the existing package operation lock, the tool requires the **same boot**, a definitely exited original owner, a null transaction ID, a `preparing`/uncommitted manual-review phase, and a bounded inspection in which every transaction entry and journal is valid/readable and none carries that automatic fence. The existing operation-lock file must itself be safe. Only then is the result `abandoned_before_staging`: no candidate was staged/executed by that attempt, the installation-attempt budget is retained, and the tag is not held as a failed candidate.

A tick can reconcile this proof automatically. The read-only recovery fence can recognize the same proof without rewriting state or calling the result applied. An operator can also request only this proof-based reconciliation explicitly:

```bash
/workspace/.dots-tailscale/dots-ts update automatic abandon-preparation \
  --confirm-recovery-terminal --json
```

This is not a force-clear command. A new boot, non-null transaction ID, a matching fence, unknown owner identity, missing/unsafe operation lock, empty or unreadable journal, or any other ambiguity remains manual review. Neither `reset-failures` nor `abandon-preparation` bypasses those conditions.

`automatic notify` is a cache-only, deduplicated interactive stderr notification of the last result. Redirected/noninteractive stderr stays silent; a scheduler must inspect the JSON or arrange its own approved notification delivery. No email, Telegram message or external callback is sent by this command.

Automatic success means the existing source transaction completed its local checks. It does not prove a new external SSH connection works, that the installed Tailscale version fixes every advisory, or that a future VM can start the runner. Keep the code-only backup and follow the manual updater's independent-terminal recovery instructions if rollback or execution is uncertain.


## Isolated validation boundary

The automatic-update tests exercise clock rollback without changing persisted bytes, cancellation and contention, safe/ambiguous pre-staging exits, and two ticks during a repeated DST hour without a duplicate install. The forked worker fixture uses the real `apply_worker`, running acknowledgement, caller submission, execution-commit guard, journals and file locks. It stubs only the transaction body; it does not install code, verify cryptographic provenance, restart a daemon or contact a network. Linux runs use native `/proc` identities/boot data; non-Linux POSIX runs adapt that Linux-specific identity observation. This fixture is not live Dots deployment evidence.
