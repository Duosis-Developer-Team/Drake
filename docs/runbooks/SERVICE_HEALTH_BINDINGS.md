# Binding a service to its workload

Drake computes service health from the Kubernetes workload that actually
runs the service. That link is configured once per service, per
environment, and stored — Drake never guesses it from names.

## What you need first

- The service exists in the catalog for the environment you are binding.
- The cluster is registered and its agent has reported inventory. If it has
  not, the binding is still created and reports `unresolved` until the
  workload is seen.
- A telemetry datasource is configured, if you want anything beyond
  binding status.

## Creating the binding

```
POST /v1/service-health/bindings
{
  "environment_service_id": "<uuid>",
  "cluster_id": "<uuid>",
  "namespace": "hermes-dev",
  "workload_kind": "Deployment",
  "workload_name": "hermes-frontend",
  "preset_key": "hermes.pilot.v1",   // gitleaks:allow - preset id, not a credential
  "health_policy_key": "default.v1"
}
```

`GET /v1/service-health/presets` lists the presets, policies and supported
workload kinds. Supported kinds are `Deployment`, `StatefulSet` and
`DaemonSet`.

Two bindings for the same service and workload are refused (409). Disable
a binding rather than deleting it:

```
POST /v1/service-health/bindings/{id}/lifecycle
{"lifecycle": "disabled", "expected_revision": 1}
```

`expected_revision` is optional; supplying it makes the change fail rather
than overwrite someone else's edit.

To re-check inventory after an agent has reported:

```
POST /v1/service-health/bindings/{id}/resolve
```

A workload that has disappeared from inventory clears the resolution marker
but **keeps the binding**. An agent outage is not a reason to discard your
configuration.

## Reading health

Once a binding exists, four endpoints answer for it. All of them are reads:
none takes a query, a metric name, a label matcher or a time step.

```
GET /v1/service-health/services?environment_id=<uuid>
GET /v1/service-health/bindings/{id}/health[?refresh=true]
GET /v1/service-health/bindings/{id}/metrics
GET /v1/service-health/bindings/{id}/series?signal=cpu_usage&range=1h
```

**`/services`** lists every service in scope with its verdict. Services
with no binding appear with `binding: null` and a `not_configured` status —
they are never omitted, because a list that hid them would make an
unobserved estate look like a healthy one.

**`/health`** is the verdict: status, `computed_at`, `newest_sample_at`,
`freshness_age_seconds`, reason codes with their sentences, the list of
signals that were not measured, and the four section breakdowns. `refresh=true`
recomputes instead of using the cached verdict.

**`/metrics`** is the per-signal detail. Every value may be `null`, and
`null` never means zero — each signal carries its own state:

| Signal state | Means |
| --- | --- |
| `ok` | Measured |
| `empty` | Queried, and the datasource returned no samples |
| `failed` | The query did not complete |
| `not_configured` | No datasource to query |
| `not_collected` | This preset does not read this signal at all |
| `stale` | Served from the broker's last-good response |

**`/series`** charts one signal. `signal` must be one the binding's preset
actually reads (anything else is 404) and `range` is one of `15m`, `1h`,
`6h`, `24h` (anything else is 422). Series count is capped at 12 and labels
are filtered again on the way out; if either bound trims the answer, the
response says so via `series_truncated` and `partial`.

Editing how a binding is read:

```
POST /v1/service-health/bindings/{id}
{"preset_key": "http.service.v1", "health_policy_key": "tolerant.v1", "expected_revision": 3}
```

Only the preset and policy are editable. Pointing a service at a different
workload is a new binding, so a health history always refers to one thing.
A stale `expected_revision` is refused with 409 rather than overwriting
someone else's edit.

## Caching and last-good

Verdicts are cached for 30 seconds and kept as a fallback for 15 minutes.

**Invalidation is by identity, not by deletion.** The cache key includes the
binding's revision and resolved workload uid, the preset, the policy, the
datasource's configuration, the registry content hash, and the
project/environment/service. Any edit bumps the revision, so the next read
computes a different key and *cannot* reach the pre-mutation verdict. There
is no window where a stale answer is still addressable.

**A failure never destroys the last good answer.** When the datasource is
unreachable, the last successful verdict is served with:

- `served_from_last_good: true` and `partial: true`
- `computed_at` unchanged — the moment the answer was actually true
- `served_at` and `age_seconds` saying how long ago that was

A `healthy` verdict served this way is downgraded to `stale`; a `degraded`
or `critical` one keeps its status, because a service that was failing when
last observed is not improved by the observation being old. With no
last-good available the answer is `unknown` — never an invented one.

## The screens

- **Service health** (`/service-health`) — every service in scope with its
  status, binding state, cluster/namespace/workload, ready/desired,
  restarts, CPU and memory, and when it was last computed.
- **Service detail** (`/service-health/{bindingId}`) — the four sections
  with their own statuses, the reason codes as text, what was not measured,
  the binding summary, and per-signal charts with a fixed range selector.
- **Binding form** (`/service-health/bind?environment_service_id=…`) —
  dependent cluster → namespace → workload selects, every option drawn from
  cluster inventory. Changing a cluster clears the namespace and workload;
  changing a namespace clears the workload. There is no text input on this
  form, so there is nothing to type a selector, a regex or a PromQL
  fragment into.

The frontend computes no health. It renders the typed response, maps reason
codes to wording, and shows `—` wherever a value is absent — never `0`.

## From health to incidents

A `critical` verdict does not stay a verdict. Drake's evaluation runner
turns repeated, trustworthy ones into an incident with its own lifecycle —
see [INCIDENT_LIFECYCLE.md](INCIDENT_LIFECYCLE.md). The rule that matters
here: a verdict that is partial, stale, served from last-good, or produced
while the datasource was unreachable never opens an incident.

## Metric presets

A preset names a set of curated queries. It never contains an expression,
and it never contains a namespace or workload name — those come from the
binding, so one preset fits every application that publishes the same
metrics.

| Preset | Reads |
| --- | --- |
| `kubernetes.baseline.v1` | Replicas, restarts, CPU/memory, throttling, scrape freshness |
| `http.service.v1` | The baseline plus request rate, error ratio and p95 latency |
| `hermes.pilot.v1` | The HTTP preset, selected for the first pilot onboarding |

If your Prometheus exposes these under different metric names, the fix is a
new preset in the curated registry — not a per-service query.

## Health statuses

| Status | Means |
| --- | --- |
| `healthy` | Every signal read is within policy |
| `degraded` | At least one signal is outside policy |
| `critical` | Availability lost, crash looping, or a hard threshold breached |
| `unknown` | Drake could not find out |
| `stale` | The newest sample is older than the policy allows |
| `not_configured` | Nothing bound, or the signal is not published |

`unknown`, `stale` and `not_configured` are **not** healthy. A failed query
or an unreachable datasource produces `unknown`, never `critical` — that is
Drake failing to look, not the workload failing.

## Reason codes

Machine-readable, and the API ships a sentence for each. Do not parse the
text; match the code.

`no_binding` · `binding_disabled` · `binding_unresolved` ·
`datasource_not_configured` · `datasource_unavailable` · `telemetry_stale` ·
`query_failed` · `partial_result` · `no_ready_replicas` ·
`partial_availability` · `rollout_incomplete` · `restart_spike` ·
`oom_killed` · `crash_loop` · `cpu_pressure` · `memory_pressure` ·
`cpu_throttling` · `high_error_rate` · `high_latency` ·
`application_metrics_missing`

## Health policies

| Policy | For |
| --- | --- |
| `default.v1` | Interactive services: any missing replica is degraded, telemetry older than 5 minutes is stale |
| `tolerant.v1` | Batch and background workloads: slower rollouts and occasional restarts are expected |

A workload with no CPU or memory **limit** configured is not judged on
utilisation ratios at all. Inventing a denominator would manufacture
pressure nobody configured.

## Connecting the Hermes pilot to a real datasource

The preset and the contract are in place; pointing them at a live
Prometheus is a deployment step:

1. Register the Prometheus datasource as an integration at the project or
   environment scope, with its credentials in a Secret — never in a values
   file or a binding.
2. Confirm the cluster agent has reported inventory for the Hermes
   namespace, so the workload can be selected rather than typed.
3. Create the binding with `preset_key: hermes.pilot.v1`, choosing the <!-- gitleaks:allow - preset id, not a credential -->
   namespace and workload from inventory.
4. Call the resolve endpoint and confirm `resolved: true`.
5. If Hermes does not publish `http_server_requests_total` and
   `http_server_request_duration_seconds_bucket`, golden signals will
   report `application_metrics_missing`. That is accurate, not a failure:
   the infrastructure signals still produce a health verdict.
6. Read `GET /v1/service-health/bindings/{id}/health`. Until step 1 is
   done it answers `not_configured` with `datasource_not_configured`, which
   is the honest state — Drake does not fabricate telemetry, and there is
   no demo or sample mode that would make an unconnected pilot look live.

If the datasource is configured but unreachable, the verdict is `unknown`
with `datasource_unavailable`, or `stale` once a previous reading exists.
Neither is `critical`: an outage on Drake's side is never reported as an
outage in the estate.
