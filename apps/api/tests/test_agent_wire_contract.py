"""The Go agent's wire contract, checked against the FastAPI request model.

This file exists because of a defect it would have caught. Sprint 8 taught
the cluster agent to report container name + image reference — the only way
Drake can say which build is running — and changed nothing on the API side.
The Go tests asserted the Go struct, the Python tests built their own
payload dicts, and no test ever carried a real agent-shaped payload across
the language boundary. Every snapshot page 422'd in a real cluster while
both suites stayed green.

So the fixture in `packages/contracts/fixtures/agent/snapshot-page.json` is
the single definition of that wire, and three consumers check it:

- the contracts package validates it against `agent-inventory.schema.json`
- `apps/cluster-agent/.../normalize_wire_test.go` proves the agent PRODUCES
  this shape
- this file proves the API ACCEPTS it

A change on any side that the others do not follow fails here.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from drake_api.agents.router_ingest import ResourceRecord, SnapshotPageMessage
from pydantic import ValidationError

FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "contracts"
    / "fixtures"
    / "agent"
    / "snapshot-page.json"
)


def fixture() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text())


def test_the_shared_snapshot_page_fixture_is_accepted_verbatim() -> None:
    """The exact bytes the contracts package validates, through the API model."""
    message = SnapshotPageMessage.model_validate(fixture())
    assert message.kind == "snapshot_page"
    assert len(message.resources) >= 2


def test_the_fixture_actually_exercises_the_nested_container_shape() -> None:
    """A fixture that omits the hard case proves nothing.

    This is the assertion that would have failed in Sprint 8: without it,
    the fixture could quietly go back to scalars-only and the contract test
    above would keep passing against a wire nobody uses.
    """
    resources = fixture()["resources"]
    workload = next(item for item in resources if item["kind"] == "Deployment")
    pod = next(item for item in resources if item["kind"] == "Pod")

    containers = workload["spec_summary"]["containers"]
    assert isinstance(containers, list) and containers
    assert {"name", "image"} <= set(containers[0])

    images = pod["status_summary"]["container_images"]
    assert isinstance(images, list) and images
    # The resolved digest: what the node actually pulled, as opposed to what
    # the spec asked for.
    assert "image_id" in images[0]


def test_the_api_and_the_agent_agree_on_every_bound() -> None:
    """Bounds are duplicated across a language boundary, so they are checked.

    Each value below is the agent's own limit in `normalize.go`. A change on
    either side without the other produces a cluster that silently stops
    reporting, which is the failure mode this whole file is about.
    """
    normalize = (
        Path(__file__).resolve().parents[3]
        / "apps"
        / "cluster-agent"
        / "internal"
        / "inventory"
        / "normalize.go"
    ).read_text()
    assert "maxContainers = 8" in normalize
    assert "maxImageLen   = 512" in normalize

    record = {
        "api_group": "apps",
        "api_version": "v1",
        "kind": "Deployment",
        "namespace": "ns",
        "name": "n",
        "uid": "11111111-2222-3333-4444-555555555555",
        "resource_version": "1",
        "status_summary": {},
        "observed_at": "2026-08-09T00:00:00Z",
    }
    # Exactly at the agent's ceiling: accepted.
    ResourceRecord.model_validate(
        {
            **record,
            "spec_summary": {"containers": [{"name": f"c{index}"} for index in range(8)]},
        }
    )
    # One past it: refused.
    with pytest.raises(ValidationError):
        ResourceRecord.model_validate(
            {
                **record,
                "spec_summary": {"containers": [{"name": f"c{index}"} for index in range(9)]},
            }
        )
    with pytest.raises(ValidationError):
        ResourceRecord.model_validate(
            {**record, "spec_summary": {"containers": [{"image": "x" * 513}]}}
        )


@pytest.mark.parametrize(
    ("label", "summary"),
    [
        # The point of widening the union narrowly: a container map is the
        # ONE nested shape, and it carries three known keys. None of these
        # is a container reference, and none of them gets in.
        ("env smuggled into a container map", {"containers": [{"name": "a", "env": "S=1"}]}),
        ("one level deeper", {"containers": [{"name": {"deep": "x"}}]}),
        ("a list of scalars", {"containers": ["api", "worker"]}),
        ("a list of lists", {"containers": [[{"name": "a"}]]}),
        ("a bare map instead of a list", {"containers": {"name": "a"}}),
        ("an arbitrary nested object", {"spec": {"volumes": [{"secret": "s"}]}}),
    ],
)
def test_nothing_beyond_the_container_shape_is_accepted(
    label: str, summary: dict[str, Any]
) -> None:
    """Widening a union is where validation quietly becomes optional."""
    with pytest.raises(ValidationError):
        (
            ResourceRecord.model_validate(
                {
                    "api_group": "apps",
                    "api_version": "v1",
                    "kind": "Deployment",
                    "namespace": "ns",
                    "name": "n",
                    "uid": "11111111-2222-3333-4444-555555555555",
                    "resource_version": "1",
                    "spec_summary": summary,
                    "status_summary": {},
                    "observed_at": "2026-08-09T00:00:00Z",
                }
            ),
            label,
        )


def test_an_unknown_top_level_field_is_still_refused() -> None:
    """`extra='forbid'` survives the change."""
    with pytest.raises(ValidationError):
        SnapshotPageMessage.model_validate({**fixture(), "surprise": 1})


def test_a_missing_required_field_is_refused_rather_than_defaulted() -> None:
    payload = fixture()
    del payload["snapshot_uid"]
    with pytest.raises(ValidationError) as refusal:
        SnapshotPageMessage.model_validate(payload)
    assert any(error["loc"] == ("snapshot_uid",) for error in refusal.value.errors())


def test_no_secret_shaped_content_rides_in_on_the_new_shape() -> None:
    """The credential guard runs over summaries too, not just labels."""
    from drake_api.agents.router_ingest import _validate_resource

    record = ResourceRecord.model_validate(
        {
            "api_group": "apps",
            "api_version": "v1",
            "kind": "Deployment",
            "namespace": "ns",
            "name": "n",
            "uid": "11111111-2222-3333-4444-555555555555",
            "resource_version": "1",
            "spec_summary": {
                "containers": [{"name": "api", "image": "-----BEGIN RSA PRIVATE KEY-----"}]
            },
            "status_summary": {},
            "observed_at": "2026-08-09T00:00:00Z",
        }
    )
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        _validate_resource(record)
