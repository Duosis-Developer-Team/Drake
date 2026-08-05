# ADR-0002: FastAPI modular monolith

**Status:** Accepted

## Context

A small team builds and operates Drake. Distributed-system overhead
(per-service deployment, tracing, versioning, failure modes) is not justified
by current scale.

## Decision

The control plane API is a single FastAPI application composed of strictly
bounded domain modules (identity, catalog, integrations, inventory, telemetry,
tenant metering, protection, incidents, deployments, audit). Modules never
write to each other's tables; cross-module access goes through application
services. Web, API, worker, and cluster-agent remain separate deployables.

## Consequences

- One deployment, one migration chain, simple local development.
- Module boundaries are enforced by review and tests now, making a future
  extraction possible without a rewrite (see ADR-0012).
