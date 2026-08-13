# Hermes — Prometheus metrics for Drake

Hand this whole file to the Hermes session. It is self-contained.

---

## Context

Our platform team runs **Drake**, a Kubernetes observability control plane. It
already watches this cluster read-only and shows Hermes' CPU, memory, restarts,
pod health, replica counts and container-waiting reasons today — all of that
comes from Kubernetes itself and needs nothing from you.

What Drake **cannot** show is request rate, error ratio and p95 latency,
because those only exist if the application emits them. Nothing else is
missing: the scrape job, the storage, the queries and the dashboards are all
deployed and waiting for the series to appear.

**Your task: make Hermes emit them.**

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
| `project` | `hermes` | constant |
| `environment` | `dev` or `test` | from an env var, not hardcoded |
| `service` | the service's own key, e.g. `core-service` | request rate is grouped by this |

`environment` must be the **catalog key**, not the namespace. So `dev`, not
`hermes-dev`. This is the single most likely mistake.

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
  rate(http_server_requests_total{project="hermes",environment="dev"}[5m]))

# error ratio
sum(rate(http_server_requests_total{status_class="5xx",project="hermes",environment="dev"}[5m]))
  / sum(rate(http_server_requests_total{project="hermes",environment="dev"}[5m]))

# p95 latency
histogram_quantile(0.95, sum by (le) (
  rate(http_server_request_duration_seconds_bucket{project="hermes",environment="dev"}[5m])))
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
- Namespaces in play: **`hermes-dev` and `hermes-test` only.**

### Ignore the `hermes` namespace

There is a third namespace called plain `hermes` in the cluster. It is the old
deployment and is not in active use, so leave it alone — do not instrument it,
and do not add annotations to anything in it. Drake's catalog registers only
`dev` and `test` for this project, which matches.

---

## What to report back

- which services you instrumented, and which you did not, and why
- the metrics port you chose
- the exact label values each service emits

We will run the three queries against production Prometheus and confirm before
calling it done. If `up{project="hermes",environment="dev"}` returns 1, the
target is being scraped.

---

## One thing Drake found in your active namespaces

Unrelated to this task: `hermes-dev` and `hermes-test` have a handful of
`Unhealthy` Kubernetes warning events, which usually means a readiness probe is
failing intermittently. Worth a look, but nothing is on fire.

(Drake also reports `core-service` in `ImagePullBackOff` with
`FailedToRetrieveImagePullSecret`. That is in the retired `hermes` namespace,
so it is noise rather than an incident — mentioned only so it is not mistaken
for a live problem when it shows up on a screen.)

---

## If a query comes back empty

In order of likelihood:

1. the annotation is on the Service instead of the pod template
2. the label is `env` rather than `environment`
3. the `environment` value is the namespace (`hermes-dev`) rather than the
   catalog key (`dev`)
4. the histogram is in milliseconds rather than seconds
5. `status_class` carries the full status code (`500`) rather than the class
   (`5xx`)
