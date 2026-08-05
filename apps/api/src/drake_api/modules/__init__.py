"""Domain module boundaries for the Drake modular monolith.

Planned modules (populated in later sprints — deliberately empty in Sprint 0):

- ``identity``        OIDC subjects, sessions, roles, permissions, grants
- ``catalog``         projects, services, environments, clusters
- ``integrations``    GitHub, Prometheus, Alertmanager, agents, adapters
- ``inventory``       normalized Kubernetes resources and change events
- ``telemetry``       metric registry, query templates, query broker
- ``tenant_metering`` tenants, plans, entitlements, usage snapshots
- ``protection``      backup policies, artifacts, restore drills
- ``incidents``       alert projection, incident state and timeline
- ``deployments``     commit/workflow/image/rollout correlation
- ``audit``           append-only security/administrative audit (foundation
                      lives in ``drake_api.audit``)

Boundary rule: a module never writes to another module's tables. Cross-module
reads go through an application service or an explicit read model.
"""
