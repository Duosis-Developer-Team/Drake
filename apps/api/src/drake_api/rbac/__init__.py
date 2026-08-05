"""Dynamic RBAC: identities, permission catalog, roles, scoped grants.

Authorization principles (enforced in the service layer, tested negatively):

- Deny by default: a new authenticated identity has zero permissions.
- Authority is computed from atomic permissions via grants — never from a
  role NAME, never from client-supplied data.
- Grants apply at a scope and inherit only parent → child. Narrow grants
  never widen to parent or sibling scopes.
- Group claims grant nothing by themselves; only explicit group mappings
  with grants do. Group overage fails closed (no group-derived authority).
- Time windows are UTC; expired, future, or revoked grants never apply.
"""
