"""Deterministic local-only metrics fixture exporter.

Serves one path per simulated scrape target
(``/metrics/<project>/<environment>/<service>``); Prometheus attaches the
project/environment/service identity as *target* labels, so ``up`` carries
them too. Values are pure functions of wall-clock time — counters grow at
fixed rates, so ``rate()`` is stable and tests are deterministic.

Bounded labels only (route_template, status_class, le). No secrets, no real
endpoints, stdlib only. Never deployed anywhere: local compose + CI e2e.
"""

import time
from http.server import BaseHTTPRequestHandler, HTTPServer

START = 1_700_000_000  # fixed epoch so counter values are reproducible

TARGETS = {
    ("alpha", "dev", "api"),
    ("alpha", "dev", "web"),
    ("alpha", "prod", "api"),
    ("beta", "dev", "api"),
}

ROUTES = ("/v1/items/{id}", "/health/live")
# requests per second by (route_template, status_class)
RATES = {
    ("/v1/items/{id}", "2xx"): 5.0,
    ("/v1/items/{id}", "5xx"): 0.25,
    ("/health/live", "2xx"): 2.0,
}
# latency histogram: deterministic share of requests per upper bound
BUCKETS = (("0.05", 0.55), ("0.1", 0.75), ("0.25", 0.9), ("0.5", 0.97), ("1", 1.0))


def render(elapsed: float) -> str:
    lines = [
        "# TYPE http_server_requests_total counter",
    ]
    total = 0.0
    for (route, status), per_second in sorted(RATES.items()):
        value = per_second * elapsed
        total += value
        lines.append(
            f'http_server_requests_total{{route_template="{route}",status_class="{status}"}}'
            f" {value:.0f}"
        )
    lines.append("# TYPE http_server_request_duration_seconds histogram")
    for le, share in BUCKETS:
        lines.append(
            f'http_server_request_duration_seconds_bucket{{le="{le}"}} {total * share:.0f}'
        )
    lines.append(f'http_server_request_duration_seconds_bucket{{le="+Inf"}} {total:.0f}')
    lines.append(f"http_server_request_duration_seconds_sum {total * 0.09:.0f}")
    lines.append(f"http_server_request_duration_seconds_count {total:.0f}")
    lines.append("# TYPE container_restarts_total counter")
    lines.append(f"container_restarts_total {int(elapsed // 86400)}")
    lines.append("# TYPE container_cpu_usage_cores gauge")
    lines.append("container_cpu_usage_cores 0.25")
    return "\n".join(lines) + "\n"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        parts = tuple(part for part in self.path.split("/") if part)
        if len(parts) == 4 and parts[0] == "metrics" and parts[1:] in TARGETS:
            body = render(time.time() - START).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args: object) -> None:  # quiet
        del args


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 9464), Handler).serve_forever()  # noqa: S104 (container-internal)
