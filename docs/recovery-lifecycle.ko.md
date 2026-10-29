# 복구 실행 조정 (개발 중)

[English](recovery-lifecycle.md)

#44를 위한 개발 기능입니다. 패키지에 포함된 watchdog과 이 경로를 명시적으로
사용하는 외부 복구 작업이 로컬 실행 원장과 예산 하나를 공유합니다. v0.7.1이나
기존 MCP 작업이 이미 이 방식을 쓴다는 뜻은 아닙니다. 일반 설치만으로 복구,
watchdog, 예약 작업이나 외부 서버가 활성화되지 않습니다.

## 먼저 확인할 점

기존 설치와 런타임 소켓을 실제로 볼 수 있고 쓸 수 있는 같은 Linux 실행 문맥에서
사용해야 합니다. `/proc`의 부팅·프로세스 식별 정보, 원본 manifest와 일치하는 코드,
설치 시 생성된 `start.sh`, 기존 노드 상태·인증 표시·CA·SSH 인증 파일을 확인합니다.
파일 누락, 안전하지 않은 권한, 심볼릭·하드 링크, 불명확한 프로세스 상태,
재로그인·승인 필요 상태에서는 자동 실행을 멈춥니다. 설치·로그인·노드 재등록·키
재생성·접근 정책 변경은 하지 않습니다. 별도로 접근 가능한 클라우드 터미널은
계속 필요합니다.

내부 watchdog은 같은 부팅에서 확인 가능한 구성요소 종료를 다룹니다. 나머지는
실행 중이고 SSH 구성요소만 없어진 경우를 따로 구분합니다. 로컬 `Running`과
loopback 22번 포트의 SSH 배너는 인증된 SSH 접속이나 외부 복구 증거가 아닙니다.
백엔드가 응답하지 않거나, SSH 프로세스는 있는데 정상 배너를 확인하지 못하면
무작정 시작 명령을 반복하지 않습니다.

기존 개인 시험용 watchdog이 있다면 검토된 기존 중지 절차로 먼저 끄세요. 두 자동
실행 경로가 모두 새 진입점을 써야 합산 예산이 적용됩니다. 여전히 `start.sh`를
직접 실행하는 작업이나 개인 watchdog은 이 원장을 거치지 않습니다. 사용자의
수동 `start.sh` 실행도 별도 작업이며 가로채지 않습니다.

## 설정과 중지

아래 명령은 이 구현이 포함된 패키지용입니다. 이전 정식 버전에서 제공하는 명령은
아닙니다.

```bash
/workspace/.dots-tailscale/dots-ts recovery status --json
/workspace/.dots-tailscale/dots-ts recovery configure --enable --json
```

설정을 켜도 프로세스는 시작하지 않습니다. 기본값은 두 실행 주체를 합쳐 최근
24시간에 **최대 3회**, 실행 간격 **3,600초**, 시작 명령 제한 시간 60초입니다.
성공한 시도도 셉니다. 최대 횟수는 1~12회, 간격은 60~86,400초, 제한 시간은
10~120초, watchdog 점검 간격은 15~300초 범위에서 명시적으로 정할 수 있습니다.

```bash
/workspace/.dots-tailscale/dots-ts recovery configure \
  --max-attempts 3 --cooldown-seconds 3600 --timeout-seconds 60 \
  --interval-seconds 60 --json
```

외부 broker의 claim 예산·간격은 별도 조건으로 그대로 남습니다. 확인된 신규 장애에
짧은 간격을 허용하도록 broker를 설정했더라도 로컬 제한을 우회하지 못합니다.
더 엄격한 쪽에서 거부할 수 있습니다. 과거 개인 운영의 12회 설정을 로컬 정책에
자동 복사하지 않으며, 로컬 간격을 바꾸면 내부·외부 두 주체에 모두 적용됩니다.
이는 broker의 복구 증거에 따른 신규 장애 예외와 다릅니다.

```bash
# 현재 부팅에서 패키지 watchdog을 명시적으로 시작·조회
/workspace/.dots-tailscale/dots-ts recovery watchdog start --json
/workspace/.dots-tailscale/dots-ts recovery watchdog status --json

# watchdog만 중지: 외부 연결 복구는 유지 가능
/workspace/.dots-tailscale/dots-ts recovery watchdog stop --json

# 두 자동 복구 주체 모두 중지: Tailscale 연결은 그대로 유지
/workspace/.dots-tailscale/dots-ts recovery stop --json
```

중지 의사는 파일에 남습니다. 이미 시작된 명령은 끝날 수 있지만 다음 실행은
거부합니다. watchdog은 다음 점검 때 중지 의사를 확인해 종료하며, 진행 중인 시작
명령은 설정된 제한 시간까지 걸릴 수 있습니다. 복구를 다시 켰다고 사용자가
의도적으로 끈 watchdog까지 조용히 켜지지 않습니다.

watchdog은 사용자 프로세스이며 systemd·cron·플랫폼 부팅 hook이 아닙니다.
VM이나 프로세스가 사라지면 스스로 재시작할 수 없습니다. 같은 부팅에서는 저장한
프로세스 신원을 확인해 중복 생성을 막고, 다른 부팅에서는 파일·무결성·실행 문맥을
다시 확인합니다. 실행 중 패키지 코드가 바뀌면 `code_changed`로 종료합니다.
이때도 명시적인 재시작을 위한 wanted 값은 남습니다. 기존 개인 시험용 watchdog을
대신 설치하거나 실행하지 않습니다.

외부 복구 뒤 패키지 watchdog을 다시 시작하려면 watchdog을 계속 쓰겠다는 상태와
아래 설정이 **둘 다** 있어야 합니다.

```bash
/workspace/.dots-tailscale/dots-ts recovery configure \
  --restart-watchdog-after-external --json
```

이 설정만으로 즉시 시작하거나 `watchdog stop`을 취소하지 않습니다.
`--no-restart-watchdog-after-external`로 다시 끌 수 있습니다.

## 외부 claim 계약

인증된 broker가 방금 허용한 작업만 외부 주체로 실행해야 합니다. 요청 ID에는
실제 claim ID를 사용합니다. 여러 claim에 재사용하는 incident ID를 쓰면 안 됩니다.
아래 대문자 값은 실제 응답에서 가져와야 하는 자리표시자입니다.

```bash
/workspace/.dots-tailscale/dots-ts recovery run \
  --actor external --request-id CLAIM_ID \
  --claim-expires-at CLAIM_EXPIRY_UNIX_SECONDS \
  --observation-time OBSERVATION_UNIX_SECONDS \
  --ssh-failed --ping-failed --json
```

만료 시각이 앞으로 120초 이내인지, 관측이 미래 시각이 아니고 150초 이내인지,
SSH와 ping 실패 표시가 모두 있는지 검사하고 로컬 사전 점검 뒤 다시 확인합니다.
이는 **호출자가 전달한 형식·신선도 검사**일 뿐 OAuth나 서명 검증이 아닙니다.
옵션만으로 실제 claim 발급을 입증하지 못하고 같은 Unix 사용자에 대한 새 보안
경계도 만들지 않습니다. broker 인증·소유자 확인·claim 1회 제한·결과 보고는
외부 작업에서 유지해야 합니다. 이전 `retry_at`이 지났다는 이유만으로 실행하지
마세요.

실행 잠금을 얻은 뒤 정책·파일·로컬 상태·공유 예산을 다시 확인합니다. 이미 로컬에서
정상이면 `skipped_healthy`를 기록하고 시작 명령을 부르지 않습니다. 그렇지 않으면
실행 의도를 먼저 저장하고 설치된 `start.sh`만 설치·로그인 인자 없이 실행합니다.
패키지 작업 잠금은 확인한 뒤 풀고, 시작 스크립트가 직접 얻도록 합니다. 같은 잠금을
자식 프로세스 실행 중까지 잡고 있으면 교착이 생기기 때문입니다. 진행 중이거나
결과가 불명확한 자동 업데이트도 복구 실행을 막습니다.

watchdog은 매 점검에 새 요청 ID를 쓰며 정상 관측만으로 원장을 채우지 않습니다.
외부에서 이미 정상이어서 건너뛴 ID는 재실행 방지를 위해 남깁니다. 기록된 ID로 다시
요청하면 원래 claim이 만료됐더라도 저장된 결과만 돌려주며 다시 실행하지 않습니다.

## 결과 불명과 원장 보존

`local_ready`는 종료 코드 0과 새 로컬 정상 점검, `failed`는 확인된 비정상 종료 코드,
`unknown`은 시간 초과·실행/결과 저장 불명을 뜻합니다. 중단된 `running` 기록도
모든 새 ID의 실행을 막습니다. watchdog 재시작 실패는 별도 결과로 남고 이미 저장된
시작 명령 결과를 지우지 않습니다. `external_confirmation`은 항상 `pending`입니다.
broker에 결과를 보고하고 외부에서 새 SSH·ping을 따로 확인해야 합니다.

결과가 불명확하면 독립 터미널에서 해당 시작 명령과 프로세스가 끝났는지 확인하세요.
새 ID로 재시도하면 안 됩니다. 해당 시도를 검토한 운영자만 다음과 같이 조정합니다.

```bash
/workspace/.dots-tailscale/dots-ts recovery reconcile \
  --request-id CLAIM_ID --outcome failed --confirm-reviewed --json
```

결과는 `failed`, `not_executed`, `local_ready` 중에서 지정합니다. 관련 시작/실행
프로세스가 끝났거나 부팅이 달라진 것을 확인하며, `local_ready`에는 새 로컬 정상
점검도 필요합니다. 외부 복구를 확정하거나 기록 삭제·예산 환급·명령 재실행을 하지
않습니다.

설정·중지 의사·프로세스 신원·원장은 설치 경로의 비공개 `.recovery`에 보존합니다.
코드 업데이트와 원복 때 되돌리면 안 됩니다. 최대 256개 기록·512KiB로 제한하며,
가득 찼거나 손상·접근 불가·안전하지 않은 파일·시계 역행이면 새 실행을 거부합니다.
불명확한 이력을 지우거나 새 예산으로 간주하지 않습니다. 가득 찬 원장의 정리는
운영자 검토가 필요하며 자동 보관/초기화 명령은 제공하지 않습니다. 공개 이슈에
원장이나 인증·상태 파일을 올리지 마세요.

격리 시험은 동시 트리거·지연 이벤트·재시작 후 예산·실행 중 중지·오래된 claim·
원장 저장 실패·부분 시작·결과 불명 조정·가상 부팅 변경을 다룹니다. 실제 VM 교체,
다른 계정의 설정 재현, 자연 장애의 무인 복구나 MCP 실행 지연 보장을 입증하지는
않습니다.
