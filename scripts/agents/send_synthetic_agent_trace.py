"""Send one synthetic multi-convention AI-agent trace over OTLP/HTTP JSON.

Used to prove the gateway end to end: every convention (OTel GenAI, older
OTel names, OpenInference, Vercel AI SDK legacy) arrives as `gen_ai.*`, and
no prompt, answer or tool argument (the SECRET-* values) is stored.

    python3 scripts/agents/send_synthetic_agent_trace.py [url]
    OTLP_TOKEN=… python3 scripts/agents/send_synthetic_agent_trace.py https://host/otlp/v1/traces
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:54318/v1/traces"
if not URL.startswith(("http://", "https://")):
    raise SystemExit("the OTLP URL must be http(s)")
TOKEN = os.environ.get("OTLP_TOKEN")
now = time.time_ns()
trace = uuid.uuid4().hex


def sid():
    return uuid.uuid4().hex[:16]


def attrs(d):
    out = []
    for k, v in d.items():
        if isinstance(v, bool):
            val = {"boolValue": v}
        elif isinstance(v, int):
            val = {"intValue": str(v)}
        else:
            val = {"stringValue": str(v)}
        out.append({"key": k, "value": val})
    return out


root, chat, tool = sid(), sid(), sid()


def span(span_id, parent, name, kind, start_ms, dur_ms, a, events=()):
    s = {
        "traceId": trace,
        "spanId": span_id,
        "name": name,
        "kind": kind,
        "startTimeUnixNano": str(now - (1000 - start_ms) * 1_000_000),
        "endTimeUnixNano": str(now - (1000 - start_ms - dur_ms) * 1_000_000),
        "attributes": attrs(a),
        "status": {},
    }
    if parent:
        s["parentSpanId"] = parent
    if events:
        s["events"] = [
            {"name": n, "timeUnixNano": str(now), "attributes": attrs(ea)} for n, ea in events
        ]
    return s


spans = [
    span(
        root,
        None,
        "invoke_agent support-triager",
        1,
        0,
        900,
        {
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": "support-triager",
            "gen_ai.agent.id": "agt_123",
            "gen_ai.provider.name": "anthropic",
            "gen_ai.conversation.id": "conv-1",
            "gen_ai.input.messages": "SECRET-PROMPT-1",
        },
    ),
    span(
        chat,
        root,
        "chat claude-sonnet-5",
        3,
        10,
        600,
        {
            "gen_ai.operation.name": "chat",
            "gen_ai.system": "anthropic",
            "gen_ai.request.model": "claude-sonnet-5",
            "gen_ai.usage.prompt_tokens": 1200,
            "gen_ai.usage.completion_tokens": 300,
            "gen_ai.prompt.0.content": "SECRET-PROMPT-2",
        },
        events=[
            ("gen_ai.content.prompt", {"gen_ai.prompt": "SECRET-PROMPT-3"}),
            ("retry", {"attempt": 1}),
        ],
    ),
    span(
        tool,
        root,
        "execute_tool get_ticket",
        1,
        620,
        200,
        {
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": "get_ticket",
            "gen_ai.tool.call.id": "call_1",
            "gen_ai.tool.call.arguments": "SECRET-ARGS",
        },
    ),
    span(
        sid(),
        root,
        "openai.chat",
        3,
        830,
        50,
        {
            "openinference.span.kind": "LLM",
            "llm.model_name": "gpt-5",
            "llm.token_count.prompt": 500,
            "llm.token_count.completion": 80,
            "input.value": "SECRET-PROMPT-4",
            "output.value": "SECRET-ANSWER",
        },
    ),
    span(
        sid(),
        root,
        "ai.generateText",
        1,
        880,
        10,
        {
            "ai.model.id": "gpt-5-mini",
            "ai.usage.promptTokens": 40,
            "ai.usage.completionTokens": 7,
            "ai.telemetry.functionId": "checkout-assistant",
            "ai.prompt": "SECRET-PROMPT-5",
        },
    ),
]
body = {
    "resourceSpans": [
        {
            "resource": {
                "attributes": attrs({"service.name": "support-bot", "k8s.namespace.name": "demo"})
            },
            "scopeSpans": [{"scope": {"name": "synthetic"}, "spans": spans}],
        }
    ]
}
req = urllib.request.Request(  # noqa: S310 — scheme checked above
    URL,
    data=json.dumps(body).encode(),
    method="POST",
    headers={
        "Content-Type": "application/json",
        **({"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}),
    },
)
try:
    with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 — scheme checked above
        print("status", r.status, "trace", trace)
except urllib.error.HTTPError as e:
    print("status", e.code, e.read()[:200])
