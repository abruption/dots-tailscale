# Tailscale security baseline and existing-install migration

[한국어](security-baseline.ko.md)

This guide describes the **unreleased 0.7.0-dev candidate** for #57. The published v0.6.0 installer still defaults to Tailscale 1.102.4. The reviewed candidate source defaults to **1.104.4** and adds an explicit source-package migration after a separate runtime upgrade. No v0.7.0 release is implied.

## Why 1.104.4

On 2026-10-28, the [official stable metadata](https://pkgs.tailscale.com/stable/?mode=json) selected 1.104.4 and the [official release](https://github.com/tailscale/tailscale/releases/tag/v1.104.4) was published at 01:05:26 UTC. The official amd64 archive is `tailscale_1.104.4_amd64.tgz`; its checksum endpoint reports SHA-256 `624ea093d47d62cf56f17b8332d74b8decda4a1faa610ebc60a1de334b2b3dc9`. Always verify the downloaded bytes, not just this recorded value.

[TS-2026-013](https://tailscale.com/security-bulletins), published on 2026-10-21, recommends 1.104.2 or later: its Go 1.27.3 runtime explicitly distrusts eight compromised eMudhra intermediate certificates. Selecting current stable 1.104.4 meets that documented version floor. No CVE identifier is asserted here.

At that review time, the [changelog](https://tailscale.com/changelog) did not yet have a detailed 1.104.4 entry, and the [SSH investigation update](https://tailscale.com/ssh-investigation-update) did not identify a complete patched-version range. A release tag alone does not establish that every reported SSH/Tailworm issue is fixed. The installer requests `--ssh=false` and uses local OpenSSH, but that default does not prove the current settings of an existing node or remove unrelated attack paths.

## Four different things to check

| Item | Meaning |
|---|---|
| Package version | The dots-tailscale scripts and original source manifest, such as published 0.6.0 or candidate 0.7.0-dev. |
| Declared installer baseline | `TS_VERSION` in that package: 1.102.4 in v0.6.0, 1.104.4 in this candidate. |
| Installed/running Tailscale | The actual CLI, daemon binary and running backend version. Changing the package declaration does not update them. |
| Host TLS trust | The OS/Python/OpenSSL verifier and saved CA bundle used around the shim; this is separate from the Tailscale Go runtime. |

New installations from the candidate verify the archive checksum, constrained archive contents and both version outputs before placing the pair. Existing binaries are not silently replaced by `install`; incomplete, mismatched or below-baseline pairs are refused. Keep all four source files and their matching original manifest together.

## Existing installation: runtime first, package second

This path is intentionally narrow: a trusted installed package declaring **1.102.4**, a complete reviewed candidate declaring **1.104.4**, and an actual installed/running Tailscale pair already at **1.104.4**. Other declaration transitions and different runtime versions need their own reviewed migration. Do not downgrade a newer node to fit this example.

1. Open an independent Dots cloud terminal and verify that it is usable without Tailscale SSH. Keep it available through external checks. Pause any external recovery/update jobs so they cannot compete with either transaction.
2. From the **installed v0.6.0 tools**, inspect integrity and the runtime, then perform the runtime upgrade if needed. Do not use the new candidate CLI against the old installation for this step.

```bash
/workspace/.dots-tailscale/dots-ts version --json
/workspace/.dots-tailscale/dots-ts runtime check --json
/workspace/.dots-tailscale/dots-ts runtime apply \
  --version 1.104.4 --proxy platform --confirm-recovery-terminal --json
/workspace/.dots-tailscale/dots-ts runtime status --id RUNTIME_TRANSACTION_ID --json
```

If both installed binaries and the running backend are already 1.104.4, skip `runtime apply`; same-version reinstalls are refused. Online apply requires the requested version to match the current official stable metadata. If that has moved on, use the [reviewed offline-bundle procedure](runtime-updates.md#offline-transfer) for the exact supported version, or wait for an expanded migration path; do not falsify metadata.

3. Confirm runtime transaction completion with no unresolved fence/cleanup warning. Open a **new strict-host-key SSH connection** and run Tailscale ping from another device. Also check the applications you depend on. A local `Running` result and the journal's `external_ssh=pending` are not external success claims.
4. Stage a reviewed complete candidate source bundle at `/workspace/reviewed-candidate`, outside the installation. Preserve its original `source-manifest.json`. Inspect the source and verify its provenance and hashes. Do not change `TS_VERSION`, regenerate a manifest to disguise mixed sources, or copy only the new CLI into the old installation.
5. Run the candidate's explicit source-only migration command:

```bash
python3 /workspace/reviewed-candidate/dots-ts update migrate-baseline \
  --bundle /workspace/reviewed-candidate \
  --confirm-recovery-terminal --json
python3 /workspace/reviewed-candidate/dots-ts update status \
  --id PACKAGE_TRANSACTION_ID --json
```

The usual `update apply` command keeps its binary-declaration equality guard and still refuses this cross-baseline transition with `binary_update_unsupported`. `migrate-baseline` is a separate, explicit guarded operation. It requires the invoking candidate to match the selected bundle, a verified installed package, the exact supported declaration transition, CLI/daemon/backend version agreement, the expected userspace/socket/proxy setup, and no unresolved package/runtime transaction or fence. The frozen worker repeats the migration checks under the shared operation lock before changing sources.

The source transaction backs up and replaces the four executable sources and manifest, then checks the shim restart. It does not replace Tailscale binaries, node state or SSH keys. A failed source migration may restore its code-only backup; it must not restore old Tailscale state or binaries as a substitute for runtime compatibility checks.

6. Check the package manifest and runtime again, and verify fresh external SSH/ping after the source transaction. Resume the paused external jobs only after these checks. If output is lost, inspect the existing transaction from the independent terminal; do not submit another migration blindly.

This new migration path has not yet been validated by a live migration in this guide. The [2026-10-26 normal runtime trial](validation/runtime-update-trial-20261026.md) used the earlier development package and is historical evidence, not a live test of this candidate or every future release.

## Runtime failure and source rollback are different

Before candidate daemon execution, the runtime updater can attempt a verified old-pair rollback. Once execution of the candidate daemon has been attempted, even a failed startup may have affected state compatibility; automatic downgrade is not assumed safe. Keep the independent terminal, transaction records and fence, and follow the [runtime recovery runbook](runtime-updates.md#manual-resolution-runbook). A source-package rollback does not authorize downgrading Tailscale or restoring authentication state.

## Inspect the host TLS path without changing trust

The shim terminates local TLS and separately verifies its upstream connection with Python's `ssl.create_default_context()`. Go's eMudhra distrust does not prove that this Python/OpenSSL verifier or the host CA store has the same policy. Inherited `SSL_CERT_FILE`/`SSL_CERT_DIR` may affect that verifier. The command below reports only whether overrides exist, not their values; its results describe the terminal's Python environment, which may differ from the running shim.

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

The saved bundle is initially built from the OS bundle plus the shim's local CA, so the two full-file hashes normally differ. Compare their **public certificate sets**, accounting for that local CA, if you need to investigate stale copies. Existing complete bundles are preserved by installation; an OS trust-store update does not automatically refresh the saved copy. A file hash, certificate count, or absence of the named intermediates does not prove a verifier rejects the compromised chains.

A [dated read-only Dots review](validation/security-baseline-20261028.md) created a fresh verifier using the running shim's interpreter and CA override environment: OpenSSL 3.5.7 with no `SSL_CERT_FILE`/`SSL_CERT_DIR` overrides. It did not inspect certificates already loaded in the running shim context. Its saved bundle differed from and predated the current OS bundle. This is evidence of a stale copied bundle, not proof of compromise or of a complete distrust policy; the shim's default upstream verifier and Tailscale's saved local-CA bundle remain separate paths.

Use the [Python SSL documentation](https://docs.python.org/3/library/ssl.html#ssl.create_default_context) and [default verify-path reference](https://docs.python.org/3/library/ssl.html#ssl.get_default_verify_paths) when interpreting the output. Confirm the running shim's actual verifier environment separately without publishing raw environment values. This review does not regenerate CA files, replace keys, disable certificate checks, change host trust policy or claim a successful negative-chain validation test.

Installation accepts a matching existing pair at or above its declared baseline; this explicit source migration only supports exactly 1.104.4. If source rollback succeeds, the package again declares 1.102.4 while the actual binaries remain 1.104.4, restoring the pre-migration state without a runtime downgrade.
