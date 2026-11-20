# ADR-0035: Rules that filter, and rules against a signal's normal

**Status:** Accepted
**Extends:** ADR-0031 (alert rules), ADR-0034 (log and trace signals)

## Context

ADR-0034 counts log lines by severity and watches "any endpoint" of a service.
Two kinds of question stayed out of reach:

- **A specific text or endpoint.** "Tell me when a line says `payment failed`"
  and "tell me when `POST /checkout` gets slow".
- **Unusual, not over a number.** "Tell me when errors are unusual for this
  time of day." Here a fixed threshold is either too noisy at peak hours or too
  quiet at night.

## Decision

### Filters: one text or one endpoint per rule

A rule may carry one of two filters. Both are stored on the rule, in nullable
columns added by migration 0029.

- `log_contains` applies to log signals. It counts only lines that contain the
  text, matched case-insensitively. A new signal, `matching_logs`, counts such
  lines whatever their severity, and it requires the text.
- `route` applies to trace signals. The rule watches only that endpoint: its
  slow requests, or its own p95. For a named endpoint, one request is enough to
  give a p95.

A filtered value belongs to the rule, not to the service. It is therefore never
written to `service_health_samples`; storing every rule's value per binding per
cycle would grow without bound.

- **Live.** Each runner page adds at most two statements: one on logs and one on
  traces, carrying every text and endpoint of the page's filtered rules.
  - Each distinct text is one `countIf` column; each endpoint is one
    `has(routes, SpanName)` group.
  - Texts and endpoints travel as typed parameters, never as SQL.
  - At most 20 of each per page. The rule's state row keeps its last value as
    before.
- **History and preview.** Both read the store on demand: the last day in
  five-minute buckets.
  - Three bindings go in one statement, so 3 × 288 rows stay under the reader's
    1000-row limit.
  - At most 24 bindings are read.
  - The result is cached for two minutes, so moving the preview slider does not
    query again.
  - Live judgement uses a sliding five-minute window, while the preview uses
    fixed five-minute buckets. The two are close, not identical, and the screen
    calls it a preview.

### Rules against normal

The two new operators, `above_normal` and `below_normal`, judge a stored signal
against its own past:

- **Normal.** The signal's values within ±30 minutes of the current time of day,
  on each of the previous 7 days, all taken from samples already stored (8 days
  are kept).
- **The band.** The median, ± sensitivity × spread.
  - The spread is the scaled median absolute deviation, which is robust to the
    outliers that define an incident.
  - The spread never falls below a floor per signal: one line or request for
    counts, one percentage point for ratios, 50 ms or 10% for latency. A signal
    that never moved, such as zero errors all week, is therefore not called
    anomalous at the first single line.
- **Sensitivity.** `threshold` holds the sensitivity, from 1 to 10. The default
  is 3.
- **Too little history.** With fewer than 30 past values there is no normal.
  The rule then has no data, which is neither a breach nor a recovery.
- **Same machinery.** The band becomes the threshold of that evaluation, so the
  same `step` judges the rule and `for_seconds` works as for any rule. The edge
  in force is recorded with the fired and resolved events.
- **Preview and history.** These compute each point's band in the same way. The
  chart draws the moving edge instead of a flat line.
- **No filters.** An anomaly rule cannot filter, because a filtered value has no
  stored history to be normal against.

## Consequences

- No new query for metrics. Filtered rules add up to two small statements per
  runner page, and only when such rules exist.
- An anomaly rule adds one indexed read of about 200 sample values per binding
  per cycle.
- Additive only. Migration 0029 adds two nullable columns and widens two
  checks. No existing rule changes meaning.
- Matching is a substring match, not a regular expression. Neither a regular
  expression nor matching on log attributes is supported yet.
