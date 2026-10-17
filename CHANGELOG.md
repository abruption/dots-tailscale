# Changelog

## v0.3.0 — unreleased

- Add `dots-ts cli` for socket-aware Tailscale commands and a read-only `doctor` that reports installation, process, socket, backend, proxy and optional TCP reachability separately.
- Add opt-in loopback SOCKS5/HTTP proxy configuration with explicit daemon apply, process/socket ownership checks, bounded readiness and rollback. Listeners remain disabled by default.
- Add bilingual private HTTPS API instructions covering network grants, application authentication, TLS and per-process proxy use.
- Source installation now requires `tailscale-setup.sh`, `shim.py`, `dots-ts` and `dots_proxy.py` together. Existing state and keys remain in the installation directory.

Live proxy enable/disable, authenticated tailnet API and real policy-denial acceptance checks remain required before release; isolated tests are recorded in the implementation PR.

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
