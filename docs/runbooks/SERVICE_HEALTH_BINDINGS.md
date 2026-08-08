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
  "preset_key": "hermes.pilot.v1",
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
3. Create the binding with `preset_key: hermes.pilot.v1`, choosing the
   namespace and workload from inventory.
4. Call the resolve endpoint and confirm `resolved: true`.
5. If Hermes does not publish `http_server_requests_total` and
   `http_server_request_duration_seconds_bucket`, golden signals will
   report `application_metrics_missing`. That is accurate, not a failure:
   the infrastructure signals still produce a health verdict.
