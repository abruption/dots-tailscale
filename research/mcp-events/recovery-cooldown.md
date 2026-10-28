# New-outage cooldown after confirmed recovery

[한국어](recovery-cooldown.ko.md)

This opt-in policy addresses #61, a narrow part of #44. A new outage need not
wait a full hour merely because a different outage was recently recovered.
It does not permit repeating an uncertain command on the same incident.

## Configuration and eligibility

Existing defaults remain three claims per rolling 24h and 3600s between claims.
Existing configured values are preserved. The operational trial uses its
existing 12/24h budget and 3600s global interval, adding only:

```json
{"recovery_new_incident_cooldown_seconds": 600}
```

The optional value is `null`/absent to disable, otherwise an integer from 300s
through the configured global interval. Booleans, strings and out-of-range
values are rejected. 600s is measured **from the previous claim**, not from the
new outage or its recovery confirmation.

A pending incident uses that shorter interval only if all conditions hold:

- It is distinct from the uniquely latest claimed incident and was created
  after that incident's qualified external recovery.
- The prior claimed incident is closed and its first accepted explicit report is success.
- At least three distinct consecutive accepted `verified_running` observations
  after the claim show both SSH and ping healthy, with no local-error markers.
  They must span at least 60s, with gaps no longer than 150s.
- The evidence is bound to the exact prior incident in the persistent
  `recovery_confirmations` table. Historical closed rows and report text alone
  are not evidence. Ambiguous ledger rows fall back to the global interval.

Accepted errors or partial health reset an unconfirmed streak. Retransmissions with the same timestamp and normalized connection result,
and rejected stale/out-of-order/malformed inputs, never advance it; rejected
requests do not mutate proof. A long gap resets the streak. Once confirmed,
the real recovered boundary is retained across later failures and restarts.
A newer claim cannot reuse another incident's evidence.

Evidence slots are created atomically with claims issued by the new code. Legacy
claims, including claims issued while older code was running, cannot gain the
shorter interval merely by receiving new healthy samples. The first explicit
report is recorded separately in `recovery_reports`; an explicit unknown report
cannot become short-path success through a later result correction. The existing
result-reporting interface may still update an unknown displayed outcome, but
that does not change the first-report evidence.

If the prior outcome is unknown, failed or blocked, or evidence is insufficient,
the full global interval applies. The daily limit always applies, and its later
expiry wins when calculating `retry_at`. Same-incident claims, expired grants,
stale observations, authorization checks and unknown execution results retain
their existing restrictions.

## What status means

`policy` retains the global `cooldown_seconds` and adds:

| Field | Meaning |
|---|---|
| `new_incident_cooldown_seconds` | Optional configured interval; `null` means disabled. |
| `effective_cooldown_seconds` | Interval selected for the queried incident. |
| `cooldown_basis` | `global` or `confirmed_recovery`. |

`incident.first_report` exposes the first accepted outcome/time; a later correction
to the displayed incident outcome is not sufficient short-path evidence.

With no qualified pending incident, a configured 600s value can correctly appear
alongside an effective 3600s/global policy. Do not infer that configuration was
ignored. Eligibility still requires fresh confirmed outage observations and a
new accepted claim. `retry_at` and policy-reminder events are advisory; neither
is execution permission or a promise of immediate ChatGPT processing.

The existing durable policy-unblocked event path is reused. A blocked incident
that later becomes eligible can receive a bounded reminder, but already-claimed
or closed incidents cannot receive permission to execute again.

## Deployment and rollback

The standard dots-tailscale installer does not deploy this research broker.
Use a reviewed operator deployment after isolated tests. Preserve the global
interval, daily budget, claim/event history, subscriptions and credentials.
KR's existing mail connector changes must be retained: add the new argument to
`load_recovery` without replacing the whole local server module.

The helper `new_incident_policy_deployment.py` performs pinned, operator-selected
code/configuration changes only during a fresh healthy and quiescent window.
The helper requires its pinned `/var/lib/dots-events` data directory and checks
socket, broker and Caddy separately. Read-only preflight does not load the new core against the live DB; its
constructor creates the additive schema only inside the controlled deployment.
Code/configuration are backed up privately. On failure restore code/config only;
**never rewind the ledger DB** to erase claims or callback progress. The harmless
additive tables can remain when rolling back code. If a new claim was issued
after restart, the helper stops for operator attention rather than automatically
putting an older reporting implementation under that in-flight claim. Arbitrary
mixed-version handling cannot retroactively prove a first report.

No synthetic production claim, outage, callback or recovery command is needed
for installation verification. Confirm the configured policy, active service,
fresh observations and natural healthy evidence for newly issued claims. With
only legacy claims, confirmation evidence is deliberately not backfilled. Actual later recovery remains
a separate end-to-end observation. To disable the short path, remove/set the
new configuration key to `null` through a controlled operator change; leave all
budgets and histories intact.
