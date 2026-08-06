# Runbook — Agent Disconnect

The cluster screen shows agent `disconnected` and/or inventory `stale`.

## What the states mean (they are separate axes)

- `disconnected`: no heartbeat inside the staleness window (default 90s;
  heartbeats are expected every 30s). Says nothing about workloads.
- `stale` inventory: no snapshot/event activity inside the inventory
  window (default 15m). The data shown is the last good projection,
  clearly labeled — never silently refreshed, never hidden.
- Workload health chips keep their last observed values under staleness;
  they answer "what did we last see", not "what is true right now".

## Triage

1. **Is the cluster itself alive?** A dead cluster takes the agent with
   it. Check your cluster-level monitoring first; Drake's inventory is not
   a substitute for cluster alerting.
2. **Is the agent pod running?** `kubectl -n <ns> get pods` — the agent is
   a single replica by design; a crash loop shows here. Inspect logs.
3. **Egress path**: the agent dials out only. Verify the NetworkPolicy /
   firewall still allows the internal endpoint, and DNS resolves.
4. **Identity**: an expired or revoked certificate fails closed —
   `agent authentication failed` in the control-plane logs, renewal
   refused. Follow AGENT_CERT_ROTATION (re-enroll if expired).
5. **Control-plane side**: is the internal listener up and serving TLS
   with the CA the agent pins?

## Recovery behavior (automatic)

On reconnect the agent never resumes blindly: it runs a bounded full
reconcile (fresh snapshot), the projection swaps atomically, and freshness
returns to `fresh`. Missed changes during the outage are captured by the
snapshot; nothing is silently lost, and the outage window remains visible
as the `last full reconcile` timestamp.

## Escalate

Escalate to the platform team if: the agent process is healthy AND egress
is confirmed AND the control plane refuses a valid identity — that
combination indicates a trust-boundary problem, not a connectivity one.
