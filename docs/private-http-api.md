# Access a private HTTP API from Dots

English · [한국어](private-http-api.ko.md) · [简体中文](private-http-api.zh-CN.md) · [日本語](private-http-api.ja.md) · [Usage guide](usage.md#development-helpers)

This recipe covers an HTTPS API on another tailnet device, called from a Dots terminal process through the optional local Tailscale proxy. This recipe requires all helper files from v0.3.0 or newer; v0.2.0 and earlier do not include them.

## Connection path and prerequisites

```text
Dots curl process -> 127.0.0.1:8080 HTTP CONNECT proxy
                 -> Tailscale -> private API device:8443 -> HTTPS API
```

The optional SOCKS5 alternative is `socks5h://127.0.0.1:1080`; the `h` delegates destination-name resolution through the proxy. HTTP CONNECT likewise passes the destination hostname to the proxy. Do not substitute the daemon shim on port `18089`: it serves the Dots platform proxy path for Tailscale itself, not application requests.

Prepare these independently:

- A Dots Tailscale node authenticated to the intended tailnet, with the complete helper set from v0.3.0 or newer installed.
- A private API device reachable in that tailnet, with an HTTPS listener bound to its intended private/Tailscale interface and port. A listener bound only to the API device's `127.0.0.1` is not reachable from Dots. Avoid exposing it on a public interface as a shortcut.
- A destination name that resolves through the chosen proxy and matches the API certificate. Use a trusted public CA or explicitly provide your private CA file to the client; an IP address also needs a matching certificate identity.
- Tailnet permission for only the required source, destination, and TCP port, plus server-side bearer authentication and API authorization. Tailscale connectivity does not replace API authentication.
- An authenticated, read-only `/health` endpoint that returns only a small response without sensitive information, for example `{"status":"ok"}`. Replace the sample endpoint with your server's equivalent, not a data-export or mutation endpoint.

## Limit network access

The following valid JSON is also valid JSON5 for a Tailscale policy. It illustrates a source tag, destination tag, and one allowed TCP port; names and administrator ownership are placeholders for your policy. Merge the relevant entries into the existing policy rather than replacing the entire policy file.

```json
{
  "tagOwners": {
    "tag:dots-client": ["autogroup:admin"],
    "tag:private-api": ["autogroup:admin"]
  },
  "grants": [
    {
      "src": ["tag:dots-client"],
      "dst": ["tag:private-api"],
      "ip": ["tcp:8443"]
    }
  ]
}
```

An authorized tailnet administrator must assign the source tag to the intended Dots device and the destination tag to the API device. These scripts do not assign tags or edit policy. Grants are additive: this narrow entry does not revoke any broader existing grants or ACL rules. Review those entries, tag ownership, and API host firewall rules before treating the resulting access as restricted. Use the tailnet policy editor's validation and policy tests for your actual node identities. See the official [grants documentation](https://tailscale.com/kb/1324/grants).

## Enable the local listeners deliberately

First read the [configuration and apply procedure](usage.md#development-helpers). Proxy listeners are off by default. Configuration alone saves desired state without restarting the running daemon:

```bash
/workspace/.dots-tailscale/dots-ts proxy show
/workspace/.dots-tailscale/dots-ts proxy configure --socks-port 1080 --http-port 8080
```

Use different ports. This implementation rejects a shared SOCKS5/HTTP port initially. The desired configuration lives at `/workspace/.dots-tailscale/outbound-proxy.json`; runtime listeners must be recreated after environment replacement. Applying the configuration is a separate, guarded daemon restart that can cut off SSH. Secure the independent Dots GUI terminal and rollback plan before following `dots-ts proxy apply` instructions. Recheck external strict-key SSH after applying.

These are loopback listeners for local processes, not authenticated gateways for other machines. Any process with access to those listeners may attempt connections. Keep API authentication and narrow tailnet permissions even if only your own script uses the proxy.

## Make one bounded authenticated request

**curl 8.4.0 or newer is required.** Starting with this version, `--max-filesize` also limits responses with unknown sizes, including chunked responses. The example checks the version before prompting for a token or making an API request and stops if the version is unavailable or too old.

Run this in the Dots terminal after the HTTP listener is applied. Replace the URL with your HTTPS service's hostname and port. The example uses Python's standard library to read a bearer token without echo and feed the header to curl on stdin. The token is not placed in command-line arguments, URLs, shell history, environment variables, or files. It remains in process memory until the request finishes. Do not enable HTTP traces or print the token.

```bash
python3 - <<'PY'
import getpass
import json
import os
import re
import subprocess

url = "https://api.example.internal:8443/health"
proxy = "http://127.0.0.1:8080"
ca_file = ""  # Optional trusted private CA file path; blank uses system trust.
child_env = os.environ.copy()
for name in ("NO_PROXY", "no_proxy", "CURL_CA_BUNDLE", "SSL_CERT_FILE", "SSL_CERT_DIR"):
    child_env.pop(name, None)
try:
    version_result = subprocess.run(
        ["curl", "--disable", "--version"],
        text=True, capture_output=True, timeout=5, env=child_env,
    )
except (OSError, subprocess.TimeoutExpired, UnicodeError):
    raise SystemExit("Could not verify curl version; curl 8.4.0 or newer is required")
version_match = re.match(r"curl ([0-9]{1,5})\.([0-9]{1,5})\.([0-9]{1,5})(?:\s|$)", version_result.stdout)
if (version_result.returncode != 0 or version_match is None
        or tuple(map(int, version_match.groups())) < (8, 4, 0)):
    raise SystemExit("curl 8.4.0 or newer is required for the response size limit")
token = getpass.getpass("API bearer token: ")
if not token or any(c in token for c in "\r\n"):
    raise SystemExit("Invalid token input")
command = [
    "curl", "--disable", "--silent", "--show-error", "--proto", "=https",
    "--proxy", proxy, "--noproxy", "",
    "--connect-timeout", "5", "--max-time", "20", "--max-filesize", "4096",
    "--header", "@-", "--write-out", "\n%{http_code}", url,
]
if ca_file:
    command.extend(["--cacert", ca_file])
try:
    result = subprocess.run(
        command, input="Authorization: Bearer " + token + "\n",
        text=True, capture_output=True, timeout=25, env=child_env,
    )
except subprocess.TimeoutExpired:
    raise SystemExit("Request timed out")
except (OSError, UnicodeError):
    raise SystemExit("Could not execute curl or decode its response")
finally:
    token = None
if result.returncode:
    raise SystemExit("curl failed (exit %d); check network, DNS and TLS separately" % result.returncode)
body, separator, status = result.stdout.rpartition("\n")
if not separator:
    raise SystemExit("Missing HTTP status")
print("HTTP", status)
if status != "200":
    raise SystemExit("Unexpected HTTP status; response body omitted")
try:
    healthy = json.loads(body) == {"status": "ok"}
except ValueError:
    healthy = False
if not healthy:
    raise SystemExit("Unexpected health response; body omitted")
print("Expected read-only health response received")
PY
```

The request does not follow redirects; verify the canonical endpoint rather than forwarding a credential to another location. HTTPS certificate and hostname verification stay enabled. The response is capped at 4 KiB and only its status and expected-shape result are printed; the sample server returns no sensitive information. Do not point this check at a response containing private records.

To use SOCKS5 instead, change only `proxy` to `socks5h://127.0.0.1:1080` after enabling that listener. `--noproxy ''` prevents curl's bypass list from skipping the chosen proxy. Clearing `NO_PROXY`/`no_proxy` and inherited CA overrides (`CURL_CA_BUNDLE`, `SSL_CERT_FILE`, `SSL_CERT_DIR`) affects only the curl child process, not your shell or other applications. For a private CA, explicitly supply a reviewed `ca_file` through `--cacert`. No system proxy, browser setting, MCP configuration, or ChatGPT plugin/connector setting is changed. A separate client must explicitly support and configure this proxy; arbitrary integrations are not automatically able to reach this API.

## Verify each layer and the denied cases

`dots-ts doctor` reports local layers. An optional `doctor --target api.example.internal:8443` is a bounded TCP probe, not an authenticated HTTPS check. A successful TCP connection does not prove that a certificate is valid or that a token is authorized.

| Check | Expected result and interpretation |
| --- | --- |
| Local installation, socket, daemon, and proxy configuration | `doctor` and `proxy show` distinguish saved configuration from running state; inspect their layer results |
| Allowed source to intended host and port | TCP succeeds; the authenticated, read-only health check returns HTTP 200 and the agreed response without sensitive information |
| Missing or deliberately invalid API token | Server rejects the request, normally HTTP 401; some services use 403. No sensitive body should be returned |
| Device or destination port outside the allowed policy | Connection is denied or times out before an API response; verify policy tests and absence of broader permits |
| Untrusted CA or hostname mismatch | TLS verification fails; fix the certificate or trust configuration, never disable verification |
| Destination name cannot be resolved through the proxy | DNS/proxy error; investigate name resolution separately from tailnet permission and API authentication |
| Configuration disabled and applied | Local listener is unavailable; `disable` alone records desired state and is not proof the active listener stopped |

An HTTP 401 means an HTTP server answered; it is not a network-policy denial. A timeout alone cannot identify ACL, firewall, routing, or DNS as the cause. Use the smallest useful check and avoid recording tokens or private response bodies.

<a id="current-validation"></a>

## Validation status

Live checks on 2026-10-18 (KST) confirmed native SOCKS5 and HTTP proxy traffic, disabling both listeners, and strict-host-key SSH reconnection. Across both proxies, all ten HTTPS checks passed: valid authentication (`200`), missing and incorrect tokens (`401`), a forbidden endpoint (`403`), and an untrusted CA (TLS failure). A separate before/block/restored check confirmed tailnet policy denial; the original policy was restored. Temporary API servers and credentials were removed. See the [proxy results](https://github.com/abruption/dots-tailscale/pull/24#issuecomment-6243157252), [policy results](https://github.com/abruption/dots-tailscale/pull/24#issuecomment-6243247818), and [HTTPS results](https://github.com/abruption/dots-tailscale/pull/24#issuecomment-6243275452).

These checks do not establish recovery after a natural VM replacement or automatic startup. In the compared environment, Dots direct Bash saw `/workspace` read-only and could not see the runtime socket, while the GUI terminal and SSH shared writable files and the running socket. Use an independent GUI terminal for applying proxy changes; do not assume direct-agent execution can restart the service. Lifecycle research remains open in [#20](https://github.com/abruption/dots-tailscale/issues/20#issuecomment-6243404537).
