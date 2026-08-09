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

**2. The approved plan is the instruction set.** Apply loads the approved
plan's items and dispatches each to exactly one registered handler. It no
longer infers work from the manifest. The manifest still supplies the
*values*, and safely: apply has already re-read it at the approved commit
and checked its digest against the one frozen on the plan, so the document
it reads is byte-for-byte what was reviewed.

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
