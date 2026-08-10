# ADR-0022 — Service health from bound workloads

**Status:** accepted (Sprint 5)

## Context

Drake knew two things separately. The catalog knew that a project has
environments and that an environment runs services. The inventory knew that
a cluster has namespaces and that a namespace runs workloads. Nothing
joined them, so "is this service healthy?" could only have been answered by
guessing — usually by matching names and hoping.

Guessing is the failure mode worth avoiding here. A monitoring product that
infers the wrong workload reports confident health for something nobody
asked about, and the mistake is invisible precisely because the answer
looks normal.

## Decision

**The join is stored, not inferred.** A `service_workload_bindings` row
says which cluster, namespace, kind and name a service runs as. An operator
chooses it from inventory; Drake never pattern-matches a name.

**Health is computed in one place, from typed thresholds.** A pure function
takes signals and returns a status, reason codes and per-section detail.
It performs no I/O and reads no clock it was not given, so every threshold
is testable at a boundary without a cluster or a Prometheus. The frontend
renders a decision it was handed; it never re-derives one, so wording can
change without changing what a user sees as healthy.

**Absence is never health.** The status set is deliberately wider than
healthy/unhealthy:

| Status | Means |
| --- | --- |
| `healthy` | Every signal read is within policy |
| `degraded` | At least one signal is outside policy |
| `critical` | Availability lost, crash looping, or a hard threshold breached |
| `unknown` | Drake could not find out — unresolved binding, unreachable datasource, failed query |
| `stale` | The newest sample is older than the policy allows |
| `not_configured` | Nothing has been bound, or the signal is not published |

Five rules follow from that, and each has a test:

- An empty result is not zero.
- A missing metric is not healthy.
- A failed query is a Drake problem, not a workload problem — it can never
  produce `critical`.
- Stale telemetry cannot support a healthy verdict, but it does not soften
  a bad one: old evidence of failure is still evidence of failure.
- One missing optional signal does not make a service critical. A workload
  that publishes no HTTP metrics is reported as `not_configured` for golden
  signals, and judged on the signals it does emit.

**Queries stay curated.** A binding stores a preset key, not an expression.
Presets name sets of registry template keys; the expressions live in the
reviewed registry and are validated on load. There is no path from a
binding, a request parameter, or a browser to arbitrary PromQL.

**Matchers come from server state.** A new `workload` scope resolves a
binding id into project, environment, service, cluster, namespace, workload
name and kind. The caller supplies an id; every label a query matches on
comes from the row behind it.

## The read path

    binding → preset + policy → curated templates → Query Broker
            → typed signals → health engine → API response

The orchestrator sits between a provider that answers vaguely and an engine
that answers precisely, so it is where every ambiguity is resolved — once,
in one place:

- An empty result leaves its signal `None`. It is reported as missing, not
  as `0`. This is the failure mode the layer exists for: reading an absent
  `replicas-ready` as zero would page for a healthy service.
- A broker error sets telemetry state, never a signal value. Every query
  failing yields `unknown` + `datasource_unavailable`; one failing yields a
  `partial` result that cannot become `critical`.
- A datasource answering "not configured" for everything is
  `not_configured` — distinct from failing, and distinct from empty.
- A binding that is disabled or unresolved is answered without querying at
  all: the verdict is already determined.

Queries for one read run concurrently under a small semaphore, and a
cancelled read cancels and awaits every query it started, so no provider
work outlives the request that asked for it.

## Caching decision

Verdicts are cached, and **invalidated by identity rather than by
deletion**: the key hashes everything a verdict depends on — binding
revision, resolved workload uid, preset, policy, datasource configuration,
registry hash, project/environment/service, window and step. A mutation
bumps the revision, so the previous answer stops being addressable. There
is no delete to miss, lose, or race.

The last-good fallback is written only from live readings and never deleted
by a failure. It is served with its original `computed_at` plus a separate
`served_at` and `age_seconds`; a `healthy` verdict served this way becomes
`stale`, while `degraded` and `critical` survive intact. Restamping
`computed_at` would present an old answer as current, which is worse than
showing nothing.

## Consequences

- A deployment whose Prometheus uses different metric names is a new
  preset, not a new code path — which is why the Hermes pilot is a preset
  with no namespace or workload name baked into it.
- Health for an unbound service is `not_configured`, immediately and
  honestly, rather than an empty chart that reads as calm.
- Thresholds are versioned policies. Changing one is a reviewable diff with
  boundary tests, not an edit to a condition inside a component.
- Drake still stores no telemetry. Prometheus remains the source of signal;
  Drake is the control plane that gives it domain meaning.

## Alternatives considered

**Infer the workload from naming conventions.** Rejected: it works until
two services share a prefix, and the failure is silent.

**Compute health in the frontend.** Rejected: two clients would drift, and
a threshold would be unreviewable and untestable. The UI therefore receives
a status and reason codes and renders them; it holds no threshold and does
no arithmetic on a status.

**Restamp last-good answers as current.** Rejected: it makes an hour-old
reading indistinguishable from a fresh one, which is the precise deception
the freshness contract exists to prevent.

**Let operators write PromQL per service.** Rejected: it turns the binding
form into a query console, makes every response a potential label leak, and
puts cardinality limits beyond Drake's control.
