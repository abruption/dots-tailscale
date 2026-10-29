# Read the installed Tailscale baseline

These features belong to the **v0.8.0 development candidate**. The latest published
stable release is v0.7.1; publishing this branch does not upgrade an installation.

`dots-ts version` and `dots-ts doctor` compare the Tailscale versions visible in the current execution context with the `TS_VERSION` declaration in the installation's `setup.sh`. They do not update anything, restart a process, change authentication or fetch security advisories.

```bash
/workspace/.dots-tailscale/dots-ts version --json
/workspace/.dots-tailscale/dots-ts doctor --json
```

The baseline is the version selected by that installed package, not a universal security floor. A version below it is not automatically classified as vulnerable, and a newer version is not described as secure. Consult the dated [security baseline guide](security-baseline.md) separately. A matching source manifest establishes consistency with that manifest; it is not independent publisher authentication.

## What each command observes

- `version` reads package integrity, runs the installed CLI's `version` and the installed daemon binary's `--version`. Each binary probe has a five-second deadline and a 4 KiB output limit. The command never queries a daemon. The running backend is explicitly `not_queried`.
- `doctor` performs those local binary probes and reuses its existing bounded local socket `status --json` query. Only that reply's `Version` supplies the running backend version. The CLI binary version never substitutes for a missing or inaccessible backend.
- Reading a declaration does not execute `setup.sh`. A missing, unreadable, symlinked, ambiguous or unsupported declaration remains unknown. Binary leaf symlinks and non-regular binary files are not executed by the version probes.
- The commands can only report what the current process can see. A different mount or process namespace, unreadable installation, missing socket or denied socket access can prevent the running-backend observation. An independent Dots cloud-computer terminal may have different visibility.

The JSON keeps the existing `package` and `tailscale` fields for `version`. It adds `runtime_baseline`; `doctor` places the same structure at `checks.runtime_baseline`. Human output prints each observation and its comparison separately.

## JSON contract, schema version 1

| Field | Meaning |
| --- | --- |
| `schema_version` | `1` for this diagnostic structure. |
| `declaration.version` / `status` | Literal stable version from installed `setup.sh`, or `null` with `missing`, `unavailable` or `invalid_declaration`. |
| `declaration.package_integrity` | The existing source-manifest integrity result. An unverified or modified declaration cannot produce a successful baseline assessment. |
| `cli`, `daemon_binary`, `backend` | Separate observations, each with `version`, `status`, `release_kind` and `comparison`. Invalid response text is not echoed. |
| Observation `status` | `available`, `timeout`, `unavailable`, `invalid_response`, `missing`, `skipped` or `not_queried`. `missing` is used when a responding backend omits `Version`. |
| Observation `comparison` | `below`, `equal` or `above` the declared stable baseline; otherwise `unknown` or `not_comparable`. |
| Observation `release_kind` | `stable`, `release_build`, `prerelease` or `unknown`. A normal Tailscale backend build suffix such as `1.104.4-t27047635f` retains the full observed text and compares as release `1.104.4`. Other prerelease suffixes are not ordered as stable releases. |
| `consistency` | `mismatch` if observed release numbers disagree, `match` if at least two observations agree, otherwise `unknown`. This compares release identities, not the bytes or build provenance of the binaries. Missing observations remain visible even when the other two match. |
| `scope` | `local_binary_pair_only` for `version`; `binary_pair_and_backend` for `doctor`, even if the socket observation was unavailable or skipped. |
| `assessment` | `local_binaries_meet_baseline`, `meets_declared_baseline`, `version_mismatch`, `below_baseline`, `nonstable_version`, `unknown_baseline`, `unverified_declaration` or `unknown`. Inspect the individual observations as well: for example, a version mismatch can include a below-baseline backend. |
| `advisory_policy` | Always `not_evaluated`; this offline comparison is not a live security assessment. |

`local_binaries_meet_baseline` says nothing about a running process. `meets_declared_baseline` requires all three available stable/release-build observations, matching release numbers, an available declaration and matching package integrity. A missing backend, timeout or malformed version cannot produce that result.

## Exit status and connection health stay compatible

The additional diagnostic does not change existing exit codes. `version` still returns `1` for incomplete or mismatched package sources and `0` otherwise, including legacy metadata or unavailable binaries. `doctor` retains its existing connectivity, component and package-integrity exit-code rules. Its `overall` and `healthy` fields remain operational checks, not a baseline or security verdict.

For example, a connected node can have `overall: healthy` and `runtime_baseline.assessment: version_mismatch`. Read `runtime_baseline` when deciding whether the actual versions agree with the installed declaration; do not infer that from `healthy` or exit code `0`.
