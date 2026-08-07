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

# Fikir-Sepeti is governed by a ruleset rather than classic protection, so
# the E2E covers both evidence paths. The summary carries no `rules`
# member; only the effective-rules endpoint answers "what applies here".
RULESET_SUMMARIES = {
    "Fikir-Sepeti": [
        {
            "id": 42,
            "name": "default branch guard",
            "target": "branch",
            "source_type": "Repository",
            "source": "Duosis-Developer-Team/Fikir-Sepeti",
            "enforcement": "active",
            "node_id": "RRS_lACkVXNlcgQ",
            "created_at": "2026-01-15T08:43:03Z",
            "updated_at": "2026-02-23T16:29:47Z",
        }
    ]
}

BRANCH_RULES = {
    "Fikir-Sepeti": [
        {
            "type": "pull_request",
            "ruleset_source_type": "Repository",
            "ruleset_source": "Duosis-Developer-Team/Fikir-Sepeti",
            "ruleset_id": 42,
            "parameters": {"required_approving_review_count": 1},
        },
        {
            "type": "non_fast_forward",
            "ruleset_source_type": "Organization",
            "ruleset_source": "Duosis-Developer-Team",
            "ruleset_id": 73,
            "parameters": {},
        },
        {
            "type": "deletion",
            "ruleset_source_type": "Organization",
            "ruleset_source": "Duosis-Developer-Team",
            "ruleset_id": 73,
            "parameters": {},
        },
        {
            "type": "required_status_checks",
            "ruleset_source_type": "Repository",
            "ruleset_source": "Duosis-Developer-Team/Fikir-Sepeti",
            "ruleset_id": 42,
            "parameters": {
                "required_status_checks": [{"context": "ci"}],
                "strict_required_status_checks_policy": True,
            },
        },
    ]
}

# Hermes is fully governed by classic protection; logislot deliberately is
# not, so the E2E can assert a blocking violation without inventing one.
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

    def log_message(self, *_args) -> None:
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

    def do_GET(self) -> None:
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
        if path == f"/app/installations/{INSTALLATION_ID}":
            self._send(
                200,
                {
                    "id": INSTALLATION_ID,
                    "account": {"login": "Duosis-Developer-Team", "type": "Organization"},
                    "app_slug": "drake",
                    "repository_selection": "selected",
                    "permissions": {
                        "metadata": "read",
                        "administration": "read",
                        "actions": "read",
                    },
                    "events": ["installation", "installation_repositories", "repository"],
                    "suspended_at": None,
                },
            )
            return
        if path == "/installation/repositories":
            # The documented shape: {total_count, repositories: [...]}.
            listed = list(REPOSITORIES.values())
            self._send(200, {"total_count": len(listed), "repositories": listed})
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
                # Ruleset SUMMARIES: the documented list response carries
                # no `rules` member, so it can never be rule evidence.
                self._send(200, RULESET_SUMMARIES.get(name, []))
                return
            if path.startswith(f"{prefix}/rules/branches/"):
                self._send(200, BRANCH_RULES.get(name, []))
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

    def do_POST(self) -> None:
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
