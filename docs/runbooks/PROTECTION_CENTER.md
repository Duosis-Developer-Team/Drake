# Protection: backups, and whether they can actually be restored

The rule the whole domain is built on:

```
backup job success  ≠  artifact exists
artifact exists     ≠  artifact is valid
valid artifact      ≠  offsite protection
offsite backup      ≠  verified recoverability
```

Each of those is a separate question with separate evidence, so Drake
reports **two axes** rather than one green tick.

## The chain

```
BackupPolicy → BackupRun → BackupArtifact → IntegrityCheck
             → ReplicationCopy (offsite) → RestoreDrill → evaluation
```

Every link has its own table, its own external identity and its own
timestamps. A missing link is visible as a missing link.

## The two axes

| `backup_state` | Means |
| --- | --- |
| `protected` | Fresh success, an observed artifact, and every requirement the policy states (integrity, offsite) met |
| `at_risk` | Backup is fresh, but the artifact, integrity or offsite evidence is missing or failing |
| `overdue` | The newest success is older than the RPO — or there has never been one |
| `failed` | The most recent attempt failed |
| `unknown` | The reporter is silent or stale, so nothing can be trusted |

| `recoverability_state` | Means |
| --- | --- |
| `verified` | A passing restore drill, inside its validity window and inside the RTO |
| `unverified` | Never drilled, drill expired, or it restored slower than the RTO |
| `failed` | The most recent drill failed |
| `unknown` | Reporter stale |

Combined into `overall_state`: `recoverable_verified`,
`protected_unverified`, `at_risk`, `overdue`, `failed`, `unknown`.

Reason codes: `backup_overdue`, `latest_run_failed`, `artifact_missing`,
`integrity_missing`, `integrity_failed`, `offsite_missing`,
`restore_never_verified`, `restore_verification_expired`, `restore_failed`,
`rto_exceeded`, `reporter_stale`.

All computation is UTC. A viewer's timezone changes how a timestamp is
displayed, never what it means.

## Connector ingest

Reporters authenticate as a **connector**, not as a user. The connector key
is registered in settings against exactly one project, so an event cannot
name a project it was not registered for.

```
POST /v1/protection/ingest/events
POST /v1/protection/ingest/snapshots     # kind: begin | page | complete

X-Drake-Protection-Connector: <key>
X-Drake-Protection-Timestamp: <unix seconds>
X-Drake-Protection-Signature: v1=HMAC_SHA256(secret, timestamp + "." + raw_body)
```

Event types (versioned CloudEvents-shaped envelope):

```
drake.backup.run.started.v1        drake.backup.integrity.completed.v1
drake.backup.run.completed.v1      drake.backup.copy.observed.v1
drake.backup.artifact.observed.v1  drake.restore.drill.completed.v1
```

Guarantees:

- **Idempotent** on the connector's own event id (primary key).
- **Out-of-order safe**: an event older than what is already recorded is
  `ignored_stale` and never drags a projection backwards.
- **Provider time ≠ Drake time**: `source_event_at` and `ingested_at` are
  separate columns, so a late delivery is visible as a late delivery.
- **Replay-bounded**: the timestamp is inside the signed material and
  checked against the clock.
- **Typed or refused**: unknown event types, unknown policies and untyped
  fields are rejected with a bounded code, never partially applied.
- **Reconciliation** uses `begin → page → complete`; an incomplete snapshot
  changes nothing, and it shares event ids with the live stream so it fills
  gaps rather than duplicating evidence.

A reporter cannot create a policy. Policies come from the reviewed contract
(`packages/contracts/protection/connectors.v1.json`), because a policy
carries the promises an assessment is made against.

## What is never stored

Backup content, dump content, storage credentials, signed download URLs,
SAS tokens, OneDrive tokens, bucket credentials, raw provider responses,
file paths, filenames, and any business data seen during a restore. There
are no columns for them. Sites and artifacts are opaque keys; restore
drills keep only typed pass/fail checks
(`schema_present`, `row_counts_sane`, `migrations_applied`,
`application_smoke`).

**An artifact that stops being reported becomes `missing`, never deleted.**
A reporter outage is not proof a backup is gone, and retention expiring is
not proof a file was removed.

## Incidents

Protection problems raise incidents through the existing Sprint 6
lifecycle — not a parallel one — and notify through the Sprint 7 planner.
`backup_overdue`, `latest_run_failed`, `integrity_failed`,
`offsite_missing`, `restore_failed` and `restore_verification_expired`
raise one; `restore_never_verified` does not, because a policy nobody has
drilled yet is a backlog item, not a page.

One incident per policy per active problem: while it persists each
evaluation updates the existing incident, and when it clears the incident
resolves through the same `auto_resolved` path a health recovery uses.

## API and permissions

```
GET /v1/protection/summary
GET /v1/protection/filters
GET /v1/protection/policies
GET /v1/protection/policies/{id}
GET /v1/protection/policies/{id}/runs
GET /v1/protection/policies/{id}/drills
GET /v1/protection/policies/{id}/incidents
GET /v1/protection/runs/{id}
GET /v1/protection/artifacts/{id}
GET /v1/protection/restore-drills/{id}
```

Visibility requires **both** `protection.view` and project access
(`environment.view`); the visible set is their intersection. Scope
filtering happens in SQL before any count, so the summary and totals never
hint at a policy the list will not show. Anything outside scope is 404.

**Read-only.** There is no endpoint to start a backup, download or delete
an artifact, trigger a restore, enter a credential, edit a storage URL, or
apply retention.

## Metrics

```
drake_backup_last_success_timestamp_seconds
drake_backup_last_attempt_timestamp_seconds
drake_backup_last_duration_seconds
drake_backup_last_artifact_size_bytes
drake_backup_consecutive_failures
drake_restore_last_success_timestamp_seconds
```

Labels are `project`, `environment`, `store`, `policy` — controlled
identities only. Never an artifact id, filename, checksum or run id: those
are unbounded cardinality and a slow leak of the thing being measured. A
missing value emits **no sample** rather than `0`, because a zero epoch
renders as 1970 and reads as "backed up 55 years ago".

## Connector projections

**Hermes** (`hermes-backup`) — `dev` and `test`; core and auth PostgreSQL
are **separate stores**, so evidence for one never stands in for the other.
Weekly schedule, weekly RPO, offsite and integrity required. OneDrive
appears only as provider/site metadata. With no restore drill recorded,
the honest state is `protected_unverified`.

**LogiSlot** (`logislot-backup`) — one PostgreSQL store, daily RPO, 14-day
retention, offsite and integrity required, weekly restore smoke. Restore
result is independent of backup result: a failed drill keeps
recoverability `failed` even when the newest backup succeeded. The
shared-table tenancy is not presented as if the artifact separated
tenants, and no tenant or facility data enters a protection payload.

## Troubleshooting

**A store shows `at_risk` with a green backup job.** That is the point: the
job succeeded but the artifact, integrity check or offsite copy has not
been observed. The reason code says which.

**Everything went `unknown`.** The connector has not reported inside its
`stale_after_seconds`. Check the reporter, not the backups.

**`protected_unverified` will not become verified.** No passing restore
drill inside its validity window, or the drill exceeded the RTO. Both are
in the reason list.

**A policy is missing entirely.** Evidence is refused for policies Drake
does not know about. Add it to the connector contract and re-seed.

**Retention passed but the artifact still shows.** Retention expiring is
not proof of deletion. The artifact becomes `expired` or `missing` when the
reporter says so.
