# Drake golden-signal instrumentation — prompts for Hermes and LogiSlot

Drake observes these clusters read-only and already shows CPU, memory,
restarts, container-waiting reasons and replica health for every workload.
What it cannot show is **request rate, error ratio and p95 latency**, because
those only exist if the application emits them. Nothing else is missing: the
scrape job, the storage, the dashboards and the queries are all in place and
waiting for the series.

The contract below is not a suggestion. Drake's query registry is a reviewed
contract and queries exactly these names and labels; a metric that differs by
one label name will be collected and never displayed.

---

## The contract (identical for both repositories)

### Two metrics

```
http_server_requests_total              counter
http_server_request_duration_seconds    histogram   (…_bucket is what is queried)
```

### Required labels on both

| label | value | why |
|---|---|---|
| `project` | `hermes` or `logislot` | Drake filters every environment query on this |
| `environment` | `dev`, `test`, `prod` | ditto — must match the catalog key, not the namespace |
| `service` | the service's own key | request rate is broken down by this |

`http_server_requests_total` additionally needs:

| label | value |
|---|---|
| `status_class` | `2xx`, `3xx`, `4xx`, `5xx` — the CLASS, not the code |

### Labels that must NOT appear

`pod`, `container`, `instance`, `tenant`, `customer`, `route`, `path`, user
ids, emails, request ids, or anything else unbounded. Drake's metric catalog
forbids them and a high-cardinality label will melt the time series database
long before anyone reads the dashboard. Aggregate the path away — if
per-endpoint breakdown is wanted later, that is a separate conversation with a
bounded, allow-listed set of route names.

### Exactly how Drake queries them

```promql
sum by (service) (rate(http_server_requests_total{project="…",environment="…"}[5m]))

sum(rate(http_server_requests_total{status_class="5xx",project="…",environment="…"}[5m]))
  / sum(rate(http_server_requests_total{project="…",environment="…"}[5m]))

histogram_quantile(0.95, sum by (le) (
  rate(http_server_request_duration_seconds_bucket{project="…",environment="…"}[5m])))
```

If those three run in your Prometheus and return data, the work is done.

### The pod must be scrapeable

Prometheus discovers targets from **pod** annotations. They go on the pod
template of the Deployment/StatefulSet, not on the Service:

```yaml
spec:
  template:
    metadata:
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/port: "<the metrics port>"
        prometheus.io/path: "/metrics"
```

Verified against the live scrape config: the job keeps only pods carrying
`prometheus.io/scrape: "true"`, and reads `prometheus.io/port` and
`prometheus.io/path` from the same annotations.

### Do not expose /metrics publicly

The endpoint belongs on the pod's own port, reachable in-cluster. It must not
be routed through the public ingress. It contains no secrets, but it does
describe your traffic shape, and there is no reason for it to leave the
cluster.

---

## Prompt for the Hermes session

> Our platform team runs **Drake**, a Kubernetes observability control plane
> that already watches this cluster read-only. It shows Hermes' CPU, memory,
> restarts and pod health today. It cannot show request rate, error ratio or
> p95 latency, because Hermes does not emit them.
>
> Please instrument the Hermes services to expose Prometheus metrics.
>
> **Scope: this repository's application code and its Kubernetes manifests
> only.** Do not change anything in other teams' namespaces.
>
> Emit exactly two metrics, with exactly these names:
>
> - `http_server_requests_total` — a counter, incremented once per completed
>   HTTP request
> - `http_server_request_duration_seconds` — a histogram of request duration
>   in **seconds** (not milliseconds)
>
> Both must carry these labels:
>
> - `project="hermes"`
> - `environment` — `dev` or `test`, matching the deployment, taken from an
>   environment variable rather than hardcoded
> - `service` — the service's own name, e.g. `core-service`
>
> The counter additionally needs `status_class`, set to `2xx`/`3xx`/`4xx`/`5xx`.
> The **class**, not the status code.
>
> Do NOT add labels for path, route, pod, container, instance, user, tenant or
> request id. Those are unbounded and will overwhelm the metrics backend; the
> platform's metric catalog explicitly forbids them.
>
> Add the scrape annotations to the pod template of every instrumented
> workload:
>
> ```yaml
> prometheus.io/scrape: "true"
> prometheus.io/port: "<metrics port>"
> prometheus.io/path: "/metrics"
> ```
>
> Keep `/metrics` on an in-cluster port. Do not route it through the public
> ingress.
>
> Namespaces in play: `hermes-dev` and `hermes-test`. There is also a `hermes`
> namespace; if a service runs there, tell us which environment key it should
> report rather than guessing.
>
> **Please also report back:** which services you instrumented, which you did
> not and why, the metrics port you chose, and the exact label values each
> service emits. We will verify the three queries above return data before
> calling it done.
>
> Two unrelated things Drake found in your namespaces that you may want to
> look at independently: `hermes/core-service` is in `ImagePullBackOff`, and
> the Kubernetes event explaining it says `FailedToRetrieveImagePullSecret`.

---

## Prompt for the LogiSlot session

> Our platform team runs **Drake**, a Kubernetes observability control plane
> that already watches this cluster read-only. It shows LogiSlot's CPU,
> memory, restarts and pod health today. It cannot show request rate, error
> ratio or p95 latency, because LogiSlot does not emit them.
>
> Please instrument the LogiSlot services to expose Prometheus metrics.
>
> **Scope: this repository's application code and its Kubernetes manifests
> only.** Do not change anything in other teams' namespaces.
>
> Emit exactly two metrics, with exactly these names:
>
> - `http_server_requests_total` — a counter, incremented once per completed
>   HTTP request
> - `http_server_request_duration_seconds` — a histogram of request duration
>   in **seconds** (not milliseconds)
>
> Both must carry these labels:
>
> - `project="logislot"`
> - `environment` — `dev` or `prod`, matching the deployment, taken from an
>   environment variable rather than hardcoded
> - `service` — the service's own name
>
> The counter additionally needs `status_class`, set to `2xx`/`3xx`/`4xx`/`5xx`.
> The **class**, not the status code.
>
> Do NOT add labels for path, route, pod, container, instance, user, tenant or
> request id. Those are unbounded and will overwhelm the metrics backend; the
> platform's metric catalog explicitly forbids them.
>
> Add the scrape annotations to the pod template of every instrumented
> workload:
>
> ```yaml
> prometheus.io/scrape: "true"
> prometheus.io/port: "<metrics port>"
> prometheus.io/path: "/metrics"
> ```
>
> Keep `/metrics` on an in-cluster port. Do not route it through the public
> ingress.
>
> Namespaces in play: `logislot-dev` and `logislot-prod`.
>
> **Please also report back:** which services you instrumented, which you did
> not and why, the metrics port you chose, and the exact label values each
> service emits. We will verify the three queries above return data before
> calling it done.
>
> One unrelated thing Drake found in your namespaces that you may want to look
> at independently: `logislot-prod` has accumulated 12 `Failed` and 13
> `Unhealthy` Kubernetes warning events.

---

## How we verify on our side

Once either team reports back, we run their three queries against the
production Prometheus. If `up{project="…",environment="…"}` returns 1, the
target is being scraped; if the rate queries return series, the dashboards
fill in with no further change on Drake's side — the panels are already
deployed and waiting.

If a query returns nothing, the usual causes in order of likelihood: the pod
annotation is on the Service instead of the pod template, the label is
`env` rather than `environment`, the histogram is in milliseconds, or
`status_class` carries the full status code.
