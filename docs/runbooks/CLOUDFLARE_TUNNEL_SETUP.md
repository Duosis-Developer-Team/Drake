# Runbook — publishing Drake through a Cloudflare Tunnel

**Nothing in this runbook has been executed.** Sprint 5D-B prepared the
repository only: no Tunnel exists, no DNS record was created, no Secret was
created, and nothing was deployed.

Target: `https://drake.duosis.com`

## Why a Tunnel rather than an Ingress

The cluster's inbound paths are already spoken for. `node1`'s 80/443 NAT
rules belong to Hermes-test, and the shared single-replica `ingress-nginx`
is relied on by Hermes-dev and LogiSlot. Publishing Drake through either
would mean editing infrastructure other applications depend on, to add an
application that has never run in production.

A Tunnel avoids that entirely: the connector dials **out** to Cloudflare on
7844, and traffic arrives through that established connection. No inbound
port, no NAT change, no ingress-controller change, no certificate in the
cluster. TLS terminates at Cloudflare's edge.

The trade is a new external dependency (Cloudflare) and a new hop in front
of the API. Both are deliberate and are listed under residual risk below.

## Operator steps

Steps 1–7 are performed on an operator workstation. Placeholders are shown
in angle brackets — substitute real values, and never paste real values
into a shared channel, a ticket, or this file.

**1. Authenticate to Cloudflare.**

```
cloudflared tunnel login
```

This writes an **account-level** `cert.pem` (`~/.cloudflared/cert.pem`).
That file can create and delete tunnels for the whole zone. It must never
be copied into the cluster, committed, or attached to a Secret. Only the
per-tunnel credentials file from step 2 goes anywhere near Kubernetes.

**2. Create a locally-managed tunnel.**

```
cloudflared tunnel create drake-prod
```

Note the tunnel UUID it prints and the credentials JSON path it writes
(`~/.cloudflared/<TUNNEL-UUID>.json`). "Locally-managed" matters: the
routing table then comes from the ConfigMap in this repository, which is
reviewable, rather than from the dashboard, which is not.

**3. Route the hostname to the tunnel.**

```
cloudflared tunnel route dns drake-prod drake.duosis.com
```

This creates the proxied CNAME in Cloudflare DNS. It touches only the new
`drake` record — no existing `duosis.com` record is modified.

**4. Create the namespace** (if it does not exist) and the pull secret for
private GHCR images:

```
kubectl create namespace drake-prod
kubectl -n drake-prod create secret docker-registry drake-ghcr \
  --docker-server=ghcr.io \
  --docker-username='<GITHUB-USERNAME>' \
  --docker-password='<GITHUB-PAT-WITH-read:packages>'
```

**5. Create the tunnel Secret.** Exactly two keys, and the chart expects
both:

```
kubectl -n drake-prod create secret generic drake-tunnel-credentials \
  --from-literal=tunnel-id='<TUNNEL-UUID>' \
  --from-file=credentials.json='<PATH-TO>/<TUNNEL-UUID>.json'
```

Do not `kubectl get -o yaml` this Secret, do not `describe` it, and do not
echo the JSON. It is not in Git and must not reach a terminal transcript,
a CI log, or a diff.

**6. Publish the images.** Run the `publish images` workflow from `main`,
typing `publish` to confirm. Record the two digests it prints — they are
the only image references the chart accepts.

**7. Register the Entra redirect URI.** In the **Drake** app registration
(a separate registration from Hermes — credentials are never shared), add:

| Field | Value |
| --- | --- |
| Redirect URI (Web) | `https://drake.duosis.com/v1/auth/callback` |
| Canonical origin | `https://drake.duosis.com` |
| Front-channel logout URL | *not applicable* |
| Post-logout redirect URI | *not applicable* |

Drake's logout is `POST /v1/auth/logout`: it deletes the server-side
session and clears the cookie, and does not redirect to the IdP. There is
no front-channel logout route, so none should be registered.

**8. Fill in the deployment values.** Copy
`deploy/drake/values-cloudflare.test.yaml` as a shape reference into a
private values file and set the real image digests, the real
`api.existingSecret` name, and the real database/Redis CIDRs. Keep it out
of Git.

**9. Open the GitOps change and get it approved.** The rendered manifests
are the reviewed artifact.

**10. Deploy, then run the acceptance plan below.**

## Acceptance criteria (run after deployment)

| # | Check | Pass condition |
| --- | --- | --- |
| 1 | `kubectl -n drake-prod get pods -l app.kubernetes.io/component=cloudflared` | 2/2 Ready |
| 2 | Connector spread | The two pods report different `spec.nodeName` when both nodes are schedulable |
| 3 | `curl -I https://drake.duosis.com/` | 200, valid Cloudflare-issued TLS, no certificate warning |
| 4 | Browser loads the web UI | Renders, no mixed content, no console CORS error |
| 5 | `curl -i https://drake.duosis.com/v1/me` | **401**, and the response comes from FastAPI, not Next |
| 6 | Entra login → callback → session | Lands signed-in; cookie is `Secure`, `HttpOnly` |
| 7 | RBAC and tenant scope | An unprivileged user still gets uniform 404s, not data |
| 8 | Cancellation (Sprint 3) | Rapid metric-range changes stay bounded; a client disconnect frees server work. **This is the check that proves the new hop did not reintroduce the regression** |
| 9 | `curl -I https://wrong.duosis.com/` pointed at the tunnel | 404 from the catch-all, no application response |
| 10 | External port scan of node1/node2 | No new inbound port opened for Drake |
| 11 | Hermes-dev, Hermes-test, LogiSlot, Datalake endpoints | Unchanged behaviour, before and after |
| 12 | Delete one cloudflared pod | Traffic continues through the surviving connector |
| 13 | Scale cloudflared to 0 | Only Drake becomes unreachable; every other application is unaffected |
| 14 | Rollback | Reverting the GitOps commit removes only Drake and cloudflared resources |

## Rollback

Revert the GitOps commit. That removes the Drake and cloudflared resources
and nothing else — the chart touches no shared object, no ingress-nginx
resource, and no other namespace.

Rollback does **not** reverse a database migration; Drake has no production
downgrade path by design. It also does not delete the Cloudflare tunnel or
the DNS record. To withdraw the public endpoint entirely, additionally run
`cloudflared tunnel delete drake-prod` and remove the DNS record from
Cloudflare — both are operator actions outside the cluster.

Scaling `cloudflared` to 0 replicas is the fastest way to take Drake off
the internet without touching anything else.

## Residual risks

- **NetworkPolicy is not a proven boundary here.** This cluster runs
  Flannel, and policy enforcement has not been demonstrated. The chart
  ships policies and they are correct if something enforces them, but they
  must not be counted as a control. Concretely: *the cloudflared pod is not
  technically isolated at the network level from other ClusterIP services;
  the blast radius is reduced in this sprint by fixed local Tunnel routes, a
  pod holding no Kubernetes credentials, zero Kubernetes RBAC, and a
  catch-all 404 — not by network policy.*
- **Cloudflare becomes an availability and trust dependency.** It
  terminates TLS, so it sees plaintext application traffic. This is
  inherent to the approach, not a defect in the configuration.
- **A new proxy hop sits in front of the API.** cloudflared replaces
  ingress-nginx as the edge, and abort propagation through it is not
  something this repository can prove statically. Acceptance check 8 exists
  precisely to verify it against real traffic.
- **`drake.duosis.com` is a real, permanent hostname**, unlike the
  `sslip.io` placeholder considered in 5D-A. No temporary DNS dependency
  remains.
