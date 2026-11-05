# Recovery budget diagnostics and operator notices

[한국어](recovery-budget-observability.ko.md)

This describes the read-only diagnostics carried on the existing authenticated
`POST /monitor/observations` acknowledgement. It does not change the claim cap,
cooldowns, claim eligibility, event delivery, or execution permissions. The
current operating cap remains 24; a fresh installation still defaults to 3.

## Counts and meaning

The summary uses the same strict rolling-window predicate as recovery policy:
`claimed > now - 86,400`. Every issued claim counts, regardless of its reported
outcome. An unknown, missing, failed, blocked or success report never refunds a
claim.

- `claims_issued` and `cap` show budget use at the summary's `as_of` time.
- `budget_release_at` is present only at exhaustion. It is the earliest time a
  claim can age out of this budget window, using the same oldest-required-row
  offset as the cap calculation. It is a budget-only recheck reference, not
  `retry_at` and not permission to claim; cooldown, fresh-observation and
  incident-state gates still apply.
- `first_reports` counts the first explicit report. `unknown` is an explicit
  unknown report; `missing` means there is no `recovery_reports` row. A missing
  row can be legacy history and does not prove that nobody reported an outcome.
- `current_outcomes` counts the mutable outcome currently stored on each claim.
  It can differ from `first_reports` after a permitted unknown-to-success
  correction.
- `externally_closed` counts incidents closed after an accepted healthy SSH and
  ping observation. `sustained_confirmed` counts claims with the separate durable
  confirmation evidence. Neither field says that a startup command ran, and a
  reported success is not an independent execution trace.
- `unclaimed_followup` projects the existing `operator_attention` status from
  the recovery follow-up policy. It contains availability, the operator-attention
  boolean, `awaiting_response`, an advisory `response_deadline_at`, and a fixed
  reason code. The deadline is not claim permission; an awaiting response alone
  does not trigger an operator warning. It does not create a new reminder or
  infer that Dots read a callback.

No incident IDs, claim IDs, event IDs, callback URLs, credentials or report text
are included. The new aggregate SQL returns one fixed-size result rather than copying claim/report rows into Python lists. The existing policy status reader still uses its recent-claim list.

## Warnings

When `operator_recovery_warnings` is explicitly enabled in the private notifier
configuration, the default warning point is `ceil(80% × cap)`. At a cap of 24,
the near-limit notice begins at 20 claims and the exhausted notice begins at 24.
Exhaustion takes precedence. With the default cap of 3, the near-limit threshold
and exhaustion coincide. `recovery_budget_warning_percent` can be set to an
integer from 50 to 99; if omitted it is 80. Both options are disabled unless
`operator_recovery_warnings` is true.

Budget near-limit, budget exhaustion and `operator_attention` each have separate
persistent deduplication state. Attempts are saved before notification through
the existing approved notifier, so an ambiguous send is not retried immediately.
The existing one-hour repeat limit applies per category. Exhaustion notices
include `budget_release_at` and tell the operator to recheck current eligibility
after that reference. A stale, missing,
malformed or future-dated summary is **unknown**, never zero or healthy, and
cannot clear an existing warning. Old health files without diagnostics remain
readable and produce no budget alert until a fresh supported acknowledgement
arrives.

The broker includes the optional summary only in its existing authenticated
machine-ingest acknowledgement. The relay validates its fixed schema and stores
it in its existing private health file with the original `as_of`. An older broker
may omit the field; an invalid optional summary does not turn an accepted
observation into a failed delivery or cause the relay to retry it. The current
SSH transport does not return this acknowledgement summary. Keep `operator_recovery_warnings` disabled on that transport and use HTTPS for this feature; enabling it with SSH produces an unsupported-diagnostics monitor fault and bounded repeat notices.

On top of the matching #79 source graph, the broker needs the reviewed `recovery_core.py`, `recovery_followups.py`, `monitor_ingest.py` and `recovery_budget_diagnostics.py` together. The core/helper pair carries the coherent read-clock interface and claimed-time index. The Mac relay and notifier need their reviewed
`monitor_relay.py`, `health_notifier.py` and the same diagnostics module. The
existing root deployment helpers do not stage this new module. Deploy the
broker-side files first, then the Mac relay/notifier files; the older relay
ignores the optional ACK field. For rollback, restore the Mac relay/notifier
files first, then the broker files. A newer relay safely treats an older
broker's missing summary as unknown.

This adds no HTTP route, MCP tool or OAuth scope. The broker reads only aggregate
claim/report/confirmation counts for the acknowledgement; it does not expose
ledger rows, mutate claim/report history, export or restore the database, reset claim history or alter the cap.
Warnings use the existing approved sender and require explicit opt-in. The
existing root deployment helpers are not aware of this additional module; any
operator deployment must include the reviewed module with the relay and notifier
code in its pinned staging set. Tests use fixtures and a stub notification
sender; no live claim, callback or notification is part of them.

This slice adds no temporary claim allowance, refunds no claim, and does not
diagnose or fix the underlying outages.

The recovery schema also adds an index on the claim timestamp so rolling-window aggregates and budget-release lookups can avoid scanning the full history. The index does not change or remove any claim.

When operator warnings are enabled, unavailable diagnostic data (including an unavailable follow-up component) also uses the existing monitor-fault grace and repeat limits. It reports an availability fault rather than inventing a zero usage count or clearing a prior warning. Old-ACK delivery remains successful; this notice does not retry a recovery event.

A transport failure contains no newer acknowledgement, so a previously validated diagnostic snapshot remains usable until its original freshness limit. A new accepted ACK with missing or invalid diagnostics marks them unknown; it does not restore the old snapshot to current. Diagnostic timestamps allow at most 150 seconds of age and the existing 5-second clock-skew margin. These tolerances do not change claim expiry or execution checks. ISO microsecond rounding may make a genuinely later reference equal to `as_of`; it remains an advisory instant.

Corrupt or future notifier warning state is preserved for operator review and raises a bounded monitor-fault notice. It is not silently emptied to obtain another notification attempt. Actual send failures or uncertain delivery take precedence over cooldown/waiting output; per-path outcomes remain stored separately.

Before enabling the optional features, verify that `monitor_ingest`, `monitor_relay` and `health_notifier` import successfully from their pinned, complete staging environments, then check a fresh validated ACK. Do not start a partially copied entry point.

If the read-only snapshot itself cannot open, the accepted observation is still acknowledged but subscription/diagnostic availability is omitted as unknown. This path does not fall back to a read-write connection or create/repair a database. A subscription boolean already read successfully is retained if only the optional rollup fails. Isolated diagnostics checks cover both DELETE and WAL journal modes; the actual operating mode still belongs to deployment validation.

The relay health file retains the last-known `subscription_active` boolean when a newer ACK has no usable boolean. Do not treat that cached flag alone as a fresh subscription check. Diagnostic availability and the original `as_of` remain separate, and opted-in monitor faults expose unavailable diagnostics.
