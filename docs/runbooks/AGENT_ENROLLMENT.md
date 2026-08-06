# Runbook — Cluster Agent Enrollment

Enroll a Drake cluster agent against the control plane. The token is
one-time and short-lived; the agent's private key is generated inside the
agent and never leaves it.

## Preconditions

- The cluster exists in the Drake catalog (you need its cluster id).
- You hold `integration.manage` at that cluster's scope.
- The internal agent endpoint (dedicated TLS listener) is reachable from
  the cluster, and you have its CA bundle to pin.

## Steps

1. **Mint a token** (shown exactly once, valid 10 minutes):
   `POST /v1/clusters/{cluster_id}/agent-enrollment-tokens` with your CSRF
   token and an `Idempotency-Key`. Copy the `token` field now — the server
   stores only a hash and cannot show it again.
2. **Create the out-of-band Secrets** in the target namespace:
   - server CA bundle secret (key `ca.pem`),
   - enrollment token secret (key `token`).
   The Helm chart only references existing Secrets; it never contains
   material.
3. **Install the agent** with `deploy/agent` values: `clusterId`,
   `clusterName`, `apiBaseUrl`, `image.digest`, and the two Secret names.
   The chart is policy-checked in CI; do not hand-edit RBAC.
4. **Verify**: the agent logs `enrollment complete` then
   `snapshot streamed`; the cluster detail screen shows agent `connected`
   and inventory `fresh`. The audit log records
   `agent.enrollment.consume`.

## Failure modes

- `enrollment refused` is deliberately generic (unknown/expired/used/
  wrong-cluster are indistinguishable). Mint a fresh token and retry; do
  not attempt to distinguish causes from the response.
- A token older than 10 minutes is dead; mint a new one.
- Two agents racing one token: exactly one wins; the loser needs its own
  token.

## Never

- Never reuse a token, share it over chat, or store it in the repo.
- Never grant the agent RBAC beyond the chart's rendered rules.
- Never install on a shared/production cluster outside the sanctioned
  deployment process.
