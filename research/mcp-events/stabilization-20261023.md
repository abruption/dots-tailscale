# Recovery path stabilization — 2026-10-23

This research deployment addresses the observation-delivery interruption seen
before the overnight Dots outage. It is separate from the released package and
does not yet establish unattended recovery after an arbitrary VM replacement.

## Implemented and applied

- The Mac relay now sends minimized observations over verified HTTPS with a
  private observation-only token. The broker stores only its digest. No SSH
  policy was relaxed; the previous transport remains an explicit fallback.
- The receiver deduplicates timestamps through the existing recovery ledger,
  rejects invalid/stale/future input, limits request size and concurrency, and
  acknowledges the exact observation time. Callback delivery does not prevent
  fresh observations from being accepted. Active subscription status accompanies
  the acknowledgement without exposing subscription identifiers or secrets.
- Complete outgoing HTTPS operations, including OS DNS, have a 20-second child
  process deadline and bounded cleanup. Redirects, TLS verification failures,
  non-JSON or mismatched acknowledgements fail closed.
- Relay health retains safe error classes, first failure, last attempt,
  freshness and subscription status. A separate Mac notifier checks source and
  delivery age, allowing 180 seconds of degradation before notifying the
  operator's existing Telegram channel, with a one-hour attempt limit.
- `report_recovery_preflight` records a fixed reason for a pending, unclaimed
  incident without claiming or consuming retry budget. It requires the existing
  recovery write scope. The plugin tool catalog was refreshed in the real UI;
  the new tool appeared alongside the existing claim/report tools. Saved task
  instructions were updated and read back successfully.

## Validation

- Local Mac and deployed Linux: **113 offline tests passed**. These include
  observation/MCP credential separation, owner/scope checks, duplicate and stale
  inputs, bounded callback work, durable preflight reports, hanging DNS and
  process cleanup, and alert grace/cooldown without contacting Telegram.
- Actual deployed HTTPS: a fresh existing observation was accepted. Missing or
  incorrect machine credentials returned 401; an old observation returned 400;
  the machine credential could not call MCP (401). TLS verification stayed on.
- Live relay health showed fresh delivery, no transport failures and an active
  recovery subscription. The pre-existing incident, successful claim and closed
  outcome were preserved; no new recovery incident or command was synthesized.
- Notification credentials were validated locally, but **no test Telegram
  message was sent**. Real fault alert delivery remains an operational
  observation, not a completed end-to-end test.
- Existing Dots services and the deliberately stopped in-VM watchdog were not
  restarted or fault-injected during this stabilization work.

## Lifecycle observation

An independent, read-only Mac collector started at **2026-10-23 10:46:20 KST**
with a fixed deadline of **2026-10-24 10:46:20 KST**. It samples every five
minutes and persists that deadline before its first SSH command. Restarting the
collector does not extend the experiment. Initial sampling found the existing
installation present and the backend Running. This is an ongoing observation;
24-hour completion and future natural outage recovery are not yet established.

See [English lifecycle notes](../lifecycle-20/README.md) and
[Korean lifecycle notes](../lifecycle-20/README.ko.md) for the baseline, resource
limits and remaining five-theme evidence matrix.

## Remaining conditions and rollback

The notifier shares the Mac and its login/power/network dependencies; it cannot
alert during a Mac outage. Token rotation, OAuth refresh, subscription renewal,
future approval blocks, real preflight reports and natural outage execution
still need continuing observation. No artificial fault was introduced into the
production Dots installation to make these tests pass.

The deployment retains private source/config backups. Roll back code and relay
configuration, then restart only the dedicated relay/broker jobs. Preserve the
current databases and incident ledger; do not restore an old database merely to
retry an attempt. Stop the notifier and bounded collector separately if ending
their observations. To disable machine ingestion, unset the broker digest and
remove the private relay token through the operator's private administration
channel. No credentials, live configurations or raw private logs are included
in this tree.

## 한국어 요약

감시 전달을 SSH 재인증에 의존하지 않는 전용 HTTPS 경로로 전환했습니다.
전달 자격증명은 관측 수신에만 쓰며 MCP 실행 권한과 분리했습니다. 오래된
관측·인증 실패·중복·시간 초과를 검증했고, 전달·구독 이상을 별도 프로세스로
감시하도록 연결했습니다. 실행 전에 중단된 사유도 claim 없이 기록할 수
있습니다. 플러그인 도구와 저장된 작업 지침을 갱신했습니다.

기존 서비스에 장애를 주입하지 않았으며, 24시간 읽기 전용 자원 관찰은 진행
중입니다. 실제 알림 전달과 사람 개입 없는 다음 자연 장애 복구는 후속
관찰로 남깁니다.
