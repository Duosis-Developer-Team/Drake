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
| 5 | PostgreSQL and Redis addresses as /32 CIDRs | `networkPolicy.databaseCIDR`, `redisCIDR` |
| 6 | GHCR pull credentials | `drake-ghcr` Secret |
| 7 | Image digests from a publication run | `api/web/migration.image.digest` |

There is deliberately **no session-signing secret**: sessions are opaque
random identifiers held server-side in Redis, so there is no key to
manage or rotate.

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

**3. Create the application Secret.** Exactly these five keys:

```
kubectl -n drake-prod create secret generic drake-api-config \
  --from-literal=DRAKE_DATABASE_URL='postgresql+psycopg://<user>:<pass>@<host>:5432/<db>' \
  --from-literal=DRAKE_REDIS_URL='redis://<host>:6379/0' \
  --from-literal=DRAKE_OIDC_ISSUER='https://login.microsoftonline.com/<TENANT>/v2.0' \
  --from-literal=DRAKE_OIDC_CLIENT_ID='<CLIENT-ID>' \
  --from-literal=DRAKE_OIDC_CLIENT_SECRET='<CLIENT-SECRET>'
```

Do not `get -o yaml` or `describe` this Secret afterwards.

**4. Fill in the values.** Copy `deploy/drake/values-drake-prod.yaml` to a
private working copy and set `publicOrigin`, the three digests, and the
two CIDRs. The committed file deliberately leaves them blank and **fails
to render** until they are supplied — a values file that renders with
placeholders is how a placeholder reaches a cluster.

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
