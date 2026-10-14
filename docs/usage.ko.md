# 사용 가이드

[English](usage.md) · [프로젝트 소개](../README.ko.md)

`/workspace`가 환경 교체 후에도 유지되는 Dots 환경에서 실행합니다. 설치·인증 상태와 실행 중에만 필요한 파일을 다른 위치에 두어 재기동 시 기존 장치 신원을 이어 사용합니다.

공개 저장소 주소는 `https://github.com/abruption/dots-tailscale`입니다. 최초 공개 시 실제 clone과 raw 다운로드를 확인했습니다. [v0.1.0 릴리스](https://github.com/abruption/dots-tailscale/releases/tag/v0.1.0)에 ZIP·tar.gz와 `tailscale-setup.sh`·`shim.py` 파일이 올라와 있습니다. `main`은 이후 변경될 수 있으므로 같은 소스로 다시 설치하려면 검토한 tag나 commit을 지정하세요.

## 최초 설치

Linux x86_64, Python 3, OpenSSH, `timeout`, `flock`, `openssl`, `ssh-keygen`, `curl`, `tar`, `sha256sum`, 일반 사용자 포트 22 바인딩을 전제로 합니다. 다운로드에 사용할 기본 프록시는 `http://browser-proxy:8889`, Tailscale 다운로드 버전은 `1.102.4`로 고정하고 공식 SHA-256과 대조합니다.

Git이 설치된 VM 터미널에서 HTTPS로 저장소를 clone한 뒤 설치 스크립트를 실행합니다. Git 네트워크 요청에만 프록시 환경변수를 적용합니다. 인증서 검증은 유지합니다.

```bash
cd /workspace
HTTPS_PROXY=http://browser-proxy:8889 HTTP_PROXY=http://browser-proxy:8889 \
  git clone 'https://github.com/abruption/dots-tailscale' dots-tailscale
cd dots-tailscale
bash tailscale-setup.sh check
bash tailscale-setup.sh install
bash /workspace/.dots-tailscale/start.sh login
```

`login`에서 출력하는 URL로 인증합니다. `install`은 파일만 배치하며 프로세스를 시작하지 않습니다. 기존 바이너리·설정·키는 덮어쓰지 않고 파일 차이·부분 손상은 중단합니다. `start.sh`는 설치 과정에서 생성되는 래퍼입니다. 설치된 경로에서 사용하세요.

`check`는 설치 파일의 존재 여부를 출력하고, 실행 중인 소켓이 있으면 Tailscale 상태도 보여줍니다. `missing:`이 있어도 종료코드가 `0`일 수 있으므로 성공 코드만으로 설치 완료를 판단하지 마세요.

기존 `$HOME/.ssh/authorized_keys`가 있으면 설치 시 복사합니다. 없으면 빈 `/workspace/.dots-tailscale/sshd/authorized_keys`를 만들므로 사용할 장비의 **공개키**를 한 줄로 추가하고 권한을 `600`으로 지정합니다. SSH 호스트 키 지문은 VM의 GUI 터미널 등 별도 경로로 확인하고 클라이언트 지문과 대조하세요. 호스트 키 검증을 우회하지 않습니다.

Dots의 GUI 터미널에서 다음 명령으로 지문을 확인합니다.

```bash
ssh-keygen -lf /workspace/.dots-tailscale/sshd/ssh_host_ed25519_key.pub
```

접속할 장비에서 `<TAILNET_IP>`를 실제 Dots 주소로 바꾸고 실행합니다. 실행 중인 `start.sh check` 출력에서 주소를 확인할 수 있습니다. 최초 접속 시 표시되는 지문을 위 결과와 대조한 뒤 일치할 때만 승인하세요.

```bash
ssh -o StrictHostKeyChecking=ask -i /path/to/client_private_key 'agent@<TAILNET_IP>' 'id; pwd'
```

승인한 뒤에는 `ssh -o StrictHostKeyChecking=yes -i /path/to/client_private_key 'agent@<TAILNET_IP>' 'id; pwd'`로 접속할 수 있습니다. 사용자명과 SSH 키 경로는 자신의 설정에 맞게 지정하세요. 이후 지문이 달라지면 Dots GUI 터미널에서 다시 확인하세요.

## Git 없이 직접 다운로드

`raw.githubusercontent.com`에서 설치 스크립트와 shim을 같은 디렉터리에 받으면 Git 없이 설치할 수 있습니다. 설치 스크립트는 shim 파일을 찾으므로 두 파일을 모두 내려받은 뒤 실행합니다. 아래 예시는 공개 릴리스 `v0.1.0`으로 고정합니다. 최신 개발 소스를 검토하려면 `DOTS_TS_REF`를 검토한 commit이나 `main`으로 바꾸세요. 두 파일에 같은 값을 사용하며, `main`은 다운로드 사이에도 변경될 수 있습니다.

```bash
mkdir -p /workspace/dots-tailscale-download
cd /workspace/dots-tailscale-download
DOTS_TS_REF=v0.1.0
curl -fsSL --proxy http://browser-proxy:8889 \
  "https://raw.githubusercontent.com/abruption/dots-tailscale/${DOTS_TS_REF}/tailscale-setup.sh" \
  -o tailscale-setup.sh
curl -fsSL --proxy http://browser-proxy:8889 \
  "https://raw.githubusercontent.com/abruption/dots-tailscale/${DOTS_TS_REF}/shim.py" \
  -o shim.py
```

두 다운로드가 모두 성공했는지 확인하고 파일을 검토한 뒤 실행합니다.

```bash
bash tailscale-setup.sh check
bash tailscale-setup.sh install
bash /workspace/.dots-tailscale/start.sh login
```

기존 설치를 업데이트할 때는 설치 디렉터리 밖의 별도 소스 디렉터리에 두 파일을 내려받아 검토하고, 아래 [업데이트 절차](#restart-update)에 따라 코드 파일만 갱신하세요. `install`은 기존의 다른 파일을 덮어쓰는 업데이트 명령이 아닙니다. HOME 관찰 도구도 사용할 경우에는 전체 clone 방식이 편리합니다.

<a id="restart-update"></a>

## 재기동·업데이트

```bash
bash /workspace/.dots-tailscale/start.sh
bash /workspace/.dots-tailscale/start.sh check
```

일반 기동에서는 인증을 시작하지 않습니다. `start`·`login`·`auth`에서 Tailscale backend가 `Running`이고 로컬 SSH 배너를 확인하면 종료코드 `0`, 인증 필요는 `10`, 그 밖의 BackendState는 `11`을 반환합니다. 기타 구성·기동 실패도 비영 종료코드입니다. 필요할 때만 `start.sh login`을 실행합니다.

설치 위치는 `DOTS_TS_DIR`, 장비 이름은 `NODE_HOSTNAME`, 업스트림 프록시는 `UPSTREAM_PROXY`로 변경합니다. 설치된 래퍼는 자기 디렉터리를 `DOTS_TS_DIR`로 사용합니다. 프록시 URL은 인증 없는 HTTP CONNECT 주소를 지원합니다.

shim 수정이 포함된 업데이트는 **setup.sh만 교체해서 완료되지 않습니다.** 다음 절차는 운영자가 별도로 수행할 배치·복구 절차이며 현재 자동 updater로 구현돼 있지 않습니다. 2026-10-14 shim 수정본의 실환경 배포 후 DERP ping과 엄격한 호스트 키 검증을 유지한 SSH 연결은 성공했습니다. 수정본 상태에서 이후 VM 교체를 거친 복구는 아직 관찰하지 않았습니다. 자세한 범위는 아래 [현재 확인한 범위](#current-validation)를 참고하세요.

1. Tailscale과 독립적인 플랫폼 GUI 터미널을 확보합니다. 일시·시간대·소스 리비전·설치 setup/shim 해시·OS·Tailscale/클라이언트 버전·프록시 전제·공개 SSH 호스트 키 지문·probe 결과를 VM 밖 비공개 위치에 기록합니다.
2. 설치 디렉터리 밖의 별도 staging 체크아웃에서 검토할 커밋을 준비합니다. `<REVIEWED_COMMIT>`을 실제 검토한 커밋으로 바꾸고, 이동 후 리비전과 두 파일 해시를 다시 기록합니다. `git status --short` 출력이 없는 깨끗한 소스를 사용합니다. 다음 명령으로 파일을 가져온 뒤 셸 구문과 shim 구문을 확인할 수 있습니다.

```bash
cd /workspace/dots-tailscale
HTTPS_PROXY=http://browser-proxy:8889 HTTP_PROXY=http://browser-proxy:8889 \
  git fetch --tags origin
git switch --detach '<REVIEWED_COMMIT>'
git rev-parse HEAD
git status --short
bash -n tailscale-setup.sh
python3 -c 'from pathlib import Path; compile(Path("shim.py").read_text(), "shim.py", "exec")'
sha256sum tailscale-setup.sh shim.py
```

3. 기존 **setup/shim 코드 파일만** 별도 보관합니다. state·CA·SSH 개인키는 복제하거나 삭제하지 않습니다.
4. GUI 터미널에서 실행 파일/명령행이 설치된 `shim/shim.py`와 일치하는 정확한 listener PID를 확인해 해당 프로세스만 종료합니다. 광범위한 `pkill python` 등은 사용하지 않습니다. 중복 shim이 없음을 확인한 뒤 `setup.sh`와 `shim/shim.py`에 검토한 파일을 모드 `700`으로 배치합니다. 이때 Tailscale 연결이 잠시 끊길 수 있습니다.
5. 독립 터미널에서 설치된 `start.sh`를 실행합니다. 연결에 의존하는 셸에서 계속할 수밖에 없는 경우에는 먼저 제한 시간과 로그 경로를 지정한 분리 실행 및 코드 롤백 계획을 준비하세요. 이 문서는 자동 롤백이 있다고 주장하지 않습니다.
6. 설치된 두 파일의 SHA-256이 검토한 소스와 일치하는지 확인합니다. backend `Running`, 로컬 SSH 배너, 클라이언트의 tailnet ping, 엄격한 호스트 키 검증을 유지한 SSH 공개키 로그인·명령 실행을 각각 검사하고 종료코드·일시를 기록합니다.
7. 실패 시 독립 터미널에서 이전 **코드만** 복원하고 필요한 프로세스를 다시 기동합니다. 실패·롤백 결과를 기록하며 인증 파일 초기화로 해결하지 않습니다.

환경 교체 후에는 `start.sh`를 직접 실행해야 합니다. 자동 시작이나 Dots 자동화 등록 기능은 구현하지 않았으며, Dots 자동화로 기동하는 방식도 시험하지 않았습니다.

이 업데이트 작업은 프로세스 재시작이며 VM 교체 검증이 아닙니다. 자연스러운 실제 교체 전후에는 HOME/workspace 관찰 결과를 클라우드 컴퓨터 밖에 보관하고 비교합니다. 아직 교체가 확인되지 않으면 관찰 대기로 유지합니다.

| 보관 위치 | 내용 |
|---|---|
| `/workspace/.dots-tailscale/state` | Tailscale 인증 state |
| `…/shim` | shim·CA·개인키·인증서 |
| `…/sshd` | SSH 설정·호스트 키·authorized_keys |
| `/tmp/dots-tailscale-1000` | UID 1000 기준 소켓·PID·로그 |

이미 인증했던 설치에서 state가 사라지면 다른 신원을 자동 생성하지 않고 중단합니다. 동일 state를 여러 실행 중인 VM에 복제하지 마세요. `/proc`의 일부 프로세스가 접근 거부돼도 탐색을 계속하며, CLI가 응답하는 기존 tailscaled는 재사용합니다. 식별되지 않는 데몬의 소켓은 자동 삭제하지 않습니다.

`StrictModes no`를 사용하되 보관 디렉터리는 `700`, SSH 키·authorized_keys·CA 개인키는 `600`으로 제한합니다. `/workspace` 상위 경로 권한 때문에 공개키 인증이 거부되는 것을 피하기 위한 설정입니다. 기동 검증은 CLI와 로컬 SSH 배너까지이며 원격 공개키 로그인을 매번 확인하지 않습니다. 로그는 재기동 시 1 MiB 초과 파일을 한 세대 회전하며 계속 실행 중인 로그 크기에는 상한이 없습니다.

## 선택적 Auth Key 인증

브라우저 없이 등록하려면 Tailscale 관리 화면에서 발급한 Auth Key를 별도 파일에 저장합니다. 키는 채팅·스크립트·Git에 넣지 마세요. 파일은 현재 사용자 소유의 일반 파일이어야 하며, 절대 경로·심볼릭 링크 없는 경로·권한 `600` 또는 `400`이 필요합니다.

```bash
DOTS_TS_AUTH_KEY_FILE=/workspace/.dots-tailscale/auth.key \
  bash /workspace/.dots-tailscale/start.sh auth
```

CLI의 `--auth-key=file:<경로>`로 공급하므로 키 값은 명령행 인자에 노출하지 않습니다. 이미 `Running`이면 키 파일 없이 기존 상태를 사용합니다. 다른 상태에서는 최대 60초 실행 제한과 45초 연결 제한으로 인증하며 실패 시 브라우저로 자동 전환하지 않습니다. 관리 측 device approval이 필요한 경우 키 설정에 따라 별도 승인도 필요할 수 있습니다.

스크립트는 `--force-reauth`, `--reset`, tag·ACL 변경을 수행하지 않습니다. 이전에 인증했던 state가 사라진 경우의 중단 정책은 `auth`에서도 유지합니다. 자동 신원 재생성은 지원하지 않습니다. 실제 키 인증은 아직 시험하지 않았습니다.

## HOME 초기화 관찰

```bash
# 체크아웃 경로에서:
python3 /workspace/dots-tailscale/home-persistence-probe.py
```

HOME의 `.dots-home-persistence-marker.json`, workspace의 `.dots-persistence-probe/workspace-marker.json`에 비밀 없는 식별자를 둡니다. `/workspace/.dots-persistence-probe/`에 `baseline.json`, `latest.json`, `history.jsonl`을 저장합니다. 사용자 파일·인증 정보 내용은 읽지 않습니다.

- `baseline_created`: 최초 기준 생성
- `markers_intact`: 두 식별자 유지
- `home_marker_changed_workspace_intact`: HOME 식별자 변경·workspace 유지
- `marker_continuity_changed`: 다른 식별자 변화

hostname·boot_id·PID 1 시작 값도 비교합니다. 식별 파일 소실은 파일 변경의 증거이며 HOME 전체 초기화나 VM 교체를 단독으로 입증하지 않습니다. 최초 기준을 유지하므로 이후 변화는 계속 표시합니다. 두 경로가 모두 사라지면 스크립트·기준도 소실될 수 있습니다. 다음과 같이 관찰 결과를 클라우드 컴퓨터 밖에 보관하면 HOME·workspace가 함께 소실된 경우에도 비교할 수 있습니다. 이 스크립트에는 자동 스케줄 등록 기능이 없습니다.

```bash
ssh agent@dots-sandbox 'python3 /workspace/dots-tailscale/home-persistence-probe.py' > observation.json
```

SSH 키 파일·호스트 별칭은 자신의 설정에 맞게 지정하세요. 결과에는 hostname과 식별자가 포함되므로 공개하기 전에 익명화하세요.

<a id="current-validation"></a>

## 현재 확인한 범위

![Dots 데스크톱 터미널에서 설치 파일·상태와 Tailscale 버전을 확인하는 스크린샷](assets/dots-tailscale-ssh-verification.png)

관리자가 제공한 Dots 데스크톱 터미널의 스크린샷을 그대로 사용했습니다. 다음 두 명령으로 기존 설치 파일·상태와 Tailscale `1.102.4`를 확인한 화면입니다. 새로 설치하는 과정이나 VM 교체 전후를 보여주는 화면은 아닙니다.

```bash
bash /workspace/.dots-tailscale/start.sh check
/workspace/.dots-tailscale/tailscale version
```

- 사용자는 이전 버전에서 24시간 이상 동작과 환경 교체 후 `/workspace`의 설치 파일이 남아 `start.sh`로 재시작되는 것을 확인했습니다.
- 2026-10-14 shim 수정본을 실환경에 배포한 뒤 DERP ping과 엄격한 호스트 키 검증을 유지한 SSH 공개키 로그인·명령 실행을 확인했습니다. 이 수정본으로 이후 VM 교체를 거친 복구는 아직 관찰하지 않았습니다.
- `check`는 파일 목록과 상태 확인용입니다. `start`의 `running: Tailscale and local SSH ready`는 backend와 로컬 SSH 배너 확인을 뜻하며, 외부 장비에서의 SSH 공개키 로그인을 매번 검사하는 것은 아닙니다.
- Auth Key 인증과 Dots 자동화를 이용한 자동 기동은 아직 시험하지 않았습니다.

## 검증과 문제 해결

셸 검사와 오프라인 회귀 테스트는 저장소의 `tests/`에서 실행합니다. 테스트는 실제 인증이나 외부 연결을 수행하지 않습니다.

- 이전 버전의 `cannot inspect process` 또는 현재의 `process inspection failed`: 현재 스크립트는 일부 `/proc` 접근 거부를 건너뜁니다. 기존 설치의 `setup.sh`는 위 [업데이트 절차](#restart-update)에 따라 검토한 코드로 갱신하세요.
- `Permission denied (publickey)`: `sshd/authorized_keys`의 공개키 지문을 확인하고 접속 장비의 공개키를 등록하세요. `running` 메시지는 공개키 인증 성공까지 확인하지 않습니다.
- `existing file differs`: 설치 파일 차이입니다. 설정·키를 검토한 뒤 필요한 파일만 갱신하세요. 설치 디렉터리 전체를 지우면 state와 호스트 키도 사라집니다.
- `socket exists but daemon cannot be identified`: 기존 데몬과 소켓 상태를 확인하세요. 스크립트는 이 경우 소켓을 자동 삭제하지 않습니다.
- `^[[200~` 등 붙여넣기 제어문자가 섞이는 경우: 명령을 취소하고 한 줄씩 입력해 실제 명령 실행과 공개키 지문을 확인하세요.
