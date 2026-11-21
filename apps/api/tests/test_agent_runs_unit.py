"""Run views (Agent Visibility, phase 3): which spans are an agent's, and
what a caller is shown of a run."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from drake_api.ai_agents import runs

NOW = datetime(2026, 11, 21, 18, 0, tzinfo=UTC)
TRACE = "ab" * 16

STABLE = {
    "id": "a1",
    "display_name": "triager",
    "confidence": "stable",
    "identity_key": "id:agt_1",
    "agent_external_id": "agt_1",
    "agent_name": "triager",
    "service_name": "support-bot",
    "cluster_ref": "c1",
    "namespace": "ns",
    "workload": "wl",
}
NAMED = {**STABLE, "confidence": "named", "identity_key": "name:support-bot:triager"}
SERVICE = {
    **STABLE,
    "confidence": "service",
    "agent_external_id": None,
    "agent_name": None,
    "identity_key": "service:support-bot:c1/ns/wl",
}


def test_each_confidence_filters_by_what_identified_it() -> None:
    sql, params = runs.agent_filter(STABLE)
    assert "{agent_id:String}" in sql and params == {"agent_id": "agt_1"}
    sql, params = runs.agent_filter(NAMED)
    assert params == {"svc": "support-bot", "agent_name": "triager"}
    sql, params = runs.agent_filter(SERVICE)
    assert params == {"svc": "support-bot", "cluster": "c1", "ns": "ns", "wk": "wl"}
    # A service-level agent excludes the spans a named agent in it claims.
    assert "'stategraph'" in sql and "match(" in sql


def test_statements_bind_values_and_refuse_bad_input() -> None:
    statement = runs.build_runs(
        database="otel", agent=STABLE, window=timedelta(hours=24), now=NOW, failed_only=True
    )
    assert "agt_1" not in statement.sql
    assert "HAVING errors > 0" in statement.sql
    assert statement.parameters["limit"] == "50"
    with pytest.raises(runs.RunQueryError):
        runs.build_runs(database="otel", agent=STABLE, window=timedelta(hours=1), now=NOW, limit=0)
    with pytest.raises(runs.RunQueryError):
        runs.build_run(database="otel", trace_id="nope", now=NOW)
    with pytest.raises(runs.RunQueryError):
        runs.build_run(database="otel", trace_id="0" * 32, now=NOW)
    assert (
        runs.build_run(database="otel", trace_id=TRACE.upper(), now=NOW).parameters["trace_id"]
        == TRACE
    )


def test_a_run_counts_model_tokens_and_falls_back_to_the_agent_span() -> None:
    row = {
        "trace_id": TRACE,
        "started": "2026-11-21 17:00:00.000000000",
        "span_ns": "900000000",
        "agent_ns": "0",
        "model_calls": "2",
        "in_inf": "100",
        "out_inf": "10",
        "in_agent": "999",
        "out_agent": "99",
        "models": ["gpt-5", ""],
        "tools": [],
    }
    (run,) = runs.normalize_runs([row])
    assert (run["input_tokens"], run["output_tokens"], run["duration_ms"]) == (100, 10, 900.0)
    assert run["started_at"] == "2026-11-21T17:00:00.000000000Z"
    assert run["models"] == ["gpt-5"]
    (only_agent,) = runs.normalize_runs([{**row, "model_calls": "0", "agent_ns": "500000000"}])
    assert (only_agent["input_tokens"], only_agent["duration_ms"]) == (999, 500.0)


def span(span_id: str, parent: str, start_ms: int, dur_ms: int, **over: Any) -> dict[str, Any]:
    base = {
        "start_ns": str((1_000_000 + start_ms) * 1_000_000),
        "span_id": span_id,
        "parent_span_id": parent,
        "name": span_id,
        "kind": "Internal",
        "duration_ns": str(dur_ms * 1_000_000),
        "status": "Unset",
        "svc": "support-bot",
        "cluster": "c1",
        "ns": "ns",
        "wk": "wl",
        "agent_id": "",
        "agent_name": "",
        "ingest": "",
        "ebpf": 0,
        "genai": 0,
        "op": "",
        "inference": 0,
        "provider": "",
        "model": "",
        "input_tokens": "0",
        "output_tokens": "0",
        "cache_read_tokens": "0",
        "reasoning_tokens": "0",
        "tool": "",
        "tool_type": "",
        "mcp_method": "",
        "server": "",
        "error_type": "",
        "http_status": "",
        "http_route": "",
    }
    return {**base, **over}


ROWS = [
    span("root", "", 0, 900, genai=1, op="invoke_agent", agent_id="agt_1", agent_name="triager"),
    span(
        "chat",
        "root",
        10,
        500,
        genai=1,
        op="chat",
        inference=1,
        agent_id="agt_1",
        provider="anthropic",
        model="claude-sonnet-5",
        input_tokens="1200",
        output_tokens="80",
    ),
    span("billing", "root", 520, 300, svc="billing"),
    span(
        "tool",
        "billing",
        530,
        200,
        genai=1,
        op="execute_tool",
        agent_id="agt_1",
        tool="get_invoice",
        status="Error",
        error_type="timeout",
    ),
    span(
        "helper",
        "root",
        840,
        50,
        svc="helper-bot",
        genai=1,
        op="invoke_agent",
        agent_id="agt_2",
        agent_name="helper",
    ),
]


def test_a_run_shows_this_agent_its_service_and_visible_agents_only() -> None:
    run = runs.assemble_run(ROWS, trace_id=TRACE, agent=STABLE, visible_agents={})
    assert run is not None
    by_id = {s["span_id"]: s for s in run.spans}
    # Another team's service and an agent the caller cannot see are left out…
    assert set(by_id) == {"root", "chat", "tool"}
    assert run.hidden == 2
    # …and the tree closes over the gap.
    assert by_id["tool"]["parent_id"] == "root"
    assert by_id["root"]["parent_id"] is None
    assert [by_id[k]["step"] for k in ("root", "chat", "tool")] == ["agent", "model", "tool"]
    assert by_id["tool"]["status"] == "error" and by_id["tool"]["error_type"] == "timeout"
    assert by_id["chat"]["offset_ms"] == 10.0 and by_id["chat"]["input_tokens"] == 1200
    assert by_id["root"]["agent"] == {"id": "a1", "name": "triager", "self": True}
    assert run.duration_ms == 900.0
    assert run.started_at.endswith("+00:00")


def test_a_visible_agent_appears_by_name() -> None:
    run = runs.assemble_run(
        ROWS,
        trace_id=TRACE,
        agent=STABLE,
        visible_agents={"id:agt_2": {"id": "a2", "display_name": "helper"}},
    )
    assert run is not None
    helper = next(s for s in run.spans if s["span_id"] == "helper")
    assert helper["agent"] == {"id": "a2", "name": "helper", "self": False}
    assert run.hidden == 1


def test_a_trace_the_agent_took_no_part_in_is_not_its_run() -> None:
    other = {**STABLE, "identity_key": "id:somebody-else"}
    assert runs.assemble_run(ROWS, trace_id=TRACE, agent=other, visible_agents={}) is None
    assert runs.assemble_run([], trace_id=TRACE, agent=STABLE, visible_agents={}) is None


def test_steps_are_named_by_what_they_did() -> None:
    assert runs.step_kind({"op": "invoke_agent"}) == "agent"
    assert runs.step_kind({"op": "", "mcp_method": "tools/call"}) == "tool"
    assert runs.step_kind({"op": "", "inference": 1}) == "model"
    assert runs.step_kind({"op": "", "mcp_method": "tools/list"}) == "mcp"
    assert runs.step_kind({"op": "", "genai": 1}) == "genai"
    assert runs.step_kind({}) == "other"
