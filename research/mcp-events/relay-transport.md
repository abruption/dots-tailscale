# Observation relay transport

The relay can submit minimized observations directly over HTTPS, avoiding an
interactive Tailscale SSH check in the continuous monitoring path. This is an
experimental transport for the #43 recovery trial, not a general remote command
endpoint. `services/relay.example.json` shows the HTTPS configuration.

The receiver stores `monitor_token_sha256` in its private configuration (unset
means the ingestion route is disabled). Rotation replaces that digest and the
private relay token file through the existing approved administration channel.
No live key or token belongs in this repository.

The receiver must expose `POST /monitor/observations` over publicly trusted TLS.
A successful response is HTTP 200 and JSON with `accepted: true` and the exact
submitted `observed_at`. The optional boolean `subscription_active` reports
whether the configured owner has a live, unexpired recovery subscription. It is
persisted in relay health; false triggers the independent notifier after its
grace period. Absent means unknown for compatibility, not inactive. A network success or a generic login page is insufficient.
The relay rejects redirects, TLS verification failures, authentication failures,
responses exceeding 4096 bytes, and mismatched acknowledgements. It does not
follow a redirect with the credential. HTTPS proxy environment variables are not
used: the configured host is contacted directly with the standard verified SSL
context. Connection operations have a 10 second socket timeout, and request and
response reads share a 15 second elapsed deadline. A supervised child enforces
a 20 second wall clock limit on the complete operation, including operating
system DNS resolution. Timeout terminates and, if necessary, kills the child
within bounded cleanup waits. The child returns only bounded JSON status data;
credentials never appear in process arguments or temporary files. Deploy
`bounded_network.py` beside both relay and health notifier. Response headers
remain subject to Python HTTP parser size/count limits.

Use an absolute `bearer_token_file` containing a random base64url token of 32–256
characters, optionally followed by one newline. The file must be a regular file,
owned by the running user, with exactly mode 0600 and no final symlink. Keep it in
a private directory. Provision the same credential's digest at the receiver
through an approved private channel; do not pass the raw token in command line
arguments, environment variables, issue text, or logs. Missing/unsafe token files
fail closed. Rotation requires updating the server digest and private token file;
a 401/403 is exposed as a sanitized authentication failure, not a login retry.

Existing SSH configurations keep working when `transport` is absent or `ssh`.
SSH retains strict host key checking and batch authentication. Diagnostic output
is continuously drained, with only the first 8192 bytes held temporarily for
classification. Raw stderr and authentication links are never written to health
files or logs. Failure classes include `auth_required`, `host_key_rejected`,
`authentication_failed`, `connection_refused`, and `timeout`.

An observation is retried with the same timestamp until it is acknowledged or
becomes stale. The receiver must deduplicate by timestamp. Once a newer sample
exists, the relay sends that fresh sample; it never fabricates historical health
or replays stale samples into incident detection.

`state.health.json` records the last check, latest delivered observation, latest
attempt, first continuous failure time, safe failure class, consecutive transport
failure count, and `stale_delivery` (no delivered sample within 150 seconds).
The first failure time survives relay process restarts and is cleared when the
path is healthy again. A stale/unreadable local observation is also degraded.
Changes into or out of degraded state emit a concise JSON line to stderr. A separate `health_notifier.py --config PRIVATE_CONFIG --once` can run every
60 seconds. Its private config contains `relay_health_file`, `observation_file`,
`state_file`, and `telegram_config_file` (the existing operator-owned 0600
Telegram token/chat_id file). It checks source age and active-subscription
status, allows a 180-second degradation grace and limits alert attempts to
once per hour across process restarts. It never sends through the ingestion
endpoint. The notifier shares the Mac and cannot alert during a Mac outage.
A successful fixture is not proof of real Telegram delivery.
