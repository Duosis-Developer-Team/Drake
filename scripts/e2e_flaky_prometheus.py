"""E2E-only flaky Prometheus proxy (local/test only, stdlib).

Forwards /api/v1/* to the local fixture Prometheus and can be switched
into failure or slow modes by the browser-acceptance suite to exercise
honest stale/unavailable states and REAL end-to-end cancellation:

    POST /__mode/fail   → upstream calls answered with 502
    POST /__mode/slow   → responses delayed (cancellation window)
    POST /__mode/ok     → normal forwarding restored
    GET  /__stats       → {"started": n, "completed": n, "disconnected": n}
    POST /__stats/reset → counters back to zero
    GET  /__health      → readiness for the Playwright webServer probe

`disconnected` counts upstream-bound requests whose CLIENT (the Drake
API's provider transport) closed the connection before the response could
be written — the observable proof that server-side cancellation reached
the provider boundary. Threaded so slow requests never block others.
Never deployed anywhere; carries no credentials.
"""

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = os.environ.get("DRAKE_E2E_PROMETHEUS_UPSTREAM", "http://127.0.0.1:59090")
PORT = int(os.environ.get("DRAKE_E2E_FLAKY_PORT", "59191"))
SLOW_DELAY_SECONDS = float(os.environ.get("DRAKE_E2E_FLAKY_SLOW_SECONDS", "3"))

if os.environ.get("DRAKE_ENV", "local") not in ("local", "test"):
    raise RuntimeError("e2e flaky prometheus proxy is local/test only")

_LOCK = threading.Lock()
MODE = {"value": "ok"}
STATS = {"started": 0, "completed": 0, "disconnected": 0}


def _bump(counter: str) -> None:
    with _LOCK:
        STATS[counter] += 1


class Handler(BaseHTTPRequestHandler):
    def _client_gone(self) -> bool:
        """Non-blocking EOF probe: a cancelled Drake API call has CLOSED its
        connection by the time the delayed response is ready — a bare write
        can falsely 'succeed' into a FIN-closed socket, so the probe is the
        reliable disconnect signal."""
        try:
            self.connection.setblocking(False)
            try:
                chunk = self.connection.recv(1)
            except BlockingIOError:
                return False  # open and quietly waiting for the response
            except OSError:
                return True
            return chunk == b""  # EOF: the peer closed
        finally:
            try:
                self.connection.setblocking(True)
            except OSError:
                pass

    def _respond(self, status: int, body: bytes, content_type: str = "text/plain") -> bool:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionResetError, OSError):
            return False

    def do_GET(self) -> None:
        if self.path == "/__health":
            self._respond(200, b"ok")
        elif self.path == "/__stats":
            with _LOCK:
                snapshot = dict(STATS)
            self._respond(200, json.dumps(snapshot).encode(), "application/json")
        else:
            self._respond(404, b"not found")

    def do_POST(self) -> None:
        if self.path in ("/__mode/fail", "/__mode/ok", "/__mode/slow"):
            MODE["value"] = self.path.rsplit("/", 1)[1]
            self._respond(200, f"mode={MODE['value']}".encode())
            return
        if self.path == "/__stats/reset":
            with _LOCK:
                for key in STATS:
                    STATS[key] = 0
            self._respond(200, b"reset")
            return
        if not self.path.startswith("/api/v1/"):
            self._respond(404, b"not found")
            return
        _bump("started")
        try:
            # Read the body BEFORE any delay: it arrives with the request.
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length)
        except OSError:
            _bump("disconnected")
            return
        if MODE["value"] == "fail":
            if self._respond(502, b"e2e flaky proxy: provider down"):
                _bump("completed")
            else:
                _bump("disconnected")
            return
        if MODE["value"] == "slow":
            time.sleep(SLOW_DELAY_SECONDS)
        if self._client_gone():
            _bump("disconnected")
            return
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
                delivered = self._respond(upstream.status, payload, "application/json")
        except urllib.error.URLError:
            delivered = self._respond(502, b"upstream unreachable")
        except OSError:
            delivered = False
        if delivered:
            _bump("completed")
        else:
            # The Drake API cancelled this provider call: the connection was
            # gone before the (delayed) response could be written.
            _bump("disconnected")

    def log_message(self, *args: object) -> None:  # quiet
        del args


if __name__ == "__main__":
    sys.stdout.write(f"flaky prometheus proxy on 127.0.0.1:{PORT} -> {UPSTREAM}\n")
    sys.stdout.flush()
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
