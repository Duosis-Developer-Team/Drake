# LogiSlot — Prometheus metrics for Drake

Hand this whole file to the LogiSlot session. It is self-contained.

---

## Context

Our platform team runs **Drake**, a Kubernetes observability control plane. It
already watches this cluster read-only and shows LogiSlot's CPU, memory,
restarts, pod health, replica counts and container-waiting reasons today — all
of that comes from Kubernetes itself and needs nothing from you.

What Drake **cannot** show is request rate, error ratio and p95 latency,
because those only exist if the application emits them. Nothing else is
missing: the scrape job, the storage, the queries and the dashboards are all
deployed and waiting for the series to appear.

**Your task: make LogiSlot emit them.**

---

## The contract

Drake's query registry is a reviewed contract. It queries **exactly** these
names and labels. A metric that differs by one label name will be collected
and never displayed — that is the failure mode to avoid, because it looks like
success until someone notices the dashboard is still empty weeks later.

### Two metrics

```
http_server_requests_total              counter
http_server_request_duration_seconds    histogram   (seconds — NOT milliseconds)
```

### Labels on both metrics

| label | value | notes |
|---|---|---|
| `project` | `logislot` | constant |
| `environment` | `dev` or `prod` | from an env var, not hardcoded |
| `service` | the service's own key | request rate is grouped by this |

`environment` must be the **catalog key**, not the namespace. So `prod`, not
`logislot-prod`. This is the single most likely mistake.

### One extra label on the counter

| label | values |
|---|---|
| `status_class` | `2xx`, `3xx`, `4xx`, `5xx` |

The **class**, not the status code. `500` is wrong; `5xx` is right.

### Labels that must NOT appear

`pod`, `container`, `instance`, `route`, `path`, `tenant`, `customer`, user
ids, emails, request ids — or anything else unbounded.

This is not style advice. Drake's metric catalog rejects these, and an
unbounded label multiplies the series count without limit; it would overwhelm
the metrics backend long before anyone read a dashboard. Aggregate the path
away. If per-endpoint breakdown is wanted later, that is a separate
conversation with a bounded, allow-listed set of route names.

---

## Exactly how Drake queries them

If these three run in your Prometheus and return data, you are done.

```promql
# request rate
sum by (service) (
  rate(http_server_requests_total{project="logislot",environment="prod"}[5m]))

# error ratio
sum(rate(http_server_requests_total{status_class="5xx",project="logislot",environment="prod"}[5m]))
  / sum(rate(http_server_requests_total{project="logislot",environment="prod"}[5m]))

# p95 latency
histogram_quantile(0.95, sum by (le) (
  rate(http_server_request_duration_seconds_bucket{project="logislot",environment="prod"}[5m])))
```

---

## Making the pod scrapeable

Prometheus discovers targets from **pod** annotations. They go on the pod
template of the Deployment/StatefulSet — **not** on the Service. Putting them
on the Service is the second most likely mistake and produces exactly the same
symptom as emitting nothing.

```yaml
spec:
  template:
    metadata:
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/port: "<your metrics port>"
        prometheus.io/path: "/metrics"
```

These names were read from the live scrape config: the job keeps only pods
carrying `prometheus.io/scrape: "true"` and reads the port and path from the
same annotation set.

### Do not publish /metrics

Keep it on an in-cluster port. Do not route it through the public ingress. It
holds no secrets, but it describes your traffic shape and has no reason to
leave the cluster.

---

## Scope and safety

- **This repository's application code and its Kubernetes manifests only.**
  Do not change anything in other teams' namespaces.
- The metrics themselves are cheap, but HTTP middleware sits in the request
  path. A badly written one adds latency or drops requests. Treat this like
  any other change to a live service: normal review, normal rollout, not
  straight to production.
- Namespaces in play: `logislot-dev` and `logislot-prod`.
- `logislot-prod` is a production namespace. Instrument `logislot-dev` first,
  confirm the queries return data there, then promote.

---

## What to report back

- which services you instrumented, and which you did not, and why
- the metrics port you chose
- the exact label values each service emits

We will run the three queries against production Prometheus and confirm before
calling it done. The three queries ARE the proof.

Do NOT check `up{project="logislot",environment="prod"}`. `up` is synthesised by
Prometheus from the scrape itself, not emitted by your application, so it
never carries your application's labels and will always come back empty.
That is expected and is not a failure. This document said otherwise until
the LogiSlot team caught it while verifying their own work.

---

## Something Drake found in your namespaces

Unrelated to this task, but worth looking at independently: `logislot-prod` has
accumulated **12 `Failed`** and **13 `Unhealthy`** Kubernetes warning events,
and `logislot-dev` has **104 `Unhealthy`** ones. `Unhealthy` at that volume
usually means a readiness or liveness probe is failing repeatedly — which also
means those pods may be cycling in and out of service without anyone noticing.

---

## If a query comes back empty

In order of likelihood:

1. the annotation is on the Service instead of the pod template
2. the label is `env` rather than `environment`
3. the `environment` value is the namespace (`logislot-prod`) rather than the
   catalog key (`prod`)
4. the histogram is in milliseconds rather than seconds
5. `status_class` carries the full status code (`500`) rather than the class
   (`5xx`)
