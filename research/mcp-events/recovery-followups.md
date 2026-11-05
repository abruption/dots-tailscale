# Received recovery requests that have not been claimed

[한국어](recovery-followups.ko.md)

Issue [#79](https://github.com/abruption/dots-tailscale/issues/79) covers an acknowledged event that never leads to a Dots claim. HTTP 200 confirms callback receipt, not task evaluation, terminal access or startup. This opt-in broker feature can send a few requests to re-evaluate the **same incident**. It never grants execution or guarantees that Dots will respond.

## Policy and limits

The feature is disabled by default. The reviewed server accepts these optional configuration keys:

```json
{
  "recovery_unclaimed_reminder_interval_seconds": 600,
  "recovery_unclaimed_reminder_maximum": 3,
  "recovery_unclaimed_reminder_lifetime_seconds": 7200
}
```

An explicit interval must be an integer from 300 to 3600 seconds, the maximum from 1 to 3, and the lifetime from 300 to 86400 seconds and strictly greater than the interval while enabled. Booleans and strings are rejected. Set the interval to `null` to disable reminders; do not use `false` or zero. The example configuration keeps recovery and this feature disabled.

The interval is measured from an actual recorded 2xx callback response for the selected request. With several active subscriptions, all pending/retrying/sending deliveries must settle and the latest tracked successful response supplies the grace period. The first tracked seed receipt used by the scheduler fixes that incident's expiry. Later reminders, policy-unblocked events, restarts, renewal or re-enabling do not reset the count or extend that recorded deadline.

At most three **semantic reminder events** are created per incident. Transport retries remain separately bounded by the existing outbox rules. Every reminder has a distinct `incident:unclaimed:generation` request identity and the existing recovery payload schema. The original event, incident, received/sending/terminal delivery records and claim history are retained; a reminder never resets a received row or creates a new outage.

## When a reminder is allowed

The broker requires a fresh confirmed external failure, the exact still-pending incident, current claim eligibility and a valid active subscription. A preflight stop report suppresses reminders. A claimed or uncertain execution must not receive another startup request. Daily/cooldown limits, local budgets, platform refusals and explicit stops remain effective.

Scheduling and semantic deduplication run under the existing SQLite writer transaction. The sender checks the current incident, policy, receipt deadline and subscription again before admitting a queued reminder to the network. Stale observations or temporary policy blocks defer it. Claims, external recovery, preflight stops, expiry, disabling or a newer policy-event generation cancel queued pending/retry semantic reminders. Healthy ingestion cancels that semantic queue in the same writer transaction that closes the incident, without waiting for a background selector to rediscover it. Sending/received/terminal records are not rewritten to force a replay.

There is a deliberate limit to this check: a callback already admitted and in flight cannot be recalled if a claim or stop races with its network response. Delayed/duplicate events remain data and require a fresh accepted claim before any command. One claim per incident and unknown-result fences remain unchanged.

When a semantic event is cancelled because that queue is stopped, a late new subscription retains the existing original/current-policy delivery path. Temporary deferral does not fall back to that path. This preserves the base-event contract without clearing a preflight stop, creating a new semantic event or granting execution.

Existing policy-unblocked events take precedence. A follow-up waits for the new policy event's actual receipt, preserving its existing count and expiry, rather than creating both kinds of reminder together. This does not promise immediate delivery or execution at a particular time.

## Receipts, old rows and stopping

`recovery_delivery_receipts` records new successful callback responses atomically with their delivery results only while this feature is enabled. Default-off operation does not passively accumulate receipt records. A response received while disabled remains without tracked timing when the feature is later enabled. It does not infer a receipt time from an old `received` row, incident creation, an observation or a status query. Old received rows without this evidence are reported as `legacy_receipt_unavailable`; enabling the feature does not replay them. Operator review or a separately authorized recovery is needed.

`recovery_followups` retains one bounded record per tracked incident. An expired/exhausted state persisted by the scheduler is terminal for that incident, even after a backward clock, changed settings or restart. A read-only status result does not itself persist that terminal adoption. Clock reversal otherwise suspends dispatch without refunding the count. Lowering the maximum below an already queued generation fences that generation without refunding the count. Turning reminders off cancels queued semantic reminders and restores selection of the original/current policy event for the existing late-subscription contract; turning them back on does not reset cancelled deliveries or the ledger. A newly verified subscription may still receive the existing semantic generation after re-enabling if its current gates permit it; that creates a new subscription/outbox pair, not a new reminder generation or a reset of an old transport row. Unsubscribing stops delivery to that subscription; a renewed expired delivery row is not reset.

The extra stores are capped at 1,000 follow-up records and 10,000 receipt records. Once a cap is reached, further proof/scheduling fails closed and reports operator attention. Capacity can require manual review; no automated maintenance workflow or unlimited operating lifetime is provided. They are not automatically pruned or treated as a new budget. Do not delete records or restore an earlier DB to clear a stop. These bounds do not claim to bound the pre-existing event/authorization history.

## Read-only status and operator attention

`get_current_recovery` adds `unclaimed_followup` without creating an event. It reports:

- selected policy, number of reminder events created and the recorded expiry;
- selected delivery evidence for currently active subscriptions, its actual receipt time and response state;
- an advisory `next_reminder_at` for another permitted reminder; after the final possible reminder, `awaiting_response` instead exposes a distinct `response_deadline_at`, with no next reminder advertised;
- a fixed reason such as `waiting`, `due`, `claimed`, `preflight_reported`, `observation_stale`, `legacy_receipt_unavailable`, `expired` or `exhausted`;
- `operator_attention` when the bounded process is exhausted, expired or lacks reliable evidence.

The status omits callback addresses, signing secrets and subscription IDs. A due time is not a claim or future permission. Status reads do not fabricate proof, advance a clock watermark, consume reminder budget or send notifications.

For an enabled, still-pending chain without a preflight stop, a receipt-backed record reaching its recorded deadline reports `expired` with `operator_attention`, even if the last callback is still `sending`. The scheduler persists that terminal state and its one-time warning. A late genuine initial receipt starts its own recorded window; the incident's earlier creation does not make a young reminder overdue.

If no receipt-based record exists, a fresh eligible incident with an active subscription whose selected delivery remains unresolved reports `delivery_unconfirmed` after the configured lifetime measured from incident creation. This age only limits how long missing evidence stays quiet. It does not create a receipt timestamp, follow-up anchor, event, replay or execution authority. The state can be reconstructed after restart from the durable incident time and outbox.

A settled terminal transport failure reports `delivery_unconfirmed` immediately when the other gates apply. The incident-age grace covers unresolved delivery, not a known terminal failure.

The scheduler persists and emits one fixed, sanitized log warning when an incident expires or exhausts its reminders. The structured `operator_attention` flag is available to a separately configured operator monitor. **This change alone does not install a Telegram/email alert path**; end-to-end operator warning integration is tracked with #80. A Dots task's subscription does not itself prove an operator saw an exhaustion warning.

## Rollout, rollback and validation

Deploy `recovery_followups.py`, `recovery_core.py`, `event_core.py` and the corresponding integrated-server constructor wiring as one reviewed source graph. The private integrated adapter must explicitly pass the new settings; do not overwrite it with the public research server. Older deployed budget/cooldown helpers may reject the expanded constructor. This repository's updated helper accepts the exact follow-up argument wiring for later policy-only changes, but still does not copy `event_core.py` or `recovery_followups.py` and is not a feature installer. Prepare a separate pinned deployment with independent terminal access, code/configuration backup and preservation of the live authorization ledger. This PR does not perform that deployment or enable an operating policy.

Disable reminders and quiesce callback work before code rollback. Old core code does not select the new semantic reminder IDs in its normal pending-delivery path, but does not understand their diagnostic/stop records. Do not manually deliver those IDs with old code or restore older DB contents. Already in-flight callbacks cannot be recalled; original incident/claim rules remain essential.

The isolated suite uses fake clocks, SQLite stores, synthetic subscriptions and signed callback fixtures. It covers reception without a claim, policy transitions, restart/concurrent scheduling, delayed delivery after claim/stop, stale observations, subscription renewal/expiry, durable expiry/exhaustion, legacy receipts and capacity limits. An authenticated ingestion/background-delivery test exercises the actual scheduler path. These are not live platform-execution, VM replacement or unattended-recovery trials.
