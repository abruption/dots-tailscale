# Main-branch review and emergency changes

The reviewed policy candidate is [repository-protection.json](repository-protection.json).
The owner approved and applied this policy on 2026-10-29, and API readback
confirmed all four provider-bound checks, zero native approval reviews, strict
base freshness, resolved conversations, and force-push/deletion restrictions.
Private vulnerability reporting was also enabled. This is a dated settings
snapshot, not proof that future settings cannot change. Merging this document
does not configure GitHub. The disposable-PR mergeability experiment is recorded
separately when completed.

The candidate requires changes through a pull request, an up-to-date base,
resolved review conversations and these GitHub Actions checks (app ID 15368):

- `Python 3.11 tests`
- `Python 3.13 tests`
- `Shell syntax and ShellCheck`
- `Python static checks`

These checks run on every pull request in the core workflow. Path-filtered
`Research Python 3.12` and `Research Python 3.13` checks remain relevant evidence
when research changes, but are not unconditional required checks: an unrelated
pull request must not wait forever for a workflow that was not triggered.

The app ID binds a check to **GitHub Actions**, not to the reviewed contents of a
particular workflow revision. A PR can change a workflow while keeping its job
names, and a skipped required job may satisfy GitHub's check evaluation. Review
workflow changes before merge and do not add conditions that silently skip a
required job. Provider/context success is not proof of independently reviewed
test contents. These four names describe classic branch protection; separately
added rulesets may impose more requirements and must be inspected too.

The solo-maintainer candidate requires zero native GitHub approval reviews. This
does not remove the project's requested independent review before opening a
change PR: freeze the reviewed revision, record each reviewer's evidence, address
required fixes, and rerun the affected checks. CLI review is not a GitHub approval
and GitHub does not enforce that procedural review from this settings file.

Force pushes and deletion of `main` are prohibited for ordinary contributors.
Administrator enforcement is deliberately off to retain an emergency recovery
path. An administrator can bypass the protections: this is a real exception, not
a claim that every main update is technically prevented. Use it only for a
documented emergency, retain the exact revision and reason in a linked issue,
perform independent review as soon as possible and verify the required checks
afterward. Do not use the exception as the routine release or merge path.

An agent or automation using the owner's administrator credential has the same
bypass authority as the owner. These settings do not turn that credential into
a restricted contributor identity. Use a separate non-administrator credential
for routine automation if technical enforcement against it is required; this
change does not create, copy or rotate such credentials.

The new `Python static checks` job must land before older PRs can satisfy the
new required context. Merge the reviewed CI-bearing change first, then update
other branches from main. In particular, PR #62 must be rebased or superseded;
do not routinely bypass a missing check just to preserve its old base.

## Apply, inspect and roll back

Use the reviewed `scripts/repository_security.py` helper. `inspect` is read-only;
`apply` requires the operator's explicit `--confirm-owner` flag (not identity
verification) and captures the current policy in a
private operator-selected backup before changing settings. It does not merge a
PR, publish a release, configure automatic deployment or revoke credentials.

Before selecting a native review count of one or more, arrange a second reviewer:
GitHub does not let a pull-request author approve their own changes. A single
maintainer relying on administrator bypass for every PR would defeat that policy.

Verify effective protection and provider-bound check names through the API, then
use a disposable PR to check failing/missing status and review behavior. A JSON
settings readback alone does not prove a real merge was refused. Record that
experiment separately from mocked helper tests. Do not attempt a real merge as
a test. Restoring a prior policy is an owner action, not an automatic response to
a failed check.

If the observation uses an administrator credential, record that privilege and
the remaining bypass path. API/UI merge eligibility is an observation of the
normal protection evaluation, not an actual non-administrator merge rejection.
The selected approval count is zero, so a missing native review is deliberately
not a blocking condition; independent CLI review remains a documented procedure.
