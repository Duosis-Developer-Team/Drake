"""GitHub App integration boundary (ADR-0019, ADR-0020).

Owns GitHub App identity, the webhook trust boundary, repository
onboarding, and read-only CI/CD policy evaluation. It never writes
another module's tables: catalog entities are referenced only through
scope ids, and audit goes through the audit service.

Credential material (private key PEM, app JWT, installation token,
webhook secret) lives in process memory or the operator's secret store
ONLY. It is never a column value, a log line, an exception message, an
audit field, or an API response.
"""
