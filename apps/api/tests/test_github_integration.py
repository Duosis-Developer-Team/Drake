"""GitHub App integration tests (real PostgreSQL + Redis, fake GitHub).

Nothing here touches the real GitHub: every upstream call goes through an
injected `httpx.MockTransport`, and the fake counts its calls so a test
can prove that a blocked repository produced ZERO of them.
"""

import hashlib
import hmac
import json as jsonlib
import uuid as uuidlib
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from drake_api.github_app import catalog
from drake_api.github_app.webhook import SUPPORTED_EVENTS
from harness_s1 import build_harness, grant_platform_owner, require_it_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from test_catalog_api_integration import build_users, grant, login_all, make_role

pytestmark = pytest.mark.integration

API_ROOT = Path(__file__).resolve().parents[1]

WEBHOOK_SECRET = "local-only-webhook-secret"
HERMES_ID = 900001
LOGISLOT_ID = 900002
DATALAKE_ID = 900003
INSTALLATION_ID = 55501


def _write_app_credentials(tmp_path: Path) -> tuple[str, str]:
    """Runtime-generated RSA key + webhook secret; nothing is committed."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_path = tmp_path / "app-key.pem"
    private_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    private_path.chmod(0o600)
    secret_path = tmp_path / "webhook-secret"
    secret_path.write_text(WEBHOOK_SECRET)
    secret_path.chmod(0o600)
    return str(private_path), str(secret_path)


class FakeGitHub:
    """A deterministic, call-counting stand-in for the GitHub REST API."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.mode = "ok"
        self.protection_status = 200
        self.branch_rules_status = 200
        # What the access-token mint actually grants, so a narrower grant
        # than requested can be exercised.
        self.granted_permissions = {"metadata": "read", "administration": "read", "actions": "read"}
        # Force an always-full page set to exercise the pagination cap.
        self.installation_repositories_pages = 1
        # Provider-side installation identity, so identity verification and
        # a missed uninstall can be exercised.
        self.installation_present = True
        self.installation_account_login = catalog.ORGANIZATION
        self.installation_id_override: int | None = None
        # Every access-token request body, so least privilege is checkable.
        self.token_requests: list[dict[str, Any]] = []
        # Effective rules per repository, shaped like
        # GET /repos/{owner}/{repo}/rules/branches/{branch}.
        self.branch_rules: dict[str, list[dict[str, Any]]] = {}
        self.repositories = {
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
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(f"{request.method} {path}")
        if self.mode == "unavailable":
            return httpx.Response(503, json={"message": "unavailable"})
        if self.mode == "rate_limited":
            return httpx.Response(
                403, json={"message": "rate limited"}, headers={"x-ratelimit-remaining": "0"}
            )

        if path.endswith("/access_tokens"):
            try:
                self.token_requests.append(jsonlib.loads(request.content or b"{}"))
            except ValueError:
                self.token_requests.append({})
            return httpx.Response(
                201,
                json={
                    # A deliberately long, non-fixed-length opaque token.
                    "token": "ghs_" + "t" * 82,
                    "expires_at": "2099-01-01T00:00:00Z",
                    "permissions": dict(self.granted_permissions),
                    "repository_selection": "selected",
                },
            )
        if path == "/app/installations":
            return httpx.Response(200, json=[{"id": INSTALLATION_ID}])
        if path == f"/app/installations/{INSTALLATION_ID}":
            if not self.installation_present:
                # GitHub's documented not-found shape.
                return httpx.Response(
                    404,
                    json={
                        "message": "Not Found",
                        "documentation_url": (
                            "https://docs.github.com/rest/apps/apps"
                            "#get-an-installation-for-the-authenticated-app"
                        ),
                        "status": "404",
                    },
                )
            return httpx.Response(
                200,
                json={
                    "id": self.installation_id_override or INSTALLATION_ID,
                    "account": {
                        "login": self.installation_account_login,
                        "type": "Organization",
                    },
                    "app_slug": "drake",
                    "repository_selection": "selected",
                    "permissions": dict(self.granted_permissions),
                    "events": sorted(SUPPORTED_EVENTS),
                    "suspended_at": None,
                },
            )
        if path == "/installation/repositories":
            if self.installation_repositories_pages > 1:
                # Always a full page: the listing is never complete.
                return httpx.Response(
                    200,
                    json={
                        "total_count": 10_000,
                        "repositories": [
                            {
                                "id": 990_000 + index,
                                "node_id": f"R_p{index}",
                                "name": f"pad{index}",
                                "full_name": f"Duosis-Developer-Team/pad{index}",
                                "private": True,
                            }
                            for index in range(100)
                        ],
                    },
                )
            listed = list(self.repositories.values())
            return httpx.Response(200, json={"total_count": len(listed), "repositories": listed})
        for name, payload in self.repositories.items():
            if path == f"/repos/Duosis-Developer-Team/{name}":
                return httpx.Response(200, json=payload)
            if path.startswith(f"/repos/Duosis-Developer-Team/{name}/branches/"):
                if self.protection_status != 200:
                    return httpx.Response(self.protection_status, json={"message": "no"})
                return httpx.Response(
                    200,
                    json={
                        "required_pull_request_reviews": {"required_approving_review_count": 1},
                        "allow_force_pushes": {"enabled": False},
                        "allow_deletions": {"enabled": False},
                        "required_status_checks": {"strict": True, "contexts": ["ci"]},
                        "enforce_admins": {"enabled": True},
                    },
                )
            if path == f"/repos/Duosis-Developer-Team/{name}/rulesets":
                # Ruleset SUMMARIES — no `rules` member, exactly as
                # documented. They are never rule evidence.
                return httpx.Response(200, json=[])
            if path.startswith(f"/repos/Duosis-Developer-Team/{name}/rules/branches/"):
                if self.branch_rules_status != 200:
                    return httpx.Response(self.branch_rules_status, json={"message": "no"})
                return httpx.Response(200, json=self.branch_rules.get(name, []))
            if path == f"/repos/Duosis-Developer-Team/{name}/actions/workflows":
                return httpx.Response(
                    200,
                    json={
                        "workflows": [
                            {"name": "build", "path": "build.yml", "state": "active"},
                            {"name": "test", "path": "test.yml", "state": "active"},
                            {"name": "codeql", "path": "codeql.yml", "state": "active"},
                        ]
                    },
                )
            if path == f"/repos/Duosis-Developer-Team/{name}/environments":
                return httpx.Response(200, json={"environments": [{"name": "production"}]})
            if path.endswith("/environments/production"):
                return httpx.Response(
                    200,
                    json={
                        "protection_rules": [{"type": "required_reviewers"}],
                        "deployment_branch_policy": {"protected_branches": True},
                    },
                )
        return httpx.Response(404, json={"message": "not found"})


def github_harness(tmp_path: Path) -> tuple[Any, FakeGitHub]:
    private_path, secret_path = _write_app_credentials(tmp_path)
    fake = FakeGitHub()
    settings = require_it_settings().model_copy(
        update={
            "github_app_enabled": True,
            "github_app_client_id": "Iv1.localtest",
            "github_app_private_key_file": private_path,
            "github_webhook_secret_file": secret_path,
            "github_api_base_url": "http://127.0.0.1:59097",
        }
    )
    harness = build_harness(settings, github_transport=httpx.MockTransport(fake.handler))
    return harness, fake


def sign(body: bytes) -> str:
    return "sha256=" + hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()


def webhook_headers(event: str, delivery: str, body: bytes) -> dict[str, str]:
    return {
        "X-Hub-Signature-256": sign(body),
        "X-GitHub-Delivery": delivery,
        "X-GitHub-Event": event,
        "Content-Type": "application/json",
    }


def installation_payload(action: str = "created", repositories: list[dict] | None = None) -> dict:
    return {
        "action": action,
        "installation": {
            "id": INSTALLATION_ID,
            "account": {"login": catalog.ORGANIZATION, "id": 1},
        },
        "repositories": repositories
        if repositories is not None
        else [
            {
                "id": HERMES_ID,
                "node_id": "R_hermes",
                "name": "Hermes",
                "full_name": "Duosis-Developer-Team/Hermes",
                "private": True,
            },
            {
                "id": LOGISLOT_ID,
                "node_id": "R_logislot",
                "name": "logislot",
                "full_name": "Duosis-Developer-Team/logislot",
                "private": True,
            },
        ],
    }


async def deliver(client: httpx.AsyncClient, event: str, payload: dict, delivery: str) -> Any:
    body = jsonlib.dumps(payload).encode()
    return await client.post(
        "/v1/integrations/github/webhook",
        content=body,
        headers=webhook_headers(event, delivery, body),
    )


async def _seed_admin(harness: Any, engine: AsyncEngine) -> None:
    await login_all(harness, ["user-owner"])
    await grant_platform_owner(engine, harness.provider.issuer, "user-owner")


async def test_webhook_verification_and_replay_semantics(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    payload = installation_payload()
    body = jsonlib.dumps(payload).encode()
    delivery = str(uuidlib.uuid4())

    async with harness.api_client() as client:
        # 1) A valid delivery is processed exactly once.
        first = await client.post(
            "/v1/integrations/github/webhook",
            content=body,
            headers=webhook_headers("installation", delivery, body),
        )
        assert first.status_code == 202, first.text
        assert first.json()["status"] == "processed"

        # 2) The SAME delivery id with the SAME digest is an idempotent no-op.
        replay = await client.post(
            "/v1/integrations/github/webhook",
            content=body,
            headers=webhook_headers("installation", delivery, body),
        )
        assert replay.status_code == 202
        assert replay.json()["status"] == "duplicate"

        # 3) The same delivery id with DIFFERENT bytes is a security event.
        tampered = jsonlib.dumps(installation_payload(action="deleted")).encode()
        conflict = await client.post(
            "/v1/integrations/github/webhook",
            content=tampered,
            headers=webhook_headers("installation", delivery, tampered),
        )
        assert conflict.status_code == 409

        # 4) Missing / wrong / malformed signatures are all one refusal.
        for headers in (
            {"X-GitHub-Delivery": str(uuidlib.uuid4()), "X-GitHub-Event": "installation"},
            {
                "X-Hub-Signature-256": "sha256=" + "0" * 64,
                "X-GitHub-Delivery": str(uuidlib.uuid4()),
                "X-GitHub-Event": "installation",
            },
            {
                "X-Hub-Signature-256": "garbage",
                "X-GitHub-Delivery": str(uuidlib.uuid4()),
                "X-GitHub-Event": "installation",
            },
        ):
            refused = await client.post(
                "/v1/integrations/github/webhook", content=body, headers=headers
            )
            assert refused.status_code == 401, headers
            assert refused.json()["error"]["message"] == "webhook rejected"

        # 5) A valid signature with an invalid delivery id is still refused.
        bad_delivery = await client.post(
            "/v1/integrations/github/webhook",
            content=body,
            headers={
                "X-Hub-Signature-256": sign(body),
                "X-GitHub-Delivery": "not-a-uuid",
                "X-GitHub-Event": "installation",
            },
        )
        assert bad_delivery.status_code == 401

        # 6) An unsupported event is refused before any domain work.
        unsupported = await client.post(
            "/v1/integrations/github/webhook",
            content=body,
            headers=webhook_headers("push", str(uuidlib.uuid4()), body),
        )
        assert unsupported.status_code == 401

        # 7) An oversized body is refused at the boundary.
        huge = jsonlib.dumps({"padding": "x" * (1024 * 1024 + 64)}).encode()
        oversized = await client.post(
            "/v1/integrations/github/webhook",
            content=huge,
            headers=webhook_headers("installation", str(uuidlib.uuid4()), huge),
        )
        assert oversized.status_code == 413

        # 8) An installation owned by somebody else is refused.
        foreign = installation_payload()
        foreign["installation"]["account"]["login"] = "some-other-org"
        foreign_body = jsonlib.dumps(foreign).encode()
        mismatch = await client.post(
            "/v1/integrations/github/webhook",
            content=foreign_body,
            headers=webhook_headers("installation", str(uuidlib.uuid4()), foreign_body),
        )
        assert mismatch.status_code == 401

    # The fake GitHub was never called: webhooks do no outbound work.
    assert fake.calls == []

    async with engine.connect() as connection:
        deliveries = (
            await connection.execute(text("SELECT count(*) FROM github_webhook_deliveries"))
        ).scalar_one()
        repositories = (
            await connection.execute(text("SELECT count(*) FROM github_repositories"))
        ).scalar_one()
        stored = (
            await connection.execute(
                text("SELECT envelope::text FROM github_webhook_deliveries LIMIT 1")
            )
        ).scalar_one()
    # Only verified deliveries created rows: one accepted + one conflict.
    assert int(deliveries) == 1
    assert int(repositories) == 2
    # The raw payload is never stored.
    assert "sender" not in stored


async def test_onboarding_lifecycle_rename_transfer_and_removal(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, _ = github_harness(tmp_path)
    await _seed_admin(harness, engine)

    async with harness.api_client() as client:
        assert (
            await deliver(client, "installation", installation_payload(), str(uuidlib.uuid4()))
        ).status_code == 202

        # A rename arrives as the SAME permanent id with a new name.
        renamed = {
            "action": "renamed",
            "installation": {"id": INSTALLATION_ID, "account": {"login": catalog.ORGANIZATION}},
            "repository": {
                "id": HERMES_ID,
                "name": "Hermes-Core",
                "full_name": "Duosis-Developer-Team/Hermes-Core",
                "private": True,
            },
        }
        assert (
            await deliver(client, "repository", renamed, str(uuidlib.uuid4()))
        ).status_code == 202

    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text("SELECT full_name, name FROM github_repositories WHERE external_id = :id"),
                {"id": HERMES_ID},
            )
        ).all()
    assert len(rows) == 1, "a rename must reconcile onto the SAME row"
    assert rows[0][0] == "Duosis-Developer-Team/Hermes-Core"

    async with harness.api_client() as client:
        # A transfer changes the owner on the same identity.
        transferred = {
            "action": "transferred",
            "installation": {"id": INSTALLATION_ID, "account": {"login": catalog.ORGANIZATION}},
            "repository": {
                "id": LOGISLOT_ID,
                "name": "logislot",
                "full_name": "Duosis-Developer-Team/logislot",
                "private": False,
            },
        }
        assert (
            await deliver(client, "repository", transferred, str(uuidlib.uuid4()))
        ).status_code == 202

        # Removal from the installation is SOFT state.
        removal = {
            "action": "removed",
            "installation": {"id": INSTALLATION_ID, "account": {"login": catalog.ORGANIZATION}},
            "repositories_removed": [
                {
                    "id": LOGISLOT_ID,
                    "full_name": "Duosis-Developer-Team/logislot",
                    "private": True,
                }
            ],
        }
        assert (
            await deliver(client, "installation_repositories", removal, str(uuidlib.uuid4()))
        ).status_code == 202

    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT onboarding_state, access_state FROM github_repositories "
                    "WHERE external_id = :id"
                ),
                {"id": LOGISLOT_ID},
            )
        ).one()
        total = (
            await connection.execute(text("SELECT count(*) FROM github_repositories"))
        ).scalar_one()
    assert row[0] == "disabled" and row[1] == "removed"
    assert int(total) == 2, "removal must never delete the row"

    # A suspend event marks the installation without losing repositories.
    async with harness.api_client() as client:
        suspend = installation_payload(action="suspend", repositories=[])
        assert (
            await deliver(client, "installation", suspend, str(uuidlib.uuid4()))
        ).status_code == 202
    async with engine.connect() as connection:
        state = (
            await connection.execute(
                text("SELECT state FROM github_installations WHERE external_id = :id"),
                {"id": INSTALLATION_ID},
            )
        ).scalar_one()
    assert state == "suspended"


async def test_datalake_security_gate_blocks_before_any_api_call(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)

    payload = installation_payload(
        repositories=[
            {
                "id": DATALAKE_ID,
                "name": "Datalake-Platform-GUI",
                "full_name": "Duosis-Developer-Team/Datalake-Platform-GUI",
                "private": True,
            }
        ]
    )
    async with harness.api_client() as client:
        assert (
            await deliver(client, "installation", payload, str(uuidlib.uuid4()))
        ).status_code == 202

    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT id, onboarding_state, security_gate, state_reason "
                    "FROM github_repositories WHERE external_id = :id"
                ),
                {"id": DATALAKE_ID},
            )
        ).one()
    assert row[1] == "blocked"
    assert row[2] == "manual_env_review"
    assert row[3] == "security_gate_manual_env_review"

    async with harness.api_client() as client:
        me = await harness.login(client, "user-owner")
        refused = await client.post(
            f"/v1/integrations/github/repositories/{row[0]}/reconcile",
            headers={"X-CSRF-Token": me["csrf_token"], "Idempotency-Key": str(uuidlib.uuid4())},
        )
    assert refused.status_code == 409
    # The whole point: not one call reached GitHub for a gated repository.
    assert fake.calls == [], f"blocked repository must make zero API calls, saw {fake.calls}"


async def test_dry_run_evaluation_makes_only_read_calls(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)

    async with harness.api_client() as client:
        await deliver(client, "installation", installation_payload(), str(uuidlib.uuid4()))
        me = await harness.login(client, "user-owner")
        listing = (await client.get("/v1/integrations/github/repositories")).json()
        hermes = next(item for item in listing["repositories"] if item["external_id"] == HERMES_ID)
        result = await client.post(
            f"/v1/integrations/github/repositories/{hermes['id']}/reconcile",
            headers={"X-CSRF-Token": me["csrf_token"], "Idempotency-Key": str(uuidlib.uuid4())},
        )
        assert result.status_code == 202, result.text
        body = result.json()
        assert body["dry_run"] is True
        assert body["overall"] in ("pass", "warn", "fail", "unknown")

        snapshot = (
            await client.get(f"/v1/integrations/github/repositories/{hermes['id']}/policy")
        ).json()
        assert snapshot["state"] == "evaluated"
        assert snapshot["dry_run"] is True
        assert len(snapshot["results"]) >= 10

        violations = (
            await client.get(f"/v1/integrations/github/repositories/{hermes['id']}/violations")
        ).json()
        assert isinstance(violations["violations"], list)

    # Every upstream call was a GET except the documented token mint.
    assert fake.calls, "the evaluation must actually talk to the (fake) provider"
    for call in fake.calls:
        method, path = call.split(" ", 1)
        assert method == "GET" or path.endswith("/access_tokens"), f"unexpected write call: {call}"

    async with engine.connect() as connection:
        state = (
            await connection.execute(
                text("SELECT onboarding_state FROM github_repositories WHERE external_id = :id"),
                {"id": HERMES_ID},
            )
        ).scalar_one()
    assert state == "ready"


async def test_provider_failures_degrade_and_never_pass(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)

    async with harness.api_client() as client:
        await deliver(client, "installation", installation_payload(), str(uuidlib.uuid4()))
        me = await harness.login(client, "user-owner")
        listing = (await client.get("/v1/integrations/github/repositories")).json()
        hermes = next(item for item in listing["repositories"] if item["external_id"] == HERMES_ID)

        fake.mode = "rate_limited"
        limited = await client.post(
            f"/v1/integrations/github/repositories/{hermes['id']}/reconcile",
            headers={"X-CSRF-Token": me["csrf_token"], "Idempotency-Key": str(uuidlib.uuid4())},
        )
        assert limited.status_code == 503

    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT onboarding_state, last_error_code FROM github_repositories "
                    "WHERE external_id = :id"
                ),
                {"id": HERMES_ID},
            )
        ).one()
    assert row[0] == "degraded", "a provider failure is DEGRADED, never READY"
    assert row[1] == "github_rate_limited"

    # A missing permission on one endpoint yields UNKNOWN, not PASS.
    fake.mode = "ok"
    fake.protection_status = 403
    async with harness.api_client() as client:
        me = await harness.login(client, "user-owner")
        listing = (await client.get("/v1/integrations/github/repositories")).json()
        hermes = next(item for item in listing["repositories"] if item["external_id"] == HERMES_ID)
        await client.post(
            f"/v1/integrations/github/repositories/{hermes['id']}/reconcile",
            headers={"X-CSRF-Token": me["csrf_token"], "Idempotency-Key": str(uuidlib.uuid4())},
        )
        snapshot = (
            await client.get(f"/v1/integrations/github/repositories/{hermes['id']}/policy")
        ).json()
    protection = next(
        item for item in snapshot["results"] if item["rule_id"] == "branch.protection.present"
    )
    assert protection["verdict"] == "unknown"
    assert "administration:read" in protection["observed"]


async def test_rbac_read_manage_separation_and_idor(engine: AsyncEngine, tmp_path: Path) -> None:
    harness, _ = github_harness(tmp_path)
    # Seed the standard user world, then the platform owner.
    users = await build_users(engine)
    harness.provider.users.update(users.provider.users)
    await _seed_admin(harness, engine)

    async with harness.api_client() as client:
        await deliver(client, "installation", installation_payload(), str(uuidlib.uuid4()))

    async with harness.api_client() as owner:
        me = await harness.login(owner, "user-owner")
        listing = (await owner.get("/v1/integrations/github/repositories")).json()
        assert len(listing["repositories"]) == 2
        repository_id = listing["repositories"][0]["id"]
        status = (await owner.get("/v1/integrations/github/status")).json()
        assert status["configuration_state"] == "configured"
        assert status["missing_operator_inputs"] == []
        # The response must never carry credential references or material.
        serialized = jsonlib.dumps(status)
        assert "private_key" not in serialized.replace("private_key_reference", "")
        assert WEBHOOK_SECRET not in serialized

    # A reader without integration.manage may look but not act.
    await login_all(harness, ["user-reader"])
    await make_role(harness, engine, "GitHub Reader S5A", ["project.view"])
    await grant(engine, harness, "user-reader", "GitHub Reader S5A", "organization", "root")
    async with harness.api_client() as reader:
        me = await harness.login(reader, "user-reader")
        assert (await reader.get("/v1/integrations/github/repositories")).status_code == 200
        denied = await reader.post(
            f"/v1/integrations/github/repositories/{repository_id}/reconcile",
            headers={"X-CSRF-Token": me["csrf_token"], "Idempotency-Key": str(uuidlib.uuid4())},
        )
        # Unmanageable is the same uniform 404 as unknown — no oracle.
        assert denied.status_code == 404
        assert (await reader.get("/v1/integrations/github/webhook-deliveries")).status_code == 403

    # A user with no integration visibility at all sees nothing and gets 404s.
    async with harness.api_client() as outsider:
        await harness.login(outsider, "user-b-only")
        empty = (await outsider.get("/v1/integrations/github/repositories")).json()
        assert empty["repositories"] == []
        assert (
            await outsider.get(f"/v1/integrations/github/repositories/{repository_id}")
        ).status_code == 404
        assert (
            await outsider.get(f"/v1/integrations/github/repositories/{repository_id}/policy")
        ).status_code == 404
        ghost = uuidlib.uuid4()
        assert (
            await outsider.get(f"/v1/integrations/github/repositories/{ghost}")
        ).status_code == 404

    # Unauthenticated callers reach nothing.
    async with harness.api_client() as anonymous:
        assert (await anonymous.get("/v1/integrations/github/repositories")).status_code == 401
        assert (await anonymous.get("/v1/integrations/github/status")).status_code == 401


async def test_status_reports_missing_operator_inputs_without_values(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """With nothing configured the surface is honestly NOT_CONFIGURED."""
    harness = build_harness(require_it_settings())
    await _seed_admin(harness, engine)
    async with harness.api_client() as client:
        await harness.login(client, "user-owner")
        status = (await client.get("/v1/integrations/github/status")).json()
    assert status["configuration_state"] == "not_configured"
    assert "feature_disabled" in status["missing_operator_inputs"]
    assert "private_key_reference" in status["missing_operator_inputs"]
    assert status["installations"] == 0

    # The webhook endpoint does not exist while the feature is off.
    async with harness.api_client() as client:
        body = jsonlib.dumps(installation_payload()).encode()
        response = await client.post(
            "/v1/integrations/github/webhook",
            content=body,
            headers=webhook_headers("installation", str(uuidlib.uuid4()), body),
        )
    assert response.status_code == 404


async def test_concurrent_duplicate_delivery_admits_exactly_one(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    import asyncio

    harness, _ = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    payload = installation_payload()
    body = jsonlib.dumps(payload).encode()
    delivery = str(uuidlib.uuid4())

    async def attempt() -> int:
        async with harness.api_client() as client:
            response = await client.post(
                "/v1/integrations/github/webhook",
                content=body,
                headers=webhook_headers("installation", delivery, body),
            )
            return response.json().get("status", "error")

    outcomes = await asyncio.gather(*(attempt() for _ in range(4)))
    assert outcomes.count("processed") == 1, outcomes
    assert outcomes.count("duplicate") == 3, outcomes

    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text("SELECT count(*) FROM github_webhook_deliveries WHERE delivery_id = :id"),
                {"id": delivery},
            )
        ).scalar_one()
    assert int(rows) == 1


async def test_audit_records_are_written_without_secrets(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, _ = github_harness(tmp_path)
    await _seed_admin(harness, engine)
    body = jsonlib.dumps(installation_payload()).encode()

    async with harness.api_client() as client:
        await client.post(
            "/v1/integrations/github/webhook",
            content=body,
            headers=webhook_headers("installation", str(uuidlib.uuid4()), body),
        )
        # An invalid signature must also leave a trace.
        await client.post(
            "/v1/integrations/github/webhook",
            content=body,
            headers={
                "X-Hub-Signature-256": "sha256=" + "0" * 64,
                "X-GitHub-Delivery": str(uuidlib.uuid4()),
                "X-GitHub-Event": "installation",
            },
        )

    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT action, result, metadata::text FROM audit_events "
                    "WHERE action LIKE 'github.%' ORDER BY occurred_at"
                )
            )
        ).all()
    actions = {row[0] for row in rows}
    assert "github.webhook.installation" in actions
    assert "github.webhook.rejected" in actions
    for row in rows:
        assert WEBHOOK_SECRET not in row[2]
        assert "ghs_" not in row[2]
        assert "PRIVATE KEY" not in row[2]


async def test_redelivery_after_reconciliation_does_not_regress_a_ready_repository(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    """A webhook says a repository EXISTS — not that our history is void.

    Regression: the announcement path used to derive every repository as
    "not yet reconciled", which pushed an already-READY row back to
    DISCOVERED. That transition is illegal, so a perfectly valid GitHub
    re-delivery turned into a 500 and GitHub would have retried it forever.
    """
    harness, _fake = github_harness(tmp_path)
    await _seed_admin(harness, engine)

    async with harness.api_client() as client:
        await deliver(client, "installation", installation_payload(), str(uuidlib.uuid4()))
        me = await harness.login(client, "user-owner")
        listing = (await client.get("/v1/integrations/github/repositories")).json()
        hermes = next(item for item in listing["repositories"] if item["external_id"] == HERMES_ID)
        reconciled = await client.post(
            f"/v1/integrations/github/repositories/{hermes['id']}/reconcile",
            headers={"X-CSRF-Token": me["csrf_token"], "Idempotency-Key": str(uuidlib.uuid4())},
        )
        assert reconciled.status_code == 202, reconciled.text

        async with engine.connect() as connection:
            before = (
                await connection.execute(
                    text(
                        "SELECT onboarding_state FROM github_repositories WHERE external_id = :id"
                    ),
                    {"id": HERMES_ID},
                )
            ).scalar_one()
        assert before == "ready"

        async with engine.connect() as connection:
            conflicts_before = int(
                (
                    await connection.execute(
                        text(
                            "SELECT count(*) FROM audit_events "
                            "WHERE action = 'github.repository.state_conflict'"
                        )
                    )
                ).scalar_one()
            )

        # A brand-new delivery announcing the same repositories plus the
        # gated one, so both the "don't regress" and "gate still wins"
        # rules are exercised by the same announcement.
        announced = installation_payload(
            repositories=[
                {
                    "id": HERMES_ID,
                    "node_id": "R_hermes",
                    "name": "Hermes",
                    "full_name": "Duosis-Developer-Team/Hermes",
                    "private": True,
                },
                {
                    "id": DATALAKE_ID,
                    "node_id": "R_datalake",
                    "name": "Datalake-Platform-GUI",
                    "full_name": "Duosis-Developer-Team/Datalake-Platform-GUI",
                    "private": True,
                },
            ]
        )
        again = await deliver(client, "installation", announced, str(uuidlib.uuid4()))
        assert again.status_code == 202, again.text

        # No state-machine conflict was recorded: the re-announcement was a
        # legal no-op, not a refusal the code quietly swallowed. Measured as
        # a DELTA — audit is append-only and shared across the suite, so an
        # absolute count says nothing about what this delivery did.
        async with engine.connect() as connection:
            conflicts_after = int(
                (
                    await connection.execute(
                        text(
                            "SELECT count(*) FROM audit_events "
                            "WHERE action = 'github.repository.state_conflict'"
                        )
                    )
                ).scalar_one()
            )
        assert conflicts_after == conflicts_before

        async with engine.connect() as connection:
            rows = dict(
                (
                    await connection.execute(
                        text(
                            "SELECT external_id, onboarding_state FROM github_repositories "
                            "WHERE external_id = ANY(:ids)"
                        ),
                        {"ids": [HERMES_ID, DATALAKE_ID]},
                    )
                ).all()
            )
    # The re-announcement is a legal transition, not the 500 this test was
    # written for, and it does not push the repository backwards to
    # DISCOVERED. It DOES leave it degraded: a webhook is a notification,
    # so evidence gathered before it is no longer current (fix gate 4 §3).
    assert rows[HERMES_ID] == "degraded"
    assert rows[DATALAKE_ID] == "blocked"
