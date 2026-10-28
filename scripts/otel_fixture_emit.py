"""Send deterministic fixture logs and traces to the LOCAL collector.

    python scripts/otel_fixture_emit.py [--endpoint http://127.0.0.1:54318]

Stdlib only, OTLP/HTTP JSON. The resource attributes match what the
cluster agent pipeline attaches in a real cluster (k8s.cluster.name,
k8s.namespace.name, k8s.deployment.name), so a local service-health binding
pointing at `local-fixture` / `alpha-prod` / `alpha-api` finds them.

Refuses any endpoint that is not loopback: this is fixture data and has no
business reaching a shared collector.
"""

import argparse
import json
import secrets
import sys
import time
import urllib.request
from urllib.parse import urlsplit

RESOURCE = {
    "service.name": "alpha-api",
    "k8s.cluster.name": "local-fixture",
    "k8s.namespace.name": "alpha-prod",
    "k8s.deployment.name": "alpha-api",
    "k8s.pod.name": "alpha-api-7d9f8c6b5-x2k4q",
    "k8s.container.name": "api",
}

# (severity text, severity number, body). One line carries a credential
# shape on purpose: the redaction processor must mask it before storage.
# Built at runtime from parts, like every credential-shaped test value in
# this repository, so the secret scanner needs no exemption for it.
CREDENTIAL_LINE = "connecting with " + "pass" + "word=" + secrets.token_hex(6) + " failed"

LINES = [
    ("INFO", 9, "GET /v1/orders 200 12ms"),
    ("INFO", 9, "GET /v1/orders/42 200 8ms"),
    ("WARN", 13, "slow query on orders: 840ms"),
    ("ERROR", 17, "POST /v1/payments 502 upstream timeout"),
    ("ERROR", 17, CREDENTIAL_LINE),
]


ROOT_SPAN_ATTRIBUTES = {"http.request.method": "POST", "http.route": "/v1/payments"}


def _attributes(values: dict[str, str]) -> list[dict[str, object]]:
    return [{"key": key, "value": {"stringValue": value}} for key, value in values.items()]


def _post(endpoint: str, path: str, document: dict[str, object]) -> None:
    request = urllib.request.Request(  # noqa: S310 - http(s) loopback, checked in main()
        endpoint.rstrip("/") + path,
        data=json.dumps(document).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - loopback only
        if response.status != 200:
            raise RuntimeError(f"{path}: HTTP {response.status}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default="http://127.0.0.1:54318")
    args = parser.parse_args()
    parts = urlsplit(args.endpoint)
    if parts.scheme not in ("http", "https") or parts.hostname not in (
        "127.0.0.1",
        "localhost",
        "::1",
    ):
        print("refusing: the fixture emitter only talks to a loopback collector", file=sys.stderr)
        return 2

    now = time.time_ns()
    trace_id = secrets.token_hex(16)
    root_span = secrets.token_hex(8)
    child_span = secrets.token_hex(8)

    records = []
    for index, (text, number, body) in enumerate(LINES):
        record: dict[str, object] = {
            "timeUnixNano": str(now - (len(LINES) - index) * 1_000_000_000),
            "severityText": text,
            "severityNumber": number,
            "body": {"stringValue": body},
        }
        if text == "ERROR":
            record["traceId"] = trace_id
            record["spanId"] = child_span
        records.append(record)

    _post(
        args.endpoint,
        "/v1/logs",
        {
            "resourceLogs": [
                {
                    "resource": {"attributes": _attributes(RESOURCE)},
                    "scopeLogs": [{"scope": {"name": "drake.fixture"}, "logRecords": records}],
                }
            ]
        },
    )

    start = now - 2_000_000_000
    _post(
        args.endpoint,
        "/v1/traces",
        {
            "resourceSpans": [
                {
                    "resource": {"attributes": _attributes(RESOURCE)},
                    "scopeSpans": [
                        {
                            "scope": {"name": "drake.fixture"},
                            "spans": [
                                {
                                    "traceId": trace_id,
                                    "spanId": root_span,
                                    "name": "POST /v1/payments",
                                    "kind": 2,
                                    "startTimeUnixNano": str(start),
                                    "endTimeUnixNano": str(start + 1_200_000_000),
                                    "status": {"code": 2, "message": "upstream timeout"},
                                    "attributes": _attributes(ROOT_SPAN_ATTRIBUTES),
                                },
                                {
                                    "traceId": trace_id,
                                    "spanId": child_span,
                                    "parentSpanId": root_span,
                                    "name": "payments-gateway call",
                                    "kind": 3,
                                    "startTimeUnixNano": str(start + 100_000_000),
                                    "endTimeUnixNano": str(start + 1_150_000_000),
                                    "status": {"code": 2},
                                },
                            ],
                        }
                    ],
                }
            ]
        },
    )
    print(f"sent {len(records)} log records and 2 spans (trace {trace_id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
