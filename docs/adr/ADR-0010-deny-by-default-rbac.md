# ADR-0010: Deny-by-default scoped RBAC

**Status:** Accepted

## Context

Drake aggregates operationally and commercially sensitive data across
projects and tenants. Authorization mistakes here are cross-project and
cross-tenant leaks.

## Decision

Authorization is deny-by-default with atomic permissions granted at explicit
scopes along the hierarchy organization → cluster/site → project →
environment → service → tenant. Grants never widen implicitly; scope
inheritance is explicit and tested. Enforcement happens server-side in the
service layer — hiding UI elements is never a security boundary, and tenant
filtering is never delegated to the frontend. Sign-in uses the corporate
OIDC identity provider; identities map to Drake role grants rather than
being trusted blindly from group claims.

## Consequences

- Every protected endpoint ships with negative (IDOR) tests.
- Tenant-scoped data requires tenant-scoped permission even for users who
  can read the surrounding project.
- Audit records authorization outcomes with correlation IDs (fail-closed
  wiring for critical mutations).
