# Deployments: what is running, and how much Drake can prove

Drake records one revision per observed workload generation, and grades
how much of this chain it actually saw:

```
COMMIT SHA → WORKFLOW RUN → IMAGE DIGEST → KUBERNETES WORKLOAD
           → ROLLOUT → HEALTH WINDOW
```

Anything short of the whole chain is never shown as verified.

## Where the data comes from

The cluster agent's existing bounded inventory records — nothing new talks
to a cluster. Sprint 8 adds two fields to what the agent already collects:

- workload `spec_summary.containers` — container **name and image
  reference** only. No env vars, args, mounts or secret references.
- pod `status_summary.container_images` — `image` plus `imageID`, which is
  the only place the digest a node actually pulled is observable.

The agent's read-only boundary and its label/annotation allowlist are
unchanged; no new RBAC verb was added.

## Evidence states

| State | Means |
| --- | --- |
| `verified` | Commit, workflow run and digest all observed, and the declared digest matches the running one |
| `partial` | Some links observed, but the chain does not close |
| `unverified` | Only a mutable tag. It may be correct; Drake has no evidence |
| `conflict` | The spec declares one digest and the node pulled another. Drake does not pick a side |

Provenance comes from allowlisted labels/annotations the agent already
passes through:

```
drake.duosis.com/commit-sha
drake.duosis.com/repository
drake.duosis.com/workflow-run-id
drake.duosis.com/workflow-provider    (defaults to github)
```

`app.kubernetes.io/version` is read as a fallback commit, but only when it
is SHA-shaped — a version string is not provenance and is dropped.

**No URL is ever stored or accepted.** A run link is composed by the
server from `DRAKE_WORKFLOW_RUN_BASE_URL` plus the typed repository and run
id, and only when both are shaped correctly. There is no GitHub API call in
this sprint: if workflow evidence is absent, the deployment stays
`partial`/`unverified`, which is the honest answer.

Repositories are matched through the existing project catalog. Nothing is
hard-coded.

## Rollout states

Read from the workload's own numbers, most structural first:

| State | When |
| --- | --- |
| `failed` | `Progressing=False` or `ReplicaFailure=True` |
| `pending` | `observedGeneration` has not caught up to `generation` |
| `healthy` | updated, ready (and available) all reached desired — or scaled to zero on purpose |
| `stalled` | still incomplete past the bounded window (15 min) |
| `degraded` | some replicas ready, some not |
| `progressing` | update under way |
| `unknown` | the observation is incomplete |

`pending` rather than `degraded` for a controller that has not seen its own
spec: a one-second-old rollout is not a problem.

## Identity and idempotence

`(cluster_id, workload_uid, revision)`. The UID survives a rename and an
agent reconnect, and the revision is the workload's own generation — so
re-reading the same workload updates the row rather than inventing a
deployment. A short disconnect produces nothing.

A new generation creates a new revision, linked to the one it replaced.

## Health correlation

For a completed rollout, Drake reads the same curated signals over a
bounded window before and after — request rate, error ratio, p95 latency,
restart delta, scrape availability — through the Sprint 5 broker. No PromQL
is composed here.

| Verdict | Means |
| --- | --- |
| `improved` | at least one signal moved the good way, none the bad way |
| `stable` | movement within tolerance (20%) |
| `regressed` | any signal moved the bad way, or an incident opened in the window |
| `insufficient_data` | nothing was measurable |

**This is temporal correlation, not causation.** Drake never claims a
deployment caused an incident, and the UI says so on the screen. Missing
telemetry degrades the comparison to `insufficient_data`; it never damages
the deployment record.

## API

```
GET /v1/deployments                         # scope-filtered, cursor-paginated
GET /v1/deployments/filters                 # the accepted vocabulary
GET /v1/deployments/{id}                    # detail incl. health comparison
GET /v1/deployments/{id}/revisions          # revision timeline
GET /v1/deployments/{id}/incidents          # incidents in the window after
```

Filters: project, environment, service, cluster, workload kind, rollout
state, evidence state, `started_within` (`24h`/`7d`/`30d`). An unknown
value is 422, never silently ignored.

Visibility: the bound service's scope (`environment.view`), or — for a
workload nobody has bound yet — the cluster's (`cluster.view`). Anything
outside scope answers 404, and the total excludes it.

**Read-only.** There is deliberately no deploy, rollback, restart or scale
endpoint: mutating a cluster needs a different authorization story than
"can read deployments".

## Running the ingest

Off by default:

```
DRAKE_DEPLOYMENT_INGEST_ENABLED=true
DRAKE_DEPLOYMENT_INGEST_INTERVAL_SECONDS=120
DRAKE_DEPLOYMENT_INGEST_BATCH_SIZE=200
DRAKE_WORKFLOW_RUN_BASE_URL=https://github.com
```

One cycle at a time across replicas (Redis lease), bounded batch, and a
workload that fails to ingest costs only itself.

## Troubleshooting

**A deployment shows `unverified`.** The workload runs a mutable tag and
carries no provenance labels. Pin the image by digest, or set the
`drake.duosis.com/*` labels at deploy time.

**A deployment shows `conflict`.** The spec declares one digest and the
node reports another — usually a tag repointed after the pod started.
Drake reports both rather than choosing; `kubectl rollout restart` resolves
it, and Drake will record the next generation.

**No deployments appear.** Check the ingest flag, then that the agent is
reporting Deployment/StatefulSet/DaemonSet inventory for the cluster. A
workload without a `generation` is skipped, because without one every
re-read would look like a new release.

**Health comparison says `not compared yet`.** It is computed once the
rollout has completed and the window after it has closed (~35 minutes).
