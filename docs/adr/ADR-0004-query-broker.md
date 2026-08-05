# ADR-0004: Query Broker with templates and budgets

**Status:** Accepted

## Context

Free-form PromQL from users is an injection and denial-of-service surface:
unbounded ranges, exploding series, and hostile regexes can take down the
metrics backend for everyone.

## Decision

All telemetry queries go through a server-side Query Broker. Users select
versioned query templates with typed parameters; parameters can only become
allowlisted label matchers — never string-concatenated query fragments. Every
query carries enforced budgets: maximum time range, minimum step, series
limit, timeout, and per-user/project concurrency. Authorization scope is
resolved before any provider call.

## Consequences

- No user-supplied PromQL anywhere in v1.
- New visualizations are added by publishing templates, not by widening
  query freedom.
- Query costs are observable and attributable; abuse tests are part of CI
  from the telemetry sprint on.
