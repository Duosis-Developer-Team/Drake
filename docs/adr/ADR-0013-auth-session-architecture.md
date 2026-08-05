# ADR-0013: Authentication and session architecture

**Status:** Accepted

## Context

Sprint 1 introduces sign-in (OIDC Authorization Code + PKCE against the
corporate identity provider) and dynamic RBAC. Several implementation choices
needed fixing beyond the product dossier's decisions.

## Decisions

1. **Server-side sessions, opaque cookie.** Tokens never reach the browser.
   The only client artifact is an HttpOnly, SameSite=Lax (Secure outside
   local/test) cookie holding a random session ID; Redis stores session data
   under a SHA-256 of that ID. Redis is a session backend, never a business
   store; if it is unreachable, authentication fails closed with a typed 503.
2. **Auth routes live under `/v1/auth/*` plus `/v1/me`.** They are part of
   the one public API surface (one origin, one error envelope, one
   correlation model) rather than a separate auth host.
3. **CSRF defense is layered.** Cookie-authenticated mutations require the
   per-session CSRF token in a header (unreadable cross-site), and when the
   browser presents an Origin it must match the allowlisted web origins.
4. **The permission catalog is exactly the product's atomic set.** No
   additional authentication/session permissions were needed: `/v1/me` is
   self-service for any authenticated identity, and everything else maps to
   existing keys (`rbac.manage`, `audit.view`, …).
5. **Role objects are global; role mutations require `rbac.manage` at the
   organization root.** Scoped delegation happens through grants. A
   project-scoped RBAC admin manages grants in their subtree but cannot mint
   or reshape global roles.
6. **System role templates are immutable.** They are starting presets;
   tailoring happens by creating new roles. This also keeps the permission-
   based last-owner invariant simple and testable.
7. **Anti-escalation is structural.** Self-grants are refused; a grantor can
   only delegate roles whose permissions are a subset of their own effective
   permissions at the target scope; editing a role can only ADD permissions
   the editor already holds at the organization scope.
8. **Outside-scope means 404.** Resource-level denials are indistinguishable
   from nonexistence (anti-enumeration); capability-level denials are 403;
   unauthenticated is 401; stale `If-Match` is 412 and a missing precondition
   header is 428.

## Consequences

- A deterministic fake OIDC provider (test-only, plaintext issuers rejected
  outside local/test at startup) exercises the full flow in CI and E2E; the
  real Entra ID connection remains an operator-gated manual step.
- Session revocation is immediate (server-side delete); there are no
  refresh-token semantics to reason about in the browser.
- Group-claim overage yields zero group-derived authority until resolved —
  visibly surfaced in the UI, never silently widened.
