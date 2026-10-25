# 외부 연결 복구 실험 기능

[English](experimental-recovery.md)

외부에서 연결 장애를 감지한 뒤 Dots에게 기존 dots-tailscale을 다시 시작하도록 요청하는 **고급 사용자용 실험 기능**입니다. Dots 밖의 서버와 감시 장비, 사용자가 승인한 Dots 작업이 필요합니다. 일반 패키지를 설치해도 이 기능이 켜지지는 않으며, 원클릭 설치나 모든 장애의 자동복구를 보장하지 않습니다.

## 어디까지 확인했나요?

관리자의 시험 환경에서는 이벤트 전달, Dots의 기존 시작 명령 실행, 이후 외부 SSH·Tailscale ping 복귀를 확인했습니다. 초기 시도에는 SSH 재인증과 작업 지침 수정이 필요했으므로 이 결과만으로 완전 무인 운영을 입증할 수는 없습니다. 부팅 식별자가 바뀐 것도 관측했지만, 재부팅·VM 교체·플랫폼 복원 중 무엇인지는 구분되지 않습니다. 임의의 VM 교체 후 복구와 다른 계정의 설치 재현은 아직 검증하지 않았습니다.

콜백 HTTP 200은 수신 확인입니다. 실제 명령 실행이나 복구 완료를 뜻하지 않습니다. 앞선 읽기 전용 시험에서는 약 19분 뒤 응답이 왔지만, 이를 고정 폴링 주기나 복구 기한으로 볼 수는 없습니다.

근거와 남은 과제는 [#20](https://github.com/abruption/dots-tailscale/issues/20), [#43](https://github.com/abruption/dots-tailscale/issues/43), [watchdog 조정 #44](https://github.com/abruption/dots-tailscale/issues/44), [실험 기능 공개 범위 #45](https://github.com/abruption/dots-tailscale/issues/45)에 정리합니다.

## 준비와 설정 순서

1. [사용 가이드](usage.ko.md)에 따라 Dots에 설치하고 Tailscale 인증을 마칩니다. SSH 호스트 키를 별도 경로로 대조하고, 새 SSH 접속과 Tailscale ping이 되는지 확인합니다. 복구할 때도 기존 설치 파일·노드 상태·SSH 키가 남아 있어야 합니다.
2. 외부 Linux 서버, `dots-events.example.com` 같은 공개 HTTPS 주소, Caddy, 개인 Authelia SSO를 준비합니다. 정확한 허용 계정·OAuth 클라이언트·리디렉션 URI·리소스를 설정합니다. Caddy는 `/authorize`와 `/oauth/consent` 모두에서 인증하고 클라이언트가 보낸 신원 헤더를 제거해야 합니다. 두 upstream 경로 모두에서 `X-Dots-Client-IP`를 Caddy의 `{client_ip}` 값으로 덮어씁니다. 보강된 서버는 전용 Unix 계정과 접근이 제한된 Unix 소켓을 사용합니다.
3. [서버 설정 예시](../research/mcp-events/config.example.json), [보안 배포 설명](../research/mcp-events/security-hardening.md), `research/mcp-events/` 코드를 검토합니다. 예시는 복구가 꺼진 상태입니다. 관리자 권한 bootstrap은 **이미 운영 중인 시험 설치를 이전하는 도구**이며, 새 서버용 설치기가 아닙니다. 이 자료만으로 다른 계정에서 처음부터 설치하는 과정은 독립 재현하지 않았습니다. 서비스 경로·의존성 잠금 파일·프록시·SSO 설정을 환경에 맞게 확인한 뒤 적용하세요.
4. tailnet에 접근할 수 있고 SSH·ping을 각각 확인하는 상시 감시 장비를 준비합니다. 별도 [HTTPS 전달기와 기기용 인증정보](../research/mcp-events/relay-transport.md)를 설정합니다. 인증정보와 실행 상태는 저장소 밖의 전용 비공개 디렉터리에 보관합니다. 토큰·콜백 URL/secret·노드 state·개인키는 공개하지 마세요. Mac 구현은 기기가 깨어 있고 온라인이며, LaunchAgent를 실행할 GUI 로그인 세션이 있어야 합니다.
5. Dots에 사용자 지정 MCP 앱을 연결하고 `events:observe`, `recovery:execute` 권한을 승인합니다. 해당 앱의 제한된 복구 도구만 의도한 무인 작업에서 사용하도록 설정합니다. [저장된 작업 지침](../research/mcp-events/dots-recovery-instructions.ko.txt)과 [상시 시험 계약](../research/mcp-events/continuous-trial.md)을 검토하세요. 서버 프로세스뿐 아니라 이벤트 구독과 OAuth 갱신도 계속 동작해야 합니다.
6. 정상 관측과 읽기 전용 이벤트 확인부터 시작합니다. SSH·ping이 정상일 때 복구 시도가 생기지 않아야 합니다. 장애를 일부러 발생시키려면 먼저 Dots 클라우드 터미널에 별도로 접근할 수 있어야 합니다. 이벤트 구독이나 시험 콜백 성공만으로 실제 복구를 확인했다고 판단하지 마세요.

### OAuth 요청 제한과 프록시 신뢰 경계

보강된 Unix socket 모드에서 애플리케이션은 보호된 socket으로 요청이 들어오고 TCP peer 주소가 없으며, 파싱 가능한 IP 값이 정확히 하나 있을 때만 `X-Dots-Client-IP`를 신뢰합니다. admission 키로 사용하기 전에 주소를 canonical 형식으로 정규화합니다. 이 값은 IP별 요청 제한을 위한 키이며 OAuth 인증 신원이 아닙니다. `/authorize`와 `/oauth/consent`는 Authelia 인증이 필요하며, 별도의 동시 처리 요청 한도 8개를 공유합니다. 공개 `/token`, `/revoke` 요청은 별도 한도를 사용합니다. 전체 동시 요청은 최대 8개, IP별로는 1개입니다. IP별 bucket은 최대 16개까지 쌓이고 초당 1개씩 충전됩니다. 전체 bucket은 최대 64개까지 쌓이고 초당 16개씩 충전되며 IP가 바뀌어도 공유됩니다. IP 항목은 최대 256개까지 보관하고 비활성 항목만 LRU 방식으로 제거합니다. OAuth metadata는 이 두 요청 pool에 포함되지 않습니다. IP 값이 없거나, 중복되거나, 잘못된 경우 403으로 거부합니다. pool이나 bucket 한도를 넘으면 `Retry-After: 5`와 429를 반환합니다.

Caddy가 CDN 뒤에 있다면 실제 upstream만 trusted proxy로 설정하고 그 경로에 맞춰 client IP를 확인해야 합니다. 그렇지 않으면 Caddy가 CDN edge IP를 전달해 서로 다른 사용자가 하나의 제한을 공유할 수 있습니다. 같은 NAT 주소를 쓰는 사용자도 제한을 공유합니다. 여러 출발지의 요청이나 upstream/CDN 포화로 장애가 생길 수 있으므로, 이러한 애플리케이션 제한은 DoS 방어를 보장하지 않습니다. 레거시 loopback 모드에서는 `X-Dots-Client-IP`를 무시하고 실제 loopback peer 주소를 사용하며 loopback이 아닌 peer를 거부합니다. loopback proxy를 통과하는 클라이언트는 같은 bucket을 공유할 수 있고, 이 모드는 Unix 계정 경계 없이 로컬 프로세스를 신뢰합니다. 정확한 제한과 경계는 [보안 설명](../research/mcp-events/security-hardening.md)을 참고하세요.

외부 감시 장비는 관측을 전달하고, 서버는 실행 허가인 claim을 발급하며, Dots는 본인의 클라우드 컴퓨터에서 아래 명령을 실행합니다.

```bash
bash /workspace/.dots-tailscale/start.sh
```

MCP 서버가 원격 셸을 실행하지는 않습니다. claim 만료 시각은 Dots가 지켜야 하는 조건이며, 셸 자체에서 실행을 차단하는 장치는 아닙니다.

## 정책과 운영 상태 확인

기본값은 **최근 24시간 최대 3회, 실행 허가 사이 최소 3,600초**입니다. 관리자 시험은 간격을 유지하면서 최대 12회로 명시적으로 설정했습니다. 성공한 시도도 횟수에 포함됩니다. 설정 키는 `recovery_max_claims_per_24h`, `recovery_cooldown_seconds`이며, 정책 변경은 서버 설정 검토를 거쳐야 합니다. Dots에게 제한을 무시하라고 요청하는 방식으로 바꾸지 마세요.

MCP의 `get_current_recovery`로 `observation_fresh`, `claimable`, `eligibility_reason`, `retry_at`, `policy`를 확인합니다. `retry_at`은 참고 시각입니다. 그 시간이 지나도 최신 상태를 다시 조회하고 새 claim을 받아야 실행할 수 있습니다. 같은 incident ID로 알림이 다시 올 수 있지만, 새 실행 권한을 뜻하지 않습니다. 원래 장애·claim·전송 이력은 보존됩니다. 이미 claim이 있는 장애는 결과가 불명확해도 다시 claim하거나 실행하지 않습니다.

보강된 Linux 설치에서는 전용 서비스를 확인합니다.

```bash
systemctl is-active dots-events-recovery.socket dots-events-recovery.service
systemctl status dots-events-recovery.service --no-pager
```

비공개 로그는 현지에서 검토하고 원문을 이슈에 붙이지 마세요. 전달기의 `state.health.json`에는 전달 시각·실패 분류·연속 실패 횟수·구독 정보가 기록됩니다. 전달된 관측 시각이 계속 갱신되고 150초 이내인지 확인합니다. 별도 알림기도 같은 감시 장비를 사용하므로 장비가 꺼져 있으면 알림을 보낼 수 없습니다.

Homebrew 등으로 Python을 업데이트하면 실행 중인 전달기가 사용하던 이전 설치 경로가 삭제될 수 있습니다. 10월 25일 시험에서는 이 상황 이후 관측 전달이 멈췄고 전달기만 재시작하자 정상화됐습니다. Python 업데이트 후에는 설정된 실행 파일을 확인하고 아래 명령으로 전달기를 재시작한 뒤 새로운 관측의 수신을 확인하세요. launchd에 실행 중으로 표시되는 것만으로 정상이라고 판단하지 마세요. 당시 자식 프로세스의 정확한 예외는 확보하지 못했습니다.

Mac 전달기를 업데이트할 때는 함께 사용하는 보조 모듈도 설치하고 비공개 설정·상태를 보존한 뒤 실행 중인 작업을 재시작합니다. Python 파일만 복사하면 기존 프로세스는 이전 코드를 계속 실행합니다. 다음은 설치된 Label을 사용하는 예시입니다.

```bash
launchctl kickstart -k "gui/$(id -u)/com.example.dots-recovery-relay"
```

`com.example.dots-recovery-relay`는 예시입니다. 설치한 plist의 실제 Label로 바꾸세요. 재시작 후 새 프로세스와 더 최근 시각의 관측 수신을 확인합니다. 서버 업데이트·원복 시에도 OAuth·장애·claim 기록을 유지해야 합니다. 재시도를 유도하려고 기록을 초기화하지 마세요.

## 복구가 멈췄을 때

| 관측된 상태 | 확인할 내용 |
|---|---|
| 관측 전달이 오래됨 | 관측 파일 시각, Mac 잠자기·로그인 상태, HTTPS 경로, 기기용 인증정보, 전달기 상태를 확인합니다. 전달 실패만으로 Dots 장애가 확정되지는 않습니다. |
| 이벤트는 왔지만 claim이 없음 | 구독·작업 상태, Dots 터미널 접근, 사전 점검 보고, `eligibility_reason`과 정책을 확인합니다. |
| 정책 때문에 claim이 거부됨 | 정책 조건과 최신 관측을 다시 확인할 수 있을 때까지 기다립니다. 서버의 정책 제한을 파일 소실·권한 오류 같은 로컬 사전 점검 사유로 바꿔 보고하지 않습니다. |
| claim은 있지만 결과가 불명확함 | 해당 claim을 보존하고 Dots 보고를 조사합니다. 명령을 무작정 다시 실행하지 않습니다. |
| 명령 성공으로 보고됨 | 외부에서 새 SSH 접속과 ping을 확인합니다. `externally_recovered`가 복구 완료의 기준입니다. |
| 로컬 데몬은 Running인데 외부 접속 실패 | 로컬 준비 상태와 외부 연결을 구분하고 관측을 보존한 뒤 플랫폼·네트워크 상태를 조사합니다. |
| 설치·state가 없거나 로그인이 필요함 | 자동 시도를 멈추고 운영자 검토 후 수동 설치·인증 절차를 따릅니다. |

허용된 시작에는 기존 경로의 디렉터리·파일 권한 정규화와 소켓·PID·로그·state의 정상 갱신이 포함됩니다. 재설치, 키 교체, state 삭제, 네트워크 정책 변경, 의도적으로 중지한 실험 watchdog 재시작은 포함되지 않습니다.

## 중지와 원복

먼저 Dots 복구 작업을 비활성화합니다. 구독이 비활성 상태이거나 만료됐는지, 진행 중인 claim은 없는지 확인한 뒤 실행이 멈췄다고 판단하세요. 설치한 plist를 사용해 전용 전달기·관측기·알림기를 각각 중지합니다. 다음은 예시입니다.

```bash
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.example.dots-recovery-relay.plist"
sudo systemctl disable --now dots-events-recovery.socket
sudo systemctl stop dots-events-recovery.service
```

launchd 명령은 Mac에서, systemctl 명령은 외부 서버에서 실행합니다. 실제 plist 경로로 바꾸고 별도로 설치한 관측기·알림기도 각각 관리하세요. 이 조치로 Dots의 Tailscale이 중지되지는 않습니다. 비공개 장애 기록과 결과가 불명확한 시도는 보존합니다. IPv6 guard는 별도 보안 서비스이므로 복구 시험을 중지하면서 방화벽 보호까지 제거하지 마세요. 인프라 변경은 최신 기록 보존과 부분 실패를 다루는 [보안 원복 절차](../research/mcp-events/security-hardening.md)를 따릅니다.
