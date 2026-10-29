# 설치된 Tailscale의 기준 버전 읽기

[English](baseline-diagnostics.md)

이 문서는 v0.8.0 개발 후보의 추가 진단을 설명합니다. 최신 안정 버전은 v0.7.1입니다.

`dots-ts version`과 `dots-ts doctor`는 현재 실행 환경에서 보이는 Tailscale 버전을 설치의 `setup.sh`에 선언된 `TS_VERSION`과 비교합니다. 업데이트·프로세스 재시작·인증 변경·보안 권고 조회는 하지 않습니다.

선언의 `sha256`과 `source_integrity`는 실제로 읽은 setup 파일이 소스 명세로 검증한 바이트와 같은지 보여줍니다. 조회 도중 파일이 바뀌면 예전 패키지 검사 결과로 새 선언을 검증했다고 처리하지 않습니다.

```bash
/workspace/.dots-tailscale/dots-ts version --json
/workspace/.dots-tailscale/dots-ts doctor --json
```

기준은 해당 패키지가 선택한 버전이지 모든 환경에 적용되는 보안 하한선이 아닙니다. 기준보다 낮다고 자동으로 취약하다고 분류하거나, 더 높다고 안전하다고 표시하지 않습니다. 날짜가 명시된 [보안 기준 안내](security-baseline.ko.md)는 별도로 확인하세요. 소스 명세가 일치한다는 것은 그 명세와의 일관성이며, 배포자 신원을 독립적으로 인증하는 것은 아닙니다.

## 명령마다 관측하는 대상

- `version`은 패키지 무결성을 읽고 설치된 CLI의 `version`, 데몬 바이너리의 `--version`을 실행합니다. 각 바이너리 조회는 최대 5초, 출력은 최대 4 KiB로 제한됩니다. 실행 중인 데몬에는 접속하지 않으며 백엔드는 `not_queried`로 표시합니다.
- `doctor`는 위 로컬 바이너리 조회에 더해 기존의 제한된 로컬 소켓 `status --json` 응답을 재사용합니다. 실행 중인 백엔드 버전은 오직 이 응답의 `Version`에서 읽습니다. CLI 버전으로 누락되거나 접근할 수 없는 백엔드의 버전을 대신하지 않습니다.
- 선언을 읽을 때 `setup.sh`를 실행하지 않습니다. 선언이 없거나 읽을 수 없거나, 심볼릭 링크이거나, 여러 개로 모호하거나, 지원하지 않는 형식이면 결과는 알 수 없는 상태로 남습니다. 바이너리 경로의 마지막 항목이 심볼릭 링크이거나 일반 파일이 아니어도 버전 조회를 실행하지 않습니다.
- 각 명령은 현재 프로세스가 볼 수 있는 대상만 보고합니다. 다른 마운트·프로세스 네임스페이스, 읽을 수 없는 설치 경로, 없거나 접근이 거부된 소켓 때문에 백엔드를 관측하지 못할 수 있습니다. 별도로 열린 Dots 클라우드 터미널에서는 보이는 대상이 다를 수 있습니다.

`version` JSON은 기존 `package`, `tailscale` 필드를 유지하고 `runtime_baseline`을 추가합니다. `doctor`는 같은 구조를 `checks.runtime_baseline`에 넣습니다. 사람이 읽는 출력에서도 각 관측값과 비교 결과를 따로 보여줍니다.

## JSON 계약: 스키마 버전 1

| 필드 | 의미 |
| --- | --- |
| `schema_version` | 이 진단 구조의 버전 `1`입니다. |
| `declaration.version` / `status` | 설치된 `setup.sh`에서 읽은 안정 버전 문자열입니다. 읽을 수 없으면 `null`과 `missing`, `unavailable`, `invalid_declaration` 중 해당 상태를 표시합니다. |
| `declaration.package_integrity` | 기존 소스 명세의 무결성 결과입니다. 검증되지 않았거나 변경된 선언으로 기준 충족 판정을 만들지 않습니다. |
| `cli`, `daemon_binary`, `backend` | 각각 별도의 관측 결과이며 `version`, `status`, `release_kind`, `comparison`을 담습니다. 잘못된 응답 원문은 출력하지 않습니다. |
| 관측 `status` | `available`, `timeout`, `unavailable`, `invalid_response`, `missing`, `skipped`, `not_queried`입니다. 응답한 백엔드에 `Version`이 없으면 `missing`입니다. |
| 관측 `comparison` | 선언된 안정 버전과 비교한 `below`, `equal`, `above`입니다. 비교할 수 없으면 `unknown` 또는 `not_comparable`입니다. |
| 관측 `release_kind` | `stable`, `release_build`, `prerelease`, `unknown`입니다. 일반적인 백엔드 빌드 접미사인 `1.104.4-t27047635f` 등은 관측한 전체 문자열을 보존하면서 릴리스 `1.104.4`로 비교합니다. 다른 prerelease 접미사는 안정 버전의 순서로 비교하지 않습니다. |
| `consistency` | 관측한 릴리스 번호가 다르면 `mismatch`, 적어도 두 관측값이 같으면 `match`, 그 외에는 `unknown`입니다. 릴리스 식별자를 비교하며 바이너리의 실제 바이트나 빌드 출처를 비교하는 것은 아닙니다. 두 값이 같아도 누락된 다른 관측값은 계속 표시합니다. |
| `scope` | `version`은 `local_binary_pair_only`, `doctor`는 소켓 관측을 못 했거나 건너뛰었더라도 `binary_pair_and_backend`입니다. |
| `assessment` | `local_binaries_meet_baseline`, `meets_declared_baseline`, `version_mismatch`, `below_baseline`, `nonstable_version`, `unknown_baseline`, `unverified_declaration`, `unknown` 중 하나입니다. 개별 결과도 함께 확인하세요. 예를 들어 버전 불일치 안에 기준보다 낮은 백엔드가 포함될 수 있습니다. |
| `advisory_policy` | 항상 `not_evaluated`입니다. 이 오프라인 비교는 현재 보안 상태에 대한 평가가 아닙니다. |

`local_binaries_meet_baseline`은 실행 중인 프로세스를 설명하지 않습니다. `meets_declared_baseline`이 되려면 세 관측값이 모두 사용 가능한 안정 버전 또는 일반 릴리스 빌드이고, 릴리스 번호가 같고, 선언을 읽을 수 있으며 패키지 무결성도 일치해야 합니다. 누락된 백엔드·시간 초과·잘못된 버전으로 이 결과를 만들지 않습니다.

## 종료 코드와 연결 상태의 의미는 유지합니다

추가 진단으로 기존 종료 코드는 바뀌지 않습니다. `version`은 소스가 누락됐거나 불일치하면 여전히 `1`, 그 외에는 이전 메타데이터이거나 바이너리를 관측하지 못했더라도 `0`입니다. `doctor`도 기존의 연결·구성요소·패키지 무결성 종료 코드 규칙을 유지합니다. `overall`과 `healthy`는 운영 상태 확인이지 기준 버전이나 보안에 대한 판정이 아닙니다.

예를 들어 연결이 된 노드에서도 `overall: healthy`와 `runtime_baseline.assessment: version_mismatch`가 함께 나올 수 있습니다. 실제 버전들이 선언과 맞는지 판단할 때는 `runtime_baseline`을 읽으세요. `healthy`나 종료 코드 `0`으로 추정하지 마세요.
