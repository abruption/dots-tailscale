# Manual Tailscale binary updates

[한국어](runtime-updates.ko.md)

Available in dots-tailscale v0.6.0 (#50); v0.5.0 and earlier do not include this command. It updates the installed `tailscale` and `tailscaled` binaries together. `dots-ts update` continues to manage the dots-tailscale source package; `dots-ts runtime` manages the Tailscale pair. Neither command enables automatic updates.

## Install the matching tool first

Use a reviewed dots-tailscale package containing this feature, including all four source files and its matching `source-manifest.json`. The installed bundle must match the tool invoking the update. Running a new checkout's CLI against an older installed package is refused: the older startup script would not know how to block an interrupted binary update.

The v0.7.0 installer uses 1.104.4 for new installations. The earlier v0.6.0 package declared 1.102.4, allowing its runtime tool to be installed through older source-only updaters. Ordinary package `update apply` still refuses a changed binary-version declaration. Existing 1.102.4-baseline installations must first obtain the v0.6.0 tools if needed, use that installed runtime updater, verify external connectivity, then follow the release's explicit [security-baseline migration](security-baseline.md). This source-only migration is separate from `runtime apply` and does not silently update binaries.

## Check without changing the installation

```bash
/workspace/.dots-tailscale/dots-ts runtime check --json
```

The default path uses the platform proxy (`UPSTREAM_PROXY`, otherwise `http://browser-proxy:8889`) explicitly. `--proxy direct` opts into direct HTTPS; it does not bypass platform egress restrictions. An explicit `check --version 1.104.4` compares against that number without validating that it is a published or latest release. An unavailable installed version must be inspected before applying an update.

Online apply requires an exact version matching the official stable metadata observed at the start of that request. Numeric versions with an unstable minor number, prerelease suffixes, downgrades and same-version reinstalls are refused. A newer stable release appearing later does not change the already selected archive URL.

## Apply manually

First open and verify an independent Dots cloud terminal that will remain usable if Tailscale SSH connectivity disappears. Pause external recovery jobs if configured, and keep that terminal available through external verification. The confirmation flag records your acknowledgement; the CLI cannot verify GUI control or that external jobs were paused.

For example, after reviewing `runtime check` and the upstream advisory:

```bash
/workspace/.dots-tailscale/dots-ts runtime apply \
  --version 1.104.4 --proxy platform \
  --confirm-recovery-terminal --json
```

The selected version is an example, not a claim that every Tailworm/SSH finding is resolved. The [Tailworm report](https://tailworm.com/) tested 1.104.2 itself. Check the current [Tailscale security bulletins](https://tailscale.com/security-bulletins) before selecting a version.

Apply requires Linux amd64, Python pidfd support, a private writable installation/runtime context, a complete verified source package, and a uniquely identified running daemon/shim/OpenSSH with the expected configuration. It stages and checks the archive before stopping anything, verifies both version commands, and backs up both existing binaries. The same operation lock coordinates startup, proxy apply and package updates.

The CLI prints a transaction ID and flushes stdout before acknowledging the detached worker. This orders local output before cutover; it does not prove that a remote terminal received the ID. If output is lost, inspect `.runtime-transactions/` from the independent terminal and use its transaction directory name for status. Do not blindly submit apply again.

```bash
/workspace/.dots-tailscale/dots-ts runtime status --id TRANSACTION_ID --json
```

`TRANSACTION_ID` is the 32-character ID returned by apply. Status is local and does not request another update.

| State | Meaning |
|---|---|
| `prepared` / `running` | Staging/acknowledgement or the worker is in progress. |
| `applied` | Local readiness, identity/configuration checks and installed pair hashes passed. External SSH/ping verification is still pending; inspect any cleanup warning. |
| `failed_rolled_back` | No candidate daemon execution was attempted; the previous pair was restored and checked. Inspect any cleanup warning. |
| `rejected` | This transaction was refused before stopping the daemon or replacing the installed binary pair; this can happen in the caller or in an acknowledged worker. Inspect the error and any transaction ID. |
| `status_unavailable` / `ack_uncertain` | The result cannot be determined from this response. Keep the recovery terminal and use the returned valid ID to inspect the transaction; do not treat it as cancelled or resubmit apply. |
| `manual_recovery_required` | Execution, process ownership, durable progress or state compatibility is uncertain. Use the independent terminal; do not repeat apply or remove the fence merely to make startup work. |

After `applied`, open a fresh external SSH connection, run Tailscale ping, and check any application path you depend on. Resume external recovery jobs only after these checks. Local `Running` alone is not end-to-end recovery.

## Offline transfer

If Dots cannot reach the package host, prepare a private directory on a trusted machine with the official archive and its checksum, then transfer both files. For the example above the directory contains:

```text
tailscale_1.104.4_amd64.tgz
tailscale_1.104.4_amd64.tgz.sha256
```

The bundle directory must be owned by the executing user with mode 0700. Files must be regular, owned by that user, not symlinks, and not group/world writable. Choose a directory outside the installation and transaction directories.

```bash
/workspace/.dots-tailscale/dots-ts runtime apply \
  --version 1.104.4 --bundle /workspace/staged-tailscale \
  --confirm-recovery-terminal --json
```

This is an explicit operator-trusted bundle, not an assertion of latest-release provenance. The checksum can be the official bare SHA-256 or a single matching archive-name checksum line. HTTPS plus a same-source checksum verifies transport/integrity consistency; it is not independent publisher-signature verification. Do not use an untrusted archive merely because a supplied checksum matches it.

## Failure and preservation boundaries

The updater does not copy, replace, print or reset node state or SSH private keys. It retains the custom socket, userspace arguments, daemon proxy/CA environment, public node identity and SSH host public key. Shim and OpenSSH are preserved. Environment values remain in memory, not in the journal.

`.runtime-update-block` is written before daemon shutdown. Mutating startup/proxy/package operations refuse this marker, including after interruption or a new boot. Only the worker's verified success or a verified pre-execution rollback clears its own marker. Check/status remain available. A missing process after a boot change does not authorize repeating a transaction.

Once candidate daemon execution is attempted, the new binary may have changed its state format. A readiness/identity failure before the terminal commit attempts to stop that candidate and leaves Tailscale offline; if stopping cannot be confirmed, process/socket state remains unknown. After a verified pair and terminal journal are committed, a fence cleanup failure does not stop the healthy daemon. It records `cleanup_review` / `fence_cleanup_failed` when possible and tries to retain its own fence. The final directory sync and a storage failure can still require operator review. This tool does not assume backward compatibility or automatically restart old binaries against that state. A failure before the verified terminal commit leaves a fenced manual-recovery result and the code backups. After that commit, the journal retains `applied` (or `failed_rolled_back` for a verified rollback). Cleanup may add `fence_cleanup_failed` or `worker_cleanup_failed`. When its own or an invalid fence remains, status projects `manual_recovery_required`; without such a fence, status can retain the terminal result with a cleanup warning. Fence restoration is best effort and the verified daemon is kept running. Such a warning makes the CLI exit nonzero and requires cleanup review, not an automatic version change. Never restore an old state snapshot or reauthenticate just to hide this failure. Recovery requires inspecting the journal, paired binary hashes, daemon/socket identity and upstream state compatibility from the independent terminal.

No live runtime upgrade or failure injection is established by offline tests. Future opt-in scheduling belongs to #31 and must reuse this transaction after its supported live paths are validated. VM replacement can remove the running updater itself; files surviving in `/workspace` do not guarantee an update worker will resume.

## Manual resolution runbook

This procedure requires the independent cloud terminal, an operator who can inspect the relevant process namespace, and all external recovery/update jobs paused. A valid old or candidate archive is not enough to determine whether downgrading its state is safe. Unknown process/socket visibility, damaged journals, or mismatching hashes are stop conditions: retain the files and ask for maintainer/upstream help rather than guessing.

### 1. Freeze competing work and inspect evidence

Take the same installation lock in the recovery shell and keep that shell open:

```bash
D=/workspace/.dots-tailscale
test -f "$D/.operation.lock" && test -O "$D/.operation.lock" &&
  test ! -L "$D/.operation.lock" &&
  test "$(stat -c '%a:%h' -- "$D/.operation.lock")" = '600:1' &&
  exec 9<>"$D/.operation.lock" && flock -n 9
```

Continue only if the lock was acquired. Use the transaction ID from status or the private `.runtime-transactions/` directory. Read its `journal.json` locally and inspect `phase`, `state_compatibility`, `worker`, `baseline_version`, `baseline_binary_hashes`, and `binary_hashes`. Missing fields are unknown, not permission to proceed. Compare boot ID and exact PID/start-time/UID/executable identity; a hidden `/proc` entry is not proof that a process exited. Confirm no updater worker remains. Never use broad `pkill` commands or delete an unidentified socket.

For both installed binaries, and any stage/backup pair you intend to use, verify ordinary non-symlink files, current-user ownership, one link, mode 0700, expected SHA-256 and matching version outputs. These are binary/public metadata checks; do not open state or private keys. The candidate pair must match `binary_hashes`; the old pair must match `baseline_binary_hashes`. If those records are unavailable, establish provenance from a trusted official artifact before proceeding.

### 2. Choose the permitted recovery direction

| Durable evidence and actual state | Permitted operator action |
|---|---|
| Worker died before cutover; no candidate execution intent; current approved pair and daemon are unchanged | Keep the pair/process. Resolve only the abandoned transaction after verifying no worker remains. |
| Valid journal proves `not_executed`; daemon/socket absence is positively established | Restore **both** verified backup binaries, then resume the baseline version. Do not infer this case merely from a missing journal or fence. |
| Candidate daemon execution may have happened (`unknown`, readiness failure, interrupted post-intent work) | Keep or restore **both** binaries of the same candidate version from the verified stage. Do not start old binaries against this state. If the candidate is incompatible, retain the fence and obtain an upstream-supported recovery plan. |
| A verified terminal commit exists with `fence_cleanup_failed` or `worker_cleanup_failed` | If the installed pair and running daemon match the committed version, leave that healthy daemon running and use step 3 to archive the reviewed record and remove only its own remaining fence, if any. |

If a daemon or socket belongs to an unknown process, stop here. If binaries must be restored, first establish that no daemon is running; copy each selected file to a new private temporary filename, validate it, then atomically rename both while holding the operation lock and fence. Do not overwrite a running executable's inode. Remove a leftover `.new` file only after proving that it belongs to this abandoned operation and is not used by a worker.

### 3. Preserve the transaction and release the fence

Only after the selected pair, version, process/socket state and permitted direction above are established:

1. Flush the installed files and installation filesystem (`sync -f "$D"` on the supported Linux environment). If this fails, leave the fence and stop.
2. Write a private operator resolution note recording the ID, selected version/hashes and reasoning; keep the original journal unchanged.
3. Create/verify a current-user-owned 0700 `$D/.runtime-resolved` directory. Move this transaction to the previously absent `$D/.runtime-resolved/TRANSACTION_ID` on the same filesystem; never delete it or overwrite another record. Moving it out of `.runtime-transactions` removes the abandoned record from the automatic prior-transaction gate. Flush the filesystem again before releasing a fence.
4. If `.runtime-update-block` exists, read it without following links and verify its schema, transaction ID and stored boot ID exactly match the archived journal. Remove **only that matching marker**. A different, malformed or unreadable marker is a stop condition. Flush the directory/filesystem; on failure keep the verified pair and diagnose storage rather than changing versions.
5. Release the shell lock (`flock -u 9`, then `exec 9>&-`). When no daemon is already running, run the installed `bash "$D/start.sh"` once. It will use the verified installed pair. Do not invoke old checkout scripts or old copies of `dots-ts update`; they do not understand the runtime fence and can replace guarded source files.
6. Confirm local readiness, a fresh external SSH connection and ping, then resume external jobs. Keep the archived journal and both code pairs until the result and rollback compatibility are understood.

After archival, `runtime status --id` no longer looks up that record; inspect the private archived journal and resolution note instead. Releasing an abandoned transaction is an explicit operator decision, not an automatic claim that the old attempt succeeded. If the same candidate still cannot start, do not repeat indefinitely or restore old state: collect the private daemon log and seek compatibility guidance.

## Operation notes

- SSH invocation is intentionally allowed because cutover uses a detached worker and explicit independent-terminal acknowledgement. Unlike proxy apply, this command is designed to survive loss of its initiating SSH session. The acknowledgement does not verify that a second terminal exists.
- `tailscaled` output remains in the validated private runtime `tailscaled.log`; status does not expose raw output. Keep this log private when diagnosing a failed startup.
- Transactions retain stage and backup binaries (about twice the uncompressed pair size). There is no automatic cleanup. Archive only resolved, inactive transactions as described above, and remove old code backups only after an operator-approved retention decision. Never sweep active transaction directories.
- During the short spawn/worker-identity handoff, a status read can conservatively report manual review before the worker record is visible. Wait for the bounded acknowledgement window and reread status; do not mutate files because of this transient observation.
- One [live normal-path trial](validation/runtime-update-trial-20261026.md) successfully upgraded 1.102.4 to 1.104.2 and independently confirmed SSH/ping recovery. Readiness timing distributions, failure paths and state downgrade compatibility remain unverified. The normal proxy readiness budget is bounded; a timeout is not proof that the version can never work. Resolve it through the independent terminal and the phase-specific procedure above.

A staging KeyboardInterrupt is recorded as rejected when possible. Abrupt caller termination (including SIGHUP/SIGKILL) or storage failure can still leave an abandoned prepared transaction; use the pre-cutover row of the manual runbook after proving no worker remains. Once an acknowledgement is visible, ending the caller does not cancel the detached worker.
