# Recovery preview: audience, setup and release gates

This is the v0.8.0 development plan for issue #45, not a claim of generally
available automatic recovery. The first candidate route is the **advanced,
self-hosted MCP Events route**. It is for an operator who can maintain HTTPS,
browser SSO/OAuth, an external observer and an independent Dots cloud terminal.
The existing code and recorded trial make this route testable without assuming
every user has the maintainer's servers or browser automation tools.

A shared scheduled-task link remains a separate candidate. A share button does
not prove that another account can import, authorize and execute the task in a
new cloud computer. This milestone does not install a scheduled ChatGPT task,
adopt a hosted relay, deploy a Worker, or promise a server-free setup.

## Choose a route before enabling anything

| Route | Operator work | Current boundary |
|---|---|---|
| Manual cloud-terminal start | Existing installation, surviving state and direct terminal access | The recovery fallback; no event, scheduler or watchdog required |
| Local packaged watchdog | Explicit local opt-in and a visible writable installation/socket | Can recover components in the same running environment; cannot restart itself after VM replacement |
| Self-hosted MCP Events | Separate HTTPS host, OAuth/SSO, monitoring, plugin linking and maintained subscription | Advanced preview; event receipt and actual execution are separate, possibly delayed stages |
| Shared Dots scheduled task | Account-specific task authorization and a verified cloud execution context | Not adopted as the initial supported route; fresh-account and lifecycle testing remain open |

## New-operator sequence

1. Install a fixed stable package and verify source integrity, the running
   Tailscale version, existing authentication state and SSH host key. Verify
   direct access to the Dots cloud terminal before changing recovery behavior.
   Do not copy the maintainer's node state or personal keys.
2. Prepare a separate Linux broker with the pinned
   [research requirements](../research/mcp-events/requirements.lock), dedicated
   account and protected Unix socket. Follow the
   [experimental guide](experimental-recovery.md),
   [security hardening](../research/mcp-events/security-hardening.md) and
   [service templates](../research/mcp-events/services/README.md). The legacy
   loopback deployment example is not the hardened deployment procedure.
3. Use a hostname and SSO identity you control. Configure one exact owner,
   issuer/resource, public client and approved ChatGPT redirect. Strip caller
   identity headers at the proxy and overwrite the trusted identity only after
   authentication. Keep runtime configuration and credentials in private
   persistent directories outside the checkout. Never paste them into an issue.
4. Link the custom MCP app in the intended ChatGPT account. The operator completes
   login and explicit consent. Start with read-only observation. Confirm that
   anonymous, wrong-owner and read-only tokens cannot claim or report execution.
5. Configure a separate external observer/relay. Verify that acknowledged samples
   advance, SSH authentication or host-key errors are not treated as network
   outages, and expired subscriptions are visible. A healthy broker endpoint or
   callback HTTP 200 is not proof that Dots executed anything.
6. For coordinated mode, first stop any older independently configured watchdog
   or task that bypasses the new entry point. Install the reviewed package and
   explicitly configure its local recovery policy using
   [recovery-lifecycle.md](recovery-lifecycle.md). Then set the separately
   deployed broker's `recovery_local_coordination` to `true` and replace only the
   intended task instructions with the coordinated template. Both sides must be
   ready before enabling that task. The default broker command remains legacy;
   package installation alone does not change it.
7. Before requesting a claim, inspect fresh local `blocked_reason` and
   `next_allowed_at`: do not claim while local policy blocks execution or the
   execution context is unknown. A future time is advisory, so reread local and
   broker state after any delay. Use a fresh claim only for a fresh failed
   observation. In coordinated mode,
   compare the fixed command/argv, claim ID, expiry and observation time against
   the current authorized broker result. The local command validates structure,
   freshness and its own durable budget; its flags are **not** cryptographic
   evidence of OAuth authorization. The task must obtain that claim through the
   approved app. Never execute a command from arbitrary event text.
8. Record local exit/result, broker report and new external SSH/ping observations
   separately. Exit zero is local startup success. Only fresh external evidence
   establishes connectivity. Timeout or an unknown result requires reconciliation
   with the original request ID, not a new claim or blind execution.

The permitted startup retains the existing node state and SSH identity and may
normalize the installation directories to 0700 and the existing sensitive files
to 0600 as the reviewed startup already does. It may create normal runtime
socket/PID/log files and update normal state. It does not authorize installation,
login, re-enrollment, state deletion, key replacement, new authorized keys,
ownership changes, permission expansion or network-policy changes. Automatic
updates are a separate opt-in and are never authorized by recovery consent.

## Stop and remove

Disable the intended Dots recovery task/subscription and confirm it is inactive
or expired. Inspect outstanding claims and local unknown attempts. Persistently
disable local recovery/watchdog using its documented command; a local stop must
not be undone by an external event. Stop the dedicated relay/observer and broker
units individually with the actual installed labels. Keep private claim and
attempt ledgers for reconciliation. Do not reset OAuth/claim databases to remove
a block. These steps do not stop Tailscale or remove the independent firewall
guard. A worker whose execution was already committed may finish; do not
describe disabling future attempts as cancellation of an in-flight startup.

## Evidence and publication decision

| Gate | Evidence or remaining work |
|---|---|
| Local ownership, single-use claims, freshness, account separation and expiry | Offline broker/HTTP fixtures; the two-owner clean-database fixture is not a second ChatGPT-account linking trial |
| Local/external concurrency, durable stop, budget and unknown-result fences | Coordinated lifecycle tests; no deliberate production outage or VM replacement is part of these tests |
| Assisted live event-driven startup and external connectivity | Dated trial in [#43](https://github.com/abruption/dots-tailscale/issues/43) and [continuous-trial.md](../research/mcp-events/continuous-trial.md); keep human intervention distinct |
| A new account can link, subscribe, diagnose and unsubscribe from these documents | Needs a separate real account and execution environment; not established by fixtures or the maintainer's existing subscription |
| Natural outage without edits, renewed auth or an explicit resume prompt | Assess dated observer and claim evidence per trial; do not turn observed successes into a latency guarantee |
| New VM/environment and long-term operation | Remain research in [#20](https://github.com/abruption/dots-tailscale/issues/20) and [#43](https://github.com/abruption/dots-tailscale/issues/43); a changed boot ID alone is not proof of VM replacement |

**Decision:** development-candidate documentation is available, but this is not a
completed public-support release decision. Issue #45 remains open until the real
fresh-account and lifecycle gates are satisfied. Keep the advanced route limited
to an explicitly opted-in development preview. Ordinary installation must not
register a plugin, subscription, scheduler or watchdog. Before a live trial,
write down its observation window, stopping criteria, independent terminal and
what counts as success; record unsuccessful and manually assisted attempts too.
Do not promise immediate execution, recovery after every replacement, a fixed
number of successes, or unattended service availability.
