# ADR-0027 — Runtimes Drake does not run, and dependencies it does not manage

Extends ADR-0020 (repository onboarding) and ADR-0025 (plan/apply parity),
which assumed the thing being onboarded runs in a cluster Drake can see. Not
everything does, and the ones that do not are the ones most likely to be
modelled dishonestly — because the schema has a shape for Kubernetes and no
shape for anything else, and a shape that fits is very tempting.

## The problem, stated precisely

A project hosted on a platform-as-a-service, with a managed data platform
behind it, has:

- no cluster
- no namespace
- no Deployment, Service, Pod or StatefulSet
- no agent, and therefore no inventory
- a database that exists but that Drake neither runs nor can measure

Every one of those is an absence. The failure mode is to fill absences with
the nearest Kubernetes noun: the hosting platform becomes a "Deployment",
the managed database becomes a "StatefulSet", and an invented namespace
holds them together. The result validates, renders, and is false.

**Drake models the real runtime. An application does not migrate to satisfy
Drake's schema.** If Drake cannot describe a runtime truthfully, that is
Drake's gap to close, not the application's architecture to change.

## What is already true

The schema and planner get the structure right, and this ADR records it so
it is not lost:

- `environment.runtime` is an enum of `kubernetes | external`
- `clusterRef` and `namespace` are **optional** — only `name`, `runtime`,
  `branch` and `criticality` are required
- `build_plan` skips cluster, namespace and workload bindings entirely when
  `runtime != "kubernetes"`
- the drift layer skips non-Kubernetes environments when computing expected
  workloads

So an external environment already produces an environment row and nothing
Kubernetes-shaped. That behaviour was correct and untested, which is the
combination worth worrying about: one deleted `continue` would give an
externally hosted project an `unmapped` cluster binding, and `unmapped` is
a **blocking** action — the project would wait forever on a cluster that is
not part of its architecture.
`apps/api/tests/test_external_runtime_plan.py` now pins it from both sides.

## What is missing (the G10 gap)

Structure is supported. Semantics are not. The manifest has no way to say:

| Fact | Today |
| --- | --- |
| which platform hosts this environment | no field — the environment is `external` and nothing more |
| that a datastore is **managed by a provider** | `dataStore.engine: postgresql` is indistinguishable from a database Drake runs |
| where health could be observed, if anywhere | no field; `health` on a service is a path, which presumes something scrapes it |
| when the runtime was last verified | no field |
| whether a claim is repository intent, owner-confirmed, or provider-observed | no field |

And one active untruth: `metricsProfile` is **required** on every service.
A service with no metrics source must still name a profile, so it names one
that nothing honours. A required field with no honest value is a field that
manufactures false claims.

Status: **PARTIALLY_SUPPORTED**. An external project can be onboarded
structurally; it cannot yet be described accurately.

## Decision

1. **External runtime is a first-class runtime, not a degraded Kubernetes
   one.** `cluster`, `namespace`, `agent` and `workload` are **not
   applicable** for it — a distinct state from *missing*. Drift must never
   report an inapplicable field as absent, and the absence of a cluster is
   not an external runtime's fault condition.

2. **Managed dependencies are declared, not measured.** A datastore a
   provider operates is a dependency with a provider identity and a
   verification status. It is never presented as an instance Drake runs,
   and provider usage appearing in a repository is not evidence that the
   production connection is healthy or even active.

3. **Health for an external runtime is `unknown` until something observes
   it**, and freshness is `unavailable` until there has been a successful
   observation. A public page returning `200` is evidence about that page.
   It is not evidence about background jobs, scheduled functions, or a
   managed database, and it must not be promoted into a project-level
   verdict.

4. **Catalog-only is not an operating model.** Recording a project that
   produces no signal is acceptable as a temporary placeholder *only* if
   every surface shows `unknown`/`not applicable` honestly. If any surface
   would render it as healthy, connected or fresh, the record is not
   created — an entry that lies is worse than an absence, because an
   absence is visibly an absence.

5. **No manifest may carry a value invented to satisfy a required field.**
   If truthful expression requires a schema change, the schema changes.

## G10 acceptance criteria

An implementation closes this gap when all of the following hold:

- an environment can name its hosting provider without naming a cluster
- a datastore can be marked provider-managed, with the provider identified
  and the instance explicitly not Drake-run
- `metricsProfile` is no longer required where no metrics source exists,
  and omitting it yields `not_configured` rather than a fabricated profile
- a health source is optional and, when absent, health resolves to
  `unknown` and freshness to `unavailable` — never to healthy
- every external claim carries a verification status distinguishing
  repository intent, owner confirmation, and provider observation
- existing Kubernetes manifests validate and plan **unchanged**, proven by
  the LogiSlot and Hermes suites
- `not applicable` is rendered distinctly from `missing` wherever drift and
  health are shown

## Security contract for any future external health source

Recorded now so that an implementation cannot quietly choose weaker rules.
Reaching out to an address is a server-side request, and a server-side
request that accepts arbitrary targets is an SSRF primitive.

- default-deny allowlist of endpoints; no tenant-supplied arbitrary URL
- `https` only
- reject loopback, link-local, private ranges, cloud metadata addresses and
  cluster-local names — **after** DNS resolution, and again after every
  redirect, so a rebind cannot land somewhere the first check allowed
- bounded timeout, bounded response size, bounded redirect count
- `GET`/`HEAD` only; no query parameters, no cookies, no credentials, no
  userinfo in the URL
- response bodies are never persisted; only status category and timing
- audit records category and time, never response content
- error output reveals no internal network detail

Nothing in this ADR authorises building that probe. It fixes the rules it
must follow if it is built.

## Consequences

Projects on external runtimes stay **deferred** rather than being onboarded
inaccurately — a visible gap instead of a false record. G10 is a schema and
domain change with migration and backward-compatibility impact, so it is its
own piece of work, not a rider on an onboarding sprint. Until it lands, the
honest states are `deferred`, `unknown` and `not applicable`, and this ADR
exists so that using them is a recorded decision rather than an omission.

---

## Implementation note (G10, migration 0021)

The decision above is unchanged. This records the exact contract that
implements it, because the ADR named concepts and the code had to name
fields.

**Manifest schema.** `environment.hostingProvider` is a closed enum, allowed
only on an external environment. An external environment may no longer
carry `clusterRef` or `namespace` at all — previously optional, now
**refused**, so it cannot quietly acquire Kubernetes identity.
`dataStore` gains `dependencyClass` (`in_cluster` default),
`provider` and `verification` (`repository_intent` default), and
`measurementProfile` is required only for `in_cluster`.

**`metricsProfile` is required exactly when the project has a Kubernetes
environment.** A document-level conditional, not a per-service one: services
are project-level in this schema, so requiredness could not key off an
environment's runtime directly. Kubernetes projects therefore keep every
requirement they had, and a project with no Kubernetes environment may omit
the field. Absent means `not_configured`; it is never rendered as a profile.

**Persistence.** `environments.hosting_provider` is nullable with two check
constraints — a closed vocabulary, and provider-only-on-external.
`service_definitions.metrics_profile` becomes nullable, where NULL means "no
metrics source". `hosting_provider` joins the immutable environment fields:
changing where something runs is a relocation, and a relocation is a
conflict for a person, not a silent metadata update.

**API.** `not_applicable` is an additive list on the environment payload
rather than a new type for `cluster`/`namespace`. Older clients keep reading
those fields exactly as before; a newer client can tell "this runtime has no
such concept" from "nobody recorded one". Conflating those two is what made
an external application render as a Kubernetes one that had lost its
cluster — which the environment page did, for any environment without a
cluster, until this change.

**Truth table.**

| health source | last observation | status | freshness |
| --- | --- | --- | --- |
| none | — | `not_configured` | `unavailable` |
| configured | none | `unknown` | `unavailable` |
| configured | present | as observed | `fresh` |

`last_observed_at` is set only by an observation. Importing a manifest is
not one, and there is no import-time input to the function that computes it.

**Still out of scope, and unchanged by this:** no probe worker, no provider
adapter, no provider credential, no background polling. The SSRF contract
above governs an implementation that does not exist yet.

---

## Review corrections (follow-up to the first implementation)

Six findings from CTO review, all fixable, all fixed. Recorded because
several of them were claims the first delivery made that were not true.

### Compatibility is a tightening, not purely additive

The first report said "v1alpha1 unchanged because additive". That was wrong
in one specific way and is corrected here: **an external environment may no
longer carry `clusterRef` or `namespace`**, which were previously merely
optional. A document of that exact shape used to validate and no longer
does. That is validation tightening.

`packages/contracts/test/external-runtime-contract.test.ts` demonstrates it
directly rather than describing it, so the incompatibility cannot be lost.

`v1alpha1` is retained, on two grounds recorded here so the decision can be
challenged: no manifest of that shape exists anywhere in the repository or
in any accepted catalog record (production holds zero projects), and
`v1alpha1` is by name an alpha contract whose compatibility policy permits
tightening. Had an accepted document been affected, this would have needed a
version bump instead.

### The service/environment invariant, stated

`metricsProfile` requiredness is keyed on "does the project have a
Kubernetes environment", which forces every service in a MIXED project to
declare one. That follows from the invariant the code actually holds:

> **Every service is expected in every Kubernetes environment.**

`expected_workloads_from_manifest` implements it, and both onboarded
projects depend on it — Hermes is 5 services × 2 environments = 10
bindings. Under that invariant a service in a mixed project genuinely does
have a Kubernetes deployment, so requiring a profile is not a false claim.

The alternative invariant — a service scoped to a subset of environments —
would need a schema field that does not exist. It is a coherent future
change and deliberately not made here; until it exists, a project that
wants a genuinely external-only service should be a separate project.
Mixed-runtime contract tests pin both directions.

### Migration reversibility is conditional

"Reversible" was too strong. Precisely:

| path | result |
| --- | --- |
| upgrade | additive; every existing row preserved |
| downgrade, no NULL metrics profile | succeeds |
| downgrade, any NULL metrics profile | **intentionally refused** |

Both paths are proven against a real PostgreSQL in
`test_migrations_audit_integration.py`, including that a refused downgrade
moves nothing — no revision change, no lost row.

### Managed dependencies now decide something

`dependency_is_workload()` was called only by its own test, while
`expected_datastores_from_manifest()` turned **every** datastore into an
expected Kubernetes workload regardless of class. In a project with a
Kubernetes environment, a `managed_data_platform` therefore became a
missing workload — permanently, and unfixable by anyone reading it. The
drift path now uses the typed decision, and a mixed-runtime regression test
fails without the fix.

### Health is two axes, and the source is a third field

The first model reported `not_configured` as the health STATUS, which reads
as a property of the application rather than of Drake's configuration; and
freshness had no threshold, so any observation was `fresh` forever and
`stale` was unreachable. Now:

- `health.source.status` — is anything configured to look
- `health.status` — the observed verdict, which survives ageing
- `freshness` — `fresh` / `stale` against an explicit `stale_after`, or
  `unavailable` when there has been no observation at all

Health and freshness are independent: unhealthy+fresh and healthy+stale are
both reachable, and both are asserted. The API serves this computed verdict
and the web renders it; both previously hard-coded the strings.

### What is NOT implemented

Recorded rather than glossed:

- **Managed dependency persistence and API round-trip.** `dataStores` are
  still manifest-and-drift only: no table, no plan item, no API field.
  `provider` and `verification` survive parsing and drift, and nothing
  further. This is `FOUNDATION_ONLY`, not implemented.
- **Health observation.** No probe worker, no provider adapter, no polling.
  The state machine is real and wired; nothing produces an observation, so
  every external record honestly reports `unknown` / `unavailable`.

---

## Sprint 13F.2 — dependencies reach the catalog

The previous section labelled managed-dependency persistence
`FOUNDATION_ONLY`. It no longer is: `dataStores` now travel manifest →
validation → plan → transactional apply → `project_dependencies` → API →
project view.

**Its own table.** A managed data platform is not a service (no workload,
no replicas, nothing to restart) and not an in-cluster datastore Drake
operates. Either host table would have meant a nullable discriminator plus
every reader remembering to check it.

**Identity is `(project, dependency_key)`**, so a repeated import reconciles
and the same name under another project is a different thing. Class and
engine are immutable — a change there is a different dependency wearing the
same name, which is a conflict for a person. Display name, provider,
verification and scope are mutable and audited like any other metadata
update.

**An import may only ever record `repository_intent`.** The column accepts
the higher levels so an out-of-band confirmation or a real observation can
set them, but `clamp_verification_for_import` refuses promotion on the
import path. A manifest asserting `provider_observed` is a repository
claiming Drake observed something, which is not evidence that Drake
observed anything. Proven end-to-end: the fixture manifest asks for
`provider_observed` and the row reads `repository_intent`.

**No credential material**, and `connectionSecretRef` is dropped rather than
stored. It is only a reference name and the schema already forbids a value
there, but nothing downstream needs it, and a field nobody reads can only
ever leak.

Four defects surfaced while wiring it, each of which had made the chain
silently incomplete:

- an absent `metricsProfile` planned as `unmapped`, which BLOCKS an apply —
  so an external project could pass validation and never be importable at
  all. Absent is now `not_configured`; a declared-but-unknown profile is
  still `unmapped`, because that is somebody naming a key Drake does not
  have.
- `hosting_provider` was added to the immutable environment fields and to
  the proposed metadata but not to the metadata loaded FROM the database,
  so every re-import of an external project conflicted with itself.
- `dependency` was missing from the plan-item entity vocabulary, so the
  INSERT failed inside the apply transaction and rolled the whole import
  back on a constraint no reviewer could see in the plan.
- `link` had no handler, so a second import of an UNCHANGED dependency made
  the entire plan unappliable.

**Migration 0022** is additive. Its downgrade drops the table — safe
because every row is re-derivable by re-applying the manifest that created
it — but it refuses when plan items record a dependency decision, rather
than deleting somebody's plan history to fit a narrower constraint.

Still out of scope and unchanged: no probe worker, no provider adapter, no
credential, no polling. Health for a dependency is `unknown` and freshness
`unavailable` because nothing observes it, and importing a manifest is not
an observation.
