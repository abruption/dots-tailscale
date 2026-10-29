# 명시적으로 켜는 자동 패키지 업데이트

자동 업데이트는 기본적으로 꺼져 있습니다. 설치나 패키지 업데이트만으로 예약 작업·cron·systemd·watchdog이 등록되거나 이 실행기가 시작되지 않습니다. 기존 수동 updater의 트랜잭션으로 소스 네 파일을 바꾸는 기능이며, Tailscale 바이너리 교체나 baseline 마이그레이션은 별도 수동 작업입니다.

지원 대상은 설치·runtime 경로를 읽고 쓸 수 있고, 구성요소와 로컬 소켓이 보이며, 기존 수동 `update apply`가 요구하는 Linux/Python/pidfd 조건을 충족한 실행 환경입니다. 활성화하기 전에 Dots 클라우드 컴퓨터의 독립 복구 터미널을 확보하세요. 업데이트할 SSH 연결 자체는 독립 복구 경로가 아닙니다.

[#20의 조사](https://github.com/abruption/dots-tailscale/issues/20)에서는 직접 Bash와 클라우드 컴퓨터 터미널의 파일·프로세스·소켓 가시성이 다를 수 있음을 확인했습니다. VM 안에서 실행한 감시기는 해당 환경이 사라지면 함께 멈춥니다. 새 VM에서 스스로 다시 시작하는 기능이나 플랫폼 부팅 hook은 아닙니다. VM 교체 후 무인 실행이나 다른 계정에서의 재현을 이번 구현만으로 검증했다고 표현하지 않습니다.

## 준비와 활성화

실행 중인 패키지는 소스 무결성이 일치하는 정식 v0.8.0 이상이어야 합니다. 후보 역시 현재보다 새로운 정식 버전이며 v0.8.0 이상이어야 합니다. 개발 버전·하위 버전·서명 없는 과거 릴리스는 자동 설치하지 않습니다.

릴리스 출처 검증 안내에 따라 독립된 trusted-root 파일과 사전 설치한 `gh` 검증기를 준비하세요. 설치 경로나 후보 파일 안의 root·검증기를 신뢰하지 않으며, 검증기를 자동 다운로드하거나 검증 실패를 체크섬만으로 대체하지 않습니다. 자동 업데이트 설정이 새 GitHub 토큰을 요구하거나 설치하지는 않습니다.

```bash
/workspace/.dots-tailscale/dots-ts update automatic enable \
  --interval-hours 24 --timezone Asia/Seoul --window 03:00-05:00 \
  --provenance-root /workspace/independent-trust/github-trusted-root.json \
  --proxy http://browser-proxy:8889 --confirm-recovery-terminal --json
/workspace/.dots-tailscale/dots-ts update automatic status --json
```

활성화는 운영자의 독립 터미널 확인을 기록합니다. 그 터미널이 앞으로도 항상 사용 가능하다는 증명은 아닙니다. 실제 tick마다 현재 쓰기 권한·소스 무결성·프로세스·소켓/backend·root/검증기·이전 소스/바이너리 트랜잭션·복구 조정 상태를 다시 확인합니다. 수동 updater의 신원·준비 상태·baseline 동등성·원복 검증도 유지합니다.

설정·보류·시도 이력·결과는 설치 경로의 전용 `.automatic-updates`에 남고, 소스 업데이트로 지워지지 않습니다. 이 디렉터리에 개인키나 Tailscale 인증 state를 복사하지 않습니다.

## 주기·시간대·버전 고정

- 확인 간격은 1~720시간이고 기본은 24시간입니다. epoch 시간으로 관리해 시계나 시간대가 바뀌었다고 같은 시도를 다시 실행하지 않습니다.
- 기본 창은 UTC 02:00~04:00입니다. `--window HH:MM-HH:MM`은 시작 시각을 포함하고 끝 시각은 포함하지 않습니다. 자정을 넘는 창은 지원하지만 시작과 끝이 같은 값은 거부합니다.
- `--timezone UTC`는 별도 시간대 데이터베이스 없이 동작합니다. IANA 이름은 Python 3.9 이상의 `zoneinfo`나 운영자가 설치한 `backports.zoneinfo`를 사용합니다. 지원 모듈·시간대가 없으면 오류로 중단하고 자동 설치하지 않습니다. 실제 업데이트에는 기존 Linux/pidfd 조건도 필요합니다.
- DST로 사라진 시간대는 건너뛰고, 반복되는 시간대라도 저장된 간격·예산·트랜잭션·새 버전 조건을 우회하지 않습니다.
- `configure --pin v0.8.1`로 정확한 정식 태그에 고정하고 `configure --unpin`으로 해제합니다. 예시 태그는 실제 공개된 뒤 사용할 수 있습니다.
- `configure --interval-hours 48 --timezone UTC --window 02:00-04:00`으로 일정을 바꾸며, `--direct`는 저장한 다운로드 프록시를 해제합니다. 설정을 바꾸면 준비 중 작업의 generation을 무효화합니다.

## 실행·보류·중지

운영자가 직접 만든 예약 작업이나 승인한 독립 실행 경로에서 tick 하나를 호출할 수 있습니다. 도구가 예약을 대신 등록하지는 않습니다.

```bash
/workspace/.dots-tailscale/dots-ts update automatic tick --json
/workspace/.dots-tailscale/dots-ts update automatic watch \
  --seconds 3600 --poll-seconds 60 --json
/workspace/.dots-tailscale/dots-ts update automatic hold --json
/workspace/.dots-tailscale/dots-ts update automatic unhold --json
/workspace/.dots-tailscale/dots-ts update automatic disable --json
```

`watch`는 1~86400초 동안 30~3600초 간격으로 tick을 호출하는 포그라운드 실행입니다. 이미 시작한 제한 시간 내 tick을 마친 뒤 끝나므로 지정 시간보다 해당 tick만큼 길어질 수 있습니다. 별도 데몬·서비스를 만들지 않습니다. 터미널을 닫아 watcher가 끝나도 이미 커밋된 updater 작업은 진행될 수 있습니다.

`hold`는 보류이고 `unhold`는 보류 해제입니다. 꺼진 설정을 `unhold`만으로 활성화하지 않습니다. `disable`은 영구 비활성화로, 네트워크나 프로세스 점검을 통과하지 못한 장애 중에도 요청할 수 있습니다. 다시 시작하려면 독립 복구 터미널 확인을 포함한 명시적 `enable`이 필요합니다. 복구 구독이나 Tailscale 연결 자체를 끄는 명령은 아닙니다.

중지와 실행 커밋은 같은 짧은 제어 잠금으로 순서를 정합니다. 준비 중 worker는 enabled·보류·generation·후보 태그·시간 창·5분 내 준비 시각을 확인하고 `execution_committed`를 먼저 기록해야 합니다. 중지/보류가 먼저 저장되면 소스 변경이나 shim 재시작 전에 해당 worker를 거부합니다. 실행 커밋이 먼저 끝난 작업은 비활성화 후에도 완료하거나 원복할 수 있습니다. 이미 진행 중인 작업을 취소했다고 안내하지 않습니다.

준비 단계에서는 복구 잠금을 잡고, 분리 실행 사이에는 저장된 자동 업데이트 시도 표식으로 다른 복구 실행을 막습니다. worker 커밋 시 복구 잠금을 다시 확인합니다. 응답 유실이나 오래된 표식만으로 재실행을 허용하지 않습니다. 수동 updater의 공통 작업 잠금도 유지합니다.

예약을 없애려면 먼저 disable하고 watcher를 종료한 뒤, 본인이 등록한 예약 항목만 제거하세요. 이 기능이 만든 예약 항목은 없습니다. 결과가 확정되기 전에 이력이나 트랜잭션 파일을 삭제해 불명 상태를 지우지 마세요.

## 결과와 실패 처리

`automatic status --json`에서 저장된 설정과 마지막 결과, 진행 중 작업의 관측 상태를 확인합니다. status 읽기만으로 실행 환경을 재검증하지 않으며, 외부 SSH는 `not_verified`입니다. 저장된 결과를 현재 연결 상태로 취급하지 않습니다.

`started`는 성공이 아닙니다. `dots-ts update status --id ID --json`으로 같은 작업을 확인하세요. 결과는 `applied`, `failed_rolled_back`, `rejected`, 수동 복구 필요 상태로 구분합니다. 응답이 없으면 같은 attempt/transaction을 조회하며 새 ID로 다시 설치하지 않습니다. 실행 여부 불명·worker 부재·원복 실패가 남으면 후속 자동 작업을 차단합니다.

24시간당 자동 설치 준비는 최대 3회, 기록된 실패도 최대 3회입니다. 점검 실패 후 5·10·20분으로 기다리며 최대 1시간까지 늘릴 수 있고, 검증된 GitHub reset/retry 시각이 더 늦으면 그 시각을 존중합니다. 일일 실패 예산을 소진하면 과거 실패가 창을 벗어날 때까지 더 기다립니다. 실패한 태그도 그대로 반복 설치하지 않습니다.

알려진 종료 결과를 검토하고 새 strict-host-key SSH 연결과 Tailscale ping으로 외부 동작을 확인한 뒤, 독립 터미널을 다시 확인한 운영자는 다음 명령으로 실패 보류를 해제할 수 있습니다.

```bash
/workspace/.dots-tailscale/dots-ts update automatic reset-failures \
  --confirm-recovery-terminal --json
```

이 명령은 설치 시도 예산을 지우지 않으며, 미확정 작업이 있으면 거부합니다. 알려지지 않은 작업을 맹목적으로 다시 실행하는 수단이 아닙니다.

`automatic notify`는 마지막 결과를 캐시에서 읽고 대화형 stderr에 한 번 알립니다. 비대화형/리다이렉션 stderr에는 출력하지 않습니다. 예약 실행자는 JSON을 확인하거나 별도로 승인한 알림 경로를 마련해야 합니다. 이 명령이 이메일·텔레그램·외부 callback을 보내지는 않습니다.

자동 업데이트 완료는 기존 소스 트랜잭션의 로컬 검증 완료입니다. 외부 SSH 접속, 모든 취약점 해결, 다음 VM에서의 watcher 실행까지 보증하지 않습니다. 불명이나 원복 실패 시 코드 전용 백업을 보존하고 독립 터미널에서 수동 복구 절차를 따르세요.
