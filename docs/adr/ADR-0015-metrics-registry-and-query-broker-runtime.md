# ADR-0015 — Metrics registry and Query Broker runtime

Status: accepted (Sprint 3)
Builds on: ADR-0001 (control plane), ADR-0003 (browser boundary),
ADR-0004 (query broker), ADR-0011 (state semantics), ADR-0014 (catalog
authority).

## Context

Sprint 3 connects Drake to its first telemetry provider (Prometheus). The
naive designs — proxying provider APIs to the browser, accepting user
PromQL, or copying samples into PostgreSQL — each destroy a boundary Drake
exists to keep. This ADR fixes the runtime rules before any provider is
reachable.

## Decision

### 1. Drake stores no samples

Drake is a control plane, not a metric sample store. Raw samples, PromQL
strings, and provider responses are never written to PostgreSQL. PostgreSQL
holds only registry-referenced catalog/integration state and the bounded
integration observation projection (states, timestamps, bounded error
codes). Redis holds only bounded, normalized, already-safe response
envelopes (fresh cache + last-good) and budget/lease bookkeeping.

### 2. The browser talks only to Drake

The browser never connects to Prometheus (or any provider). Provider URLs,
credentials, and `config_ref` values never appear in any API response,
cache entry, log line, error message, or client bundle. The web
provider-access guard enforces this statically in CI.

### 3. No user PromQL — versioned templates only

Users cannot submit PromQL, metric names, label names, regexes, operators,
or query fragments. Every query is compiled from a **versioned,
repository-controlled query template** (reviewed like code). Template
parameters are bounded and schema-validated with `extra=forbid`. Trusted
constant regex/operators inside a template are allowed; callers can never
influence them.

### 4. Scope matchers derive from the catalog

Matcher label names come from the template registry; matcher **values**
come from authoritative catalog rows (project/environment/service/cluster
keys resolved by opaque id). Callers never send keys. Values pass through
one central exact-match escaping function. The compiler is deterministic:
same input, same query.

### 5. Authorization before everything

The strict order is: session → request shape → authoritative scope lookup
→ `telemetry.query` effective grants → catalog relationship checks →
template/scope compatibility → provider integration resolution → cache →
budgets → provider call → normalization → response. No cache lookup,
provider config lookup, or provider call happens before authorization
succeeds; unauthorized callers get the consistent 404/403 semantics of
ADR-0014 and cause **zero** provider calls and zero cache reads.

### 6. Budgets are fail-closed

Range, step, series, point, response-size, timeout, and concurrency
budgets are enforced server-side; templates may narrow but never widen the
global ceiling. Concurrency uses atomic Redis leases (unique token,
bounded TTL, self-release only, stale recovery). **If the Redis budget
layer is unavailable, the query is refused with a typed retryable 503 —
budgets are never bypassed.**

### 7. Honest, distinct states

`empty`, `zero`, `stale`, `partial`, `not_configured`, and `unavailable`
are different states and are rendered differently. Last-good data served
during a provider outage is always labelled `stale` with its `as_of`;
stale is never presented as ok/healthy. Non-finite values (`NaN`, `±Inf`)
are never coerced to `0`: they become `null` points with a `partial` flag
and a bounded warning code.

### 8. Providers are server-owned connectors

`integrations.config_ref` is only a reference name resolved by a
server-owned connector resolver (environment/external-secret backed;
dependency-injected fakes in tests). The adapter enforces an SSRF
boundary: scheme allowlist, no embedded credentials, no redirects,
metadata/link-local/multicast/unspecified targets always refused,
plaintext HTTP refused outside local/test, private networks only via
explicitly allowed server-owned connectors, bounded body/timeouts, strict
JSON validation, raw upstream errors redacted to bounded codes.

### 9. Registries fail closed

Metric, query-template, and dashboard-template registries are validated at
boot: shaped keys, immutable versions, duplicate rejection,
cross-reference resolution, forbidden-label denylist, deterministic
ordering, and a content hash that participates in cache keys. A malformed
registry refuses to serve telemetry rather than serving it loosely.

### 10. Real cluster changes need operator approval

The kube-prometheus-stack dev package is rendered and policy-checked in CI
only. Applying it to a real cluster is a separate, operator-approved step
that no Sprint 3 automation performs.

## Consequences

- New capability = new registry entry + review, never a runtime toggle.
- The provider can change (Thanos, VictoriaMetrics) behind the adapter
  without touching authorization, budgets, or the browser contract.
- Query latency includes broker overhead; budgets and caching keep it
  bounded and predictable.
- Users cannot explore arbitrary metrics from Drake — by design; Grafana
  remains the tool for ad-hoc exploration by operators with direct access.
