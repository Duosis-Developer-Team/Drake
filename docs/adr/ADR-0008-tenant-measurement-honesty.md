# ADR-0008: Tenant measurement honesty

**Status:** Accepted

## Context

In shared-table multi-tenant databases, physical bytes cannot be exactly
attributed to tenant rows. Presenting estimates as exact measurements would
corrupt commercial and capacity decisions.

## Decision

Every tenant storage value carries an explicit measurement method
(`physical_database_exact`, `physical_schema_exact`, `logical_rollup_exact`,
`logical_sampled_estimate`, `row_count_only`, `unavailable`), a confidence
class, and an as-of timestamp. Values measured by different methods are never
summed silently. The gap between physical database totals and tenant logical
rollups is shown as a separate shared/unattributed overhead bucket. Tenant
business facts (plan, entitlements, usage) come only from authoritative
application snapshots — never inferred, never fabricated.

## Consequences

- UI always shows method/confidence alongside tenant storage numbers.
- The snapshot contract (`packages/contracts`) makes method/confidence
  mandatory at the schema level.
- "We don't know" is a legitimate, visible answer (see ADR-0011).
