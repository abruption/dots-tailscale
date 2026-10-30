# 릴리스 출처 검증과 업데이트의 신뢰 범위

[English](release-provenance.md)

이 문서는 v0.8.0부터 적용하는 출처 검증 정책을 설명합니다. 배포 파일은 릴리스 페이지에서 확인하세요. 패키지를 설치해도 자동 업데이트가 켜지거나 복구 작업이 등록되지는 않습니다.

패키지 **v0.8.0부터** 업데이트 대상의 원본 `source-manifest.json`에 대한 GitHub Actions artifact attestation을 요구합니다. 이 명세가 실행 소스 네 개의 SHA-256을 연결합니다. 검증은 문법 검사, 트랜잭션 준비, 코드 교체, 후보 실행 전에 수행합니다. 분리된 worker도 준비된 명세가 검증 기록에 남은 것과 같은지 확인합니다. 이 정책은 설치 절차, 노드 state·SSH 키 관리, 별도 Tailscale 바이너리 업데이트 방식에는 영향을 주지 않습니다.

## 무엇을 신뢰하나요?

이미 신뢰하는 설치와 **미리 설치한 GitHub CLI 2.103.3 이상**을 사용합니다. 검증기를 다운로드하거나 후보 묶음에 들어 있는 검증기를 실행하지 않습니다. 별도로 얻은 Sigstore 신뢰 루트 파일을 `--provenance-root`로 지정해야 합니다. 후보나 설치 디렉터리에서 가져온 신뢰 루트·검증기는 거부합니다. 독립적인 루트 파일은 해당 디렉터리 밖에 두고 운영자 또는 root가 소유하며 그룹·다른 사용자에게 쓰기 권한이 없어야 합니다.

실제 경로의 부모 디렉터리도 운영자 또는 root가 소유하고 그룹·다른 사용자가 쓸 수 없어야 합니다. 신뢰하는 소유자의 sticky 디렉터리는 바로 아래 항목도 운영자 또는 root 소유일 때만 허용합니다. `/tmp` 아래의 비공개 디렉터리가 이런 경우입니다. 신뢰 루트는 실제로 연 파일 디스크립터의 정보를 확인하고 같은 디스크립터에서 한 번 읽습니다. 검증기는 부모 경로까지 확인한 실제 경로로 실행합니다. 이는 다른 UID의 경로 바꿔치기를 막는 검사이며, 신뢰 범위 안에 있는 운영자나 root의 악의적인 변경까지 막지는 않습니다.

일부 macOS Homebrew·외장 볼륨 경로는 부모 디렉터리에 그룹 쓰기 권한이 있어, 마지막 파일이 읽기 전용이어도 이 검사를 통과하지 못합니다. 경로를 신뢰할 조건이 맞지 않는다는 뜻이지 바이너리가 악성이라는 판정은 아닙니다. 패키지 적용과 자동 실행은 원래도 Linux와 pidfd 지원이 필요합니다. 별도로 검토하는 첫 전환에서는 운영자가 독립적으로 신뢰하는 `gh`를 직접 쓰거나, 검증한 도구와 루트를 보호된 경로에 준비할 수 있습니다. updater가 호스트 권한을 바꾸거나 쓰기 가능한 관리자 그룹을 암묵적으로 신뢰해 검사를 통과시키지는 않습니다.

현재 신뢰하는 updater 안에 다음 정책이 고정돼 있습니다.

- 저장소 `abruption/dots-tailscale`, 숫자 저장소 ID `1418377192`
- 소유자 `abruption`, 숫자 소유자 ID `23277516`
- 해당 저장소의 `.github/workflows/release-provenance.yml` 워크플로
- 후보 명세의 버전과 정확히 일치하는 안정 태그 `refs/tags/vX.Y.Z`
- 인증서와 출처 진술에 연결된 소스 커밋
- GitHub Actions의 OIDC issuer와 GitHub가 호스팅하는 runner
- SLSA provenance v1과 원본 명세의 정확한 SHA-256

인증서의 인증된 필드와 검증된 출처 진술에 담긴 워크플로·ref·소스 의존성이 모두 일치해야 합니다. 별도로 전달할 수 있는 `release-provenance.json`은 이 확인을 통과하기 전까지 **신뢰할 수 없는 선택용 메타데이터**일 뿐입니다. 서명 확인 없이 DSSE payload를 풀어 읽은 주장이나, 저장된 “검증 성공” 텍스트를 신뢰하지 않습니다.

검증기는 비공개 임시 GitHub 설정 디렉터리와 home에서, 최소한의 고정 환경만 받아 실행합니다. 기존 자격증명·프록시·로더 설정을 넘기거나 대화형 입력을 받지 않습니다. 검증은 30초, stdout은 2 MiB로 제한하고 원시 오류를 사용자 진단에 복사하지 않습니다. 검증기 버전 조회는 별도로 5초·4 KiB로 제한합니다.

임시 작업 경로도 검증기를 실행하기 전에 같은 부모 경로 검사를 통과해야 합니다. 쓰기 가능한 공유 부모 아래에 `TMPDIR`을 지정했다면 마지막 디렉터리만 0700으로 만드는 것으로는 충분하지 않습니다. 안전하지 않은 작업 경로에서는 검증기를 실행하지 않고 중단합니다.

신뢰 루트 파일은 비공개 임시 검증 경로로 복사합니다. `--bundle`과 `--custom-trusted-root`를 쓰면 `gh`가 attestation을 받거나 TUF 메타데이터를 갱신하지 않고 제공된 증거를 검증합니다. 독립적으로 신뢰하는 장비에 설치된 `gh attestation trusted-root`로 루트를 확보·갱신하고, 출처를 확인해 따로 전송하세요. 릴리스 묶음에 `trusted-root.jsonl`이 들어 있다는 사실만으로 그 파일을 신뢰할 수는 없습니다.

이 정책이 이미 침해된 로컬 운영자·root, 악성 검증기, 바뀐 신뢰 루트, 허용된 attestation을 만들 권한이 있는 관리자·워크플로·계정의 침해까지 막는 것은 아닙니다. 출처 진술의 predicate는 워크플로가 제어하므로 소스·ref를 OIDC 인증서와 함께 묶어 확인합니다. 그렇다고 워크플로 자체가 침해될 수 없다는 뜻은 아닙니다. 브랜치·태그·워크플로 보호와 독립 리뷰도 계속 필요합니다.

숫자 저장소·소유자 ID를 고정하면 오프라인에서도 이름 재사용을 거부할 수 있지만, 현재 소유권이나 철회 상태를 새로 조회하는 것은 아닙니다. 최신 릴리스인지, 서명 이후 태그가 다른 커밋으로 옮겨졌는지까지 확인하지는 않습니다. 선택한 버전·ref는 일치해야 하며 자동 업데이트는 별도로 prerelease와 downgrade를 제외해야 합니다. 유효한 과거 attestation 하나가 곧 설치 허가는 아닙니다.

## 수동 업데이트와 자동 업데이트

출처 증명이 있는 릴리스에는 별도로 준비한 루트를 지정합니다. 아래 v0.8.0 명령은 **해당 정식 릴리스가 발행된 뒤**의 예시입니다.

```bash
/workspace/.dots-tailscale/dots-ts update apply --tag v0.8.0 \
  --proxy http://browser-proxy:8889 \
  --provenance-root /workspace/trusted-tools/dots-ts/trusted-root.jsonl \
  --confirm-recovery-terminal --json
```

검토한 오프라인 소스 묶음에는 원본 소스 네 개, 원본 명세, `release-provenance.json`, `source-manifest.sigstore.json`이 있어야 합니다. `update apply --bundle /path/to/bundle`에도 같은 `--provenance-root`를 지정합니다. 로컬 묶음이면 GitHub 릴리스·API 요청을 하지 않지만, 독립적인 로컬 `gh`와 신뢰 루트는 필요합니다. 증거가 없거나 검증에 실패하거나, 워크플로·ref·커밋이 틀리거나, 검증기를 쓸 수 없으면 중단합니다. 해시만 확인하는 성공으로 되돌아가지 않으며, 임의의 `--repo`로 바꾸는 옵션도 없습니다.

서명 없는 **v0.8.0 미만** 대상에는 명시적인 수동 검토가 필요합니다.

```bash
/workspace/.dots-tailscale/dots-ts update apply --tag v0.7.1 \
  --proxy http://browser-proxy:8889 --allow-unsigned-legacy \
  --confirm-recovery-terminal --json
```

이 예외는 운영자가 소스 출처와 바이트를 따로 검토했다는 뜻입니다. `legacy_operator_review`로 남기며, **인증된 출처 검증으로 기록하지 않습니다**. 서명 없는 v0.8.0 이상 후보에는 적용할 수 없고 자동 업데이트 호출자는 이 예외를 사용하면 안 됩니다. `update migrate-baseline`도 같은 명시적 예외·루트 선택을 받으며 기존 복구와 바이너리 기준 검사는 유지합니다. 읽기 전용 업데이트 조회는 출처를 인증하지 않고 메타데이터를 비교할 수 있지만, 그것이 실행·설치 허가는 아닙니다.

이미 설치된 v0.8.0 이전 updater는 새 정책을 강제할 수 없습니다. 따라서 첫 전환은 명시적인 수동 검토가 필요합니다. 독립적으로 신뢰하는 `gh`로 새 원본 명세와 attestation을 확인하고, 후보 도구를 실행하기 **전에** 받은 소스 네 개의 해시를 인증된 명세와 대조하세요. 검증되지 않은 후보의 스크립트를 실행해 그 후보 자체의 신뢰성을 입증하지 마세요. 검토한 소스 전환이 state·키 교체를 허용하거나 독립 복구 터미널 조건을 없애지는 않습니다. 서명 없이 발행된 이전 릴리스를 나중에 서명된 릴리스였다고 설명해서도 안 됩니다.

## 릴리스 후보 만들기

**Release provenance candidate** 워크플로는 정해진 저장소의 안정 버전 태그에서만 동작합니다. 검증 job은 `contents: read`만 갖고 시험 도구를 설치한 뒤 원본 명세·Bash 문법·오프라인 시험을 확인합니다. 이 job에는 `id-token: write`나 `attestations: write`를 부여하지 않습니다.

별도의 서명 job은 검증 성공을 기다린 다음, 같은 `github.sha`를 새로 체크아웃해 후보를 다시 만듭니다. 검증 job의 서명 없는 artifact를 내려받아 신뢰하지 않습니다. 서명 job은 runner에 미리 설치된 `/usr/bin/python3` 3.11 이상을 사용합니다. 첫 Python 실행을 격리하고, 표준 라이브러리 빌드 도구의 하위 실행에도 시스템 도구 경로와 안전한 Python import 설정을 적용합니다. 서명 권한을 가진 채로 패키지 설치·setup-python·전체 시험을 실행하지 않습니다. 빌드 도구는 계속 체크아웃 커밋·태그·`VERSION`의 일치를 요구합니다. SHA로 고정한 GitHub action으로 원본 명세와 두 소스 압축 파일에 attestation을 발행하고, 별도로 전달할 수 있는 증거를 `source-manifest.sigstore.json`으로 보관합니다. job을 나눠도 허용된 워크플로 신원·소스 ref·커밋 정책은 바뀌지 않습니다.

서명 job은 여전히 GitHub가 호스팅하는 runner와 사전 설치된 인터프리터, 검토한 저장소의 빌드·명세 도구, SHA로 고정한 checkout·attestation·upload Actions를 신뢰합니다. 배포자 권한으로 실행되는 불필요한 코드를 줄이는 설계이지, runner·인터프리터·저장소·모든 제삼자 침해를 막는다는 보장은 아닙니다.

결과 artifact에는 기존 릴리스 파일 여덟 개와 선택용 메타데이터·Sigstore 묶음이 더해져 파일 열 개가 들어 있습니다. 워크플로는 별도로 전달할 수 있는 묶음을 복사한 뒤 그 체크섬을 `SHA256SUMS`에 추가하므로, 자기 자신을 제외한 배포 파일 아홉 개가 모두 포함됩니다. 체크섬은 배포 파일의 일관성을 확인하며, Sigstore 묶음의 인증은 attestation 검증으로 수행합니다. 워크플로는 관리자가 검토할 임시 Actions artifact만 올립니다. GitHub Release를 만들거나 기존 릴리스를 덮어쓰거나 운영 환경에 배포하지 않습니다. 관리자는 정확한 실행·소스·태그를 확인하고 증거를 독립적으로 검증한 뒤 다시 빌드하지 않은 원본 후보 바이트를 발행해야 합니다.

## 검증한 범위

오프라인 시험은 신뢰하는 `gh`의 성공을 mock한 뒤 정책용 fixture를 parser에 넣습니다. 다른 저장소·소유자 ID, 워크플로, ref, 소스 커밋, 명세·소스 바이트, 누락된 증거, 새 버전의 unsigned 예외와 이전 버전 fallback 거부를 시험합니다. 실제 로컬 자식 프로세스로 출력·시간 제한도 확인합니다. 이 fixture는 암호학적 서명이나 새 운영 attestation이 아니며 태그 워크플로가 실행됐다는 증거도 아닙니다. 실제 발행 전에는 해당 워크플로를 실행하고 신뢰하는 `gh`로 결과를 검증해야 합니다. 이 fixture 시험에서는 실제 Dots 운영 환경 설치나 공격 시나리오를 검증하지 않았습니다.

별도의 도구 동작 점검에서는 [GitHub CLI v2.103.3의 공개 fixture](https://github.com/cli/cli/tree/v2.103.3/pkg/cmd/attestation/test/data)와 독립적으로 받은 신뢰 루트를 사용했습니다. 실제 `gh`가 정확한 워크플로·ref·소스에 고정된 `actions/attest-demo` 증명은 허용하고 저장소 선택이나 artifact 바이트가 바뀐 경우는 거부했습니다. 이는 CLI 옵션과 출력 구조를 확인한 것이지 dots-tailscale에 발행된 attestation이 아닙니다.

도구의 공식 문서: [GitHub CLI attestation verify](https://cli.github.com/manual/gh_attestation_verify), [trusted-root](https://cli.github.com/manual/gh_attestation_trusted-root), [GitHub artifact attestations](https://docs.github.com/en/actions/security-for-github-actions/using-artifact-attestations/using-artifact-attestations-to-establish-provenance-for-builds).
