# Runbook — Drake's first standard Kubernetes deployment

**Nothing here has been executed.** No cluster was contacted, no namespace
created, no Secret created, no image published, no release installed.

Target namespace: `drake-prod`.

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

Both live in Drake's own namespace. The chart emits no `namespaceSelector`
for them, which is what confines the peer to this namespace; set
`networkPolicy.database.namespaceSelector` only if a datastore genuinely
runs elsewhere.

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

**1. Publish the images.** The `publish images` workflow runs only from
`main` and only by manual dispatch, typing `publish` to confirm. It builds
`drake-api` and `drake-web` from the same commit, tags strictly by commit
SHA, never `latest`, and prints both digests to the run summary. Record
them.

**2. Create the namespace and the pull secret.**

```
kubectl create namespace drake-prod
kubectl -n drake-prod create secret docker-registry drake-ghcr \
  --docker-server=ghcr.io \
  --docker-username='<GITHUB-USERNAME>' \
  --docker-password='<TOKEN-WITH-read:packages>'
```

**3. Create the application Secret.** The internal deployment needs
**exactly two keys**:

```
kubectl -n drake-prod create secret generic drake-api-config \
  --from-literal=DRAKE_DATABASE_URL='postgresql+psycopg://<user>:<pass>@<host>:5432/<db>' \
  --from-literal=DRAKE_REDIS_URL='redis://<host>:6379/0'
```

Do not `get -o yaml` or `describe` this Secret afterwards.

**Entra keys are added later, not now.** The API resolves OIDC discovery
lazily — only on the login path — so it starts, passes `/health/live` and
reports `/health/ready` from PostgreSQL and Redis alone. Health and
readiness verification of this deployment is fully supported without
Entra.

**Never invent placeholder OIDC values.** A fake issuer or client id does
not make login work; it makes the failure appear later and somewhere else.
Leave the keys absent until a real Drake app registration exists.

When it does, add the three keys **without recreating the Secret and
without printing it**:

```
kubectl -n drake-prod patch secret drake-api-config --type merge -p "$(
  python3 - <<'PY'
import base64, json
def b(v): return base64.b64encode(v.encode()).decode()
print(json.dumps({"data": {
    "DRAKE_OIDC_ISSUER":        b("https://login.microsoftonline.com/<TENANT>/v2.0"),
    "DRAKE_OIDC_CLIENT_ID":     b("<CLIENT-ID>"),
    "DRAKE_OIDC_CLIENT_SECRET": b("<CLIENT-SECRET>"),
}}))
PY
)"
kubectl -n drake-prod rollout restart deployment/drake-api
```

A patch leaves the existing database and Redis values untouched, so there
is no window in which the Secret is incomplete.

**4. Check the values.** `deploy/drake/values-drake-prod.yaml` is complete
as committed: images are pinned to published digests, and the datastores
are selected by label rather than by address. Deploy it as-is unless the
datastore labels in your namespace differ from the contract above.

**5. Render and read the output before installing.**

```
helm lint deploy/drake
helm template drake deploy/drake --namespace drake-prod -f <YOUR-VALUES>
```

Confirm: namespace is `drake-prod` everywhere, no Ingress, no NodePort or
LoadBalancer, no plaintext Secret, no `latest`, no placeholder digest,
`imagePullSecrets` present, both Services `ClusterIP`.

**6. Preflight the cluster (read-only).**

```
kubectl --context <CTX> version
kubectl --context <CTX> get nodes -o wide
kubectl --context <CTX> get ns
kubectl --context <CTX> get storageclass
kubectl --context <CTX> -n drake-prod get all
kubectl --context <CTX> get ingress -A
```

Verify it is the two-node Drake target cluster, that `drake-prod` holds no
prior resources, and that no name, port or hostname collides with an
existing application. Stop if the context is wrong, an image is missing,
or a required Secret is absent.

**7. Install.**

```
helm upgrade --install drake deploy/drake \
  --namespace drake-prod \
  --create-namespace \
  -f <YOUR-VALUES> \
  --atomic --wait --timeout 10m
```

`--atomic` matters: the `drake-migrate` Job is a `pre-install`/`pre-upgrade`
hook with `backoffLimit: 0`, so a failed migration fails the release and
no application pod ever serves against a half-migrated schema.

**8. Verify.**

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

**9. Smoke test through port-forward.** No domain, no Ingress, no TLS.

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
- `drake-database-ingress`

Helm does not track hook resources as part of the release, so
**`helm uninstall` leaves both behind**. Each install or upgrade replaces
them (`hook-delete-policy: before-hook-creation`), so they stay current on
their own; nothing needs to be done during a normal deploy or upgrade.

Only after a genuine uninstall, remove exactly those two:

```
kubectl -n drake-prod delete networkpolicy \
  drake-migration-egress \
  drake-database-ingress \
  --ignore-not-found
```

That command names both policies explicitly and touches nothing else — the
PostgreSQL and Redis workloads, their Services, Secrets and PVCs are not
Helm resources and are unaffected. Do not run it as part of an upgrade.
