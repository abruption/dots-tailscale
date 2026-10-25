<h1 align="center">dots-tailscale</h1>

<p align="center"><a href="README.md">English</a> · 한국어 · <a href="README.zh-CN.md">简体中文</a> · <a href="README.ja.md">日本語</a></p>

Dots 클라우드 컴퓨터에서 일반 사용자 권한으로 Tailscale과 OpenSSH를 실행합니다. 설치 파일, Tailscale 상태, SSH 키는 `/workspace`에 보관하고 실행 중 필요한 소켓·PID·로그는 `/tmp`에 둡니다.

`/workspace`가 환경 교체 후 보존되는 환경을 대상으로 합니다. 새 환경에서는 start 명령을 실행해야 합니다. OpenAI 또는 Tailscale 공식 도구가 아닌 독립 프로젝트입니다.

## 외부 연결 복구 실험 기능

고급 사용자를 위한 [외부 복구 실험 안내](docs/experimental-recovery.ko.md)를 제공합니다. 외부 서버·개인 SSO·승인된 Dots 작업이 필요하며 일반 설치기로 활성화되지 않습니다. 다른 계정의 설치 재현과 임의의 VM 교체 후 복구는 아직 검증하지 않았습니다.

## 데모

![Dots 터미널에서 설치 상태와 Tailscale 버전 1.102.4를 확인하는 스크린샷](docs/assets/dots-tailscale-ssh-verification.png)

관리자가 제공한 Dots 데스크톱 터미널 화면입니다. 기존 설치 파일과 상태, Tailscale 1.102.4 버전을 확인한 모습이며, 새로 설치하거나 VM을 교체하는 장면은 아닙니다.

```bash
bash /workspace/.dots-tailscale/start.sh check
/workspace/.dots-tailscale/tailscale version
```

## 빠른 시작

Linux x86_64, Git, Python 3, OpenSSH와 사용자 계정의 22번 포트 바인딩 권한이 필요합니다. 전체 의존성 및 SSH 설정은 [한국어 사용 가이드](docs/usage.ko.md)를 참고하세요.

확인한 Dots SSH 환경에서는 GitHub에 직접 HTTPS 연결이 실패해 아래 clone 명령에 제공된 프록시를 지정했습니다. 연결이 실패하면 [다운로드·프록시 확인 방법](docs/usage.ko.md#download-proxy)을 참고하세요.

```bash
cd /workspace
HTTPS_PROXY=http://browser-proxy:8889 HTTP_PROXY=http://browser-proxy:8889 \
  git clone --branch v0.5.0 --depth 1 https://github.com/abruption/dots-tailscale.git
cd dots-tailscale
bash tailscale-setup.sh check
bash tailscale-setup.sh install
bash /workspace/.dots-tailscale/start.sh login
```

최초 로그인 URL을 승인한 뒤 SSH 공개키를 설치 경로의 `authorized_keys`에 등록하세요. 호스트 키 지문은 클라우드 컴퓨터의 GUI 터미널에서 확인한 후 SSH로 접속합니다. 환경이 교체되면 `bash /workspace/.dots-tailscale/start.sh`로 기존 설치를 직접 실행해야 합니다. 스크립트는 자동 시작을 등록하지 않습니다.

## v0.4.0: 진단과 비공개 HTTP 접속

아래 도구를 사용하려면 [v0.4.0](https://github.com/abruption/dots-tailscale/releases/tag/v0.4.0)의 소스 파일 네 개와 `source-manifest.json`을 함께 설치하세요.

```bash
/workspace/.dots-tailscale/dots-ts doctor
/workspace/.dots-tailscale/dots-ts cli status
/workspace/.dots-tailscale/dots-ts proxy show
```

`doctor`는 상태를 바꾸지 않고 로컬 진단 정보를 읽습니다. `cli`는 설치된 Tailscale 실행 파일과 소켓을 선택합니다. 선택 기능인 로컬 SOCKS5·HTTP 프록시는 기본적으로 꺼져 있으며, 활성화하려면 데몬 재시작을 별도로 검토해야 합니다. 설정과 접근 정책, 검증 범위는 [사용 가이드](docs/usage.ko.md#development-helpers)와 [비공개 HTTP API 연결 예시](docs/private-http-api.ko.md)를 참고하세요. ChatGPT 커넥터·MCP 클라이언트·Dots 브라우저에 자동으로 적용되지는 않습니다.

## 설치된 패키지 버전과 업데이트

v0.4.0의 `dots-ts version`과 `dots-ts version --json`은 dots-tailscale 패키지 버전과 내부 Tailscale 버전을 따로 보여줍니다. v0.3.1에는 포함되지 않은 명령입니다. 자세한 내용은 [패키지 버전과 파일 확인](docs/usage.ko.md#package-version)을 참고하세요.

v0.4.0에는 정식 릴리스 확인, 선택적으로 켜는 캐시 알림, 코드 백업과 원복을 갖춘 수동 업데이트도 포함됩니다. 실행 조건, 복구 터미널 확인과 별도 SSH 검증은 [업데이트 안내](docs/usage.ko.md#update-management)를 참고하세요.

## 구성 요소

| 경로 | 역할 |
|---|---|
| `tailscale-setup.sh`, `shim.py` | Tailscale을 점검·설치·기동하고 로컬 OpenSSH를 설정하며 Tailscale 인증을 명시적으로 수행 |
| `dots-ts`, `dots_proxy.py` | CLI·진단과 프로세스별 외부 연결 프록시 설정 |
| `source-manifest.json` | 패키지 버전과 소스 파일 네 개의 SHA-256 체크섬 |
| `home-persistence-probe.py` | HOME과 `/workspace` 지속성 마커를 기록하고 변화 확인 |
| `tests/` | 오프라인 회귀 테스트 |

[English guide](docs/usage.md) · [한국어 사용 가이드](docs/usage.ko.md) · [简体中文使用指南](docs/usage.zh-CN.md) · [日本語の使い方](docs/usage.ja.md) · [MIT License](LICENSE)
