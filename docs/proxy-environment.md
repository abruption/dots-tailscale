# Daemon proxy environment and NoState recovery

[한국어](proxy-environment.ko.md)

v0.9.1 fixes inherited lowercase HTTP proxy variables bypassing the local shim.
Verify published artifacts before installing. The package's Tailscale baseline
remains 1.104.4; this fix does not change installed runtime binaries or identity.

## What happened

In the 2026-11-12 maintainer-assisted Dots observation, package v0.9.0 was intact
and CLI/daemon/backend were 1.104.5, but the backend remained `NoState`. Uppercase
`HTTP_PROXY` and `HTTPS_PROXY` selected `http://127.0.0.1:18089`; inherited lowercase
`http_proxy` and `https_proxy` selected the platform proxy. The daemon log showed
that it actually selected the platform proxy, and the control connection failed
with HTTP 403. Removing the lowercase aliases and restarting the identified daemon
against its existing state restored `Running` and authenticated health.

Tailscale v1.104.5 uses `golang.org/x/net` v0.60.0, whose
[environment resolver prefers lowercase names](https://github.com/golang/net/blob/v0.60.0/http/httpproxy/proxy.go#L75-L89).
An ordinary HTTPS `/key` response through both paths was 200 during the failure;
that did not verify the control-protocol Upgrade path. A 403 or `NoState` alone
cannot diagnose every outage or prove lost authentication.

## Fixed behavior

- Startup sets `HTTP_PROXY`, `HTTPS_PROXY`, `http_proxy` and `https_proxy` to the
  local shim before launching tailscaled, and selects the installed CA bundle.
  This affects the script's children, not the parent shell or global settings.
- Proxy apply and manual runtime updates reject a conflicting nonempty lowercase
  alias before stopping the existing daemon or replacing binaries. Existing
  uppercase and CA checks remain mandatory. Values are not echoed in the error.
- Absent/empty legacy lowercase aliases are accepted and normalized for the new
  process. Candidate and rollback launches use the same verified environment.
  A conflicting environment also rejects a no-change `proxy apply` before its
  no-op return; it is not a repair command for an affected running daemon.
- Application SOCKS/HTTP listeners, update-download proxies, state, keys, update
  budgets and recovery permissions are unchanged. Proxy exclusions such as
  `NO_PROXY` are not redesigned by this patch.

## Existing installations

A source update does not modify an already-running daemon's environment. The
normal package updater still requires its healthy baseline. If the old daemon is
already stuck in `NoState`, recover it before applying the verified v0.9.1 package.
Do not remove state, log out, force reauthentication or downgrade binaries to
bypass this problem.

Use an independent cloud-computer terminal; this recovery interrupts Tailscale
connectivity. First inspect the selected proxy without dumping state contents:

```bash
/workspace/.dots-tailscale/dots-ts doctor
grep -F 'tshttpproxy: using proxy' /tmp/dots-tailscale-$(id -u)/tailscaled.log | tail -n 5
ps -C tailscaled -o pid=,etimes=,stat=,comm=
```

Review/redact any credential-bearing URL before sharing logs. Confirm the target
PID, UID, executable, installation state directory and socket refer to this
installation. Do not kill every tailscaled process or remove a socket blindly.
If a package/runtime update or recovery operation is in progress, resolve its
status first; never delete an update fence to proceed.

After the identified daemon has been gracefully stopped and confirmed absent,
restart the existing installation with lowercase inherited aliases removed:

```bash
env -u http_proxy -u https_proxy bash /workspace/.dots-tailscale/start.sh login
```

The environment prefix alone cannot change a daemon that is still running.
`login` requests `tailscale up` without force-reauthentication. If a new approval
URL appears unexpectedly, stop and review the retained state/device identity
before authorizing replacement. Preserve state and SSH keys throughout.

```bash
/workspace/.dots-tailscale/dots-ts doctor
```

Require `Running`, authenticated status and matching source integrity, then verify
a new external strict-host-key SSH session and Tailscale ping separately. Apply
the published, provenance-verified package using the [update guide](usage.md#update-management).
A source-code rollback is not a runtime/state downgrade. The dated recovery above
is not an unattended recovery or VM-replacement support claim.
