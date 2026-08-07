# ADR-0020 — Repository onboarding identity and read-only policy evaluation

Status: accepted (Sprint 5A)
Builds on: ADR-0011 (unknown/stale/partial state semantics), ADR-0014
(catalog authority and scope topology), ADR-0017 (atomic projection and
honest freshness), ADR-0019 (GitHub App trust boundary).

## Context

Repositories get renamed, transferred between owners, made private,
archived, and removed from an installation. Any model keyed on
`owner/name` corrupts itself the first time someone renames a
repository: Drake would either duplicate the row or silently attach
history to the wrong project.

Separately, Drake must answer "is this repository governed safely?"
without becoming the thing that changes the answer.

## Decision

### 1. Identity is GitHub's permanent id, never the name

Repositories are tracked by **provider + GitHub repository id** (the
numeric id, alongside the GraphQL node id where available). Name, full
name, owner login, visibility, archived/disabled flags and default
branch are **observed attributes** that get reconciled, not identity.

A rename therefore updates attributes on the same row and leaves the
audit trail intact. A transfer updates the owner attributes on the same
row. Re-delivery of the same installation/repository event cannot create
a second row, because the id carries a uniqueness constraint per
provider.

### 2. Onboarding is an explicit state machine

```
DISCOVERED → VALIDATING → READY
                 ↓          ↓
              BLOCKED   DEGRADED
                 ↓          ↓
              DISABLED  (recoverable)
```

- `DISCOVERED` — GitHub told us the repository exists in an installation;
  nothing has been validated yet.
- `VALIDATING` — reconciliation is in flight.
- `READY` — reconciled, policy evaluated, access confirmed.
- `DEGRADED` — reconciled at least once, but something is currently
  wrong and recoverable: stale data, a rate limit, a missing optional
  permission.
- `BLOCKED` — Drake must not proceed: a manual security gate is open, a
  required permission is absent, or ownership validation failed.
  `BLOCKED` is never entered implicitly by a transient error.
- `DISABLED` — access was removed (uninstalled, suspended, repository
  removed from the installation). The row and its history **stay**.

Transitions live in one module with one entry point, so every path is
testable and every transition is audited with a reason code. Nothing
outside that module writes the state column.

### 3. Access removal is soft state, never deletion

Losing access is an observation, not an erasure. Uninstall, suspend, or
repository removal moves the row to `DISABLED` and records when and why.
The row, its policy history and its audit records survive so the next
installation can be reconciled against what was true before.

### 4. Policy evaluation is read-only and fail-closed

The engine reads; it never writes to GitHub. Each rule produces exactly
one of `PASS` / `WARN` / `FAIL` / `UNKNOWN`, and every non-pass carries a
stable rule id, severity, the expected state, the observed state,
whether it blocks, and secret-free remediation guidance.

The decisive rule: **absence of evidence is never evidence of
compliance.** A missing permission, a rate limit, a timeout, a 404 on a
protection endpoint, or any unreadable API result yields `UNKNOWN` (and
where it matters, `BLOCKED`) — never `PASS`. Evidence is deterministic
and free of secrets so two evaluations of unchanged inputs produce
identical snapshots.

Drake's own eight required check names are **not** imposed on other
repositories. Profiles describe *classes* of gate (a build gate, a test
gate, a security-scan gate); the minimum security baseline is central,
and the profile only widens what a given repository type must also
satisfy.

### 5. A manual security gate outranks automation

A repository may carry an operator-controlled security gate. While that
gate is open the repository is `BLOCKED`, and Drake performs **no**
credential read, no installation access, no live reconciliation and no
API call against it — the block is enforced before any network path, not
after. Closing the gate is an explicit, audited operator action; nothing
in the reconciliation or webhook path may close it automatically.

This is why `Datalake-Platform-GUI` stays blocked in Sprint 5A: its
tracked `.env` finding requires authorized operator review, credential
rotation where necessary, git-history containment, `.gitignore`
remediation, and a safe `.env.example` contract first.

## Consequences

- Renames, transfers and reinstalls are boring operations instead of
  data-integrity incidents.
- The projection can be honestly incomplete: `UNKNOWN` rules and
  `DEGRADED` repositories are first-class, visible states rather than
  silent gaps.
- Because evaluation never writes, a wrong rule costs a wrong report —
  never a changed repository setting. Remediation stays a human decision
  in this sprint.


## Amendment — evidence sources (CTO fix gate)

Two corrections to how evidence is gathered, both of which could have
produced a PASS that was not true.

**Ruleset evidence comes from the effective-rules endpoint.** The ruleset
list endpoint returns summaries with no `rules` member, so an entry there
says a ruleset exists — not what it enforces, nor whether it applies to
the default branch. Rule evidence now comes from
`GET /repos/{owner}/{repo}/rules/branches/{branch}`, which reports the
rules actually in effect for that branch and already accounts for
organization-level rulesets, enforcement status, and target conditions.

**A partial answer is never a PASS.** An aggregate rule that spans several
objects — production environments especially — states something about all
of them. If any one was unreadable, that statement cannot be made, so the
verdict is UNKNOWN with the per-object reason recorded. A *known*
violation still outranks an unknown: FAIL survives, PASS does not.
