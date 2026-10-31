# Proposed operating recovery budget: 24 claims per day

[한국어](recovery-budget.ko.md)

This is the proposed operating-policy change for [#80](https://github.com/abruption/dots-tailscale/issues/80). It raises an explicitly configured **12-claim** allowance to **24 claims per rolling 86,400 seconds**. Merging this document or installing dots-tailscale does not change a running broker.

The package default remains **3 claims per rolling 24 hours**. The operating proposal keeps the global interval at **3,600 seconds** and the confirmed-new-incident interval at **600 seconds**. An existing claim still counts whether its result is success, failure, unknown or unreported. The change does not reset the rolling window, refund claims, change local coordination or change the local watchdog budget.

## Prepare the change

Review a one-key change in the existing private broker configuration:

```json
{"recovery_max_claims_per_24h": 24}
```

Keep all other values, including `recovery_cooldown_seconds=3600`, `recovery_new_incident_cooldown_seconds=600`, authentication, mail integration and subscription settings. Do not replace the full private configuration with an example file. The general `config.example.json` remains disabled and does not adopt this operating allowance.

The reviewed `new_incident_policy_deployment.py` helper accepts an optional `--max-claims-per-24h`. Omitting it preserves the current budget or implicit default; an explicit value must be an integer from 1 to 24. Invalid existing values are rejected rather than repaired implicitly.

The helper retains its protected staging, source pins, exclusive deployment locks and fresh/healthy/quiescent checks. **It refuses to apply during an open outage or in-flight delivery.** Prepare an independent recovery terminal and a maintenance point meeting those checks; the helper cannot be used to bypass an active incident's refusal.

For this budget-only operating change, stage the already installed, independently verified core unchanged and use its same digest for both core arguments. Inspect and pin the existing integrated server; it must already pass the supported policy arguments so no connector code needs editing. The helper preserves the supported `local_coordination` argument if present. Never replace the integrated server with the public research server.

Before using this one-key recipe, read the current MCP `policy` and inspect the private configuration to confirm the existing policy is exactly 12/3600/600 and that the reviewed server patch would leave its bytes unchanged; stop if either precondition differs. The general helper also supports other explicitly requested changes, so those flags alone do not enforce this recipe.

After inspecting the exact hashes, the root-authenticated invocation has this shape. Uppercase values are placeholders, not usable hashes:

```sh
python3 /root/reviewed/new_incident_policy_deployment.py \
  --source-dir /root/reviewed/source \
  --expected-core-sha256 INSTALLED_CORE_SHA256 \
  --expected-server-sha256 INSTALLED_INTEGRATED_SERVER_SHA256 \
  --candidate-core-sha256 INSTALLED_CORE_SHA256 \
  --new-incident-cooldown-seconds 600 \
  --max-claims-per-24h 24
```

Only a separately approved operating deployment runs that command. It needs root authentication, briefly stops/restarts the broker socket and service, and retains private code/configuration backups. No claim, callback replay or startup command is issued by the helper. Normal monitoring may deliver a policy-unblocked event after restart if current conditions warrant it; that event still requires a fresh claim before execution.

## Verify and roll back

Check the returned policy and a fresh read-only MCP status: `max_claims_per_24h=24`, `cooldown_seconds=3600`, `new_incident_cooldown_seconds=600`. An effective interval of 3,600 seconds remains valid when there is no eligible confirmed-new-incident transition. Existing claim counts must be retained. The isolated tests check that the 25th claim within the same rolling window is blocked; do not create test claims on the operating broker. `retry_at` is advisory; time passing alone does not authorize execution.

Compare configuration values as parsed JSON; the helper rewrites key ordering and formatting. Verify that the integrated core/server content, private configuration outside the budget key, subscriptions and authorization history are preserved. Keep local recovery policy unchanged. Independent external SSH and ping remain necessary to confirm any later recovery.

To return to 12, confirm the starting policy is 24/3600/600 and use the same source-preserving process with `--max-claims-per-24h 12`. Rollback also requires fresh healthy observations and no open work. For an emergency stop during an outage, follow the existing [stop and rollback procedure](../../docs/experimental-recovery.md#stop-and-rollback) instead of bypassing those guards. If usage is already above 12, that policy may block new claims until enough old entries expire. Never restore an old DB or delete entries to make the lower limit fit. The helper keeps its existing conservative rollback behavior, including operator attention if a new claim appears during deployment; it never restores a ledger.

This finite increase does not resolve repeated disconnects, the received-but-unclaimed reminder gap in #79, or the remaining diagnostics/early-warning/temporary-escalation work in #80. Those items remain separate. No operating rollout or unattended recovery success is established by the isolated tests for this change.
