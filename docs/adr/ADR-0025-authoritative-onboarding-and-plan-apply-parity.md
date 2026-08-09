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
