# Runbook — Agent Certificate Rotation

Agent certificates live 14 days and self-rotate; this runbook covers the
automatic path, forcing a rotation, and revocation.

## Automatic rotation (normal case)

Renewal is two-phase and crash/retry safe; no operator action is needed:

1. **Prepare** — at a jittered ~2/3 of lifetime the agent generates a
   FRESH P-256 key and sends a CSR with a stable `renewal_id`, signed
   with its CURRENT key. The server signs into a pending slot (public
   material only, bounded expiry); the current key keeps working.
2. **Save** — the agent writes the new bundle as a versioned directory
   (0600) that nothing references yet.
3. **Activate** — the agent proves possession of the NEW key by signing
   `/internal/v1/agent/certificates/activate`; the server promotes the
   pending key atomically. Only now does the old key stop working.
4. **Promote** — the agent atomically points its identity at the new
   bundle and swaps its transport.

A lost response at ANY step recovers, and the two ambiguity cases are
worth knowing apart:

- **The activation never committed** (server unreachable): the OLD key
  keeps working; the agent retries the SAME pending renewal with bounded
  backoff and never deletes the pending material.
- **The activation committed but the response was lost**: the server has
  already switched to the new key and refuses the old one — the RUNNING
  agent detects this through the same idempotent activation retry and
  promotes the pending bundle in process (same renewal id, no new CSR,
  transport swapped automatically). No restart is needed or expected.

No new renewal ever starts while a pending one exists; an explicit
server refusal discards the pending without ever assuming the new key.
An agent restarted mid-renewal reconciles the pending at startup through
the same activation call. The cluster detail screen shows certificate
expiry; an "expires soon" badge appears under 5 days — if you see it,
renewal has been failing long enough to matter.

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
