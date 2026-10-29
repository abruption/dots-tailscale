# Tailscale 보안 기본 버전과 기존 설치 전환

[English](security-baseline.md)

이 문서는 **dots-tailscale v0.7.0 이후 버전**(현재 v0.7.1)과 Tailscale **1.104.4** 설치 기본값을 설명합니다(#57). 이전 v0.6.0 설치 스크립트는 1.102.4를 선언합니다. 해당 기본값의 기존 패키지는 Tailscale 자체를 먼저 갱신한 뒤 아래 명시적 소스 전환 절차를 따라야 합니다.

## 1.104.4를 선택한 근거

2026-10-28 확인한 [공식 stable 정보](https://pkgs.tailscale.com/stable/?mode=json)는 1.104.4를 선택했고, [공식 릴리스](https://github.com/tailscale/tailscale/releases/tag/v1.104.4)는 01:05:26 UTC에 공개됐습니다. 공식 amd64 파일은 `tailscale_1.104.4_amd64.tgz`이며 체크섬 주소가 반환한 SHA-256은 `624ea093d47d62cf56f17b8332d74b8decda4a1faa610ebc60a1de334b2b3dc9`입니다. 이 기록만 믿지 말고 실제로 내려받은 파일의 해시를 대조해야 합니다.

2026-10-21 발표된 [TS-2026-013](https://tailscale.com/security-bulletins)은 1.104.2 이상으로 업데이트할 것을 권합니다. 해당 버전의 Go 1.27.3은 유출된 eMudhra 중간 인증서 8개를 명시적으로 신뢰하지 않습니다. 현재 stable인 1.104.4는 이 공지의 최소 버전 조건을 충족합니다. 이 문서에서 별도의 CVE 번호를 주장하지는 않습니다.

검토 시점에는 [변경 이력](https://tailscale.com/changelog)에 1.104.4의 상세 항목이 없었고, [SSH 조사 업데이트](https://tailscale.com/ssh-investigation-update)도 모든 문제에 대한 확정 수정 버전 범위를 제시하지 않았습니다. 태그가 공개됐다는 사실만으로 SSH·Tailworm 보고 항목이 모두 해결됐다고 볼 수는 없습니다. 설치 스크립트는 `--ssh=false`와 로컬 OpenSSH를 사용하지만, 기존 노드의 현재 설정이나 다른 공격 경로까지 확인한 것은 아닙니다.

## 따로 확인할 네 가지

| 항목 | 의미 |
|---|---|
| 패키지 버전 | dots-tailscale 스크립트와 원본 manifest의 버전입니다. 설치된 v0.6.0과 v0.7.1 릴리스를 구분합니다. |
| 설치 기본 버전 선언 | 패키지의 `TS_VERSION`입니다. v0.6.0은 1.102.4, v0.7.0과 v0.7.1은 1.104.4입니다. |
| 설치·실행 중인 Tailscale | 실제 CLI, 데몬 파일, 실행 중인 백엔드의 버전입니다. 패키지의 선언만 바꿔도 이 버전들이 갱신되지는 않습니다. |
| 호스트 TLS 신뢰 설정 | shim 주변의 OS·Python·OpenSSL 검증기와 저장된 CA 묶음입니다. Tailscale의 Go 런타임과 별개입니다. |

v0.7.0 이후 버전으로 새로 설치할 때는 파일 체크섬, 제한된 압축 파일 구조, 두 실행 파일의 버전 출력을 확인한 뒤 배치합니다. `install`은 기존 실행 파일을 조용히 바꾸지 않습니다. 한쪽만 있거나 버전이 다르거나 기본 버전보다 낮으면 거부합니다. 소스 파일 네 개와 해당 버전의 원본 manifest는 항상 함께 두세요.

## 기존 설치: Tailscale을 먼저, 패키지는 나중에

이번 절차가 지원하는 범위는 좁습니다. 기본값을 **1.102.4**로 선언한 신뢰 가능한 기존 패키지에서, **1.104.4**를 선언한 완전한 v0.7.1 소스로 전환하며, 실제 설치·실행 중인 Tailscale도 이미 **1.104.4**여야 합니다. 다른 선언 전환이나 다른 실행 버전은 별도의 검토가 필요합니다. 이 예시에 맞추려고 더 최신인 노드를 다운그레이드하지 마세요.

1. Tailscale SSH 없이도 사용할 수 있는 Dots 클라우드 터미널을 열고 직접 제어되는지 확인하세요. 외부 접속 검증까지 열어 두고, 두 업데이트와 충돌할 수 있는 외부 복구·업데이트 작업을 잠시 중지하세요.
2. 도구가 v0.6.0보다 오래됐다면 먼저 [패키지 업데이트 안내](usage.ko.md#update-management)로 같은 1.102.4 기본값의 공개 v0.6.0 도구를 설치하세요. v0.7.1을 바로 적용하지 마세요. 이후 **설치된 v0.6.0 도구**로 무결성과 실행 버전을 확인한 뒤 필요하면 Tailscale을 갱신하세요. 이 실행 파일 갱신 단계에서는 새 릴리스 checkout의 CLI를 기존 설치에 대고 실행하면 안 됩니다.


신뢰 가능한 v0.4.0 이상 패키지에서 도구가 v0.6.0보다 오래됐다면, 다음 준비 단계는 같은 기본 버전 선언을 유지합니다. 작업이 완료됐는지 확인한 뒤 진행하세요. 신뢰할 수 있는 메타데이터가 없는 설치는 먼저 출처를 수동으로 확인해야 합니다.

```bash
/workspace/.dots-tailscale/dots-ts update apply --tag v0.6.0 \
  --proxy http://browser-proxy:8889 --confirm-recovery-terminal --json
/workspace/.dots-tailscale/dots-ts update status --id BOOTSTRAP_TRANSACTION_ID --json
```
```bash
/workspace/.dots-tailscale/dots-ts version --json
/workspace/.dots-tailscale/dots-ts runtime check --json
/workspace/.dots-tailscale/dots-ts runtime apply \
  --version 1.104.4 --proxy platform --confirm-recovery-terminal --json
/workspace/.dots-tailscale/dots-ts runtime status --id RUNTIME_TRANSACTION_ID --json
```

두 설치 파일과 실행 중인 백엔드가 이미 1.104.4라면 `runtime apply`는 생략하세요. 같은 버전의 재설치는 거부합니다. 온라인 적용은 요청한 버전이 현재 공식 stable 정보와 같아야 합니다. stable이 더 올라갔다면 정확히 지원하는 버전의 [검토된 오프라인 묶음](runtime-updates.ko.md#외부-다운로드가-막힐-때)을 사용하거나 전환 범위가 확대될 때까지 기다리세요. 메타데이터를 속여서 맞추면 안 됩니다.

3. 실행 파일 업데이트가 완료됐고 미해결 차단 표식이나 정리 경고가 없는지 확인하세요. 다른 기기에서 **엄격한 호스트 키 검증을 유지한 새 SSH 연결**과 Tailscale ping을 시험하고, 필요한 앱의 접속도 확인하세요. 로컬 `Running`이나 journal의 `external_ssh=pending`은 외부 연결 성공을 뜻하지 않습니다.
4. 검토한 v0.7.1 소스 전체를 설치 경로 밖의 `/workspace/reviewed-v0.7.1`에 준비하세요. 원본 `source-manifest.json`을 보존하고 출처와 해시를 확인하세요. `TS_VERSION`을 바꾸거나, 섞인 소스를 맞추려고 manifest를 다시 만들거나, 새 CLI만 기존 설치에 복사하면 안 됩니다.
5. 릴리스 checkout에 들어 있는 명시적 소스 전환 명령을 실행하세요.

```bash
DOTS_TS_DIR=/workspace/.dots-tailscale \
  python3 /workspace/reviewed-v0.7.1/dots-ts update migrate-baseline \
  --bundle /workspace/reviewed-v0.7.1 \
  --confirm-recovery-terminal --json
DOTS_TS_DIR=/workspace/.dots-tailscale \
  python3 /workspace/reviewed-v0.7.1/dots-ts update status \
  --id PACKAGE_TRANSACTION_ID --json
```

`--bundle`은 가져올 소스 묶음을 선택하고, `DOTS_TS_DIR`은 갱신하거나 상태를 조회할 **기존 설치 경로**를 지정합니다. 새 소스의 CLI는 별도 지정이 없으면 자신의 디렉터리를 대상으로 삼으므로, 위 두 명령에는 설치 경로를 명시해야 합니다. 다른 경로에 설치했다면 두 명령의 `/workspace/.dots-tailscale`을 실제 설치 경로로 바꾸세요. `/workspace/reviewed-v0.7.1`은 검토한 소스 디렉터리로 따로 유지합니다.

일반 `update apply`의 기본 버전 선언 비교는 그대로 남으며, 이처럼 기본값이 바뀌는 업데이트는 `binary_update_unsupported`로 거부합니다. `migrate-baseline`은 별도로 선택하는 제한된 작업입니다. 실행한 릴리스 checkout과 선택한 묶음이 같고 기존 패키지의 무결성이 확인돼야 합니다. 지원하는 선언 전환, CLI·데몬·백엔드 버전 일치, 예상한 userspace·소켓·프록시 구성, 미해결 패키지·실행 파일 작업과 차단 표식의 부재도 확인합니다. 고정된 worker가 공통 작업 잠금을 잡은 뒤 소스를 바꾸기 전에 조건을 다시 검사합니다.

소스 작업은 실행 소스 네 개와 manifest만 백업·교체한 뒤 shim 재시작을 확인합니다. Tailscale 실행 파일, 기기 상태, SSH 키는 바꾸지 않습니다. 소스 전환에 실패하면 코드 백업으로 원복할 수 있지만, 실행 파일의 호환성 확인 대신 예전 Tailscale 상태나 바이너리를 되돌려서는 안 됩니다.

6. 패키지 manifest와 실행 버전을 다시 확인하고, 소스 전환 후에도 새 외부 SSH·ping을 검증하세요. 이 확인을 마친 뒤에만 외부 작업을 재개하세요. 출력이 사라졌다면 독립 터미널에서 기존 작업 기록을 확인하고, 전환 명령을 무작정 다시 실행하지 마세요.

릴리스 공개 시점에는 새 소스 전환을 격리 환경에서만 검증했습니다. 이후 [2026-10-28 실환경 소스 전환](validation/v0.7.0-migration-trial-20261028.md)에서 설치 대상을 명시해 기존 패키지 하나를 0.6.0에서 0.7.0으로 옮겼고, 이미 설치된 1.104.4 실행 파일과 기기·SSH 정보를 유지했습니다. v0.7.1로 바로 전환하는 경로도 같은 선언 전환과 버전에 무관한 격리 테스트를 거치지만, 실환경 시험은 따로 하지 않았습니다. 신규 설치, 장애 주입이나 앞으로의 모든 버전을 검증한 것은 아닙니다. [2026-10-26 런타임 시험](validation/runtime-update-trial-20261026.md)은 별도의 과거 기록입니다.

## 실행 파일 실패와 소스 원복은 다릅니다

후보 데몬을 실행하기 전의 실패라면 실행 파일 업데이터가 검증된 기존 쌍으로 원복을 시도할 수 있습니다. 후보 데몬의 실행을 시도한 뒤에는 시작에 실패했더라도 상태 호환성이 달라졌을 수 있어 자동 다운그레이드를 안전하다고 가정하지 않습니다. 독립 터미널과 작업 기록·차단 표식을 유지하고 [실행 파일 복구 절차](runtime-updates.ko.md#수동-복구-절차)를 따르세요. 소스 패키지의 원복이 Tailscale 다운그레이드나 인증 상태 복원을 허용하는 것은 아닙니다.

## 신뢰 설정을 바꾸지 않고 호스트 TLS 경로 확인하기

shim은 로컬 TLS를 끝내고, 업스트림 연결을 Python의 `ssl.create_default_context()`로 따로 검증합니다. Go의 eMudhra 불신 설정이 Python·OpenSSL 검증기나 호스트 CA 저장소에도 적용됐다고 볼 수는 없습니다. 상속된 `SSL_CERT_FILE`·`SSL_CERT_DIR`의 영향도 받을 수 있습니다. 아래 명령은 재정의 변수의 값이 아닌 존재 여부만 출력합니다. 결과는 터미널의 Python 환경을 설명하며, 실행 중인 shim의 환경과 다를 수 있습니다.

```bash
python3 - <<'PY'
import hashlib, json, os, pathlib, ssl
paths = ssl.get_default_verify_paths()
report = {
    'openssl_version': ssl.OPENSSL_VERSION,
    'compiled_default_cafile': paths.openssl_cafile,
    'compiled_default_capath': paths.openssl_capath,
    'effective_cafile_present': bool(paths.cafile and pathlib.Path(paths.cafile).is_file()),
    'effective_capath_present': bool(paths.capath and pathlib.Path(paths.capath).is_dir()),
    'SSL_CERT_FILE_override_present': 'SSL_CERT_FILE' in os.environ,
    'SSL_CERT_DIR_override_present': 'SSL_CERT_DIR' in os.environ,
}
for label, filename in (
    ('os_bundle', '/etc/ssl/certs/ca-certificates.crt'),
    ('saved_bundle', '/workspace/.dots-tailscale/shim/bundle.crt'),
):
    path = pathlib.Path(filename)
    report[label] = {'present': path.is_file()}
    if path.is_file():
        raw = path.read_bytes()
        report[label].update(sha256=hashlib.sha256(raw).hexdigest(),
                             certificate_count=raw.count(b'-----BEGIN CERTIFICATE-----'))
print(json.dumps(report, indent=2))
PY
```

저장된 묶음은 처음에 OS CA 묶음과 shim의 로컬 CA를 합쳐 만들므로 전체 파일 해시는 보통 다릅니다. 오래된 복사본인지 조사하려면 로컬 CA를 구분한 뒤 **공개 인증서 집합**을 비교해야 합니다. 설치 시 완전한 기존 묶음은 보존하며, OS 신뢰 저장소가 갱신돼도 이 복사본이 자동으로 바뀌지는 않습니다. 해시·인증서 개수나 문제의 중간 인증서가 없다는 사실만으로 해당 검증기가 공격 인증서 체인을 거부한다고 증명할 수는 없습니다.

[날짜가 명시된 Dots 읽기 전용 검토](validation/security-baseline-20261028.md)에서는 실행 중인 shim의 Python 검증기가 OpenSSL 3.5.7을 사용하고 `SSL_CERT_FILE`·`SSL_CERT_DIR` 재정의가 없다고 기록했습니다. 저장된 묶음은 현재 OS 묶음과 다르고 더 오래된 복사본이었습니다. 이는 저장된 묶음이 오래됐다는 근거이며, 침해나 완전한 인증서 불신 정책의 증거는 아닙니다. shim의 기본 업스트림 검증 경로와 Tailscale이 사용하는 로컬 CA 포함 묶음은 구분해야 합니다.

결과 해석에는 [Python SSL 문서](https://docs.python.org/3/library/ssl.html#ssl.create_default_context)와 [기본 검증 경로 설명](https://docs.python.org/3/library/ssl.html#ssl.get_default_verify_paths)을 참고하세요. 실행 중인 shim이 실제로 사용하는 검증 환경도 별도로 확인하되 원본 환경 변수 값은 공개하지 마세요. 이 검토는 CA 파일 재생성, 키 교체, 인증서 확인 해제, 호스트 신뢰 정책 변경이나 공격 체인 거부 시험의 성공을 뜻하지 않습니다.

읽기 전용 점검은 실행 중인 shim과 같은 인터프리터·CA 환경으로 새 검증 문맥을 만든 결과입니다. 기존 shim 문맥에 이미 올라간 인증서 목록을 직접 조회한 것은 아닙니다.

새 설치의 기존 바이너리 검사는 기본값 이상의 일치하는 버전을 허용하지만, 이 전환 작업은 정확히 1.104.4만 지원합니다. 소스 원복 시에는 이전 패키지의 1.102.4 선언으로 돌아가고 실제 바이너리는 1.104.4로 남습니다. 이는 전환 직전 상태이며 런타임 다운그레이드가 아닙니다.
