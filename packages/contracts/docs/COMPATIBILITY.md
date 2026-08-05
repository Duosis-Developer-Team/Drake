# Contract versioning and compatibility

## Current generations

| Contract | Version | Status |
|---|---|---|
| Project manifest (`ProjectObservability`) | `drake.duosis.com/v1alpha1` | Experimental (Sprint 0–3) |
| Tenant snapshot | `1.0` | Experimental |
| Backup/restore events | `v1` (CloudEvents payload) | Experimental |

## Rules

1. **Unknown fields are errors.** All schemas set `additionalProperties: false`.
   A field the schema does not know is a validation failure, not a warning.
2. **Breaking changes require a new apiVersion.** `v1alpha1` may change without
   notice while experimental. From `v1beta1` on, removing/renaming a field or
   narrowing an enum requires a new apiVersion plus a conversion path.
3. **Promotion gates.** `v1alpha1 → v1beta1` requires at least two real
   projects using the manifest and a round-trip migration test.
   `v1beta1 → v1` requires a documented backward-compatibility policy and
   conversion tests.
4. **No credential values.** Manifests and events may carry secret *reference
   names* (e.g. `connectionSecretRef`) but never credential values, private
   keys, tokens, connection strings, or raw SQL. The validator enforces this
   with content policy rules in addition to schema validation.
5. **Fixtures are the contract's test surface.** Every schema change must keep
   `fixtures/valid/*` passing and `fixtures/invalid/*` failing, extending both
   sets when behavior changes.
