# ADR-0005: Read-only cluster agent

**Status:** Accepted

## Context

An in-cluster agent holds the most sensitive position in the system. Any
write capability, secret access, or interactive verb it holds becomes the
blast radius of a compromise.

## Decision

The cluster agent v1 is strictly read-only: the only Kubernetes verbs it may
ever hold are `get`, `list`, and `watch`, over an explicitly enumerated
resource list. It never reads Secrets or ConfigMap data, never receives
`exec`/`attach`/`portforward`, never holds write verbs, and never uses
wildcard RBAC. Connectivity is outbound-only to the Drake API; the agent's
sole listener is a loopback liveness probe. The collector registry rejects
any collector declaring a forbidden resource kind at registration time.

## Consequences

- Compromising the agent yields observation of non-secret cluster state at
  worst — no lateral movement primitives.
- Remediation features (restart/scale/etc.) are excluded from v1 by design
  and would require a new ADR with its own security model.
- RBAC manifests can be reviewed against a closed, testable contract.
