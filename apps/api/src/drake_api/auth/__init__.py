"""Authentication: OIDC (Authorization Code + PKCE) and server-side sessions.

Security invariants:

- Tokens never reach the browser; the only client artifact is an opaque,
  HttpOnly session cookie.
- Sessions live server-side in Redis under a hashed key; the raw session ID
  is never stored or logged.
- If the session backend is unavailable, authentication fails closed.
- A plaintext/test OIDC issuer cannot be configured outside local/test.
"""
