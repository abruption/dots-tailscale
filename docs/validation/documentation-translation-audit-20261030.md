# Documentation translation audit — 2026-10-30

Tracking: [#77](https://github.com/abruption/dots-tailscale/issues/77). Source baseline: `a54688e1e2814ed89e9a219c62d7398398b855cd` (v0.8.0).

## Scope and method

The inventory below covers all **61 tracked Markdown files** at the source baseline: **43 under `docs/`**, plus root, research and fixture documentation. Languages: 41 English, 14 Korean, three Japanese and three Simplified Chinese files. Three user-facing recovery instruction `.txt` files were also reviewed. Dependency requirements, untracked files and private operating records are outside this language audit.

GPT-6 Luna subagents independently compared translated guides with English and reviewed English wording. Codex checked findings against the source and applied edits in an isolated worktree. Gemini 3.8 Flash Medium provided a separate read-only review through Antigravity CLI. Final model results and validation are recorded below. These are language and source-comparison reviews, not new runtime, security or recovery trials; model agreement is not a guarantee that no language defects remain.

The reported words **“예비군”** and **“붓기”** are not present in tracked Markdown at this baseline, including either release-provenance file. Their origin in the reading experience was not reproduced. This audit corrects verified source problems without attributing those words to a particular browser feature.

## Corrections

- Restore the Korean lifecycle research record's omitted acceptance gates, observation limits, collector safeguards and distinctions between event receipt, execution reports and external recovery. Keep its 2026-10-23 evidence and historical versions as historical observations.
- Restore missing installation opt-in and automatic-update/recovery-policy caveats in Korean guides.
- Describe a retry backoff interval without implying that the command sleeps for a minute.
- Translate compromised certificates as **침해된**, rather than asserting they were leaked.
- Describe HOME continuity as **HOME 유지 여부**, rather than HOME initialization.
- Clarify provenance statements, the untrusted selection descriptor and execution context in Korean.
- Make the Japanese cross-baseline update guard explicit and clarify the expected non-sensitive HTTP response.
- Correct English version spacing and awkward expressions without changing historical test counts or results. Simplified Chinese files needed no substantive changes in this pass.

## Terminology for future reviews

Translate technical terms by their role in the sentence; do not replace identifiers or quoted JSON values.

| Term | Preferred Korean meaning in context |
|---|---|
| boot / boot ID | 부팅 / 부팅 ID |
| reserve a connection slot | 연결 슬롯을 확보하다 |
| claim | claim(실행 승인); keep protocol identifiers unchanged |
| backoff | 재시도 대기 시간; distinguish it from a blocking sleep |
| compromised | 침해된; do not infer leakage without evidence |
| provenance statement | 출처 진술; distinguish authenticated evidence from unverified claims |
| bootstrap | 최초 신뢰 전환 or 최초 시작, according to context |
| execution context | 실행 환경; retain namespace/visibility qualifications |

Commands, flags, paths, JSON fields, hashes, versions, numerical policies, evidence boundaries and opt-in requirements need comparison with the English source as well as a natural-language review. Historical reports must not silently become claims about current operation.

## File coverage

“Reviewed; no change required” means this pass found no actionable wording defect; it is not a certification of perfect translation.

| File at baseline | Language | Disposition |
|---|---|---|
| `CHANGELOG.md` | English | Reviewed; no change required |
| `README.ja.md` | Japanese | Revised |
| `README.ko.md` | Korean | Reviewed; no change required |
| `README.md` | English | Revised |
| `README.zh-CN.md` | Simplified Chinese | Reviewed; no change required |
| `SECURITY.md` | English | Reviewed; no change required |
| `docs/automatic-updates.ko.md` | Korean | Revised |
| `docs/automatic-updates.md` | English | Reviewed; no change required |
| `docs/baseline-diagnostics.ko.md` | Korean | Revised |
| `docs/baseline-diagnostics.md` | English | Reviewed; no change required |
| `docs/experimental-recovery.ko.md` | Korean | Revised |
| `docs/experimental-recovery.md` | English | Reviewed; no change required |
| `docs/maintainer-policy.md` | English | Reviewed; no change required |
| `docs/private-http-api.ja.md` | Japanese | Revised |
| `docs/private-http-api.ko.md` | Korean | Reviewed; no change required |
| `docs/private-http-api.md` | English | Reviewed; no change required |
| `docs/private-http-api.zh-CN.md` | Simplified Chinese | Reviewed; no change required |
| `docs/recovery-lifecycle.ko.md` | Korean | Revised |
| `docs/recovery-lifecycle.md` | English | Reviewed; no change required |
| `docs/recovery-support-readiness.ko.md` | Korean | Revised |
| `docs/recovery-support-readiness.md` | English | Reviewed; no change required |
| `docs/release-provenance.ko.md` | Korean | Revised |
| `docs/release-provenance.md` | English | Reviewed; no change required |
| `docs/releases/v0.2.0.md` | English | Reviewed; no change required |
| `docs/releases/v0.3.1.md` | English | Revised |
| `docs/releases/v0.4.0.md` | English | Reviewed; no change required |
| `docs/releases/v0.5.0.md` | English | Reviewed; no change required |
| `docs/releases/v0.6.0.md` | English | Reviewed; no change required |
| `docs/releases/v0.7.0.md` | English | Revised |
| `docs/releases/v0.7.1.md` | English | Reviewed; no change required |
| `docs/releases/v0.8.0.md` | English | Reviewed; no change required |
| `docs/runtime-updates.ko.md` | Korean | Reviewed; no change required |
| `docs/runtime-updates.md` | English | Reviewed; no change required |
| `docs/security-baseline.ko.md` | Korean | Revised |
| `docs/security-baseline.md` | English | Reviewed; no change required |
| `docs/shim-admission.md` | English | Reviewed; no change required |
| `docs/static-checks.md` | English | Reviewed; no change required |
| `docs/update-check-resilience.ko.md` | Korean | Revised |
| `docs/update-check-resilience.md` | English | Reviewed; no change required |
| `docs/usage.ja.md` | Japanese | Reviewed; no change required |
| `docs/usage.ko.md` | Korean | Revised |
| `docs/usage.md` | English | Reviewed; no change required |
| `docs/usage.zh-CN.md` | Simplified Chinese | Reviewed; no change required |
| `docs/validation/cooldown-external-health.md` | English | Reviewed; no change required |
| `docs/validation/runtime-update-trial-20261026.md` | English | Reviewed; no change required |
| `docs/validation/security-baseline-20261028.md` | English | Reviewed; no change required |
| `docs/validation/v0.4.0-update-trial.md` | English | Reviewed; no change required |
| `docs/validation/v0.7.0-migration-trial-20261028.md` | English | Revised |
| `docs/validation/v0.8.0-development-review.md` | English | Reviewed; no change required |
| `research/lifecycle-20/README.ko.md` | Korean | Revised |
| `research/lifecycle-20/README.md` | English | Reviewed; no change required |
| `research/mcp-events/README.md` | English | Revised |
| `research/mcp-events/continuous-trial.md` | English | Reviewed; no change required |
| `research/mcp-events/deployment.md` | English | Reviewed; no change required |
| `research/mcp-events/recovery-cooldown.ko.md` | Korean | Revised |
| `research/mcp-events/recovery-cooldown.md` | English | Reviewed; no change required |
| `research/mcp-events/relay-transport.md` | English | Reviewed; no change required |
| `research/mcp-events/security-hardening.md` | English | Reviewed; no change required |
| `research/mcp-events/services/README.md` | English | Reviewed; no change required |
| `research/mcp-events/stabilization-20261023.md` | English | Reviewed; no change required |
| `tests/fixtures/README.md` | English | Reviewed; no change required |

The three additional text documents were `research/mcp-events/coordinated-recovery-instructions.en.txt`, `research/mcp-events/coordinated-recovery-instructions.ko.txt` and `research/mcp-events/dots-recovery-instructions.ko.txt`. They were reviewed without changes.

This audit record is new and is not counted among the 61 baseline files.

## Independent review and disposition

- **GPT-6 Luna:** read-only source comparisons covered all translated Markdown and English documentation. Final Korean review found no blocking meaning or safeguard changes; its additional clarity suggestions were applied and checked by Codex. A separate reviewer also read the three recovery instruction templates.
- **Gemini 3.8 Flash Medium:** the initial read-only Antigravity run completed successfully and returned coverage for all 61 baseline Markdown files. It independently identified the backoff wording, certificate terminology, Japanese baseline guard and omitted Korean lifecycle safeguards. Those findings agree with direct source comparisons and are addressed here.
- **Rejected model suggestions:** Gemini attributed the reported words to external automatic translation without observing that rendering path. That causal claim remains unverified. It also treated lines beginning `#44`, `#61` or `#75` as accidental headings; CommonMark ATX headings require whitespace after the opening hash sequence, so these issue references were not escaped as a purported syntax repair. Optional jargon-heavy replacements were not accepted merely because a model suggested them.
- **Gemini final review:** a separate fresh read-only `gemini-3.8-flash-medium` invocation compared all 18 revised documents and their English counterparts, including the restored lifecycle record. It returned no required corrections. Codex checked the result against the actual diff rather than adopting its absolute claims of perfection or incidental file-description errors. The two Gemini calls completed successfully; there was no unavailable-model fallback.
- Final clarity edits from Luna (cooldown streak reset wording and terminology) and the remaining usage backoff phrase were checked against English after the model snapshots. Neither changes an identifier, command or policy.

## Validation

- `git diff --check`: passed.
- Relative Markdown links, explicit HTML anchors and generated heading anchors: no missing local targets or anchors across the baseline inventory.
- Fenced command blocks in the existing documents are unchanged except in the restored Korean lifecycle record; its command blocks now match the English record exactly.
- `python3 scripts/source_manifest.py --check`: passed. Executable sources and the release source manifest are unchanged.
- The 61-file inventory was checked against tracked Markdown paths; the three text templates were reviewed separately. Untracked/private files were excluded.
- No live commands from the documentation were executed, and no runtime, deployment, authentication, repository protection or signed release asset was modified. Local runtime test suites were not rerun for prose-only changes; required PR CI remains a separate check.
