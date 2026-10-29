# Experimental external recovery

[한국어](experimental-recovery.ko.md)

## v0.8.0 development integration

The new local coordination path is an opt-in development candidate, not a change
to existing tasks or the published v0.7.1 package. Follow the
[setup and publication gates](recovery-support-readiness.md) and
[local lifecycle guide](recovery-lifecycle.md). A separately deployed broker
keeps its legacy startup command unless `recovery_local_coordination: true` is
explicitly configured after the matching local helper is ready. Use the versioned
[English](../research/mcp-events/coordinated-recovery-instructions.en.txt) or
[Korean](../research/mcp-events/coordinated-recovery-instructions.ko.txt) coordinated
instruction template only in that mode. The older trial instructions below
remain historical/legacy guidance; switching packages does not rewrite them.

The coordinated local execution budget and external claim budget remain separate.
Switch both actor paths and stop the old private watchdog before describing
local attempts as shared. A local-ready result still requires external SSH/ping
confirmation. A second real ChatGPT account is unavailable for this milestone's
trial, so new-account setup and general support remain unverified; the clean
two-owner fixtures are not evidence of that live gate.

This is an **advanced-user experiment** for asking Dots to restart an existing dots-tailscale installation after an externally observed outage. It requires infrastructure outside Dots and an authorized Dots task. It is not enabled by installing the normal package, and there is no one-click installer or guarantee that every outage will recover.

## What has been observed

The maintainer's trial has exercised event delivery, Dots execution of the existing startup command, and subsequent external SSH and Tailscale ping recovery. Early attempts required SSH reauthentication and task-instruction corrections; those attempts do not establish fully unattended operation. A changed boot identity was observed, but it cannot distinguish reboot, VM replacement, or platform restore. Recovery after arbitrary VM replacement and setup by another account remain unverified. Callback HTTP 200 means receipt, not execution. An earlier read-only response arrived about 19 minutes later; this is not a fixed polling interval or a recovery deadline.

Detailed evidence and open work: [#20](https://github.com/abruption/dots-tailscale/issues/20), [#43](https://github.com/abruption/dots-tailscale/issues/43), [watchdog coordination #44](https://github.com/abruption/dots-tailscale/issues/44), and [experimental release scope #45](https://github.com/abruption/dots-tailscale/issues/45).

## Requirements and setup sequence

1. Install and authenticate dots-tailscale in Dots using the [usage guide](usage.md). Independently verify its SSH host key and confirm a new SSH connection and Tailscale ping. Existing installation, node state, and SSH identity must remain available when recovery runs.
2. Provide an external Linux server, a public HTTPS hostname such as `dots-events.example.com`, Caddy, and a private Authelia SSO deployment. Configure the exact account, OAuth client, redirect URI, and resource. Caddy must authenticate both `/authorize` and `/oauth/consent`, remove client-supplied identity headers, and overwrite `X-Dots-Client-IP` with `{client_ip}` on both upstream paths. The hardened broker uses a dedicated Unix account and a protected Unix socket.
3. Review the [broker configuration example](../research/mcp-events/config.example.json), [security deployment notes](../research/mcp-events/security-hardening.md), and code under `research/mcp-events/`. The example disables recovery. The privileged bootstrap migrates an **existing trial installation**; it is not a fresh-host installer. Fresh deployment from these materials has not been independently reproduced with another account. Adapt and validate the service paths, dependency locks, reverse proxy and SSO configuration before applying anything.
4. Provide an always-on observer with tailnet access that independently checks SSH and ping. Configure the separate [HTTPS relay and machine credential](../research/mcp-events/relay-transport.md). Keep credentials and runtime files outside the checkout in private directories. Do not publish tokens, callback URLs/secrets, node state, or private keys. The Mac implementation requires an awake, online Mac and a logged-in GUI session for its LaunchAgents.
5. Connect the custom MCP app in Dots and authorize `events:observe` and `recovery:execute`. Configure only this app's bounded recovery tools for the intended unattended task. Review the [saved task instructions](../research/mcp-events/dots-recovery-instructions.ko.txt) and [continuous trial contract](../research/mcp-events/continuous-trial.md). Active event subscriptions and OAuth refresh must continue working; a running broker alone is insufficient.
6. Begin with normal observations and a read-only event check. Confirm that healthy SSH/ping creates no recovery attempt. Establish independent access to the Dots cloud terminal before any deliberate fault test. Do not assume that an event subscription or a successful synthetic callback proves actual recovery.

### OAuth admission and proxy trust

In the hardened Unix-socket mode, the application trusts `X-Dots-Client-IP` only over the protected socket, with no TCP peer address and exactly one valid IP value. It normalizes that address for the admission key, which is not an authenticated OAuth identity. `/authorize` and `/oauth/consent` require the Authelia identity and share a separate eight-request in-flight limit. Public `/token` and `/revoke` requests use a separate pool: at most eight in flight globally and one per IP, with a per-IP bucket of 16 requests burst/refilling at one per second and a global bucket of 64 burst/refilling at 16 per second across IP changes. The bounded table holds 256 IP entries and evicts idle entries only. OAuth metadata is outside these pools. Missing, duplicate or invalid IP values are denied with 403; pool or bucket denials return 429 with `Retry-After: 5`.

If Caddy is behind a CDN, configure trusted-proxy ranges and client-IP resolution only for that actual upstream; otherwise Caddy may supply the edge IP and combine unrelated users into one budget. Shared NAT addresses can also combine clients. Distributed sources or exhaustion of the upstream/CDN can still cause an outage: these application limits do not guarantee denial-of-service protection. In legacy loopback mode the application ignores `X-Dots-Client-IP`, uses the actual loopback peer address, and rejects non-loopback peers. Clients relayed by a loopback proxy may share the same bucket, and legacy mode trusts local processes without the Unix-account boundary. See the [security notes](../research/mcp-events/security-hardening.md) for the full limits and boundary.

The external monitor sends observations; the broker grants a bounded claim; Dots executes the fixed command in its own cloud computer:

```bash
bash /workspace/.dots-tailscale/start.sh
```

The broker does not execute a remote shell. The claim's expiry must be respected by Dots; it is not a runtime gate inside that shell.

## Policy and operating status

The default is **three claims per rolling 24 hours, at least 3,600 seconds apart**. The maintainer's trial uses an explicit 12-claim limit with the same interval. Successful attempts also count. Operator configuration keys are `recovery_max_claims_per_24h` and `recovery_cooldown_seconds`; changes require reviewed server configuration, not a prompt asking Dots to bypass policy.

Use the MCP `get_current_recovery` tool to inspect `observation_fresh`, `claimable`, `eligibility_reason`, `retry_at`, and `policy`. A future `retry_at` is advisory. Even after that time, Dots must reread fresh state and obtain an accepted claim. A reminder can carry the same incident ID; it does not create new execution authority. The original incident, claims and delivery history remain intact. An already claimed incident must not be claimed or executed again, including when its result is unknown.

For the hardened Linux installation, check the dedicated units:

```bash
systemctl is-active dots-events-recovery.socket dots-events-recovery.service
systemctl status dots-events-recovery.service --no-pager
```

Review private logs locally rather than pasting raw output into issues. The relay's `state.health.json` records delivery freshness, failure category, consecutive failures and subscription information. Check that delivered observations advance and are no more than 150 seconds old. A notifier shares the monitoring host and cannot alert while that host is down.

Homebrew or another package manager can also replace the Python interpreter while a relay is running. The October 25 trial stopped delivering after its old runtime directory was removed, and a relay-only restart restored delivery. After an interpreter upgrade, verify the configured Python executable, restart the relay using the command below, and check fresh acknowledgements. Do not assume that a running launchd job is healthy. The exact child exception in that incident was unavailable.

When updating a Mac relay, install its matching helper modules, preserve private configuration/state, and restart the loaded job: copying Python files does not replace an already running process. Use your installed label, for example:

```bash
launchctl kickstart -k "gui/$(id -u)/com.example.dots-recovery-relay"
```

`com.example.dots-recovery-relay` is a placeholder. Find the actual Label in your installed plist. Confirm a new process and a newer acknowledged observation after restart. Broker upgrades and rollback must preserve OAuth, incident and claim ledgers; never reset them to force another attempt.

## When recovery stalls

| Observation | Next check |
|---|---|
| Relay delivery is stale | Observer freshness, Mac sleep/login state, HTTPS reachability, machine credential and relay health. A delivery failure is not proof of a Dots outage. |
| Event received, no claim | Subscription/task status, Dots cloud-terminal access, preflight report, `eligibility_reason` and policy. |
| Policy blocks a claim | Wait for the reported eligibility conditions and fresh observations. Do not report a server policy denial as a missing-file or permission preflight failure. |
| Claim exists, result unknown | Preserve that claim and investigate the Dots report. Do not blindly rerun the command. |
| Command reports success | Verify a new external SSH connection and ping; only `externally_recovered` confirms recovery. |
| Local daemon says Running but external probes fail | Treat local readiness and external connectivity separately; retain observations and inspect platform/network state. |
| Installation/state missing or login required | Stop automatic attempts and use the manual installation/authentication procedure after operator review. |

The permitted startup includes its existing directory/file mode normalization and normal socket, PID, log and state updates. It does not authorize reinstalling, replacing keys, deleting state, changing network policy, or starting the deliberately stopped experimental watchdog.

## Stop and rollback

Disable the Dots recovery task first. Confirm its subscription is inactive or has expired and check for an outstanding claim before assuming execution has stopped. Disable the dedicated relay/observer/notifier jobs using their installed plists, for example:

```bash
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.example.dots-recovery-relay.plist"
sudo systemctl disable --now dots-events-recovery.socket
sudo systemctl stop dots-events-recovery.service
```

Run the launchd command on the Mac and systemctl commands on the broker host. Substitute the actual plist; manage separately installed observers/notifiers individually. These actions do not stop Tailscale in Dots. Retain private incident records and unknown results. The IPv6 guard is a separate security service: stopping recovery must not remove firewall protection. Follow the reviewed [security rollback procedure](../research/mcp-events/security-hardening.md) for infrastructure changes, including partial failures and preserved latest ledgers.
