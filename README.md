<h1 align="center">dots-tailscale</h1>

<p align="center"><a href="README.ko.md">한국어</a> · English</p>

Run Tailscale with user-level OpenSSH in a Dots cloud computer. Installation files, node state, and SSH identity live in `/workspace`; runtime sockets, PIDs, and logs are recreated in `/tmp`.

This setup targets environments that preserve `/workspace`. Processes still need a start command in each new environment. This project is independent and is not an official OpenAI or Tailscale tool.

## Demo

![SSH output showing installation-file checks and Tailscale version 1.102.4](docs/assets/dots-tailscale-ssh-demo.gif)

Real SSH output captured from a Dots cloud computer on 2026-10-14, re-rendered as an approximately 15-second animation rather than a screen recording. The tailnet IP and hostname are redacted. It checks existing installation files and shows Tailscale 1.102.4; it does not perform a new installation.

```bash
bash /workspace/.dots-tailscale/start.sh check
/workspace/.dots-tailscale/tailscale version
```

## Quick start

Requires Linux x86_64, Git, Python 3, OpenSSH, and permission to bind local port 22. Follow the [usage guide](docs/usage.md) for all dependencies and SSH setup.

```bash
cd /workspace
HTTPS_PROXY=http://browser-proxy:8889 HTTP_PROXY=http://browser-proxy:8889 \
  git clone https://github.com/abruption/dots-tailscale.git
cd dots-tailscale
bash tailscale-setup.sh check
bash tailscale-setup.sh install
bash /workspace/.dots-tailscale/start.sh login
```

The first login prints an approval URL. Add the client public key to the installed SSH `authorized_keys` file and verify the host-key fingerprint over the cloud computer's GUI terminal before connecting. After environment replacement, start the existing installation with `bash /workspace/.dots-tailscale/start.sh`. The scripts do not register automatic startup.

## Included

| Path | Purpose |
|---|---|
| `tailscale-setup.sh`, `shim.py` | Check, install, start, and explicitly authenticate Tailscale with local SSH |
| `home-persistence-probe.py` | Record HOME and `/workspace` continuity markers |
| `tests/` | Offline regression tests |

See the [English usage guide](docs/usage.md), [Korean usage guide](docs/usage.ko.md), and [MIT License](LICENSE).
