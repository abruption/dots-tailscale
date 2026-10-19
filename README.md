<h1 align="center">dots-tailscale</h1>

<p align="center">English · <a href="README.ko.md">한국어</a></p>

Run Tailscale with user-level OpenSSH in a Dots cloud computer. Installation files, node state, and SSH identity live in `/workspace`; runtime sockets, PIDs, and logs are recreated in `/tmp`.

This setup targets environments that preserve `/workspace`. Processes still need a start command in each new environment. This project is independent and is not an official OpenAI or Tailscale tool.

## Demo

![Dots terminal screenshot showing installation checks and Tailscale version 1.102.4](docs/assets/dots-tailscale-ssh-verification.png)

A screenshot provided by the maintainer from the Dots desktop terminal. It shows existing installation checks and status, plus Tailscale version 1.102.4. The supplied image is used unchanged. No new installation is performed in this screenshot.

```bash
bash /workspace/.dots-tailscale/start.sh check
/workspace/.dots-tailscale/tailscale version
```

## Quick start

Requires Linux x86_64, Git, Python 3, OpenSSH, and permission to bind local port 22. Follow the [usage guide](docs/usage.md) for all dependencies and SSH setup.

The clone command explicitly uses the Dots proxy because direct GitHub HTTPS failed in the tested Dots SSH environment. See [download connectivity and proxy settings](docs/usage.md#download-proxy) for diagnostics and the scope of these settings.

```bash
cd /workspace
HTTPS_PROXY=http://browser-proxy:8889 HTTP_PROXY=http://browser-proxy:8889 \
  git clone --branch v0.3.1 --depth 1 https://github.com/abruption/dots-tailscale.git
cd dots-tailscale
bash tailscale-setup.sh check
bash tailscale-setup.sh install
bash /workspace/.dots-tailscale/start.sh login
```

The first login prints an approval URL. Add the client public key to the installed SSH `authorized_keys` file and verify the host-key fingerprint over the cloud computer's GUI terminal before connecting. After environment replacement, start the existing installation with `bash /workspace/.dots-tailscale/start.sh`. The scripts do not register automatic startup.

## v0.3.1: diagnostics and private HTTP access

Install all four source files from [v0.3.1](https://github.com/abruption/dots-tailscale/releases/tag/v0.3.1) to use these helpers.

```bash
/workspace/.dots-tailscale/dots-ts doctor
/workspace/.dots-tailscale/dots-ts cli status
/workspace/.dots-tailscale/dots-ts proxy show
```

`doctor` reads local diagnostics without changing state. `cli` selects the installed Tailscale binary and socket. Optional loopback SOCKS5/HTTP proxies are disabled by default and require a separately reviewed daemon restart to enable. See the [usage guide](docs/usage.md#development-helpers) and [private HTTP API recipe](docs/private-http-api.md) for configuration, access policy, and validation limits. These proxies do not automatically configure ChatGPT connectors, MCP clients, or the Dots browser.

## Included

| Path | Purpose |
|---|---|
| `tailscale-setup.sh`, `shim.py` | Check, install, start, and explicitly authenticate Tailscale with local SSH |
| `dots-ts`, `dots_proxy.py` | CLI, diagnostics, and optional per-process outbound proxy configuration |
| `home-persistence-probe.py` | Record HOME and `/workspace` continuity markers |
| `tests/` | Offline regression tests |

See the [English usage guide](docs/usage.md), [Korean usage guide](docs/usage.ko.md), and [MIT License](LICENSE).
