# LogiSlot onboarding — evidence and current state

Every value in
[`packages/contracts/onboarding/logislot.project.yaml`](../../packages/contracts/onboarding/logislot.project.yaml)
came from one of two places: the LogiSlot repository, or a read-only look at
the cluster. This file records which, so a later reader can tell what was
verified from what was intended.

Nothing in this sprint changed LogiSlot. No `kubectl apply`, no Helm
upgrade, no rollout, no scale, no secret or database access.

## Repository

| | | source |
| --- | --- | --- |
| repository | `Duosis-Developer-Team/logislot` | GitHub API |
| default branch | **`dev`** — not `main` | GitHub API |
| stack | FastAPI + Next.js 15 + PostgreSQL 16, Kustomize | `k8s/`, `apps/` |
| deploy | `.github/workflows/deploy.yml`, branches `dev` and `prod`, tag `<env>-<sha7>` | workflow |
| ownership | **none found** — no CODEOWNERS, no team named in the CTO pack | repository |

`k8s/base` holds the shared resources; `k8s/overlays/dev` adds the three
portal deployments (`supplier`, `admin`, `platform` — one web image, three
`LOGISLOT_PORTAL_MODE` values), and `k8s/overlays/prod` does not.

## Environments

### dev — observed

Namespace `logislot-dev` on `duosis-prod-1`, 32 days old. Seven workloads,
all Ready, zero restarts:

| workload | kind | image |
| --- | --- | --- |
| `logislot-api` | Deployment | `logislot-api:dev-fe58a16` |
| `logislot-scheduler` | Deployment | `logislot-api:dev-fe58a16` |
| `logislot-web` | Deployment | `logislot-web:dev-fe58a16` |
| `logislot-web-admin` | Deployment | `logislot-web:dev-fe58a16` |
| `logislot-web-platform` | Deployment | `logislot-web:dev-fe58a16` |
| `logislot-web-supplier` | Deployment | `logislot-web:dev-fe58a16` |
| `logislot-postgres` | StatefulSet | `postgres:16` |

Every workload carries `app.kubernetes.io/name: <its own name>`, which is
what the manifest's `workloadSelector` entries match on.

### prod — declared, never observed

`k8s/overlays/prod` exists, the `prod` branch exists, and the deploy
workflow accepts `prod` as a target. None of that is evidence it runs:

- the `logislot-prod` namespace **does not exist** on the cluster
- **no Deploy workflow run has ever targeted `prod`** — every run is `dev`

So prod is declared in the manifest and reports as
`namespace_not_observed`, not as healthy and not as absent. Drake has no
inventory for it, which is a different statement from "it is broken".

## Observed drift

Running the manifest against the cluster state above:

```
in_sync: false
matched:                7   (all dev workloads)
namespace_not_observed: 7   (all prod expectations)
```

One more difference, visible but deliberately **not** modelled as drift:
the repository's `postgres-statefulset.yaml` requests `10Gi` and the bound
PVC is `5Gi`. Drake's drift layer compares shape — what exists and what
does not — and leaves volume sizing to the storage story, because a report
that mixes "this is missing" with "this is smaller than intended" is harder
to act on than two reports that each mean one thing.

## Decisions that were judgement calls

**`tenantModel: shared_table`** — from the CTO integration pack, which took
it from the LogiSlot domain model (`Tenant`, `Plan`, 1:1 `Facility`, shared
facility-scoped tables). No tenant data was read; this is a shape, not
content.

**Ownership is unverified.** The repository has no CODEOWNERS and no team is
named anywhere, so `team: logislot` is intent.

This previously claimed the value "resolves to an `unmapped` plan item"
until a team exists in a catalog. That was wrong on both counts, verified
against `build_plan`: Drake has no independent owner-team catalog, and an
unrecognised key never blocks — for a new project the association is
created with it, and for an existing project a missing one plans as
`create` and is added. An ownership row is bounded metadata that grants no
permission, so confirming an owner is an operator decision on the manifest,
never something the planner refuses on your behalf.

**`postgres-v1` is not in Drake's metric catalog** (which holds
`fastapi-v1`, `nextjs-v1`, `kubernetes-service-v1`, `tenant-snapshot-v1`).
The datastore is declared with it anyway: the database is real and belongs
in the manifest, and a missing measurement profile is a decision to make
rather than a reason to omit the StatefulSet that holds all of the
application's state. It will surface as `unmapped`.

**The database is both a service and a datastore.** It is a StatefulSet with
its own rollout and readiness, and it is also state with storage and backup
semantics. Declaring only the datastore left a running StatefulSet that no
service described, and the drift report flagged it on every run — a finding
nobody could ever resolve is how a drift report gets ignored.

**Web probes point at `/login`**, a page rather than a health endpoint.
Recorded as found. This manifest describes LogiSlot; it does not redesign
it.

## What is NOT done, and why

This sprint delivers the manifest, the drift layer and their tests. It does
**not** register LogiSlot in Drake's catalog, because two things stand in
the way and neither is a code problem:

1. **Drake's production catalog is empty** — `projects=0`, `clusters=0`,
   `environments=0` — and writing to it is a production database mutation,
   which this sprint's constraints exclude. The manifest's
   `clusterRef: duosis-prod-1` names a cluster that must be registered
   first; until then it is an `unmapped` plan item by design.
2. **No cluster agent is installed** — `cluster_agents=0`, and the
   `drake-agent` namespace does not exist. Drake therefore has no observed
   inventory of its own. The dev workloads above were confirmed by a
   read-only `kubectl` query for this document; they are evidence for the
   manifest, and they were deliberately not injected into Drake as if the
   agent had reported them.

So: **manifest and code are ready; runtime onboarding is not performed.**
Those are separate claims and are reported separately on purpose. When the
agent is installed and the cluster registered, the same manifest applies
without change, and the drift report above is what Drake should produce.
