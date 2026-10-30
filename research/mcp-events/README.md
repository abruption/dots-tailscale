# MCP Events recovery research (#43)

This directory contains research fixtures, not a supported recovery service.
No component is included in the dots-tailscale installer or release manifest. Full source archives include this directory. See the [experimental operator guide](../../docs/experimental-recovery.md) for setup boundaries, status, stop and diagnosis procedures.

The operator subsequently authorized an ongoing recovery trial. See
[continuous-trial.md](continuous-trial.md) for its event policy, fixed startup
command, claim limits, authentication and supervision. The initial read-only
subscriptions are disabled; the continuous recovery subscription is separate.

## Question

Can an observer outside the Dots VM deliver an event to a subscribed Dots agent,
which then runs the existing startup script in its own cloud computer?

Persisted Tailscale state and an external startup trigger solve different parts
of recovery. The script can reuse surviving state; this experiment investigates
how to request its execution when SSH is unavailable.

## Evidence collected on 2026-10-22

- The signed-in ChatGPT account exposes Add custom MCP server, Server URL,
  Tunnel, and OAuth controls. No plugin or subscription was created during this
  inspection. The existence of these controls does not establish Events support
  for this account until a real subscription succeeds.
- The existing external observer publishes a timestamped JSON result every
  minute. A separate reader can consume it without changing the observer.
- An available external Linux host has Python 3.12, Caddy and browser SSO.
  OAuth discovery is not configured on that SSO service. A new hostname resolves
  via the existing wildcard DNS. Subsequent public-route checks are recorded below.
- The installed local SDK is `mcp==2.3.0`. Its extension API supports custom
  request methods; the ChatGPT Events methods require an explicit implementation.

## Initial experiment results

- 38 local tests passed, including the real SDK HTTP path with the OAuth adapter,
  a signed synthetic callback fixture, subscription and unsubscribe. No OpenAI
  event was delivered by these tests.
- SDK native discovery serialization removed the `events` capability from the
  handler result. A public post-dispatch middleware hook now adds it to the wire
  response; the HTTP test verifies the actual response rather than a Python dict.
- Historical observer replay produced three candidate outages, first observed at
  03:44:51, 11:32:59 and 13:02:15 UTC on 2026-10-21. Candidate times were 03:47:01,
  11:35:10 and 13:04:25 UTC. These are replay results, not delivered events.
- The external Linux host's loopback trial server returned health/metadata HTTP
  200, advertised S256, rejected unauthenticated MCP calls with 401, and rejected
  consent requests without verified identity with 403.
- The operator applied the HTTPS route and consent rule. Public metadata and
  unauthenticated MCP 401 were observed; ChatGPT discovered the OAuth endpoints.
  The first login attempt exposed a consent rule bug: Authelia evaluates the
  query string, and the original resource regex rejected `?ticket=...`. A bounded
  repair and regression tests were applied by the operator. The browser also
  enforced consent form CSP across its redirect chain; the registered ChatGPT
  callback was added explicitly to `form-action`. Account linking then succeeded.
- Deployment validation originally omitted the installed service EnvironmentFile.
  It now runs in a bounded transient systemd unit using that file directly. The
  successful operator apply followed this correction. Secret values are not
  copied into command arguments or public evidence.
- The initial remote process is bounded to six hours. It does not forward real
  monitoring events or execute recovery commands.

## Live synthetic delivery

The actual subscription supplied a callback on `connectors.api.openai.com`.
The initial allowlist rejected it before delivery. After adding that exact
observed origin, the signed callback challenge succeeded and the subscription
became active. No callback URL path or signing secret is included here.

At 2026-10-22 05:12:51 UTC, an explicit operator invocation emitted
`mcp43-20261022-synthetic-01`. One delivery attempt received HTTP 200. The dot
then replied with that exact test ID in its own conversation. This establishes
event reception and a conversational response, not command execution or recovery.

The dot was asked to end only that trial subscription. The server independently
showed zero active subscriptions. A subsequent explicitly generated negative
test had no delivery targets and made no callback request.

A separate read-only trial was subscribed, and its event was accepted at
05:15:10 UTC. The dot later reported that the event data arrived, but no computer
tool was called before a manual status inquiry. Its self-inspection reported
instructions arriving before event data and a requested wait; it did not establish
the cause of the later lack of progress. This is not proof of an approval failure
or lack of computer access, and is not counted as automatic command execution.
The early manual inquiry also limits this trial's usefulness for measuring
uninterrupted asynchronous execution; do not label it a permanent execution failure.

After unsubscribing, a separate direct-message comparison succeeded: the dot
reported UTC 05:21:34, UID 1000, a readable startup script and the socket present.
An independent SSH comparison at 05:15:29 UTC found the same UID and visibility.
The direct-message test does not retroactively make the event trial successful.

A new read-only trial (`mcp43-20261022-readonly-02`) explicitly requests a receipt
followed by the same observations without an intervening user message. Its
callback was received once with HTTP 200 at event time 05:24:04.733799 UTC.
The live dot conversation subsequently displayed a receipt and a read-only
result with UTC 05:43:39, UID 1000, readable startup script and socket present.
This was checked directly in the browser; no follow-up message or resend was
sent between subscription setup and that result. The elapsed event-to-reported-
inspection time was approximately 1,174.27 seconds (19 minutes 34 seconds).
The execution time and environment details are the dot's report, not an
independently captured shell trace. No startup command, outage or new-boot
recovery was tested. This supports delayed event-driven observation, not an
immediate recovery or latency guarantee.

The operator reported roughly ten-minute delays with Gmail-driven dot updates.
This is a separate observation, not a measured latency guarantee for this custom
server. Official MCP Events documentation distinguishes webhook receipt from
asynchronous processing and allows task-level batching. It does not specify a
default ten-minute interval or maximum latency. The new trial is observed for
15 minutes, extended to 20 minutes and then approximately one hour at the
operator's request, without a follow-up message, resend or Run now. The result
arrived during that observation. These are research windows, not a platform SLA.

An optional `get_probe_event(test_id)` read-only lookup fixture was prepared and
tested locally (42 local tests pass). It reads only the authenticated principal's
recorded synthetic event and cannot send events or operate a computer. It has
not been deployed during the uninterrupted `readonly-02` observation. The live
server at that point still exposes only profile and capability reads.

## Stages and distinct success criteria

1. Offline replay: identify bounded outage candidates, with no delivery.
2. Local protocol checks: authorization, subscription ownership, callback
   verification, signature, expiry, deduplication and restart behavior.
3. Synthetic delivery: one `dots.recovery_probe` event. The dot must repeat the
   exact test ID without operating its computer.
4. Read-only execution: ask the dot to report its execution environment and
   whether the startup script is visible. Compare GUI, agent and SSH contexts.
5. Explicit recovery trial: execute the existing startup script within the
   approved trial and verify a new SSH session and Tailscale ping externally.
6. New-boot observation: repeat after an observed environment transition.
   Same-boot success does not complete this stage.

Track callback receipt, dot response, command execution and external recovery
as separate outcomes. A webhook HTTP 2xx proves only receipt.

## Candidate deployment design

Use an external host with an HTTPS MCP endpoint, not a process in Dots.
The first deployed server exposes only a synthetic probe and read-only tools.
No remote-shell tool or automatic recovery loop is part of the first stage.

The current OAuth adapter uses the existing SSO for browser authorization
(`/authorize`) and consent (`/oauth/consent`). Caddy strips caller-supplied
identity headers, authenticates the browser, and forwards the verified identity
on both routes. Authorization requires this identity before allocating a ticket. MCP requests use OAuth bearer
tokens; token exchange uses an authorization code and S256 PKCE. The existing
SSO configuration does not need OIDC enabled for this design.

The adapter must allow one configured local account, exact client/redirect and
resource identifiers, short-lived single-use codes and rotating refresh tokens.
The original synthetic trial used a loopback listener that trusts the local host
and Caddy, including other local processes. The hardened deployment uses a
dedicated service account and a protected Unix socket; see
[security hardening](security-hardening.md). The loopback example in
[deployment](deployment.md) remains a legacy trial configuration. Do not expose the listener
directly or forward unverified identity headers.

Runtime secrets, OAuth state, webhook signing keys and callback URLs belong in a
private directory outside this checkout and outside temporary storage. Public
research evidence must contain only sanitized results.

## Offline replay

`offline_replay.py` reads previously captured observer JSONL and emits dry-run
candidates. It never sends network requests or executes commands. Require three
distinct ordered samples with SSH transport failure and an explicit failed ping.
Only a newer successful SSH observation rearms an emitted outage.

```sh
python3 research/mcp-events/offline_replay.py --help
python3 -m unittest discover -s research/mcp-events -p 'test_*.py' -v
```

## References

- [Research issue #43](https://github.com/abruption/dots-tailscale/issues/43)
- [Lifecycle and recovery evidence #20](https://github.com/abruption/dots-tailscale/issues/20)
- [Official MCP Events](https://developers.openai.com/plugins/build/mcp-events)
- [Official MCP authentication](https://developers.openai.com/plugins/build/auth)
- [Connect a custom MCP server](https://developers.openai.com/plugins/deploy/connect-chatgpt)
- [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)
- [Caddy forward_auth](https://caddyserver.com/docs/caddyfile/directives/forward_auth)
