<h1 align="center">dots-tailscale</h1>

<p align="center"><a href="https://github.com/abruption/dots-tailscale/stargazers"><img src="https://img.shields.io/github/stars/abruption/dots-tailscale?style=flat&logo=github" alt="GitHub Stars"></a></p>

<p align="center"><a href="README.md">English</a> · 한국어 · <a href="README.zh-CN.md">简体中文</a> · <a href="README.ja.md">日本語</a></p>

내 기기에서 Tailscale과 SSH로 Dots 클라우드 컴퓨터에 접속할 수 있게 해주는 도구입니다. root 권한이나 systemd 없이 설치할 수 있으며, 설치 파일과 Tailscale 기기 정보, SSH 키를 `/workspace`에 보관합니다. 실행 환경이 바뀌어도 이 경로가 남아 있다면 기존 정보를 다시 사용할 수 있습니다.

**현재 릴리스는 [v0.7.0](https://github.com/abruption/dots-tailscale/releases/tag/v0.7.0)입니다.** 신규 설치의 Tailscale을 1.104.4로 올리고, 바이너리 검증과 기존 패키지의 명시적 전환 절차를 추가했습니다. 복구 전달기는 삭제된 Python 실행 환경도 감지합니다. OpenAI나 Tailscale의 공식 도구가 아닌 독립 프로젝트입니다.

**v0.6.0 등 이전 기본 버전에서 업데이트한다면:** Tailscale 기본값이 1.102.4인 패키지는 실행 파일을 먼저 갱신한 뒤 [명시적 보안 기본 버전 전환](docs/security-baseline.ko.md)을 거쳐야 합니다. 일반 `update apply`의 기본 버전 변경 제한은 유지됩니다.

## 할 수 있는 일

| 작업 | 제공하는 기능 |
|---|---|
| 설치와 재접속 | Tailscale과 로컬 OpenSSH를 실행합니다. 설치 경로가 보존됐다면 기존 기기 정보와 키를 그대로 사용해 다시 시작할 수 있습니다. |
| 연결 상태 확인 | `dots-ts version`과 `doctor`로 설치 파일의 무결성, 실행 중인 프로세스, 소켓과 Tailscale 상태를 확인합니다. |
| 비공개 서비스 접속 | Tailscale 네트워크로 연결하거나, 필요한 앱에만 로컬 SOCKS5·HTTP 프록시를 설정해 사용할 수 있습니다. |
| 패키지 업데이트 | `dots-ts update`로 릴리스를 확인하고 스크립트를 직접 업데이트합니다. 선택적으로 캐시된 로컬 알림도 켤 수 있습니다. |
| Tailscale 업데이트 | `dots-ts runtime`으로 버전을 확인하고 CLI와 데몬을 함께 교체합니다. 진행 상태와 복구 기록은 파일로 남깁니다. |

자동 시작과 자동 업데이트는 활성화하지 않습니다. 아래에서 소개하는 외부 복구 실험은 별도로 설정해야 합니다.

## 빠른 시작

**처음 설치한다면** Dots 클라우드 터미널에서 진행하세요. Linux x86_64, Python 3, Git, OpenSSH와 로컬 22번 포트를 사용할 권한이 필요합니다. [전체 필요 도구와 SSH 설정](docs/usage.ko.md)을 먼저 확인하세요. 기존 사용자는 다시 설치하지 말고 다음 업데이트 절차를 따라주세요.

```bash
cd /workspace
HTTPS_PROXY=http://browser-proxy:8889 HTTP_PROXY=http://browser-proxy:8889 \
  git clone --branch v0.7.0 --depth 1 https://github.com/abruption/dots-tailscale.git
cd dots-tailscale
bash tailscale-setup.sh check
bash tailscale-setup.sh install
bash /workspace/.dots-tailscale/start.sh login
```

첫 로그인 때 출력되는 승인 URL을 여세요. 접속할 기기의 공개키를 설치된 SSH의 `authorized_keys` 파일에 추가하고, 접속 전에 클라우드 터미널에서 호스트 키 지문을 직접 대조하세요. 자세한 순서는 [사용 가이드](docs/usage.ko.md)에 있습니다.

시험한 Dots SSH 환경에서는 GitHub에 직접 HTTPS로 접속하지 못해, 위 clone 명령에 플랫폼 프록시를 명시했습니다. 그래도 다운로드가 안 된다면 [접속 진단과 파일 전달 대안](docs/usage.ko.md#download-proxy)을 확인하세요. 소스 파일 네 개와 해당 버전의 원본 `source-manifest.json`은 항상 함께 두어야 합니다.

실행 환경이 바뀐 뒤에도 설치 파일이 남아 있다면 다음 명령으로 다시 시작할 수 있습니다.

```bash
bash /workspace/.dots-tailscale/start.sh
```

## 기존 설치 업데이트

관리하는 버전은 두 가지입니다.

| 명령 | 업데이트하는 대상 | 업데이트하지 않는 대상 |
|---|---|---|
| `dots-ts update` | dots-tailscale 스크립트와 패키지 정보 | Tailscale 실행 파일 |
| `dots-ts runtime` | 설치된 `tailscale`과 `tailscaled` 한 쌍 | dots-tailscale 패키지 |

먼저 설치된 패키지와 업데이트할 릴리스를 확인하세요.

```bash
/workspace/.dots-tailscale/dots-ts version
/workspace/.dots-tailscale/dots-ts update check --tag v0.7.0 --proxy http://browser-proxy:8889 --json
```

기본 Tailscale을 1.102.4로 선언하는 v0.6.0 등 기존 패키지는 [보안 기본 버전 전환](docs/security-baseline.ko.md)에 따라 v0.7.0으로 이동하세요. 설치된 도구가 v0.6.0보다 오래됐다면 [패키지 업데이트 안내](docs/usage.ko.md#update-management)로 같은 기본 버전의 v0.6.0 도구를 먼저 준비하세요. 신뢰할 수 있는 메타데이터가 없는 이전 설치는 수동 확인이 필요합니다.

v0.6.0 이상 도구가 설치됐다면 공식 Tailscale 안정 버전을 확인할 수 있습니다.

```bash
/workspace/.dots-tailscale/dots-ts runtime check --json
```

**v0.7.0으로 새로 설치하면 Tailscale은 1.104.4로 시작합니다.** 패키지 업데이트나 `install`이 기존 실행 파일을 조용히 바꾸지는 않습니다. 실행 파일을 갱신하려면 조회된 버전과 공식 공지를 검토한 뒤 [수동 업데이트 가이드](docs/runtime-updates.ko.md)를 따르세요. 기본 버전 전환은 설치된 두 실행 파일과 실행 중인 백엔드가 모두 정확히 1.104.4인 것을 확인한 뒤 소스를 바꿉니다.

업데이트를 적용하기 전에 Tailscale SSH가 끊겨도 사용할 수 있는 별도의 클라우드 복구 터미널을 확인하세요. Tailscale 자체를 업데이트할 때는 외부 복구 작업을 잠시 중지하고, 새 SSH 연결과 ping이 정상인지 확인한 뒤 다시 켜세요. 새 데몬의 실행을 시도한 뒤에는 시작에 실패했더라도 자동으로 이전 버전으로 되돌리지 않습니다. 실패 원인이 불확실하면 가이드의 수동 복구 절차가 필요합니다. 패키지 코드의 원복과 Tailscale 상태 정보의 하위 버전 호환성은 별개입니다.

## 자주 쓰는 명령

```bash
# 패키지 무결성과 별도로 관리되는 Tailscale 버전 확인
/workspace/.dots-tailscale/dots-ts version
# 로컬 설치와 연결 상태 진단
/workspace/.dots-tailscale/dots-ts doctor
# 설치된 Tailscale CLI와 해당 소켓으로 실행
/workspace/.dots-tailscale/dots-ts cli status
# 선택적으로 사용하는 앱 프록시 설정 확인
/workspace/.dots-tailscale/dots-ts proxy show
```

로컬 SOCKS5·HTTP 프록시는 기본적으로 꺼져 있습니다. 활성화하려면 데몬을 명시적으로 재시작해야 하며, Dots 브라우저나 ChatGPT 커넥터, MCP 클라이언트에 자동으로 적용되지는 않습니다. [프록시 안내](docs/usage.ko.md#development-helpers)와 [비공개 HTTPS API 연결 예시](docs/private-http-api.ko.md)를 참고하세요.

## 검증한 범위

- [실제 Dots 시험](docs/validation/runtime-update-trial-20261026.md)에서 검토한 개발 패키지로 Tailscale 1.102.4를 1.104.2로 업데이트했습니다. 기존 기기 정보와 SSH 키를 유지했고, 이후 새 외부 SSH 연결과 릴레이 ping도 확인했습니다.
- [v0.6.0 릴리스](https://github.com/abruption/dots-tailscale/releases/tag/v0.6.0)에는 최종 커밋의 기본·연구 테스트와 격리된 환경에서 수행한 공식 실행 파일 검증 기록이 있습니다. 앞으로 나올 모든 버전과의 호환성이나 실환경 장애 복구, 이전 버전으로 되돌릴 때의 상태 호환성을 입증한 것은 아닙니다.
- [v0.7.0 릴리스](https://github.com/abruption/dots-tailscale/releases/tag/v0.7.0)에는 격리된 신규 설치·소스 전환 테스트가 추가됐습니다. 새 소스 전환은 아직 실환경에서 실행하지 않았습니다. 근거와 한계는 [날짜별 기본 버전·TLS 점검 기록](docs/validation/security-baseline-20261028.md)을 확인하세요.
- `/workspace`가 남아 있어야 사용할 수 있으며, 플랫폼이 이 경로의 보존을 보장한다는 뜻은 아닙니다. 실행 중 필요한 소켓·PID·로그는 `/tmp`에 다시 만듭니다. 새 실행 환경에서는 여전히 시작 명령을 실행할 방법이 필요합니다.
- 로컬에서 `Running`이 나와도 외부에서 접속된다는 뜻은 아닙니다. 새 SSH 연결과 실제 사용하는 앱의 접속 경로를 확인하세요. 1.104.2 업데이트로 Tailworm 문제가 해결됐다고 주장하지도 않습니다.

![Dots 터미널에서 설치 상태와 Tailscale 1.102.4를 확인했던 화면](docs/assets/dots-tailscale-ssh-verification.png)

관리자가 제공한 과거의 설치·상태 확인 화면입니다. 현재 패키지 버전이나 VM 교체 후 복구를 보여주는 화면은 아닙니다. 이후 업데이트 시험은 위의 날짜가 명시된 검증 기록에서 확인할 수 있습니다.

## 외부 연결 복구 실험

고급 설정이 필요하다면 [Dots를 통한 외부 복구 실험](docs/experimental-recovery.ko.md)을 살펴보세요. 외부 감시 도구, 인증을 거치는 MCP Events 서버, 사용자가 허용한 Dots 작업으로 연결 장애 후 시작 명령 실행을 요청하는 방식입니다. 일반 설치 과정에서 이 구성을 배포하거나 복구 작업을 켜지는 않습니다.

현재 실험에는 외부 서버와 SSO 설정이 필요합니다. 이벤트 처리가 지연될 수 있고, 다른 계정에서의 설정 재현이나 임의의 VM 교체 후 복구는 아직 검증하지 않았습니다. watchdog과 외부 복구의 조정, 선택적 자동 업데이트는 v0.8.0 후속 과제이며 v0.7.0에서 켜지는 기능이 아닙니다.

## 문서와 구성 파일

- [사용법과 문제 해결](docs/usage.ko.md) · [Tailscale 수동 업데이트](docs/runtime-updates.ko.md)
- [비공개 HTTPS API 연결 예시](docs/private-http-api.ko.md) · [외부 복구 실험](docs/experimental-recovery.ko.md)
- [릴리스 노트](docs/releases/v0.7.0.md) · [변경 이력](CHANGELOG.md) · [MIT 라이선스](LICENSE)

| 경로 | 역할 |
|---|---|
| `tailscale-setup.sh`, `shim.py` | 설치·시작, 로컬 OpenSSH와 플랫폼 프록시를 통한 Tailscale 통신 |
| `dots-ts`, `dots_proxy.py` | 진단, 패키지·Tailscale 업데이트와 선택적 앱 프록시 설정 |
| `source-manifest.json` | 패키지 버전과 소스 파일 네 개의 체크섬 |
| `home-persistence-probe.py` | HOME과 `/workspace`가 유지되는지 확인할 표식 기록 |
| `tests/`, `research/` | 회귀 테스트와 별도로 배포하는 연구 도구 |
