# Cluster agent operations

The agent reads a Kubernetes cluster and reports what it sees. It writes
nothing to that cluster, and it holds one thing worth protecting: its own
identity — a private key and the certificate Drake's Agent CA issued for it.

Everything below is about that identity: how it is created, how it survives,
and how to remove it on purpose rather than by accident.

Nothing here should be run casually. Each command changes what a real
cluster reports to a real control plane.

## The shape

```
drake-prod                        drake-agent
  drake-agent-gateway               drake-cluster-agent
    enroll  :8144  CERT_NONE    ←──── one call, once, with a token
    ingest  :8143  CERT_REQUIRED ←─── everything else, with a certificate
```

Both listeners are ClusterIP. Nothing outside the cluster can reach either
one, and there is no value in the chart that would publish them.

The agent's identity lives on a PersistentVolumeClaim mounted at
`/var/lib/drake-agent`:

```
agent-key.pem   the private key, 0600, generated in the pod
agent.pem       the certificate the Agent CA issued
agent-id        the agent's Drake id
```

## First enrolment

1. Register the cluster, if it is not registered yet. Either
   `POST /v1/clusters` with `integration.manage`, or inside the API image:

   ```
   python -m drake_api.catalog.register_cluster_cli \
       --cluster-ref duosis-prod-1 \
       --display-name "Duosis Production" \
       --actor <operator identity uuid>
   ```

   Repeating this is safe; it returns the cluster that already exists.

2. Issue a one-time enrolment token for that cluster
   (`POST /v1/clusters/{id}/agent-enrollment-tokens`). It is short-lived and
   single-use.

   **The token must not reach stdout, a log, a shell history or a file that
   outlives the step.** Write it directly into the Secret the agent reads.

3. Install the agent chart with that Secret referenced, the cluster id, the
   agent image digest, the ingest endpoint as `apiBaseUrl` and the enroll
   endpoint as `enrollmentBaseUrl`.

4. Watch for the identity to appear. The agent logs `enrollment complete`
   with its agent id — not its key.

5. **After the first snapshot completes, delete the enrolment token
   Secret.** It is already spent, but a consumed token in a Secret invites
   somebody to try reusing it, and its absence proves the agent no longer
   needs one.

   Confirm the identity is on the claim *before* deleting it:

   ```
   kubectl -n drake-agent exec deploy/drake-cluster-agent -- \
       ls -l /var/lib/drake-agent
   ```

   Three files, owner-only. Never print their contents.

## Ordinary pod restart

Nothing to do. The pod comes back, reads its identity from the claim,
reconnects with the same certificate, and the inventory returns to fresh. No
token is needed and none should be created.

If a restarted agent asks for a token, the claim is not mounted — check
`persistence.enabled` and that the claim is `Bound` before doing anything
else. Issuing a token to work around a missing volume creates a second
identity for the same cluster.

## Reinstalling the chart with the claim in place

`helm uninstall` leaves the claim (`helm.sh/resource-policy: keep`).
Reinstalling with the same release name and namespace picks the same claim
back up, and the agent reconnects as itself. This is the supported way to
change chart values, image digests or resource limits.

## Losing the claim

If the claim or its volume is gone, the identity is gone. There is no
recovery — the private key existed only there, which is the point.

Then, and only then:

1. Retire the old agent record in Drake so a dead identity cannot act.
2. Register nothing new: the cluster row stays; it is the agent that is
   being replaced.
3. Issue a fresh enrolment token and let the agent enrol again.
4. Confirm afterwards that the cluster has exactly one active agent.

## Certificate renewal

The agent renews on its own, in two phases: it asks for a new certificate
against its current key, then activates it by proving possession of the
pending key. Both calls go to the ingest listener and both require the
current certificate, so a lapsed agent cannot renew — it has to be
re-enrolled. Nothing to run by hand.

## Taking an agent out of service deliberately

1. `helm uninstall` the agent release. Reporting stops; the cluster's
   inventory goes stale rather than empty, which is the honest state — Drake
   no longer knows, rather than knowing there is nothing.
2. Retire the agent record in Drake, so its certificate cannot be used.
3. Leave the claim if the agent may come back. Delete it only when the
   decision is that this agent is finished:

   ```
   kubectl -n drake-agent delete pvc drake-cluster-agent-state
   ```

   This is irreversible and forces a fresh enrolment. It is a separate,
   deliberate step precisely so an uninstall cannot do it by accident.

## What never appears anywhere

The private key, the certificate's private half, and any enrolment token.
Not in logs, not in test output, not in an artifact, not in a report, not in
this repository. Commands here print file listings and identifiers; if a
procedure seems to need a key's contents, the procedure is wrong.
