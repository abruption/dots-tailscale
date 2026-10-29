# Coordinated recovery (development)

These features belong to the **v0.8.0 development candidate**. The latest published
stable release is v0.7.1; publishing this branch does not upgrade an installation.

[한국어](recovery-lifecycle.ko.md)

This opt-in helper gives a packaged local watchdog and an explicitly integrated
external recovery task one local execution ledger and budget. It is development
work for #44, not a claim that v0.7.1 or an existing MCP task uses this path.
Installing the normal package does not enable recovery, start a watchdog,
register a scheduled task, or deploy the external broker.

## Before enabling it

Use the same writable Linux execution context that owns the existing installation
and its runtime socket. The helper requires `/proc` boot/process identity, an
unchanged source manifest, the original generated `start.sh`, existing node state,
an authentication marker, CA files and SSH identity. Missing or unsafe files,
symlinks, hardlinks, unknown process visibility, and login/approval requirements
stop automatic execution. It never installs missing files, authenticates,
re-registers a node, regenerates keys or changes tailnet policy. Independent access
to the cloud terminal remains necessary for manual recovery.

The local watchdog handles visibly missing components in the same boot. A missing
SSH component with an otherwise running daemon/shim is classified separately.
`Running` and an SSH banner on loopback port 22 are local readiness only. They do
not prove an authenticated SSH session, DERP reachability or external recovery.
An unknown/unresponsive backend or an existing SSH process with no valid banner
is not treated as permission to keep restarting it.
`NoState` can be a transient startup state. It is reported as unknown, not as a
definitive diagnosis that the user must log in again.

Stop any older private watchdog using its own reviewed procedure before enabling
the packaged watchdog. Both automatic actors must use this entry point for the
shared total to apply. A private watchdog or task that still directly executes
`start.sh` bypasses this ledger. Existing manual `start.sh` remains a separate
operator action; the helper does not intercept it.

## Configure, inspect and stop

These commands require a package containing this implementation; they are not
commands available in older stable packages.

```bash
/workspace/.dots-tailscale/dots-ts recovery status --json
/workspace/.dots-tailscale/dots-ts recovery configure --enable --json
```

Enabling the policy does not launch anything. The defaults are a **shared maximum
of three startup attempts per rolling 24 hours**, at least **3,600 seconds apart**,
and a 60-second startup deadline. Successful attempts also count. Policy accepts
1–12 attempts, a 60–86,400 second cooldown, a 10–120 second startup deadline and a
15–300 second watchdog interval. For example, an operator can explicitly tune the
shared budget:

```bash
/workspace/.dots-tailscale/dots-ts recovery configure \
  --max-attempts 3 --cooldown-seconds 3600 --timeout-seconds 60 \
  --interval-seconds 60 --json
```

The external broker's claim budget/cooldown remains an independent gate, including
any separately configured shortened interval for a confirmed new outage. A broker
claim does not bypass the local gate: the stricter effective policy can refuse
it. The maintainer's historical 12-claim broker setting is not silently copied
into the local policy. Operator changes to the local cooldown apply to both local
actors and are not the broker's evidence-qualified new-incident policy.
To benefit from a shorter broker interval, the local cooldown must not remain
longer than that interval. Lowering it also changes watchdog startup retry
cooldown; it does not change the separate watchdog polling interval.

Before consuming a broker claim, read `recovery status --json` and its
`blocked_reason` and `next_allowed_at`. Shared cooldown/daily limits, unknown
attempts, disabled policy and a full ledger are reported without modifying the
ledger. Do not claim while a local block exists or the execution context is
unknown. `next_allowed_at` describes only the currently known local ledger gate;
it is advisory, not a scheduled run or future permission. After it passes, read
fresh local and broker state again. File checks, current readiness, claim
freshness and update fences still apply at execution time.

```bash
# Explicitly start the packaged watchdog in the current boot.
/workspace/.dots-tailscale/dots-ts recovery watchdog start --json
/workspace/.dots-tailscale/dots-ts recovery watchdog status --json

# Stop only this watchdog; external startup can remain enabled.
/workspace/.dots-tailscale/dots-ts recovery watchdog stop --json

# Stop both coordinated automatic actors, preserving files and Tailscale.
/workspace/.dots-tailscale/dots-ts recovery stop --json
```

An accepted stop is durable. If a stop/configuration command returns
`recovery_busy`, its change has not been persisted: read fresh status and retry
the stop; do not assume the watchdog or recovery was disabled. A command that
has already started can finish, while later
requests are refused. A watchdog exits within one configured interval after it
sees the stop intention; an in-flight startup may take its configured deadline.
Stopping recovery does not stop the existing Tailscale/SSH connection. Re-enabling
the policy does not quietly re-enable a deliberately stopped watchdog.

The packaged watchdog is a user process, not systemd/cron or a platform boot hook.
It cannot bring itself back after its VM/process disappears. Same-boot starts
check the saved process identity and do not create a duplicate worker. On another
boot, starting it requires visible, intact files and a supported execution
context again. If package code changes while a watchdog is running, it exits as
`code_changed`, retaining the wanted flag for an explicitly authorized restart.
While that old worker is still releasing its lifetime lock, a restart returns
`watchdog_previous_worker_exiting` without creating another process. Recheck and
retry after the lock is released; `code_changed` alone is not proof of exit.
The helper never starts or replaces the older private trial watchdog.

An external startup can restart the packaged watchdog only after both explicit
choices exist: the watchdog is still wanted, and this policy is enabled:

```bash
/workspace/.dots-tailscale/dots-ts recovery configure \
  --restart-watchdog-after-external --json
```

This setting alone does not start the watchdog or cancel `watchdog stop`. Disable
it with `--no-restart-watchdog-after-external`.
This explicit opt-in also permits starting the newly verified packaged code
after a `code_changed` exit, once the old lifetime lock has been released. It
never authorizes restarting an older private watchdog.

## External claim contract

Only an authorized task freshly admitted by its authenticated broker should use
the external actor. Use that broker claim's ID as the request ID, not an incident
ID reused across claims. The values below are placeholders from the actual
accepted claim/observation, not commands to paste unchanged:

```bash
/workspace/.dots-tailscale/dots-ts recovery run \
  --actor external --request-id CLAIM_ID \
  --claim-expires-at CLAIM_EXPIRY_UNIX_SECONDS \
  --observation-time OBSERVATION_UNIX_SECONDS \
  --ssh-failed --ping-failed --json
```

The helper checks that expiry is in the next 120 seconds, observation age is at
most 150 seconds without future timestamps, and both external probes are asserted
failed. It rechecks freshness after local preflight. These are **caller-asserted
structure/freshness checks**, not OAuth or signature verification. Command flags
are not proof of a broker-issued claim and add no security boundary against the
same Unix user. Keep broker authentication, owner checks, one-claim semantics and
result reporting in the external task. Do not run an external actor merely
because a previous `retry_at` has passed.

The 120/150-second checks are deliberately conservative and do not add clock
skew allowance. Broker/Dots clock disagreement or a near-expiry observation can
cause rejection; inspect clocks and fresh state rather than extending an expiry,
rewriting an observation time, or retrying the consumed claim.

After acquiring the nonblocking local run lock, the helper rechecks files,
policy, local state and shared budget. If the installation is already locally
healthy, an external request records `skipped_healthy` without calling `start.sh`.
Otherwise it records an execution intent before starting exactly the installed
startup command, with no install/login arguments. The separate package operation
lock is checked and released first; the startup script acquires that same lock
itself. Holding it across the subprocess would deadlock. Automatic-update
in-flight/unknown state also blocks recovery; coordinated updates use the local
recovery guard and their durable in-flight marker.

The watchdog uses `--actor watchdog` with a fresh request ID for each tick. Its
healthy observations do not grow the attempt ledger. Externally skipped IDs are
retained so replays remain no-ops. A repeated recorded ID returns its stored
result and cannot execute again, even if the original claim has since expired.

## Results, uncertainty and retention

`local_ready` means the command returned zero and a new local check was healthy.
`failed` means a known nonzero exit. `unknown` includes timeout, uncertain launch
or failed result persistence. An interrupted `running` record also fences every
new ID. A watchdog restart failure is a separate result; it cannot erase a
durable startup outcome. Every local result leaves `external_confirmation` as
`pending`: report the broker result and check fresh external SSH and ping
separately. The local helper never manufactures that external proof.

After an unknown result, use the independent terminal to inspect whether the
startup/processes completed. Do not invent a new ID to retry. Only an operator
who has reviewed the particular attempt should reconcile it, for example:

```bash
/workspace/.dots-tailscale/dots-ts recovery reconcile \
  --request-id CLAIM_ID --outcome failed --confirm-reviewed --json
```

The available outcomes are `failed`, `not_executed` and `local_ready`. The helper
requires the relevant startup/executor processes to be proven exited (or a
different boot), and checks fresh local readiness for `local_ready`. Reconciliation
does not create external confirmation, delete the attempt, refund budget, or
execute another command.
In the same boot, a different process start tick proves PID reuse; a changed
executable with the original start tick does not prove exit. For an unfinished
watchdog-owned attempt, stop the watchdog and confirm its executor has exited
before reconciliation. A busy stop is not an accepted stop.

Configuration, stop intention, worker identity and attempt records live in the
owned private `.recovery` directory inside the installation. They survive code-only
updates and code rollback; never restore an older ledger to gain retries. Records
are bounded to 256 and the state file to 512 KiB. A full, malformed, inaccessible,
unsafe or future-clock ledger refuses new execution rather than deleting uncertain
history or assuming a fresh budget. Full-ledger maintenance requires operator
review; no automatic archive/reset command is provided. Keep this operational
state private and out of public bug reports.

The isolated regression suite covers concurrent actors, delayed healthy events,
shared budgets after restart, stop during execution, stale claims, interrupted
journal writes, partial startup, unknown-result reconciliation and simulated boot
changes. Those tests do not prove a real VM replacement, natural unattended
recovery, another account's setup, or an execution-time guarantee for MCP events.
