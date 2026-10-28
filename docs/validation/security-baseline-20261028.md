# Security baseline review — 2026-10-28

This record separates the published package, candidate installation default,
actual installed binaries and host TLS verification. The candidate is
`0.7.0-dev`; publishing or deploying it is outside this record.

## Selected artifact and advisory scope

The official stable metadata reported **1.104.4**, with amd64 archive
`tailscale_1.104.4_amd64.tgz`. The official GitHub release was published on
2026-10-28 at 01:05:26 UTC. Its checksum endpoint reported SHA-256
`624ea093d47d62cf56f17b8332d74b8decda4a1faa610ebc60a1de334b2b3dc9`.
Full archive parsing and actual version probes are checked by the isolated
artifact workflow; endpoint metadata alone is not that verification.

[TS-2026-013](https://tailscale.com/security-bulletins) recommends 1.104.2 or
later, incorporating Go 1.27.3 distrust of compromised eMudhra intermediates.
The selected stable version meets that documented floor. At this review, the
[official changelog](https://tailscale.com/changelog) did not yet contain a
1.104.4 section, and the [SSH investigation update](https://tailscale.com/ssh-investigation-update)
did not identify a final patched version or complete issue-by-issue fix range.
No claim of complete Tailworm/SSH remediation follows from the new pin.

Sources: [stable metadata](https://pkgs.tailscale.com/stable/?mode=json),
[release](https://github.com/tailscale/tailscale/releases/tag/v1.104.4),
[archive checksum](https://pkgs.tailscale.com/stable/tailscale_1.104.4_amd64.tgz.sha256).
These are dated observations; consult current upstream guidance before deployment.

## Existing runtime observation

Before this candidate was developed, a separately authorized normal update on
2026-10-28 used the installed v0.6.0 runtime updater to move an existing node from
1.104.2 to 1.104.4. Its journal reached `applied/complete`, with no error,
cleanup warning or fence. Both CLI and daemon reported 1.104.4; fresh strict
SSH and relay ping succeeded, and public node/SSH identities matched the
pre-update snapshot. The source package remained 0.6.0. External recovery was
paused for cutover, then confirmed active again.

A subsequent read-only compatibility snapshot found both binaries at mode 0700
with one hardlink each, an owned runtime socket, exact supported daemon
arguments, no pending proxy restart and a `Running` backend reporting
`1.104.4-t938213198-gd38c96d6a`. This checks the existing launcher/runtime
contract, not execution of the new migration.

That trial is evidence for the deployed userspace/custom-socket/shim path at
1.104.4. It was not a live test of this candidate's new installer or source
baseline migration, a new 1.102.4-to-1.104.4 live transition, failure injection,
VM replacement, or state downgrade compatibility.

## Read-only host/shim trust review

A read-only probe used the running shim's interpreter and CA override environment
to create a new verifier with OpenSSL **3.5.7 (9 Jun 2026)**. The shim process had
no `SSL_CERT_FILE`/`SSL_CERT_DIR` override. This fresh context found default CA
file/directory paths and held 152 CA certificates and zero CRLs. The probe did
not introspect the existing shim context's already-loaded trust store. These counts do not establish distrust of
a particular certificate or safe revocation handling.

The saved installation bundle did **not** begin with the current OS CA file.
Its recorded modification time was older than the OS file. Therefore the
initially copied bundle and current OS trust source differ; this does not by
itself identify the certificate differences or prove compromise.

- Current OS CA SHA-256:
  `b67dce2c84359d6ba0b0ffbb980c819737cb6fc94aa85ead330ae1e957c69e69`
- Saved bundle SHA-256:
  `eef06a738348e285c073904b4214f6c0ae1874e9872a868d44d63eb04119c332`

The shim independently terminates upstream TLS using Python/OpenSSL. Updating
Tailscale's Go verifier does not establish equivalent distrust in that separate
verifier. The installed local CA, private keys, bundle and trust policy were
not modified. A controlled trust-store/bundle refresh and certificate-specific
policy verification remain separate operator work, not an automatic baseline
migration side effect.

## Candidate validation contract

- Ordinary package `update apply` continues to refuse a changed `TS_VERSION`.
- The explicit candidate `update migrate-baseline` adopts sources only after
  both installed binaries and the identified running daemon are already at
  1.104.4. It supports the declared 1.102.4-to-1.104.4 transition only, with
  matching manifests, frozen source hashes and repeated worker checks.
- Source migration reuses package transaction locks, code backups, readiness
  checks and code rollback. It does not downgrade runtime state or replace
  binaries, credentials or SSH identity.
- Fresh installation checks the archive/checksum and both version outputs
  before placing the pair. A mismatched, partial, symlinked or below-baseline
  existing pair is rejected rather than implicitly upgraded.

Regression and exact-commit CI results are recorded on the implementation PR.
Offline lifecycle fixtures and version-only container probes are not live
migration or application end-to-end validation.
