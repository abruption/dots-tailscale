# Continuous recovery trial

The operator authorized replacing the read-only experiment with an ongoing
recovery trial on 2026-10-22. This is experimental infrastructure outside the
released dots-tailscale package. It observes naturally occurring failures;
this transition does not deliberately stop Tailscale or replace a VM.

## Path

1. The existing Mac observer checks a new SSH connection and Tailscale ping
   every 60 seconds.
2. A separate launchd relay reads its latest observation every 15 seconds and
   sends minimized fields to the external Linux broker over verified HTTPS
   using a private observation-only machine credential. Strict SSH remains an
   explicitly selected fallback, not an automatic fallback after HTTPS failure.
3. Three distinct, chronological, fresh observations with explicit SSH timeout
   or network-unreachable errors and failed ping create one durable incident.
   Local execution, authentication, host-key and ambiguous ping failures do not
   create a recovery incident.
4. The authenticated broker emits `dots.recovery_requested` once per incident.
   The dot's event task has no end date; its subscription must keep refreshing.
5. Before executing, the dot reads the current incident and fresh external
   observations. Recovery that already occurred or stale monitoring blocks a
   new claim, including when the triggering event was delayed.
6. After preparing its own cloud terminal and checking the script exists, the
   dot obtains `claim_recovery(incident_id)` and immediately runs only
   `bash /workspace/.dots-tailscale/start.sh` once within the returned 120-second
   grant window. It records a sanitized outcome with the same claim ID.
7. Only a later successful external SSH **and** ping closes the incident as
   `externally_recovered`. A successful command report alone cannot do that.

The broker never executes shell commands on behalf of an MCP client. A claim
records permission and deduplicates requests; its expiry is an instruction to
the dot, not an execution gate inside the remote shell. The existing startup
script retains its own process checks and lock.

## Startup permissions clarified on 2026-10-23

The saved task instructions are mirrored in
[dots-recovery-instructions.ko.txt](dots-recovery-instructions.ko.txt). The
previous broad restriction on file/configuration changes caused the dot to
stop before claiming when it found the installed startup path's chmod calls.

The operator explicitly allows the existing startup command to normalize its
installation, shim/state/sshd and per-UID runtime directories to 0700, and its
existing authorized_keys, SSH host private key and shim CA private key to 0600,
even if the current modes differ. Normal startup of the existing components,
operation locks, runtime sockets/logs, daemon state updates and readiness
markers are permitted. This does not authorize editing identity/authorization
contents, reinstalling, logging in again, deleting state, changing ownership,
network-policy changes, or restarting the deliberately stopped watchdog.
Missing persistent identity/state or a platform approval block still stops the
attempt. No secret file contents should be returned to the conversation.

An explicit user resume request may re-evaluate an existing unclaimed incident
without waiting for another webhook. It must still satisfy fresh observations,
claim eligibility, the same fixed command, one execution per claim/incident,
and external recovery confirmation. An already delivered event is not resent
or erased to force another attempt. A pre-claim block is recorded through `report_recovery_preflight` with a fixed
reason and in the task conversation. This does not create a claim or refresh
observations; unavailable/older tool catalogs fall back to a conversation
report without fabricating a claim.

## Limits

- One claim per incident; no repeat after an unknown result.
- Defaults: at least one hour between claims and at most three claims per rolling 24 hours. Operators can configure `recovery_max_claims_per_24h` (strict integer 1–24) and `recovery_cooldown_seconds` (300–86400). The operator deployed an explicit 12-per-24-hour, 3600-second trial policy on 2026-10-24; changing it requires an operator configuration deployment, not a prompt change.
- Status includes `eligibility_reason`, `retry_at`, and `policy`. `retry_at` is the earliest time-only restriction expiry, not authorization: a fresh observation and new accepted claim are still required. Server policy denial is not a local preflight report reason.
- A recorded policy-blocked → freshly eligible transition creates a distinct reminder event for the same incident (maximum three such reminders per incident). Original events, delivery attempts, claims, and outcomes remain intact. Already-claimed or closed incidents cannot emit these reminders. Delayed processing remains possible.
- Monitoring data must be no older than 150 seconds; gaps reset the failure streak.
- Only explicit transport failures trigger this trial. A reachable node with a
  separate SSH failure is not automatically restarted by this external policy.
- Missing installation/state, approval blocks and uncertain execution require
  a user-visible report. The automation does not reinstall software, recreate
  identity, delete state, change network policy or stop other services.
- No automatic restoration of the experimental in-VM watchdog is included.
  The fixed startup command starts the installed Tailscale/SSH components.
- Event reception can be delayed. A prior read-only trial took about 19m34s;
  this is neither a fixed period nor a latency guarantee.

## Authentication and supervision

`events:observe` permits monitoring reads and event subscriptions.
`recovery:execute` additionally permits claims and outcome reports. Existing
read-only grants cannot acquire that scope through refresh. The dedicated
recovery plugin is explicitly granted those scopes and its bounded tools may
run unattended. Other plugins' permission settings are not part of this trial.

The production trial config grants rotating refresh tokens with a fixed
90-day maximum grant lifetime. Reauthorization is needed at expiry or earlier
revocation. Access tokens still expire after ten minutes. Subscription grants
remain finite and rely on ChatGPT renewal; continuous operation requires both
renewal and a healthy broker/observer, not only a running process.

The Linux broker is a user systemd service with restart-on-failure and lingering
enabled. The six-hour timeout process is retired. The Mac observer and relay
are LaunchAgents with KeepAlive. They run while that user's GUI login session
is available and start after login. The Mac must remain awake and online.

Set `TERM=xterm-256color` for the Mac observer. In this installation the
Tailscale application executable returned a GUI startup error with exit 0 when
TERM was absent. A real `pong` plus the exit status is required as evidence;
exit 0 alone is insufficient.

## Delivery supervision added on 2026-10-23

The relay no longer requires an interactive Tailscale SSH check for normal
observation delivery. `POST /monitor/observations` accepts a separate private
machine token; its SHA-256 digest is configured at the broker. That token cannot
call MCP tools, claim recovery, read OAuth secrets or operate a computer. The
receiver rejects stale/future observations, ambiguous acknowledgements and
oversized/invalid input. TLS verification is mandatory and redirects are not
followed. Whole outgoing HTTPS operations, including DNS, run under a 20-second
process deadline. The existing OAuth flow for the Dots plugin is unchanged.

A separate Mac health notifier checks the observer, relay delivery timestamps
and broker-reported active subscription. Persistent failures have a 180-second
grace and at most one notification attempt per hour, with state surviving
restarts. It uses the existing operator's Telegram channel. No synthetic test
message was sent; transport and cooldown tests use fixtures. This notifier is
independent of the relay process, but shares the Mac, power/network availability
and login session. It cannot alert while that host is unavailable. Its delivery
channel can also fail, so a saved alert attempt is not proof of user receipt.
The original hourly Tailscale notification monitor remains separate.

[Transport details](relay-transport.md) and
[stabilization evidence](stabilization-20261023.md) describe provisioning,
rotation, stopping and remaining tests. The 24-hour lifecycle collector is a
separate, read-only bounded observation; see [#20 research](../lifecycle-20/README.md).

## Verification and stopping

Local tests cover outage selection, freshness, persistence, deduplication,
claims, limits, scope separation and external confirmation. Normal live SSH and
ping must produce no incident or startup command. Real outage recovery and
recovery after a new boot remain separate observations to collect.

At activation, 73 local tests passed. The Mac observer and relay were running,
the Linux broker was enabled/active with lingering, and fresh minimized
observations showed SSH and ping healthy. The broker had one active recovery
subscription, zero active old probe subscriptions, zero incidents and zero
claims. The dot confirmed a recovery automation with no end date. Future
subscription renewal, OAuth refresh over the full grant lifetime, and actual
VM-transition recovery are ongoing observations, not completed validations.

To stop the trial, first disable its `dots.recovery_requested` automation in
the dot, confirm the broker has no active recovery subscription, then stop the
dedicated relay and broker services. The preexisting notification monitor and
the in-VM watchdog are separate services. Retain private incident records when
investigating an interrupted attempt, rather than clearing them to force retry.

OAuth tokens, callback URLs/secrets and live configuration stay outside the
checkout in private runtime directories. Public evidence contains only
sanitized status and timestamps.

## Rolling-limit improvement — 2026-10-24

A reported fourth outage could not be claimed under the original three-claims-per-day policy. Successful attempts also consume that budget. The operator approved an improvement; the default remains unchanged while this deployment explicitly sets the trial to 12 claims per rolling day and preserves the one-hour interval and one claim per incident.

`policy_deployment.py` must run from verified root-owned staging. It seeds an existing pending incident's policy-blocked marker using the **old** configuration before changing the limit, without invoking a callback or claim. It preserves every database, installs the reviewed server/core, and changes only the runtime policy fields. Rollback restores code/configuration without overwriting ledger updates. The edited Dots task instructions were saved through the browser; they now defer to server policy and do not treat a reminder or `retry_at` as permission to execute.
