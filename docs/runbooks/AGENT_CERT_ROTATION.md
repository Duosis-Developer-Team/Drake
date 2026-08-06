# Runbook — Agent Certificate Rotation

Agent certificates live 14 days and self-rotate; this runbook covers the
automatic path, forcing a rotation, and revocation.

## Automatic rotation (normal case)

The agent renews at a jittered ~2/3 of certificate lifetime: it generates
a FRESH P-256 key, sends a CSR over its current verified identity to
`/internal/v1/agent/certificates/renew`, persists the new material
atomically (0600), and swaps its transport. No operator action. The
cluster detail screen shows the certificate expiry; an "expires soon"
badge appears under 5 days — if you see it, renewal has been failing long
enough to matter.

## If renewal keeps failing

1. Check agent logs for `certificate renewal failed; retrying`.
2. Verify the internal listener is reachable from the cluster and its CA
   bundle secret still matches the listener's certificate.
3. If the certificate expires entirely, the agent's identity is dead —
   expired identities cannot authenticate anything, including renewal
   (fail-closed by design). Re-enroll via AGENT_ENROLLMENT with a fresh
   one-time token.

## Forced rotation / suspected key exposure

1. Set the agent row's lifecycle to `revoked` (operator SQL/admin path) —
   every request from that identity is refused immediately, including
   renewal.
2. Delete the agent pod AND its state volume so no key material remains.
3. Re-enroll with a fresh token; the new agent gets a new key, id, and
   certificate.

## Guarantees to rely on

- Private keys are generated in the agent and never leave it; the control
  plane stores public keys and certificate metadata only.
- Renewal is bound to the verified caller — an agent can never renew a
  certificate for another agent or cluster.
- The CA key is only ever referenced via external file configuration;
  rotating the CA is a deploy-time operation followed by fleet
  re-enrollment.
