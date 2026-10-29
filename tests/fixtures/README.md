# GitHub CLI attestation stdout contract

`gh-attestation-2.103.3-actions-attest-demo.json` is **unmodified stdout** captured
from trusted GitHub CLI **2.103.3** on 2026-10-29. It was not reconstructed from
selected fields or flattened for presentation.

- Size: **7,837 bytes**
- SHA-256: **`f46563c3a1ffb835dec870a01e6605960350ae9fdef073489fe5a9b33f03a324`**
- Upstream public material: the wheel and bundle in
  [cli/cli v2.103.3 test data](https://github.com/cli/cli/tree/v2.103.3/pkg/cmd/attestation/test/data),
  named `github_provenance_demo-0.0.12-py3-none-any.whl` and
  `github_provenance_demo-0.0.12-py3-none-any-bundle.jsonl`.
- That upstream material describes `actions/attest-demo`, workflow
  `.github/workflows/build-python.yml`, ref `refs/heads/main`, source commit
  `a6c23b9806c593664f68637c8f9d45dfcf98b2db`. It is **not dots-tailscale evidence**.

The capture used independently retrieved Sigstore trust roots and this command
(paths shown below are generic input aliases, not embedded in the fixture):

```bash
gh attestation verify github_provenance_demo-0.0.12-py3-none-any.whl \
  --repo actions/attest-demo \
  --signer-workflow actions/attest-demo/.github/workflows/build-python.yml \
  --source-ref refs/heads/main \
  --source-digest a6c23b9806c593664f68637c8f9d45dfcf98b2db \
  --deny-self-hosted-runners \
  --bundle github_provenance_demo-0.0.12-py3-none-any-bundle.jsonl \
  --custom-trusted-root independently-obtained-trusted-root.jsonl --format json
```

The actual result structure is an array of objects containing `attestation` and
`verificationResult`. The certificate is at
`verificationResult.signature.certificate`; `signature` is an object, not a list.
The statement and timestamps are also inside `verificationResult`. Tests preserve
this wrapper and assert its concrete types. A projection such as
`[{certificate, statement, verifiedTimestamps}]` is not raw CLI output and must
not be used to argue that the consumer should accept a different schema.

The captured JSON includes only public certificate/signature/statement material.
It was checked for private filesystem paths, GitHub token markers and private-key
PEM material before publication. No input credentials, trust-root private keys or
local configuration files are included. The public verification material remains
byte-identical to the capture.

The parser-positive regression deep-copies this JSON and changes **only the
policy-consumed summary values** for a synthetic package: certificate publisher,
workflow/ref/source fields, manifest subject digest and predicate workflow/source
binding. The original signed bundle and wrapper are retained. Those adapted
summaries therefore do **not** match the signed bundle and are **not
cryptographically valid mutated proof**. They are a post-verifier parser fixture,
never a replacement for `gh attestation verify`. The unmodified upstream JSON is
separately required to fail this package's fixed publisher policy.

A subsequent real tool-contract check used the same upstream material with an
exact `--signer-workflow ...@refs/heads/main` and matching `--signer-digest`:
the valid upstream selection exited **0**, a changed repository selection exited
**1**, and modified artifact bytes exited **1**. These are the previously recorded
upstream tool checks, also preserved unchanged in
`gh-attestation-2.103.3-contract-results.json`, not tests that issue a new
dots-tailscale attestation. The
unit suite does not rerun network verification or claim that our release workflow
has already produced evidence.
