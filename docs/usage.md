# Usage

[English README](../README.md) · [한국어 사용법](usage.ko.md)

These instructions target a user-level installation in an environment that preserves `/workspace` across environment replacements. The recovery path reuses saved installation files, authentication state, and SSH host keys, without a new login while saved credentials remain valid. Installation does not register a startup hook; run the start command in each new environment.

## Published version and verification

The initial public repository clone and raw downloads of both installation files were verified against the prepared source. [Release v0.2.0](https://github.com/abruption/dots-tailscale/releases/tag/v0.2.0) includes ZIP and tar.gz archives plus `tailscale-setup.sh` and `shim.py` as individual assets. The Git clone example uses `main`; the direct-download example pins both files to `v0.2.0`. The bundled Tailscale version remains `1.102.4`.

![Dots desktop terminal screenshot of installation checks and Tailscale version](assets/dots-tailscale-ssh-verification.png)

This screenshot provided by the maintainer shows `start.sh check` and Tailscale version `1.102.4` in the Dots desktop terminal. The supplied image is used unchanged. It documents the existing installation's files, status, and CLI version, rather than a new installation or a VM replacement.

<a id="download-proxy"></a>

## Download connectivity and proxy settings

In the Dots SSH environment tested on 2026-10-15, direct HTTPS requests to the GitHub repository, raw file, and release asset URLs failed, while requests explicitly using `http://browser-proxy:8889` succeeded. A proxy-backed clone resolved `v0.2.0` to commit `643484`, and the downloaded raw files matched that revision's checksums. These observations apply to that environment; they do not establish a global GitHub block. Browser downloads must be checked separately; these terminal checks do not diagnose browser errors.

If clone or download fails, run these bounded checks in the affected terminal to inspect the proxy path. They discard response bodies and print the final HTTP status without exposing redirected, signed download URLs:

```bash
for url in \
  'https://github.com/abruption/dots-tailscale' \
  'https://raw.githubusercontent.com/abruption/dots-tailscale/v0.2.0/tailscale-setup.sh' \
  'https://github.com/abruption/dots-tailscale/releases/download/v0.2.0/tailscale-setup.sh'
do
  printf '%s\n' "$url"
  curl --fail --silent --show-error --location \
    --proxy http://browser-proxy:8889 --connect-timeout 5 --max-time 20 \
    --output /dev/null --write-out 'HTTP %{http_code}\n' "$url"
done
```

For a direct-path comparison, replace `--proxy http://browser-proxy:8889` with `--noproxy '*'` in the same commands. curl exit code `7` means a connection failed and `28` means the time limit was reached; neither result alone proves a hostname is blocked. Keep certificate verification enabled and use the proxy address actually provided by your environment if it differs.

The settings serve different scopes:

| Setting | Scope |
| --- | --- |
| `HTTPS_PROXY` / `HTTP_PROXY` before `git clone` or `git fetch` | Only that Git command; no global Git setting is changed |
| curl `--proxy` | Only that download or diagnostic request |
| Installer `UPSTREAM_PROXY` | Tailscale package/checksum downloads during installation, and the shim's upstream proxy during startup |
| Local shim at `127.0.0.1:18089` | TLS/WebSocket bridge used by the started Tailscale daemon through its process environment |

The local shim does not configure a global proxy or route unrelated applications through Tailscale. Set `UPSTREAM_PROXY` explicitly for installation and subsequent starts if your environment uses a different upstream address.

## Install and start

Clone the repository from `https://github.com/abruption/dots-tailscale` into `/workspace` so the setup script and `shim.py` are available together. Cloning requires Git and GitHub HTTPS access through the cloud computer's proxy. The setup requires Linux x86_64, Python 3, `timeout`, `flock`, OpenSSL, `ssh-keygen`, and `/usr/sbin/sshd`. Installation additionally requires curl, tar, and sha256sum. Binding port 22 must be allowed for the current user; root or systemd is not required.

```bash
cd /workspace
HTTPS_PROXY=http://browser-proxy:8889 HTTP_PROXY=http://browser-proxy:8889 \
  git clone 'https://github.com/abruption/dots-tailscale' dots-tailscale
cd dots-tailscale
bash tailscale-setup.sh check
bash tailscale-setup.sh install
bash /workspace/.dots-tailscale/start.sh login
```

These proxy settings apply only to the clone command; they do not change global Git configuration. Use the proxy supplied by your environment if its address differs. Keep HTTPS certificate verification enabled.

`check` lists the presence of selected installation files and prints Tailscale status if the daemon socket and CLI are available. It does not start components or run dependency checks. Missing files alone do not cause a nonzero exit: an uninstalled environment can print `missing:` lines and exit `0`. Use successful startup and an actual SSH connection to assess readiness.

`install` places files without starting processes. Existing binaries are retained; a conflicting configuration or script stops installation for manual review. The downloaded Tailscale archive is pinned to the version in the script and checked against its official SHA-256 file.

`login` starts missing components and runs interactive Tailscale authentication, with a 180-second limit. Open the printed URL and approve the intended account and tailnet. No auth key is embedded in these scripts; optional file-based authentication is described below.

After authentication, use this command to start missing components:

```bash
bash /workspace/.dots-tailscale/start.sh
```

For inspection or explicit authentication, pass `check` or `login` to that installed wrapper. Installation generates `start.sh` in the installation directory and copies `tailscale-setup.sh` to `setup.sh`. The generated wrapper derives that directory from its own location and invokes the neighboring `setup.sh`.

| Setting | Default |
| --- | --- |
| `DOTS_TS_DIR` | `/workspace/.dots-tailscale` |
| `UPSTREAM_PROXY` | `http://browser-proxy:8889` |
| `NODE_HOSTNAME` | `dots-sandbox` |

An alternate `DOTS_TS_DIR` must be an absolute path without whitespace. Use consistent settings for installation and subsequent starts. The shim currently listens on port 18089 and SSH on port 22; this is a single-installation setup.

### SSH keys and readiness

SSH listens on `127.0.0.1:22` and uses public-key authentication. Tailscale SSH is disabled; the local OpenSSH server handles shell sessions. If HOME already contains `.ssh/authorized_keys`, installation copies it into the persistent installation. Otherwise, add your client public key to:

```text
/workspace/.dots-tailscale/sshd/authorized_keys
```

Append a client public key as one complete line and set the file to mode `600`. On your client, retrieve the public key from its `.pub` file; never transfer the private key. Verify the server host-key fingerprint in the cloud computer before accepting it on your client:

```bash
ssh-keygen -lf /workspace/.dots-tailscale/sshd/ssh_host_ed25519_key.pub
```

In the Dots terminal, obtain the node address through the installed wrapper:

```bash
bash /workspace/.dots-tailscale/start.sh check
```

This uses the installed CLI and its per-user `/tmp/dots-tailscale-$(id -u)/tailscaled.sock`; no global `tailscale` command or default socket is required. The daemon must already be running to display its address. For a custom installation, use that installation's `start.sh`.

For a first connection, run the following on your tailnet client. At the host-key prompt, compare the displayed fingerprint with the one you obtained independently in the Dots terminal. If they differ, answer `no` and stop; do not enroll the key or remove an existing entry. Do not trust `ssh-keyscan` output alone. Accept it only if they match; this records the key in the client's `known_hosts` file.

```bash
ssh -o StrictHostKeyChecking=ask -i /path/to/client_private_key 'agent@<tailscale-ip>' 'id; pwd'
```

Once the verified key is recorded, use strict checking for subsequent connections:

```bash
ssh -o StrictHostKeyChecking=yes -i /path/to/client_private_key 'agent@<tailscale-ip>' 'id; pwd'
```

Replace the example username if your cloud-computer user differs. Use the address reported by `start.sh check` while the daemon is running. `StrictHostKeyChecking=yes` requires an already trusted host key; it cannot accept a new key interactively. If the key changes, verify the new fingerprint independently before changing the saved entry; do not bypass host-key verification.

`running: Tailscale and local SSH ready` means the backend is Running and the local SSH banner responds. It does not prove client key authentication; test an actual SSH command separately.

| Start result | Exit code |
| --- | --- |
| Running and local SSH ready | `0` |
| Authentication required (`needs_auth`) | `10` |
| Another backend state (`unavailable`) | `11` |
| Setup or component failure | Nonzero, normally `1` |

Ordinary `start` does not invoke login. Previously authenticated state disappearing causes a failure for manual review rather than silent identity replacement. Existing conflicting files, partial CA installations, and missing established host keys also require review. Do not delete the installation directory to update a script: it holds node state and private keys.

The custom TLS/WebSocket shim implements the configured local HTTP CONNECT bridge. Local tests cover initial payload preservation and transport boundaries using isolated sockets. The revised shim was deployed on 2026-10-14 and passed live DERP ping and strict public-key SSH checks through the proxy path. With the previous shim, the maintainer observed retained installation files and successful manual restart after an environment replacement. The revised version has not yet been verified across a later replacement. Changes to the platform proxy can break it.

There is no VM cron, systemd service, or automatic startup registration in this repository. Startup through Dots automation has not been tested. A process loop inside the VM cannot survive destruction of that environment. Start the installation explicitly in a new environment or through a separately verified platform execution mechanism.

### Install without Git

The raw URLs below select the published `v0.2.0` release. To review development sources instead, set `DOTS_TS_REF` to a reviewed commit or `main`. Use the same ref for both files and download them into the same directory:

```bash
mkdir -p /workspace/dots-tailscale-download
cd /workspace/dots-tailscale-download
DOTS_TS_REF=v0.2.0
curl -fsSL --proxy http://browser-proxy:8889 --connect-timeout 5 --max-time 60 \
  "https://raw.githubusercontent.com/abruption/dots-tailscale/${DOTS_TS_REF}/tailscale-setup.sh" \
  -o tailscale-setup.sh
curl -fsSL --proxy http://browser-proxy:8889 --connect-timeout 5 --max-time 60 \
  "https://raw.githubusercontent.com/abruption/dots-tailscale/${DOTS_TS_REF}/shim.py" \
  -o shim.py
```

Continue only after both downloads succeed. Review the downloaded files, then run:

```bash
bash tailscale-setup.sh check
bash tailscale-setup.sh install
bash /workspace/.dots-tailscale/start.sh login
```

The setup needs its own file path and the neighboring `shim.py`, so save both files before execution. A tag or commit selects a fixed revision; `main` can change between downloads.

### Optional noninteractive authentication

If browser-free authentication is needed, supply a Tailscale auth key through a private file, then explicitly run `auth`:

```bash
chmod 600 /workspace/.dots-tailscale/auth.key
DOTS_TS_AUTH_KEY_FILE=/workspace/.dots-tailscale/auth.key \
  bash /workspace/.dots-tailscale/start.sh auth
```

Create the key file separately using a secure method. Do not paste the key into chat, source code, command-line arguments, or Git. `DOTS_TS_AUTH_KEY_FILE` must name an absolute path to a regular file owned by the current user, with no symlink and permissions exactly `600` or `400`. The key is passed to Tailscale as `--auth-key=file:PATH`.

If the backend is already Running, `auth` skips authentication without reading or requiring the key file. This mode still refuses missing state after an established authentication marker; it does not silently replace an existing node identity. The script does not change tailnet tags or ACL policy, force reauthentication, or reset preferences. Auth commands are bounded by a 60-second process timeout and a 45-second connection timeout; they do not fall back to a browser. Actual auth-key enrollment has not been tested. Ordinary `start` never falls back to an auth key automatically.

## Check HOME continuity

Run the probe from the repository checkout in Dots:

```bash
python3 /workspace/dots-tailscale/home-persistence-probe.py
```

The first run creates a random marker in `$HOME/.dots-home-persistence-marker.json` and a baseline, workspace marker, latest result, and history under `/workspace/.dots-persistence-probe`. Later runs compare against that original baseline. The probe reads its own markers and process context, not authentication state or user documents.

| `assessment` | Meaning |
| --- | --- |
| `baseline_created` | First observation recorded |
| `markers_intact` | Both marker identities match the baseline |
| `home_marker_changed_workspace_intact` | HOME marker changed or disappeared while workspace marker remained intact |
| `marker_continuity_changed` | Other marker continuity change |

Marker loss is evidence of marker loss, not proof that the entire HOME directory was reset. Hostname, boot ID, and PID 1 start ticks provide context; boot ID alone cannot distinguish a container replacement from a host reboot. If the workspace baseline disappears too, the next run creates a new baseline, so retain a result outside the VM for comparison.

Custom paths are available through `--home` and `--probe-root`. No periodic execution is registered for this probe.

## Update installed scripts

There is no automatic updater. Stage and review `tailscale-setup.sh` and `shim.py` together outside `/workspace/.dots-tailscale`, using a selected commit or release rather than updating installed files in place from a moving branch. Record that version and both SHA-256 hashes. Validate the staged shell syntax and parse the staged Python source before changing the installation.

Use a separate staging checkout at `/workspace/dots-tailscale` (adjust the path if needed). Replace `<REVIEWED_COMMIT>` with the full 40-character SHA you reviewed. This same block works from a branch or detached HEAD, including repeated updates. It refuses tracked, staged, and untracked changes without discarding them. Fetch, revision, checkout, or validation failures stop the subshell; continue with deployment only if the entire block succeeds. It changes only the staging checkout, not installed files or authentication state. Proxy settings apply only to fetch; keep certificate verification enabled.

<!-- update-staging:start -->
```bash
(
  set -euo pipefail
  cd /workspace/dots-tailscale
  REVIEWED_COMMIT='<REVIEWED_COMMIT>'
  [[ "$REVIEWED_COMMIT" =~ ^[0-9a-fA-F]{40}$ ]] || {
    printf 'Stop: specify the full reviewed commit SHA.\n' >&2
    exit 1
  }
  status=$(git status --porcelain --untracked-files=all)
  [[ -z "$status" ]] || {
    printf 'Stop: preserve and review local changes before updating.\n%s\n' "$status" >&2
    exit 1
  }
  HTTPS_PROXY=http://browser-proxy:8889 HTTP_PROXY=http://browser-proxy:8889 \
    git fetch --tags origin
  target=$(git rev-parse --verify "${REVIEWED_COMMIT}^{commit}")
  git switch --detach "$target"
  [[ "$(git rev-parse HEAD)" = "$target" ]]
  bash -n tailscale-setup.sh
  python3 -c 'from pathlib import Path; compile(Path("shim.py").read_text(), "shim.py", "exec")'
  git rev-parse HEAD
  sha256sum tailscale-setup.sh shim.py
)
```
<!-- update-staging:end -->

Use this deployment sequence when the shim changes:

1. Open the cloud computer's platform GUI terminal, independently of Tailscale SSH. Keep that terminal available for recovery. Record the installed `setup.sh` and `shim/shim.py` hashes, the node address, and the public SSH host-key fingerprint outside the VM. Confirm an authenticated baseline and record it separately from the candidate version.
2. Save private rollback copies of the old `setup.sh` and `shim/shim.py` source files only. Leave authentication state, CA keys, SSH host keys, and authorized keys in place; do not copy state into another installation. Have a bounded, detached restart plan and a rollback plan ready before stopping a process. The detached action must survive loss of the SSH session; this repository does not implement that updater for you.
3. Identify the running shim by its exact command line and installed script path. From the independent terminal, stop only that identified shim PID. Do not use broad process-name termination. Confirm it exited and that no duplicate shim remains. Install the staged `shim.py` at `shim/shim.py` and the staged `tailscale-setup.sh` at `setup.sh`, both with mode `700`.
4. Execute the prepared detached startup, then compare the installed source hashes with the staged hashes. Confirm a single shim and the expected daemon processes. Check Tailscale CLI state and the local SSH banner in the independent terminal, then test external DERP connectivity and a real SSH command with strict host-key verification. Record the results against the candidate version.
5. If validation fails, use the independent terminal to stop only the identified candidate shim, restore the old source files, and run startup again. Recheck the old hashes and baseline connection. Preserve existing state and keys throughout rollback; investigate any unexpected host-key change instead of accepting it.

Do not run `install` as an update shortcut or delete the installation directory. Installation intentionally rejects conflicting files, and the installed directory contains identity material. A setup-script-only update still needs version and hash records and startup validation; it does not establish that a separately changed shim has been loaded.

A successful controlled restart verifies process recovery for that environment, not persistence after VM replacement. The continuity probe remains observational: wait for a natural environment change, retain its results externally, and evaluate authenticated continuity separately. No forced reset or restart is needed for the continuity probe.

## Protect generated files

Keep Tailscale state, host and client private keys, shim CA private keys, auth-key files, generated backups, and runtime logs out of Git. Do not copy one Tailscale node's state into multiple simultaneously running VMs. `/workspace` is not automatically a private location; only use a directory whose access you control.

## Offline regression checks

Run `python3 -m unittest discover -s tests -v` and `bash -n tailscale-setup.sh` from the repository checkout. The onboarding tests require Git, Bash, Python 3, and OpenSSH client/server tools. They use disposable repositories, keys, `known_hosts`, a mocked Tailscale CLI, and an isolated Unix socket. SSH runs over local stdio without a listener; these tests do not authenticate to a tailnet, exercise the live proxy, or validate VM replacement.
