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
