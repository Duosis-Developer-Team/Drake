#!/usr/bin/env bash
# Render + policy-check the Drake cluster agent chart. CI-safe: no cluster
# access, no install. Exit non-zero on any policy violation. This script is
# the executable form of the agent's security contract:
#   - RBAC verbs are EXACTLY get/list/watch
#   - no secrets / configmaps / exec / attach / portforward / tokenreviews /
#     subjectaccessreviews / wildcard resources
#   - no write verb of any kind, no impersonate/bind/escalate
#   - hardened pod: non-root, read-only rootfs, all caps dropped, seccomp
#     RuntimeDefault, no privilege escalation, no host namespaces/hostPath
#   - single replica; resources limited; existingSecret refs only
set -euo pipefail

cd "$(dirname "$0")"

echo "[helm] lint"
helm lint . \
  --set clusterId=00000000-0000-0000-0000-000000000000 \
  --set clusterName=policy-check \
  --set apiBaseUrl=https://drake-internal.example.test \
  --set image.digest=sha256:0000000000000000000000000000000000000000000000000000000000000000 \
  --set serverCA.existingSecret=drake-agent-server-ca \
  --set enrollmentToken.existingSecret=drake-agent-enrollment >/dev/null

echo "[helm] template"
RENDERED="$(mktemp)"
trap 'rm -f "$RENDERED"' EXIT
helm template policy-check . \
  --namespace drake-system \
  --set clusterId=00000000-0000-0000-0000-000000000000 \
  --set clusterName=policy-check \
  --set apiBaseUrl=https://drake-internal.example.test \
  --set image.digest=sha256:0000000000000000000000000000000000000000000000000000000000000000 \
  --set serverCA.existingSecret=drake-agent-server-ca \
  --set enrollmentToken.existingSecret=drake-agent-enrollment > "$RENDERED"

python3 - "$RENDERED" <<'PYEOF'
import sys

import yaml

rendered = open(sys.argv[1]).read()
documents = [doc for doc in yaml.safe_load_all(rendered) if doc]

failures: list[str] = []


def fail(message: str) -> None:
    failures.append(message)


FORBIDDEN_VERBS = {
    "create", "update", "patch", "delete", "deletecollection",
    "impersonate", "bind", "escalate", "*",
}
FORBIDDEN_RESOURCES = {
    "secrets", "configmaps", "pods/exec", "pods/attach", "pods/portforward",
    "tokenreviews", "subjectaccessreviews", "*",
}
ALLOWED_VERBS = {"get", "list", "watch"}

roles = [doc for doc in documents if doc.get("kind") in ("ClusterRole", "Role")]
if not roles:
    fail("no ClusterRole rendered")
for role in roles:
    for rule in role.get("rules", []):
        verbs = set(rule.get("verbs", []))
        resources = set(rule.get("resources", []))
        groups = set(rule.get("apiGroups", ["<missing>"]))
        if not verbs <= ALLOWED_VERBS:
            fail(f"RBAC verbs beyond get/list/watch: {sorted(verbs - ALLOWED_VERBS)}")
        for item in resources | groups:
            if "*" in str(item):
                fail(f"wildcard in RBAC rule: {item}")
        overlap = resources & FORBIDDEN_RESOURCES
        if overlap:
            fail(f"forbidden resources in RBAC: {sorted(overlap)}")
        if not resources:
            fail("RBAC rule with no explicit resources")

deployments = [doc for doc in documents if doc.get("kind") == "Deployment"]
if len(deployments) != 1:
    fail(f"expected exactly one Deployment, found {len(deployments)}")
for deployment in deployments:
    spec = deployment["spec"]
    if spec.get("replicas") != 1:
        fail("agent must be single-replica")
    pod = spec["template"]["spec"]
    pod_security = pod.get("securityContext", {})
    if pod_security.get("runAsNonRoot") is not True:
        fail("pod must run as non-root")
    if pod_security.get("seccompProfile", {}).get("type") != "RuntimeDefault":
        fail("pod must use seccomp RuntimeDefault")
    for flag in ("hostNetwork", "hostPID", "hostIPC"):
        if pod.get(flag):
            fail(f"host namespace enabled: {flag}")
    for volume in pod.get("volumes", []):
        if "hostPath" in volume:
            fail(f"hostPath volume: {volume.get('name')}")
    for container in pod.get("containers", []):
        security = container.get("securityContext", {})
        if security.get("allowPrivilegeEscalation") is not False:
            fail("allowPrivilegeEscalation must be false")
        if security.get("readOnlyRootFilesystem") is not True:
            fail("readOnlyRootFilesystem must be true")
        if security.get("privileged"):
            fail("privileged container")
        drops = set(security.get("capabilities", {}).get("drop", []))
        if "ALL" not in drops:
            fail("capabilities must drop ALL")
        resources = container.get("resources", {})
        if not resources.get("limits"):
            fail("resource limits are required")
        if "@sha256:" not in container.get("image", ""):
            fail("image must be digest-pinned")
        for env in container.get("env", []):
            if "value" in env and any(
                marker in str(env.get("value", "")).lower()
                for marker in ("begin private key", "token:", "password")
            ):
                fail(f"credential-shaped env value: {env['name']}")

services = [doc for doc in documents if doc.get("kind") == "Service"]
if services:
    fail("agent must have NO Service (no inbound surface)")
ingresses = [doc for doc in documents if doc.get("kind") == "Ingress"]
if ingresses:
    fail("agent must have NO Ingress")

secrets = [doc for doc in documents if doc.get("kind") == "Secret"]
if secrets:
    fail("chart must never render Secret material (existingSecret refs only)")

if "BEGIN PRIVATE KEY" in rendered or "BEGIN EC PRIVATE KEY" in rendered:
    fail("private key material in rendered output")

if failures:
    for failure in failures:
        print(f"POLICY VIOLATION: {failure}", file=sys.stderr)
    sys.exit(1)
print(f"[policy] agent chart OK ({len(documents)} documents checked)")
PYEOF
