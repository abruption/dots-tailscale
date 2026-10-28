# Tailscale 바이너리 수동 업데이트

[English](runtime-updates.md)

dots-tailscale v0.6.0에 추가된 기능입니다(#50). v0.5.0 이하에는 이 명령이 없습니다. 설치된 `tailscale`과 `tailscaled`를 한 쌍으로 갱신합니다. `dots-ts update`는 dots-tailscale 소스 패키지를, `dots-ts runtime`은 Tailscale 실행 파일을 관리합니다. 자동 업데이트를 켜지는 않습니다.

## 먼저 같은 버전의 도구를 설치하세요

이 기능이 들어 있는 검토된 패키지의 소스 4개와 `source-manifest.json`을 함께 설치해야 합니다. 실행하는 도구와 설치된 소스 묶음이 다르면 거부합니다. 새 저장소의 CLI만 실행해 오래된 설치를 갱신하면, 기존 시작 스크립트가 중간에 끊긴 업데이트를 알아보지 못하기 때문입니다.

공개된 v0.6.0은 기존 소스 전용 업데이터로 새 도구를 설치할 수 있도록 기본값 1.102.4를 유지합니다. 미출시 0.7.0-dev 후보는 신규 설치에 1.104.4를 사용합니다. 일반 패키지 `update apply`는 실행 파일 버전 선언이 바뀌면 여전히 거부합니다. 기존 설치는 설치된 도구로 Tailscale을 먼저 갱신하고 외부 접속을 확인한 뒤, 후보의 명시적 [보안 기본 버전 전환](security-baseline.ko.md)을 따라야 합니다. 이 소스 전환과 `runtime apply`는 별개이며 실행 파일을 조용히 바꾸지 않습니다.

## 변경 없이 확인하기

```bash
/workspace/.dots-tailscale/dots-ts runtime check --json
```

기본은 플랫폼 프록시(`UPSTREAM_PROXY`, 없으면 `http://browser-proxy:8889`)를 명시적으로 사용합니다. `--proxy direct`는 직접 HTTPS 연결을 선택할 뿐 플랫폼의 외부 연결 제한을 우회하지 않습니다. `check --version 1.104.4`는 그 숫자와 비교만 하며, 공개된 최신 버전인지 확인하지 않습니다. 설치 버전을 읽지 못한 경우 먼저 원인을 확인하세요.

온라인 적용은 요청을 시작할 때 공식 stable 목록에 나온 정확한 버전을 지정해야 합니다. 불안정 minor 번호·prerelease 표기·다운그레이드·같은 버전 재설치는 거부합니다. 도중에 새 릴리스가 나와도 이미 선택한 다운로드 주소를 바꾸지 않습니다.

## 직접 적용하기

Tailscale 연결이 끊겨도 사용할 수 있는 Dots 클라우드 터미널을 먼저 열고 제어되는지 확인하세요. 외부 자동복구를 사용 중이면 잠시 중지하고, 외부 접속 확인까지 터미널을 유지하세요. 확인 플래그는 사용자의 확인을 받는 장치이며 실제 GUI 제어나 작업 중지를 도구가 검증하는 것은 아닙니다.

확인한 버전이 1.104.4인 경우의 예시입니다.

```bash
/workspace/.dots-tailscale/dots-ts runtime apply \
  --version 1.104.4 --proxy platform \
  --confirm-recovery-terminal --json
```

선택한 버전이 Tailworm·SSH 보고 항목을 모두 해결했다는 뜻은 아닙니다. [Tailworm 보고서](https://tailworm.com/)도 1.104.2를 대상으로 했습니다. 적용 전 [공식 보안 공지](https://tailscale.com/security-bulletins)를 확인하세요.

적용에는 Linux amd64, Python pidfd 지원, 쓰기 가능한 사용자 전용 설치·실행 경로, 무결성이 확인된 패키지와 기존 구성으로 실행 중인 daemon·shim·OpenSSH가 필요합니다. 다운로드와 압축·해시·실행 버전 확인을 먼저 끝내고 두 바이너리를 백업합니다. 시작 스크립트·프록시 적용·패키지 업데이트와 같은 작업 잠금을 사용합니다.

CLI는 작업 ID를 출력하고 stdout을 비운 뒤 분리된 worker의 진행을 허용합니다. 로컬 출력이 교체보다 먼저 일어난다는 뜻이지, 원격 터미널이 ID를 받았다는 보장은 아닙니다. 출력을 잃었다면 독립 터미널에서 `.runtime-transactions/`의 작업 디렉터리 이름을 확인하세요. apply를 무작정 반복하지 마세요.

```bash
/workspace/.dots-tailscale/dots-ts runtime status --id TRANSACTION_ID --json
```

`TRANSACTION_ID`에는 반환된 32자리 ID를 넣습니다. status는 로컬 상태만 읽습니다.

| 상태 | 의미 |
|---|---|
| `prepared` / `running` | 준비·실행 허가 대기 또는 worker 진행 중입니다. |
| `applied` | 로컬 준비 상태·기존 identity/설정·설치 파일 해시 확인을 통과했습니다. 외부 SSH·ping은 별도로 확인해야 합니다. |
| `failed_rolled_back` | 새 daemon 실행을 시도하기 전에 실패해 기존 바이너리 쌍을 복원하고 확인했습니다. 정리 경고가 있는지도 확인하세요. |
| `rejected` | 이 작업이 daemon을 중지하거나 설치된 바이너리 쌍을 교체하기 전에 거부됐습니다. 호출 단계뿐 아니라 ack 이후 worker에서도 거부될 수 있습니다. 오류와 작업 ID를 확인하세요. |
| `status_unavailable` / `ack_uncertain` | 이 응답만으로 결과를 확정할 수 없습니다. 복구 터미널을 유지하고 반환된 유효한 ID로 상태를 확인하세요. 취소된 것으로 여기거나 apply를 다시 제출하지 마세요. |
| `manual_recovery_required` | 실행·프로세스 소유·진행 기록·state 호환성을 확정할 수 없습니다. 독립 터미널에서 확인하고, 재실행이나 차단 표식 삭제로 넘기지 마세요. |

`applied` 이후 새 외부 SSH 연결과 Tailscale ping, 사용하는 애플리케이션 경로를 확인하세요. 그 뒤 외부 자동복구를 재개합니다. 로컬 `Running`만으로 전체 연결이 복구됐다고 판단하지 않습니다.

## 외부 다운로드가 막힐 때

신뢰할 수 있는 장비에서 공식 파일과 checksum을 받아 전송합니다. 예시 디렉터리에는 다음 두 파일이 있어야 합니다.

```text
tailscale_1.104.4_amd64.tgz
tailscale_1.104.4_amd64.tgz.sha256
```

설치·작업 디렉터리 밖에 두세요. bundle 디렉터리는 실행 사용자 소유·0700이어야 하며, 파일은 같은 사용자 소유의 일반 파일이어야 합니다. 심볼릭 링크와 그룹·다른 사용자에게 쓰기를 허용한 파일은 거부합니다.

```bash
/workspace/.dots-tailscale/dots-ts runtime apply \
  --version 1.104.4 --bundle /workspace/staged-tailscale \
  --confirm-recovery-terminal --json
```

이 경로는 운영자가 신뢰한 bundle을 사용하는 선택입니다. 최신 공개 버전이라는 뜻은 아닙니다. checksum은 SHA-256만 들어 있거나 정확한 archive 이름이 붙은 한 줄이어야 합니다. HTTPS와 같은 출처의 checksum 대조는 전송·파일 일관성을 확인하며, 별도의 배포자 서명 검증은 아닙니다. 출처가 불분명한 파일은 checksum이 맞아도 사용하지 마세요.

## 실패했을 때의 처리 범위

도구는 노드 state나 SSH 개인키를 복사·교체·출력·초기화하지 않습니다. 소켓·userspace 인자·daemon 프록시/CA 환경·공개 노드 identity·SSH 호스트 공개키를 확인하며 shim과 OpenSSH는 유지합니다. 환경 변수 값은 메모리에서만 유지하고 journal에 저장하지 않습니다.

Daemon을 중지하기 전에 `.runtime-update-block` 표식을 만듭니다. 시작·프록시·패키지 변경 경로는 표식이 있으면 중단하므로, worker가 끊기거나 새로 부팅돼도 섞인 바이너리를 그대로 시작하지 않습니다. 정상 적용 또는 새 daemon 실행 전의 검증된 원복을 마친 worker만 자기 표식을 지웁니다. check/status는 계속 사용할 수 있습니다.

새 daemon 실행을 시도한 뒤에는 state 형식이 바뀌었을 수 있습니다. 하위 호환성을 추정해 옛 바이너리를 자동으로 다시 실행하지 않습니다. 정상 쌍과 완료 journal을 저장하기 전 실패하면 백업과 표식을 남기고 수동 복구를 요구합니다. 완료 저장 뒤에는 journal의 `applied` 또는 검증된 원복의 `failed_rolled_back`을 유지합니다. 정리 오류는 `fence_cleanup_failed`나 `worker_cleanup_failed` 경고로 남을 수 있습니다. 자기 표식이나 손상된 표식이 남으면 status는 `manual_recovery_required`로 표시하고, 그렇지 않으면 완료 상태와 경고를 함께 표시할 수 있습니다. 표식 복원은 최선 노력으로 수행하며 검증된 정상 daemon은 유지합니다. 경고가 있으면 CLI는 0이 아닌 종료코드를 반환하므로 자동 버전 변경 대신 정리 상태를 확인하세요. 과거 state 복원이나 재로그인으로 실패를 덮지 마세요. 독립 터미널에서 journal·바이너리 쌍의 해시·daemon/socket identity와 상위 버전의 state 호환성을 확인해야 합니다.

오프라인 테스트는 실제 Dots의 바이너리 갱신이나 장애 복구를 입증하지 않습니다. 선택적 자동화는 #31에서 이 경로의 실환경 검증 후 다룹니다. VM 교체로 updater 프로세스가 사라지면, `/workspace`에 파일이 남아 있다는 이유만으로 작업이 이어진다고 보장할 수 없습니다.

## 수동 복구 절차

새 daemon 실행 이후 readiness나 identity 확인이 실패하면 candidate를 중지하려고 시도하며, Tailscale이 꺼진 상태로 남을 수 있습니다. 중지를 확인하지 못하면 프로세스·소켓 상태도 미확인입니다. 반대로 정상 바이너리 쌍과 완료 journal까지 저장한 뒤 표식 정리만 실패한 경우에는 정상 daemon을 중지하지 않습니다. 가능한 경우 `cleanup_review` / `fence_cleanup_failed`를 기록하고 자기 표식을 유지합니다.

다음은 독립 클라우드 터미널에서 운영자가 수행하는 절차입니다. 외부 자동복구·업데이트 작업을 먼저 중지하세요. journal 손상, 해시 불일치, 확인할 수 없는 프로세스나 소켓은 중단 조건입니다. 보이지 않는 것을 없다고 판단하지 마세요.

### 1. 작업 잠금과 증빙 확인

복구 셸에서 같은 잠금을 잡고 셸을 유지합니다.

```bash
D=/workspace/.dots-tailscale
test -f "$D/.operation.lock" && test -O "$D/.operation.lock" &&
  test ! -L "$D/.operation.lock" &&
  test "$(stat -c '%a:%h' -- "$D/.operation.lock")" = '600:1' &&
  exec 9<>"$D/.operation.lock" && flock -n 9
```

잠금 획득에 성공한 경우에만 계속합니다. 작업의 `journal.json`에서 `phase`, `state_compatibility`, `worker`, `baseline_version`, `baseline_binary_hashes`, `binary_hashes`를 확인하세요. 필드가 없으면 미확인입니다. 부팅 ID와 PID·시작 시각·UID·실행 파일을 대조해 updater worker가 확실히 종료됐는지 확인합니다. 접근 제한 때문에 `/proc`이 안 보이는 상황을 종료로 취급하지 마세요. 포괄적인 `pkill`이나 소유자를 모르는 소켓 삭제는 하지 않습니다.

설치된 두 바이너리와 사용할 stage/backup 쌍은 심볼릭 링크가 아닌 일반 파일, 현재 사용자 소유, 링크 수 1, 권한 0700인지 확인합니다. SHA-256과 두 파일의 버전 출력도 대조합니다. candidate는 `binary_hashes`, 기존 버전은 `baseline_binary_hashes`와 같아야 합니다. 자료가 없으면 신뢰할 수 있는 공식 artifact로 출처를 먼저 확인하세요. state나 개인키를 열 필요는 없습니다.

### 2. 복구 방향 선택

| 확인된 상태 | 허용되는 운영자 조치 |
|---|---|
| 교체 전에 worker가 종료됐고 실행 의도가 없으며 기존 바이너리·daemon이 그대로임 | 파일과 프로세스를 유지하고 중단된 작업 기록만 정리합니다. |
| 유효한 journal이 `not_executed`를 입증하며 daemon·소켓이 없음을 확인함 | 검증한 backup **두 파일 모두**를 복원해 기존 버전을 재개할 수 있습니다. journal이나 표식이 없다는 사실만으로 이 경우라고 판단하면 안 됩니다. |
| candidate가 실행됐을 수 있음: `unknown`, readiness 실패, 실행 의도 이후 중단 | 검증한 stage의 **같은 candidate 버전 두 파일**을 유지하거나 복원합니다. 구버전을 현재 state에 실행하지 마세요. candidate가 호환되지 않으면 표식을 유지하고 상위 프로젝트의 복구 방안을 확인합니다. |
| 완료 기록에 `fence_cleanup_failed` 또는 `worker_cleanup_failed`가 있음 | 현재 쌍과 daemon이 완료된 버전과 일치하면 정상 daemon을 유지합니다. 3단계에 따라 확인한 기록을 보관하고, 자기 표식이 남아 있을 때만 해제합니다. |

소유자를 모르는 daemon·소켓이 있으면 여기서 멈춥니다. 바이너리 복원이 필요하면 먼저 daemon이 실행 중이 아님을 확인하세요. 선택한 두 파일을 새 임시 이름으로 복사·검증하고, 잠금과 표식을 유지한 채 원자적 rename으로 둘 다 교체합니다. 실행 중인 파일의 inode에 덮어쓰지 마세요. 남은 `.new` 파일도 종료된 해당 작업의 파일임을 확인하기 전에는 삭제하지 않습니다.

### 3. 기록 보존과 차단 해제

앞 단계에서 바이너리 쌍·버전·프로세스·소켓·복구 방향을 확정한 경우에만 다음을 진행합니다.

1. `sync -f "$D"`로 설치 파일과 파일시스템 기록을 반영합니다. 실패하면 표식을 남기고 중단합니다.
2. 작업 ID, 선택한 버전·해시, 판단 근거를 사용자 전용 복구 메모로 남깁니다. 원래 journal은 수정하지 않습니다.
3. 현재 사용자 소유·0700인 `$D/.runtime-resolved`를 준비하고, 해당 작업을 비어 있는 새 경로 `$D/.runtime-resolved/TRANSACTION_ID`로 같은 파일시스템 안에서 옮깁니다. 다른 기록을 덮거나 삭제하지 마세요. 활성 작업 디렉터리 밖으로 보관하면 이후 apply의 이전 작업 검사에서 제외됩니다. 파일시스템을 다시 반영한 뒤 표식을 해제합니다.
4. `.runtime-update-block`이 있으면 링크를 따라가지 않고 읽어 schema·작업 ID·기록된 boot ID가 보관한 journal과 정확히 일치하는지 확인합니다. **그 작업의 표식만** 제거합니다. 다르거나 읽을 수 없는 표식은 중단 조건입니다. 디렉터리/파일시스템 반영이 실패하면 검증된 쌍을 유지하고 저장 장치 문제를 확인하세요.
5. `flock -u 9`, `exec 9>&-`로 셸 잠금을 해제합니다. daemon이 이미 실행 중이지 않은 경우 설치된 `bash "$D/start.sh"`를 한 번 실행합니다. 표식을 모르는 구 checkout의 시작 스크립트나 구버전 `dots-ts update`도 사용하지 마세요. 보호 장치가 들어 있는 소스를 교체할 수 있습니다.
6. 로컬 준비 상태와 새 외부 SSH·ping을 확인한 뒤 외부 작업을 재개합니다. 결과와 호환성을 확인하기 전까지 원래 journal과 두 코드 쌍을 보관합니다.

보관한 작업은 `runtime status --id` 대신 보관 경로의 journal·복구 메모로 확인합니다. 이는 운영자의 명시적 해소 결정이며 옛 시도가 자동으로 성공 처리되는 것은 아닙니다. 같은 candidate가 계속 시작되지 않으면 반복 실행이나 옛 state 복원 대신 비공개 daemon 로그를 확인하고 호환성 지원을 요청하세요.

## 운영 참고

- 독립 복구 터미널 확인과 detached worker가 있으므로 SSH에서 시작하는 것은 허용합니다. 확인 플래그만으로 다른 터미널의 존재를 검증하지는 않습니다.
- daemon 출력은 검증된 사용자 전용 `tailscaled.log`에 남습니다. status는 원시 로그를 반환하지 않으니 진단 시 로그를 비공개로 다루세요.
- 각 작업은 stage와 backup을 보관하므로 압축 해제된 바이너리 쌍 크기의 약 두 배가 필요합니다. 자동 정리는 없습니다. 활성 디렉터리를 일괄 삭제하지 말고, 해소된 작업만 위 절차로 보관한 뒤 보존 정책에 따라 정리하세요.
- worker 생성과 identity 기록 사이의 짧은 구간에는 status가 보수적으로 수동 확인을 요구할 수 있습니다. 제한된 대기 시간이 지난 뒤 다시 조회하고, 일시적인 응답만으로 파일을 변경하지 마세요.
- [단일 실환경 정상 경로 시험](validation/runtime-update-trial-20261026.md)에서 1.102.4 → 1.104.2 갱신과 외부 SSH·ping 복귀를 확인했습니다. 기동 시간 분포·실패 경로·state 다운그레이드 호환성은 아직 검증하지 않았습니다. readiness timeout이 그 버전의 영구적인 사용 불가를 뜻하지는 않습니다. 독립 터미널과 단계별 절차로 판단해야 합니다.

스테이징 중 KeyboardInterrupt는 가능한 경우 rejected로 기록합니다. SIGHUP/SIGKILL 같은 갑작스러운 종료나 저장소 오류는 prepared 작업을 남길 수 있습니다. worker가 없음을 확인한 뒤 수동 절차의 실행 전 항목으로 판단하세요. ack가 이미 보이는 시점에는 호출을 종료해도 detached worker가 취소되지 않습니다. 최종 디렉터리 동기화나 저장소 문제가 있으면 정상 쌍을 유지한 채 운영자가 확인해야 합니다.
