# Runbook — Drake's first standard Kubernetes deployment

Target namespace: `drake-prod`.

**This is a procedure, not a status report.** It does not record what has or
has not been done to any cluster — such a note is correct for a day and
misleading afterwards. Assume nothing about prior runs:

- Discover the real cluster state read-only, every time, before acting.
- An existing resource is not an error. Do not recreate one, and do not
  treat its presence as a failure.
- Every step below is written to be safe to re-read and re-evaluate against
  whatever the namespace currently holds.

**The datastores come first and are not Helm's.** PostgreSQL, Redis, the
PostgreSQL PVC and the application Secret are provisioned out of band and
must already be running before the application is installed — Drake cannot
start without them. The chart creates none of them; it only references them
by label and by Secret name. So the namespace is *expected* to be
populated at install time, and "the namespace must be empty" would be
impossible by construction.

## The shape of this deployment

The first deployment publishes **no public route**. Drake runs on its
ClusterIP Services and is proven to start, migrate, pass its probes and
answer inside the cluster *before* a hostname, a certificate and an
identity provider are attached to it.

That separation is deliberate. Domain, TLS and Entra are a different kind
of work with different failure modes, and bundling them into "does the
application run at all" is how a certificate problem gets diagnosed as an
application problem. `edge.mode: internal` is the state that says so
honestly; flipping it to `ingress` later changes the route and nothing
about identity.

## What Drake needs that Drake does not provide

The chart deploys **three workloads and nothing else**: `drake-api`,
`drake-web`, and the `drake-migrate` Job. It ships **no PostgreSQL and no
Redis** — both are external services the operator provides.

| Dependency | Required? | Notes |
| --- | --- | --- |
| PostgreSQL 16 | **Yes** | Authoritative store. Drake needs its own database and role — never Hermes' or LogiSlot's credentials |
| Redis 7 | **Yes** | Session store, and it is fail-closed: if Redis is unavailable, authentication fails rather than degrading |
| Entra (OIDC) | For login only | The API **starts** without it; every route then answers 401 except `/health/*`. Deployment is not blocked by a missing Entra app |
| GitHub App | No | `github.enabled: false`; the UI reports `NOT_CONFIGURED` and no provider call is made |

## Operator inputs

Everything below is a real value that does not exist in this repository
and must not be committed.

| # | Input | Where it goes |
| --- | --- | --- |
| 1 | Public hostname Drake will answer on | `publicOrigin` (`https://…`, no port) |
| 2 | PostgreSQL DSN for a **dedicated** database + role | `DRAKE_DATABASE_URL` key in `drake-api-config` |
| 3 | Redis URL | `DRAKE_REDIS_URL` key in `drake-api-config` |
| 4 | Entra issuer / client id / client secret | `DRAKE_OIDC_ISSUER`, `DRAKE_OIDC_CLIENT_ID`, `DRAKE_OIDC_CLIENT_SECRET` |
| 5 | GHCR pull credentials | `drake-ghcr` Secret |
| 6 | Datastore pods labelled as below | see "Datastore label contract" |

There is deliberately **no session-signing secret**: sessions are opaque
random identifiers held server-side in Redis, so there is no key to
manage or rotate.

Image digests are already pinned in the committed values, and the
datastores are reached by label rather than by address, so nothing else is
left blank.

## Datastore label contract

The chart selects PostgreSQL and Redis by label. The pods you deploy must
carry these, and they must come from the controller's pod template so a
rescheduled pod keeps them:

| Datastore | Required pod label |
| --- | --- |
| PostgreSQL | `app.kubernetes.io/name: drake-postgres` |
| Redis | `app.kubernetes.io/name: drake-redis` |

**Both datastores must live in Drake's own namespace.** That is a
requirement of this deployment model, not a default to be overridden.

The datastore egress peers carry no `namespaceSelector` at all, and in
Kubernetes semantics a peer without one selects pods in the policy's own
namespace — which is exactly the confinement wanted here.

`networkPolicy.database.namespaceSelector` and
`networkPolicy.redis.namespaceSelector` are **not supported**. Supplying
either — empty `{}` or populated — stops the production render rather than
being silently ignored, so a stale overlay cannot quietly widen the peer.

Moving a datastore to another namespace is not a matter of setting that
value. The chart also creates the datastore *ingress* policy, and a
NetworkPolicy only governs pods in its own namespace, so the ingress side
would have to be created and managed in the other namespace too. That is a
separate architectural change, deliberately not in this chart.

Addresses are deliberately not used. A pod connecting to a Service is
redirected to a backing pod address, and where that rewrite sits relative
to policy evaluation is up to the CNI — so an `ipBlock` holding a ClusterIP
is not portable across clusters, and a pod address does not survive a
reschedule. `podSelector` is what Kubernetes defines for in-cluster peers
and survives both.

## What each component may reach

Default-deny covers the whole namespace in both directions. On top of it:

| Component | DNS | PostgreSQL 5432 | Redis 6379 | Outbound HTTPS |
| --- | --- | --- | --- | --- |
| `api` | yes | yes | yes | only entries in `networkPolicy.apiExternalEgress` (empty by default) |
| `migration` | yes | yes | **no** — `alembic/env.py` opens the database and nothing else | no |
| `web` | no | no | no | no — it makes no server-side call; the browser talks to `/v1` directly |

The chart also grants ingress *to* the datastores from exactly those
components. That is not optional hardening: default-deny applies to the
datastore pods too, so without it a permitted connection would leave the
API and be dropped on arrival.

## A note on enforcement

The production cluster runs Flannel with no policy controller, and no
NetworkPolicy exists anywhere in it. **Nothing enforces these policies
there today**, so they are declarative intent and must not be counted as a
security control until an enforcing CNI is present. They are written
correctly so that the day one is installed, Drake keeps working instead of
losing its database.

Where policy *is* enforced, expect a brief window at pod start before the
rules are programmed — measured at roughly five seconds on k3s/kube-router.
The API's readiness probe covers this; it is only worth knowing when
reading the first seconds of a new pod's logs.

## Steps

**1. Confirm the images — publish only if the application changed.**

`deploy/drake/values-drake-prod.yaml` already pins both digests, and the
comment beside each records the commit they were built from. A change that
touches only the chart, the runbook or tests does **not** need new images:
republishing to chase a moved `main` would produce a different digest for
identical application code and discard the provenance already recorded.

So verify rather than rebuild:

```
# Both digests must exist and be pullable with the deployment's credentials.
gh api "orgs/<ORG>/packages/container/drake-api/versions" \
  --jq '.[] | select(.name=="<API-DIGEST>") | .name'
gh api "orgs/<ORG>/packages/container/drake-web/versions" \
  --jq '.[] | select(.name=="<WEB-DIGEST>") | .name'
```

Publish new images only when `apps/api` or `apps/web` has actually changed:
the `publish images` workflow runs from `main` by manual dispatch, typing
`publish` to confirm, builds both components from **one** commit, tags
strictly by commit SHA, never `latest`, and prints both digests. If you do
publish, update the digests and their source-commit comments together —
API, web and migration must all come from the same commit.

**2. Establish the namespace and the pull secret — check first.**

Read what is there before creating anything:

```
kubectl --context <CTX> get namespace drake-prod
kubectl --context <CTX> -n drake-prod get secret drake-ghcr \
  -o jsonpath='{.type}{"\n"}'
```

*If the namespace exists* (the normal case on a cluster whose datastores
are already provisioned): **do not recreate it.** Confirm it is the
intended one and move on.

*If `drake-ghcr` exists*: **do not recreate or rotate it.** Confirm only
its type — `kubernetes.io/dockerconfigjson` — and that image pulls work
(step 6 checks that against a real digest). Rotating a working pull
credential mid-deployment turns one problem into two.

*Only if either is genuinely absent* — verified by the commands above, not
assumed — create it:

```
kubectl create namespace drake-prod
kubectl -n drake-prod create secret docker-registry drake-ghcr \
  --docker-server=ghcr.io \
  --docker-username='<GITHUB-USERNAME>' \
  --docker-password-stdin < /dev/stdin
```

Reading the token from stdin keeps it out of argv and shell history. If a
resource is missing and you do not know the correct value for it, stop and
report the missing input rather than inventing one.

**3. Establish the application Secret — check first.**

The internal deployment needs **exactly two keys**: `DRAKE_DATABASE_URL`
and `DRAKE_REDIS_URL`.

Verify what exists without decoding or printing any value. This lists key
names only:

```
kubectl --context <CTX> -n drake-prod get secret drake-api-config \
  -o go-template='{{range $k, $v := .data}}{{$k}}{{"\n"}}{{end}}'
```

*If `drake-api-config` exists with both keys* — the normal case on a
cluster whose datastores are already provisioned — **that is the expected
state.** Do not recreate it, do not patch it, and do not print it. The
database and Redis credentials inside it are what the running PostgreSQL
and Redis were created with; replacing them would break a working
deployment.

Never run `get -o yaml`, `get -o json` or `describe` on this Secret: each
prints the full data map. The `go-template` above is deliberate — it
emits key names and nothing else.

*If a key is missing*, stop and report it. Do not guess a connection
string: the correct one is whatever the existing PostgreSQL and Redis were
provisioned with, and inventing one produces a pod that starts and then
fails authentication.

*Only on a genuinely empty namespace*, create it — reading each value from
stdin so nothing lands in argv or shell history:

```
kubectl -n drake-prod create secret generic drake-api-config \
  --from-file=DRAKE_DATABASE_URL=/dev/stdin   # paste, then Ctrl-D
```



**Entra keys are added later, not now.** The API resolves OIDC discovery
lazily — only on the login path — so it starts, passes `/health/live` and
reports `/health/ready` from PostgreSQL and Redis alone. Health and
readiness verification of this deployment is fully supported without
Entra.

**Never invent placeholder OIDC values.** A fake issuer or client id does
not make login work; it makes the failure appear later and somewhere else.
Leave the keys absent until a real Drake app registration exists.

When it does, add the three keys **without recreating the Secret, without
printing them, and without putting them on a command line**.

`kubectl patch -p '<json>'` would place the client secret in the `kubectl`
process's argv, where it is visible to `ps` and lands in shell history.
Instead the patch is built in memory and piped straight to
`--patch-file=/dev/stdin` (kubectl v1.22+; the target cluster runs
v1.34.3):

```
read -rsp 'Entra tenant id: '      TENANT;  echo
read -rsp 'Entra client id: '      CLIENT;  echo
read -rsp 'Entra client secret: '  SECRET;  echo

TENANT="$TENANT" CLIENT="$CLIENT" SECRET="$SECRET" python3 - <<'PY' |
import base64, json, os
def b(v): return base64.b64encode(v.encode()).decode()
print(json.dumps({"data": {
    "DRAKE_OIDC_ISSUER":        b(f"https://login.microsoftonline.com/{os.environ['TENANT']}/v2.0"),
    "DRAKE_OIDC_CLIENT_ID":     b(os.environ["CLIENT"]),
    "DRAKE_OIDC_CLIENT_SECRET": b(os.environ["SECRET"]),
}}))
PY
kubectl -n drake-prod patch secret drake-api-config \
  --type merge --patch-file=/dev/stdin

unset TENANT CLIENT SECRET
kubectl -n drake-prod rollout restart deployment/drake-api
```

`read -rs` keeps the values off the screen and out of history; the
environment carries them only to the child process that encodes them; the
patch never touches disk; and `unset` clears them from the shell.

A patch leaves the existing database and Redis values untouched, so there
is no window in which the Secret is incomplete.

**4. Check the values.** `deploy/drake/values-drake-prod.yaml` is complete
as committed: images are pinned to published digests, and the datastores
are selected by label rather than by address. Deploy it as-is unless the
datastore labels in your namespace differ from the contract above.

**5. Render and check ownership before installing.**

```
helm lint deploy/drake
helm template drake deploy/drake --namespace drake-prod \
  -f deploy/drake/values-drake-prod.yaml > /tmp/drake-render.yaml
```

The render must **use** the datastores, never claim them. Confirm all of
the following in `/tmp/drake-render.yaml`:

| Must be absent | Why |
| --- | --- |
| `kind: Secret` | the application Secret is out of band; rendering one would make Helm adopt and later delete it |
| `kind: PersistentVolumeClaim` | the PostgreSQL PVC holds the data |
| `kind: StatefulSet` | PostgreSQL is not Helm's |
| any `drake-postgres` / `drake-redis` workload or Service | likewise |
| `kind: Ingress`, `NodePort`, `LoadBalancer` | this deployment publishes no public route |

| Must be present | Expected |
| --- | --- |
| Deployments | exactly `drake-api`, `drake-web` |
| Job | exactly `drake-migrate` |
| Services | exactly `drake-api`, `drake-web`, both `ClusterIP` |
| datastore references | label selectors + `existingSecret` name only |
| image digests | identical to the committed values; no `latest`, no placeholder |

A single command that answers all of it:

```
python3 - /tmp/drake-render.yaml <<'PY'
import sys, yaml
docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d]
kinds = {}
for d in docs:
    kinds.setdefault(d["kind"], []).append(d["metadata"]["name"])
for forbidden in ("Secret", "PersistentVolumeClaim", "StatefulSet", "Ingress"):
    assert forbidden not in kinds, f"chart renders a {forbidden}; it must not own that"
for name in [n for names in kinds.values() for n in names]:
    assert not name.startswith(("drake-postgres", "drake-redis")), \
        f"{name} would make Helm adopt an out-of-band datastore"
assert sorted(kinds.get("Deployment", [])) == ["drake-api", "drake-web"]
assert kinds.get("Job") == ["drake-migrate"]
print("ownership OK:", {k: sorted(v) for k, v in kinds.items()})
PY
```

If any check fails, **do not install** — a render that claims a datastore
will delete it on the next `helm uninstall`.

**6. Preflight the cluster (read-only).**

```
kubectl --context <CTX> version
kubectl --context <CTX> get nodes -o wide
kubectl --context <CTX> get storageclass
kubectl --context <CTX> -n drake-prod get all,pvc -o wide
kubectl --context <CTX> -n drake-prod get pods --show-labels
kubectl --context <CTX> get ingress -A
helm --kube-context <CTX> list -n drake-prod
```

**Expected to be present.** These are prerequisites, not leftovers:
PostgreSQL workload and Service, Redis workload and Service, the
PostgreSQL PVC in `Bound`, the out-of-band Secrets (`drake-api-config`,
`drake-ghcr`), and anything else the datastores need to run.

**Stop conditions.** Any one of these means do not install:

- the kube context or cluster is not the intended target
- the target is anything other than the `drake-prod` namespace
- a `drake` Helm release already exists in the namespace
- a `drake-api` or `drake-web` Deployment or Service already exists that
  Helm does not own — the chart would collide with it
- unexpected application resources from a previous failed attempt
- datastore pods missing the labels the chart selects
  (`app.kubernetes.io/name: drake-postgres` / `drake-redis`)
- `drake-api-config` missing either required key, or `drake-ghcr` absent
- PostgreSQL or Redis not Ready
- the PostgreSQL PVC not `Bound`
- the render claims a datastore workload, Service, PVC or Secret (step 5)

Preflight is read-only. It contains no command that deletes, patches or
restarts anything.

**7. Data-protection gate — before the migration runs.**

The migration applies DDL to a database that already holds data. Record a
baseline read-only, and confirm there is a way back:

```
# A usable, current recovery point must exist for this PostgreSQL.
# Record where it is and when it was taken. If you cannot confirm one,
# STOP: there is no rollback for a schema change.

# Baseline the identities that must not change (no secret values printed):
kubectl -n drake-prod get pvc drake-postgres-data \
  -o jsonpath='{.metadata.name}{" "}{.metadata.uid}{" "}{.status.phase}{" "}{.spec.volumeName}{"\n"}'
kubectl -n drake-prod get statefulset drake-postgres \
  -o jsonpath='{.metadata.uid}{" "}{.metadata.generation}{"\n"}'
kubectl -n drake-prod get deployment drake-redis \
  -o jsonpath='{.metadata.uid}{" "}{.metadata.generation}{"\n"}'
kubectl -n drake-prod get secret drake-api-config \
  -o jsonpath='{.metadata.uid}{" "}{.metadata.resourceVersion}{"\n"}'
kubectl -n drake-prod get svc drake-postgres drake-redis \
  -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.metadata.uid}{"\n"}{end}'
```

**`--atomic` does not undo a migration.** It rolls back Kubernetes objects
to the previous release; Alembic has no automatic downgrade and Drake ships
no production downgrade path. If a migration succeeds and the release then
fails, the schema change stays. A recovery point is the only way back, and
that is why this gate exists.

The chart holds none of the resources baselined above. They are outside the
release, so Helm will neither adopt nor delete them — which is also why
their identities are worth recording: any change to them came from
somewhere else.

**8. Install.**

```
helm upgrade --install drake deploy/drake \
  --namespace drake-prod \
  -f deploy/drake/values-drake-prod.yaml \
  --atomic --wait --timeout 10m
```

Add `--create-namespace` only on the genuinely-empty path; where the
datastores are already provisioned the namespace exists and the flag is a
no-op that obscures the assumption.

```
```

`--atomic` matters: the `drake-migrate` Job is a `pre-install`/`pre-upgrade`
hook with `backoffLimit: 0`, so a failed migration fails the release and
no application pod ever serves against a half-migrated schema.

**9. Verify.**

```
kubectl -n drake-prod get all
kubectl -n drake-prod get pods -o wide
kubectl -n drake-prod get events --sort-by=.metadata.creationTimestamp
kubectl -n drake-prod rollout status deployment/drake-api
kubectl -n drake-prod rollout status deployment/drake-web
kubectl -n drake-prod logs job/drake-migrate
```

Expect: migration Job `Complete`; both Deployments rolled out; no
`CrashLoopBackOff`; no `ImagePullBackOff`; no restarts.

**10. Confirm nothing outside the release was touched.**

Re-read the baseline from step 7 and compare. These must be **unchanged**:

| Baseline | Must match |
| --- | --- |
| `drake-postgres-data` PVC | name, UID, `Bound`, and the same `spec.volumeName` |
| `drake-postgres` StatefulSet | UID and `generation` |
| `drake-redis` Deployment | UID and `generation` |
| `drake-api-config` Secret | UID (and `resourceVersion`, if nothing legitimately patched it) |
| `drake-postgres` / `drake-redis` Services | name and UID |

```
helm -n drake-prod get manifest drake | grep -E "^  name:|^kind:" | paste - -
```

That listing must contain only `drake-api`, `drake-web`, `drake-migrate`
and the NetworkPolicies — no datastore object. If a datastore appears in
the release manifest, Helm has adopted it and `helm uninstall` would delete
it.

**Pod UIDs are deliberately not on that list.** A datastore pod may be
rescheduled for entirely legitimate reasons, and a runbook that demanded an
unchanged pod UID would raise false alarms while missing the thing that
matters. Controller, PVC and Service identity are what persist; those are
what is checked.

Data reachability is the final confirmation: `/health/ready` returning 200
in step 11 means the API reached both PostgreSQL and Redis with the
credentials that were already there.

**11. Smoke test through port-forward.** No domain, no Ingress, no TLS.

```
kubectl -n drake-prod port-forward svc/drake-api 8000:8000
curl -i http://127.0.0.1:8000/health/live      # expect 200
curl -i http://127.0.0.1:8000/health/ready     # expect 200 once the DB is reachable
curl -i http://127.0.0.1:8000/v1/me            # expect 401 unauthenticated
```

```
kubectl -n drake-prod port-forward svc/drake-web 3000:3000
curl -i http://127.0.0.1:3000/                 # expect 200
```

**Known limitation, stated plainly:** the web app and the API will **not**
work together through two port-forwards. In production the browser calls
same-origin `/v1` and the edge routes it to the API; with port-forwarding
there is no edge, and the web app deliberately carries no API origin
(ADR-0021 — a proxy hop in front of `/v1` breaks query cancellation). So
the browser on `:3000` has nothing to route `/v1` to. Verify the API
directly on its own port-forward, and treat full browser-to-API testing as
part of the separate public-endpoint task.

`/health/ready` returning 200 is the real proof of database connectivity.

## What is deferred

Domain, DNS, TLS certificate, Ingress, and the Entra redirect URI
registration are one follow-up task. None of them blocks this deployment.
When they are ready, set `edge.mode: ingress` with the ingress values and
register `<publicOrigin>/v1/auth/callback` in the Drake app registration —
a **separate** registration from Hermes; credentials are never shared.
Drake's logout is `POST /v1/auth/logout`, a server-side session deletion,
so there is no front-channel logout URL to register.

## Rollback

```
helm rollback drake <previous-revision> --namespace drake-prod
```

or, for the first release:

```
helm uninstall drake --namespace drake-prod
```

Either affects only the `drake-prod` namespace; the chart creates nothing
outside it and touches no shared object. Rollback does **not** reverse a
migration — Drake has no production downgrade path by design.

### Two policies survive an uninstall

The migration needs its network access to exist *before* the pre-install
hook Pod runs, so two NetworkPolicies are themselves Helm hooks:

- `drake-migration-egress`
- `drake-database-migration-ingress`

The API's own route to PostgreSQL is **not** among them.
`drake-database-api-ingress` is an ordinary release resource, precisely so
that recreating the migration hook at the start of an upgrade cannot
interrupt a running API's database access. Helm removes it on uninstall
like anything else.

Helm does not track hook resources as part of the release, so
**`helm uninstall` leaves both behind**. Each install or upgrade replaces
them (`hook-delete-policy: before-hook-creation`), so they stay current on
their own; nothing needs to be done during a normal deploy or upgrade.

Only after a genuine uninstall, remove exactly those two:

```
kubectl -n drake-prod delete networkpolicy \
  drake-migration-egress \
  drake-database-migration-ingress \
  --ignore-not-found
```

That command names both policies explicitly and touches nothing else — the
PostgreSQL and Redis workloads, their Services, Secrets and PVCs are not
Helm resources and are unaffected. Do not run it as part of an upgrade.
