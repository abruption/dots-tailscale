# External recovery health follow-up (#75)

This is a source/test/documentation follow-up to the v0.8.0 development snapshot,
not evidence of an operating rollout. It addresses
[#75](https://github.com/abruption/dots-tailscale/issues/75) in
[PR #74](https://github.com/abruption/dots-tailscale/pull/74).

The relay now derives an explicit `external_health` value without rewriting
watchdog diagnostic `state`. Initially `verified_running` and `prior_boot` support
positive evidence, subject to strict SSH/probe/ping flags, integer zero exit codes
and absent/`ok` error markers. Other accepted diagnostic states remain
`unconfirmed`. The broker independently checks the optional field; legacy inputs
retain their original confirmation rule. The persisted confirmation, claim,
first-report, interval and daily-budget guards are unchanged.

## Isolated validation

Candidate source:
[`a1bdbfbabf330aeefb7cd3e7e028439a1c2611b3`](https://github.com/abruption/dots-tailscale/commit/a1bdbfbabf330aeefb7cd3e7e028439a1c2611b3).
Subsequent changes clarify documentation; the reviewed core and relay bytes remain
the same.

- [Research checks 40969366647](https://github.com/abruption/dots-tailscale/actions/runs/40969366647): Linux Python 3.12 and 3.13 each passed **243 research tests and 11 lifecycle tests**. Local Python 3.13 passed 243 research tests with one explicitly Linux-only inherited-socket fixture skipped.
- [Core checks 40969366566](https://github.com/abruption/dots-tailscale/actions/runs/40969366566): Python 3.11 and 3.13 each passed **541 tests**, with the required static, manifest and shell checks successful.
- Twelve new regressions exercise the actual relay sanitizer, authenticated HTTP middleware and temporary SQLite recovery core. They cover sustained `prior_boot` confirmation and the 600-second boundary, restart, legacy input, malformed/contradictory fields, authentication rejection, duplicate timestamps, explicit unconfirmed samples, gaps, unknown first reports, daily limits and claims without evidence slots.
- No live SSH, peer ping, startup command, callback, credential, subscription or operating ledger participates in these tests. HTTP test-client execution is not operating relay-transport or integrated-server E2E.

A separate isolated compatibility experiment used the public `6f27ef6` core whose
SHA-256 matches the read-only operating source check:
`2d3b20833e2772cb1916218ba38bee174e546d83b139781bf1e64ebad590367f`.
It used temporary ledgers and the unchanged authenticated ingestion middleware:

| Receiver source | Legacy payload | Explicit-health payload |
|---|---|---|
| Prior operating core source | HTTP 200 | HTTP 400 |
| Candidate core source | HTTP 200 | HTTP 200 |

This verifies the schema boundary in isolation, not a new request to the live
broker. Deploy the broker before the relay; reverse the order for rollback.
Inverted order can halt observation delivery and outage detection. Preserve
already earned evidence and all ledgers. See the
[EN](../../research/mcp-events/recovery-cooldown.md) /
[KO](../../research/mcp-events/recovery-cooldown.ko.md) operation guides.

## Independent review

Claude Code **2.1.312**, requested and metadata-confirmed
`claude-opus-5-5`, and Codex CLI **0.165.0**, using its isolated default model
(actual selected ID not exposed), independently reviewed the same frozen input:

`695538f0df438025f7db9c8456bd5049a94e464d7cb1a373313c0cac2ea22e5b`

Both completed with exit zero and no required product correction. Input hashes
were checked before and after execution. Claude tools were disabled; Codex ran
read-only and ephemeral with user configuration ignored. These were source-based
reviews, not independent execution of the CI. Recommendations clarified existing
unconfirmed slots, observation-loss risks during reversed rollout and evidence
retention during code rollback. No new runtime change followed the reviewed
candidate.

## Operating boundary

The bug investigation read minimized policy/claim/confirmation times and observer
classifications. A successful first report and an existing confirmation slot with
zero streak were distinguished from an absent legacy slot. These observations
and the source invariant identify the watchdog/connection mismatch; they do not
prove every observer sample was delivered or isolate all event-processing delay.

Before operating deployment, verify the actual integrated adapter/transport path,
retain its mail/OAuth customizations, and check fresh accepted observations and
the queried incident's effective policy. No existing pinned deployment helper is
implicitly refreshed by this PR. No merge, release or operating deployment was
performed as part of this fix.
