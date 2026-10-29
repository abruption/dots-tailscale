# Release provenance and the update trust boundary

These features belong to the **v0.8.0 development candidate**. The latest published
stable release is v0.7.1; publishing this branch does not upgrade an installation.

Starting with package **v0.8.0**, the updater requires a GitHub Actions artifact
attestation for the exact original `source-manifest.json`. That manifest binds
the four executable source files by SHA-256. Verification happens before syntax
checks, transaction staging, code replacement or candidate execution. A detached
worker also checks that the staged manifest is the same one recorded in the
verified receipt. The installer, node state, SSH keys and Tailscale runtime update
contracts are not changed by this policy.

## What is trusted

The updater uses an already trusted installation and a **preinstalled GitHub CLI
2.103.3 or newer**. It does not download a verifier or execute one from a candidate
bundle. A separately obtained Sigstore trust-root file must be supplied with
`--provenance-root`. The updater rejects a root or verifier taken from the candidate
directory or installation directory. Keep the independent root outside those
directories, owned by the operator or root, without group/other write permission.
Their canonical parent directories must also belong to the operator or root and
must not allow group/other writes. A trusted sticky parent is allowed only when
its immediate child is also operator/root-owned, as with a private directory in
`/tmp`. The root's actual opened descriptor is checked and read once; the verifier
is executed by its checked canonical path after these ancestor checks. These
checks prevent replacement by other UIDs, not malicious changes by the operator
or root, who remain part of the trust boundary.

Some macOS Homebrew or external-volume paths have group-writable ancestors and
are therefore refused by these embedded checks, even when the leaf file is
read-only. This is a path-trust refusal, not a finding that the binary is malicious.
Package apply and automatic execution already require Linux/pidfd support. For
the separately reviewed first-upgrade bootstrap, an operator may use their own
independently trusted `gh` directly, or prepare verified tools and roots under
protected paths. The updater does not change host permissions or implicitly
trust a writable administrator group to make a path pass.

The verification policy is fixed in the currently trusted updater:

- repository `abruption/dots-tailscale`, numeric repository ID `1418377192`;
- repository owner `abruption`, numeric owner ID `23277516`;
- workflow `.github/workflows/release-provenance.yml` in that repository;
- the exact stable `refs/tags/vX.Y.Z` matching the candidate manifest version;
- the source commit bound in the certificate and provenance statement;
- GitHub Actions' OIDC issuer and GitHub-hosted runners;
- SLSA provenance v1 and the exact manifest SHA-256.

Both the certificate's authenticated fields and the verified statement's workflow,
ref and source dependency must agree. The portable `release-provenance.json` is
only an **untrusted selection descriptor** until those checks pass. The updater
does not trust claims decoded from an unsigned DSSE payload or a saved
"verification succeeded" text file. The verifier runs with a private empty GitHub
configuration directory and home, a minimal fixed environment without inherited
credentials or proxy/loader settings, no interactive
prompts, a 30-second verification deadline and a 2 MiB stdout limit. Raw verifier
errors are not copied into user diagnostics. The version probe has a separate
5-second/4 KiB limit.

The private temporary workspace is checked against the same protected-ancestor
rules before running the verifier. An operator-selected `TMPDIR` under a writable
shared ancestor is not made safe merely by giving its last directory mode 0700;
an unsafe workspace is refused without executing the verifier.

The trust-root file is copied into private temporary verification storage. With
`--bundle` and `--custom-trusted-root`, `gh` verifies the supplied evidence without
fetching attestations or refreshing TUF metadata. Obtain/refresh the roots on an
independently trusted machine using its installed `gh attestation trusted-root`,
review their provenance and transfer them separately. Merely including a
`trusted-root.jsonl` in a release bundle does not authorize using it.

This does **not** protect against a compromised local operator/root, malicious
preinstalled verifier, compromised trust roots, or a maintainer/workflow/account
that is allowed to create the accepted attestation. Statement predicate contents
are workflow-controlled, so source/ref checks are additionally bound to the OIDC
certificate; they do not make the workflow itself immune to compromise. Reviewed
branch/tag/workflow protections and independent review remain important. Exact
numeric repository/owner IDs also reject offline name reuse, but this is not a
fresh online check of the repository's current ownership or revocation state.
It does not establish latest-release freshness or detect a tag being retargeted
after signing. The selected version/ref must agree; automatic update callers
separately exclude prereleases and downgrades rather than treating any valid
historical attestation as permission to install it.

## Manual and automatic updates

For an attested release, provide the independently prepared root:

```bash
/workspace/.dots-tailscale/dots-ts update apply --tag v0.8.0 \
  --proxy http://browser-proxy:8889 \
  --provenance-root /workspace/trusted-tools/dots-ts/trusted-root.jsonl \
  --confirm-recovery-terminal --json
```

A reviewed offline source bundle contains the four original sources, original
manifest, `release-provenance.json` and `source-manifest.sigstore.json`. Use the
same `--provenance-root` with `update apply --bundle /path/to/bundle`. The local
bundle avoids GitHub release/API requests; verification still requires the
independent local `gh` and trust root. Proof failure, missing evidence, a wrong
workflow/ref/commit or an unavailable verifier stops the operation. There is no
fallback to hash-only success, and no `--repo` override.

Older unsigned targets **below v0.8.0** require explicit manual operator review:

```bash
/workspace/.dots-tailscale/dots-ts update apply --tag v0.7.1 \
  --proxy http://browser-proxy:8889 --allow-unsigned-legacy \
  --confirm-recovery-terminal --json
```

That exception means the operator independently reviewed the source origin and
bytes. It records `legacy_operator_review`, **not authenticated provenance**.
It cannot authorize an unsigned v0.8.0-or-newer candidate. Automatic update
callers must never set this exception. `update migrate-baseline` accepts the same
explicit legacy flag/root selection and keeps its existing recovery and binary
baseline guards. Read-only update discovery can compare release metadata without
authenticating provenance; it is not permission to execute or install that release.

An already installed pre-v0.8.0 updater cannot enforce a new verification policy.
Its first transition therefore needs an explicitly reviewed bootstrap: verify the
new original manifest and its attestation using an independently trusted `gh`,
then check all four downloaded source hashes against that authenticated manifest
before executing any candidate helper. Do not use a verifier script from the
unverified candidate to establish its own trust. A source-only reviewed bootstrap
does not authorize state/key replacement or bypass the independent recovery
terminal. Historical unsigned releases are not retroactively described as signed.

## Producing a release candidate

The **Release provenance candidate** workflow runs only in the canonical
repository at stable version tags. It checks the original manifest and offline
tests, requires the checked-out commit, tag and `VERSION` to agree, then builds
the source archives and individual sources from that exact commit. It attests
the original manifest and both source archives using a pinned GitHub action.
The portable bundle is retained as `source-manifest.sigstore.json`.

The resulting artifact has ten files: the existing eight release assets plus
the descriptor and Sigstore bundle. After copying the portable bundle, the workflow
adds its checksum to `SHA256SUMS`, covering all nine other distributed files.
These checksums provide distribution consistency, while the bundle authenticates
itself through attestation verification.
The workflow uploads a temporary Actions artifact for maintainer review. It does
**not** create a GitHub Release, overwrite an existing release or deploy anything.
The maintainer must inspect the exact run/source/tag, independently verify its
evidence, and publish the original candidate bytes without rebuilding them.

## Validation scope

Offline tests feed policy fixtures to the parser **after mocking trusted `gh`
success**. They test rejection of wrong repository/owner IDs, workflow, ref,
source commit, manifest/source bytes, missing evidence, unsigned-new-version
exceptions and legacy fallback. Real local child-process tests cover verifier
output/time limits. These fixtures are not cryptographic signatures, a new
production attestation or evidence that a tag workflow has run. A release must
also run the actual workflow and validate the resulting evidence with trusted
`gh` before publication. No live Dots installation or attack was tested by those
fixtures.

A separate tool-contract smoke check used the public
[GitHub CLI v2.103.3 upstream fixture](https://github.com/cli/cli/tree/v2.103.3/pkg/cmd/attestation/test/data)
and independently retrieved trust roots. Real `gh` verification accepted its
authorized `actions/attest-demo` provenance with an exact workflow/ref/source
pin, and rejected both a changed repository selection and modified artifact
bytes. This validates the selected CLI flags and output shape; it is not an
attestation issued for dots-tailscale.

Primary tool contracts: [GitHub CLI attestation verify](https://cli.github.com/manual/gh_attestation_verify),
[trusted-root](https://cli.github.com/manual/gh_attestation_trusted-root),
[GitHub artifact attestations](https://docs.github.com/en/actions/security-for-github-actions/using-artifact-attestations/using-artifact-attestations-to-establish-provenance-for-builds).
