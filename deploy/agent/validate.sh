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

COMMON_SETS=(
  --set clusterId=00000000-0000-0000-0000-000000000000
  --set clusterName=policy-check
  --set apiBaseUrl=https://drake-internal.example.test
  --set image.digest=sha256:0000000000000000000000000000000000000000000000000000000000000000
  --set serverCA.existingSecret=drake-agent-server-ca
  --set enrollmentToken.existingSecret=drake-agent-enrollment
  --set networkPolicy.apiEndpointCIDR=203.0.113.10/32
  --set networkPolicy.kubernetesApiCIDR=198.51.100.0/24
)

echo "[helm] lint"
helm lint . "${COMMON_SETS[@]}" >/dev/null

echo "[policy] fail-closed renders"
# Missing digest, missing netpol CIDRs, and broad CIDRs must ALL refuse.
if helm template policy-check . --namespace drake-system \
  "${COMMON_SETS[@]}" --set image.digest="" >/dev/null 2>&1; then
  echo "POLICY VIOLATION: render succeeded without a digest-pinned image" >&2
  exit 1
fi
if helm template policy-check . --namespace drake-system \
  "${COMMON_SETS[@]}" --set networkPolicy.apiEndpointCIDR="" >/dev/null 2>&1; then
  echo "POLICY VIOLATION: render succeeded without an explicit API endpoint CIDR" >&2
  exit 1
fi
if helm template policy-check . --namespace drake-system \
  "${COMMON_SETS[@]}" --set networkPolicy.kubernetesApiCIDR="" >/dev/null 2>&1; then
  echo "POLICY VIOLATION: render succeeded without an explicit Kubernetes API CIDR" >&2
  exit 1
fi

echo "[helm] template"
RENDERED="$(mktemp)"
trap 'rm -f "$RENDERED"' EXIT
helm template policy-check . --namespace drake-system "${COMMON_SETS[@]}" > "$RENDERED"

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

policies = [doc for doc in documents if doc.get("kind") == "NetworkPolicy"]
if len(policies) != 1:
    fail("expected exactly one NetworkPolicy")
for policy in policies:
    spec = policy["spec"]
    if spec.get("ingress") != []:
        fail("NetworkPolicy must deny ALL ingress")
    if set(spec.get("policyTypes", [])) != {"Ingress", "Egress"}:
        fail("NetworkPolicy must cover both Ingress and Egress")
    rendered_policy = str(policy)
    for broad in ("0.0.0.0/0", "::/0"):
        if broad in rendered_policy:
            fail(f"NetworkPolicy contains the whole internet: {broad}")
    for rule in spec.get("egress", []):
        for target in rule.get("to", []):
            block = target.get("ipBlock")
            if block is not None and not block.get("cidr"):
                fail("egress ipBlock without an explicit CIDR")

for deployment in deployments:
    for container in deployment["spec"]["template"]["spec"].get("containers", []):
        probe = container.get("livenessProbe", {}).get("exec", {}).get("command", [])
        if probe[:2] != ["/usr/local/bin/drake-agent", "healthcheck"]:
            fail(
                "liveness must use the agent binary's healthcheck subcommand "
                f"(the Dockerfile contract), got {probe}"
            )
        if not container.get("livenessProbe", {}).get("timeoutSeconds"):
            fail("liveness probe needs a bounded timeout")

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
