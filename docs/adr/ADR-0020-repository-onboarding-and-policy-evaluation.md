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


## Amendment 2 — reconciliation and readiness (CTO fix gate 2)

**Reconciliation reconciles.** The endpoint named "reconcile" only read a
repository as policy input and wrote a snapshot; the projection itself was
never corrected, so a missed rename stayed wrong forever. Reconciliation
now re-derives the observed attributes on the permanent id, and
installation-level sync re-derives membership — which is the only way
drift from a missed webhook gets fixed. A page set that came back
incomplete fails instead of committing a partial membership as though the
absent repositories had been removed.

**Readiness and compliance are different statements.** `READY` used to be
applied at the end of any successful HTTP flow, including one whose
evidence was full of UNKNOWNs. The contract is now explicit:

- `BLOCKED` — a manual security gate, or a required read permission that
  was never granted. Both are configuration facts, not transient failures.
- `DEGRADED` — the provider answered in part: unreadable subresource, rate
  limit, incomplete reconciliation. "We do not know" is not a state
  anything may be called ready on.
- `READY` — the projection is complete and current.

Governance is orthogonal: a repository whose facts we read *completely*
and which fails policy is `READY` with a `FAIL` verdict. That is a real,
reportable answer. What cannot happen is `READY` on partial evidence.
`last_reconciled_at` moves only when a reconciliation actually completed.


## Amendment 3 — a webhook is not a source of truth (CTO fix gate 3)

Most of this round has one cause: a delivery was treated as though it
re-established facts it never carried.

**One precedence chain owns repository state.** Scattered writers each
decided a state from whatever they happened to know, so a rename could
restore access under a suspended App and a membership event could promote
a repository whose evidence we knew was partial. There is now a single
ordering, and every path derives through it:

    security gate > installation deleted > installation suspended >
    repository access removed > evidence incomplete > accessible

A weaker observation can never override a stronger reason. `restore_access`
writes access only; what that leaves the repository in is the chain's call.

**Provider identity is verified before anything is written.** We ask the
provider about a *path* but we mean a *permanent id*, and those diverge
the moment a repository is renamed, transferred, or deleted and re-created
at the same path. The response's `id` must be an integer and must equal
the id we meant; the owner must be the expected organization. A mismatch
mutates nothing, is audited once, and leaves the repository BLOCKED with
an identity conflict rather than preserving a stale READY.

**The gate is derived from the name, so it is re-derived when the name
changes.** A repository renamed *into* the gated name is blocked before a
single policy subresource is read. A repository renamed *away* from it
stays blocked: a gate may be opened by an observation, never closed by
one. Closing it remains a manual operator process.

**Completeness is its own fact.** `last_reconciled_at` was doing two jobs
— recording the last success and standing in for "the current picture is
complete" — so an old success let a later webhook promote a degraded
repository back to READY. `reconciliation_state` now records what the
current evidence is worth, and only a complete reconciliation transaction
produces READY.

**Membership sync is not policy evaluation.** It reads identity and
attributes with a Metadata-only token, validates what was actually
granted, and never promotes anything. When an attribute the evidence
depended on has moved — the default branch above all, since every
branch-scoped verdict was gathered against it — the stored verdict is
marked stale rather than left looking current.


## Amendment 4 — recorded state outranks later observation (CTO fix gate 4)

**The gate is a row, not a name.** It was derived from `full_name` alone,
so renaming a repository away from the gated name restarted the provider
calls — anyone who could rename could decide when Drake talks to GitHub
about it. Both sources are consulted before anything reaches the network,
and OPEN wins: the recorded gate, and the gate the current name derives.

**Losing sight of a repository clears current evidence.** An access loss
left `reconciliation_state = complete` behind, so the precedence chain
derived READY again the moment access returned — from a reading taken
before we stopped being able to see it. Suspension, removal and uninstall
now clear current evidence outright. The last-good snapshot and the last
successful timestamp are untouched: they record history, not the present.

**Absent is absent.** The repository upsert wrote every column from
defaults, so a rename reported the repository as un-archived, enabled, and
on a guessed default branch — facts the message never carried. Optional
attributes now mean "not carried" unless supplied, the envelope keeps
`private` only when the payload stated it, and a metadata webhook marks
the evidence gathered before it as no longer current.

**Comparisons use the pre-update snapshot.** Membership sync compared
after part of the projection had already been written, so a field the
update had just overwritten compared equal to itself and the change that
invalidated the evidence went unnoticed. The old projection is read once,
before any write, and compared symmetrically in both directions.

**A malformed membership entry fails the listing.** Skipping it made the
repository it described look like one that had vanished, which would have
marked a live repository removed.

**Node identity must agree.** The numeric id remains the identity, but a
node id we already hold and one the provider reports must match; an empty
stored value is a legacy row filled in from the verified response.
