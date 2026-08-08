# ADR-0021 — Production edge contract

**Status:** accepted (Sprint 5C)
**Supersedes the open Sprint 3 item:** production `/v1` ingress routing

## Context

Drake is two processes — a FastAPI control plane and a Next.js web app —
and until now the only thing joining them was a development convenience:
Next rewrites `/v1/*` to the API so the browser stays same-origin.

That rewrite cannot go to production, and not for tidiness. Sprint 3
established empirically that Next's proxy hop does **not** propagate a
client abort upstream: it drains the upstream response to reuse the pooled
connection. Server-side query cancellation on client disconnect —
something Drake deliberately implements — therefore never fires behind
that hop. The Sprint 3 report recorded this as an unresolved production
requirement. This ADR closes it.

## Decision

One public origin. Two prefixes. No rewrite.

```
https://<PUBLIC_HOST>/      → Drake web Service
https://<PUBLIC_HOST>/v1    → Drake API Service
```

- `networking.k8s.io/v1` Ingress, explicit `ingressClassName`, exact host
  (never a wildcard), TLS required.
- Both routes use `pathType: Prefix`. Kubernetes selects the **longest
  matching path**, so `/v1` wins over `/` for API requests. No ordering
  trick, no regex, no `configuration-snippet`, no `rewrite-target`.
- The path arrives **unchanged**. The API owns the whole `/v1` route
  space: `GET /v1/projects?limit=20` must reach it as
  `/v1/projects?limit=20`. With a `rewrite-target` every API route would
  404 and the symptom would look like an application bug.
- Query strings are untouched — Kubernetes matches on path elements only.
- Public Services stay `ClusterIP`. The Ingress is the only front door; a
  NodePort or LoadBalancer would be a second, unmanaged one that bypasses
  TLS termination and this contract.
- No production CORS, because there is nothing cross-origin.
- The web container is given **no** API origin: no `DRAKE_API_URL`, no
  `NEXT_PUBLIC_*` API host. The browser calls relative `/v1`.

### Why the origin is configuration, not a request property

Every externally visible URL — the OIDC redirect, the webhook URL an
operator pastes into GitHub, post-login and logout redirects — derives
from one configured `publicOrigin`. Deriving them from the incoming
request would let a forged `Host` or `X-Forwarded-Host` decide where Drake
sends a user, or what it claims its own callback is. In production the
CSRF origin comparison uses that configured value alone; folding
`request.base_url` into the allow-list, as development does, would defeat
the check it exists to perform.

Production validation refuses a plaintext origin, localhost, a loopback or
bare IP, embedded credentials, a path/query/fragment, a wildcard, a
malformed hostname, a placeholder, and any origin that disagrees with the
ingress host.

### What this does not change

The GitHub setup callback stays untrusted, exactly as Sprint 5B left it.
Making Drake publicly reachable does not turn a callback query parameter
into identity evidence; installation identity still comes from the webhook
and from provider reconciliation.

## Consequences

- Client disconnects propagate to the API, so Sprint 3's cancellation,
  budget and last-good semantics hold in production as they do in tests.
- The chart fails to **render** when a required production value is
  missing, rather than installing something plausible. That is deliberate:
  a chart that fills in a default for TLS or a host is how an environment
  ends up unprotected without anyone deciding it should be.
- Health endpoints live outside `/v1` and are therefore not publicly
  routed to the API. Kubernetes probes address the pod directly, and
  liveness/readiness are not something the internet needs.
- Drake deploys before the GitHub App exists. With the integration
  disabled the API and web app start, non-GitHub features work, the
  integration UI states `NOT_CONFIGURED`, and no token is minted.

## Alternatives considered

**Keep the Next proxy in production.** Rejected: it reintroduces the
cancellation regression, and it puts a second process in the path of every
API call for no benefit.

**A second hostname for the API** (`api.<host>`). Rejected: it requires
CORS, a second certificate, and cookie configuration that has to stay in
sync with the web origin — three ways to drift for one avoided prefix.

**Rewrite `/v1` away at the edge and mount the API at the root.**
Rejected: the API's route space, its OpenAPI document, its OIDC callback
and its webhook URL are all `/v1`-prefixed. Stripping the prefix at the
edge means every one of those has to be rewritten back somewhere else.
