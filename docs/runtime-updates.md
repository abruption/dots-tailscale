# Manual Tailscale binary updates (development)

[한국어](runtime-updates.ko.md)

This feature is under development for #50 and is not in the v0.5.0 release. It updates the installed `tailscale` and `tailscaled` binaries together. `dots-ts update` continues to manage the dots-tailscale source package; `dots-ts runtime` manages the Tailscale pair. Neither command enables automatic updates.

## Install the matching tool first

Use a reviewed dots-tailscale package containing this feature, including all four source files and its matching `source-manifest.json`. The installed bundle must match the tool invoking the update. Running a new checkout's CLI against an older installed package is refused: the older startup script would not know how to block an interrupted binary update.

This change keeps the default installer pin at 1.102.4, allowing the tool itself to be distributed through the existing code-only updater. It does not silently upgrade existing nodes or change the Tailscale version used for new installations. A later change of the default pin needs its own package migration validation; the existing package updater's binary-version declaration guard remains in place.

## Check without changing the installation

```bash
/workspace/.dots-tailscale/dots-ts runtime check --json
```

The default path uses the platform proxy (`UPSTREAM_PROXY`, otherwise `http://browser-proxy:8889`) explicitly. `--proxy direct` opts into direct HTTPS; it does not bypass platform egress restrictions. An explicit `check --version 1.104.2` compares against that number without validating that it is a published or latest release. An unavailable installed version must be inspected before applying an update.

Online apply requires an exact version matching the official stable metadata observed at the start of that request. Numeric versions with an unstable minor number, prerelease suffixes, downgrades and same-version reinstalls are refused. A newer stable release appearing later does not change the already selected archive URL.

## Apply manually

First open and verify an independent Dots cloud terminal that will remain usable if Tailscale SSH connectivity disappears. Pause external recovery jobs if configured, and keep that terminal available through external verification. The confirmation flag records your acknowledgement; the CLI cannot verify GUI control or that external jobs were paused.

For example, after reviewing `runtime check` and the upstream advisory:

```bash
/workspace/.dots-tailscale/dots-ts runtime apply \
  --version 1.104.2 --proxy platform \
  --confirm-recovery-terminal --json
```

The version is an example, not a claim that 1.104.2 resolves Tailworm. That report tested 1.104.2 itself. Check the current [Tailscale security bulletins](https://tailscale.com/security-bulletins) before selecting a version.

Apply requires Linux amd64, Python pidfd support, a private writable installation/runtime context, a complete verified source package, and a uniquely identified running daemon/shim/OpenSSH with the expected configuration. It stages and checks the archive before stopping anything, verifies both version commands, and backs up both existing binaries. The same operation lock coordinates startup, proxy apply and package updates.

The CLI prints a transaction ID and flushes stdout before acknowledging the detached worker. This orders local output before cutover; it does not prove that a remote terminal received the ID. If output is lost, inspect `.runtime-transactions/` from the independent terminal and use its transaction directory name for status. Do not blindly submit apply again.

```bash
/workspace/.dots-tailscale/dots-ts runtime status --id TRANSACTION_ID --json
```

`TRANSACTION_ID` is the 32-character ID returned by apply. Status is local and does not request another update.

| State | Meaning |
|---|---|
| `prepared` / `running` | Staging/acknowledgement or the worker is in progress. |
| `applied` | Local readiness, identity/configuration checks and installed pair hashes passed. External SSH/ping verification is still pending. |
| `failed_rolled_back` | No candidate daemon execution was attempted; the previous pair was restored and checked. |
| `rejected` | The operation was refused. Inspect the fixed error code and journal. |
| `manual_recovery_required` | Execution, process ownership, durable progress or state compatibility is uncertain. Use the independent terminal; do not repeat apply or remove the fence merely to make startup work. |

After `applied`, open a fresh external SSH connection, run Tailscale ping, and check any application path you depend on. Resume external recovery jobs only after these checks. Local `Running` alone is not end-to-end recovery.

## Offline transfer

If Dots cannot reach the package host, prepare a private directory on a trusted machine with the official archive and its checksum, then transfer both files. For the example above the directory contains:

```text
tailscale_1.104.2_amd64.tgz
tailscale_1.104.2_amd64.tgz.sha256
```

The bundle directory must be owned by the executing user with mode 0700. Files must be regular, owned by that user, not symlinks, and not group/world writable. Choose a directory outside the installation and transaction directories.

```bash
/workspace/.dots-tailscale/dots-ts runtime apply \
  --version 1.104.2 --bundle /workspace/staged-tailscale \
  --confirm-recovery-terminal --json
```

This is an explicit operator-trusted bundle, not an assertion of latest-release provenance. The checksum can be the official bare SHA-256 or a single matching archive-name checksum line. HTTPS plus a same-source checksum verifies transport/integrity consistency; it is not independent publisher-signature verification. Do not use an untrusted archive merely because a supplied checksum matches it.

## Failure and preservation boundaries

The updater does not copy, replace, print or reset node state or SSH private keys. It retains the custom socket, userspace arguments, daemon proxy/CA environment, public node identity and SSH host public key. Shim and OpenSSH are preserved. Environment values remain in memory, not in the journal.

`.runtime-update-block` is written before daemon shutdown. Mutating startup/proxy/package operations refuse this marker, including after interruption or a new boot. Only the worker's verified success or a verified pre-execution rollback clears its own marker. Check/status remain available. A missing process after a boot change does not authorize repeating a transaction.

Once candidate daemon execution is attempted, the new binary may have changed its state format. This tool does not assume backward compatibility or automatically restart old binaries against that state. A failure at or after that point leaves a fenced manual-recovery result and the code backups. Never restore an old state snapshot or reauthenticate just to hide this failure. Recovery requires inspecting the journal, paired binary hashes, daemon/socket identity and upstream state compatibility from the independent terminal.

No live runtime upgrade or failure injection is established by offline tests. Future opt-in scheduling belongs to #31 and must reuse this transaction after its supported live paths are validated. VM replacement can remove the running updater itself; files surviving in `/workspace` do not guarantee an update worker will resume.
