"""The production Helm chart renders the edge contract, or refuses.

These render the real chart with `helm template`, so what is asserted is
what would install. A chart that renders something plausible from a
missing value is how an edge ends up without TLS.
"""

import json
import re
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
        ("networkPolicy.database.podSelector.matchLabels=null", "no database selector"),
        ("networkPolicy.redis.podSelector.matchLabels=null", "no redis selector"),
        ("networkPolicy.database.port=", "no database port"),
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
    selected = [
        target["podSelector"]["matchLabels"]
        for entry in egress["spec"]["egress"]
        for target in entry.get("to", [])
        if "podSelector" in target
    ]
    assert selected, "datastore peers are selected by label, not addressed"
    for entry in egress["spec"]["egress"]:
        assert entry.get("ports"), "every egress rule must be port-restricted"


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


# --- private images, explicit namespace, and the internal edge (Sprint 5D)


PROD_VALUES = CHART / "values-drake-prod.yaml"

# The real production values leave digests and datastore CIDRs for the
# operator, so tests supply shape-correct stand-ins to exercise the rest.
_PROD_FILL: tuple[str, ...] = ()


def render_prod(*overrides: str) -> list[dict[str, Any]]:
    result = subprocess.run(  # noqa: S603 - resolved binary, fixed argv, no shell
        [
            str(HELM),
            "template",
            "drake",
            str(CHART),
            "-f",
            str(PROD_VALUES),
            "--namespace",
            "drake-prod",
            *_PROD_FILL,
            *overrides,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def refuses_prod(*overrides: str) -> bool:
    result = subprocess.run(  # noqa: S603 - resolved binary, fixed argv, no shell
        [
            str(HELM),
            "template",
            "drake",
            str(CHART),
            "-f",
            str(PROD_VALUES),
            "--namespace",
            "drake-prod",
            *_PROD_FILL,
            *overrides,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode != 0


def test_internal_mode_publishes_no_public_route() -> None:
    """The mode that proves the app runs before a name is attached to it."""
    docs = render_prod("--set", "edge.mode=internal", "--set", "ingress.enabled=false")
    kinds = {d["kind"] for d in docs}
    assert "Ingress" not in kinds
    for service in by_kind(docs, "Service"):
        assert service["spec"]["type"] == "ClusterIP", service["metadata"]["name"]
        for port in service["spec"]["ports"]:
            assert "nodePort" not in port
    assert "LoadBalancer" not in {s["spec"]["type"] for s in by_kind(docs, "Service")}


def test_internal_mode_still_demands_a_real_identity() -> None:
    """No public route does not mean no origin: cookies and OIDC need one."""
    assert refuses_prod("--set", "publicOrigin="), "an unpublished deployment still has an identity"
    api = next(
        d for d in by_kind(render_prod(), "Deployment") if d["metadata"]["name"] == "drake-api"
    )
    env = {
        e["name"]: e.get("value") for e in api["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    origin = yaml.safe_load(PROD_VALUES.read_text())["publicOrigin"]
    assert origin.startswith("https://")
    assert env["DRAKE_PUBLIC_ORIGIN"] == origin
    assert env["DRAKE_OIDC_REDIRECT_URL"] == f"{origin}/v1/auth/callback"


def test_the_expected_production_workloads_and_nothing_else() -> None:
    """The listener is in this list because production RUNS it.

    It was absent while `values-drake-prod.yaml` omitted `internalAgentApi`
    — and the omission was not a decision, it was a gap: revision 12 ran
    the listener in the cluster while the committed values said nothing,
    so the first upgrade driven only by this file deleted it.
    """
    docs = render_prod()
    assert sorted(d["metadata"]["name"] for d in by_kind(docs, "Deployment")) == [
        "drake-agent-gateway",
        "drake-api",
        "drake-edge",
        "drake-web",
    ]
    assert [d["metadata"]["name"] for d in by_kind(docs, "Job")] == ["drake-migrate"]
    assert sorted(d["metadata"]["name"] for d in by_kind(docs, "Service")) == [
        "drake-agent-gateway",
        "drake-api",
        "drake-edge",
        "drake-web",
    ]


def test_every_workload_can_pull_private_images() -> None:
    docs = render_prod()
    workloads = by_kind(docs, "Deployment") + by_kind(docs, "Job")
    assert len(workloads) == 5, "api, web, edge, agent-gateway, migration"
    for doc in workloads:
        pod = doc["spec"]["template"]["spec"]
        assert pod["imagePullSecrets"] == [{"name": "drake-ghcr"}], doc["metadata"]["name"]
        for container in pod["containers"]:
            assert ":latest" not in container["image"], doc["metadata"]["name"]
            assert "@sha256:" in container["image"], doc["metadata"]["name"]


def test_an_unset_pull_secret_emits_no_key_at_all() -> None:
    """`imagePullSecrets: []` would hide 'nobody set one' as 'none needed'."""
    docs = render_prod("--set", "imagePullSecrets=null")
    for doc in by_kind(docs, "Deployment") + by_kind(docs, "Job"):
        assert "imagePullSecrets" not in doc["spec"]["template"]["spec"], doc["metadata"]["name"]


def test_every_resource_names_its_namespace() -> None:
    """Under review, which namespace this lands in must be in the file."""
    cluster_scoped = {"ClusterRole", "ClusterRoleBinding", "IngressClass"}
    for doc in render_prod():
        if doc["kind"] in cluster_scoped:
            assert "namespace" not in doc["metadata"], doc["metadata"]["name"]
            continue
        assert doc["metadata"]["namespace"] == "drake-prod", doc["metadata"]["name"]


def test_probes_match_endpoints_the_api_actually_serves() -> None:
    api = next(
        d for d in by_kind(render_prod(), "Deployment") if d["metadata"]["name"] == "drake-api"
    )
    container = api["spec"]["template"]["spec"]["containers"][0]
    assert container["livenessProbe"]["httpGet"]["path"] == "/health/live"
    assert container["readinessProbe"]["httpGet"]["path"] == "/health/ready"


def test_the_chart_creates_no_secret_and_references_one_by_name() -> None:
    docs = render_prod()
    assert not by_kind(docs, "Secret")
    api = next(d for d in by_kind(docs, "Deployment") if d["metadata"]["name"] == "drake-api")
    refs = [
        source["secretRef"]["name"]
        for source in api["spec"]["template"]["spec"]["containers"][0]["envFrom"]
    ]
    assert refs == ["drake-api-config"]


def test_the_production_values_are_complete_as_committed() -> None:
    """No operator blanks remain: selectors replaced the address contract."""
    text = PROD_VALUES.read_text()
    assert "REPLACE" not in text
    assert "CIDR" not in text, "the address-based contract is gone"
    assert render_prod(), "the committed production values must render on their own"


@pytest.mark.parametrize(
    ("override", "because"),
    [
        ("edge.mode=bogus", "an unknown edge mode must not silently pick one"),
        ("edge.mode=internal", "internal mode must not also publish a route"),
        ("api.image.digest=", "an unpinned image can change under you"),
        ("api.image.tag=latest", "latest is never deployable"),
        (
            "networkPolicy.database.podSelector.matchLabels=null",
            "an empty selector would match every pod in the namespace",
        ),
        ("networkPolicy.redis.port=", "a peer without a port is unrestricted"),
        ("api.existingSecret=", "secrets are referenced, never inlined"),
    ],
)
def test_production_values_fail_closed(override: str, because: str) -> None:
    assert refuses_prod("--set", override), because


def test_production_images_are_real_digests_from_one_commit() -> None:
    """API, web and migration must not drift apart across releases.

    The migration runs `alembic upgrade head` from the API image, so a
    migration image older or newer than the API it precedes would apply a
    schema the running code does not expect.
    """
    values = yaml.safe_load(PROD_VALUES.read_text())
    api = values["api"]["image"]["digest"]
    web = values["web"]["image"]["digest"]
    migration = values["migration"]["image"]["digest"]

    for name, digest in (("api", api), ("web", web), ("migration", migration)):
        assert digest.startswith("sha256:"), name
        assert len(digest) == len("sha256:") + 64, name
        # A placeholder is a run of one repeated hex character.
        body = digest.removeprefix("sha256:")
        assert len(set(body)) > 4, f"{name} digest looks like a placeholder"

    assert migration == api, "the migration must run the exact code that is about to serve"
    assert web != api, "web and api are different images"

    # And a tag must never accompany a digest in production.
    for component in ("api", "web", "migration"):
        assert not values[component]["image"].get("tag"), component


# --- NetworkPolicy: in-cluster peers are selected, never addressed --------


def _policies(docs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {p["metadata"]["name"]: p for p in by_kind(docs, "NetworkPolicy")}


def _egress_peers(policy: dict[str, Any]) -> set[tuple[str, int]]:
    """(selected label value, port) pairs this policy actually permits."""
    out: set[tuple[str, int]] = set()
    for rule in policy["spec"].get("egress", []):
        ports = {p["port"] for p in rule.get("ports", [])}
        for peer in rule.get("to", []):
            for value in peer.get("podSelector", {}).get("matchLabels", {}).values():
                out |= {(value, port) for port in ports}
    return out


def test_datastores_are_never_reached_by_address() -> None:
    """A ClusterIP in an ipBlock is not portable and a pod IP is not stable.

    Where a Service address is rewritten relative to policy evaluation is
    up to the CNI, so an address-based rule may work on one cluster and
    silently deny on another. `podSelector` is what Kubernetes defines for
    in-cluster peers.
    """
    policies = _policies(render_prod())
    for name in ("drake-api-egress", "drake-migration-egress"):
        for rule in policies[name]["spec"].get("egress", []):
            for peer in rule.get("to", []):
                assert "ipBlock" not in peer, f"{name} addresses a peer instead of selecting it"


def test_default_deny_still_covers_both_directions() -> None:
    policy = _policies(render_prod())["drake-default-deny"]
    assert policy["spec"]["podSelector"] == {}
    assert set(policy["spec"]["policyTypes"]) == {"Ingress", "Egress"}


def test_the_api_reaches_dns_postgres_and_redis_and_nothing_else() -> None:
    policy = _policies(render_prod())["drake-api-egress"]
    assert policy["spec"]["podSelector"]["matchLabels"]["app.kubernetes.io/component"] == "api"
    peers = _egress_peers(policy)
    assert ("drake-postgres", 5432) in peers
    assert ("drake-redis", 6379) in peers
    # Every rule is port-restricted; none is an open door.
    for rule in policy["spec"]["egress"]:
        assert rule.get("ports"), "an egress rule without ports permits every port"
    # DNS is the only namespace-wide peer, and only on 53.
    for rule in policy["spec"]["egress"]:
        if any("namespaceSelector" in p and "podSelector" not in p for p in rule.get("to", [])):
            assert {p["port"] for p in rule["ports"]} == {53}


def test_the_migration_gets_postgres_but_not_redis() -> None:
    """`alembic/env.py` reads DRAKE_DATABASE_URL and opens nothing else."""
    policy = _policies(render_prod())["drake-migration-egress"]
    peers = _egress_peers(policy)
    assert ("drake-postgres", 5432) in peers
    assert not any(target == "drake-redis" for target, _ in peers)


def test_the_migration_policy_exists_before_the_migration_pod() -> None:
    """A plain release resource lands after the pre-install hook has run.

    Under enforcement that ordering denies the migration DNS and Postgres
    on a clean install, so the policy is itself a hook, weighted ahead of
    the Job, and kept so the next upgrade's migration still has access.
    """
    docs = render_prod()
    policy = _policies(docs)["drake-migration-egress"]
    job = by_kind(docs, "Job")[0]

    annotations = policy["metadata"]["annotations"]
    assert "pre-install" in annotations["helm.sh/hook"]
    assert "pre-upgrade" in annotations["helm.sh/hook"]
    assert annotations["helm.sh/hook-delete-policy"] == "before-hook-creation"

    policy_weight = int(annotations["helm.sh/hook-weight"])
    job_weight = int(job["metadata"]["annotations"]["helm.sh/hook-weight"])
    assert policy_weight < job_weight, "the policy must be created before the Job"


def test_the_web_app_is_granted_no_egress() -> None:
    """It makes no server-side call: the browser talks to /v1 directly."""
    policies = _policies(render_prod())
    for policy in policies.values():
        selector = policy["spec"]["podSelector"].get("matchLabels", {})
        if selector.get("app.kubernetes.io/component") == "web":
            raise AssertionError("the web app needs no egress and should be granted none")


def test_external_egress_is_empty_by_default_and_narrow_when_set() -> None:
    policy = _policies(render_prod())["drake-api-egress"]
    assert not [
        peer
        for rule in policy["spec"]["egress"]
        for peer in rule.get("to", [])
        if "ipBlock" in peer
    ], "a default Drake calls no third party"

    docs = render_prod(
        "--set",
        "networkPolicy.apiExternalEgress[0].cidr=203.0.113.10/32",
        "--set",
        "networkPolicy.apiExternalEgress[0].port=443",
    )
    blocks = [
        peer["ipBlock"]["cidr"]
        for rule in _policies(docs)["drake-api-egress"]["spec"]["egress"]
        for peer in rule.get("to", [])
        if "ipBlock" in peer
    ]
    assert blocks == ["203.0.113.10/32"]


def test_identical_datastore_selectors_are_refused() -> None:
    """Otherwise each peer silently inherits the other's access."""
    assert refuses_prod(
        "--set",
        r"networkPolicy.redis.podSelector.matchLabels.app\.kubernetes\.io/name=drake-postgres",
    )


def test_the_migration_job_is_sized_for_a_short_ddl_run() -> None:
    """Exact values, so a careless edit to either side is caught.

    Alembic applies DDL and waits on the database: short-lived and
    single-threaded. It is deliberately given less than the API rather
    than inheriting the API's sizing.
    """
    job = by_kind(render_prod(), "Job")[0]
    resources = job["spec"]["template"]["spec"]["containers"][0]["resources"]
    assert resources == {
        "requests": {"cpu": "50m", "memory": "128Mi"},
        "limits": {"cpu": "500m", "memory": "512Mi"},
    }

    def millicores(value: str) -> int:
        return int(value[:-1]) if value.endswith("m") else int(float(value) * 1000)

    def mebibytes(value: str) -> int:
        return int(value.removesuffix("Mi")) if value.endswith("Mi") else int(value) // 2**20

    assert millicores(resources["requests"]["cpu"]) > 0
    assert mebibytes(resources["requests"]["memory"]) > 0
    assert millicores(resources["limits"]["cpu"]) >= millicores(resources["requests"]["cpu"])
    assert mebibytes(resources["limits"]["memory"]) >= mebibytes(resources["requests"]["memory"])

    api = next(
        d for d in by_kind(render_prod(), "Deployment") if d["metadata"]["name"] == "drake-api"
    )
    api_limits = api["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"]
    assert millicores(resources["limits"]["cpu"]) < millicores(api_limits["cpu"])
    assert mebibytes(resources["limits"]["memory"]) <= mebibytes(api_limits["memory"])


# --- the datastores are same-namespace, and DNS is the resolver only -----


def test_datastore_peers_are_same_namespace_only() -> None:
    """A NetworkPolicy governs only its own namespace.

    Allowing the egress side to point elsewhere would render a rule that
    looks complete while the matching ingress policy — which this chart
    creates in Drake's namespace — could never apply to the peer.
    """
    policies = _policies(render_prod())
    for name in ("drake-api-egress", "drake-migration-egress"):
        for rule in policies[name]["spec"].get("egress", []):
            ports = {p["port"] for p in rule.get("ports", [])}
            if not (ports & {5432, 6379}):
                continue
            for peer in rule.get("to", []):
                assert "namespaceSelector" not in peer, f"{name} datastore peer crosses namespaces"
                assert "podSelector" in peer


@pytest.mark.parametrize(
    "override",
    [
        "networkPolicy.database.namespaceSelector.matchLabels.name=elsewhere",
        "networkPolicy.redis.namespaceSelector.matchLabels.name=elsewhere",
    ],
)
def test_a_cross_namespace_datastore_is_refused_not_ignored(override: str) -> None:
    assert refuses_prod("--set", override)


def test_dns_is_narrowed_to_the_resolver_pods() -> None:
    """`namespaceSelector: {}` on port 53 is not "DNS only"."""
    for name in ("drake-api-egress", "drake-migration-egress"):
        policy = _policies(render_prod())[name]
        dns_rules = [
            r for r in policy["spec"]["egress"] if {p["port"] for p in r.get("ports", [])} == {53}
        ]
        assert len(dns_rules) == 1, f"{name} should have exactly one DNS rule"
        rule = dns_rules[0]
        assert {p["protocol"] for p in rule["ports"]} == {"TCP", "UDP"}

        # Both selectors in ONE peer, so they intersect rather than union.
        assert len(rule["to"]) == 1
        peer = rule["to"][0]
        assert peer["namespaceSelector"]["matchLabels"] == {
            "kubernetes.io/metadata.name": "kube-system"
        }
        assert peer["podSelector"]["matchLabels"] == {"k8s-app": "kube-dns"}


@pytest.mark.parametrize(
    "override",
    [
        "networkPolicy.dns.namespaceSelector.matchLabels=null",
        "networkPolicy.dns.podSelector.matchLabels=null",
        "networkPolicy.dns.port=",
    ],
)
def test_an_unnarrowed_dns_rule_is_refused(override: str) -> None:
    assert refuses_prod("--set", override)


# --- declared outbound HTTPS is allowed; datastores by address are not ---


def test_a_narrow_https_egress_entry_renders() -> None:
    docs = render_prod(
        "--set",
        "networkPolicy.apiExternalEgress[0].cidr=203.0.113.10/32",
        "--set",
        "networkPolicy.apiExternalEgress[0].port=443",
    )
    policy = _policies(docs)["drake-api-egress"]
    blocks = [
        (peer["ipBlock"]["cidr"], {p["port"] for p in rule["ports"]})
        for rule in policy["spec"]["egress"]
        for peer in rule.get("to", [])
        if "ipBlock" in peer
    ]
    assert blocks == [("203.0.113.10/32", {443})]


@pytest.mark.parametrize(
    ("overrides", "because"),
    [
        (
            [
                "networkPolicy.apiExternalEgress[0].cidr=0.0.0.0/0",
                "networkPolicy.apiExternalEgress[0].port=443",
            ],
            "all of IPv4 defeats the policy",
        ),
        (
            [
                "networkPolicy.apiExternalEgress[0].cidr=::/0",
                "networkPolicy.apiExternalEgress[0].port=443",
            ],
            "all of IPv6 defeats the policy",
        ),
        (["networkPolicy.apiExternalEgress[0].cidr=203.0.113.10/32"], "an entry needs a port"),
        (["networkPolicy.apiExternalEgress[0].port=443"], "an entry needs a cidr"),
        (
            [
                "networkPolicy.apiExternalEgress[0].cidr=203.0.113.10/32",
                "networkPolicy.apiExternalEgress[0].port=5432",
            ],
            "this field is outbound HTTPS; a datastore port must never be reachable by address",
        ),
    ],
)
def test_external_egress_fails_closed(overrides: list[str], because: str) -> None:
    flags: list[str] = []
    for override in overrides:
        flags += ["--set", override]
    assert refuses_prod(*flags), because


def test_no_datastore_port_is_ever_reachable_by_address() -> None:
    docs = render_prod(
        "--set",
        "networkPolicy.apiExternalEgress[0].cidr=203.0.113.10/32",
        "--set",
        "networkPolicy.apiExternalEgress[0].port=443",
    )
    for policy in _policies(docs).values():
        for rule in policy["spec"].get("egress", []):
            ports = {p["port"] for p in rule.get("ports", [])}
            if not any("ipBlock" in peer for peer in rule.get("to", [])):
                continue
            assert not (ports & {5432, 6379}), "a datastore behind an ipBlock is not portable"


# --- hook resources outlive the release, and that is documented ----------


RUNBOOK = CHART.parents[1] / "docs" / "runbooks" / "STANDARD_KUBERNETES_DEPLOYMENT.md"


def test_hook_policies_carry_no_misleading_retention_annotation() -> None:
    """Helm never tracked these as release resources in the first place."""
    for policy in _policies(render_prod()).values():
        annotations = policy["metadata"].get("annotations", {})
        if "helm.sh/hook" not in annotations:
            continue
        assert "helm.sh/resource-policy" not in annotations
        assert annotations["helm.sh/hook-delete-policy"] == "before-hook-creation"


def test_the_runbook_names_the_policies_that_outlive_an_uninstall() -> None:
    surviving = [
        name
        for name, policy in _policies(render_prod()).items()
        if "helm.sh/hook" in policy["metadata"].get("annotations", {})
    ]
    assert sorted(surviving) == ["drake-database-migration-ingress", "drake-migration-egress"]

    text = RUNBOOK.read_text()
    for name in surviving:
        assert name in text, f"{name} survives uninstall and must be named in the runbook"
    assert "delete networkpolicy" in text
    # The cleanup must not reach the datastores themselves.
    start = text.index("kubectl -n drake-prod delete networkpolicy")
    command = text[start : text.index("```", start)]
    assert "drake-migration-egress" in command
    assert "drake-database-migration-ingress" in command
    for forbidden in ("pvc", "persistentvolumeclaim", "statefulset", "secret", "deployment", "ns "):
        assert forbidden not in command.lower(), f"cleanup must not touch {forbidden}"


# --- the API's database route must survive an upgrade --------------------


def test_api_database_ingress_is_a_release_resource_not_a_hook() -> None:
    """`before-hook-creation` deletes and recreates hooks on every upgrade.

    If one policy carried both the API's and the migration's access, that
    window would revoke a running API's database access while default-deny
    stayed in force. Policy programming is asynchronous — the k3s run
    showed a new pod denied for several seconds — so the gap is real.
    """
    policy = _policies(render_prod())["drake-database-api-ingress"]
    annotations = policy["metadata"].get("annotations", {})
    assert "helm.sh/hook" not in annotations
    assert "helm.sh/hook-delete-policy" not in annotations


def test_migration_database_ingress_is_a_hook_created_before_the_job() -> None:
    docs = render_prod()
    policy = _policies(docs)["drake-database-migration-ingress"]
    annotations = policy["metadata"]["annotations"]
    assert "pre-install" in annotations["helm.sh/hook"]
    assert "pre-upgrade" in annotations["helm.sh/hook"]
    assert annotations["helm.sh/hook-delete-policy"] == "before-hook-creation"

    job = by_kind(docs, "Job")[0]
    assert int(annotations["helm.sh/hook-weight"]) < int(
        job["metadata"]["annotations"]["helm.sh/hook-weight"]
    )


def _admitted(policy: dict[str, Any]) -> set[str | None]:
    return {
        peer["podSelector"]["matchLabels"].get("app.kubernetes.io/component")
        for rule in policy["spec"].get("ingress", [])
        for peer in rule.get("from", [])
        if "podSelector" in peer
    }


def test_the_two_database_ingress_policies_do_not_overlap() -> None:
    """Each admits exactly one component, which is what makes the split work."""
    policies = _policies(render_prod())
    assert _admitted(policies["drake-database-api-ingress"]) == {"api"}
    assert _admitted(policies["drake-database-migration-ingress"]) == {"migration"}
    assert _admitted(policies["drake-redis-ingress"]) == {"api"}


def test_recreating_the_migration_hook_cannot_remove_the_apis_access() -> None:
    """The only policy Helm deletes mid-upgrade admits nothing but the migration."""
    policies = _policies(render_prod())
    recreated = [
        name
        for name, policy in policies.items()
        if policy["metadata"].get("annotations", {}).get("helm.sh/hook-delete-policy")
        == "before-hook-creation"
    ]
    for name in recreated:
        assert "api" not in _admitted(policies[name]), (
            f"{name} is deleted and recreated on every upgrade; admitting the API "
            "there would interrupt live database access"
        )
    # ...and the API's own route is not in that set.
    assert "drake-database-api-ingress" not in recreated


def test_the_dns_port_is_pinned_to_53() -> None:
    for name in ("drake-api-egress", "drake-migration-egress"):
        policy = _policies(render_prod())[name]
        dns = [
            r
            for r in policy["spec"]["egress"]
            if {p["protocol"] for p in r["ports"]} == {"TCP", "UDP"}
        ]
        assert len(dns) == 1
        assert {p["port"] for p in dns[0]["ports"]} == {53}


@pytest.mark.parametrize("port", ["5353", "0", "80"])
def test_a_dns_port_other_than_53_is_refused(port: str) -> None:
    assert refuses_prod("--set", f"networkPolicy.dns.port={port}")


def test_an_empty_namespace_selector_key_is_refused_like_a_populated_one() -> None:
    """A stale overlay carrying `namespaceSelector: {}` must not pass."""
    import tempfile

    for datastore in ("database", "redis"):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as handle:
            handle.write(f"networkPolicy:\n  {datastore}:\n    namespaceSelector: {{}}\n")
            overlay = handle.name
        result = subprocess.run(  # noqa: S603 - resolved binary, fixed argv, no shell
            [
                str(HELM),
                "template",
                "drake",
                str(CHART),
                "-f",
                str(PROD_VALUES),
                "-f",
                overlay,
                "--namespace",
                "drake-prod",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        Path(overlay).unlink()
        assert result.returncode != 0, f"empty {datastore}.namespaceSelector was accepted"


def test_the_runbook_never_puts_a_patch_body_on_the_command_line() -> None:
    """A secret in argv is visible to `ps` and lands in shell history."""
    for line in RUNBOOK.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("kubectl") and "patch" in stripped:
            assert " -p " not in stripped and " --patch " not in stripped
    assert "--patch-file" in RUNBOOK.read_text()


def test_the_cleanup_command_leaves_helm_managed_policies_alone() -> None:
    text = RUNBOOK.read_text()
    start = text.index("kubectl -n drake-prod delete networkpolicy")
    command = text[start : text.index("```", start)]
    assert "drake-migration-egress" in command
    assert "drake-database-migration-ingress" in command
    assert "drake-database-api-ingress" not in command, "Helm manages that one"
    assert "drake-redis-ingress" not in command, "Helm manages that one"


def test_the_runbook_never_tells_an_operator_to_set_an_unsupported_field() -> None:
    """The runbook drifted once: it advised setting a field the chart refuses.

    A document that recommends a value the render rejects is worse than
    silence — the operator follows it and the deploy stops. Markdown wraps,
    so the advice and the field name land on different lines; this inspects
    the surrounding sentence rather than the line.
    """
    text = RUNBOOK.read_text()
    unsupported = (
        "networkPolicy.database.namespaceSelector",
        "networkPolicy.redis.namespaceSelector",
    )
    # Collapse wrapping so a sentence is one string.
    flat = " ".join(text.split())

    for field in unsupported:
        for match in re.finditer(re.escape(field), flat):
            window = flat[max(0, match.start() - 200) : match.end() + 200].lower()
            advises = any(
                phrase in window
                for phrase in ("set `network", "set network", "supply ", "configure ", "only if")
            )
            refuses = any(
                phrase in window for phrase in ("not supported", "refus", "fail", "stops the")
            )
            assert not advises or refuses, (
                f"the runbook appears to advise setting {field}, which the chart refuses"
            )

    assert "not supported" in text
    assert "must live in Drake's own namespace" in text


def test_the_runbook_states_the_same_namespace_requirement() -> None:
    text = RUNBOOK.read_text().lower()
    assert "own namespace" in text
    assert "separate architectural change" in text, (
        "the runbook must say cross-namespace needs a design change, not a value"
    )


# --- the chart uses the pre-provisioned datastores, it never claims them --


def test_the_render_claims_no_out_of_band_resource() -> None:
    """Adoption is the danger, not absence.

    PostgreSQL, Redis, the PVC and the application Secret exist before the
    release and outlive it. If the chart rendered any of them, Helm would
    adopt them and `helm uninstall` would delete the database.
    """
    docs = render_prod()
    kinds = {d["kind"] for d in docs}
    for forbidden in ("Secret", "PersistentVolumeClaim", "StatefulSet"):
        assert forbidden not in kinds, f"the chart must not own a {forbidden}"

    # A NetworkPolicy *about* a datastore pod is the chart's own resource and
    # is expected; a workload or Service carrying the datastore's name would
    # be Helm claiming the datastore itself.
    owning_kinds = ("Deployment", "StatefulSet", "DaemonSet", "Service", "Job")
    for doc in docs:
        if doc["kind"] not in owning_kinds:
            continue
        name = doc["metadata"]["name"]
        assert not name.startswith(("drake-postgres", "drake-redis")), (
            f"{doc['kind']}/{name} would make Helm adopt an out-of-band datastore"
        )


def test_the_render_owns_exactly_the_application_resources() -> None:
    docs = render_prod()
    assert sorted(d["metadata"]["name"] for d in by_kind(docs, "Deployment")) == [
        "drake-agent-gateway",
        "drake-api",
        "drake-edge",
        "drake-web",
    ]
    assert [d["metadata"]["name"] for d in by_kind(docs, "Job")] == ["drake-migrate"]
    assert sorted(d["metadata"]["name"] for d in by_kind(docs, "Service")) == [
        "drake-agent-gateway",
        "drake-api",
        "drake-edge",
        "drake-web",
    ]
    assert {
        s["spec"]["type"] for s in by_kind(docs, "Service") if s["metadata"]["name"] != "drake-edge"
    } == {"ClusterIP"}


def test_the_datastores_are_referenced_only_by_label_and_secret_name() -> None:
    """The chart's whole relationship to the datastores is a reference."""
    docs = render_prod()
    api = next(d for d in by_kind(docs, "Deployment") if d["metadata"]["name"] == "drake-api")
    container = api["spec"]["template"]["spec"]["containers"][0]

    # Connection details arrive by Secret reference, never as a value.
    assert [s["secretRef"]["name"] for s in container["envFrom"]] == ["drake-api-config"]
    for entry in container.get("env", []):
        name = entry["name"]
        if "DATABASE" in name or "REDIS" in name:
            raise AssertionError(f"{name} is rendered inline instead of referenced")

    # And the network peers are labels, resolved at runtime.
    selectors = [
        # The DNS peer selects on k8s-app, so read the key tolerantly.
        peer["podSelector"]["matchLabels"].get("app.kubernetes.io/name")
        for policy in by_kind(docs, "NetworkPolicy")
        for rule in policy["spec"].get("egress", [])
        for peer in rule.get("to", [])
        if "podSelector" in peer
    ]
    assert "drake-postgres" in selectors and "drake-redis" in selectors


# --- the runbook matches a namespace that is already populated ------------


def test_the_runbook_does_not_require_an_empty_namespace() -> None:
    """The datastores must exist first, so an empty namespace is impossible."""
    text = RUNBOOK.read_text()
    assert "holds no prior resources" not in text
    flat = " ".join(text.split()).lower()
    for claim in ("namespace must be empty", "no prior resources", "must contain no"):
        if claim in flat:
            window = flat[flat.index(claim) - 200 : flat.index(claim) + 200]
            assert "impossible" in window or "would be" in window, (
                f"the runbook still demands an empty namespace: ...{claim}..."
            )


def test_the_runbook_lists_the_datastores_as_expected_preflight_state() -> None:
    text = RUNBOOK.read_text()
    section = text[text.index("Preflight the cluster") : text.index("Data-protection gate")]
    lowered = section.lower()
    assert "expected to be present" in lowered
    for prerequisite in ("postgresql", "redis", "pvc", "bound"):
        assert prerequisite in lowered, f"preflight must expect {prerequisite}"
    # ...and it must stop on the collisions that actually matter.
    for stop in ("helm release already exists", "not ready", "not `bound`", "already exists that"):
        assert stop in lowered, f"preflight must stop on: {stop}"


def test_the_runbook_never_recreates_an_existing_secret() -> None:
    text = RUNBOOK.read_text()
    flat = " ".join(text.split())
    marker = "If `drake-api-config` exists with both keys"
    assert marker in flat
    guidance = flat[flat.index(marker) : flat.index(marker) + 400].lower()
    for prohibition in ("do not recreate", "do not patch", "do not print"):
        assert prohibition in guidance, f"missing: {prohibition}"


def test_the_runbook_never_dumps_a_secret() -> None:
    """`-o yaml`/`-o json`/`describe` on a Secret prints the whole data map."""
    for line in RUNBOOK.read_text().splitlines():
        stripped = line.strip()
        if "secret" not in stripped.lower() or not stripped.startswith("kubectl"):
            continue
        if "describe" in stripped:
            raise AssertionError(f"describe prints Secret data: {stripped}")
        if "-o yaml" in stripped or "-o json" in stripped:
            raise AssertionError(f"this prints the full data map: {stripped}")


def test_the_runbook_gates_the_migration_on_a_recovery_point() -> None:
    text = RUNBOOK.read_text()
    gate = text[text.index("Data-protection gate") : text.index("**8. Install.**")]
    lowered = gate.lower()
    assert "recovery point" in lowered
    assert "stop" in lowered, "the gate must stop when no recovery point is confirmed"
    # It must not promise something --atomic cannot do.
    assert "does not undo a migration" in lowered
    assert "no automatic downgrade" in lowered or "has no automatic downgrade" in lowered
    # Baselines are identities, not pod UIDs.
    for baseline in ("uid", "volumename", "generation", "resourceversion"):
        assert baseline in lowered, f"the gate must baseline {baseline}"


def test_the_runbook_does_not_claim_pod_uids_stay_constant() -> None:
    """A rescheduled datastore pod is legitimate; demanding a fixed pod UID
    would raise false alarms while missing real adoption."""
    text = " ".join(RUNBOOK.read_text().split()).lower()
    assert "pod uids are deliberately not on that list" in text


def test_the_runbook_carries_no_point_in_time_status_claim() -> None:
    """A procedure that records cluster state is wrong the day after."""
    text = " ".join(RUNBOOK.read_text().split()).lower()
    for stale in (
        "no image published",
        "no namespace created",
        "no secret created",
        "nothing here has been executed",
        "has never been deployed",
    ):
        assert stale not in text, f"stale status claim in a permanent procedure: {stale}"
    assert "this is a procedure, not a status report" in text


# --- Kubernetes service links must never reach a Drake pod ---------------


def _pod_specs(docs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Every PodSpec this chart produces, keyed by workload name."""
    return {
        d["metadata"]["name"]: d["spec"]["template"]["spec"]
        for d in docs
        if d["kind"] in ("Deployment", "Job", "StatefulSet", "DaemonSet")
    }


def test_no_pod_receives_kubernetes_service_link_variables() -> None:
    """The first production install died on exactly this.

    Kubernetes injects a legacy Docker-style variable per Service in the
    namespace. The Service named `drake-api` produces
    `DRAKE_API_PORT=tcp://<clusterIP>:8000`, and Drake reads `DRAKE_*`
    settings — so it arrived as `api_port` and the API crash-looped parsing
    "tcp://..." as an integer. Drake resolves peers through DNS and reads
    none of these variables.
    """
    specs = _pod_specs(render_prod())
    assert set(specs) == {
        "drake-api",
        "drake-web",
        "drake-migrate",
        "drake-edge",
        "drake-agent-gateway",
    }
    for name, spec in specs.items():
        assert spec.get("enableServiceLinks") is False, (
            f"{name} would receive DRAKE_API_PORT and fail to parse it as a setting"
        )


def test_the_ingress_edge_render_is_equally_protected() -> None:
    """Both value sets, not just the one that happened to break."""
    for name, spec in _pod_specs(render()).items():
        assert spec.get("enableServiceLinks") is False, name


def test_the_setting_that_collided_is_still_named_api_port() -> None:
    """The fix is at the pod boundary, not a rename.

    Renaming the setting or the Service would have hidden the collision
    while leaving the mechanism intact for the next `DRAKE_*` name.
    """
    from drake_api.settings import Settings

    assert "api_port" in Settings.model_fields
    services = {s["metadata"]["name"] for s in by_kind(render_prod(), "Service")}
    assert services == {"drake-api", "drake-web", "drake-edge", "drake-agent-gateway"}


def test_the_fix_did_not_disturb_ownership_or_images() -> None:
    """A one-field change must not move anything else."""
    docs = render_prod()
    kinds = {d["kind"] for d in docs}
    # An Ingress is expected now; a Secret, PVC or StatefulSet never is.
    for forbidden in ("Secret", "PersistentVolumeClaim", "StatefulSet"):
        assert forbidden not in kinds
    for doc in docs:
        if doc["kind"] in ("Deployment", "Service", "Job"):
            assert not doc["metadata"]["name"].startswith(("drake-postgres", "drake-redis"))
    assert {
        s["spec"]["type"] for s in by_kind(docs, "Service") if s["metadata"]["name"] != "drake-edge"
    } == {"ClusterIP"}

    values = yaml.safe_load(PROD_VALUES.read_text())
    images = {
        d["metadata"]["name"]: d["spec"]["template"]["spec"]["containers"][0]["image"]
        for d in docs
        if d["kind"] in ("Deployment", "Job")
    }
    assert images["drake-api"].endswith(values["api"]["image"]["digest"])
    assert images["drake-web"].endswith(values["web"]["image"]["digest"])
    assert images["drake-migrate"] == images["drake-api"]


# --- the public edge: one entrance, Drake's own controller ---------------


def test_exactly_one_public_entrance() -> None:
    """Only the edge is published; the application Services stay internal."""
    docs = render_prod()
    published = {
        (s["metadata"]["name"], port["nodePort"])
        for s in by_kind(docs, "Service")
        for port in s["spec"]["ports"]
        if port.get("nodePort")
    }
    assert published == {("drake-edge", 30773)}
    for service in by_kind(docs, "Service"):
        if service["metadata"]["name"] in ("drake-api", "drake-web"):
            assert service["spec"]["type"] == "ClusterIP"
    assert not [s for s in by_kind(docs, "Service") if s["spec"]["type"] == "LoadBalancer"]


def test_the_edge_publishes_no_plaintext_port() -> None:
    """An http listener on a public node port is a way in nobody asked for."""
    edge = next(
        s for s in by_kind(render_prod(), "Service") if s["metadata"]["name"] == "drake-edge"
    )
    assert [p["name"] for p in edge["spec"]["ports"]] == ["https"]


def test_the_ingress_host_carries_no_port() -> None:
    """A Kubernetes Ingress host is a hostname; the port lives in the origin."""
    ingress = by_kind(render_prod(), "Ingress")[0]
    host = ingress["spec"]["rules"][0]["host"]
    assert ":" not in host
    assert host == "drake-84-247-180-172.sslip.io"


def test_the_public_origin_keeps_its_port() -> None:
    """Everything the browser is told derives from the full origin."""
    api = next(
        d for d in by_kind(render_prod(), "Deployment") if d["metadata"]["name"] == "drake-api"
    )
    env = {
        e["name"]: e.get("value") for e in api["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    origin = "https://drake-84-247-180-172.sslip.io:30773"
    assert env["DRAKE_PUBLIC_ORIGIN"] == origin
    assert env["DRAKE_ALLOWED_WEB_ORIGINS"] == f'["{origin}"]'
    assert env["DRAKE_OIDC_REDIRECT_URL"] == f"{origin}/v1/auth/callback"
    assert env["DRAKE_AUTH_MODE"] == "local"


def test_one_origin_routes_v1_to_the_api_and_everything_else_to_the_web() -> None:
    ingress = by_kind(render_prod(), "Ingress")[0]
    paths = {
        p["path"]: (p["pathType"], p["backend"]["service"]["name"])
        for p in ingress["spec"]["rules"][0]["http"]["paths"]
    }
    assert paths["/v1"] == ("Prefix", "drake-api")
    assert paths["/"] == ("Prefix", "drake-web")


def test_drake_runs_its_own_controller_and_claims_nothing_shared() -> None:
    """The shared controllers serve other applications; Drake touches none."""
    docs = render_prod()
    classes = by_kind(docs, "IngressClass")
    assert [c["metadata"]["name"] for c in classes] == ["drake-nginx"]
    assert classes[0]["spec"]["controller"] == "k8s.io/drake-nginx"
    assert (
        classes[0]["metadata"]
        .get("annotations", {})
        .get("ingressclass.kubernetes.io/is-default-class")
        != "true"
    ), "claiming the default class would serve other teams' Ingresses"

    ingress = by_kind(docs, "Ingress")[0]
    assert ingress["spec"]["ingressClassName"] == "drake-nginx"

    controller = next(
        d for d in by_kind(docs, "Deployment") if d["metadata"]["name"] == "drake-edge"
    )
    args = controller["spec"]["template"]["spec"]["containers"][0]["args"]
    assert "--controller-class=k8s.io/drake-nginx" in args
    assert "--ingress-class=drake-nginx" in args
    assert "--election-id=drake-edge-leader" in args, (
        "a shared lease would fight another controller"
    )
    assert any(a.startswith("--watch-namespace=") for a in args)


def test_the_edge_image_is_digest_pinned() -> None:
    controller = next(
        d for d in by_kind(render_prod(), "Deployment") if d["metadata"]["name"] == "drake-edge"
    )
    image = controller["spec"]["template"]["spec"]["containers"][0]["image"]
    assert "@sha256:" in image
    assert ":latest" not in image


def test_the_render_still_owns_no_datastore_or_shared_controller() -> None:
    docs = render_prod()
    kinds = {d["kind"] for d in docs}
    for forbidden in ("Secret", "PersistentVolumeClaim", "StatefulSet"):
        assert forbidden not in kinds
    for doc in docs:
        name = doc["metadata"]["name"]
        # Nothing belonging to the cluster's existing controllers.
        assert not name.startswith("ingress-nginx"), f"{doc['kind']}/{name} is not Drake's"
        if doc["kind"] in ("Deployment", "StatefulSet", "Service", "Job"):
            assert not name.startswith(("drake-postgres", "drake-redis"))


def test_every_drake_pod_still_disables_service_links() -> None:
    for name, spec in _pod_specs(render_prod()).items():
        assert spec.get("enableServiceLinks") is False, name


@pytest.mark.parametrize(
    ("override", "because"),
    [
        ("publicOrigin=https://84.247.180.172:30773", "a bare IP shares its cookie jar"),
        ("edge.dedicatedController.image.digest=", "an unpinned controller can change"),
        ("edge.dedicatedController.httpsNodePort=443", "outside the NodePort range"),
        ("edge.dedicatedController.controllerClass=", "two controllers would share a class"),
        ("ingress.tls.mode=bogus", "an unknown TLS mode"),
        ("edge.mode=internal", "internal mode publishes no route, so ingress must be off"),
    ],
)
def test_the_public_edge_fails_closed(override: str, because: str) -> None:
    assert refuses_prod("--set", override), because


def test_a_hostname_origin_with_a_port_is_accepted_but_a_bare_ip_is_not() -> None:
    """The port is legitimate here — the edge is a NodePort, not 443."""
    from drake_api.origin import InvalidOriginError, parse_public_origin

    accepted = parse_public_origin("https://drake-84-247-180-172.sslip.io:30773")
    assert accepted.port == 30773
    assert accepted.host == "drake-84-247-180-172.sslip.io"

    for refused in ("https://84.247.180.172:30773", "http://drake-x.sslip.io:30773"):
        with pytest.raises(InvalidOriginError):
            parse_public_origin(refused)


# --- repository writes ----------------------------------------------------


def test_production_values_keep_repository_writes_off() -> None:
    """The shipped production values enable no write path.

    Asserted on the rendered Deployment rather than on the values file: what
    matters is what the container is told, and a default that is right in
    YAML and wrong in a template is still wrong.
    """
    deployments = by_kind(render(), "Deployment")
    assert deployments
    env = {
        entry["name"]: entry.get("value")
        for entry in deployments[0]["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    assert env["DRAKE_GITHUB_GITOPS_PR_ENABLED"] == "false"
    assert env["DRAKE_GITOPS_WORKER_ENABLED"] == "false"
    assert env["DRAKE_GITHUB_APP_ENABLED"] == "false"


def test_the_chart_refuses_half_of_the_write_decision() -> None:
    """Both flags or neither.

    One alone accepts requests nothing delivers, or runs a worker nothing
    can reach — states that look like they are working.
    """
    assert refuses("--set", "github.gitopsPrEnabled=true")
    assert refuses("--set", "github.workerEnabled=true")


def test_the_chart_refuses_repository_writes_without_a_real_app() -> None:
    """A write path needs an App, and an App needs its credential mount."""
    both = ("--set", "github.gitopsPrEnabled=true", "--set", "github.workerEnabled=true")
    assert refuses(*both), "no GitHub App at all"
    assert refuses(*both, "--set", "github.enabled=true"), "no secret reference"
    assert refuses(
        *both,
        "--set",
        "github.enabled=true",
        "--set",
        "github.existingSecret=drake-github-app",
    ), "no app identity"


def test_the_chart_renders_a_fully_specified_write_path() -> None:
    """The refusal has to let a complete decision through.

    A guard that refuses everything is an outage, not a guard.
    """
    docs = render(
        "--set",
        "github.enabled=true",
        "--set",
        "github.existingSecret=drake-github-app",
        "--set",
        "github.clientId=Iv1.example",
        "--set",
        "github.gitopsPrEnabled=true",
        "--set",
        "github.workerEnabled=true",
    )
    deployment = by_kind(docs, "Deployment")[0]
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    env = {entry["name"]: entry.get("value") for entry in container["env"]}
    assert env["DRAKE_GITHUB_GITOPS_PR_ENABLED"] == "true"
    assert env["DRAKE_GITOPS_WORKER_ENABLED"] == "true"
    # References, never material: the private key and the webhook secret
    # are file paths the API reads from a mounted Secret.
    assert env["DRAKE_GITHUB_APP_PRIVATE_KEY_FILE"].endswith("private-key.pem")
    assert env["DRAKE_GITHUB_WEBHOOK_SECRET_FILE"].endswith("webhook-secret")
    rendered = yaml.safe_dump(docs)
    for forbidden in ("BEGIN RSA", "BEGIN PRIVATE", "ghs_", "ghp_"):
        assert forbidden not in rendered, forbidden


# --- the internal agent listener (Sprint 13B) ----------------------------
#
# It is the one surface a cluster agent talks to, and the one place the
# Agent CA private key is mounted. Everything below is about keeping it
# unreachable from outside the cluster.

_LISTENER_ON: tuple[str, ...] = (
    "--set",
    "internalAgentApi.enabled=true",
    "--set",
    "internalAgentApi.tlsSecret=drake-agent-tls",
    "--set",
    "internalAgentApi.caSecret=drake-agent-ca",
)


def render_listener(*overrides: str) -> list[dict[str, Any]]:
    return render_prod(*_LISTENER_ON, *overrides)


def test_the_listener_is_off_unless_it_is_switched_on() -> None:
    """Shipping a listener and running one are separate decisions.

    Asserted against the CHART DEFAULTS, which is what this test was always
    about. It used to read `render_prod()` — true only while the production
    values happened to omit the block, and it stopped being a statement
    about the chart the moment production switched the listener on.
    """
    names = {d["metadata"]["name"] for d in render()}
    assert not [n for n in names if "agent-gateway" in n]


def test_the_listener_is_two_processes_with_separate_surfaces() -> None:
    """The bootstrap asymmetry, expressed as two containers.

    An agent enrolling for the first time has no client certificate; every
    call afterwards must present one. One listener cannot be both, so there
    are two — and the enrolment one carries nothing else.
    """
    gateway = next(
        d
        for d in by_kind(render_listener(), "Deployment")
        if d["metadata"]["name"] == "drake-agent-gateway"
    )
    containers = {c["name"]: c for c in gateway["spec"]["template"]["spec"]["containers"]}
    assert sorted(containers) == ["enroll", "ingest"]

    enroll_args = " ".join(containers["enroll"]["args"])
    ingest_args = " ".join(containers["ingest"]["args"])
    assert "--surface=enrollment" in enroll_args
    assert "--surface=ingest" in ingest_args
    assert "--port=8144" in enroll_args
    assert "--port=8143" in ingest_args
    # One image for both: the surfaces differ by argument, not by build.
    assert containers["enroll"]["image"] == containers["ingest"]["image"]
    assert "@sha256:" in containers["enroll"]["image"]
    # One replica: a second copy of the CA key mount buys nothing.
    assert gateway["spec"]["replicas"] == 1


def test_the_listener_is_reachable_only_inside_the_cluster() -> None:
    """No Ingress, no node port, no load balancer, no host networking.

    Publishing the enrolment surface is not a configuration option, so
    there is no value to set wrong.
    """
    docs = render_listener()
    service = next(
        s for s in by_kind(docs, "Service") if s["metadata"]["name"] == "drake-agent-gateway"
    )
    assert service["spec"]["type"] == "ClusterIP"
    assert not [p for p in service["spec"]["ports"] if p.get("nodePort")]
    assert {p["name"] for p in service["spec"]["ports"]} == {"enroll", "ingest"}

    for ingress in by_kind(docs, "Ingress"):
        backends = {
            p["backend"]["service"]["name"]
            for rule in ingress["spec"]["rules"]
            for p in rule["http"]["paths"]
        }
        assert "drake-agent-gateway" not in backends, "the agent surface must not be published"

    pod = next(
        d for d in by_kind(docs, "Deployment") if d["metadata"]["name"] == "drake-agent-gateway"
    )["spec"]["template"]["spec"]
    assert not pod.get("hostNetwork")
    for container in pod["containers"]:
        for port in container.get("ports", []):
            assert "hostPort" not in port


def test_the_public_edge_is_untouched_by_the_listener() -> None:
    """Turning the agent surface on must not move the front door."""
    published = {
        (s["metadata"]["name"], port["nodePort"])
        for s in by_kind(render_listener(), "Service")
        for port in s["spec"]["ports"]
        if port.get("nodePort")
    }
    assert published == {("drake-edge", 30773)}


def test_the_ca_private_key_is_mounted_only_on_the_listener() -> None:
    """The key that signs agent identities must not sit in the process that
    answers the internet."""
    docs = render_listener()
    for doc in by_kind(docs, "Deployment") + by_kind(docs, "Job"):
        for container in doc["spec"]["template"]["spec"]["containers"]:
            env = {e["name"] for e in container.get("env", [])}
            mounts = {m["mountPath"] for m in container.get("volumeMounts", [])}
            on_gateway = doc["metadata"]["name"] == "drake-agent-gateway"
            assert ("DRAKE_AGENT_CA_KEY_FILE" in env) is on_gateway, doc["metadata"]["name"]
            assert ("/etc/drake/agent-ca" in mounts) is on_gateway, doc["metadata"]["name"]


def test_the_listener_pod_is_hardened() -> None:
    gateway = next(
        d
        for d in by_kind(render_listener(), "Deployment")
        if d["metadata"]["name"] == "drake-agent-gateway"
    )
    pod = gateway["spec"]["template"]["spec"]
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert pod["securityContext"]["seccompProfile"]["type"] == "RuntimeDefault"
    assert pod["enableServiceLinks"] is False
    for container in pod["containers"]:
        security = container["securityContext"]
        assert security["allowPrivilegeEscalation"] is False
        assert security["readOnlyRootFilesystem"] is True
        assert security["capabilities"]["drop"] == ["ALL"]
        assert container["resources"]["requests"] and container["resources"]["limits"]


def test_the_listener_admits_one_peer_and_reaches_only_its_datastores() -> None:
    """A network boundary — and only that.

    A vanilla NetworkPolicy selects namespaces and pods by label; a label
    is an assertion by whoever created the pod, so this narrows WHERE a
    connection may come from and never establishes WHO is calling. Identity
    is mutual TLS plus proof-of-possession, and it is asserted elsewhere.
    """
    policies = {p["metadata"]["name"]: p for p in by_kind(render_listener(), "NetworkPolicy")}
    ingress = policies["drake-agent-gateway-ingress"]
    assert ingress["spec"]["policyTypes"] == ["Ingress"]
    peers = ingress["spec"]["ingress"][0]["from"]
    assert peers == [
        {
            "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "drake-agent"}},
            "podSelector": {"matchLabels": {"app.kubernetes.io/name": "drake-cluster-agent"}},
        }
    ]
    assert {p["port"] for p in ingress["spec"]["ingress"][0]["ports"]} == {8143, 8144}

    egress = policies["drake-agent-gateway-egress"]
    assert egress["spec"]["policyTypes"] == ["Egress"]
    reachable = {
        (peer["podSelector"]["matchLabels"].get("app.kubernetes.io/name"), rule["ports"][0]["port"])
        for rule in egress["spec"]["egress"]
        for peer in rule["to"]
        if "podSelector" in peer
    }
    assert ("drake-postgres", 5432) in reachable
    assert ("drake-redis", 6379) in reachable
    # No Kubernetes API egress: the AGENT reads the cluster, not this.
    assert not [
        rule for rule in egress["spec"]["egress"] if any(p["port"] == 6443 for p in rule["ports"])
    ]


def test_a_listener_without_its_secrets_refuses_to_render() -> None:
    """Fail closed: a missing CA reference must not become a listener that
    silently serves without one.

    The secret names are blanked EXPLICITLY. They used to be absent simply
    because the production values named neither, so `enabled=true` alone
    was enough to prove the refusal. Now that production names both, this
    has to unset them or it would assert nothing.
    """
    assert refuses_prod(
        "--set",
        "internalAgentApi.enabled=true",
        "--set",
        "internalAgentApi.tlsSecret=",
        "--set",
        "internalAgentApi.caSecret=",
    )
    assert refuses_prod(
        "--set",
        "internalAgentApi.enabled=true",
        "--set",
        "internalAgentApi.tlsSecret=t",
        "--set",
        "internalAgentApi.caSecret=",
    )


def test_the_listener_still_owns_no_datastore_or_secret() -> None:
    docs = render_listener()
    for forbidden in ("Secret", "PersistentVolumeClaim", "StatefulSet"):
        assert forbidden not in {d["kind"] for d in docs}


def test_the_listener_runs_a_module_the_image_actually_contains() -> None:
    """The bug that rolled a production upgrade back.

    The chart started the listener with `python -m
    scripts.run_internal_agent_api`. The API image copies `apps/api` and
    `packages`; the repository root is not in it. Both containers exited on
    start, readiness never passed, and `helm upgrade --atomic` rolled the
    release back.

    This asserts the command names a module inside the application package
    — the only part of the tree the image contains. It is deliberately NOT
    the whole guarantee: `scripts/api_image_entrypoint_smoke.sh` builds the
    real image and runs the real command in it, because a string assertion
    cannot tell whether a module is present in a filesystem.
    """
    gateway = next(
        d
        for d in by_kind(render_listener(), "Deployment")
        if d["metadata"]["name"] == "drake-agent-gateway"
    )
    for container in gateway["spec"]["template"]["spec"]["containers"]:
        command = container["command"]
        assert command[:2] == ["python", "-m"], container["name"]
        module = command[2]
        assert module == "drake_api.agents.run_internal_listener", container["name"]
        # Anything outside the package is not in the image.
        assert not module.startswith("scripts."), container["name"]

    # And the public API's own command is untouched by moving a listener.
    api = next(
        d for d in by_kind(render_listener(), "Deployment") if d["metadata"]["name"] == "drake-api"
    )
    assert "command" not in api["spec"]["template"]["spec"]["containers"][0]


def test_the_packaged_runner_is_importable_and_keeps_its_contract() -> None:
    """Moving the entrypoint must not quietly change what it accepts."""
    import argparse
    import inspect

    from drake_api.agents import run_internal_listener

    assert inspect.isfunction(run_internal_listener.main)
    source = inspect.getsource(run_internal_listener)
    for option in ("--host", "--port", "--tls-cert", "--tls-key", "--client-ca", "--surface"):
        assert option in source, option
    # The TLS asymmetry the two listeners exist for.
    assert "CERT_NONE" in source and "CERT_REQUIRED" in source

    # Importing it must not start a listener.
    assert isinstance(argparse.ArgumentParser(), argparse.ArgumentParser)


def test_the_legacy_script_delegates_rather_than_duplicating() -> None:
    """Two copies of a TLS bootstrap are two places for it to drift.

    The root script stays — local development, the chart smoke and two test
    suites invoke it by path — but it holds no logic of its own.
    """
    legacy = (CHART / ".." / ".." / "scripts" / "run_internal_agent_api.py").resolve()
    text = legacy.read_text()
    assert "from drake_api.agents.run_internal_listener import main" in text
    for reimplemented in ("argparse", "uvicorn.run", "CERT_REQUIRED", "ssl."):
        assert reimplemented not in text, reimplemented


# --- the production runtime-security contract, per workload ---------------
#
# `Settings.validate_runtime_security()` is a property of the APPLICATION.
# Any workload built from the API image has to satisfy it — including the
# internal listeners, which nothing outside the cluster can reach.
#
# A production upgrade failed exactly here: the listener carried its own CA
# and surface settings and not these, raised on startup, crash-looped both
# containers, and `--atomic` rolled the release back. `helm template`
# succeeded the whole time, because a missing environment variable is not a
# render error.

#: What the validator REQUIRES and the defaults do not satisfy.
#:
#: `trusted_proxy_count` defaults to 0 and the check demands ≥ 1;
#: `allowed_web_origins` defaults to two localhost URLs and the check demands
#: nothing but the canonical origin. Everything else it inspects is already
#: production-safe by default, which is why this list is two entries and not
#: a copy of the API's environment.
_RUNTIME_SECURITY_ENV = ("DRAKE_TRUSTED_PROXY_COUNT", "DRAKE_ALLOWED_WEB_ORIGINS")


def _env_of(docs: list[dict[str, Any]], workload: str, container: str) -> dict[str, Any]:
    deployment = next(d for d in by_kind(docs, "Deployment") if d["metadata"]["name"] == workload)
    spec = next(
        c for c in deployment["spec"]["template"]["spec"]["containers"] if c["name"] == container
    )
    return {e["name"]: e for e in spec.get("env", [])}


@pytest.mark.parametrize(
    ("workload", "container"),
    [
        ("drake-api", "api"),
        ("drake-agent-gateway", "enroll"),
        ("drake-agent-gateway", "ingest"),
    ],
)
def test_every_application_workload_carries_the_runtime_security_env(
    workload: str, container: str
) -> None:
    """Rendered, per container — not "the helper exists"."""
    docs = render_listener()
    env = _env_of(docs, workload, container)
    origin = "https://drake-84-247-180-172.sslip.io:30773"

    for name in _RUNTIME_SECURITY_ENV:
        assert name in env, f"{workload}/{container} is missing {name}"
        # A literal value, resolved by the chart — not a Secret reference
        # that could be absent, and not a value the process must discover.
        assert "value" in env[name], f"{workload}/{container}: {name} is not a plain value"

    assert env["DRAKE_TRUSTED_PROXY_COUNT"]["value"] == "1"
    # Derived from the deployment's own public origin, so the listener and
    # the API cannot disagree about what the canonical origin is.
    assert env["DRAKE_ALLOWED_WEB_ORIGINS"]["value"] == f'["{origin}"]'


def test_the_listener_env_actually_satisfies_the_production_validator() -> None:
    """The check that would have caught it.

    The chart contract above proves the variables are rendered. This proves
    what they are rendered FOR: a `Settings` built from exactly the
    listener's environment passes the validator that killed the containers.

    Nothing is mocked. Bypassing the validator to make a test pass would be
    testing that the test passes.
    """
    from drake_api.settings import Settings

    docs = render_listener()
    origin = "https://drake-84-247-180-172.sslip.io:30773"

    for container in ("enroll", "ingest"):
        env = {
            name: entry["value"]
            for name, entry in _env_of(docs, "drake-agent-gateway", container).items()
            if "value" in entry
        }
        settings = Settings(
            env="production",
            public_origin=env["DRAKE_PUBLIC_ORIGIN"],
            auth_mode=env["DRAKE_AUTH_MODE"],
            trusted_proxy_count=int(env["DRAKE_TRUSTED_PROXY_COUNT"]),
            allowed_web_origins=json.loads(env["DRAKE_ALLOWED_WEB_ORIGINS"]),
            internal_agent_api_enabled=env["DRAKE_INTERNAL_AGENT_API_ENABLED"] == "true",
            agent_ca_cert_file=env["DRAKE_AGENT_CA_CERT_FILE"],
            agent_ca_key_file=env["DRAKE_AGENT_CA_KEY_FILE"],
        )
        # Raises if the listener would refuse to start.
        settings.validate_runtime_security()
        assert str(settings.resolved_public_origin()) == origin, container


@pytest.mark.parametrize("dropped", _RUNTIME_SECURITY_ENV)
def test_dropping_either_variable_reproduces_the_production_failure(dropped: str) -> None:
    """Proof that the test above is load-bearing.

    Remove one of them and the validator refuses, with the same class of
    error the production containers died on.
    """
    from drake_api.settings import Settings

    origin = "https://drake-84-247-180-172.sslip.io:30773"
    complete = {
        "env": "production",
        "public_origin": origin,
        "auth_mode": "local",
        "trusted_proxy_count": 1,
        "allowed_web_origins": [origin],
        "internal_agent_api_enabled": True,
        "agent_ca_cert_file": "/etc/drake/agent-ca/ca.crt",
        "agent_ca_key_file": "/etc/drake/agent-ca/ca.key",
    }
    # Dropping the variable means falling back to the field's default, which
    # is what an unset environment actually produces.
    field = {
        "DRAKE_TRUSTED_PROXY_COUNT": "trusted_proxy_count",
        "DRAKE_ALLOWED_WEB_ORIGINS": "allowed_web_origins",
    }[dropped]
    del complete[field]

    with pytest.raises(RuntimeError):
        Settings(**complete).validate_runtime_security()


# --- the listener's runtime file access (Sprint 13B-B5) ------------------
#
# The third production rollout died on `PermissionError: /etc/drake/
# agent-ca/ca.crt`. Secret volume files are owned by root; the chart mounted
# them 0400 and the process runs as uid 65532, so it could not read the CA
# it had just been handed.
#
# 0444 would have "fixed" it by putting the Agent CA PRIVATE KEY — in the
# same directory — within reach of any uid in the pod. A root initContainer
# running chmod would have traded a permission problem for a privileged one.
# An fsGroup makes 0440 readable by the process and by nothing else.

#: Kubernetes renders volume modes as decimal. 0440 == 288.
_MODE_0440 = 288


def _gateway(docs: list[dict[str, Any]]) -> dict[str, Any]:
    return next(
        d for d in by_kind(docs, "Deployment") if d["metadata"]["name"] == "drake-agent-gateway"
    )


def test_the_listener_can_read_its_own_secrets_without_being_root() -> None:
    pod = _gateway(render_listener())["spec"]["template"]["spec"]
    security = pod["securityContext"]
    assert security["runAsNonRoot"] is True
    assert security["runAsUser"] == 65532
    assert security["runAsGroup"] == 65532
    # Without this the process cannot read a root-owned Secret file at any
    # mode short of world-readable.
    assert security["fsGroup"] == 65532


def test_no_private_key_is_world_readable() -> None:
    """Both mounts hold a private key: the listener's server key, and the
    Agent CA signing key."""
    pod = _gateway(render_listener())["spec"]["template"]["spec"]
    modes = {v["name"]: v["secret"]["defaultMode"] for v in pod["volumes"] if "secret" in v}
    assert set(modes) == {"agent-tls", "agent-ca"}
    for name, mode in modes.items():
        assert mode == _MODE_0440, f"{name} is {oct(mode)}, expected 0440"
        # The bits that would matter if this were ever loosened.
        assert mode & 0o007 == 0, f"{name} is world-accessible"
        assert mode & 0o111 == 0, f"{name} is executable"


def test_the_container_is_still_hardened_after_the_permission_fix() -> None:
    """A fix for a file mode must not become a fix for the security context."""
    gateway = _gateway(render_listener())
    pod = gateway["spec"]["template"]["spec"]
    assert not pod.get("hostNetwork") and not pod.get("hostPID") and not pod.get("hostIPC")
    assert not [v for v in pod["volumes"] if "hostPath" in v]
    for container in pod["containers"]:
        security = container["securityContext"]
        assert security["allowPrivilegeEscalation"] is False
        assert security["readOnlyRootFilesystem"] is True
        assert security["capabilities"]["drop"] == ["ALL"]
        assert not security.get("privileged")
        # Read-only mounts: the listener reads its material and writes none.
        for mount in container["volumeMounts"]:
            if mount["name"] in ("agent-tls", "agent-ca"):
                assert mount.get("readOnly") is True, mount["name"]


def test_both_listeners_mount_the_ca_because_both_of_them_sign() -> None:
    """Not an oversight, and not something to narrow.

    The enrolment surface signs an agent's first certificate; the ingest
    surface signs its renewals. Renewal has to stay behind mutual TLS, so it
    cannot move to the listener that asks for no certificate — mounting the
    key on only one of them would break renewal rather than tighten
    anything.
    """
    from drake_api.agents import router_internal

    source = Path(router_internal.__file__).read_text()
    enrolment = source.index("@enrollment_router.post")
    renewal = source.index('@certificate_router.post("/certificates/renew")')
    activation = source.index('@certificate_router.post("/certificates/activate")')
    # Each of these regions calls the CA.
    assert "_ca(request).sign" in source[enrolment:renewal]
    assert "_ca(request)" in source[renewal:activation]

    pod = _gateway(render_listener())["spec"]["template"]["spec"]
    for container in pod["containers"]:
        mounts = {m["name"] for m in container["volumeMounts"]}
        assert "agent-ca" in mounts, container["name"]
