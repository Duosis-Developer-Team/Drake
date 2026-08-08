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
trap 'rm -f "$RENDERED" "${CF_RENDERED:-}"' EXIT
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

# --- The Cloudflare Tunnel edge -------------------------------------------
#
# Same chart, different edge. Nothing about the Tunnel mode may open an
# inbound route, and the connector's routing table must send /v1 straight
# to the API.
echo "[policy] cloudflare tunnel edge"
CF=(-f values-cloudflare.test.yaml --namespace drake-prod)
CF_RENDERED="$(mktemp)"
helm template drake . "${CF[@]}" > "$CF_RENDERED"

echo "[policy] tunnel fail-closed renders"
for override in \
  "cloudflared.tunnelSecretName=" \
  "cloudflared.image.digest=" \
  "cloudflared.replicas=1" \
  "ingress.enabled=true" \
  "edge.mode=bogus" \
  "publicOrigin=http://drake.duosis.com"
do
  if helm template drake . "${CF[@]}" --set "$override" >/dev/null 2>&1; then
    echo "  chart rendered with '$override' but should have refused" >&2
    exit 1
  fi
done

python3 - "$CF_RENDERED" <<'PY'
import sys, yaml

docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d]
kinds: dict[str, list] = {}
for d in docs:
    kinds.setdefault(d["kind"], []).append(d)

def die(message: str) -> None:
    print(f"  {message}", file=sys.stderr)
    raise SystemExit(1)

if "Ingress" in kinds:
    die("tunnel mode must not create an Ingress")
for svc in kinds.get("Service", []):
    if svc["spec"]["type"] != "ClusterIP":
        die(f"{svc['metadata']['name']} must stay ClusterIP")
    if any("nodePort" in p for p in svc["spec"]["ports"]):
        die(f"{svc['metadata']['name']} must not allocate a NodePort")

for d in docs:
    if d["metadata"].get("namespace") != "drake-prod":
        die(f"{d['kind']}/{d['metadata']['name']} is not namespaced to drake-prod")

dep = next(d for d in kinds["Deployment"] if d["metadata"]["name"] == "drake-cloudflared")
pod = dep["spec"]["template"]["spec"]
if dep["spec"]["replicas"] != 2:
    die("exactly two connectors are expected")
if pod.get("automountServiceAccountToken") is not False:
    die("the connector must not receive a Kubernetes API token")
if kinds.get("Role") or kinds.get("RoleBinding"):
    die("the connector needs no RBAC; none should be created")
if kinds.get("Secret"):
    die("the chart must never create a Secret")

container = pod["containers"][0]
if "@sha256:" not in container["image"] or ":latest" in container["image"]:
    die("the connector image must be digest-pinned and never :latest")

# The routing table the connector actually loads.
cm = next(c for c in kinds["ConfigMap"] if c["metadata"]["name"] == "drake-cloudflared")
rules = yaml.safe_load(cm["data"]["config.yaml"])["ingress"]
api_rule = rules[0]
if not api_rule.get("path") or api_rule["service"] != "http://drake-api:8000":
    die("/v1 must be the first rule and must reach the API Service directly")
if "drake-web" in api_rule["service"]:
    die("/v1 must not pass through the web app: it breaks query cancellation")
if rules[-1] != {"service": "http_status:404"}:
    die("the last rule must refuse everything unmatched")

text = yaml.safe_dump_all(docs)
for marker in ("BEGIN PRIVATE KEY", "TunnelSecret", "AccountTag", "ghs_"):
    if marker in text:
        die(f"rendered manifests must not contain {marker}")

print(f"[policy] cloudflare tunnel edge OK ({len(docs)} documents checked)")
PY

# --- The connector's own opinion of the routing table ----------------------
# Validated with the pinned cloudflared binary, so the rules are checked by
# the program that will run them rather than by our reading of the docs.
CF_IMAGE="docker.io/cloudflare/cloudflared@sha256:59bab8d3aceec09bf6bdb07d6beca0225ca5cd7ab79436a87ea97978fe1dc4f9"
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  echo "[policy] cloudflared ingress rules"
  CFDIR="$(mktemp -d)"
  trap 'rm -f "$RENDERED" "$CF_RENDERED"; rm -rf "$CFDIR"' EXIT
  python3 -c "
import sys, yaml
for d in yaml.safe_load_all(open('$CF_RENDERED')):
    if d and d['kind'] == 'ConfigMap' and d['metadata']['name'] == 'drake-cloudflared':
        open('$CFDIR/config.yaml', 'w').write(d['data']['config.yaml'])
"
  docker run --rm -v "$CFDIR:/cfg:ro" "$CF_IMAGE" \
    --config /cfg/config.yaml tunnel ingress validate >/dev/null

  check_rule() {
    expected="$2"
    actual="$(docker run --rm -v "$CFDIR:/cfg:ro" "$CF_IMAGE" \
      --config /cfg/config.yaml tunnel ingress rule "$1" 2>/dev/null \
      | grep -oE 'http://[a-z-]+:[0-9]+|http_status:404' | tail -1)"
    if [ "$actual" != "$expected" ]; then
      echo "  $1 resolved to '$actual', expected '$expected'" >&2
      exit 1
    fi
  }
  check_rule "https://drake.duosis.com/v1/me"            "http://drake-api:8000"
  check_rule "https://drake.duosis.com/v1/projects?x=1"  "http://drake-api:8000"
  check_rule "https://drake.duosis.com/"                 "http://drake-web:3000"
  check_rule "https://drake.duosis.com/integrations"     "http://drake-web:3000"
  check_rule "https://wrong.duosis.com/"                 "http_status:404"
  echo "[policy] cloudflared ingress rules OK"
else
  echo "[policy] cloudflared ingress rules SKIPPED (docker unavailable)"
fi
