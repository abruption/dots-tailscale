# Changelog

## v0.5.0 — Unreleased (release preparation)

- Add opt-in MCP Events recovery research with authenticated external observations, durable single-use claims, explicit eligibility reasons and bounded reminders when a policy limit clears (#43).
- Add external relay/subscription health checks, preflight reporting, and bounded lifecycle observation tools (#20).
- Document a separately deployed recovery broker with a dedicated service account, restricted Unix socket, bounded request handling and resource limits. Include reviewed deployment helpers and offline fixtures.
- Record boot-change recovery observations, broker restart checks and monitor maintenance limitations to define the experimental support scope (#45).

The four installed executable sources are unchanged from v0.4.0; this preparation updates package metadata and adds research material and documentation. The standard installer and updater do not deploy the broker, register a Dots task, start a watchdog or enable automatic recovery. Existing installations are not changed by preparing or publishing a release.

Research remains experimental. Three observed boot-change recoveries do not establish VM replacement, a recovery-time guarantee or reproducible setup for another account. See the [release preparation notes](docs/releases/v0.5.0.md) for evidence and remaining gates. No publication date or completed v0.5.0 deployment is claimed here.

## v0.4.0 — 2026-10-21

- Add offline `dots-ts version` reporting for package identity, source-file integrity and the separate Tailscale binary version (#27).
- Require a matching source manifest for new installations; preserve unknown metadata for legacy installations instead of guessing a version.
- Add package integrity to `doctor` and document the features in all four languages.

- Add bounded stable-release checks with explicit download proxy selection and validated release manifests (#28).
- Add opt-in, cached local notifications with expiry, deduplication and no network work during startup (#29).

- Add detached manual updates from fixed releases or explicitly reviewed local bundles, with code-only backups, a persistent transaction journal and bounded shim rollback (#30).

The reviewed `0.4.0-dev` candidate passed normal update, intentional startup-failure rollback, checksum-refusal and fresh external SSH/ping checks in Dots. See the [dated validation report](docs/validation/v0.4.0-update-trial.md) for the independent review, CI evidence and limits. The release promotes the same reviewed executable sources with stable package metadata. Publishing does not upgrade existing Dots installations.

## v0.3.1 — 2026-10-19

- Wait for transient startup backend states within a bounded readiness deadline and distinguish authentication, approval and query failures (#25).
- Explain read-only installation/runtime access and socket visibility in the current execution context, with safe GUI-terminal guidance (#26).
- Validate and retain the proxy apply log descriptor before stopping the current daemon, including rollback launches (#32).
- Preserve upstream CONNECT response remainder bytes in raw tunnels (#33).
- Reject malformed upstream WebSocket frames before forwarding payloads or sending pong responses (#34).

These fixes were merged in [PR #35](https://github.com/abruption/dots-tailscale/pull/35). The final combined candidate passed all 178 offline tests on Linux Python 3.11/3.13; focused proxy, shim, CLI and readiness suites also passed on actual Python 3.8/3.13. The candidate has not been deployed to Dots. Startup-readiness [PR #36](https://github.com/abruption/dots-tailscale/pull/36) and execution-context diagnostics [PR #37](https://github.com/abruption/dots-tailscale/pull/37) are merged and included in this release.

See the [release preparation notes](docs/releases/v0.3.1.md).

## v0.3.0 — 2026-10-18

- Add `dots-ts cli` for socket-aware Tailscale commands and a read-only `doctor` that reports installation, process, socket, backend, proxy and optional TCP reachability separately.
- Add opt-in loopback SOCKS5/HTTP proxy configuration with explicit daemon apply, process/socket ownership checks, bounded readiness and rollback. Listeners remain disabled by default.
- Add bilingual private HTTPS API instructions covering network grants, application authentication, TLS and per-process proxy use.
- Source installation now requires `tailscale-setup.sh`, `shim.py`, `dots-ts` and `dots_proxy.py` together. Existing state and keys remain in the installation directory.

- Reject symlinked required files in `doctor`. Require curl 8.4.0 or newer for the bounded HTTPS recipe and clear inherited CA overrides.

Validation: 149 offline tests passed on Linux Python 3.11/3.13 and in Dots. Live native proxy enable/disable, ten authenticated HTTPS/TLS rejection checks through HTTP and SOCKS5, a separate network-policy denial experiment, and final strict-host-key SSH recovery passed. See [PR #24](https://github.com/abruption/dots-tailscale/pull/24) for dated evidence and scope.

Startup after environment replacement remains manual. Direct Dots Bash and GUI/SSH may have different workspace/socket visibility; automatic recovery, natural VM replacement and long-duration fault recovery remain research in [#20](https://github.com/abruption/dots-tailscale/issues/20). Publishing the release does not upgrade existing installations.

## v0.2.0 — 2026-10-14

### Fixed

- Detect the configured service executable and its relevant arguments instead of treating an unrelated process containing the installation path as a running component. Exclude `sshd -t/-T` configuration checks even when options follow the configuration path ([#12](https://github.com/abruption/dots-tailscale/pull/12)).
- Bound CONNECT, TLS, HTTP response, and WebSocket setup; limit HTTP headers to 64 KiB; close sockets and join bridge workers on exit. Established streams remain long-lived, and HTTP uploads can proceed while awaiting a response ([#13](https://github.com/abruption/dots-tailscale/pull/13)).
- Validate WebSocket status, the request-specific Accept hash, Upgrade/Connection headers, selected subprotocol, and extensions before forwarding protocol bytes ([#14](https://github.com/abruption/dots-tailscale/pull/14)).

- Preserve Python 3.8 compatibility in service detection and WebSocket hashing; handle Linux leading-slash paths and PyPy/current Python executable identities.

### Documentation and verification

- Refresh both usage guides, keep English first in the README language links, and show the maintainer-provided Dots terminal screenshot.
- Document SSH key onboarding, independent host-key verification, and staged updates that preserve existing node state and keys.
- Expand offline coverage for process detection, setup stalls and cleanup, valid and invalid WebSocket handshakes, initial payloads, and long-lived connections.

Tailscale remains pinned to **1.102.4**. Startup after environment replacement is manual; auth-key enrollment remains untested. These fixes do not establish a new live deployment or validation across another VM replacement.

See the [release preparation notes](docs/releases/v0.2.0.md).

## v0.1.0 — 2026-10-14

Initial public release of the setup script, TLS/WebSocket shim, continuity probe, bilingual usage guides, and offline regression tests. Release assets include ZIP and tar.gz source archives plus `tailscale-setup.sh` and `shim.py`.
