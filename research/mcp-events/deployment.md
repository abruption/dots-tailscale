# Deployment checklist for the synthetic trial

Status: preparation only. Local tests are not evidence of a ChatGPT event
subscription or delivery. No recovery command is exposed by this service.

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

        @consent path /oauth/consent /oauth/consent/*
        handle @consent {
            forward_auth 127.0.0.1:9091 {
                uri /api/authz/forward-auth
                copy_headers Remote-User
            }
            reverse_proxy 127.0.0.1:18743
        }
        handle {
            reverse_proxy 127.0.0.1:18743
        }
    }
}
```

The selected host's existing Authelia rules default to deny for a new hostname.
Add a rule for the exact trial host and account; retain the existing default and
rules. Example entry under `access_control.rules`:

```yaml
- domain: 'dots-events.example.com'
  resources: ['^/oauth/consent(?:/[^?]*)?(?:\?.*)?$']
  policy: 'two_factor'
  subject: ['user:operator']
```

Both consent methods must authenticate. Backend checks also reject missing or
unexpected identity, non-loopback peers, invalid consent tickets and CSRF.
Start Uvicorn with proxy headers disabled; forwarded IP headers must not change
the peer check. The local host and proxy are part of the trusted boundary.

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
2. Forged `Remote-User` headers cannot authorize consent; unapproved users fail.
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
