# ADR-0026 — Running the agent listener in production, and where an agent's identity lives

Extends ADR-0016, which defined agent enrollment, the certificate lifecycle
and the mTLS trust boundary. That ADR described the protocol. This one is
about running it: what listens, where, and what survives a restart.

Two things had to be decided before a real cluster could connect, and
neither was obvious from the code.

## The bootstrap asymmetry

An agent enrolling for the first time has no client certificate. Everything
it does afterwards must present one. A single mutual-TLS listener cannot
serve both, because `CERT_REQUIRED` refuses the first connection an agent
ever makes.

The tempting answer is `CERT_OPTIONAL` at the transport plus a check in the
application: allow `/enroll` without a certificate, demand one everywhere
else. **That cannot be written honestly on this stack.** uvicorn 0.52.1
builds the ASGI scope from `type, asgi, http_version, server, client,
scheme, method, root_path, path, raw_path, query_string, headers, state` —
there is no transport, no `ssl_object`, and no TLS extension. The string
`getpeercert` does not appear anywhere in the package. An application-layer
client-certificate check would have to reach into uvicorn's protocol
internals, and a security control that depends on a private attribute of a
dependency is a control that disappears in a patch release without anyone
noticing.

**Decision: two listeners, one image, one pod.**

| Listener | Port | TLS | Serves |
| --- | --- | --- | --- |
| `enroll` | 8144 | server-authenticated, `CERT_NONE` | `POST /internal/v1/agent/enroll`, and nothing else |
| `ingest` | 8143 | `CERT_REQUIRED` against the Agent CA | heartbeat, inventory, certificate renew and activate |

The guarantee now lives where it cannot be forgotten. A caller with no
client certificate does not get a 403 from the ingest listener — it never
gets a request at all, because the handshake fails first. And the enrolment
listener carries no other route, so a stolen one-time token cannot be spent
somewhere a certificate would normally be required: those paths answer 404
there, because they genuinely do not exist on that app.

The router was split to make this true structurally rather than by
configuration: `enrollment_router` and `certificate_router` are separate
objects, and the app factory takes a `surface` that decides which ones are
mounted. A combined `router` remains for local, test and CI, where the
transport is not what is under test.

**Two containers in one pod, not two Deployments.** They share the same
image, the same Agent CA mount and the same lifecycle; splitting them into
separate workloads would double the places that key is mounted and give
nothing back. The cost is that a restart takes both surfaces down together,
which for a listener nothing outside the cluster can reach is acceptable.

**The listener is a separate workload from the public API.** It holds the
CA private key that signs agent identities, and the public API answers
browsers. Those do not belong in one process. The chart mounts
`DRAKE_AGENT_CA_KEY_FILE` on the gateway and nowhere else — not on the API,
not on the web app, not on the edge — and a contract test asserts exactly
that.

## Where an agent's identity lives

The agent chart backed `/var/lib/drake-agent` with a memory emptyDir. That
directory holds `agent-key.pem`, `agent.pem` and `agent-id`, so every pod
restart destroyed the identity and the next start demanded a **new one-time
enrolment token**. An ordinary node drain became an operator task, and an
operator task whose job is to mint a credential is one that gets automated
badly — usually by leaving a long-lived token in a Secret.

The other obvious option is for the agent to write its identity into a
Kubernetes Secret. That would require giving a deliberately read-only agent
**write access to Secrets**, which is the single permission this whole
design exists to not have.

**Decision: a PersistentVolumeClaim.** ReadWriteOnce, one replica, mounted
only by the agent. The key is node-scoped, which is what a node-scoped
identity should be. The claim carries `helm.sh/resource-policy: keep`, so
`helm uninstall` followed by reinstall reconnects the same agent instead of
silently minting a second one.

What this costs: the claim outlives the release, so removing an agent for
good is two steps rather than one, and the runbook says so. Deleting the
claim is the deliberate way to force re-enrolment — which is exactly why it
is not something an uninstall does by accident.

## What the NetworkPolicy does and does not prove

The gateway admits connections only from the `drake-agent` namespace
(`kubernetes.io/metadata.name`, a label the API server sets rather than the
manifest author) and only from pods carrying the agent's name label, on
exactly the two listener ports. Egress reaches PostgreSQL, Redis and the
cluster resolver, and nothing else — in particular no Kubernetes API, since
the agent reads the cluster and reports; the gateway never reads back.

That is a **network** boundary. Vanilla Kubernetes NetworkPolicy cannot
verify a ServiceAccount: it selects namespaces and pods by label, and a
label is an assertion by whoever created the pod. So the policy means
"nothing outside the agent namespace even gets to try". It does not
establish who is calling.

Identity is the other layer, and it is the one that authenticates: a client
certificate this deployment's own CA issued, carrying the agent's SPIFFE
URI, plus a per-request proof-of-possession signature over the method,
path, body hash, timestamp and a single-use nonce, checked against the
public key recorded for that agent, bound to that cluster. Claiming the
NetworkPolicy authenticates anything would be describing a guarantee Drake
does not have.

## Registering a cluster

A manifest's `clusterRef` names something that must already exist, and an
enrolment token is issued *for a cluster*. Until now the only code that
created a cluster row was `catalog.bootstrap` — a local/test fixture loader
that fails closed outside local and test and is exposed through no API. A
production Drake could not be told about a cluster by any supported means.

`POST /v1/clusters` closes that, under `integration.manage` — the same
authority that already governs enrolment tokens for a cluster, applied one
step earlier. It creates; it does not update, rename or delete, because a
cluster ref anchors workload bindings, inventory and certificate subjects,
and changing one later is a migration rather than an edit. A repeat with
the same name returns the existing cluster; a repeat with a different name
is a conflict.

There is also a management command, because the first cluster has to exist
before any UI can list one and the person doing a rollout holds cluster
credentials rather than necessarily a Drake session. It calls the same
function, with the same validation, idempotency and audit event, and takes
an explicit actor identity that must already exist. It is not a database
shell: it registers a cluster, and there is no statement argument to pass.
