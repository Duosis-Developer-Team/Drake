# Sprint 3 — Observability Report

What Drake can honestly observe after this sprint, and what it cannot.

## 1. What works now

- **Versioned registries** (`packages/contracts/registry/`): 7 metrics,
  10 Prometheus query templates (service + environment scopes),
  2 dashboard templates — schema-validated, cross-reference-checked,
  content-hashed. Changing telemetry means changing a reviewed file, not
  a runtime toggle.
- **Query Broker**: `POST /v1/telemetry/query` compiles trusted templates
  with catalog-derived matchers, enforces budgets, caches normalized
  envelopes in Redis (fresh + last-good), and updates the integration
  observation projection on real provider calls.
- **Live local telemetry**: the docker-compose stack includes Prometheus
  v3.5.0 (digest-pinned, 127.0.0.1:59090, 3d retention) scraping a
  deterministic fixture exporter; integration tests and E2E run against
  real scraped data. `tenant.storage.logical_bytes` stays snapshot-sourced
  and is structurally barred from the Prometheus broker.
- **Web**: Project Overview renders the environment-overview dashboard for
  one selected authorized environment (URL `?env=`); service detail
  renders golden signals via `metrics_profile`; time range presets
  (1h/24h/7d) live in the URL. Widgets distinguish loading / empty / zero
  / stale / partial / not-configured / denied / unavailable and carry
  screen-reader summaries plus data-table fallbacks.
- **Broker self-observability**: bounded internal counters/histograms
  (query count/outcome/cache state, duration, returned points,
  rejections) at `/v1/internal/metrics` — never for public ingress.

## 2. State semantics (unchanged law, now with data)

`empty`, `zero`, `stale`, `partial`, `not_configured`, and `unavailable`
are distinct: a configured provider with no series is `empty`; a null
point from a non-finite value marks the response `partial`; a provider
outage serves last-good as `stale` (with its `as_of`) or an explicit
retryable `unavailable` — never fabricated zeros, never stale-as-healthy.

## 3. Cache & freshness policy

- Fresh TTL: template-defined (30s for v1 templates); stale last-good TTL
  15 min; provider errors are never cached; empty successes may be.
- Cache keys bind registry content hash, template key+version,
  integration configuration identity, authorized scope, normalized
  matchers/parameters, aligned window, and effective step — cross-project
  reuse is impossible (negative-tested).
- Observation freshness: a success older than 5 minutes turns later
  failures into `stale` instead of `degraded`.

## 4. Deployment posture

- **Nothing was deployed.** The kube-prometheus-stack dev package
  (pinned 88.1.5) is rendered and policy-checked in CI only; applying it
  to a real cluster requires explicit operator approval.
- Known upstream limitation, reported honestly: the chart pins its
  sub-images by tag, not digest; mirroring/digest-pinning is part of the
  real onboarding gate.

## 5. What Drake still cannot see (honest list)

- Real Hermes cluster telemetry (Sprint 5: real provider onboarding).
- Kubernetes inventory/workloads — Cluster Agent enrollment is Sprint 4.
- Logs, traces (Loki/Tempo/OTel), Thanos long-term storage: later.
- Deployment correlation, tenant metering, backup evidence, alert
  projection: later sprints, already modeled as `not_configured`.
- Cluster-scope dashboards: the API supports cluster-scoped queries, but
  no v1 template targets clusters yet — added when the agent lands.
