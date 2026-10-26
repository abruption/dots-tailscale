<h1 align="center">dots-tailscale</h1>

<p align="center"><a href="https://github.com/abruption/dots-tailscale/stargazers"><img src="https://img.shields.io/github/stars/abruption/dots-tailscale?style=flat&logo=github" alt="GitHub Stars"></a></p>

<p align="center">English · <a href="README.ko.md">한국어</a> · <a href="README.zh-CN.md">简体中文</a> · <a href="README.ja.md">日本語</a></p>

Use Tailscale and SSH to reach your Dots cloud computer from your own devices. dots-tailscale installs the tools without root or systemd and keeps the installation, Tailscale node state and SSH identity under `/workspace` so you can reuse them when that directory survives an environment change.

**Current release: [v0.6.0](https://github.com/abruption/dots-tailscale/releases/tag/v0.6.0).** It adds manual Tailscale binary updates and better diagnostics for the separately deployed recovery monitor. This is an independent project, not an official OpenAI or Tailscale tool.

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
  git clone --branch v0.6.0 --depth 1 https://github.com/abruption/dots-tailscale.git
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
/workspace/.dots-tailscale/dots-ts update check --tag v0.6.0 --proxy http://browser-proxy:8889 --json
```

For a verified v0.4.0-or-later installation, follow [package update instructions](docs/usage.md#update-management) to install v0.6.0 first. Older installations without trusted package metadata need the guide's manual review procedure. Running only a new checkout's CLI against an older installed package is not a supported shortcut.

Once the matching v0.6.0 tools are installed, check the official stable Tailscale version:

```bash
/workspace/.dots-tailscale/dots-ts runtime check --json
```

**New installations still start with Tailscale 1.102.4.** Keeping that default allows older package updaters to install the new tools; upgrading the package alone does not upgrade Tailscale. To change the binaries, review the reported version and upstream advisories, then follow the [manual runtime update guide](docs/runtime-updates.md). It covers explicit version selection, offline bundles and transaction status.

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
- `/workspace` preservation is a condition, not a platform guarantee. Runtime sockets, PIDs and logs are recreated in `/tmp`. A new environment still needs a way to execute the start command.
- A local `Running` result is not proof of external reachability. Check a fresh SSH connection and the application paths you use. Upgrading to 1.104.2 is not a claim that Tailworm is fixed.

![Historical Dots terminal showing installation checks and Tailscale 1.102.4](docs/assets/dots-tailscale-ssh-verification.png)

This maintainer-provided screenshot is a historical installation/status check, not a display of the current package version or proof of recovery after VM replacement. The dated trial above describes the later update test.

## Experimental external recovery

Advanced users can explore [Dots-assisted external recovery](docs/experimental-recovery.md). It uses an external monitor, an authenticated MCP Events server and an authorized Dots task to request a start command after a connection failure. The normal installer does not deploy this infrastructure or enable a recovery task.

The current experiment requires external server/SSO setup. Event processing can be delayed; setup on another account and recovery after arbitrary VM replacement remain unverified. Watchdog coordination and opt-in automatic updates are follow-up work, not features enabled by v0.6.0.

## Documentation and project files

- [Usage and troubleshooting](docs/usage.md) · [Manual Tailscale updates](docs/runtime-updates.md)
- [Private HTTPS API example](docs/private-http-api.md) · [Experimental recovery](docs/experimental-recovery.md)
- [Release notes](docs/releases/v0.6.0.md) · [Changelog](CHANGELOG.md) · [MIT License](LICENSE)

| Path | Purpose |
|---|---|
| `tailscale-setup.sh`, `shim.py` | Installation/startup, local OpenSSH and proxy-compatible Tailscale transport |
| `dots-ts`, `dots_proxy.py` | Diagnostics, package/runtime updates and optional application proxy settings |
| `source-manifest.json` | Package version and checksums of the four source files |
| `home-persistence-probe.py` | Record HOME and `/workspace` continuity markers |
| `tests/`, `research/` | Regression tests and separately deployed research tools |
