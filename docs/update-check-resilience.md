# Inspecting updates when GitHub requests are limited

These features belong to the **v0.8.0 development candidate**. The latest published
stable release is v0.7.1; publishing this branch does not upgrade an installation.

The package checker uses unauthenticated GitHub REST requests, even when a tag is
specified. A shared outbound proxy can exhaust that address's quota independently
of this installation. A `rate_limited` result is a failed check, not evidence that
the installed package is current. Setting `GH_TOKEN` or `GITHUB_TOKEN` does not
authenticate these requests.

## Inspect a local bundle without changing the installation

Obtain the original four source files and `source-manifest.json` from a fixed
release on a trusted machine. Verify its publication and provenance separately,
then transfer that source-only bundle through your approved route. To compare it:

```bash
/workspace/.dots-tailscale/dots-ts update check --bundle /workspace/reviewed-release --json
```

This command checks the bundle's manifest and captured source hashes and compares
its declared version with the installed package. It does not execute bundle code,
replace files, restart services, contact GitHub, or establish a fresh publication
or publisher-identity check. `selection` and `evidence` both say `local_bundle`;
the `trust` field makes this limit explicit. A newer local version is not proof
that the bundle is an authorized stable release. Do not substitute this result for
the separately enforced provenance and recovery gates when applying an update.

`--bundle` and `--tag` are mutually exclusive. A proxy does not apply to a local
bundle. Package/bundle integrity errors remain errors. Older clients do not have
this inspection option; their `update apply --bundle` command is an installation
operation, not a read-only check.

## Retry diagnostics and backoff

Primary HTTP 403 quota exhaustion and HTTP 429 limits remain `rate_limited`.
The tool keeps only bounded numeric `Retry-After` seconds and
`X-RateLimit-Reset` epoch seconds, plus a computed `retry_at`. It never prints
raw error text, arbitrary headers, tokens, or the proxy address. If usable retry
headers are absent, the local backoff is one minute. Supported header windows are
bounded to 24 hours; `retry_at` is advisory and never permission to execute code.

The next package check, notification refresh, or package apply using the same
explicit proxy returns that failure from the private local error cache until the
window expires. The JSON `evidence` is then `rate_limit_cache`, not
`github_live`. A changed tag cannot evade a known quota window; a different
explicit proxy is a separate cache scope. Successful release metadata is not
cached or substituted for a failed/fresh check. The cache stores only one current
proxy scope as a hash; it is a best-effort local throttle, not cross-machine quota
coordination or a promise that concurrent first requests never race.

Malformed, future-dated, unsafe, read-only or unavailable cache storage does not
turn an error into success. When a new limit cannot be saved, output includes
`warning: backoff_storage_unavailable`; repeated invocations may therefore make
requests. Do not repeatedly poll an unavailable cache. Diagnose the private
installation directory rather than weakening permissions or adding credentials.

The existing bounded downloads, repository/tag/URL checks, manifest checks,
independent recovery-terminal confirmation and transaction safeguards still apply.
An API-light pinned-tag shortcut is deliberately not used: it would need to retain
stable-publication and provenance checks, including the work tracked in #67.
