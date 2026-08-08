#!/usr/bin/env bash
# Render + policy-check the Drake control plane chart. CI-safe: no cluster
# access, no install, no secrets. This script is the executable form of the
# production edge contract (ADR-0021):
#
#   - one public origin: / -> web, /v1 -> API
#   - /v1 reaches the API UNCHANGED (no rewrite, no regex, no snippets)
#   - TLS required, exact host, explicit ingress class
#   - public Services stay ClusterIP
#   - images are digest-pinned; `latest` is never deployable
#   - default-deny NetworkPolicy survives, ingress controller admitted
#     narrowly
#   - secrets are referenced, never inlined
set -euo pipefail

cd "$(dirname "$0")"
PROD=(-f values-production.test.yaml)

fail() { echo "POLICY VIOLATION: $*" >&2; exit 1; }

# A production render that SHOULD refuse. Passing means the chart would
# have installed something the contract forbids.
refuses() {
  local why="$1"; shift
  if helm template drake . "${PROD[@]}" "$@" >/dev/null 2>&1; then
    fail "render succeeded $why"
  fi
}

echo "[helm] lint"
helm lint . "${PROD[@]}" >/dev/null

echo "[policy] fail-closed production renders"
refuses "with ingress disabled"            --set ingress.enabled=false
refuses "without an ingress class"         --set ingress.className=""
refuses "without an ingress host"          --set ingress.host=""
refuses "with TLS disabled"                --set ingress.tls.enabled=false
refuses "without a TLS secret reference"   --set ingress.tls.secretName=""
refuses "with a wildcard host"             --set ingress.host="*.example.test" --set publicOrigin="https://*.example.test"
refuses "with a placeholder host"          --set ingress.host="REPLACE_ME" --set publicOrigin="https://REPLACE_ME"
refuses "with a plaintext public origin"   --set publicOrigin="http://drake.example.test"
refuses "when origin and ingress host disagree" --set publicOrigin="https://other.example.test"
refuses "without a digest-pinned api image"     --set api.image.digest=""
refuses "without a digest-pinned web image"     --set web.image.digest=""
refuses "with a mutable api tag alongside a digest" --set api.image.tag="latest"
refuses "without an application secret reference"   --set api.existingSecret=""
refuses "with the network policy disabled"          --set networkPolicy.enabled=false
refuses "without an ingress-controller selector"    --set networkPolicy.ingressControllerNamespaceSelector=null
refuses "without a database CIDR"                   --set networkPolicy.databaseCIDR=""
refuses "without a redis CIDR"                      --set networkPolicy.redisCIDR=""
refuses "with an all-addresses egress CIDR"         --set networkPolicy.databaseCIDR="0.0.0.0/0"
refuses "with a rewrite-target annotation" \
  --set 'ingress.annotations.nginx\.ingress\.kubernetes\.io/rewrite-target=/'
refuses "with a configuration-snippet annotation" \
  --set 'ingress.annotations.nginx\.ingress\.kubernetes\.io/configuration-snippet=return 200;'
refuses "with the github integration enabled but no secret reference" \
  --set github.enabled=true

echo "[helm] template"
RENDERED="$(mktemp)"
trap 'rm -f "$RENDERED"' EXIT
helm template drake . "${PROD[@]}" --namespace drake-system > "$RENDERED"

echo "[policy] edge contract"
python3 - "$RENDERED" <<'PY'
import sys, yaml

docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d]
kinds = {}
for doc in docs:
    kinds.setdefault(doc["kind"], []).append(doc)

def die(message):
    print(f"POLICY VIOLATION: {message}", file=sys.stderr)
    sys.exit(1)

# --- Ingress -------------------------------------------------------------
ingresses = kinds.get("Ingress", [])
if len(ingresses) != 1:
    die(f"expected exactly one Ingress, found {len(ingresses)}")
ingress = ingresses[0]

if ingress["apiVersion"] != "networking.k8s.io/v1":
    die("Ingress must use networking.k8s.io/v1")
if not ingress["spec"].get("ingressClassName"):
    die("Ingress must name an explicit ingressClassName")

tls = ingress["spec"].get("tls") or []
if not tls or not tls[0].get("secretName"):
    die("Ingress must terminate TLS with a secret reference")

rules = ingress["spec"]["rules"]
if len(rules) != 1:
    die("exactly one public host is allowed")
host = rules[0]["host"]
if not host or "*" in host:
    die("Ingress host must be an exact hostname")
if host not in tls[0]["hosts"]:
    die("the TLS certificate must cover the routed host")

paths = {p["path"]: p for p in rules[0]["http"]["paths"]}
if set(paths) != {"/v1", "/"}:
    die(f"expected exactly the /v1 and / prefixes, found {sorted(paths)}")
for path, spec in paths.items():
    if spec["pathType"] != "Prefix":
        die(f"path {path} must use pathType Prefix (longest-prefix wins picks /v1 over /)")
    if any(ch in path for ch in "()[]*$^"):
        die(f"path {path} looks like a regex; the contract is plain prefixes")

if paths["/v1"]["backend"]["service"]["name"] != "drake-api":
    die("/v1 must route to the API Service")
if paths["/"]["backend"]["service"]["name"] != "drake-web":
    die("/ must route to the web Service")

annotations = ingress["metadata"].get("annotations") or {}
for key in annotations:
    lowered = key.lower()
    if "rewrite" in lowered or "snippet" in lowered:
        die(f"annotation {key} would modify the path; the API owns /v1 unchanged")

# --- Services ------------------------------------------------------------
for service in kinds.get("Service", []):
    kind = service["spec"].get("type", "ClusterIP")
    if kind != "ClusterIP":
        die(f"Service {service['metadata']['name']} is {kind}; public entry is the Ingress only")

# --- Images --------------------------------------------------------------
def containers(doc):
    spec = doc["spec"]["template"]["spec"]
    return spec.get("containers", []) + spec.get("initContainers", [])

for doc in kinds.get("Deployment", []) + kinds.get("Job", []):
    for container in containers(doc):
        image = container["image"]
        if "@sha256:" not in image:
            die(f"image {image} is not digest-pinned")
        if ":latest" in image:
            die(f"image {image} uses a mutable tag")

# --- Secrets -------------------------------------------------------------
if "Secret" in kinds:
    die("the chart must not create Secrets; they are provisioned out of band")
for doc in kinds.get("Deployment", []) + kinds.get("Job", []):
    for container in containers(doc):
        for entry in container.get("env", []) or []:
            if "valueFrom" not in entry and any(
                token in entry["name"].upper()
                for token in ("SECRET", "PASSWORD", "TOKEN", "PRIVATE_KEY")
            ):
                die(f"env {entry['name']} carries an inline value; use a Secret reference")

# --- NetworkPolicy -------------------------------------------------------
policies = {p["metadata"]["name"]: p for p in kinds.get("NetworkPolicy", [])}
deny = policies.get("drake-default-deny")
if deny is None or deny["spec"]["podSelector"] != {}:
    die("the default-deny policy must select every pod in the namespace")
if set(deny["spec"]["policyTypes"]) != {"Ingress", "Egress"}:
    die("default-deny must cover both directions")

admitted = policies.get("drake-ingress-controller")
if admitted is None:
    die("the ingress controller must be admitted explicitly")
rule = admitted["spec"]["ingress"][0]
if not rule["from"][0].get("namespaceSelector", {}).get("matchLabels"):
    die("ingress-controller admission must be scoped by namespace selector")
if not rule.get("ports"):
    die("ingress-controller admission must be limited to the application ports")

egress = policies.get("drake-api-egress")
if egress is None:
    die("API egress must be constrained")
for entry in egress["spec"]["egress"]:
    for target in entry.get("to", []):
        cidr = target.get("ipBlock", {}).get("cidr")
        if cidr in ("0.0.0.0/0", "::/0"):
            die("API egress must not be open to all addresses")

# --- Migration -----------------------------------------------------------
jobs = [j for j in kinds.get("Job", []) if j["metadata"]["name"].endswith("-migrate")]
if len(jobs) != 1:
    die(f"expected exactly one migration Job, found {len(jobs)}")
job = jobs[0]
hooks = job["metadata"]["annotations"]["helm.sh/hook"]
if "pre-install" not in hooks or "pre-upgrade" not in hooks:
    die("the migration must run before application pods start")
command = containers(job)[0]["command"]
if command != ["alembic", "upgrade", "head"]:
    die(f"the migration must be exactly `alembic upgrade head`, found {command}")
if "downgrade" in " ".join(command):
    die("automatic downgrade is never part of a release")
if job["spec"].get("backoffLimit") != 0:
    die("a failed migration must stop the release, not retry blindly")

print(f"[policy] drake chart OK ({len(docs)} documents checked)")
PY

# --- The drake-prod release ------------------------------------------------
#
# Same chart, first deployment: no public route yet. The application must be
# provable on its ClusterIP Services before a hostname, a certificate and an
# identity provider are attached to it.
echo "[policy] drake-prod production values"

# The committed values deliberately leave image digests and datastore CIDRs
# blank. Rendering them as-is must FAIL rather than produce a release that
# would install with placeholders.
if helm template drake . -f values-drake-prod.yaml --namespace drake-prod >/dev/null 2>&1; then
  fail "values-drake-prod.yaml rendered with unfilled operator inputs"
fi

FILL=(--set "publicOrigin=https://drake.example.test"
      --set "api.image.digest=sha256:1111111111111111111111111111111111111111111111111111111111111111"
      --set "web.image.digest=sha256:2222222222222222222222222222222222222222222222222222222222222222"
      --set "migration.image.digest=sha256:1111111111111111111111111111111111111111111111111111111111111111"
      --set "networkPolicy.databaseCIDR=10.0.10.5/32"
      --set "networkPolicy.redisCIDR=10.0.10.6/32")

for override in \
  "edge.mode=bogus" \
  "ingress.enabled=true" \
  "publicOrigin=" \
  "api.image.digest=" \
  "api.existingSecret="
do
  if helm template drake . -f values-drake-prod.yaml --namespace drake-prod \
      "${FILL[@]}" --set "$override" >/dev/null 2>&1; then
    fail "drake-prod rendered with '$override' but should have refused"
  fi
done

PROD_RENDERED="$(mktemp)"
helm template drake . -f values-drake-prod.yaml --namespace drake-prod "${FILL[@]}" > "$PROD_RENDERED"

python3 - "$PROD_RENDERED" <<'PY'
import sys, yaml

docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d]
kinds: dict[str, list] = {}
for d in docs:
    kinds.setdefault(d["kind"], []).append(d)

def die(message: str) -> None:
    print(f"  {message}", file=sys.stderr)
    raise SystemExit(1)

if "Ingress" in kinds:
    die("the first deployment must publish no public route")
if "Secret" in kinds:
    die("the chart must never create a Secret")
for svc in kinds.get("Service", []):
    if svc["spec"]["type"] != "ClusterIP":
        die(f"{svc['metadata']['name']} must stay ClusterIP")
    if any("nodePort" in p for p in svc["spec"]["ports"]):
        die(f"{svc['metadata']['name']} must not allocate a NodePort")

for d in docs:
    if d["metadata"].get("namespace") != "drake-prod":
        die(f"{d['kind']}/{d['metadata']['name']} is not namespaced to drake-prod")

names = {d["metadata"]["name"] for d in docs}
for expected in ("drake-api", "drake-web", "drake-migrate"):
    if expected not in names:
        die(f"expected workload {expected} is missing")

for doc in kinds.get("Deployment", []) + kinds.get("Job", []):
    pod = doc["spec"]["template"]["spec"]
    if pod.get("imagePullSecrets") != [{"name": "drake-ghcr"}]:
        die(f"{doc['metadata']['name']} cannot pull private images")
    for container in pod["containers"]:
        image = container["image"]
        if ":latest" in image or "@sha256:" not in image:
            die(f"{doc['metadata']['name']} image must be digest-pinned and never :latest")

text = yaml.safe_dump_all(docs).lower()
for marker in ("cloudflare", "cloudflared", "tunnel"):
    if marker in text:
        die(f"the standard deployment must contain no {marker} resource")
for marker in ("begin private key", "ghs_"):
    if marker in text:
        die(f"rendered manifests must not contain {marker}")

print(f"[policy] drake-prod release OK ({len(docs)} documents checked)")
PY
rm -f "$PROD_RENDERED"
