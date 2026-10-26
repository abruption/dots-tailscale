# PR #51 live runtime update trial — 2026-10-26

This is one operator-authorized normal-path trial on an existing Dots cloud computer. Tested source commit: `2ef1c77710bf00a7f14f901bda1440968bb30dc7`. It is not a general state-compatibility, rollback or VM-replacement guarantee.

## Preparation

- Existing installed package: **0.4.0**, source integrity matching its manifest; Tailscale **1.102.4**.
- Official stable metadata was rechecked and selected **1.104.2** with the matching amd64 archive name.
- An independent cloud-computer GUI terminal was opened and a read-only command executed. Its UID/boot identity matched the SSH environment.
- The Dots recovery task was explicitly paused for the test, without changing other tasks or restarting the stopped watchdog.
- The reviewed four-source bundle plus manifest was transferred to a private staging directory. The existing package updater applied it as **0.6.0-dev**, retaining Tailscale 1.102.4. A fresh external SSH connection and the new runtime check succeeded before binary cutover.

## Runtime operation

The installed candidate ran:

```bash
/workspace/.dots-tailscale/dots-ts runtime apply \
  --version 1.104.2 --proxy platform \
  --confirm-recovery-terminal --json
```

The online platform-proxy path succeeded; no local archive fallback or in-place manual overwrite was used. The caller returned a `prepared` transaction ID after **5.825 seconds**, including SSH command startup, download/staging and handoff. This is not a daemon stop/start duration measurement.

The worker journal reached `applied` / `complete` with `state_compatibility=current_version_only`, no error or cleanup warning, and no remaining runtime fence. Its final timestamp was **13:55:10 KST** (journal precision is whole seconds).

The installed CLI and daemon both reported **1.104.2**. Their SHA-256 values matched the official artifact already checked by the isolated artifact smoke:

| Binary | SHA-256 |
|---|---|
| `tailscale` | `790cf117e5ddb64421faed041490274e65c47ad6c90e9d4ce2cb3968ad06e535` |
| `tailscaled` | `2b391d03f7cf25c4e0d11d344d577deca94436efad1ae9af002e46269d951ba3` |

## Independent external observations

A separate Mac observer opened fresh strict-host-key SSH connections and ran Tailscale ping at approximately five-second intervals. Relay responses were accepted with `--until-direct=false`; an unavailable direct route is not a reachability failure in this userspace/proxy setup.

| Observation (KST) | Result |
|---|---|
| 13:55:08.312 | SSH and relay ping each timed out in one sample |
| 13:55:13.322 / 13:55:13.326 | Next SSH / ping samples succeeded |
| Through 13:56:13.410 | All subsequent samples succeeded |

There were 43 SSH samples and 37 valid ping samples, with one failure in each. Initial ping samples that mistakenly required a direct route returned nonzero despite a valid DERP pong; those probe-mode artifacts were preserved privately and excluded from outage interpretation. The sample gap does not establish an exact five-second outage or a recovery SLA.

## Preservation and restoration checks

- The boot identity remained unchanged during this trial.
- The digest of public node identity fields (`ID`, `PublicKey`, `TailscaleIPs`) matched before and after.
- The SSH host public key digest matched. SSH and shim-CA private-key inode/UID/mode metadata were unchanged; private-key contents were not read or copied for this comparison.
- Shim and OpenSSH process identities were preserved across the **runtime** update; only the Tailscale daemon changed. The earlier package update had intentionally replaced the shim process.
- Post-update desired/running application-proxy ports were both unset and `restart_required=false`; the runtime worker also passed its before/after proxy-configuration comparison.
- Source package integrity matched the candidate manifest. State remained present, without manual state restoration, key recreation or reauthentication.
- The recovery task was resumed. A subsequent monitor acknowledgement reported an active subscription and healthy delivery. Cloud-computer control was returned to Dots.

`runtime status` continues to report `external_ssh=pending` because it is a local journal interface; the independent external checks above are recorded separately rather than rewriting the journal to imply in-process external verification.

## Limits

No failure injection, automatic downgrade, old-state restoration, separate application-specific HTTPS test, VM replacement, or long-duration upgraded-runtime soak was performed. This validates one successful 1.102.4 → 1.104.2 transition in the maintainer's environment. It does not establish the timing distribution of daemon shutdown/readiness, other-account compatibility, all future versions, or remediation of the vulnerabilities described by Tailworm.
