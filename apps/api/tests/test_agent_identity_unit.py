"""Who an agent is, from the best signal present — and how sure we are."""

from drake_api.ai_agents.discovery import Bucket, caller_of, identity_of, real_name, statements


def row(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "svc": "support-bot",
        "cluster": "prod-1",
        "ns": "support",
        "wk": "support-bot",
        "agent_id": "",
        "agent_name": "",
        "ingest": "",
        "ebpf": 0,
    }
    return {**base, **over}


def test_a_provider_id_is_a_stable_identity() -> None:
    ident = identity_of(row(agent_id="arn:aws:bedrock:agent/ABC", agent_name="triager"))
    assert ident.confidence == "stable"
    assert ident.key == "id:arn:aws:bedrock:agent/ABC"
    assert ident.display_name == "triager"


def test_a_real_name_within_its_service_is_named() -> None:
    ident = identity_of(row(agent_name="ticket-triager"))
    assert ident.confidence == "named"
    assert ident.key == "name:support-bot:ticket-triager"


def test_framework_defaults_and_ids_are_not_names() -> None:
    for generic in ("StateGraph", "agent", "LangGraph", "6f1c2e0a-9d3b-4c1e-8a7f-2b9c0d1e3f45"):
        assert not real_name(generic)
        assert identity_of(row(agent_name=generic)).confidence == "service"


def test_only_ebpf_is_observed_and_external_is_marked() -> None:
    seen = identity_of(row(ebpf=1, svc=""))
    assert seen.confidence == "observed"
    assert seen.key == "observed:prod-1/support/support-bot"
    assert identity_of(row(ingest="external")).ingest == "external"
    assert identity_of(row()).ingest == "cluster"


def test_tokens_come_from_model_calls_or_the_agent_span_never_both() -> None:
    calls = Bucket()
    calls.add({"inference_calls": 2, "in_inf": 100, "out_inf": 10, "in_agent": 999, "out_agent": 9})
    assert (calls.input_tokens, calls.output_tokens) == (100, 10)
    agent_only = Bucket()
    agent_only.add({"invocations": 1, "in_agent": 40, "out_agent": 4})
    assert (agent_only.input_tokens, agent_only.output_tokens) == (40, 4)


def test_a_caller_is_an_id_or_a_name_and_never_an_email() -> None:
    full = caller_of("8C1F0B2A-7d3e", "  Gencay   Coskun ")
    assert full is not None
    assert (full.key, full.user_id, full.user_name) == (
        "id:8C1F0B2A-7d3e",
        "8C1F0B2A-7d3e",
        "Gencay Coskun",
    )
    named = caller_of("", "Ayse")
    assert named is not None and (named.key, named.user_id) == ("name:Ayse", None)
    # An email is never kept, as an id or as a name.
    assert caller_of("gencay@example.com", "") is None
    only_id = caller_of("u-1", "gencay@example.com")
    assert only_id is not None and only_id.user_name is None
    assert caller_of("", "line\nbreak") is None
    assert caller_of("has space", "") is None
    assert caller_of(None, None) is None
    long = caller_of("", "x" * 500)
    assert long is not None and len(long.user_name or "") == 120


def test_the_callers_statement_is_bounded_and_separate() -> None:
    *_, mcp, callers = statements("otel")
    assert "caller_key" in callers and "caller_key" not in mcp
    assert "LIMIT {max_rows:UInt16}" in callers
    assert "user.name" in callers and "enduser.id" in callers
    assert "email" not in callers
