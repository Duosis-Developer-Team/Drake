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


def test_the_first_deployment_publishes_no_public_route() -> None:
    """Prove the application runs before attaching a name and a certificate."""
    docs = render_prod()
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
    docs = render_prod()
    assert sorted(d["metadata"]["name"] for d in by_kind(docs, "Deployment")) == [
        "drake-api",
        "drake-web",
    ]
    assert [d["metadata"]["name"] for d in by_kind(docs, "Job")] == ["drake-migrate"]
    assert sorted(d["metadata"]["name"] for d in by_kind(docs, "Service")) == [
        "drake-api",
        "drake-web",
    ]


def test_every_workload_can_pull_private_images() -> None:
    docs = render_prod()
    workloads = by_kind(docs, "Deployment") + by_kind(docs, "Job")
    assert len(workloads) == 3, "api, web, migration"
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
    for doc in render_prod():
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
        ("ingress.enabled=true", "internal mode must not also publish a route"),
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
