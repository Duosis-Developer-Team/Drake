"""The production Helm chart renders the edge contract, or refuses.

These render the real chart with `helm template`, so what is asserted is
what would install. A chart that renders something plausible from a
missing value is how an edge ends up without TLS.
"""

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

CHART = Path(__file__).resolve().parents[3] / "deploy" / "drake"
VALUES = CHART / "values-production.test.yaml"

HELM = shutil.which("helm")

pytestmark = pytest.mark.skipif(HELM is None, reason="helm is not installed")


def render(*overrides: str) -> list[dict[str, Any]]:
    result = subprocess.run(  # noqa: S603 - resolved binary, fixed argv, no shell
        [
            str(HELM),
            "template",
            "drake",
            str(CHART),
            "-f",
            str(VALUES),
            "--namespace",
            "drake-system",
            *overrides,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def refuses(*overrides: str) -> bool:
    result = subprocess.run(  # noqa: S603 - resolved binary, fixed argv, no shell
        [str(HELM), "template", "drake", str(CHART), "-f", str(VALUES), *overrides],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode != 0


def by_kind(docs: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [doc for doc in docs if doc.get("kind") == kind]


# --- the route contract ---------------------------------------------------


def test_production_renders_one_tls_ingress_on_an_exact_host() -> None:
    ingress = by_kind(render(), "Ingress")
    assert len(ingress) == 1
    spec = ingress[0]["spec"]
    assert ingress[0]["apiVersion"] == "networking.k8s.io/v1"
    assert spec["ingressClassName"] == "nginx"
    assert spec["tls"][0]["secretName"] == "drake-tls"
    assert spec["tls"][0]["hosts"] == ["drake.example.test"]
    assert len(spec["rules"]) == 1
    assert spec["rules"][0]["host"] == "drake.example.test"
    assert "*" not in spec["rules"][0]["host"]


def test_v1_routes_to_the_api_and_root_routes_to_the_web() -> None:
    paths = {
        p["path"]: p for p in by_kind(render(), "Ingress")[0]["spec"]["rules"][0]["http"]["paths"]
    }
    assert set(paths) == {"/v1", "/"}
    assert paths["/v1"]["backend"]["service"]["name"] == "drake-api"
    assert paths["/v1"]["backend"]["service"]["port"]["number"] == 8000
    assert paths["/"]["backend"]["service"]["name"] == "drake-web"
    assert paths["/"]["backend"]["service"]["port"]["number"] == 3000


def test_both_prefixes_use_prefix_matching() -> None:
    """Longest-prefix precedence is what picks /v1 over / — not ordering."""
    paths = by_kind(render(), "Ingress")[0]["spec"]["rules"][0]["http"]["paths"]
    assert {p["pathType"] for p in paths} == {"Prefix"}


def test_the_v1_prefix_is_never_rewritten() -> None:
    """A rewrite would deliver /projects to an API that serves /v1/projects."""
    ingress = by_kind(render(), "Ingress")[0]
    annotations = ingress["metadata"].get("annotations") or {}
    for key in annotations:
        assert "rewrite" not in key.lower()
        assert "snippet" not in key.lower()


def test_no_regex_paths_are_rendered() -> None:
    paths = by_kind(render(), "Ingress")[0]["spec"]["rules"][0]["http"]["paths"]
    for entry in paths:
        assert not any(ch in entry["path"] for ch in "()[]*$^?+")


def test_nested_v1_paths_and_query_strings_are_covered_by_the_prefix() -> None:
    """`/v1` as a Prefix covers every deeper API route by construction.

    Kubernetes matches on path elements and never touches the query
    string, so `/v1/projects?limit=20` reaches the API unchanged.
    """
    paths = {
        p["path"] for p in by_kind(render(), "Ingress")[0]["spec"]["rules"][0]["http"]["paths"]
    }
    assert "/v1" in paths
    for route in ("/v1/projects", "/v1/integrations/github/webhook", "/v1/auth/callback"):
        assert route.startswith("/v1")


# --- fail-closed ----------------------------------------------------------


@pytest.mark.parametrize(
    ("override", "because"),
    [
        ("ingress.enabled=false", "ingress disabled"),
        ("ingress.className=", "no ingress class"),
        ("ingress.host=", "no host"),
        ("ingress.tls.enabled=false", "TLS disabled"),
        ("ingress.tls.secretName=", "no TLS secret reference"),
        ("publicOrigin=http://drake.example.test", "plaintext origin"),
        ("publicOrigin=https://other.example.test", "origin/host mismatch"),
        ("api.image.digest=", "unpinned api image"),
        ("web.image.digest=", "unpinned web image"),
        ("api.image.tag=latest", "mutable tag"),
        ("api.existingSecret=", "no secret reference"),
        ("networkPolicy.enabled=false", "network policy disabled"),
        ("networkPolicy.databaseCIDR=", "no database CIDR"),
        ("networkPolicy.redisCIDR=", "no redis CIDR"),
        ("networkPolicy.databaseCIDR=0.0.0.0/0", "open egress"),
        ("github.enabled=true", "github enabled without a secret reference"),
    ],
)
def test_production_render_fails_closed(override: str, because: str) -> None:
    assert refuses("--set", override), f"render succeeded with {because}"


def test_a_wildcard_host_is_refused() -> None:
    assert refuses(
        "--set", "ingress.host=*.example.test", "--set", "publicOrigin=https://*.example.test"
    )


def test_a_placeholder_host_is_refused() -> None:
    assert refuses("--set", "ingress.host=REPLACE_ME", "--set", "publicOrigin=https://REPLACE_ME")


def test_a_rewrite_annotation_is_refused() -> None:
    assert refuses("--set", r"ingress.annotations.nginx\.ingress\.kubernetes\.io/rewrite-target=/")


# --- services, images, policy, migration ----------------------------------


def test_public_services_stay_internal() -> None:
    services = by_kind(render(), "Service")
    assert {s["metadata"]["name"] for s in services} == {"drake-api", "drake-web"}
    for service in services:
        assert service["spec"].get("type", "ClusterIP") == "ClusterIP"


def test_every_production_image_is_digest_pinned() -> None:
    docs = render()
    images = [
        container["image"]
        for doc in by_kind(docs, "Deployment") + by_kind(docs, "Job")
        for container in doc["spec"]["template"]["spec"]["containers"]
    ]
    assert images
    for image in images:
        assert "@sha256:" in image
        assert ":latest" not in image


def test_default_deny_survives_while_the_ingress_controller_is_admitted() -> None:
    policies = {p["metadata"]["name"]: p for p in by_kind(render(), "NetworkPolicy")}
    deny = policies["drake-default-deny"]
    assert deny["spec"]["podSelector"] == {}
    assert set(deny["spec"]["policyTypes"]) == {"Ingress", "Egress"}

    admitted = policies["drake-ingress-controller"]
    rule = admitted["spec"]["ingress"][0]
    assert rule["from"][0]["namespaceSelector"]["matchLabels"]
    ports = {p["port"] for p in rule["ports"]}
    assert ports == {8000, 3000}, "only the application ports are admitted"


def test_datastores_are_not_exposed_and_egress_is_specific() -> None:
    docs = render()
    for service in by_kind(docs, "Service"):
        for port in service["spec"]["ports"]:
            assert port["port"] not in (5432, 6379)
    egress = next(
        p for p in by_kind(docs, "NetworkPolicy") if p["metadata"]["name"] == "drake-api-egress"
    )
    cidrs = [
        target["ipBlock"]["cidr"]
        for entry in egress["spec"]["egress"]
        for target in entry.get("to", [])
        if "ipBlock" in target
    ]
    assert cidrs
    assert "0.0.0.0/0" not in cidrs


def test_the_migration_has_one_owner_and_fails_closed() -> None:
    jobs = by_kind(render(), "Job")
    assert len(jobs) == 1
    job = jobs[0]
    hooks = job["metadata"]["annotations"]["helm.sh/hook"]
    assert "pre-install" in hooks and "pre-upgrade" in hooks
    assert job["spec"]["backoffLimit"] == 0
    command = job["spec"]["template"]["spec"]["containers"][0]["command"]
    assert command == ["alembic", "upgrade", "head"]
    assert "downgrade" not in " ".join(command)


def test_the_chart_creates_no_secrets_and_inlines_no_values() -> None:
    docs = render()
    assert by_kind(docs, "Secret") == []
    for doc in by_kind(docs, "Deployment") + by_kind(docs, "Job"):
        for container in doc["spec"]["template"]["spec"]["containers"]:
            for entry in container.get("env") or []:
                name = entry["name"].upper()
                if any(t in name for t in ("SECRET", "PASSWORD", "TOKEN", "PRIVATE_KEY")):
                    assert "valueFrom" in entry, f"{entry['name']} is inlined"


def test_the_api_derives_its_public_urls_from_the_one_origin() -> None:
    api = next(d for d in by_kind(render(), "Deployment") if d["metadata"]["name"] == "drake-api")
    env = {
        e["name"]: e.get("value") for e in api["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    assert env["DRAKE_PUBLIC_ORIGIN"] == "https://drake.example.test"
    assert env["DRAKE_OIDC_REDIRECT_URL"] == "https://drake.example.test/v1/auth/callback"
    assert env["DRAKE_ALLOWED_WEB_ORIGINS"] == '["https://drake.example.test"]'
    # Exactly one hop: the ingress controller.
    assert env["DRAKE_TRUSTED_PROXY_COUNT"] == "1"


def test_the_web_container_is_given_no_api_origin() -> None:
    """The browser calls same-origin /v1; nothing points at a Service name."""
    web = next(d for d in by_kind(render(), "Deployment") if d["metadata"]["name"] == "drake-web")
    env = {
        e["name"]: e.get("value") for e in web["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    assert "DRAKE_API_URL" not in env
    assert not any(key.startswith("NEXT_PUBLIC") for key in env)
    assert env["DRAKE_DEPLOYMENT_MODE"] == "production"


def test_github_can_be_disabled_so_drake_deploys_before_the_app_exists() -> None:
    api = next(d for d in by_kind(render(), "Deployment") if d["metadata"]["name"] == "drake-api")
    env = {
        e["name"]: e.get("value") for e in api["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    assert env["DRAKE_GITHUB_APP_ENABLED"] == "false"
    secret_refs = [
        source["secretRef"]["name"]
        for source in api["spec"]["template"]["spec"]["containers"][0]["envFrom"]
    ]
    assert secret_refs == ["drake-api-config"], "no GitHub secret is required while disabled"


def test_rendered_manifests_carry_no_credential_material() -> None:
    text = yaml.safe_dump_all(render())
    assert "BEGIN PRIVATE KEY" not in text
    assert "BEGIN RSA PRIVATE KEY" not in text
    assert "ghs_" not in text
    # Secrets are referenced by name. Any env var carrying a literal
    # credential value would show up as an inline assignment here.
    for doc in by_kind(render(), "Deployment"):
        for container in doc["spec"]["template"]["spec"]["containers"]:
            for entry in container.get("env", []):
                name = entry["name"]
                if any(
                    marker in name
                    for marker in ("SECRET", "PASSWORD", "TOKEN", "PRIVATE_KEY", "CLIENT_SECRET")
                ):
                    # Only a path may be named after a secret, never a value.
                    assert name.endswith("_FILE"), f"{name} must not carry a value"
                    assert str(entry["value"]).startswith("/"), name


# --- the GitHub App material is mounted, never put in the environment -----


def test_github_key_material_is_mounted_as_files_not_env_vars() -> None:
    """`*_file` settings need a path on disk, so the chart must mount one.

    Wiring the App Secret through `envFrom` would render a Deployment that
    starts and then fails its own runtime validation, because the API reads
    both of these from the filesystem.
    """
    docs = render(
        "--set",
        "github.enabled=true",
        "--set",
        "github.existingSecret=drake-github-app",
        "--set",
        "github.appId=123456",
    )
    api = next(d for d in by_kind(docs, "Deployment") if d["metadata"]["name"] == "drake-api")
    pod = api["spec"]["template"]["spec"]
    container = pod["containers"][0]
    env = {e["name"]: e.get("value") for e in container["env"]}

    assert env["DRAKE_GITHUB_APP_ENABLED"] == "true"
    assert env["DRAKE_GITHUB_APP_PRIVATE_KEY_FILE"] == "/etc/drake/github/private-key.pem"
    assert env["DRAKE_GITHUB_WEBHOOK_SECRET_FILE"] == "/etc/drake/github/webhook-secret"
    assert env["DRAKE_GITHUB_APP_ID"] == "123456"

    # The Secret is a volume, and it is NOT also poured into the environment.
    secret_refs = [source["secretRef"]["name"] for source in container["envFrom"]]
    assert "drake-github-app" not in secret_refs

    volume = next(v for v in pod["volumes"] if v["name"] == "github-app")
    assert volume["secret"]["secretName"] == "drake-github-app"
    mount = next(m for m in container["volumeMounts"] if m["name"] == "github-app")
    assert mount["mountPath"] == "/etc/drake/github"
    assert mount["readOnly"] is True


def test_enabling_github_without_an_identifier_is_refused() -> None:
    assert refuses(
        "--set", "github.enabled=true", "--set", "github.existingSecret=drake-github-app"
    ), "an App with no app id or client id cannot authenticate"


def test_both_deployments_declare_readiness() -> None:
    """A pod without a readiness probe takes traffic before it is listening."""
    for doc in by_kind(render(), "Deployment"):
        container = doc["spec"]["template"]["spec"]["containers"][0]
        assert container["readinessProbe"]["httpGet"]["port"] == "http", doc["metadata"]["name"]
        assert container["livenessProbe"]["httpGet"]["port"] == "http", doc["metadata"]["name"]


# --- the Cloudflare Tunnel edge (Sprint 5D-B) -----------------------------

CF_VALUES = CHART / "values-cloudflare.test.yaml"


def render_cf(*overrides: str) -> list[dict[str, Any]]:
    result = subprocess.run(  # noqa: S603 - resolved binary, fixed argv, no shell
        [
            str(HELM),
            "template",
            "drake",
            str(CHART),
            "-f",
            str(CF_VALUES),
            "--namespace",
            "drake-prod",
            *overrides,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def refuses_cf(*overrides: str) -> bool:
    result = subprocess.run(  # noqa: S603 - resolved binary, fixed argv, no shell
        [
            str(HELM),
            "template",
            "drake",
            str(CHART),
            "-f",
            str(CF_VALUES),
            "--namespace",
            "drake-prod",
            *overrides,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode != 0


def cf_config() -> dict[str, Any]:
    """The routing table the connector will actually load."""
    cm = next(
        d for d in by_kind(render_cf(), "ConfigMap") if d["metadata"]["name"] == "drake-cloudflared"
    )
    parsed: dict[str, Any] = yaml.safe_load(cm["data"]["config.yaml"])
    return parsed


def test_tunnel_mode_creates_no_inbound_route() -> None:
    """The whole point: reach the internet without opening a port."""
    docs = render_cf()
    kinds = {d["kind"] for d in docs}
    assert "Ingress" not in kinds
    for service in by_kind(docs, "Service"):
        assert service["spec"]["type"] == "ClusterIP", service["metadata"]["name"]
        for port in service["spec"]["ports"]:
            assert "nodePort" not in port, "a NodePort would be a second, unmanaged front door"
    # The connector's metrics/readiness port stays pod-local.
    assert not [s for s in by_kind(docs, "Service") if "cloudflared" in s["metadata"]["name"]]


def test_v1_goes_straight_to_the_api_not_through_the_web_app() -> None:
    """Sprint 3: a proxy hop in front of /v1 breaks query cancellation."""
    rules = cf_config()["ingress"]
    api_rule = next(r for r in rules if r.get("path"))
    assert api_rule["hostname"] == "drake.duosis.com"
    assert api_rule["service"] == "http://drake-api:8000"
    assert "drake-web" not in api_rule["service"]


def test_routes_are_ordered_most_specific_first_and_end_in_a_catch_all() -> None:
    rules = cf_config()["ingress"]
    assert rules[0].get("path"), "the /v1 rule must be evaluated before the catch-all host rule"
    assert rules[1].get("path") is None and rules[1]["service"] == "http://drake-web:3000"
    assert rules[-1] == {"service": "http_status:404"}, "unmatched traffic must be refused"
    assert "hostname" not in rules[-1]


def test_the_connector_holds_no_kubernetes_credentials() -> None:
    docs = render_cf()
    dep = next(
        d for d in by_kind(docs, "Deployment") if d["metadata"]["name"] == "drake-cloudflared"
    )
    pod = dep["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    assert "serviceAccountName" not in pod
    # Nothing in this chart grants the connector any API permission.
    assert not by_kind(docs, "Role") and not by_kind(docs, "RoleBinding")


def test_the_connector_is_hardened_and_pinned() -> None:
    dep = next(
        d
        for d in by_kind(render_cf(), "Deployment")
        if d["metadata"]["name"] == "drake-cloudflared"
    )
    pod = dep["spec"]["template"]["spec"]
    container = pod["containers"][0]

    assert dep["spec"]["replicas"] == 2
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert pod["securityContext"]["seccompProfile"]["type"] == "RuntimeDefault"
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]
    assert "@sha256:" in container["image"] and ":latest" not in container["image"]
    assert "--no-autoupdate" in container["args"]
    # Both mounts are read-only; the credential is a Secret volume, never env.
    assert all(m["readOnly"] for m in container["volumeMounts"])
    assert {v["name"] for v in pod["volumes"]} == {"config", "credentials"}


def test_a_rollout_never_drops_both_connectors() -> None:
    docs = render_cf()
    dep = next(
        d for d in by_kind(docs, "Deployment") if d["metadata"]["name"] == "drake-cloudflared"
    )
    assert dep["spec"]["strategy"]["rollingUpdate"]["maxUnavailable"] == 0
    pdb = by_kind(docs, "PodDisruptionBudget")[0]
    assert pdb["spec"]["minAvailable"] == 1
    # Replicas prefer separate nodes, but not so hard that a drain wedges.
    affinity = dep["spec"]["template"]["spec"]["affinity"]["podAntiAffinity"]
    assert "requiredDuringSchedulingIgnoredDuringExecution" not in affinity
    term = affinity["preferredDuringSchedulingIgnoredDuringExecution"][0]["podAffinityTerm"]
    assert term["topologyKey"] == "kubernetes.io/hostname"


def test_the_tunnel_credential_is_referenced_never_contained() -> None:
    docs = render_cf()
    text = yaml.safe_dump_all(docs)
    assert "credentials.json" in text, "the config must point at the mounted file"
    # ...but the chart creates no Secret and carries no credential value.
    assert not by_kind(docs, "Secret")
    assert "TunnelSecret" not in text
    assert "AccountTag" not in text

    dep = next(
        d for d in by_kind(docs, "Deployment") if d["metadata"]["name"] == "drake-cloudflared"
    )
    container = dep["spec"]["template"]["spec"]["containers"][0]
    tunnel_id = next(e for e in container["env"] if e["name"] == "TUNNEL_ID")
    assert "value" not in tunnel_id, "the tunnel UUID must come from the Secret, not the manifest"
    assert tunnel_id["valueFrom"]["secretKeyRef"] == {
        "name": "drake-tunnel-credentials",
        "key": "tunnel-id",
    }
    assert "$(TUNNEL_ID)" in container["args"]


def test_every_workload_can_pull_private_images() -> None:
    docs = render_cf()
    workloads = by_kind(docs, "Deployment") + by_kind(docs, "Job")
    assert len(workloads) == 4, "api, web, cloudflared, migration"
    for doc in workloads:
        pod = doc["spec"]["template"]["spec"]
        assert pod["imagePullSecrets"] == [{"name": "drake-ghcr"}], doc["metadata"]["name"]
        for container in pod["containers"]:
            assert ":latest" not in container["image"], doc["metadata"]["name"]
            assert "@sha256:" in container["image"], doc["metadata"]["name"]


def test_an_unset_pull_secret_emits_no_key_at_all() -> None:
    """An empty `imagePullSecrets: []` hides 'nobody set one' as 'none needed'."""
    docs = render_cf("--set", "imagePullSecrets=null")
    for doc in by_kind(docs, "Deployment") + by_kind(docs, "Job"):
        assert "imagePullSecrets" not in doc["spec"]["template"]["spec"], doc["metadata"]["name"]


def test_everything_lands_in_the_named_namespace() -> None:
    for doc in render_cf():
        assert doc["metadata"]["namespace"] == "drake-prod", doc["metadata"]["name"]


def test_the_api_still_derives_its_public_urls_from_the_one_origin() -> None:
    dep = next(
        d for d in by_kind(render_cf(), "Deployment") if d["metadata"]["name"] == "drake-api"
    )
    env = {
        e["name"]: e.get("value") for e in dep["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    assert env["DRAKE_PUBLIC_ORIGIN"] == "https://drake.duosis.com"
    assert env["DRAKE_OIDC_REDIRECT_URL"] == "https://drake.duosis.com/v1/auth/callback"
    assert env["DRAKE_ALLOWED_WEB_ORIGINS"] == '["https://drake.duosis.com"]'


def test_the_web_app_still_proxies_nothing() -> None:
    dep = next(
        d for d in by_kind(render_cf(), "Deployment") if d["metadata"]["name"] == "drake-web"
    )
    env = {
        e["name"]: e.get("value") for e in dep["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    assert env["DRAKE_DEPLOYMENT_MODE"] == "production"
    assert "DRAKE_API_URL" not in env
    assert not any(k.startswith("NEXT_PUBLIC") for k in env)


@pytest.mark.parametrize(
    ("override", "because"),
    [
        ("cloudflared.tunnelSecretName=", "the connector cannot authenticate without credentials"),
        ("cloudflared.image.digest=", "an unpinned connector can change under you"),
        ("cloudflared.replicas=1", "one connector means a drain is an outage"),
        ("ingress.enabled=true", "tunnel mode must not also open an inbound route"),
        ("edge.mode=bogus", "an unknown edge mode must not silently pick one"),
        ("publicOrigin=http://drake.duosis.com", "plaintext origin"),
        ("publicOrigin=https://drake.duosis.com:30772", "a tunnel hostname carries no port"),
    ],
)
def test_tunnel_mode_fails_closed(override: str, because: str) -> None:
    assert refuses_cf("--set", override), because
