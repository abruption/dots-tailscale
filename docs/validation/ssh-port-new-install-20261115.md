# Issue #63: new-install SSH port validation — 2026-11-15

This is unreleased local development based on main/tag v0.9.1 commit
`c0a9c806d59b74786da88f50711aec36539e1828`. The remote main was checked again and
remained at that commit; no related open PR was present at task start. `VERSION`
is unchanged. The refreshed source manifest records working-tree consistency,
not publication or publisher authentication of this development feature.

## Implementation

- [`tailscale-setup.sh`](../../tailscale-setup.sh): a command-scoped
  `DOTS_TS_SSH_PORT` is accepted for a new `install` only. Preflight validates the
  selected value against the kernel threshold, application proxies, shim port
  and occupied loopback listeners before identity/code placement. Startup checks
  the saved/configured value again before launching anything and passes it to
  the local SSH banner probe.
- [`dots_proxy.py`](../../dots_proxy.py): `ssh-port.json` stores one integer with
  schema 1 in an owned mode-600 file. The generated OpenSSH configuration retains
  loopback binding, public-key authentication and the original identity paths.
  Reader validation rejects unknown/duplicate fields, unsafe metadata, malformed
  or noncanonical settings, mismatched/custom sshd configuration and proxy-port
  conflicts. Existing generated port-22 installations without metadata retain
  their legacy interpretation; no implicit port migration is written.
- [`dots-ts`](../../dots-ts): source update ownership/readiness checks,
  `doctor`'s selected endpoint and the existing local recovery probe consume the
  same reader. Runtime-update preservation checks use the same value. This is
  endpoint consistency work, not a new recovery operation or policy.
- Update preparation and its frozen worker validate candidate support. All three
  port-dependent sources must declare schema 1 for nondefault installations;
  incomplete/mixed/unsupported declarations are rejected. Settings/configuration
  hashes and file identities are rechecked before code replacement and during
  readiness. The package updater still changes/restarts only the shim and code;
  it does not restart or rewrite sshd or the selected setting.

## Tests and evidence

No operating node, SSH server, selected port 22/2222, tailnet policy, root/TUN or
sysctl setting was changed. Process ownership, probes and signals in new endpoint
cases are mocks; identity/configuration files are inert temporary fixtures.

| Check | Result and scope |
| --- | --- |
| SSH-port unit/integration cases | 21 passed, including ports 22/2222; thresholds 0/1024/2222/2223; malformed/range/reserved values; metadata/config mismatch; proxy and occupied-listener conflicts; startup, doctor and update endpoint agreement. |
| Source updater suite | 61 tests: pass with one existing Linux-only skip on macOS. Nondefault support, unsupported candidate refusal before writes/signals, snapshot drift and identity retention are covered. |
| Runtime updater suite | 75 tests passed in fixtures; selected SSH ownership checks retain port 2222 without changing the SSH identity. |
| Baseline diagnostic suite | 18 passed. |
| Installer fixture smoke | Default 22, saved 2222 intent and conflicting intent cases passed on macOS with only unused tool-availability checks and GNU chmod's option separator adapted. This is not a Linux installer execution. |
| Startup proxy fixture smoke | Passed on BSD Bash with only GNU empty-array expansion semantics adapted; the extra SSH-banner argument is correctly distinguished from the upstream-proxy URL. The standard case remains Linux-only. |
| Bash syntax / ShellCheck | Passed. |
| Source manifest / pinned Python static gate | Passed; static gate includes 90 Python sources including the new regression file. |

The default and nondefault fixtures check that node/SSH key data remains unchanged
on success and rejection; installer cases also compare retained file inodes.
No fake key is represented as a usable production identity.

### Exact previous-updater trial

The v0.9.1 CLI was loaded from the local tag and its SHA-256 was checked against
the original manifest:
`cb4503517686c124c6bcf3e79701750d3508f736bab0e3e0a88b140057963b64`.
Both its source-update transaction and runtime-update transaction were exercised
with a synthetic generated port-2222 listener and mocked `/proc` ownership.
They asked for listener ports `[18089, 22]` and stopped with
`listener_identity_unknown` before source writes, binary replacement or signals;
all retained fixture file bytes/inodes stayed unchanged.

This establishes safe refusal for those reached transaction guards, not support
for managing a high-port installation with the old CLI. Public caller/package
checks may reject even earlier. Use the matching new installed CLI; nondefault
installations cannot use legacy candidates that lack the complete declarations.
Default/legacy port-22 candidates retain the ordinary guarded update path.

### Python matrix and platform limitations

The commands corresponding to the offline suite in `checks.yml` were run with
CPython 3.11.17 and 3.13.17 on the local **Darwin** host, not a Linux CI runner.
Each run executes 578 tests with **4 failures, 9 errors and 23 skips**. The same
failure/error IDs were independently reproduced using unmodified v0.9.1 source;
there are no additional failure IDs in the working implementation. These runs
are **not reported as green suites**.

The pre-existing environment failures involve `/var` symlink ancestry in temporary
fixtures, missing GNU `timeout`, and the host's OpenSSH fixture/configuration
limitations (including checkout paths with spaces). Twenty-three Linux-only
cases are skipped. The final product/test files are hashed before and after the
matrix so this platform comparison is tied to the tested revision.

There is no local Linux/container runner available. At that initial revision no push or workflow dispatch
was used to obtain CI, and no live SSH/tailnet test was performed. The real Linux
Python 3.11/3.13 CI suites and GNU installer execution remain a required gate
before publication; they must not be inferred from the macOS fixture results.

## Remaining compatibility boundaries

- Existing installation port migration is not implemented. A completed install
  refuses the selection option even if it repeats the stored value; normal
  start/login reads the setting without an environment override.
- Missing metadata means legacy 22 only with the generated matching configuration.
  Missing or ambiguous configuration in a completed installation is not rebuilt
  automatically. Include/Match, duplicate directives, unsupported custom settings
  or listener overrides fail closed rather than being guessed.
- Selection is local configuration, not permission to reach a node. Separately
  review the chosen port's tailnet policy and host-key onboarding when an actual
  deployment is requested. Nondefault client/known_hosts examples are equivalent
  across EN/KO/JA/ZH usage guides.
- No operating recovery/rollout, version bump, tag or release is part of this
  change. The feature remains unreleased; Draft review and Linux CI are separate
  from release approval.


## Independent-review amendments (local follow-up)

The initial independent Claude Opus 5.5 and Gemini 4 Argon Medium reviews identified
compatibility and refusal-path gaps. The local revision adds Linux-path lexical
normalization (including legacy redundant slashes), literal generated-configuration
parsing instead of POSIX shell unescaping, private-directory repair under the
operation lock, installed-helper-only startup, reuse-address/deleted-executable
handling for the availability probe and atomic no-overwrite setting publication.
SSH snapshot drift is checked before a runtime fence or daemon stop. A late source
snapshot drift after shim stop remains an explicit manual-review outcome: source
and SSH settings are not rewritten to hide the drift, even if the old shim restarts.

All published v0.1.0–v0.9.1 generated SSH templates were compared locally and have
the same directive set. Strict checks in new code cannot be forced into an old
published updater. The four usage guides therefore require an independently
provenance-authenticated candidate and a separate read-only SSH precheck for the
first upgrade, with old automatic updates held and no concurrent config changes.
This is not an atomic old-worker guarantee; unsupported custom configurations still
need manual review and no migration is provided.

Additional regressions cover atomic write failure/no partial final file, concurrent
publication preservation, lexical path normalization, quote/escape rejection,
pre-fence runtime drift, post-stop source drift, installed-helper/locked permission
repair, deleted sshd process identity and a real journal JSON round-trip. Earlier
matrix counts above describe the initial revision; follow-up and CI results must
be recorded separately before this work is presented as ready.


The second review caught a misplaced startup recheck in the installer body; it was
moved into `start_all` and new portable placement fixtures now execute both 22 and
2222 paths on the local host. Startup fixtures assert the installed helper is
actually called and a changed saved value prevents the banner probe. Configuration
parsing now uses ASCII structural separators, rejects hidden Unicode/control
separators and parent/dot traversal, and normalizes repeated slashes only. A crash
after link publication can be retried using narrowly verified same-inode sibling
cleanup, only before node/SSH identity exists; unknown/active links are preserved.

The suggestion that the kernel threshold file requirement is newly regressed was
not adopted: the original v0.9.1 preflight already unconditionally reads that file
and rejects port 22 above the threshold, even with a capability. This feature
retains that existing policy and the requested threshold boundary contract.


The final follow-up distinguishes U+212A (Kelvin sign) from ASCII K in the keyword
folding regression: the test now uses an explicit `\u212A` escape/codepoint assertion,
and separately confirms normal ASCII keyword case remains accepted. Root paths
with SSH quote/escape/comment/percent-expansion characters are refused before new
identity work; ordinary Unicode-letter paths remain supported. Existing generated
port-22 data is not a blanket compatibility claim for custom directives, file
permissions/hardlinks or unusual interpreted paths. The usage guides explicitly
state this tightening and require authenticated old-updater prechecks rather than
claiming an old worker can enforce new guards. Public-only/authenticated identity
evidence also prevents classifying an installation as fresh.


## Final independent-review disposition

Fresh read-only, tool-disabled/plan-mode CLI reviews used Claude Code 2.1.325
(`claude-opus-5-5`) and Antigravity 1.5.9 (`gemini-4-argon-medium`). Both ran in
parallel on identical frozen inputs; requested/returned model metadata matched,
completion succeeded and input hashes stayed unchanged. Initial full-path reviews
and later scoped reviews are distinguished: the final scoped input SHA-256 is
`228167bc5410a8b8801a7cd307b8ed03060cbcfcc386a75e8208ab9771c879aa`.
No reviewer's agreement is treated as an executed test or proof of absence of bugs.

Required findings were fixed and regression-tested. The final scoped reviews found
no confirmed remaining required code defect. Claude requested verification of a
miscount in the review prompt: there are **12 published tags**, each with the same
**11 SSH directives**, not 12 directives. The saved original templates and v0.9.1
source confirm this was a prompt count error, not a missing configuration directive.
ASCII keyword case remains accepted; the Kelvin-sign regression is a separate
non-ASCII case. Optional ideas not implemented are not release guarantees.

Follow-up local checks supersede the initial focused counts: 39 SSH-port tests
passed; the installer suite has 22 tests (3 portable passes, 19 Linux-only skips);
source updates have 62 tests (one Linux-only skip on Darwin); runtime updates have
76 tests. Bash syntax, ShellCheck, manifest consistency, the pinned static checker
(90 Python sources), and whitespace checks passed. The initial Darwin full-matrix
failures above remain disclosed; Linux CI results must be recorded independently.
