# Runbook — Drake's first production deployment

**As of Sprint 5C, Drake has never been deployed.** This runbook is the
sequence for doing it the first time. Nothing in it has been executed.

Read the status table before planning a date: roughly half of the inputs
below do not exist yet, and none of them can be created by the repository.

## What Sprint 5C did and did not do

| Area | State |
| --- | --- |
| Production edge contract (`/` → web, `/v1` → API) | **Implemented and tested** |
| Helm chart, fail-closed on missing production values | **Implemented and tested** |
| Container images, digest-pinned, non-root | **Built and verified locally (linux/amd64)** |
| Image publication workflow | **Implemented, never run** |
| Read-only preflight command | **Implemented** |
| Production deployment | **Not executed** |
| Public DNS record | **Not configured** |
| TLS certificate | **Not provisioned** |
| Production database and Redis | **Not provisioned** |
| Production OIDC client | **Not registered** |
| GitHub App | **Not registered, not installed** |
| Hermes onboarding | **Not started** |
| Datalake | **Out of scope; its security gate is open and unchanged** |
| Deployments to date | **0** |

## Legend

- **[5C]** — implemented in this sprint; nothing for an operator to decide.
- **[INPUT]** — a real value an operator must obtain or choose. It does not
  exist in the repository and must never be committed.
- **[DEPLOY]** — executed during the first deployment.
- **[LATER]** — deferred until the GitHub App is activated. Drake runs
  without it.

---

## Phase 1 — Decide the inputs (nothing is applied)

**1. [INPUT] Choose the public FQDN.**
One host, exactly. `drake.example.test` throughout the repository is a
documentation placeholder and always will be. The real hostname is chosen
here and lives only in the operator's private values file.

**2. [INPUT] Create the DNS record.**
An A/AAAA (or CNAME) record for that FQDN pointing at the ingress
controller's external address. Drake does not create DNS.

**3. [INPUT] Provision the TLS certificate.**
Issue a certificate covering exactly that host and store it as a
Kubernetes Secret of type `kubernetes.io/tls`. The chart references the
Secret by name and never contains certificate material. A wildcard is
refused at render time.

**4. [INPUT] Identify the ingress controller.**
Its `ingressClassName`, its namespace, and the labels its pods carry. The
last two feed the NetworkPolicy: a default-deny namespace with no
allowance for the controller blocks the route that was just configured,
and the failure looks like DNS.

**5. [INPUT] Provision PostgreSQL 16 and Redis 7.**
Managed or in-cluster, but reachable from the Drake namespace, and with
their addresses expressible as specific CIDRs. `0.0.0.0/0` is refused.

**6. [INPUT] Register the production OIDC client.**
Redirect URI exactly `https://<FQDN>/v1/auth/callback` — the API refuses
to start if its configured redirect URL disagrees with its public origin.
Record the issuer, client id, and client secret.

**7. [5C] Review the edge contract.**
[ADR-0021](../adr/ADR-0021-production-edge-contract.md). One origin, `/v1`
straight to the API, no rewrite, no second hostname, no CORS.

## Phase 2 — Build the artefacts

**8. [5C] The images are reproducible.**
`apps/api/Dockerfile` and `apps/web/Dockerfile` build from digest-pinned
base images, run as uid 65532 with a read-only root filesystem, and carry
no build-time secrets. The web image is a Next standalone build with no
API origin compiled into it.

**9. [DEPLOY] Publish the images.**
Run the `publish-images` workflow manually from `main`. It requires typing
`publish` to confirm, tags strictly by commit SHA, never `latest`, and
prints the resulting digests to the run summary.
**Record both digests.** They are the only thing the chart accepts.

## Phase 3 — Prepare the release (still nothing applied)

**10. [INPUT] Write the private production values file.**
Copy `deploy/drake/values-production.test.yaml` as a shape reference and
fill in the real host, class, TLS Secret name, image digests, Secret
names, and CIDRs. **Keep it out of the repository.** It contains no secret
values — only names — but it does contain the production topology.

**11. [INPUT] Create the application Secret** (`api.existingSecret`), out
of band, with `kubectl create secret generic`. Keys:

| Key | Meaning |
| --- | --- |
| `DRAKE_DATABASE_URL` | PostgreSQL DSN |
| `DRAKE_REDIS_URL` | Redis URL |
| `DRAKE_SESSION_SECRET` | session signing key |
| `DRAKE_OIDC_ISSUER` | from step 6 (must be `https://`) |
| `DRAKE_OIDC_CLIENT_ID` | from step 6 |
| `DRAKE_OIDC_CLIENT_SECRET` | from step 6 |

The chart creates no Secret and inlines no value; a test asserts that no
rendered manifest carries credential material.

**12. [5C] Confirm what the chart derives for you.**
`DRAKE_PUBLIC_ORIGIN`, `DRAKE_ALLOWED_WEB_ORIGINS`,
`DRAKE_OIDC_REDIRECT_URL` and `DRAKE_TRUSTED_PROXY_COUNT=1` are computed
from the single ingress host. Do not set them by hand; two sources of
truth for one hostname is how a redirect ends up outside the certificate.

**13. [5C] Render locally and read the output.**
`helm template drake deploy/drake -f <your-values.yaml>`. A missing host,
origin, TLS secret, ingress class, image digest, Secret name or CIDR fails
the render rather than producing something installable.

**14. [DEPLOY] Run the read-only preflight.**

```
scripts/production_preflight.sh --context <ctx> --namespace <ns> --values <your-values.yaml>
```

It reads only: no apply, no Secret creation, no migration, no GitHub call.
It checks tooling, cluster reachability, the ingress class, the TLS
Secret's existence, that every referenced Secret exists (by name — it
never reads a value), image immutability, that no rewrite annotation or
NodePort is present, and the NetworkPolicy prerequisites. Fix everything
it reports before continuing.

## Phase 4 — Deploy

**15. [DEPLOY] Create the namespace** and confirm the default-deny posture
it will run under.

**16. [DEPLOY] Install.**
`helm install drake deploy/drake -n <ns> -f <your-values.yaml>`.
The pre-install hook Job runs `alembic upgrade head` with `backoffLimit:
0`. It is the **only** migration mechanism: the API does not migrate at
startup, and there is no second path. If the Job fails, the release fails
and no pod serves traffic against a half-migrated schema. Read the Job's
logs; do not run a downgrade.

**17. [DEPLOY] Verify the edge from outside the cluster.**

- `https://<FQDN>/` returns the web app.
- `https://<FQDN>/v1/me` returns `401` when unauthenticated — proof the
  API is being reached, on the unmodified path.
- A nested path with a query string reaches the correct handler.
- `http://<FQDN>/` does not serve the application.
- `https://<FQDN>/health/live` does **not** reach the API: health lives
  outside `/v1` and is intentionally not published. Probes address the
  pod directly.

**18. [DEPLOY] Log in once** and confirm the callback returns to the
canonical origin and the session cookie is `Secure`.

## Phase 5 — Deferred

**19. [LATER] Register and install the GitHub App.**
Follow [GITHUB_APP_ONBOARDING.md](GITHUB_APP_ONBOARDING.md). Until then
leave `github.enabled: false`; the integration UI reports
`NOT_CONFIGURED`, no token is minted, and nothing else is affected.

**20. [LATER] Enable the integration.**
Create the App Secret with keys `private-key.pem` and `webhook-secret`,
set `github.enabled=true`, `github.existingSecret`, and `github.appId`
(or `clientId`). The chart **mounts** that Secret read-only at
`/etc/drake/github` rather than putting it in the environment, because the
API reads both from disk — key material should not be visible in
`kubectl describe pod`. Set the webhook URL to
`https://<FQDN>/v1/integrations/github/webhook`.

**21. [LATER] Onboard the first pilot repository** (Hermes). Datalake stays
out of scope and its security gate stays open until it is addressed
separately.

---

## Rollback

`helm rollback drake <previous-revision>` restores the previous pods and
image digests. It does **not** reverse a migration — Drake has no
downgrade path in production by design. A schema change that must be
undone is a forward migration, planned deliberately.
