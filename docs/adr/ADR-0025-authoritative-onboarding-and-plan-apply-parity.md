# ADR-0025 — Authoritative onboarding and plan/apply parity

**Status:** accepted (Sprint 12A.1)
**Supersedes in part:** ADR-0020 (repository onboarding), for the import path

## Context

Sprint 11 introduced a reviewable onboarding plan: a repository is analysed
at one commit, a plan proposes what would change, an authorized person
approves that exact version, and apply materialises it.

Apply did not, in fact, materialise it. It walked the **manifest** and
inferred what to do from what already existed. The plan was used only for
the freshness and version checks. The two drifted, and both directions of
the drift were silent:

- A plan could propose something apply never did. An operator approved a
  metadata change, apply took its `link` branch, nothing happened, and the
  screen reported success.
- Apply could change something the plan never mentioned — integration
  placeholders, the repository link, environment↔service attachment. Nobody
  reviewed them because they were never shown.

Two whole capabilities were proposed and never stored: `spec.slos` produced
a plan item and no `slo_definitions` row, and no `service_workload_bindings`
row was ever created, so Service Health, deployment correlation and alert
correlation had nothing to attach to after an import.

Separately, Drake has **two** onboarding paths. The Sprint 5B panel at
`/v1/integrations/github/.../onboarding/import` has working UI buttons and
goes straight from scan to catalog with no plan. The Sprint 11 path has the
plan, the approval and the apply, and no UI at all. The path an operator can
actually reach is the unreviewed one.

## Decision

**1. `/v1/onboarding/sessions/*` is the one authoritative onboarding path.**
Every future onboarding capability lands there.

**2. The approved plan is the instruction set, values included.** Apply
loads the approved plan's items and dispatches each to exactly one
registered handler. It no longer infers work from the manifest, and it no
longer reads *values* from it either.

Binding only field NAMES was not enough. A plan that says "display_name
will change" leaves the new value free to change between review and apply —
a time-of-check/time-of-use gap wearing a review process as a disguise. The
digest check proves the manifest is unchanged; it does not prove the plan
was built from this reading of it.

So each actionable item carries a **canonical mutation payload**: exactly
the values that handler will execute. It is allowlisted per entity kind (an
unknown field is refused, not dropped — dropping it silently would let a
manifest carry something nobody notices is ignored), credential-checked
again even though the manifest policy already refused those shapes, bounded
in size, and covered by the plan digest.

An `update_metadata` item also carries `{field: {before, after}}`, canonical
on both sides, so an approval is informed rather than a list of field names.
Both sides are stored values and both are allowlisted and scanned.

Apply handlers read that payload and nothing else — not the manifest, not
the analysis snapshot, not the live request. A test replaces the document
handed to apply with an empty one and the import still produces exactly the
approved catalog.

**3. Two invariants, enforced by tests rather than by convention.**

```
every actionable item in an approved plan has exactly one apply handler
every persistent mutation apply makes is represented in that plan
```

An actionable item with no handler stops the apply **before any mutation**.
Applying the rest and reporting success would leave a catalog half-matching
an approved plan, with no way to tell which half.

**4. `link` no longer hides a difference.** An existing row is compared
field by field against a canonical form of the manifest's intent:

| Outcome | Meaning |
| --- | --- |
| `conflict` | The manifest would move an identity field. Identity is the catalog's. |
| `update_metadata` | Mutable data differs. The plan names the fields. |
| `link` | An unclaimed row this repository takes over, with nothing to update. |
| `no_change` | Nothing differs. |

Mutable and immutable field sets are explicit allowlists and never overlap.
A manifest may never change a project key, a scope, a tenant model, a
repository ownership, or any RBAC relationship — those are identity and
authority, and a manifest states intent about a system rather than owning it.

**5. Absence of evidence is not evidence of difference.** When Drake cannot
see a row's current metadata it proposes the link it can justify and claims
neither a conflict nor an update.

**6. Nothing is deleted.** There is still no `delete` action. An SLO removed
from a manifest keeps its definition and its evaluation history; retiring
one is a separate, explicit decision and is out of scope here.

**7. Bindings come only from observed workloads.** A binding is proposed
only when the cluster agent has actually seen a matching workload — matched
by namespace and the service's own `workloadSelector`. No observation means
no proposal and, deliberately, **no block**: a project being onboarded for
the first time has no agent report yet, and refusing the import would make
onboarding impossible before the agent runs. Two matching workloads *do*
block, because choosing one would attribute another workload's health,
restarts and deployments to this service.

**8. A deployment source is recorded as evidence and claims nothing.** The
catalog has no column for it, so a `link` apply cannot honour would be
exactly the silently-skipped item this ADR forbids. It is `no_change` with a
stated reason until a schema decision is made.

**9. Real GitHub mutation happens only through the Sprint 12B provider.**
`RecordingProvider` is a local fake and is never presented as production
reality. `github_gitops_pr_enabled` stays off by default, and turning it on
is not sufficient on its own — installation, repository and scope checks
still apply.

**10. The Datalake `manual_env_review` gate stays open.** It is the Datalake
team's to close, after operator review of the tracked `.env`, credential
rotation where necessary and git-history containment.

**11. No deployment until Sprint 12A is complete.**

## The old path stays, for now

`/v1/integrations/github/.../onboarding/import` is **not removed in this
slice**. Removing it before the new UI can do the same job would leave
operators with no way to onboard anything at all.

This is technical debt and a real, temporary risk, stated plainly: while
both paths exist, an operator with `integration.manage` can still import a
repository into the catalog **without a reviewable plan and without the
`onboarding.apply` permission**. Sprint 12A.2 retires it together with the
new UI actions, and that is the slice that closes this gap.

## Consequences

- Adding a plan item kind now requires adding a handler, or apply refuses
  the plan. That is the intended cost.
- A plan-item vocabulary change needs a migration, because the item kinds
  are a database CHECK. Workload bindings therefore reuse the `service`
  entity kind with a distinguishing item key and a `binding` flag in
  `detail`, rather than growing the enum in a slice that was scoped to add
  no migration. A `workload_binding` kind is a candidate for a later one.
- Apply counters (`metadata_updated`, `slo_definitions_created`,
  `slo_definitions_updated`, `bindings_created`, `no_change_count`) are
  additive on the API and reflect committed work only.

## Later decisions (Sprint 12A.1 review)

**Workload bindings are their own plan entity kind.** They briefly borrowed
`service` with a flag in `detail`, which made two genuinely different
decisions read as one, put the handler registry's key at odds with what it
dispatched on, and would have pushed the discriminator into every API
consumer including the 12A.2 UI. Migration `0019` widens exactly one CHECK
constraint and touches nothing else.

**Audit is inside the apply transaction.** `record_audit_event` opens its
own transaction, which is right for anything audited after its work already
committed and wrong here: an apply that changed a catalog with no record of
who asked is worse than one that did not happen, because nobody can
discover it afterwards. `record_audit_event_in` shares the caller's
transaction, and a failing audit fails the apply. That is the intended
trade — fail closed.

**`onboarding_applies` is a transactional apply receipt, not an outbox
row.** An earlier draft of this ADR called it one and cited ADR-0024. That
was wrong, and the wrong word hid a design question. ADR-0024 describes a
notification flow: a durable row written with the domain change and *read
by a worker afterwards*. Nothing reads `onboarding_applies`. It is a
receipt — it commits inside the apply transaction, it records what that
apply did, and it is what a retry of the same request replays. No worker,
no queue, no consumer.

**Onboarding-completion outbox: `not_applicable` for this slice.** No
consumer is defined for "an onboarding finished", so there is nothing for
an outbox to deliver. Adding an event table now would mean a durable,
security-relevant surface with no reader — the same reasoning that kept
`pull_request` out of the webhook allowlist. When a consumer exists, this
decision gets revisited on its own evidence; until then the honest record
is that the question does not arise, not that it was answered.

**Idempotency is concurrency-safe, and proved as such.** Two independent
PostgreSQL sessions, released together by a barrier, race one plan and one
key. A unique constraint arbitrates; one applies, the other returns the
recorded outcome, and the catalog ends with exactly one of everything.

**Deployment source stays informational**, and says so in the payload:
`materialized: false` with the bounded reason
`catalog_projection_not_supported`. A client decides on the code, never on
the sentence beside it. Whether the catalog grows a column for it is a
separate schema decision.

## Later decisions (Sprint 12A.1 acceptance review)

Six problems found by reading the source at `0149a31`, not by running it.
Each one is a case of a guarantee that was claimed but not enforced.

**The approved plan is verified before it is trusted.** Decision 2 says the
plan is the instruction set. It did not say who checks the instruction set
is still the one that was approved. The digest was stored beside the items
and never recompared, so a direct write to `onboarding_plan_items` between
approval and apply changed what apply executed while the approval record
kept pointing at it.

Apply now rebuilds the digest from the stored items — the same canonical
shape, the same ordering, the same serialization used to compute it at plan
time — and compares it to the digest recorded on the plan. A difference
raises `plan_integrity_mismatch` (409) and nothing is written, not even a
receipt, because a request that was never applied must not be replayable as
though it had been.

It runs **twice**, and both are load bearing:

- Once as the first thing `apply` does after finding the plan: ahead of the
  idempotency replay lookup and ahead of the first provider call. Ahead of
  the provider because a rewritten plan should not buy an installation
  token and two GitHub reads before being refused — refusing after spending
  is a way to make refusing expensive. Ahead of the replay lookup because a
  receipt would otherwise be a licence to stop checking: apply once, rewrite
  a plan item, send the same idempotency key, and the recorded answer comes
  back without anything having looked at what the plan now says.
- Once inside the mutation transaction, before the claim and before any
  handler. The provider round-trip between the two takes real time over a
  network, and the stored plan can be rewritten during it.

The check covers every field the digest covers: identity, action,
`reason_code`, the mutation payload and the before/after `changes`. It is
canonical, so re-serializing a payload with its keys in a different order
is not a mismatch — only a change in meaning is. A stored `detail` that is
no longer a JSON object is also a mismatch and says so, rather than
surfacing a `ValueError` as a 500 for something the service detected.

**Apply no longer reads the manifest at all.** Decision 2 claimed handlers
read the payload "and nothing else". One did not: the repository projection
wrote a `manifest_digest` recomputed from the live document. The value now
comes from the plan-bound digest, and `_ApplyContext` no longer carries the
document, so a handler that tried to read it would not compile. The
strip-document test asserts the projected `commit_sha` and `manifest_digest`
alongside the SLO and binding values, rather than only that the apply
succeeded.

**An idempotency key is scoped to its session, and reuse is a conflict.**
Uniqueness was `(plan_id, idempotency_key)`, which made the same key under
a *different* plan version a different request — so a client retrying what
it believed was one call could apply a second, newer approval it never
intended to send. Uniqueness moves to `(session_id, idempotency_key)`.
Reuse within a session under another plan raises `idempotency_key_reused`
(409). Different sessions may reuse a key freely; it is the client's
namespace, not a global one. The decision is enforced by the database
constraint, because a pre-check is exactly what a concurrent pair defeats.

**A retry returns the first answer, on every field.** The receipt stored
three counters and the response carries seven, so a retried apply reported
`metadata_updated: 0` for work that had happened. That breaks the only
promise an idempotency key makes. Migration `0020` stores all seven, and a
retry replays them instead of recomputing anything.

That includes the outcome word. An intermediate version answered
`unchanged` on a replay, which reads as "this request changed nothing" —
but the request did change things, the first time it was sent. A reused
idempotency key is not a new operation with its own result; it is one
committed answer, sent back. So the replayed body equals the first body
exactly, and that only one mutation happened is proved by counting
receipts, audit rows and catalog rows, not by wording one response
differently from the other. No `replayed` flag was added: that would be a
new API field, and this slice is not the place to decide one.

**The claim decision lives in one function.** `claim_apply` inserts the
receipt, and when the insert conflicts it reads what already holds the key
and either replays it or refuses. Keeping that in a single production
helper is what lets the concurrency test drive the real decision instead of
a copy of its SQL — a copied statement proves the constraint works and
proves nothing about the claim → read → refuse path built on top of it.

Receipts written before `0020` never recorded the four extended counters.
They come back as `null` — "not recorded" — not as zero. They are not
reconstructed from audit metadata: audit records what happened, not how
many rows a counter reached, and deriving one from the other would produce
a confident wrong number.

**Migration `0019` refuses to downgrade rather than deleting plans.** Its
downgrade deleted the `workload_binding` items that the narrowed CHECK
would reject. A plan item is the evidence of what somebody approved, and
the digest above is computed over exactly those items — deleting some to
make a schema change fit destroys the record of a decision and silently
breaks the integrity check for the rest. The downgrade now fails closed
with a bounded error naming the count; an operator decides what happens to
those sessions. `0020`'s downgrade refuses on the same grounds when any
receipt carries counters the older schema cannot hold.

## Sprint 12A.2a — the operator UI, and closing the old door

The authoritative path existed and nobody could use it from a browser. The
screen that shipped in Sprint 11 could read a session and its plan but not
create, analyse, approve, apply or cancel one, so every real onboarding
still went through the Sprint 5B panel — the path with no plan, no approval
and no receipt. A rule that is harder to follow than the thing it replaces
is a suggestion.

**Mutation authorization is scoped to the session's own scope.** Every
mutation used to ask two questions and never notice they were about two
different things: "does this principal hold `onboarding.apply` anywhere?"
and "can this principal see session X?". A user with `onboarding.view` on
project A and `onboarding.apply` on project B passed both and could apply
A's plan. Each check was correct on its own, which is why it survived
review.

`repository.authorize_session` now answers one question — may this
principal use this permission on THIS session — and every session endpoint
goes through it. Unknown, invisible and visible-but-forbidden all return
the same 404; a 403 would confirm the session exists. Downward inheritance
is unchanged, because a grant on an organisation covering its projects is
the direction delegation actually runs. The `can_manage` / `can_apply` /
`can_gitops` flags on a session are computed the same way, so an enabled
button and a permitted request are the same claim.

**The state machine is enforced in the database, not in the UI.** Hiding a
button prevents nothing. `lock_session_for` takes the session's row lock
inside the mutating transaction and then checks the action is legal from
that state, so a double-click, a retry and two operators all serialise.
`imported` and `cancelled` are terminal: a repository is revisited with a
new session, which keeps the record of what the old one decided.

An apply's REPLAY is answered before the state check, and the order is
deliberate. After a successful apply the session is `imported`, which is
not a state an apply may start from — but a replay is not starting one, it
is returning the answer to work that already committed.

**The repository picker has its own endpoint.** `GET
/v1/onboarding/repositories` is scoped to `onboarding.manage` and filters
in SQL before paginating, so the list is what the caller can act on rather
than what they can read. Pointing the screen at the integration list would
have been less code and would have shown repositories whose Start button
404s. The cursor orders by `full_name COLLATE "C"`: a locale collation
sorts differently on different databases, and a cursor that means something
else on the next instance is worse than no cursor.

**The manifest draft moved to the session.** Retiring the old panel would
otherwise have removed the only way to obtain a manifest for a repository
that has none — and Drake still cannot write one, because `Contents: write`
is not requested and the GitOps provider is Sprint 12B with its flag off.
`GET /v1/onboarding/sessions/{id}/manifest-draft` builds it from that
session's stored analysis, with no provider call, and serves it as an
attachment with `no-store`. The UI links to it and never renders it: a
manifest is a file to commit, not markup to put in a document.

**The Sprint 5B path is a tombstone.** Five routes answer `410 Gone` with
`legacy_onboarding_retired` — a tombstone rather than a 404, because the
difference tells an operator or an old client that this moved instead of
that they mistyped it. Identical for every repository id, so it cannot be
used to ask which repositories exist. No provider call, no token, no draft
write, no catalog mutation, no success audit.

What was NOT done: the `OnboardingScanner` and `CatalogImporter` classes
still exist and the `github_onboarding_drafts` table still holds its rows.
Retiring an entry point is not the same as deleting the code behind it, and
a migration that dropped historical drafts would destroy a record this
change has no business touching.

**Bounded error codes reach the client.** The error envelope stringified a
dict detail into its message, so a browser wanting to tell `plan_stale`
from `version_conflict` had to parse a Python repr — which meant it did
not, and every 409 looked alike. The handler now promotes a `{"code",
"message"}` detail into `error.code`, and only those two keys, so a detail
object can never invent an envelope field.

**Two counters that must not be confused.** A plan item with
`update_metadata` had been grouped under "No change" in the UI, which hid
the only part of an apply that edits an existing row; it has its own group
now, with `before` / `after` per field and `—` for an absent value. And an
apply counter that a pre-0020 receipt never recorded renders as "Not
recorded" rather than `0`, all the way out to the TypeScript type, which is
`number | null`.

## Sprint 12A.2b — the production boundary, and what Sprint 12A ships as

Sprint 12A is complete. What it delivers is a **read-only-to-GitHub**
onboarding control plane: Drake analyses a repository, proposes a reviewed
plan, and writes the approved result to its own catalog. It does not write
to anybody's repository, and cannot.

    12A.2a  the authoritative browser workflow, and retiring the
            Sprint 5B import path
    12A.2b  the production fail-closed boundary and the
            release-candidate proof
    12B     a real GitHub create-or-reuse provider, and a separate
            decision about switching it on

**`RecordingProvider` cannot exist in a production process.** It was the
startup default. It is a test double, and it returns a pull-request
NUMBER — so a production runtime holding one would report an open pull
request that does not exist, against a branch nobody created, and every
layer downstream would agree with it: the request row says `active`, the
API says `active`, the screen says `active`. A fake that is wrong in a way
nothing can detect is worse than a missing feature.

Two independent guards, because either alone leaves a way in:

- settings validation refuses `github_gitops_pr_enabled` and
  `gitops_worker_enabled` outside local/test, naming both flags, so the
  process does not start;
- the startup wiring constructs no provider outside local/test, so a
  caller that somehow got past the flags still holds nothing to call.

Fail closed at startup rather than at the first request. A worker running
against a fake, or requests accepted and never deliverable, are both
half-enabled states — and a half-enabled write path is worse than a
disabled one, because it looks like it works.

**No new API field was added to say this.** `gitops_pr_enabled` on the
status endpoint already carries it: production can no longer have that flag
on, so the value is `false` there by construction, and the screen's
existing sentence — *repository writes are disabled; no branch or pull
request will be created* — is the truth rather than a placeholder.

**Activation is not a flag change.** Turning GitOps on needs Sprint 12B's
real provider AND a separate CTO decision. The flags alone now refuse.

Unchanged by this slice, and deliberately: no real GitHub write exists,
`github_gitops_pr_enabled` is off, the Datalake `manual_env_review` gate is
open, no migration was added (head stays `0020`), and nothing was deployed.

## Sprint 12B — a real provider, still switched off

`GitHubPullRequestProvider` is the real implementation. Production now gets
it instead of nothing when a GitHub App is configured, and never gets the
recording double. The flags stay off: shipping the code and turning it on
are separate decisions, and this slice only makes the first.

**The contract is one sentence.** The same session, at the same base
commit, with the same content, produces at most one branch, one commit and
one pull request — however many times it is attempted and whatever the
network does in between.

That is not a retry policy, it is why every step reads before it writes. A
POST that timed out may have been applied, so re-sending it is how one
intent becomes two pull requests. `GitHubAmbiguousWriteError` says "unknown
outcome" and the next attempt RECONCILES: is the branch there, is the file
already exactly right, does the pull request already exist. The same
property makes an attempt interrupted anywhere safe to resume.

Mutations do not go through the read client's retry loop. `_request`
retries on transport failure, which is right for a GET and wrong for
anything that changes state, so writes use `_mutate`: one attempt, same
response-size budget, same redirect refusal, and a failure that is reported
rather than repeated.

**What the write path can do, exhaustively.** Create a branch that does not
exist, write `.drake/project.yaml` on it, open a draft pull request. The
client exposes ref CREATE — which cannot move an existing ref — a
single-file write restricted to that path on a `drake/`-prefixed branch,
and pull-request create. Merge, force-push, branch delete and
default-branch commit are not refused by a check; there is no method that
could perform one.

**Refusals that write nothing at all**: a base branch that moved since the
plan was reviewed, a repository whose numeric id no longer matches the
projection, an archived repository, a `drake/` branch carrying content that
is not this proposal, and a token granted less than
`contents: write` + `pull_requests: write`. The last is terminal, because
retrying cannot grant a permission.

**The pull request is always a draft.** The manifest Drake generates leaves
operator decisions as explicit `REPLACE_ME` fields, and a review-ready pull
request would claim it is finished. The body names the fields a person must
fill in and says plainly that merging it imports nothing — it puts the
manifest in the repository, and the import still happens through analyse →
review → approve → apply in Drake.

The draft is re-checked immediately before it leaves: shape, size,
credential-shaped content, and an ALLOWLIST of the placeholders it may
contain. An unexpected placeholder means the generator changed and this
provider was not re-reviewed, which is exactly the kind of thing noticed
only afterwards.

**The pull-request link is composed, not followed.** GitHub returns an
`html_url`; using it would mean the browser navigates wherever a provider
response says. The URL is built from the repository projection and the pull
request number — three values Drake already holds — and anything that does
not look like them produces no link at all.

**Activation.** `github.gitopsPrEnabled` and `github.workerEnabled` are one
decision: the chart and the API both refuse half of it, because one alone
accepts requests nothing delivers or runs a worker nothing can reach.
Turning them on also requires a configured App, its mounted credential
references, and `https://api.github.com` — a configurable API origin plus a
write credential is an exfiltration primitive.

Nothing in this slice contacted GitHub. Every provider behaviour above is
proved against a stateful fake that records which mutations were applied,
including the ones where the response is dropped after the write landed.
