# Connecting a customer's cluster (tracesis-agent)

ADR-0037. A customer installs one chart. Their logs, traces and metrics stay
in their cluster, and Tracesis reads them through the agent's own outbound
connection.

Who this is for: the operator of a Tracesis deployment, turning the Connect
flow on, and anyone debugging a connected cluster.

## What has to be true before the Connect button works

The Clusters page asks `GET /v1/agent-install` first. Until all of these
hold, it explains why and creates nothing.

1. **The chart is published.** The `publish images` workflow pushes
   `oci://ghcr.io/duosis-developer-team/charts/tracesis-agent:<version>`
   with that run's agent and collector digests stamped in. A version that
   already exists is not rewritten: bump `deploy/tracesis-agent/Chart.yaml`
   to ship new images.
2. **Customers can pull it.** GHCR packages are private by default. Make
   these public, or give the customer a pull secret and have them set
   `clusterAgent.imagePullSecrets` and `telemetry.imagePullSecrets`:
   - `drake-cluster-agent`
   - `tracesis-collector`
   - `charts/tracesis-agent`
3. **The agent listener is reachable from outside.** Set the following in
   the deployment's values. It adds a NodePort Service (30844 enrol, 30843
   ingest) and opens those two ports in the NetworkPolicy:

   ```yaml
   internalAgentApi:
     external:
       enabled: true
       host: <the edge host name>
       serverCASecret: drake-agent-server-ca   # key ca.crt, certificate only
       chartVersion: <published chart version>
   ```

4. **The listener's server certificate names that host.** Re-sign it with
   the internal server CA, adding `DNS:<host>` to the existing SANs. In-cluster
   agents keep working, because the old names stay. Back up the current
   `drake-agent-tls` Secret first. The CA key never goes into the cluster.

## What a connected cluster looks like

- **Tracesis side:** `clusters.telemetry_location = 'cluster'`, and a
  Prometheus integration with `config_ref = agent:<cluster_ref>`.
- **Customer side:** namespace `tracesis`, holding `drake-cluster-agent`
  (inventory and relay), `drake-otel-agent` (one per node),
  `drake-otel-gateway`, `drake-otel-clickhouse-0`, `tracesis-prometheus`,
  kube-state-metrics and node-exporter.
- **Reads:** a log, trace or metric screen for that cluster puts the read
  on `relay:q:<cluster id>` in Redis. The agent picks it up within its long
  poll and the answer returns. If the agent has sent no heartbeat for 90
  seconds, the screen reports `store_agent_offline` immediately.

## When something is wrong

| Symptom | Look at |
|---|---|
| Install fails with `enrollment.token is required` | The file is older than ten minutes or was already used. Make a new one: Connect again with the same name and ID. |
| Agent logs `relay poll failed` | Reachability of the ingest NodePort from the customer cluster; whether the listener certificate includes the host. |
| Screens say `store_agent_offline` | `kubectl -n tracesis logs deploy/drake-cluster-agent`; whether heartbeats arrive (`cluster_agents.last_heartbeat_at`). |
| Screens say `store_auth_refused` | The `tracesis-clickhouse` Secret was replaced by hand. The store and the agent must read the same `reader-password`. |
| Metrics empty, logs fine | Whether `tracesis-prometheus` is running, and whether its series carry `cluster_ref=<cluster>`. |
