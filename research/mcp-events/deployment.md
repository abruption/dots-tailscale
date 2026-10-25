# Deployment checklist for the synthetic trial

This page documents the original bounded synthetic trial using a **legacy
loopback listener**. It is not the hardened broker deployment guide. For the
dedicated service account and protected Unix socket, use
[security hardening](security-hardening.md) and the
[experimental recovery guide](../../docs/experimental-recovery.md). The examples
below use the current OAuth identity checks even when exercising that legacy
transport. Local tests do not establish a live subscription or delivery.

## Host preparation

Use the selected external Linux host, Python 3.12 and a dedicated loopback
port (18743 for this trial). Put source and its virtual environment under an
operator-owned application directory. Create a mode-0700 runtime directory
outside the repository. Set umask 077 before starting; keep its configuration
and OAuth/event databases mode 0600.

Copy `config.example.json` into that private runtime directory and replace the
origin, account and paths. The callback origins are an explicit allowlist, not
a claim that those are the actual ChatGPT callback destinations. Confirm the
destination origin supplied during subscription; do not log its path or secret.
The production transport rejects nonpublic DNS answers and redirects even for
allowlisted origins.

Use a predefined OAuth public client with S256 PKCE. The example's stable
redirect URI is valid only when the provider advertises issuer identification
and includes the exact issuer in authorization responses. Confirm the actual
redirect shown by ChatGPT before connecting. Do not accept arbitrary redirects.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements.lock
.venv/bin/python server.py serve --config /absolute/private/config.json
```

For this bounded trial keep the process under an operator-owned foreground
session until account linking and unsubscribe behavior are verified. A permanent
systemd service and automatic observer-to-event forwarding are later decisions.

## New HTTPS host only

The following is a template for a Caddy installation with an existing
`tls_origin_dev` snippet and Authelia at port 9091. Replace the example hostname.
Do not replace unrelated site blocks.

```caddyfile
https://dots-events.example.com {
    import tls_origin_dev
    route {
        request_header -Remote-User
        request_header -Remote-Groups
        request_header -Remote-Email
        request_header -Remote-Name

        @consent path /oauth/consent /oauth/consent/* /authorize
        handle @consent {
            forward_auth 127.0.0.1:9091 {
                uri /api/authz/forward-auth
                copy_headers Remote-User
            }
            reverse_proxy 127.0.0.1:18743 {
                header_up X-Dots-Client-IP {client_ip}
            }
        }
        handle {
            reverse_proxy 127.0.0.1:18743 {
                header_up X-Dots-Client-IP {client_ip}
            }
        }
    }
}
```

The selected host's existing Authelia rules default to deny for a new hostname.
Add a rule for the exact trial host and account; retain the existing default and
rules. Example entry under `access_control.rules`:

```yaml
- domain: 'dots-events.example.com'
  resources: ['^/authorize(?:\?.*)?$', '^/oauth/consent(?:/[^?]*)?(?:\?.*)?$']
  policy: 'two_factor'
  subject: ['user:operator']
```

`/authorize` and both consent methods must authenticate. The backend checks the
trusted identity **before allocating an authorization ticket**; protecting only
consent causes account linking to fail at `/authorize`. The Authelia resource
patterns include query strings because OAuth authorization and consent URLs
carry parameters. Token exchange, revocation and public metadata do not use
this browser SSO matcher.

For this legacy loopback example, backend checks also reject missing or
unexpected identity, non-loopback peers, invalid consent tickets and CSRF.
Start Uvicorn with proxy headers disabled; forwarded IP headers must not change
the peer check. The local host and proxy are part of the trusted boundary.

For the hardened Unix-socket deployment, both reverse proxies must overwrite
`X-Dots-Client-IP` with Caddy's `{client_ip}` as shown above. The backend accepts
this admission identity only from its protected Unix socket and rejects missing,
duplicate or invalid IP values on public OAuth POSTs. In legacy loopback mode it
uses the peer address and ignores this header. Never copy an incoming
`X-Dots-Client-IP` or unvalidated forwarding header into the trusted value.

When Caddy sits behind a CDN, configure its trusted-proxy ranges and client-IP
header for that actual upstream only. Verify that arbitrary client-supplied
forwarding headers cannot choose `{client_ip}`. Without trusted-proxy resolution,
Caddy can report the CDN edge address; this groups unrelated clients under one
limit and can deny legitimate requests. Do not solve that by trusting all proxies.

The app admits public token/revocation POSTs before reading their bodies, with
per-client limits and a pool separate from browser authorization/consent. Thus
public requests cannot consume interactive admission slots. This uses the
standard Caddy build; it does not require a custom rate-limit module. It remains
an application-level resource bound, not protection against distributed flooding
or saturation of the CDN/proxy itself.

For an existing Unix-socket deployment, validate and reload the identity-header
proxy changes before starting a backend that requires that header. Verify both
proxy routes; updating only the SSO route breaks public token exchange.

Back up configuration on the server with private permissions and record hashes.
Build candidates on the server, validate both with the installed versions, then
apply only if the current files still match those hashes. Caddy supports reload;
the observed Authelia unit requires restart. Record service health before and
after. On failure, restore the private backups, validate and restore service.
Configuration bodies and credential values must not enter public evidence.

The research SSH account belongs to the sudo group, but `sudo -n` requests a
password. An operator must authenticate locally to apply this part. Do not send
the password through chat or weaken SSO defaults to avoid that requirement.

`apply_routes.py` prepares private backups and candidates on the server,
validates both, then applies the exact host/account addition only with `--apply`.
It requires the synthetic server to be healthy first. On failure it attempts
both configuration and service rollbacks. Authelia validation runs in a bounded
transient systemd unit with the installed service's `/etc/authelia/authelia.env`.
systemd loads the file directly; the helper does not read or copy its values.
A failed validation applies nothing.

## Ordered live checks

1. Public resource and authorization metadata return JSON (no SSO redirects or
   Cloudflare challenge). Unauthenticated `/mcp` returns 401.
2. Both `/authorize` (including OAuth query parameters) and `/oauth/consent`
   require SSO. Forged `Remote-User` headers cannot bypass either route;
   unapproved users fail. Anonymous backend authorization requests allocate no
   ticket.
3. Link ChatGPT using the configured public OAuth client and exact redirect.
   The user completes the existing SSO and explicit consent form.
4. Refresh plugin metadata; confirm the synthetic event is listed.
5. Ask the intended dot to subscribe for one hour and repeat the test ID on
   receipt, without using its computer. Record subscription and challenge
   success without their secret fields.
6. Run one local `emit-probe --request-id <unique-id>` with the same config.
   Record callback receipt separately from the dot's exact-ID response.
7. Unsubscribe, then verify no further delivery. Stop the trial process when
   finished, unless the user has requested continued operation.

Reusing an emit request ID does not create a fresh event. Inspect the receipt
after an uncertain result. Do not generate a new ID just to force another send.
The prototype has bounded explicit attempts, not an automatic retry scheduler.

Only after these checks pass should a separate trial authorize read-only cloud
commands and then the existing `start.sh`. New-boot execution remains a distinct
unverified stage.
