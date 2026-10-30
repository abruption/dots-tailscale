# Reporting a security problem

Please report suspected vulnerabilities privately through
[GitHub's vulnerability reporting form](https://github.com/abruption/dots-tailscale/security/advisories/new).
You can also open the repository's **Security → Advisories → Report a vulnerability**
page. You need a GitHub account. Reports go to the repository maintainers rather
than the public issue tracker. If the form is unavailable, do not put exploit
details or secrets in a public issue; use a public issue only to ask for a working
private reporting route without describing the vulnerability.

## Supported versions

Security fixes target the [latest stable release](https://github.com/abruption/dots-tailscale/releases/latest).
There are no maintained older release branches or promised backports. Please
include your installed package version even if it is older, and update through
the documented recovery-aware procedure when a fix is available. A development
branch is not a published release.

The package version, installed Tailscale CLI/daemon version, running backend,
Python/OpenSSL and Dots platform are separate components. A package update does
not necessarily update Tailscale binaries or the platform. The
[baseline guide](docs/security-baseline.md) explains those boundaries.

## What helps us investigate

- The package version, Tailscale versions and relevant platform/runtime versions.
- A short description of the impact and the access an attacker would need.
- Minimal, sanitized reproduction steps or a small fixture in an isolated environment.
- Relevant configuration facts, such as whether tailnet policy permits access to
  the affected port. A loopback listener can be reachable through userspace
  networking; its bind address alone does not establish isolation.

Do not include private keys, Tailscale state, authentication tokens, passwords,
OAuth callback secrets, browser profiles, full installation directories or raw
private logs. Do not test against someone else's account, node or server. An
isolated reproduction is preferable to disrupting a live Dots connection.

## How disclosure works

Maintainers will review the report, ask for missing sanitized evidence and agree
on disclosure where possible. This is a small volunteer project; there is no
guaranteed response time or security SLA. A report is not a confirmed
vulnerability until it has been investigated. Fixes and verified scope are
published through security advisories and release notes; do not publish exploit
details before discussing disclosure with the maintainers.

See [published advisories](https://github.com/abruption/dots-tailscale/security/advisories)
for known issues. In particular, v0.7.1 fixes
[GHSA-84m8-25vr-f74m](https://github.com/abruption/dots-tailscale/security/advisories/GHSA-84m8-25vr-f74m)
in versions v0.1.0 through v0.7.0. That advisory does not establish that an
installation was compromised or that every future security issue is resolved.
