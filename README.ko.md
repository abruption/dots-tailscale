<h1 align="center">dots-tailscale</h1>

<p align="center"><a href="https://github.com/abruption/dots-tailscale/stargazers"><img src="https://img.shields.io/github/stars/abruption/dots-tailscale?style=flat&logo=github" alt="GitHub Stars"></a></p>

<p align="center"><a href="README.md">English</a> · 한국어 · <a href="README.zh-CN.md">简体中文</a> · <a href="README.ja.md">日本語</a></p>

내 기기에서 Tailscale과 SSH로 Dots 클라우드 컴퓨터에 접속할 수 있게 해주는 도구입니다. root 권한이나 systemd 없이 설치할 수 있으며, 설치 파일과 Tailscale 기기 정보, SSH 키를 `/workspace`에 보관합니다. 실행 환경이 바뀌어도 이 경로가 남아 있다면 기존 정보를 다시 사용할 수 있습니다.

**이 안내는 [v0.8.0](https://github.com/abruption/dots-tailscale/releases/tag/v0.8.0) 기준입니다. 설치 전에 릴리스 페이지에서 발행된 파일을 확인하세요.** 인증서 생성 전에 CONNECT 대상 호스트를 검증하는 v0.7.1 보안 수정을 포함합니다([GHSA-84m8-25vr-f74m](https://github.com/abruption/dots-tailscale/security/advisories/GHSA-84m8-25vr-f74m)). v0.7.0 이하 버전이 영향을 받습니다. 신규 설치의 Tailscale은 1.104.4입니다. OpenAI나 Tailscale의 공식 도구가 아닌 독립 프로젝트입니다.

**v0.6.0 등 이전 기본 버전에서 업데이트한다면:** Tailscale 기본값이 1.102.4인 패키지는 실행 파일을 먼저 갱신한 뒤 [명시적 보안 기본 버전 전환](docs/security-baseline.ko.md)을 거쳐야 합니다. 일반 `update apply`의 기본 버전 변경 제한은 유지됩니다.

## v0.8.0 기능

아래 설치 예시는 **v0.8.0**으로 고정합니다. 자동 업데이트와 복구 실행기는 직접 켜야 하며, 일반 설치는 자동 시작이나 복구 작업을 등록하지 않습니다.

| 기능 | 알아둘 점 |
|---|---|
| 실행 버전과 기준 비교 | `version`과 `doctor`가 설치에 선언된 기준과 각각 관측한 버전을 비교합니다. 연결이 정상이거나 기준과 같다는 이유만으로 안전한 버전이라고 판단하지 않습니다. |
| 패키지 업데이트 출처 검증 | v0.8.0 이상 대상은 릴리스 출처 증명, 미리 준비한 신뢰할 수 있는 `gh`, 별도로 확보한 신뢰 루트가 필요합니다. 서명 없는 이전 버전은 명시적인 수동 검토 예외로만 다루며, 자동 업데이트에는 이 예외를 쓰지 않습니다. |
| 오프라인 묶음 확인 | `update check --bundle`로 검토한 로컬 묶음을 설치 변경 없이 비교할 수 있습니다. 최신 발행 여부나 배포자 신원을 새로 확인하는 명령은 아닙니다. |
| shim 연결 수 제한 | 기본값은 활성 연결 64개이며, 운영자가 직접 관리하는 실행에서는 1–256개로 정할 수 있습니다. 정상적인 control/DERP 연결의 수명을 제한하지는 않습니다. |
| 선택적 업데이트·복구 실행기 | 명시적으로 켜기 전에는 동작하지 않습니다. 별도의 실행 환경이 필요하고, VM이 교체돼 실행기가 사라지면 스스로 다시 켜질 수 없습니다. 패키지 자동 업데이트는 Tailscale 바이너리를 바꾸지 않습니다. |

공통 로컬 복구 원장은 기존 watchdog을 중지하고 내부·외부 실행 주체가 **모두** 새 복구 진입점을 사용할 때만 적용됩니다. 운영 중인 작업이 `start.sh`를 직접 호출한다면 여전히 기존 경로를 사용합니다. MCP 방식은 고급 사용자용 미리보기로 남겨 둡니다. 다른 실제 계정에서 설정과 복구를 재현한 시험은 **아직 완료하지 않았으며**, 일반적인 자동 복구 지원을 뜻하지 않습니다.

활성화하기 전에 [v0.8.0 사용 안내](docs/usage.ko.md#development-v080), [자동 업데이트](docs/automatic-updates.ko.md), [릴리스 출처 검증](docs/release-provenance.ko.md), [내부·외부 복구 조정](docs/recovery-lifecycle.ko.md), [실험 지원 준비 상태](docs/recovery-support-readiness.ko.md)를 확인하세요.

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
  git clone --branch v0.8.0 --depth 1 https://github.com/abruption/dots-tailscale.git
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
/workspace/.dots-tailscale/dots-ts update check --tag v0.8.0 --proxy http://browser-proxy:8889 --json
```

v0.8.0 이전 updater에서 처음 전환할 때는 [독립 검증을 거치는 첫 전환 절차](docs/release-provenance.ko.md)를 따르세요. 후보 도구를 실행하기 전에 원본 manifest와 소스 네 파일의 출처를 확인해야 합니다. 해시 일치만으로 배포자를 인증할 수는 없습니다.

v0.7.0 패키지는 같은 Tailscale 기본 버전을 선언하므로 일반 [패키지 업데이트](docs/usage.ko.md#update-management)로 v0.7.1로 올릴 수 있으며, 적용하면 수정된 shim이 다시 시작됩니다. 기본 Tailscale을 1.102.4로 선언하는 v0.6.0 등 기존 패키지는 [보안 기본 버전 전환](docs/security-baseline.ko.md)에 따라 v0.7.1로 이동하세요. 설치된 도구가 v0.6.0보다 오래됐다면 [패키지 업데이트 안내](docs/usage.ko.md#update-management)로 같은 기본 버전의 v0.6.0 도구를 먼저 준비하세요. 신뢰할 수 있는 메타데이터가 없는 이전 설치는 수동 확인이 필요합니다. 이 이전 버전 절차로 기본 버전을 맞춘 뒤, v0.8.0으로 전환할 때는 위의 독립 검증 절차를 따르세요.

v0.6.0 이상 도구가 설치됐다면 공식 Tailscale 안정 버전을 확인할 수 있습니다.

```bash
/workspace/.dots-tailscale/dots-ts runtime check --json
```

**v0.8.0으로 새로 설치하면 Tailscale은 1.104.4로 시작합니다.** 패키지 업데이트나 `install`이 기존 실행 파일을 조용히 바꾸지는 않습니다. 실행 파일을 갱신하려면 조회된 버전과 공식 공지를 검토한 뒤 [수동 업데이트 가이드](docs/runtime-updates.ko.md)를 따르세요. 기본 버전 전환은 설치된 두 실행 파일과 실행 중인 백엔드가 모두 정확히 1.104.4인 것을 확인한 뒤 소스를 바꿉니다.

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
- [v0.7.0 릴리스](https://github.com/abruption/dots-tailscale/releases/tag/v0.7.0)에는 격리된 신규 설치·소스 전환 테스트가 추가됐습니다. 이후 [2026-10-28 실환경 소스 전환 시험](docs/validation/v0.7.0-migration-trial-20261028.md)에서 기존 설치 한 건의 패키지를 0.6.0에서 0.7.0으로 옮겼습니다. 이미 설치된 Tailscale 1.104.4 실행 환경과 기기·SSH 정보를 유지했고, 전환 후 엄격한 호스트 키 검증을 적용한 새 SSH 연결과 릴레이 ping이 성공했습니다. 신규 설치, 다른 계정, 실환경 장애 주입, 중단된 작업 복구, VM 교체, 실행 상태의 하위 버전 전환이나 앞으로의 모든 버전을 검증한 것은 아닙니다. [공개 전 기본 버전·TLS 점검](docs/validation/security-baseline-20261028.md)은 별도의 과거 기록입니다.
- `/workspace`가 남아 있어야 사용할 수 있으며, 플랫폼이 이 경로의 보존을 보장한다는 뜻은 아닙니다. 실행 중 필요한 소켓·PID·로그는 `/tmp`에 다시 만듭니다. 새 실행 환경에서는 여전히 시작 명령을 실행할 방법이 필요합니다.
- 로컬에서 `Running`이 나와도 외부에서 접속된다는 뜻은 아닙니다. 새 SSH 연결과 실제 사용하는 앱의 접속 경로를 확인하세요. 1.104.2 업데이트로 Tailworm 문제가 해결됐다고 주장하지도 않습니다.

![Dots 터미널에서 설치 상태와 Tailscale 1.102.4를 확인했던 화면](docs/assets/dots-tailscale-ssh-verification.png)

관리자가 제공한 과거의 설치·상태 확인 화면입니다. 현재 패키지 버전이나 VM 교체 후 복구를 보여주는 화면은 아닙니다. 이후 업데이트 시험은 위의 날짜가 명시된 검증 기록에서 확인할 수 있습니다.

## 외부 연결 복구 실험

고급 설정이 필요하다면 [Dots를 통한 외부 복구 실험](docs/experimental-recovery.ko.md)을 살펴보세요. 외부 감시 도구, 인증을 거치는 MCP Events 서버, 사용자가 허용한 Dots 작업으로 연결 장애 후 시작 명령 실행을 요청하는 방식입니다. 일반 설치 과정에서 이 구성을 배포하거나 복구 작업을 켜지는 않습니다.

현재 실험에는 외부 서버와 SSO 설정이 필요합니다. 이벤트 처리가 지연될 수 있고, 다른 계정에서의 설정 재현이나 임의의 VM 교체 후 복구는 아직 검증하지 않았습니다. watchdog과 외부 복구 조정, 선택적 자동 업데이트는 v0.8.0에 구현돼 있지만 v0.7.1이나 일반 설치가 켜주는 기능은 아닙니다. 위의 실험 지원 조건을 함께 확인하세요.

## 문서와 구성 파일

- [사용법과 문제 해결](docs/usage.ko.md) · [Tailscale 수동 업데이트](docs/runtime-updates.ko.md)
- [비공개 HTTPS API 연결 예시](docs/private-http-api.ko.md) · [외부 복구 실험](docs/experimental-recovery.ko.md)
- [릴리스 노트](docs/releases/v0.8.0.md) · [변경 이력](CHANGELOG.md) · [MIT 라이선스](LICENSE)

| 경로 | 역할 |
|---|---|
| `tailscale-setup.sh`, `shim.py` | 설치·시작, 로컬 OpenSSH와 플랫폼 프록시를 통한 Tailscale 통신 |
| `dots-ts`, `dots_proxy.py` | 진단, 패키지·Tailscale 업데이트와 선택적 앱 프록시 설정 |
| `source-manifest.json` | 패키지 버전과 소스 파일 네 개의 체크섬 |
| `home-persistence-probe.py` | HOME과 `/workspace`가 유지되는지 확인할 표식 기록 |
| `tests/`, `research/` | 회귀 테스트와 별도로 배포하는 연구 도구 |
