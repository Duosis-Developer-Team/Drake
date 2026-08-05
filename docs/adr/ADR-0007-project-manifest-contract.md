# ADR-0007: Versioned project manifest contract

**Status:** Accepted

## Context

Project onboarding needs a reviewable, versionable statement of observability
intent: environments, services, tenancy model, data stores, backup policies,
SLOs.

## Decision

Each onboarded repository carries a `.drake/project.yaml` manifest
(`kind: ProjectObservability`), validated by a JSON Schema plus content
policy. Unknown fields are errors. Manifests never contain credential values,
connection strings, private keys, or raw SQL — secret *references* by name
are the only allowed pointer. Breaking changes require a new `apiVersion`
(`v1alpha1` → `v1beta1` → `v1` with conversion tests). The repository is the
intent authority; Drake stores accepted revisions and runtime projections,
and UI-driven changes materialize as pull requests, not silent DB edits.

## Consequences

- Onboarding is reviewable in version control like any other change.
- The validator (`packages/contracts`) is a shared, CI-enforced gate.
- Runtime drift is displayed as drift; it never overwrites intent.
