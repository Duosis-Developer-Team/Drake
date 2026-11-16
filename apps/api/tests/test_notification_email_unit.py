"""E-mail delivery through Microsoft Graph (ADR-0030): composition, the
registry's validation, and one send attempt's classification.

No database, no network. Graph and the Microsoft login endpoint are an
`httpx.MockTransport` that records what arrived; no request leaves the
process, and no real tenant, client or secret exists here.
"""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from drake_api.notifications.email import (
    IDEMPOTENCY_MESSAGE_HEADER,
    clear_token_cache,
    compose_message,
    send_email,
)
from drake_api.settings import EmailDestination, GraphMailSender, Settings, WebhookDestination
from pydantic import ValidationError

LOGIN = "https://login.example.test"
GRAPH = "https://graph.example.test"
SECRET = "test-client-secret-value"

PAYLOAD: dict[str, Any] = {
    "schema_version": 1,
    "event_type": "opened",
    "occurred_at": "2026-11-16T09:00:00+00:00",
    "incident": {
        "id": "7d1f3f6e-0000-4000-8000-000000000001",
        "state": "open",
        "severity": "critical",
        "title": "core-service is failing requests",
        "primary_reason": "error_rate_critical",
        "url": "/incidents/7d1f3f6e-0000-4000-8000-000000000001",
    },
    "service": {
        "project_key": "hermes",
        "environment_key": "dev",
        "service_key": "core-service",
        "cluster_ref": "duosis-prod-1",
        "namespace": "hermes-dev",
        "workload_name": "core-service",
    },
}


@pytest.fixture(autouse=True)
def _fresh_tokens() -> Any:
    clear_token_cache()
    yield
    clear_token_cache()


@pytest.fixture
def sender(tmp_path: Path) -> GraphMailSender:
    secret = tmp_path / "client-secret"
    secret.write_text(SECRET + "\n", encoding="utf-8")
    return GraphMailSender(
        tenant_id="tenant-1",
        client_id="client-1",
        client_secret_file=str(secret),
        sender="alerts@example.com",
        login_base_url=LOGIN,
        graph_base_url=GRAPH,
    )


DESTINATION = EmailDestination(
    recipients=["one@example.com", "two@example.com"], display_name="Test"
)


class FakeGraph:
    """The login endpoint and Graph's sendMail, recording every request."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.token_status = 200
        self.send_status = 202
        self.send_retry_after: str | None = None
        self.expires_in = 3600
        self.timeout_on_send = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("/oauth2/v2.0/token"):
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_client"})
            return httpx.Response(
                200,
                json={
                    "access_token": f"token-{len(self.token_requests)}",
                    "expires_in": self.expires_in,
                },
            )
        if self.timeout_on_send:
            raise httpx.ReadTimeout("graph is slow")
        headers = {"Retry-After": self.send_retry_after} if self.send_retry_after else {}
        return httpx.Response(self.send_status, headers=headers)

    @property
    def token_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path.endswith("/token")]

    @property
    def send_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path.endswith("/sendMail")]

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


def settings(**update: Any) -> Settings:
    return Settings(env="test", public_origin="https://drake.example.test", **update)


async def send(graph: FakeGraph, sender: GraphMailSender, payload: dict[str, Any] = PAYLOAD):
    return await send_email(
        DESTINATION,
        sender,
        settings(),
        payload=payload,
        idempotency_key="idem-123",
        transport=graph.transport,
    )


# --- composition -------------------------------------------------------------


def test_the_subject_names_severity_event_and_title() -> None:
    message = compose_message(PAYLOAD)
    assert message.subject == "[Drake][CRITICAL] Incident opened: core-service is failing requests"


def test_the_body_says_where_and_links_absolutely() -> None:
    message = compose_message(PAYLOAD, public_origin="https://drake.example.test/")
    assert "Service:   hermes/dev/core-service" in message.body
    assert "Workload:  duosis-prod-1/hermes-dev/core-service" in message.body
    assert (
        "Open in Drake: https://drake.example.test/incidents/7d1f3f6e-0000-4000-8000-000000000001"
        in message.body
    )


def test_a_relative_link_stays_relative_without_an_origin() -> None:
    message = compose_message(PAYLOAD)
    assert "Open in Drake: /incidents/" in message.body


def test_a_resolution_reads_as_a_resolution() -> None:
    message = compose_message({**PAYLOAD, "event_type": "auto_resolved"})
    assert message.subject.startswith("[Drake][CRITICAL] Incident resolved:")
    assert "resolved this incident automatically" in message.body


def test_a_title_cannot_inject_a_header_line() -> None:
    hostile = {**PAYLOAD, "incident": {**PAYLOAD["incident"], "title": "x\r\nBcc: a@b.c"}}
    message = compose_message(hostile)
    assert "\r" not in message.subject and "\n" not in message.subject
    assert message.subject.endswith("x Bcc: a@b.c")


def test_composition_is_bounded() -> None:
    long = {**PAYLOAD, "incident": {**PAYLOAD["incident"], "title": "y" * 5000}}
    message = compose_message(long)
    assert len(message.subject) <= 230
    assert len(message.body) <= 8000


# --- the registry ------------------------------------------------------------


@pytest.mark.parametrize(
    "recipients",
    [
        [],
        [f"p{i}@example.com" for i in range(11)],
        ["Name <a@example.com>"],
        ["a@example.com, b@example.com"],
        ["a@example.com\nBcc: c@example.com"],
        ["no-domain@"],
        ["a@example.com", "A@example.com"],
    ],
)
def test_a_destination_takes_only_a_short_list_of_plain_addresses(recipients: list[str]) -> None:
    with pytest.raises(ValidationError):
        EmailDestination(recipients=recipients)


def test_a_sender_must_be_a_plain_address(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        GraphMailSender(
            tenant_id="t", client_id="c", client_secret_file="/x", sender="Drake <a@b.com>"
        )


def test_webhook_and_email_keys_share_one_namespace() -> None:
    with pytest.raises(ValidationError, match="unique across webhooks and e-mail"):
        settings(
            notification_webhooks={"ops": WebhookDestination(url="https://hooks.example.test")},
            notification_emails={"ops": DESTINATION},
        )


def test_an_email_key_has_the_database_shape() -> None:
    with pytest.raises(ValidationError, match="wrong shape"):
        settings(notification_emails={"Ops Team": DESTINATION})


def test_the_registry_loads_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "DRAKE_NOTIFICATION_EMAILS",
        json.dumps({"drake-test": {"display_name": "T", "recipients": ["one@example.com"]}}),
    )
    monkeypatch.setenv(
        "DRAKE_NOTIFICATION_EMAIL_SENDER",
        json.dumps(
            {
                "tenant_id": "t",
                "client_id": "c",
                "client_secret_file": "/etc/drake/email/client-secret",
                "sender": "alerts@example.com",
            }
        ),
    )
    loaded = Settings(env="test")
    assert loaded.notification_emails["drake-test"].recipients == ["one@example.com"]
    assert loaded.notification_email_sender is not None
    assert loaded.notification_email_sender.graph_base_url == "https://graph.microsoft.com"


def test_plaintext_microsoft_endpoints_are_refused_outside_local(sender: GraphMailSender) -> None:
    from test_production_edge_unit import _production

    _production(notification_email_sender=sender).validate_runtime_security()
    for field in ("login_base_url", "graph_base_url"):
        plain = sender.model_copy(update={field: "http://endpoint.example.test"})
        with pytest.raises(RuntimeError, match=f"{field} must use https"):
            _production(notification_email_sender=plain).validate_runtime_security()


# --- one attempt ---------------------------------------------------------------


async def test_a_send_fetches_a_token_then_mails_every_recipient(
    sender: GraphMailSender,
) -> None:
    graph = FakeGraph()
    result = await send(graph, sender)

    assert (result.outcome, result.http_status, result.error_code) == ("delivered", 202, None)
    token_request = graph.token_requests[0]
    assert token_request.url == httpx.URL(f"{LOGIN}/tenant-1/oauth2/v2.0/token")
    form = dict(httpx.QueryParams(token_request.content.decode()))
    assert form["grant_type"] == "client_credentials"
    assert form["client_secret"] == SECRET
    assert form["scope"] == "https://graph.microsoft.com/.default"

    mail = graph.send_requests[0]
    assert mail.url == httpx.URL(f"{GRAPH}/v1.0/users/alerts@example.com/sendMail")
    assert mail.headers["Authorization"] == "Bearer token-1"
    body = json.loads(mail.content)
    assert body["saveToSentItems"] is False
    message = body["message"]
    assert [r["emailAddress"]["address"] for r in message["toRecipients"]] == [
        "one@example.com",
        "two@example.com",
    ]
    assert message["body"]["contentType"] == "Text"
    assert message["internetMessageHeaders"] == [
        {"name": IDEMPOTENCY_MESSAGE_HEADER, "value": "idem-123"}
    ]
    assert "https://drake.example.test/incidents/" in message["body"]["content"]


async def test_the_secret_goes_only_to_the_login_endpoint(sender: GraphMailSender) -> None:
    graph = FakeGraph()
    await send(graph, sender)
    for request in graph.send_requests:
        assert SECRET not in request.content.decode()
        assert SECRET not in str(request.headers)


async def test_a_token_is_reused_until_it_nears_expiry(sender: GraphMailSender) -> None:
    graph = FakeGraph()
    await send(graph, sender)
    await send(graph, sender)
    assert len(graph.token_requests) == 1
    assert len(graph.send_requests) == 2


async def test_a_short_lived_token_is_not_cached(sender: GraphMailSender) -> None:
    graph = FakeGraph()
    graph.expires_in = 60
    await send(graph, sender)
    await send(graph, sender)
    assert len(graph.token_requests) == 2


async def test_wrong_credentials_are_terminal_and_nothing_is_mailed(
    sender: GraphMailSender,
) -> None:
    graph = FakeGraph()
    graph.token_status = 401
    result = await send(graph, sender)
    assert (result.outcome, result.http_status, result.error_code) == (
        "terminal",
        401,
        "email_auth_refused",
    )
    assert graph.send_requests == []


async def test_an_unavailable_login_endpoint_is_retryable(sender: GraphMailSender) -> None:
    graph = FakeGraph()
    graph.token_status = 503
    result = await send(graph, sender)
    assert (result.outcome, result.error_code) == ("retryable", "email_auth_unavailable")


async def test_an_unreadable_secret_is_retryable_and_calls_nobody(
    sender: GraphMailSender,
) -> None:
    graph = FakeGraph()
    missing = sender.model_copy(update={"client_secret_file": "/nonexistent/client-secret"})
    result = await send(graph, missing)
    assert (result.outcome, result.error_code) == ("retryable", "email_secret_unavailable")
    assert graph.requests == []


@pytest.mark.parametrize(
    ("status", "outcome"),
    [
        (403, "terminal"),
        (400, "terminal"),
        (404, "terminal"),
        (429, "retryable"),
        (503, "retryable"),
    ],
)
async def test_graph_statuses_are_classified(
    sender: GraphMailSender, status: int, outcome: str
) -> None:
    graph = FakeGraph()
    graph.send_status = status
    result = await send(graph, sender)
    assert (result.outcome, result.http_status, result.error_code) == (
        outcome,
        status,
        f"http_{status}",
    )


async def test_a_throttled_send_honours_a_small_retry_after(sender: GraphMailSender) -> None:
    graph = FakeGraph()
    graph.send_status = 429
    graph.send_retry_after = "20"
    result = await send(graph, sender)
    assert result.retry_after_seconds == 20


async def test_a_rejected_token_is_dropped_and_replaced_next_time(
    sender: GraphMailSender,
) -> None:
    graph = FakeGraph()
    graph.send_status = 401
    first = await send(graph, sender)
    assert first.outcome == "retryable"
    graph.send_status = 202
    second = await send(graph, sender)
    assert second.outcome == "delivered"
    assert len(graph.token_requests) == 2
    assert graph.send_requests[-1].headers["Authorization"] == "Bearer token-2"


async def test_a_slow_graph_is_retryable_with_a_safe_code(sender: GraphMailSender) -> None:
    graph = FakeGraph()
    graph.timeout_on_send = True
    result = await send(graph, sender)
    assert (result.outcome, result.error_code) == ("retryable", "timeout")
