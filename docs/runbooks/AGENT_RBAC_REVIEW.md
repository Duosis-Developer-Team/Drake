# Runbook — Agent RBAC Review

How to review (and re-prove) the agent's Kubernetes permission surface.
The RBAC file IS the blast radius; treat every diff to it as a security
review.

## The contract

- Verbs: exactly `get`, `list`, `watch`. Nothing else, ever.
- Resources: the explicit allowlist in
  `deploy/agent/templates/rbac.yaml` — core inventory, apps, batch,
  storage, autoscaling, policy, discovery.k8s.io, and the optional
  monitoring.coreos.com CRDs.
- Forbidden forever: `secrets`, `configmaps`, `pods/exec`, `pods/attach`,
  `pods/portforward`, `tokenreviews`, `subjectaccessreviews`, `leases`,
  wildcards, and every write/impersonate/bind/escalate verb.

## Review procedure (any RBAC-touching PR)

1. Read the rendered diff, not just the template:
   `helm template deploy/agent … | grep -A40 ClusterRole`.
2. Run the policy gate locally: `bash deploy/agent/validate.sh` — it fails
   on any verb beyond get/list/watch, any forbidden or wildcard resource,
   and any rule without explicit resources. CI runs the same script.
3. Re-prove on a disposable cluster:
   `bash scripts/k3d_agent_acceptance.sh` — a `kubectl auth can-i` matrix
   (21 allowlisted yes / 19 forbidden no) against the actual rendered
   RBAC. The output table is the review artifact; paste it into the PR.
4. If a new resource is proposed: it must be justified in an ADR update,
   added to BOTH the agent-side allowlist and the ingest schema, and
   covered by new matrix rows (yes-rows for it, no-rows proving no
   write verbs came along).

## Red flags that end a review immediately

- Any `*` anywhere in the rules.
- `secrets` or `configmaps` in any form, including "just metadata".
- A subresource (`pods/exec`, `pods/attach`, `pods/portforward`).
- Any verb outside get/list/watch, including `create` on review-ish
  resources (`tokenreviews`, `subjectaccessreviews`).
- Widening RBAC to work around an ingest refusal — the ingest schema is
  the contract, not an obstacle.
