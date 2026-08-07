"""E2E-only fake GitHub REST API (stdlib, loopback, deterministic).

Serves just enough of the documented read surface for the policy engine,
plus the installation-token mint. It records every request so a test can
prove a blocked repository produced zero calls, and it exposes control
endpoints to force failure modes.

Local/test only: it binds loopback and speaks plain HTTP.
"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("DRAKE_E2E_GITHUB_PORT", "59097"))

_STATE = {"mode": "ok", "calls": []}
_LOCK = threading.Lock()

HERMES_ID = 900001
LOGISLOT_ID = 900002
FIKIR_ID = 900004
INSTALLATION_ID = 55501

REPOSITORIES = {
    "Hermes": {
        "id": HERMES_ID,
        "node_id": "R_hermes",
        "name": "Hermes",
        "full_name": "Duosis-Developer-Team/Hermes",
        "private": True,
        "visibility": "private",
        "archived": False,
        "disabled": False,
        "default_branch": "main",
        "security_and_analysis": {
            "secret_scanning": {"status": "enabled"},
            "dependabot_security_updates": {"status": "enabled"},
        },
    },
    "logislot": {
        "id": LOGISLOT_ID,
        "node_id": "R_logislot",
        "name": "logislot",
        "full_name": "Duosis-Developer-Team/logislot",
        "private": True,
        "visibility": "private",
        "archived": False,
        "disabled": False,
        "default_branch": "main",
    },
    "Fikir-Sepeti": {
        "id": FIKIR_ID,
        "node_id": "R_fikir",
        "name": "Fikir-Sepeti",
        "full_name": "Duosis-Developer-Team/Fikir-Sepeti",
        "private": True,
        "visibility": "private",
        "archived": False,
        "disabled": False,
        "default_branch": "main",
    },
}

# Hermes is fully governed; logislot deliberately is not, so the E2E can
# assert a blocking violation without inventing one.
PROTECTED = {
    "Hermes": {
        "required_pull_request_reviews": {"required_approving_review_count": 1},
        "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False},
        "required_status_checks": {"strict": True, "contexts": ["ci"]},
        "enforce_admins": {"enabled": True},
    }
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *_args) -> None:  # noqa: ANN002 - silence access logs
        return

    def _send(self, status: int, payload) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _record(self) -> None:
        with _LOCK:
            _STATE["calls"].append(f"{self.command} {self.path.split('?')[0]}")

    def do_GET(self) -> None:  # noqa: N802 - stdlib contract
        path = self.path.split("?")[0]
        if path == "/__health":
            self._send(200, {"status": "ok"})
            return
        if path == "/__calls":
            with _LOCK:
                self._send(200, {"calls": list(_STATE["calls"]), "mode": _STATE["mode"]})
            return
        self._record()
        with _LOCK:
            mode = _STATE["mode"]
        if mode == "unavailable":
            self._send(503, {"message": "unavailable"})
            return
        if mode == "rate_limited":
            self.send_response(403)
            self.send_header("Content-Type", "application/json")
            self.send_header("x-ratelimit-remaining", "0")
            body = json.dumps({"message": "rate limited"}).encode()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/app/installations":
            self._send(200, [{"id": INSTALLATION_ID}])
            return
        for name, payload in REPOSITORIES.items():
            prefix = f"/repos/Duosis-Developer-Team/{name}"
            if path == prefix:
                self._send(200, payload)
                return
            if path.startswith(f"{prefix}/branches/"):
                protection = PROTECTED.get(name)
                if protection is None:
                    self._send(404, {"message": "Branch not protected"})
                else:
                    self._send(200, protection)
                return
            if path == f"{prefix}/rulesets":
                self._send(200, [])
                return
            if path == f"{prefix}/actions/workflows":
                self._send(
                    200,
                    {
                        "workflows": [
                            {"name": "build", "path": "build.yml", "state": "active"},
                            {"name": "test", "path": "test.yml", "state": "active"},
                            {"name": "codeql", "path": "codeql.yml", "state": "active"},
                        ]
                    },
                )
                return
            if path == f"{prefix}/environments":
                self._send(200, {"environments": [{"name": "production"}]})
                return
            if path == f"{prefix}/environments/production":
                self._send(
                    200,
                    {
                        "protection_rules": [{"type": "required_reviewers"}],
                        "deployment_branch_policy": {"protected_branches": True},
                    },
                )
                return
        self._send(404, {"message": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib contract
        path = self.path.split("?")[0]
        if path.startswith("/__mode/"):
            with _LOCK:
                _STATE["mode"] = path.rsplit("/", 1)[-1]
            self._send(200, {"mode": _STATE["mode"]})
            return
        if path == "/__reset":
            with _LOCK:
                _STATE["calls"] = []
                _STATE["mode"] = "ok"
            self._send(200, {"status": "reset"})
            return
        self._record()
        if path.endswith("/access_tokens"):
            self._send(
                201,
                {
                    # Deliberately long and non-fixed-length.
                    "token": "ghs_" + "e" * 82,
                    "expires_at": "2099-01-01T00:00:00Z",
                    "permissions": {"metadata": "read", "administration": "read"},
                    "repository_selection": "selected",
                },
            )
            return
        self._send(404, {"message": "not found"})


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
