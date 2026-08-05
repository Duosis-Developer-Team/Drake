# ADR-0003: The browser talks only to the Drake API

**Status:** Accepted

## Context

Dashboards that query telemetry backends directly from the browser leak
provider credentials, bypass authorization, and turn every user into an
unbounded query source.

## Decision

The web application has exactly one data source: the Drake API. The browser
never connects to Prometheus, Thanos, Alertmanager, or the Kubernetes API —
no direct URLs, no proxies/rewrites in the web tier. A static guard test in
`apps/web` fails the build if browser code references telemetry providers or
absolute backend URLs.

## Consequences

- Credentials for providers exist only server-side.
- Authorization, rate limiting, caching, and auditing happen in one place.
- All telemetry access flows through the Query Broker (ADR-0004).
