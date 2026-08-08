"""Local stand-in for the production Ingress (test infrastructure only).

Implements exactly the routing the chart renders, and nothing else:

    /v1  -> the API, path and query UNCHANGED
    /    -> the web app

Longest prefix wins, as Kubernetes `pathType: Prefix` does. There is no
rewrite, no regex and no configuration snippet — the point of this script
is to run the same contract the chart asserts structurally, so that "the
API needs its /v1 path intact" is demonstrated rather than assumed.

It binds loopback, speaks plain HTTP, and refuses to run outside
local/test. It is never part of a deployment.
"""

import http.server
import os
import socketserver
import threading
import urllib.error
import urllib.request

PROXY_PORT = int(os.environ.get("DRAKE_EDGE_PROXY_PORT", "18080"))
API_PORT = int(os.environ.get("DRAKE_EDGE_API_PORT", "18000"))
WEB_PORT = int(os.environ.get("DRAKE_EDGE_WEB_PORT", "13100"))

# What the proxy forwarded upstream, so a test can assert the API was
# addressed with the ORIGINAL path. Recording it here rather than adding a
# debug endpoint to the API keeps the application surface unchanged.
_FORWARDED: list[str] = []
_LOCK = threading.Lock()


class EdgeServer(socketserver.ThreadingTCPServer):
    # Set before bind, not after: a leftover socket in TIME_WAIT from the
    # previous run must not make the smoke fail for an unrelated reason.
    allow_reuse_address = True
    daemon_threads = True


class EdgeHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *_args) -> None:
        return

    def do_GET(self) -> None:
        if self.path.startswith("/__forwarded"):
            with _LOCK:
                body = "\n".join(_FORWARDED).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # Longest prefix wins: /v1 before /.
        if self.path == "/v1" or self.path.startswith("/v1/") or self.path.startswith("/v1?"):
            upstream_port = API_PORT
        else:
            upstream_port = WEB_PORT

        # The path is forwarded verbatim. No rewriting happens here,
        # because none happens at the real edge either.
        target = f"http://127.0.0.1:{upstream_port}{self.path}"
        with _LOCK:
            if upstream_port == API_PORT:
                _FORWARDED.append(self.path)

        request = urllib.request.Request(target, method="GET")
        for header in ("Cookie", "Accept", "X-CSRF-Token"):
            value = self.headers.get(header)
            if value:
                request.add_header(header, value)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
                body = response.read()
                status = response.status
                content_type = response.headers.get("Content-Type", "text/plain")
        except urllib.error.HTTPError as error:
            body = error.read()
            status = error.code
            content_type = error.headers.get("Content-Type", "text/plain")
        except OSError:
            body = b"upstream unavailable"
            status = 502
            content_type = "text/plain"

        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    if os.environ.get("DRAKE_ENV", "local") not in ("local", "test"):
        raise RuntimeError("the edge proxy is local/test infrastructure only")
    with EdgeServer(("127.0.0.1", PROXY_PORT), EdgeHandler) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
