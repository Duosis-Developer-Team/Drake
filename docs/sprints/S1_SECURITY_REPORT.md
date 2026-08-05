# Sprint 1 — Security Report

Public-safe summary of the identity, RBAC, and audit security posture.
Everything listed as enforced is backed by executed tests
(see S1_TEST_REPORT).

## 1. Authentication

- OIDC Authorization Code + PKCE (S256). Strict validation of issuer,
  audience, signature (JWKS with rotation + kid-miss refetch), `exp`, `nbf`,
  and `nonce`. Single-use `state` (atomic take), single-use authorization
  codes at the provider, login-state TTL as login timeout.
- No tokens in the browser — no localStorage/sessionStorage use (asserted in
  tests); the only artifact is an opaque HttpOnly SameSite=Lax cookie,
  Secure outside local/test.
- Sessions live server-side in Redis under hashed keys; logout deletes
  server-side; expiry enforced by TTL. A fresh session ID is minted on every
  login (fixation defense, proven by test).
- Fail-closed: session backend or provider unavailability yields typed 503
  responses — never an anonymous pass, never a fake sign-out.
- Open-redirect-safe post-login targets (strict relative-path allowlist).
- CSRF: per-session token header required on cookie-authenticated mutations,
  plus Origin allowlist verification.
- OIDC/provider errors are typed codes; raw tokens and provider bodies never
  surface in responses, logs, or audit (redaction layers from Sprint 0
  apply throughout).
- A plaintext/test issuer cannot boot outside local/test (startup guard);
  the deterministic fake provider lives under tests/ and is never part of
  the shipped package.

## 2. Authorization (dynamic RBAC)

- Deny by default: a newly authenticated identity holds zero permissions.
- Authority is computed server-side from atomic permissions via grants —
  never from role names, never from client-supplied context.
- Scope inheritance is strictly parent → child along
  organization → cluster → project → environment → service → tenant;
  narrow grants never widen to parents or siblings (negative-tested).
- Tenant visibility is its own permission; project access never implies it.
- Group claims grant nothing without an explicit mapping AND grants on it;
  group overage fails closed to zero group-derived authority and is
  surfaced in the UI.
- Time-windowed, revocable grants; expired/future/revoked grants are inert;
  clock comparisons are UTC.
- Delegation safety: self-grants refused; only permission-subsets at
  equal-or-narrower scopes can be delegated; role edits cannot add
  permissions the editor lacks; system templates are immutable; the last
  organization-root identity grant carrying `rbac.manage` cannot be revoked
  (permission-based invariant, not name-based).
- Denial semantics resist enumeration: outside-scope resources are
  consistent 404s with identical bodies for existing and non-existing IDs.

## 3. Audit

- RBAC mutations and their audit rows commit in one transaction; a failed
  audit write rolls the mutation back (proven by fault-injection test).
- Recorded: login success/failure, logout, role create/update/archive,
  role-permission changes, grant create/revoke, group-mapping changes,
  authorization denials, self-escalation and delegation-violation attempts.
- Payload hygiene: no tokens, cookies, session IDs, authorization codes,
  PKCE verifiers, secrets, or raw provider responses; client IP is stored
  only as a short hash; the credential-shape guard from Sprint 0 rejects
  unsafe metadata at write time.
- Table remains database-enforced append-only (trigger; negative-tested).
- Query surface: `audit.view` + scope filtering (subtree visibility;
  unscoped platform events only with organization-root visibility), cursor
  pagination only, bounded page sizes — no offset scans, no unbounded
  export.

## 4. Web

- The browser talks only to the Drake API through a same-origin rewrite;
  the provider-access guard still forbids telemetry/Kubernetes references
  and absolute URLs in client code.
- UI permission gating is a convenience; every decision is re-made by the
  API (negative-tested at the API layer).
- No fake identities, roles, or audit rows anywhere in the product UI;
  test fixtures stay in test code.

## 5. Known security work ahead

- Real Entra ID connection (tenant, app registration, group object IDs) is
  an operator-gated manual step; until then the fake provider covers the
  contract. Conditional Access/MFA behavior can only be validated then.
- Session inactivity timeout and device/session listing are candidates for
  a later sprint (absolute TTL exists today).
- Rate limiting on auth endpoints is deferred to the hardening sprint.
- One source-repository security review item remains tracked privately and
  gates the corresponding integration sprint (unchanged from Sprint 0).
