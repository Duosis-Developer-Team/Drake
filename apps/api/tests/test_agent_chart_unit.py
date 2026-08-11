"""The agent chart's egress names destinations that keep meaning what they said.

The production values used to carry the gateway's Service ClusterIP as a
/32. Revision 13 deleted and recreated that Service, the address moved, and
the committed rule was left aimed at nothing — silently, because no agent
was installed yet to fail against it. An egress rule that stops matching
does not announce itself; it looks like DNS, or like a listener that is
down. These render the real chart, so what is asserted is what would
install.
"""

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

CHART = Path(__file__).resolve().parents[3] / "deploy" / "agent"
PROD_VALUES = CHART / "values-drake-prod.yaml"

HELM = shutil.which("helm")

pytestmark = pytest.mark.skipif(HELM is None, reason="helm is not installed")

# The production values leave `clusterId` empty on purpose: Drake generates
# it when the cluster row is created, so it cannot be committed before
# enrolment. Everything else here is the committed file.
_CLUSTER_ID = "--set=clusterId=00000000-0000-0000-0000-000000000000"

# A render that names the gateway off-cluster, as the k3d smokes do, where
# the listener runs on the host and no pod selector could reach it.
_OFF_CLUSTER: tuple[str, ...] = (
    "--set=clusterId=00000000-0000-0000-0000-000000000000",
    "--set=clusterName=policy-check",
    "--set=apiBaseUrl=https://drake-internal.example.test",
    "--set=image.digest=sha256:" + "0" * 64,
    "--set=serverCA.existingSecret=drake-agent-server-ca",
    "--set=enrollmentToken.existingSecret=drake-agent-enrollment",
    "--set=networkPolicy.kubernetesApiCIDR=198.51.100.0/24",
)


def _render(*args: str, values: Path | None = None) -> subprocess.CompletedProcess[str]:
    argv = [str(HELM), "template", "drake-agent", str(CHART), "--namespace", "drake-agent"]
    if values is not None:
        argv += ["-f", str(values)]
    return subprocess.run(  # noqa: S603 - resolved binary, fixed argv, no shell
        [*argv, *args], capture_output=True, text=True, check=False
    )


def render_prod(*overrides: str) -> list[dict[str, Any]]:
    result = _render(_CLUSTER_ID, *overrides, values=PROD_VALUES)
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def refuses(*args: str, values: Path | None = None) -> bool:
    return _render(*args, values=values).returncode != 0


def policy(docs: list[dict[str, Any]]) -> dict[str, Any]:
    policies = [d for d in docs if d.get("kind") == "NetworkPolicy"]
    assert len(policies) == 1, "the agent has exactly one policy, or the gate is meaningless"
    return policies[0]["spec"]


def gateway_rule(spec: dict[str, Any]) -> dict[str, Any]:
    """The egress rule for the Drake gateway: the one that is not DNS or the apiserver."""
    rules = [
        rule
        for rule in spec["egress"]
        if not any(port["port"] == 53 for port in rule.get("ports", []))
        and not any(port["port"] == 6443 for port in rule.get("ports", []))
    ]
    assert len(rules) == 1, rules
    return rules[0]


def test_production_names_the_gateway_by_labels_not_by_address() -> None:
    rule = gateway_rule(policy(render_prod()))
    assert len(rule["to"]) == 1
    target = rule["to"][0]
    assert "ipBlock" not in target, (
        "a ClusterIP is not an identity: recreating the Service moves it and the rule "
        "goes on rendering, installing and matching nothing"
    )
    assert target["namespaceSelector"]["matchLabels"] == {
        "kubernetes.io/metadata.name": "drake-prod"
    }
    assert target["podSelector"]["matchLabels"] == {
        "app.kubernetes.io/name": "drake",
        "app.kubernetes.io/component": "agent-gateway",
    }


def test_production_allows_both_listener_ports() -> None:
    """Enrolment and ingest are different ports because they are different listeners.

    Enroll is 8144 and accepts no client certificate; ingest is 8143 and
    requires one. A policy carrying only 8143 blocks the one call an agent
    makes before it has an identity, so it never gets one.
    """
    rule = gateway_rule(policy(render_prod()))
    assert sorted(p["port"] for p in rule["ports"]) == [8143, 8144]
    assert {p["protocol"] for p in rule["ports"]} == {"TCP"}


def test_production_can_resolve_through_the_node_local_dns_cache() -> None:
    """This cluster's pods resolve against a link-local address, not a pod IP.

    `nodelocaldns` answers on 169.254.25.10, which belongs to the node. The
    kube-dns pod selector names nothing the agent actually sends to, so
    without this the agent resolves nothing and every destination below is
    academic.
    """
    dns_targets = [
        target
        for rule in policy(render_prod())["egress"]
        if any(port["port"] == 53 for port in rule.get("ports", []))
        for target in rule["to"]
    ]
    assert {"ipBlock": {"cidr": "169.254.25.10/32"}} in dns_targets
    assert any("podSelector" in target for target in dns_targets), "kube-dns pods stay allowed"


def test_the_agent_still_accepts_nothing_inbound() -> None:
    spec = policy(render_prod())
    assert spec["ingress"] == []
    assert sorted(spec["policyTypes"]) == ["Egress", "Ingress"]


def test_the_apiserver_stays_an_explicit_address() -> None:
    """The apiserver is reached at a node address, which no in-cluster selector names."""
    rules = [
        rule
        for rule in policy(render_prod())["egress"]
        if any(port["port"] == 6443 for port in rule.get("ports", []))
    ]
    assert len(rules) == 1
    assert rules[0]["to"] == [{"ipBlock": {"cidr": "84.247.180.172/32"}}]
    assert [p["port"] for p in rules[0]["ports"]] == [6443], "one port, listed once"


def test_production_can_pull_its_private_image() -> None:
    """A Secret is namespaced, so `drake-ghcr` in drake-prod does not reach here.

    Without this the pod stops at ImagePullBackOff behind a kubelet event
    that reads "failed to fetch anonymous token" — a 401 about a package
    that exists and is simply private. The install itself reports only that
    the rollout did not finish.
    """
    deployments = [d for d in render_prod() if d.get("kind") == "Deployment"]
    assert len(deployments) == 1
    pod = deployments[0]["spec"]["template"]["spec"]
    assert pod["imagePullSecrets"] == [{"name": "drake-ghcr"}]


def test_a_chart_with_no_pull_secret_omits_the_key_entirely() -> None:
    """`imagePullSecrets: []` is a valid field with a meaningless value.

    It would hide the difference between "no credential is needed" — which
    is true of the smoke clusters, whose images are imported locally — and
    "somebody forgot to name one".
    """
    result = _render(*_OFF_CLUSTER, "--set=networkPolicy.apiEndpointCIDR=203.0.113.10/32")
    assert result.returncode == 0, result.stderr
    docs = [doc for doc in yaml.safe_load_all(result.stdout) if doc]
    pod = next(d for d in docs if d["kind"] == "Deployment")["spec"]["template"]["spec"]
    assert "imagePullSecrets" not in pod


def test_an_off_cluster_listener_can_still_be_named_by_cidr() -> None:
    """The k3d smokes run the listener on the host, where labels cannot reach it."""
    result = _render(*_OFF_CLUSTER, "--set=networkPolicy.apiEndpointCIDR=203.0.113.10/32")
    assert result.returncode == 0, result.stderr
    docs = [doc for doc in yaml.safe_load_all(result.stdout) if doc]
    assert gateway_rule(policy(docs))["to"] == [{"ipBlock": {"cidr": "203.0.113.10/32"}}]


def test_naming_the_gateway_twice_is_refused() -> None:
    """Two descriptions of one destination cannot both be the one being enforced."""
    assert refuses(
        _CLUSTER_ID,
        "--set=networkPolicy.apiEndpointCIDR=203.0.113.10/32",
        values=PROD_VALUES,
    )


def test_naming_the_gateway_not_at_all_is_refused() -> None:
    assert refuses(*_OFF_CLUSTER)


def test_a_namespace_without_a_pod_selector_is_refused() -> None:
    """Otherwise the rule quietly means "every pod in drake-prod"."""
    assert refuses(*_OFF_CLUSTER, "--set=networkPolicy.gatewayNamespace=drake-prod")
