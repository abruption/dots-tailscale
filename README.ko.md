<h1 align="center">dots-tailscale</h1>

<p align="center"><a href="README.md">English</a> · 한국어</p>

Dots 클라우드 컴퓨터에서 사용자 권한으로 Tailscale과 OpenSSH를 실행합니다. 설치 파일·장치 state·SSH identity는 `/workspace`, 실행 socket·PID·로그는 `/tmp`에 둡니다.

`/workspace`가 환경 교체 후 보존되는 환경을 대상으로 합니다. 새 환경에서는 start 명령을 실행해야 합니다. OpenAI 또는 Tailscale 공식 도구가 아닌 독립 프로젝트입니다.

## 데모

![Dots 터미널에서 설치 상태와 Tailscale 버전 1.102.4를 확인하는 스크린샷](docs/assets/dots-tailscale-ssh-verification.png)

관리자가 제공한 Dots 데스크톱 터미널의 스크린샷입니다. 기존 설치 파일과 상태, Tailscale 1.102.4 버전을 확인하는 장면입니다. 제공된 이미지를 그대로 사용했으며, 새로 설치하는 과정은 포함하지 않습니다.

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
  git clone --branch v0.3.1 --depth 1 https://github.com/abruption/dots-tailscale.git
cd dots-tailscale
bash tailscale-setup.sh check
bash tailscale-setup.sh install
bash /workspace/.dots-tailscale/start.sh login
```

최초 로그인 URL을 승인한 뒤 SSH 공개키를 설치 경로의 `authorized_keys`에 등록하세요. 호스트 키 지문은 클라우드 컴퓨터의 GUI 터미널에서 확인한 후 SSH로 접속합니다. 환경이 교체되면 `bash /workspace/.dots-tailscale/start.sh`로 기존 설치를 수동 기동하세요.

## v0.3.1: 진단과 비공개 HTTP 접속

아래 도구를 사용하려면 [v0.3.1](https://github.com/abruption/dots-tailscale/releases/tag/v0.3.1)의 소스 파일 네 개를 함께 설치하세요.

```bash
/workspace/.dots-tailscale/dots-ts doctor
/workspace/.dots-tailscale/dots-ts cli status
/workspace/.dots-tailscale/dots-ts proxy show
```

`doctor`는 상태를 바꾸지 않고 로컬 진단 정보를 읽습니다. `cli`는 설치된 Tailscale 실행 파일과 소켓을 선택합니다. 선택 기능인 로컬 SOCKS5·HTTP 프록시는 기본적으로 꺼져 있으며, 활성화하려면 데몬 재시작을 별도로 검토해야 합니다. 설정과 접근 정책, 검증 범위는 [사용 가이드](docs/usage.ko.md#development-helpers)와 [비공개 HTTP API 연결 예시](docs/private-http-api.ko.md)를 참고하세요. ChatGPT 커넥터·MCP 클라이언트·Dots 브라우저에 자동으로 적용되지는 않습니다.

## 파일

| 경로 | 역할 |
|---|---|
| `tailscale-setup.sh`, `shim.py` | Tailscale·OpenSSH 점검, 설치, 기동 및 명시적 인증 |
| `dots-ts`, `dots_proxy.py` | CLI·진단과 프로세스별 외부 연결 프록시 설정 |
| `home-persistence-probe.py` | HOME과 `/workspace` 식별자 관찰 |
| `tests/` | 오프라인 회귀 테스트 |

[English guide](docs/usage.md) · [MIT License](LICENSE)
