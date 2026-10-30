<h1 align="center">dots-tailscale</h1>

<p align="center"><a href="https://github.com/abruption/dots-tailscale/stargazers"><img src="https://img.shields.io/github/stars/abruption/dots-tailscale?style=flat&logo=github" alt="GitHub Stars"></a></p>

<p align="center">English · <a href="README.ko.md">한국어</a> · <a href="README.zh-CN.md">简体中文</a> · <a href="README.ja.md">日本語</a></p>

Use Tailscale and SSH to reach your Dots cloud computer from your own devices. dots-tailscale installs the tools without root or systemd and keeps the installation, Tailscale node state and SSH identity under `/workspace` so you can reuse them when that directory survives an environment change.

**This guide covers [v0.8.0](https://github.com/abruption/dots-tailscale/releases/tag/v0.8.0); check the release page for published artifacts before installing.** It includes the v0.7.1 security fix that validates CONNECT hosts before certificate generation ([GHSA-84m8-25vr-f74m](https://github.com/abruption/dots-tailscale/security/advisories/GHSA-84m8-25vr-f74m)); v0.7.0 and earlier are affected. New installations use Tailscale 1.104.4. This is an independent project, not an official OpenAI or Tailscale tool.

**Upgrading from v0.6.0 or an older baseline:** packages declaring Tailscale 1.102.4 need the [explicit security-baseline migration](docs/security-baseline.md), after a separate runtime upgrade. Ordinary `update apply` keeps its cross-baseline guard.

## v0.8.0 features

The installation examples below pin **v0.8.0**. Automatic updates and recovery runners remain opt-in; ordinary installation does not register a startup hook or recovery task.

| Feature | What to expect |
|---|---|
| Runtime baseline diagnostics | `version` and `doctor` compare the declared baseline with separately observed versions. A healthy connection or matching baseline is not a security verdict. |
| Stronger package-update verification | Targets from v0.8.0 onward require release provenance, a preinstalled trusted `gh` and an independently prepared trust root. Older unsigned targets need explicit manual review; automatic updates cannot use that exception. |
| Offline release inspection | `update check --bundle` compares a reviewed local bundle without changing the installation. It does not establish fresh publication or publisher identity. |
| Bounded shim admission | The default budget is 64 active connections; an operator-managed launch can select 1–256. Healthy control/DERP streams keep their normal lifetime. |
| Optional update and recovery runners | Both stay off until explicitly enabled. A runner still needs an execution environment; it cannot restart itself after VM replacement. Automatic package updates do not update Tailscale binaries. |

The local recovery ledger is shared only after the older watchdog is stopped and **both** local and external actors use the coordinated entry point. Existing production tasks that call `start.sh` directly still follow the legacy path. The MCP route remains an advanced preview: a second real account has **not** completed setup/recovery reproduction, so this is not a claim of general automatic-recovery support.

Read the [v0.8.0 usage notes](docs/usage.md#development-v080), [automatic updates](docs/automatic-updates.md), [release provenance](docs/release-provenance.md), [coordinated recovery](docs/recovery-lifecycle.md) and [preview readiness](docs/recovery-support-readiness.md) before opting in.

## What you can do

| Task | How it helps |
|---|---|
| Install and reconnect | Run Tailscale with local OpenSSH; reuse existing state and keys when restarting a preserved installation. |
| Check a connection | Inspect installation integrity, processes, socket and backend status with `dots-ts version` and `doctor`. |
| Reach private services | Use tailnet connections and optional local SOCKS5/HTTP proxies for explicitly configured applications. |
| Update the package | Check releases and manually update the scripts with `dots-ts update`; optionally enable cached local notices. |
| Update Tailscale | Use `dots-ts runtime` to check and manually replace the CLI/daemon pair, with persistent progress and recovery records. |

Automatic startup and automatic updates are not enabled. The external recovery experiment described below is a separate setup.

## Quick start

For a **new installation**, use the Dots cloud terminal. You need Linux x86_64, Python 3, Git, OpenSSH and permission to bind local port 22; see the [full dependencies and SSH setup](docs/usage.md). Existing users should follow the next section instead of reinstalling.

```bash
cd /workspace
HTTPS_PROXY=http://browser-proxy:8889 HTTP_PROXY=http://browser-proxy:8889 \
  git clone --branch v0.8.0 --depth 1 https://github.com/abruption/dots-tailscale.git
cd dots-tailscale
bash tailscale-setup.sh check
bash tailscale-setup.sh install
bash /workspace/.dots-tailscale/start.sh login
```

Open the approval URL printed at first login. Add your client public key to the installed SSH `authorized_keys` file, and independently check the host-key fingerprint in the cloud terminal before connecting. The [usage guide](docs/usage.md) covers these steps.

The clone command explicitly uses the platform proxy because direct GitHub HTTPS failed in the tested Dots SSH environment. If downloads still fail, see [download diagnostics and transfer alternatives](docs/usage.md#download-proxy). Keep all four source files and their original `source-manifest.json` together.

When the environment changes but the installation survives, start it again:

```bash
bash /workspace/.dots-tailscale/start.sh
```

## Updating an existing installation

There are two separate versions to manage:

| Command | Updates | Does not update |
|---|---|---|
| `dots-ts update` | The dots-tailscale scripts and package metadata | Tailscale binaries |
| `dots-ts runtime` | The installed `tailscale` and `tailscaled` pair | The dots-tailscale package |

First check the installed package and available package release:

```bash
/workspace/.dots-tailscale/dots-ts version
/workspace/.dots-tailscale/dots-ts update check --tag v0.8.0 --proxy http://browser-proxy:8889 --json
```

For the first upgrade from a pre-v0.8.0 updater, follow the [independently verified bootstrap](docs/release-provenance.md): authenticate the original manifest and all four sources before executing a candidate helper. Matching hashes alone do not authenticate the publisher.

v0.7.0 packages declare the same Tailscale baseline and reach v0.7.1 through the ordinary [package update](docs/usage.md#update-management); applying it restarts the shim with the fix. Existing packages declaring Tailscale 1.102.4, including v0.6.0, must follow the [security-baseline migration](docs/security-baseline.md) to reach v0.7.1. If your installed tools predate v0.6.0, first use the [package update instructions](docs/usage.md#update-management) to obtain v0.6.0 on the same baseline. Older installations without trusted metadata need the manual review procedure. These legacy steps establish the baseline; use the verified bootstrap above for the transition to v0.8.0.

Once v0.6.0 or later tools are installed, check the official stable Tailscale version:

```bash
/workspace/.dots-tailscale/dots-ts runtime check --json
```

**New v0.8.0 installations start with Tailscale 1.104.4.** Existing binaries are not silently replaced by a package update or `install`. To change them, review the reported version and upstream advisories, then follow the [manual runtime update guide](docs/runtime-updates.md). The baseline migration requires both installed binaries and the running backend to be exactly 1.104.4 before the source package changes.

Before applying an update, verify an independent cloud recovery terminal is usable. For runtime updates, pause external recovery jobs and resume them after fresh SSH/ping checks. Once execution of a candidate daemon has been attempted, even a failed startup does not permit automatic downgrade: uncertain failures require the guide's manual recovery procedure. Package code rollback and Tailscale state compatibility are different things.

## Everyday commands

```bash
# Show package integrity and the separate Tailscale version
/workspace/.dots-tailscale/dots-ts version
# Diagnose local installation and connection state
/workspace/.dots-tailscale/dots-ts doctor
# Run the installed Tailscale CLI with its socket
/workspace/.dots-tailscale/dots-ts cli status
# Inspect optional application proxy settings
/workspace/.dots-tailscale/dots-ts proxy show
```

Local SOCKS5/HTTP proxies are off by default. Enabling them requires an explicit daemon restart; they do not configure the Dots browser, ChatGPT connectors or MCP clients automatically. See the [proxy guide](docs/usage.md#development-helpers) and [private HTTPS API example](docs/private-http-api.md).

## What has been checked

- A [live Dots trial](docs/validation/runtime-update-trial-20261026.md) upgraded Tailscale 1.102.4 to 1.104.2 with the reviewed development package, preserved node/SSH identity and confirmed fresh external SSH and relay ping afterward.
- The [v0.6.0 release](https://github.com/abruption/dots-tailscale/releases/tag/v0.6.0) records final-commit core/research tests and isolated official-binary checks. These do not establish all future-version compatibility, live failure recovery or state downgrade safety.
- The [v0.7.0 release](https://github.com/abruption/dots-tailscale/releases/tag/v0.7.0) adds isolated new-install and source-migration tests. A later [2026-10-28 live source-migration trial](docs/validation/v0.7.0-migration-trial-20261028.md) moved one existing installation from package 0.6.0 to 0.7.0, preserving the already-installed Tailscale 1.104.4 runtime and node/SSH identities; a fresh strict-host-key SSH connection and relay ping succeeded afterward. This does not validate a fresh installation, another account, live failure injection, interrupted-transaction repair, VM replacement, runtime state downgrade or all future versions. The [pre-publication baseline and TLS review](docs/validation/security-baseline-20261028.md) remains a separate historical record.
- `/workspace` preservation is a condition, not a platform guarantee. Runtime sockets, PIDs and logs are recreated in `/tmp`. A new environment still needs a way to execute the start command.
- A local `Running` result is not proof of external reachability. Check a fresh SSH connection and the application paths you use. Upgrading to 1.104.2 is not a claim that Tailworm is fixed.

![Historical Dots terminal showing installation checks and Tailscale 1.102.4](docs/assets/dots-tailscale-ssh-verification.png)

This maintainer-provided screenshot is a historical installation/status check, not a display of the current package version or proof of recovery after VM replacement. The dated trial above describes the later update test.

## Experimental external recovery

Advanced users can explore [Dots-assisted external recovery](docs/experimental-recovery.md). It uses an external monitor, an authenticated MCP Events server and an authorized Dots task to request a start command after a connection failure. The normal installer does not deploy this infrastructure or enable a recovery task.

The current experiment requires external server/SSO setup. Event processing can be delayed; setup on another account and recovery after arbitrary VM replacement remain unverified. Watchdog coordination and opt-in automatic updates are implemented in v0.8.0, but are not enabled by v0.7.1 or by ordinary installation. See the preview readiness gates above.

## Documentation and project files

- [Usage and troubleshooting](docs/usage.md) · [Manual Tailscale updates](docs/runtime-updates.md)
- [Private HTTPS API example](docs/private-http-api.md) · [Experimental recovery](docs/experimental-recovery.md)
- [Release notes](docs/releases/v0.8.0.md) · [Changelog](CHANGELOG.md) · [MIT License](LICENSE)

| Path | Purpose |
|---|---|
| `tailscale-setup.sh`, `shim.py` | Installation/startup, local OpenSSH and proxy-compatible Tailscale transport |
| `dots-ts`, `dots_proxy.py` | Diagnostics, package/runtime updates and optional application proxy settings |
| `source-manifest.json` | Package version and checksums of the four source files |
| `home-persistence-probe.py` | Record HOME and `/workspace` continuity markers |
| `tests/`, `research/` | Regression tests and separately deployed research tools |
