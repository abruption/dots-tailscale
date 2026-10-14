<h1 align="center">dots-tailscale</h1>

<p align="center"><a href="README.md">English</a> · 한국어</p>

Dots 클라우드 컴퓨터에서 사용자 권한으로 Tailscale과 OpenSSH를 실행합니다. 설치 파일·장치 state·SSH identity는 `/workspace`, 실행 socket·PID·로그는 `/tmp`에 둡니다.

`/workspace`가 환경 교체 후 보존되는 환경을 대상으로 합니다. 새 환경에서는 start 명령을 실행해야 합니다. OpenAI 또는 Tailscale 공식 도구가 아닌 독립 프로젝트입니다.

## 데모

![SSH로 설치 상태와 Tailscale 버전 1.102.4를 확인하는 실제 터미널 스크린샷](docs/assets/dots-tailscale-ssh-verification.png)

2026-10-14에 Dots 클라우드 컴퓨터에 SSH로 접속해 캡처한 실제 터미널 스크린샷입니다. tailnet IP와 호스트명, 창 제목의 로컬 사용자명은 가렸습니다. 기존 설치 파일과 상태, Tailscale 1.102.4 버전을 확인하는 장면이며, 새로 설치하는 과정은 포함하지 않습니다.

```bash
bash /workspace/.dots-tailscale/start.sh check
/workspace/.dots-tailscale/tailscale version
```

## 빠른 시작

Linux x86_64, Git, Python 3, OpenSSH와 사용자 계정의 22번 포트 바인딩 권한이 필요합니다. 전체 의존성 및 SSH 설정은 [한국어 사용 가이드](docs/usage.ko.md)를 참고하세요.

```bash
cd /workspace
HTTPS_PROXY=http://browser-proxy:8889 HTTP_PROXY=http://browser-proxy:8889 \
  git clone https://github.com/abruption/dots-tailscale.git
cd dots-tailscale
bash tailscale-setup.sh check
bash tailscale-setup.sh install
bash /workspace/.dots-tailscale/start.sh login
```

최초 로그인 URL을 승인한 뒤 SSH 공개키를 설치 경로의 `authorized_keys`에 등록하세요. 호스트 키 지문은 클라우드 컴퓨터의 GUI 터미널에서 확인한 후 SSH로 접속합니다. 환경이 교체되면 `bash /workspace/.dots-tailscale/start.sh`로 기존 설치를 수동 기동하세요.

## 파일

| 경로 | 역할 |
|---|---|
| `tailscale-setup.sh`, `shim.py` | Tailscale·OpenSSH 점검, 설치, 기동 및 명시적 인증 |
| `home-persistence-probe.py` | HOME과 `/workspace` 식별자 관찰 |
| `tests/` | 오프라인 회귀 테스트 |

[English guide](docs/usage.md) · [MIT License](LICENSE)
