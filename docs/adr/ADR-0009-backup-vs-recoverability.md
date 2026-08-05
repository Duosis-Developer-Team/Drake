# ADR-0009: Backup evidence ≠ recoverability

**Status:** Accepted

## Context

The existence of a backup artifact proves only that a job produced a file.
Organizations regularly discover at restore time that their "successful"
backups were unusable.

## Decision

Drake models the full chain as distinct facts: backup policy → run → artifact
→ replication/offsite copy → integrity check → restore drill. Artifact
success is never presented as restore success. Status semantics separate
`protected` (fresh, integrity-checked, offsite satisfied) from
`recoverable_verified` (a restore drill actually passed within the RTO
window) and `protected_unverified` (backup exists, restore evidence missing
or stale).

## Consequences

- The Protection view always shows restore-drill age/result next to backup
  freshness.
- Reporters push metadata-only evidence; backup content and storage
  credentials never reach Drake.
- Overdue restore drills degrade protection status even when backups succeed.
