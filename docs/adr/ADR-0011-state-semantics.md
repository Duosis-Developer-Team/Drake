# ADR-0011: Unknown/stale/partial state semantics

**Status:** Accepted

## Context

The most damaging observability failure mode is false confidence: a source
goes silent and the dashboard keeps smiling. Zero, no-data, and query-failure
are different truths that dashboards routinely conflate.

## Decision

`unknown`, `stale`, `partial`, `estimated`, and `not_configured` are
first-class states. They are never converted to zero or healthy. "No data",
"zero", and "query failed" are rendered as three distinct states everywhere.
Stale data is shown as last-good with its timestamp. Every data card can
disclose provenance: source, as-of, freshness, scope, measurement method,
and confidence. The web foundation ships these as reusable primitives so
future screens inherit correct behavior by default.

## Consequences

- UI reviews check state coverage (loading/error/empty/no-data/zero/stale/
  partial/estimated/unknown/not-configured/permission-denied).
- Aggregations must propagate quality flags rather than average them away.
- "Unknown shown as healthy" incidents are treated as correctness bugs of
  the highest class.
