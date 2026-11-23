"""The relay's routing and reply handling, without Redis or a database.

ADR-0037 §3: a statement that names a cluster kept in its own stores is read
through that cluster's agent; a statement that names only central clusters,
or none, reads the central store; a statement over both reads both and
returns the union. The relayed answer passes the same checks as a direct one.
"""

import asyncio
import base64
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from drake_api.observability.store import (
    ClickHouseStore,
    StoreContractError,
    StoreUnavailableError,
    named_clusters,
)
from drake_api.relay import RelayedCluster, RelayReply, RelayUnavailableError
from drake_api.relay.broker import decode_reply
from drake_api.settings import ObservabilityStoreConnector
from drake_api.telemetry.provider import (
    PrometheusAdapter,
    ProviderContractError,
    ProviderUnavailableError,
    RelayConnector,
)
from drake_api.testing import make_settings


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _settings(tmp_path: Path) -> Any:
    password_file = tmp_path / "reader-password"
    password_file.write_text("local-test-reader\n", encoding="utf-8")
    return make_settings(
        env="test",
        observability_logs_enabled=True,
        observability_store=ObservabilityStoreConnector(
            url="http://127.0.0.1:58123",
            username="drake_reader",
            password_file=str(password_file),
        ),
    )


def _document(*rows: dict[str, Any]) -> bytes:
    return json.dumps({"data": list(rows)}).encode()


class FakeRelay:
    """Answers relayed reads from a table; records what it was asked."""

    def __init__(self, kept: dict[str, bool], answers: dict[str, RelayReply | Exception]):
        self.kept = kept
        self.answers = answers
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def clusters(self) -> dict[str, RelayedCluster]:
        import uuid

        return {
            ref: RelayedCluster(id=uuid.uuid4(), cluster_ref=ref, online=online)
            for ref, online in self.kept.items()
        }

    async def call(
        self, cluster_ref: str, kind: str, request: dict[str, Any], *, timeout_seconds: float
    ) -> RelayReply:
        self.calls.append((cluster_ref, kind, request))
        answer = self.answers[cluster_ref]
        if isinstance(answer, Exception):
            raise answer
        return answer


def _central(rows: list[dict[str, Any]], seen: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=_document(*rows))

    return httpx.MockTransport(handler)


# --- naming -------------------------------------------------------------------


def test_named_clusters_reads_the_single_and_the_array_parameter() -> None:
    assert named_clusters({"cluster": "a"}) == ["a"]
    assert named_clusters({"clusters": "['a','b-2']"}) == ["a", "b-2"]
    assert named_clusters({"cluster": "a", "clusters": "['a','c']"}) == ["a", "c"]
    assert named_clusters({"namespace": "x"}) == []
    assert named_clusters({"clusters": "[]"}) == []


# --- ClickHouse routing -------------------------------------------------------


def test_a_central_cluster_reads_the_central_store_and_never_the_relay(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    relay = FakeRelay({"kept": True}, {})
    store = ClickHouseStore(
        _settings(tmp_path),
        transport=_central([{"n": 1}], seen),
        relay=relay,  # type: ignore[arg-type]
    )
    result = _run(
        store.query(
            "SELECT 1", {"cluster": "central-1"}, max_rows=10, timeout_seconds=5, correlation_id="t"
        )
    )
    assert result.rows == [{"n": 1}]
    assert len(seen) == 1
    assert relay.calls == []


def test_a_kept_cluster_is_read_through_its_agent_with_the_same_request(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    relay = FakeRelay(
        {"kept": True},
        {"kept": RelayReply(200, "application/json", _document({"n": 2}))},
    )
    store = ClickHouseStore(
        _settings(tmp_path),
        transport=_central([], seen),
        relay=relay,  # type: ignore[arg-type]
    )
    result = _run(
        store.query(
            "SELECT {cluster:String}",
            {"cluster": "kept"},
            max_rows=10,
            timeout_seconds=5,
            correlation_id="t",
        )
    )
    assert result.rows == [{"n": 2}]
    assert seen == []
    [(cluster, kind, request)] = relay.calls
    assert (cluster, kind) == ("kept", "clickhouse.query")
    assert request["statement"] == "SELECT {cluster:String}"
    assert request["params"]["param_cluster"] == "kept"
    assert request["params"]["max_result_rows"] == "10"
    assert request["params"]["default_format"] == "JSON"
    # The reader's credential is the agent's, never sent by Tracesis.
    assert "password" not in json.dumps(request)
    assert "local-test-reader" not in json.dumps(request)


def test_a_statement_over_both_kinds_reads_both_and_returns_the_union(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    relay = FakeRelay(
        {"kept": True},
        {"kept": RelayReply(200, "application/json", _document({"k": "kept|a"}))},
    )
    store = ClickHouseStore(
        _settings(tmp_path),
        transport=_central([{"k": "central|a"}], seen),
        relay=relay,  # type: ignore[arg-type]
    )
    result = _run(
        store.query(
            "SELECT 1",
            {"clusters": "['central','kept']"},
            max_rows=10,
            timeout_seconds=5,
            correlation_id="t",
        )
    )
    assert sorted(row["k"] for row in result.rows) == ["central|a", "kept|a"]
    assert len(seen) == 1
    assert len(relay.calls) == 1


def test_the_union_still_honours_the_row_bound(tmp_path: Path) -> None:
    relay = FakeRelay(
        {"kept": True},
        {"kept": RelayReply(200, "application/json", _document({"n": 1}, {"n": 2}))},
    )
    store = ClickHouseStore(
        _settings(tmp_path),
        transport=_central([{"n": 3}, {"n": 4}], []),
        relay=relay,  # type: ignore[arg-type]
    )
    with pytest.raises(StoreContractError, match="store_too_many_rows"):
        _run(
            store.query(
                "SELECT 1",
                {"clusters": "['c','kept']"},
                max_rows=3,
                timeout_seconds=5,
                correlation_id="t",
            )
        )


def test_a_statement_with_no_cluster_reads_the_central_store(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    relay = FakeRelay({"kept": True}, {})
    store = ClickHouseStore(
        _settings(tmp_path),
        transport=_central([{"n": 1}], seen),
        relay=relay,  # type: ignore[arg-type]
    )
    _run(store.query("SELECT 1", {}, max_rows=1, timeout_seconds=5, correlation_id="t"))
    assert len(seen) == 1 and relay.calls == []


@pytest.mark.parametrize(
    ("reply", "error", "code"),
    [
        (RelayReply(500, "", b""), StoreUnavailableError, "store_upstream_error"),
        (RelayReply(401, "", b""), StoreUnavailableError, "store_auth_refused"),
        (
            RelayReply(400, "", b"Code: 62 ... the statement"),
            StoreContractError,
            "store_rejected_query",
        ),
        (RelayReply(302, "", b""), StoreContractError, "store_redirect_refused"),
        (
            RelayReply(200, "application/json", b"not json"),
            StoreContractError,
            "store_malformed_response",
        ),
        (
            RelayUnavailableError("store_agent_offline"),
            StoreUnavailableError,
            "store_agent_offline",
        ),
        (RelayUnavailableError("store_timeout"), StoreUnavailableError, "store_timeout"),
        (
            RelayUnavailableError("store_response_too_large"),
            StoreContractError,
            "store_response_too_large",
        ),
    ],
)
def test_a_relayed_answer_passes_the_same_checks_as_a_direct_one(
    tmp_path: Path, reply: Any, error: type[Exception], code: str
) -> None:
    relay = FakeRelay({"kept": True}, {"kept": reply})
    store = ClickHouseStore(_settings(tmp_path), transport=_central([], []), relay=relay)  # type: ignore[arg-type]
    with pytest.raises(error, match=code):
        _run(
            store.query(
                "SELECT 1", {"cluster": "kept"}, max_rows=1, timeout_seconds=5, correlation_id="t"
            )
        )


# --- replies ------------------------------------------------------------------


def test_decode_reply_maps_agent_failures_to_bounded_store_codes() -> None:
    with pytest.raises(RelayUnavailableError, match="store_unreachable"):
        decode_reply(json.dumps({"error": "unreachable"}))
    with pytest.raises(RelayUnavailableError, match="store_relay_refused"):
        decode_reply(json.dumps({"error": "kind_refused"}))
    # A code the agent invents later is never echoed.
    with pytest.raises(RelayUnavailableError, match="store_unreachable"):
        decode_reply(json.dumps({"error": "something new and chatty"}))
    with pytest.raises(RelayUnavailableError, match="store_unreachable"):
        decode_reply(b"{not json")
    reply = decode_reply(
        json.dumps(
            {
                "status_code": 200,
                "content_type": "application/json",
                "body_b64": base64.b64encode(b"{}").decode(),
            }
        )
    )
    assert reply == RelayReply(200, "application/json", b"{}")


# --- Prometheus ---------------------------------------------------------------


def _matrix() -> bytes:
    return json.dumps(
        {
            "status": "success",
            "data": {"resultType": "matrix", "result": [{"metric": {}, "values": [[1, "1"]]}]},
        }
    ).encode()


def test_an_agent_config_ref_resolves_to_a_relay_connector_only_with_a_relay() -> None:
    settings = make_settings(env="test")
    assert PrometheusAdapter(settings).resolve_connector("agent:kept") is None
    relayed = PrometheusAdapter(settings, relay=FakeRelay({}, {}))  # type: ignore[arg-type]
    assert relayed.resolve_connector("agent:kept") == RelayConnector("kept")
    assert relayed.resolve_connector("agent:") is None


def test_prometheus_reads_through_the_agent_and_validates_the_matrix() -> None:
    relay = FakeRelay({"kept": True}, {"kept": RelayReply(200, "application/json", _matrix())})
    adapter = PrometheusAdapter(make_settings(env="test"), relay=relay)  # type: ignore[arg-type]
    result = _run(
        adapter.query_range(
            RelayConnector("kept"),
            "up",
            start=1,
            end=2,
            step_seconds=60,
            timeout_seconds=5,
            correlation_id="t",
        )
    )
    assert result.result[0]["values"] == [[1, "1"]]
    [(cluster, kind, form)] = relay.calls
    assert (cluster, kind) == ("kept", "prometheus.query_range")
    assert form == {"query": "up", "start": "1", "end": "2", "step": "60"}


@pytest.mark.parametrize(
    ("reply", "error", "code"),
    [
        (
            RelayReply(200, "text/html", b"<html>"),
            ProviderContractError,
            "provider_unexpected_content_type",
        ),
        (
            RelayReply(422, "application/json", b"{}"),
            ProviderContractError,
            "provider_rejected_query",
        ),
        (
            RelayUnavailableError("store_agent_offline"),
            ProviderUnavailableError,
            "provider_agent_offline",
        ),
    ],
)
def test_prometheus_relay_failures_are_bounded_provider_codes(
    reply: Any, error: type[Exception], code: str
) -> None:
    relay = FakeRelay({"kept": True}, {"kept": reply})
    adapter = PrometheusAdapter(make_settings(env="test"), relay=relay)  # type: ignore[arg-type]
    with pytest.raises(error, match=code):
        _run(
            adapter.query_range(
                RelayConnector("kept"),
                "up",
                start=1,
                end=2,
                step_seconds=60,
                timeout_seconds=5,
                correlation_id="t",
            )
        )
