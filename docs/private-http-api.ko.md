# Dots에서 비공개 HTTP API에 접속하기

[English](private-http-api.md) · 한국어 · [简体中文](private-http-api.zh-CN.md) · [日本語](private-http-api.ja.md) · [사용 가이드](usage.ko.md#development-helpers)

다른 tailnet 장비의 HTTPS API를 Dots 터미널에서 선택적 로컬 Tailscale 프록시로 호출하는 예시입니다. 필요한 도구는 v0.3.0부터 제공됩니다. v0.3.0 이상 버전의 helper 파일을 모두 설치하세요. v0.2.0 이하에는 해당 도구가 없습니다.

## 연결 경로와 준비 사항

```text
Dots curl 프로세스 -> 127.0.0.1:8080 HTTP CONNECT 프록시
                  -> Tailscale -> 비공개 API 장비:8443 -> HTTPS API
```

SOCKS5를 쓰려면 `socks5h://127.0.0.1:1080`을 사용할 수 있습니다. `h`는 목적지 이름 확인을 프록시에 맡깁니다. HTTP CONNECT도 목적지 호스트명을 프록시에 전달합니다. `18089`번 데몬 shim으로 바꾸면 안 됩니다. 이 shim은 Tailscale 자체가 Dots 플랫폼 프록시를 통과하기 위한 것이며 앱 요청용이 아닙니다.

다음 항목을 별도로 준비하세요.

- 대상 tailnet에 인증된 Dots Tailscale 노드와 v0.3.0 이상 helper 파일 전체.
- tailnet에서 접근할 API 장비와 원하는 비공개/Tailscale 인터페이스·포트에서 수신하는 HTTPS 서버. API 장비의 `127.0.0.1`에서만 수신하면 Dots에서 접속할 수 없습니다. 편의상 공개 인터페이스에 노출하지 마세요.
- 선택한 프록시를 통해 확인할 수 있고 API 인증서와 일치하는 목적지 이름. 신뢰하는 공개 CA를 사용하거나 클라이언트에 사설 CA 파일을 명시하세요. IP로 호출해도 인증서에 해당 IP가 포함돼야 합니다.
- 필요한 출발 장비·도착 장비·TCP 포트만 허용하는 tailnet 정책과 서버의 bearer 인증·API 권한 검사. Tailscale 연결이 API 인증을 대신하지는 않습니다.
- 인증이 필요한 읽기 전용 `/health` 경로. 의도적으로 `{"status":"ok"}` 같은 민감한 정보가 포함되지 않은 짧은 응답만 반환하게 만드세요. 예시 주소는 실제 서버의 상태 확인 경로로 바꾸며 데이터 내보내기·변경 경로를 사용하지 않습니다.

## 네트워크 접근 범위 제한

아래 JSON은 유효한 JSON5이기도 하므로 Tailscale 정책 형식으로 사용할 수 있습니다. 출발 tag·목적지 tag·허용할 TCP 포트 하나를 보여주는 예시이며 이름과 관리자 소유권은 실제 정책에 맞게 바꿔야 합니다. 전체 정책 파일을 교체하지 말고 기존 정책의 해당 항목에 합치세요.

```json
{
  "tagOwners": {
    "tag:dots-client": ["autogroup:admin"],
    "tag:private-api": ["autogroup:admin"]
  },
  "grants": [
    {
      "src": ["tag:dots-client"],
      "dst": ["tag:private-api"],
      "ip": ["tcp:8443"]
    }
  ]
}
```

권한 있는 tailnet 관리자가 의도한 Dots 장비에 출발 tag를, API 장비에 목적지 tag를 지정해야 합니다. 이 스크립트는 tag를 붙이거나 정책을 수정하지 않습니다. Grant는 허용을 더하는 방식이므로 좁은 규칙을 추가해도 기존의 넓은 grant·ACL 허용은 취소되지 않습니다. 접근이 제한됐다고 판단하기 전에 기존 항목·tag 소유권·API 장비 방화벽을 함께 검토하세요. 실제 노드 신원으로 tailnet 정책 편집기의 검증과 정책 테스트를 사용하세요. 공식 [grants 문서](https://tailscale.com/kb/1324/grants)를 참고할 수 있습니다.

## 로컬 listener는 명시적으로 활성화

먼저 [설정 저장과 적용 절차](usage.ko.md#development-helpers)를 읽으세요. 프록시는 기본적으로 꺼져 있습니다. 설정 명령은 원하는 상태만 저장하며 실행 중인 데몬을 재시작하지 않습니다.

```bash
/workspace/.dots-tailscale/dots-ts proxy show
/workspace/.dots-tailscale/dots-ts proxy configure --socks-port 1080 --http-port 8080
```

포트는 서로 다르게 지정하세요. 이번 구현은 같은 SOCKS5·HTTP 포트를 처음에는 거부합니다. 설정은 `/workspace/.dots-tailscale/outbound-proxy.json`에 남으며 환경 교체 후 실행 listener를 다시 만들어야 합니다. 적용은 별도의 보호 절차가 있는 데몬 재시작이며 SSH가 끊길 수 있습니다. `dots-ts proxy apply` 안내를 따르기 전에 독립적인 Dots GUI 터미널과 복구 방법을 확보하세요. 적용 후 외부에서 엄격한 키 검증을 유지한 SSH도 다시 확인합니다.

이는 로컬 프로세스용 loopback listener이며 다른 장비를 위한 인증된 게이트웨이가 아닙니다. listener에 접근할 수 있는 프로세스는 연결을 시도할 수 있습니다. 자신의 스크립트만 사용해도 API 인증과 좁은 tailnet 권한을 유지하세요.

## 제한 시간 있는 인증 요청 한 번 보내기

**curl 8.4.0 이상이 필요합니다.** 이 버전부터 `--max-filesize`가 크기를 미리 알 수 없는 응답과 chunked 응답도 제한합니다. 예제는 토큰 입력과 API 요청 전에 버전을 확인하며, 확인할 수 없거나 구버전이면 중단합니다.

HTTP listener를 적용한 뒤 Dots 터미널에서 실행합니다. URL은 실제 HTTPS 서비스 이름과 포트로 바꾸세요. Python 표준 라이브러리로 bearer token을 화면에 표시하지 않고 입력받아 curl의 표준 입력으로 헤더를 전달합니다. token은 명령행 인자·URL·셸 기록·환경변수·파일에 넣지 않습니다. 요청이 끝날 때까지 프로세스 메모리에는 남습니다. HTTP 추적을 켜거나 token을 출력하지 마세요.

```bash
python3 - <<'PY'
import getpass
import json
import os
import re
import subprocess

url = "https://api.example.internal:8443/health"
proxy = "http://127.0.0.1:8080"
ca_file = ""  # Optional trusted private CA file path; blank uses system trust.
child_env = os.environ.copy()
for name in ("NO_PROXY", "no_proxy", "CURL_CA_BUNDLE", "SSL_CERT_FILE", "SSL_CERT_DIR"):
    child_env.pop(name, None)
try:
    version_result = subprocess.run(
        ["curl", "--disable", "--version"],
        text=True, capture_output=True, timeout=5, env=child_env,
    )
except (OSError, subprocess.TimeoutExpired, UnicodeError):
    raise SystemExit("Could not verify curl version; curl 8.4.0 or newer is required")
version_match = re.match(r"curl ([0-9]{1,5})\.([0-9]{1,5})\.([0-9]{1,5})(?:\s|$)", version_result.stdout)
if (version_result.returncode != 0 or version_match is None
        or tuple(map(int, version_match.groups())) < (8, 4, 0)):
    raise SystemExit("curl 8.4.0 or newer is required for the response size limit")
token = getpass.getpass("API bearer token: ")
if not token or any(c in token for c in "\r\n"):
    raise SystemExit("Invalid token input")
command = [
    "curl", "--disable", "--silent", "--show-error", "--proto", "=https",
    "--proxy", proxy, "--noproxy", "",
    "--connect-timeout", "5", "--max-time", "20", "--max-filesize", "4096",
    "--header", "@-", "--write-out", "\n%{http_code}", url,
]
if ca_file:
    command.extend(["--cacert", ca_file])
try:
    result = subprocess.run(
        command, input="Authorization: Bearer " + token + "\n",
        text=True, capture_output=True, timeout=25, env=child_env,
    )
except subprocess.TimeoutExpired:
    raise SystemExit("Request timed out")
except (OSError, UnicodeError):
    raise SystemExit("Could not execute curl or decode its response")
finally:
    token = None
if result.returncode:
    raise SystemExit("curl failed (exit %d); check network, DNS and TLS separately" % result.returncode)
body, separator, status = result.stdout.rpartition("\n")
if not separator:
    raise SystemExit("Missing HTTP status")
print("HTTP", status)
if status != "200":
    raise SystemExit("Unexpected HTTP status; response body omitted")
try:
    healthy = json.loads(body) == {"status": "ok"}
except ValueError:
    healthy = False
if not healthy:
    raise SystemExit("Unexpected health response; body omitted")
print("Expected read-only health response received")
PY
```

리디렉션을 따라가지 않습니다. 자격증명을 다른 주소로 넘기기보다 정확한 최종 경로를 확인하세요. HTTPS 인증서·호스트명 검증은 유지합니다. `ca_file`이 비어 있으면 시스템 신뢰 저장소를 사용하고, 사설 CA가 필요하면 검토한 CA 파일 경로를 넣습니다. 응답은 4 KiB로 제한하고 HTTP 상태와 예상 형식 여부만 표시합니다. 예시 서버는 민감한 정보가 포함되지 않은 응답만 반환하도록 구성합니다. 개인 기록이 포함된 응답에 이 확인 명령을 사용하지 마세요.

SOCKS5를 쓰려면 해당 listener를 활성화한 뒤 `proxy`만 `socks5h://127.0.0.1:1080`으로 바꾸세요. `--noproxy ''`는 curl의 우회 목록 때문에 프록시를 건너뛰는 것을 막습니다. `NO_PROXY`·`no_proxy`와 상속된 CA 설정인 `CURL_CA_BUNDLE`·`SSL_CERT_FILE`·`SSL_CERT_DIR`는 curl 자식 프로세스에서만 제거합니다. 셸이나 다른 앱에는 영향이 없습니다. 사설 CA는 검토한 `ca_file`을 `--cacert`로 명시적으로 전달합니다. 시스템 프록시·브라우저·MCP·ChatGPT 플러그인/커넥터 설정은 바꾸지 않습니다. 다른 클라이언트는 해당 프록시를 직접 지원하고 설정해야 하며 임의의 연동 서비스가 자동으로 이 API에 접근할 수 있는 것은 아닙니다.

## 계층별 확인과 거부 사례

`dots-ts doctor`는 로컬 계층을 진단합니다. 선택적인 `doctor --target api.example.internal:8443`는 시간 제한 있는 TCP 검사이며 인증된 HTTPS 검사는 아닙니다. TCP 성공만으로 인증서 유효성이나 token 권한을 확인할 수 없습니다.

| 확인 항목 | 기대 결과와 해석 |
| --- | --- |
| 로컬 설치·소켓·데몬·프록시 설정 | `doctor`와 `proxy show`의 계층별 결과에서 저장한 설정과 실제 실행 상태를 구분 |
| 허용된 출발 장비에서 의도한 호스트·포트 | TCP에 연결되고 인증된 읽기 전용 상태 확인이 HTTP 200과 사전에 정한 민감 정보 없는 응답을 반환 |
| 누락되거나 의도적으로 잘못된 API token | 일반적으로 HTTP 401 거부. 서비스에 따라 403 사용. 민감한 본문은 반환하면 안 됨 |
| 정책상 허용하지 않은 장비나 목적지 포트 | API 응답 전에 연결 거부·시간 초과. 정책 테스트와 기존의 넓은 허용 유무도 확인 |
| 신뢰하지 않는 CA·호스트명 불일치 | TLS 검증 실패. 검증을 끄지 말고 인증서·신뢰 설정 수정 |
| 프록시 경로에서 목적지 이름 확인 실패 | DNS·프록시 오류. tailnet 권한·API 인증과 이름 확인을 구분해 조사 |
| 프록시 비활성화 후 적용 | 로컬 listener 연결 불가. `disable`만으로는 실제 listener 중단을 증명하지 않음 |

HTTP 401은 HTTP 서버가 응답했다는 뜻이며 네트워크 정책 거부가 아닙니다. 시간 초과만으로 ACL·방화벽·경로·DNS 중 원인을 단정할 수 없습니다. 필요한 최소 검사부터 진행하고 token이나 비공개 응답 본문을 기록하지 마세요.

<a id="current-validation"></a>

## 검증 상태

2026-10-18(KST) 실환경에서 SOCKS5·HTTP 프록시의 실제 통신, 두 listener 비활성화, 엄격한 호스트 키 검증을 유지한 SSH 재접속을 확인했습니다. 두 프록시에서 정상 인증(`200`), 토큰 누락·오류(`401`), 권한 없는 경로(`403`), 신뢰하지 않는 CA(TLS 실패)의 HTTPS 검사 10개가 모두 통과했습니다. 별도의 허용→차단→복원 비교로 tailnet 정책 차단도 확인했으며 원래 정책으로 복구했습니다. 임시 API 서버와 인증정보는 정리했습니다. [프록시 결과](https://github.com/abruption/dots-tailscale/pull/24#issuecomment-6243157252), [정책 결과](https://github.com/abruption/dots-tailscale/pull/24#issuecomment-6243247818), [HTTPS 결과](https://github.com/abruption/dots-tailscale/pull/24#issuecomment-6243275452)를 참고하세요.

이 검증은 자연스러운 VM 교체 후 복구나 자동 시작까지 확인한 것은 아닙니다. 비교한 환경에서 Dots 직접 Bash는 `/workspace`를 읽기 전용으로 보았고 실행 소켓도 보지 못했습니다. GUI 터미널과 SSH는 쓰기 가능한 파일과 실행 중인 소켓을 공유했습니다. 프록시 변경은 독립적인 GUI 터미널에서 적용하고 직접 에이전트 실행으로 서비스를 재시작할 수 있다고 가정하지 마세요. 실행 환경과 복구에 관한 추가 조사는 [#20](https://github.com/abruption/dots-tailscale/issues/20#issuecomment-6243404537)에서 계속합니다.
