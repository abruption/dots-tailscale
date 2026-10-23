# Dots lifecycle research — 2026-10-23

Tracking: [#20](https://github.com/abruption/dots-tailscale/issues/20). Recovery transport and execution: [#43](https://github.com/abruption/dots-tailscale/issues/43). Coordination policy: [#44](https://github.com/abruption/dots-tailscale/issues/44).

This record separates current SSH observations from earlier external records and Dots-reported execution. It does not establish that a particular VM was replaced, that all workspace files persist, or that unattended recovery is reliable.

## Current read-only observation

On 2026-10-23, a new SSH connection succeeded with strict host-key checking. The public host-key fingerprint matched the independently established fingerprint. No private key, authorized-key contents, node-state contents, or credential values were read or hashed. Only the four public executable sources and their public source manifest were hashed.

- All four installed sources and the manifest match the repository's **v0.4.0** manifest, obtained from the local Git tag. Installed `setup.sh` maps to release `tailscale-setup.sh`, and `shim/shim.py` maps to release `shim.py`.
- Bundled Tailscale version: **1.102.4**. A socket-specific local status query returned **Running**.
- SSH execution UID: **1000**; PID 1 reports **bwrap**. The socket is visible from this SSH context. PATH does not include a `/workspace` entry. Namespace identities were compared privately and are not published.
- Current boot identity matches the post-recovery boot already observed on October 23, and differs from the previous healthy environment's boot identity. These are boot transitions; they do not distinguish a reboot, VM replacement, or a platform restore.

Three samples, ten seconds apart, were collected from **01:33:52 to 01:34:12 UTC** (10:33:52–10:34:12 KST):

| Component | RSS KiB | Threads | Open descriptors |
|---|---|---|---|
| shim | 19,424 → 19,424 → 19,432 | 7 → 7 → 7 | 10 → 10 → 10 |
| tailscaled | 45,532 → 45,532 → 45,532 | 14 → 14 → 14 | 20 → 21 → 20 |

During that interval, shim log size stayed at 39 bytes, tailscaled log grew from 67,282 to 67,425 bytes, and SSH log grew from 17,956 to 18,200 bytes. Active SSH probes can themselves create SSH log entries. A separate SSH listener sample was 2,028 KiB RSS, one thread, and eight descriptors; short follow-up samples are recorded in `observation.json`.

These are short baseline measurements, not a soak test, proof of no leak, or evidence that an existing application stream survived an outage. No components were restarted and no failures were injected.

## What the overnight recovery establishes

External records supplied by the coordinating session show last healthy SSH/ping at **2026-10-22 22:22:22 KST**, followed by failure at **22:23:27**. The relay had stopped delivering observations earlier, at **21:24**, while the server remained alive. After operator SSH reauthentication, a recovery event was delivered on October 23. Dots initially stopped before claiming because the recovery instructions conflicted with normal startup permission repair. After those instructions were clarified and a resume request was sent, Dots claimed at **09:33:49**, reported one successful start, and the external observer confirmed new SSH/ping at **09:34:25**.

This establishes a Dots-side recovery path after operator intervention. It does not establish a completely unattended end-to-end recovery. An HTTP 200 receipt is not execution, a Dots success report is not external recovery, and missing fresh observations must not be treated as a healthy target. Original timestamps and execution provenance remain in #43; this document does not substitute its own live re-test for those records.

The earlier deliberately stopped local watchdog was not expected to restart itself. An old heartbeat's `running` value is historical unless boot identity and live process identity match. Its bootstrap and budget coordination belong to #44.

## Five-theme evidence and remaining gates

| #20 theme | Established evidence | Still open / acceptance gate |
|---|---|---|
| State retention after transition | Current release sources remain intact; known SSH public identity remains usable; boot differs from earlier healthy environment. Earlier incidents showed stale or missing watchdog artifacts. | Record before/after public-source hashes, boot equality/change, state-file existence only, and public identity. Never infer complete workspace persistence or inspect state contents. A transition can be classified as a boot change without claiming VM replacement. |
| Independent startup | Dots executed the installed start path while external SSH/ping were unavailable, followed by external recovery. | One natural outage must recover with no authentication prompt, instruction edit, manual start, resume request, or event resend. Capture blocked-before-claim outcomes. Scheduled-task execution on a new environment remains a separate untested path. |
| Execution-context differences | Current SSH UID, PATH/socket visibility and namespaced context recorded. Earlier Dots report established its UID and script readability. | Collect contemporaneous agent, GUI terminal and SSH snapshots on the same source revision/boot; compare only sanitized booleans/categories. Do not assume socket, HOME or network visibility is shared merely because `/workspace` is visible. |
| Long connection / resource recovery | Fresh SSH works after recovery; current 20-second process baseline measured. Earlier same-boot component-exit tests are documented in #20. | Complete a declared observation period and inspect outage/recovery intervals. No current evidence proves an old stream survives, a connectivity-isolated Running daemon repairs itself, or a new boot bootstraps its watchdog. |
| Platform-proxy compatibility | Current strict SSH over the deployed stack succeeds with matched v0.4.0 sources. Sources define HTTP CONNECT, TLS/HTTP1.1 and Upgrade/WebSocket requirements and setup deadlines. | Publish a phase-to-error matrix and isolated proxy/transport regression coverage. Current success does not guarantee future platform policy, certificate, CONNECT or WebSocket compatibility. Exit-node research stays in #16. |

## Bounded next observation plan

Proposal only: no new daemon, schedule, persistent collector, or failure injection was installed by this work.

1. Observe for **24 hours**, with an explicit review at the end. Use the existing 60-second external observer; take process/log-size samples every **five minutes**, each remote command bounded to **15 seconds**. Keep only public metadata and aggregate network outcomes. No private raw environment or status output.
2. Bound this observation's exported evidence to **10 MiB total**, rotating summaries and retaining incident boundaries. If the limit is reached, stop the added evidence collector and record incomplete coverage. Do not stop the running recovery service automatically.
3. Use no fault injection during this passive interval. Keep the already configured maximum **three external claims per rolling 24 hours**, one-hour cooldown and one claim per incident. This is the external budget only; it is not a global bound combined with a future watchdog. No speculative retries after unknown outcomes.
4. Abort new recovery attempts if host keys change, source verification fails, required authenticated state/identity is missing, execution context cannot be established, or observed freshness/authorization is uncertain. Notify the operator; never regenerate identities or reinstall to make a test pass.
5. Stop additional active probes and request review if a component exceeds **256 MiB RSS**, **128 threads**, **512 descriptors**, or total runtime logs exceed **50 MiB**. These are experiment guardrails, not proven product limits. Record the crossing rather than restarting components or deleting logs automatically.
6. Record delivery, event receipt, claim, start outcome, independent SSH and ping separately. If no natural incident occurs, report **24-hour healthy observation only**; leave automatic recovery and transition gates open. If an outage occurs, keep its complete timeline and classify any operator involvement.

### Bounded collector

`collector.py` implements the resource portion of this plan. Deployment and the actual observation start are recorded separately by the operator; the baseline above is not its 24-hour result.

Copy `config.example.json` to a private, operator-owned **0600** file outside the checkout. Set `ssh_host` and explicit `ssh_hostname` to the previously verified research target, and `data_dir` to an existing private **0700** directory. Never place credentials in this config. The collector uses the existing local SSH authentication and strict known-host trust; it does not accept extra SSH options or modify trust files.

```sh
python3 collector.py --config /absolute/private/collector.json --once
python3 collector.py --config /absolute/private/collector.json
```

The first invocation persists its original 24-hour deadline before contacting the target. The second invocation resumes that same deadline. `--duration SECONDS` can shorten a new experiment, to at most 86,400 seconds; it never extends a previously initialized state. Do not delete/recreate its state to silently renew the window. A separate authorized run needs a separate data directory.

The collector holds a file lock, writes atomic `state.json` and 0600 `samples.jsonl`, and exits successfully after expiry or an experiment guardrail. A service manager must not restart it merely because it exited successfully. Config, lock, existing state and evidence files must be regular owner-only files; symlink inputs are rejected. The total sample file never exceeds 10 MiB. Private fixed-size state/lock metadata are additional to that evidence cap.

Each remote invocation runs under `timeout 15s`, with a 20-second outer SSH bound. Stdout is drained incrementally and retained to at most 65,536 bytes; exceeding the limit kills and reaps the SSH process immediately. Stderr is discarded at the process boundary. More than 32 matching remote processes produces a failed probe rather than a truncated healthy sample. Baseline source hashes and versions are collected only at the first successful sample and after a different boot hash. Subsequent samples return resource metadata, uptime, file-presence flags, socket presence and BackendState only. The private boot UUID is hashed before transmission. Only explicit public-source paths are hashed; symlink source files are rejected. Local schema projection drops unknown fields. SSH stdout/stderr failures are not copied into records, so a failed probe produces a generic error category without an authentication URL or private diagnostics. SSH failure does not run a recovery command.

Run the meaningful offline checks with:

```sh
python3 -m unittest discover -s research/lifecycle-20 -p 'test_collector.py' -v
```

The checks cover persistent deadlines, boot tracking, resource and evidence bounds, private-file safety and redaction. They do not establish a completed live observation or reproduce a platform transition.

## When #20 can close

Each of the five rows needs a bounded reproducible procedure, source revision, actual results, limitations, and linked English/Korean user guidance. Any observed transition must use appropriately narrow language. A pending long-term or platform-transition observation must remain open or be split into an explicitly tracked follow-up, rather than silently counted as verified. #43 must separately close its unattended authentication/delivery/execution gaps. #44 must separately implement local/external coordination. This baseline alone closes none of those issues.
