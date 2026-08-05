# ADR-0012: No early microservices

**Status:** Accepted

## Context

Premature service decomposition multiplies operational surface (deploys,
versioning, network failure modes, observability) before domain boundaries
are proven by real usage.

## Decision

Drake does not split the API into services in its initial phases. The
modular monolith (ADR-0002) with enforced module boundaries is the unit of
evolution. Extraction of a module into a service requires a dedicated ADR
demonstrating a concrete scaling or isolation need, plus contract tests at
the new boundary. The four deployables (web, api, worker, cluster-agent)
remain the only process-level split.

## Consequences

- Team velocity concentrates on product correctness, not distributed
  plumbing.
- Module boundaries stay honest because they are the future extraction seams.
- Any "let's split it" proposal has a defined, evidence-based path.
