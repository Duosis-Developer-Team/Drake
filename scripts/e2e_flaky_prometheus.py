"""E2E-only flaky Prometheus proxy (local/test only, stdlib).

Forwards /api/v1/* to the local fixture Prometheus and can be switched into
a failure mode by the browser-acceptance suite to exercise honest
stale/unavailable states:

    POST /__mode/fail   → upstream calls answered with 502
    POST /__mode/ok     → forwarding restored
    GET  /__health      → readiness for the Playwright webServer probe

Never deployed anywhere; carries no credentials.
"""

import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

UPSTREAM = os.environ.get("DRAKE_E2E_PROMETHEUS_UPSTREAM", "http://127.0.0.1:59090")
PORT = int(os.environ.get("DRAKE_E2E_FLAKY_PORT", "59191"))

if os.environ.get("DRAKE_ENV", "local") not in ("local", "test"):
    raise RuntimeError("e2e flaky prometheus proxy is local/test only")

MODE = {"value": "ok"}


class Handler(BaseHTTPRequestHandler):
    def _respond(self, status: int, body: bytes, content_type: str = "text/plain") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/__health":
            self._respond(200, b"ok")
        else:
            self._respond(404, b"not found")

    def do_POST(self) -> None:
        if self.path == "/__mode/fail":
            MODE["value"] = "fail"
            self._respond(200, b"mode=fail")
            return
        if self.path == "/__mode/ok":
            MODE["value"] = "ok"
            self._respond(200, b"mode=ok")
            return
        if not self.path.startswith("/api/v1/"):
            self._respond(404, b"not found")
            return
        if MODE["value"] == "fail":
            self._respond(502, b"e2e flaky proxy: provider down")
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        request = urllib.request.Request(  # noqa: S310 - fixed local upstream
            f"{UPSTREAM}{self.path}",
            data=body,
            headers={
                "Content-Type": self.headers.get(
                    "Content-Type", "application/x-www-form-urlencoded"
                )
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as upstream:  # noqa: S310
                payload = upstream.read()
                self._respond(upstream.status, payload, "application/json")
        except urllib.error.URLError:
            self._respond(502, b"upstream unreachable")

    def log_message(self, *args: object) -> None:  # quiet
        del args


if __name__ == "__main__":
    sys.stdout.write(f"flaky prometheus proxy on 127.0.0.1:{PORT} -> {UPSTREAM}\n")
    sys.stdout.flush()
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
