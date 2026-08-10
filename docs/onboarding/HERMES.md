# Hermes onboarding — evidence and current state

Every value in
[`packages/contracts/onboarding/hermes.project.yaml`](../../packages/contracts/onboarding/hermes.project.yaml)
came from the Hermes repository or from a read-only look at the cluster.

Nothing in Hermes was changed: no apply, no upgrade, no rollout, no scale,
no image change, no MCP route change, and no database or tenant data read.

## Repository

| | | source |
| --- | --- | --- |
| repository | `Duosis-Developer-Team/Hermes` | GitHub API |
| default branch | `dev` | GitHub API |
| stack | FastAPI services, Vite/React frontend, PostgreSQL + TimescaleDB | `k8s/`, `backend/`, audit |
| k8s layout | plain manifests: `k8s/*.yaml` (dev), `k8s/test/*.yaml` (test) — no Kustomize | repository |
| ownership | **none found** — no CODEOWNERS, no team named in the CTO pack | repository |
| tenancy | **`none`** | `hermes_rbac_design.md`, CTO pack |

`hermes_rbac_design.md` states it directly: "roles attach directly to users
— Hermes has no tenant/facility dimension". The CTO pack adds the trap
worth naming: the Azure tenant id in auth configuration is an
identity-provider setting, **not** a business tenant, and is not modelled
as one.

## Two environments, and one namespace that is not one

| environment | namespace | age | state |
| --- | --- | --- | --- |
| dev | `hermes-dev` | 186d | 5 Deployments + 2 StatefulSets, all 1/1 |
| test | `hermes-test` | 157d | 5 Deployments + 2 StatefulSets, all 1/1 |

Both run the **same image digest**
(`839b94925d0fc0f5aaf2ae3153a7b873e8525e5b`) across all five services.

### Why the generic `hermes` namespace is excluded

It exists, and it is not an environment of this project. What is in it:

- one `core-service` Deployment, **0/1 ready**
- its pod has been in **`ImagePullBackOff` for 31 days**
- it uses the mutable tag **`:latest`**, where dev and test both pin a digest
- its Deployment was created 48 days ago (the namespace itself is 157d) and is part of neither deploy path

The decisive detail is the name. `hermes/core-service` is called exactly
what `hermes-dev/core-service` and `hermes-test/core-service` are called. If
namespaces were ever conflated — matching a workload by name rather than by
namespace — a Deployment that has been failing to pull an image for a month
would answer for two environments that are entirely healthy. Its purpose is
documented here; it contributes no environment, no workload binding and no
health input.

`test_generic_namespace_workloads_cannot_become_dev_or_test_evidence` pins
this: feeding the `hermes` namespace's workloads to the drift layer
produces no match for any dev or test expectation.

## Ten workload bindings

Five services × two environments = **10**, matching the canonical scope.

| service | component | dev | test | health |
| --- | --- | --- | --- | --- |
| `auth-service` | api | ✓ | ✓ | `/health` :8000 |
| `core-service` | api | ✓ | ✓ | `/health` :8001 |
| `reporting-service` | api | ✓ | ✓ | `/health` :8002 |
| `frontend` | web | ✓ | ✓ | `/` :80 |
| `hermes-mcp` | mcp | ✓ | ✓ | `/health` :8010 |

The two databases per environment — `auth-db` (postgres:15-alpine) and
`core-db` (TimescaleDB on PG15) — are **dataStores**, not services. They are
state; they get no service→workload binding. Counting them would report
fourteen bindings for a system that has ten.

Evaluated against the observed cluster:

```
matched:               10   (5 services × dev + test)
datastore_matched:      4   (auth-db, core-db × dev + test)
observed_not_expected:  5   (CronJobs)
in_sync: false
```

The five `observed_not_expected` are scheduled jobs the manifest does not
describe: `hermes-api-cleanup` and `task-auto-archive` in both namespaces,
plus `hermes-weekly-backup` in test only. They are reported rather than
filtered, because that is a real finding with a real resolution — declare
them as `component: job`, or decide they are out of model. Completed `Job`
pods spawned by those CronJobs are excluded as churn.

## The MCP boundary

`k8s/09-mcp-service.yaml` documents the boundary in its own comment, and the
deployment matches it:

```
HERMES_PUBLIC_API_BASE = http://core-service/api/public/v1
automountServiceAccountToken: false
```

No database URL. No auth-service URL. No service-to-service credential. No
Kubernetes API permission. The manifest therefore gives `hermes-mcp` **no
datastore dependency** — modelling one would assert a boundary the code
deliberately does not cross — and it is declared `component: mcp`, never as
a datastore.

**A gap worth naming, found while verifying this.** The repository has
`k8s/09-mcp-networkpolicy.yaml`, restricting MCP egress to `core-service`
port 8001 plus DNS. There is **no equivalent under `k8s/test/`**, and a
read-only check found **zero NetworkPolicies in either namespace**. So the
API-only boundary is enforced today by configuration and image contents, not
by the network layer, in both dev and test. That is repository intent that
has not reached runtime. It is recorded here and deliberately not fixed:
this sprint does not mutate Hermes.

Routing differs between the two, which is also recorded rather than
smoothed over. dev serves `/mcp` from its single `hermes-ingress`. test has
two Ingresses: `hermes-mcp-ingress`, host-bound to `hermes.duosis.com`,
carries `/mcp`; the hostless `hermes-test-ingress` does not.

## Direct kubectl is not agent inventory

Everything above was read with `kubectl` for this document. It is evidence
for the manifest and **was not written into Drake as if an agent had
reported it**.

Drake's state is unchanged and still empty: `projects=0`, `clusters=0`,
`cluster_agents=0`, and the `drake-agent` namespace does not exist. There is
no agent snapshot for these namespaces, so there is no freshness to report
and nothing may be shown as observed by Drake.

```
DIRECT_KUBECTL_EVIDENCE != DRAKE_AGENT_INVENTORY
```

## What is NOT done

As with LogiSlot: **manifest and code are ready; runtime onboarding is not
performed.** Registering Hermes needs a write to Drake's production catalog,
which this sprint excludes, and `clusterRef: duosis-prod-1` names a cluster
that is not registered yet. Until an agent is installed Drake has no
observed inventory of its own, so the drift figures above are what Drake
*should* produce once it does — not what it currently reports.

Open items carried forward, none of them blocking:

- ownership unverified → `unmapped` until a team exists in Drake's catalog
- `postgres-v1` is not in Drake's metric catalog → `unmapped`
- five CronJobs undeclared → declare as `component: job` or accept as out of model
- MCP NetworkPolicy present in the repository, absent from both namespaces
