# Tailscale 바이너리 수동 업데이트 (개발 중)

[English](runtime-updates.md)

이 기능은 #50에서 준비 중이며 v0.5.0 릴리스에는 없습니다. 설치된 `tailscale`과 `tailscaled`를 한 쌍으로 갱신합니다. `dots-ts update`는 dots-tailscale 소스 패키지를, `dots-ts runtime`은 Tailscale 실행 파일을 관리합니다. 자동 업데이트를 켜지는 않습니다.

## 먼저 같은 버전의 도구를 설치하세요

이 기능이 들어 있는 검토된 패키지의 소스 4개와 `source-manifest.json`을 함께 설치해야 합니다. 실행하는 도구와 설치된 소스 묶음이 다르면 거부합니다. 새 저장소의 CLI만 실행해 오래된 설치를 갱신하면, 기존 시작 스크립트가 중간에 끊긴 업데이트를 알아보지 못하기 때문입니다.

이번 변경은 기본 설치 버전 1.102.4를 유지합니다. 먼저 기존 패키지 업데이터로 새 도구를 제공할 수 있게 하는 단계입니다. 기존 노드나 신규 설치 버전을 조용히 바꾸지 않습니다. 나중에 기본 버전을 바꾸는 패키지는 별도의 전환 검증이 필요하며, 현재 패키지 업데이터의 바이너리 버전 변경 제한도 유지됩니다.

## 변경 없이 확인하기

```bash
/workspace/.dots-tailscale/dots-ts runtime check --json
```

기본은 플랫폼 프록시(`UPSTREAM_PROXY`, 없으면 `http://browser-proxy:8889`)를 명시적으로 사용합니다. `--proxy direct`는 직접 HTTPS 연결을 선택할 뿐 플랫폼의 외부 연결 제한을 우회하지 않습니다. `check --version 1.104.2`는 그 숫자와 비교만 하며, 공개된 최신 버전인지 확인하지 않습니다. 설치 버전을 읽지 못한 경우 먼저 원인을 확인하세요.

온라인 적용은 요청을 시작할 때 공식 stable 목록에 나온 정확한 버전을 지정해야 합니다. 불안정 minor 번호·prerelease 표기·다운그레이드·같은 버전 재설치는 거부합니다. 도중에 새 릴리스가 나와도 이미 선택한 다운로드 주소를 바꾸지 않습니다.

## 직접 적용하기

Tailscale 연결이 끊겨도 사용할 수 있는 Dots 클라우드 터미널을 먼저 열고 제어되는지 확인하세요. 외부 자동복구를 사용 중이면 잠시 중지하고, 외부 접속 확인까지 터미널을 유지하세요. 확인 플래그는 사용자의 확인을 받는 장치이며 실제 GUI 제어나 작업 중지를 도구가 검증하는 것은 아닙니다.

확인한 버전이 1.104.2인 경우의 예시입니다.

```bash
/workspace/.dots-tailscale/dots-ts runtime apply \
  --version 1.104.2 --proxy platform \
  --confirm-recovery-terminal --json
```

이 예시는 1.104.2가 Tailworm을 해결했다는 뜻이 아닙니다. 해당 보고서도 1.104.2를 대상으로 했습니다. 적용 전 [공식 보안 공지](https://tailscale.com/security-bulletins)를 확인하세요.

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
| `failed_rolled_back` | 새 daemon 실행을 시도하기 전에 실패해 기존 바이너리 쌍을 복원하고 확인했습니다. |
| `rejected` | 적용을 거부했습니다. 고정 오류 코드와 journal을 확인하세요. |
| `manual_recovery_required` | 실행·프로세스 소유·진행 기록·state 호환성을 확정할 수 없습니다. 독립 터미널에서 확인하고, 재실행이나 차단 표식 삭제로 넘기지 마세요. |

`applied` 이후 새 외부 SSH 연결과 Tailscale ping, 사용하는 애플리케이션 경로를 확인하세요. 그 뒤 외부 자동복구를 재개합니다. 로컬 `Running`만으로 전체 연결이 복구됐다고 판단하지 않습니다.

## 외부 다운로드가 막힐 때

신뢰할 수 있는 장비에서 공식 파일과 checksum을 받아 전송합니다. 예시 디렉터리에는 다음 두 파일이 있어야 합니다.

```text
tailscale_1.104.2_amd64.tgz
tailscale_1.104.2_amd64.tgz.sha256
```

설치·작업 디렉터리 밖에 두세요. bundle 디렉터리는 실행 사용자 소유·0700이어야 하며, 파일은 같은 사용자 소유의 일반 파일이어야 합니다. 심볼릭 링크와 그룹·다른 사용자에게 쓰기를 허용한 파일은 거부합니다.

```bash
/workspace/.dots-tailscale/dots-ts runtime apply \
  --version 1.104.2 --bundle /workspace/staged-tailscale \
  --confirm-recovery-terminal --json
```

이 경로는 운영자가 신뢰한 bundle을 사용하는 선택입니다. 최신 공개 버전이라는 뜻은 아닙니다. checksum은 SHA-256만 들어 있거나 정확한 archive 이름이 붙은 한 줄이어야 합니다. HTTPS와 같은 출처의 checksum 대조는 전송·파일 일관성을 확인하며, 별도의 배포자 서명 검증은 아닙니다. 출처가 불분명한 파일은 checksum이 맞아도 사용하지 마세요.

## 실패했을 때의 처리 범위

도구는 노드 state나 SSH 개인키를 복사·교체·출력·초기화하지 않습니다. 소켓·userspace 인자·daemon 프록시/CA 환경·공개 노드 identity·SSH 호스트 공개키를 확인하며 shim과 OpenSSH는 유지합니다. 환경 변수 값은 메모리에서만 유지하고 journal에 저장하지 않습니다.

Daemon을 중지하기 전에 `.runtime-update-block` 표식을 만듭니다. 시작·프록시·패키지 변경 경로는 표식이 있으면 중단하므로, worker가 끊기거나 새로 부팅돼도 섞인 바이너리를 그대로 시작하지 않습니다. 정상 적용 또는 새 daemon 실행 전의 검증된 원복을 마친 worker만 자기 표식을 지웁니다. check/status는 계속 사용할 수 있습니다.

새 daemon 실행을 시도한 뒤에는 state 형식이 바뀌었을 수 있습니다. 하위 호환성을 추정해 옛 바이너리를 자동으로 다시 실행하지 않습니다. 이 단계 이후 실패하면 백업과 표식을 남기고 수동 복구를 요구합니다. 과거 state 복원이나 재로그인으로 실패를 덮지 마세요. 독립 터미널에서 journal·바이너리 쌍의 해시·daemon/socket identity와 상위 버전의 state 호환성을 확인해야 합니다.

오프라인 테스트는 실제 Dots의 바이너리 갱신이나 장애 복구를 입증하지 않습니다. 선택적 자동화는 #31에서 이 경로의 실환경 검증 후 다룹니다. VM 교체로 updater 프로세스가 사라지면, `/workspace`에 파일이 남아 있다는 이유만으로 작업이 이어진다고 보장할 수 없습니다.
