"""Credential validation, token scoping, response bounds, and the real
GitHub ruleset contract.

CTO fix-gate regressions §9 to §13. The GitHub payloads here are shaped
like the documented responses for API version 2022-11-28, including the
part that caused the defect: the ruleset LIST endpoint returns summaries
with no `rules` member at all.
"""

import datetime as dt
import json

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from drake_api.github_app.auth import (
    GitHubAppAuth,
    GitHubAuthError,
    InstallationToken,
    InstallationTokenCache,
    TokenScope,
    missing_permissions,
    validate_credentials,
)
from drake_api.github_app.client import (
    GitHubClient,
    GitHubContractError,
)
from drake_api.github_app.policy import PolicyInputs, evaluate
from drake_api.testing import make_settings

# --- helpers -------------------------------------------------------------


def _write_rsa_key(path) -> str:  # type: ignore[no-untyped-def]
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    target = path / "app-key.pem"
    target.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return str(target)


def _write_secret(path, content: str = "a-local-only-webhook-secret") -> str:  # type: ignore[no-untyped-def]
    target = path / "webhook-secret"
    target.write_text(content)
    return str(target)


def _settings(**overrides):  # type: ignore[no-untyped-def]
    base = {
        "github_app_enabled": True,
        "github_app_client_id": "Iv1.localtest",
        "github_api_base_url": "http://127.0.0.1:59097",
    }
    base.update(overrides)
    return make_settings().model_copy(update=base)


def _verdict(inputs: PolicyInputs, rule_id: str) -> str:
    return next(item for item in evaluate(inputs).results if item.rule_id == rule_id).verdict


# --- §9 startup credential validation -----------------------------------


def test_disabled_feature_never_opens_a_secret_file(tmp_path) -> None:  # type: ignore[no-untyped-def]
    settings = _settings(
        github_app_enabled=False,
        github_app_private_key_file=str(tmp_path / "does-not-exist.pem"),
        github_webhook_secret_file=str(tmp_path / "nope"),
    )
    # No exception: a disabled integration reads nothing at all.
    validate_credentials(settings)


def test_valid_material_passes(tmp_path) -> None:  # type: ignore[no-untyped-def]
    validate_credentials(
        _settings(
            github_app_private_key_file=_write_rsa_key(tmp_path),
            github_webhook_secret_file=_write_secret(tmp_path),
        )
    )


def test_a_missing_private_key_file_is_refused_at_startup(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(GitHubAuthError) as refusal:
        validate_credentials(
            _settings(
                github_app_private_key_file=str(tmp_path / "absent.pem"),
                github_webhook_secret_file=_write_secret(tmp_path),
            )
        )
    assert "private key" in str(refusal.value)


def test_a_non_pem_private_key_is_refused_at_startup(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A path that exists proves nothing about what is in it."""
    bogus = tmp_path / "app-key.pem"
    bogus.write_text("-----BEGIN PRIVATE KEY-----\nnot actually base64 DER\n")
    with pytest.raises(GitHubAuthError):
        validate_credentials(
            _settings(
                github_app_private_key_file=str(bogus),
                github_webhook_secret_file=_write_secret(tmp_path),
            )
        )


def test_a_non_rsa_private_key_is_refused(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """GitHub signs RS256; an Ed25519 key would fail at the first mint."""
    key = ed25519.Ed25519PrivateKey.generate()
    target = tmp_path / "ed25519.pem"
    target.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    with pytest.raises(GitHubAuthError) as refusal:
        validate_credentials(
            _settings(
                github_app_private_key_file=str(target),
                github_webhook_secret_file=_write_secret(tmp_path),
            )
        )
    assert "RSA" in str(refusal.value)


def test_an_empty_webhook_secret_is_refused(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(GitHubAuthError) as refusal:
        validate_credentials(
            _settings(
                github_app_private_key_file=_write_rsa_key(tmp_path),
                github_webhook_secret_file=_write_secret(tmp_path, "   \n"),
            )
        )
    assert "empty" in str(refusal.value)


def test_a_missing_webhook_secret_file_is_refused(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(GitHubAuthError):
        validate_credentials(
            _settings(
                github_app_private_key_file=_write_rsa_key(tmp_path),
                github_webhook_secret_file=str(tmp_path / "absent"),
            )
        )


def test_missing_app_identity_is_refused(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(GitHubAuthError):
        validate_credentials(
            _settings(
                github_app_client_id="",
                github_app_id="",
                github_app_private_key_file=_write_rsa_key(tmp_path),
                github_webhook_secret_file=_write_secret(tmp_path),
            )
        )


@pytest.mark.parametrize("ttl", [0, -1, 601, 100_000])
def test_jwt_ttl_outside_one_to_six_hundred_is_refused(tmp_path, ttl: int) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(GitHubAuthError):
        validate_credentials(
            _settings(
                github_jwt_ttl_seconds=ttl,
                github_app_private_key_file=_write_rsa_key(tmp_path),
                github_webhook_secret_file=_write_secret(tmp_path),
            )
        )


@pytest.mark.parametrize("buffer_seconds", [0, 5, 100_000])
def test_refresh_buffer_outside_the_safe_range_is_refused(tmp_path, buffer_seconds: int) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(GitHubAuthError):
        validate_credentials(
            _settings(
                github_token_refresh_buffer_seconds=buffer_seconds,
                github_app_private_key_file=_write_rsa_key(tmp_path),
                github_webhook_secret_file=_write_secret(tmp_path),
            )
        )


@pytest.mark.parametrize("limit", [0, 16, 100 * 1024 * 1024])
def test_webhook_body_limit_outside_the_safe_range_is_refused(tmp_path, limit: int) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(GitHubAuthError):
        validate_credentials(
            _settings(
                github_webhook_max_body_bytes=limit,
                github_app_private_key_file=_write_rsa_key(tmp_path),
                github_webhook_secret_file=_write_secret(tmp_path),
            )
        )


def test_plaintext_api_url_is_refused_outside_local(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(GitHubAuthError):
        validate_credentials(
            _settings(
                env="prod",
                github_api_base_url="http://api.github.com",
                github_app_private_key_file=_write_rsa_key(tmp_path),
                github_webhook_secret_file=_write_secret(tmp_path),
            )
        )


def test_a_broken_credential_message_never_quotes_the_file(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The refusal is logged, so it must not carry key material."""
    secret_like = "-----BEGIN PRIVATE KEY-----\nSUPERSECRETMATERIAL\n-----END PRIVATE KEY-----\n"
    broken = tmp_path / "broken.pem"
    broken.write_text(secret_like)
    with pytest.raises(GitHubAuthError) as refusal:
        validate_credentials(
            _settings(
                github_app_private_key_file=str(broken),
                github_webhook_secret_file=_write_secret(tmp_path),
            )
        )
    rendered = repr(refusal.value) + str(refusal.value)
    cause = refusal.value.__cause__
    rendered += repr(cause) + str(cause)
    assert "SUPERSECRETMATERIAL" not in rendered


# --- §12 token scope and cache key --------------------------------------


def _token(expires_in: int = 3600, **overrides) -> InstallationToken:  # type: ignore[no-untyped-def]
    payload = {
        "token": "ghs_" + "a" * 80,
        "expires_at": dt.datetime.now(dt.UTC) + dt.timedelta(seconds=expires_in),
        "permissions": {"metadata": "read"},
    }
    payload.update(overrides)
    return InstallationToken(**payload)  # type: ignore[arg-type]


def test_a_token_for_one_repository_is_not_reused_for_another() -> None:
    cache = InstallationTokenCache()
    scope_a = TokenScope.build(1, [111], {"metadata": "read"})
    scope_b = TokenScope.build(1, [222], {"metadata": "read"})
    cache.put(scope_a, _token())
    assert cache.get(scope_a) is not None
    assert cache.get(scope_b) is None, "a token scoped to repo A must not answer for repo B"


def test_a_narrow_token_is_not_reused_for_a_wider_request() -> None:
    cache = InstallationTokenCache()
    narrow = TokenScope.build(1, [111], {"metadata": "read"})
    wider = TokenScope.build(1, [111], {"metadata": "read", "administration": "read"})
    cache.put(narrow, _token())
    assert cache.get(wider) is None


def test_a_wide_token_is_not_silently_reused_where_a_narrow_one_was_asked_for() -> None:
    cache = InstallationTokenCache()
    wide = TokenScope.build(1, [], {"metadata": "read", "administration": "read"})
    narrow = TokenScope.build(1, [111], {"metadata": "read"})
    cache.put(wide, _token())
    assert cache.get(narrow) is None


def test_scope_is_order_independent() -> None:
    left = TokenScope.build(1, [222, 111], {"actions": "read", "metadata": "read"})
    right = TokenScope.build(1, [111, 222, 111], {"metadata": "read", "actions": "read"})
    assert left == right, "the same authority must hit the same cache entry"


def test_installations_do_not_share_cache_entries() -> None:
    cache = InstallationTokenCache()
    cache.put(TokenScope.build(1, [111], {"metadata": "read"}), _token())
    assert cache.get(TokenScope.build(2, [111], {"metadata": "read"})) is None


def test_invalidate_drops_every_scope_of_one_installation() -> None:
    cache = InstallationTokenCache()
    first = TokenScope.build(7, [111], {"metadata": "read"})
    second = TokenScope.build(7, [222], {"metadata": "read"})
    other = TokenScope.build(8, [333], {"metadata": "read"})
    for scope in (first, second, other):
        cache.put(scope, _token())
    cache.invalidate(7)
    assert cache.get(first) is None and cache.get(second) is None
    assert cache.get(other) is not None


def test_a_token_inside_the_refresh_buffer_is_treated_as_expired() -> None:
    cache = InstallationTokenCache(refresh_buffer_seconds=300)
    scope = TokenScope.build(1, [111], {"metadata": "read"})
    cache.put(scope, _token(expires_in=120))
    assert cache.get(scope) is None


def test_missing_permissions_reports_the_shortfall_without_escalating() -> None:
    required = {"metadata": "read", "administration": "read", "actions": "read"}
    granted = {"metadata": "read", "actions": "read"}
    assert missing_permissions(granted, required) == ["administration"]
    assert missing_permissions(required, required) == []
    # A write grant satisfies a read requirement; the reverse never happens.
    assert missing_permissions({"metadata": "write"}, {"metadata": "read"}) == []


# --- §13 outbound response and pagination bounds ------------------------


def _client(handler, **overrides):  # type: ignore[no-untyped-def]
    settings = _settings(**overrides)
    auth = GitHubAppAuth(settings)
    return GitHubClient(settings, auth, transport=httpx.MockTransport(handler))


async def test_an_oversized_streamed_response_is_refused_not_buffered() -> None:
    """The budget must bite while streaming, not after the fact."""
    served = {"bytes": 0}
    chunk = b"x" * (256 * 1024)

    async def stream():  # type: ignore[no-untyped-def]
        # Far past the 4 MiB budget if it were all buffered.
        for _ in range(64):
            served["bytes"] += len(chunk)
            yield chunk

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=stream())

    client = _client(handler)
    with pytest.raises(GitHubContractError) as refusal:
        await client.get_repository(_token(), "o", "r")
    assert "size budget" in str(refusal.value)
    assert served["bytes"] <= 5 * 1024 * 1024, (
        f"served {served['bytes']} bytes: the client kept reading past its own budget"
    )


async def test_hitting_the_page_cap_with_a_full_page_is_an_error_not_a_short_answer() -> None:
    """A truncated listing must never look like a complete one.

    Returning what we managed to read would let a rule conclude "no
    violation found" from an answer that was cut off.
    """
    pages = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        pages["n"] += 1
        # Always a full page: there is always more to fetch.
        return httpx.Response(
            200,
            json={"workflows": [{"name": f"w{index}", "state": "active"} for index in range(100)]},
        )

    client = _client(handler)
    with pytest.raises(GitHubContractError) as refusal:
        await client.list_workflows(_token(), "o", "r")
    assert "incomplete" in str(refusal.value)
    assert pages["n"] <= 20, "pagination must stay bounded"


async def test_a_short_final_page_completes_normally() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        count = 100 if page == 1 else 3
        return httpx.Response(
            200,
            json={"workflows": [{"name": f"w{page}-{i}", "state": "active"} for i in range(count)]},
        )

    workflows = await _client(handler).list_workflows(_token(), "o", "r")
    assert len(workflows) == 103


# --- §10 the real ruleset contract --------------------------------------

# GitHub's documented shape for `GET /repos/{owner}/{repo}/rulesets`:
# summary objects. Note what is NOT here — a `rules` member.
RULESET_LIST_RESPONSE = [
    {
        "id": 42,
        "name": "super cool ruleset",
        "target": "branch",
        "source_type": "Repository",
        "source": "monalisa/my-repo",
        "enforcement": "active",
        "node_id": "RRS_lACkVXNlcgQ",
        "_links": {"self": {"href": "https://api.github.com/repos/o/r/rulesets/42"}},
        "created_at": "2023-07-15T08:43:03Z",
        "updated_at": "2023-08-23T16:29:47Z",
    }
]

# And for `GET /repos/{owner}/{repo}/rules/branches/{branch}`: the rules
# actually in effect, already resolved across repository and organization
# rulesets and already filtered to active enforcement.
BRANCH_RULES_RESPONSE = [
    {
        "type": "commit_message_pattern",
        "ruleset_source_type": "Repository",
        "ruleset_source": "monalisa/my-repo",
        "ruleset_id": 42,
        "parameters": {"operator": "starts_with", "pattern": "issue"},
    },
    {
        "type": "pull_request",
        "ruleset_source_type": "Organization",
        "ruleset_source": "monalisa",
        "ruleset_id": 73,
        "parameters": {"required_approving_review_count": 2},
    },
]


def test_a_ruleset_summary_carries_no_rules_member() -> None:
    """Guards the assumption the defect was built on.

    If a future fixture starts inventing a `rules` array here, this fails
    and the real contract gets restated rather than quietly re-broken.
    """
    for summary in RULESET_LIST_RESPONSE:
        assert "rules" not in summary


async def test_the_client_reads_effective_rules_from_the_documented_endpoint() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/repos/o/r/rules/branches/main":
            return httpx.Response(200, json=BRANCH_RULES_RESPONSE)
        return httpx.Response(200, json=RULESET_LIST_RESPONSE)

    rules = await _client(handler).get_branch_rules(_token(), "o", "r", "main")
    assert seen == ["/repos/o/r/rules/branches/main"]
    assert {rule["type"] for rule in rules} == {"commit_message_pattern", "pull_request"}
    # Organization-level rulesets are included by the endpoint itself.
    assert any(rule["ruleset_source_type"] == "Organization" for rule in rules)


def test_effective_rules_from_an_organization_ruleset_count_as_protection() -> None:
    inputs = PolicyInputs(
        full_name="o/r",
        default_branch="main",
        protection=None,
        branch_rules=BRANCH_RULES_RESPONSE,
        workflows=[],
        environments=[],
    )
    assert _verdict(inputs, "branch.protection.present") == "pass"
    assert _verdict(inputs, "branch.pull_request.required") == "pass"


def test_an_empty_effective_rules_answer_is_a_real_fail() -> None:
    """Rulesets that exist but do not apply simply are not in the answer.

    Tag-targeted rulesets, disabled rulesets, and rulesets scoped to other
    branches are all filtered out by the endpoint, so an empty list is a
    genuine "nothing protects this branch".
    """
    inputs = PolicyInputs(
        full_name="o/r", default_branch="main", protection=None, branch_rules=[], workflows=[]
    )
    assert _verdict(inputs, "branch.protection.present") == "fail"


def test_unreadable_effective_rules_are_unknown_not_fail() -> None:
    inputs = PolicyInputs(
        full_name="o/r",
        default_branch="main",
        protection=None,
        protection_error="missing permission (administration:read)",
        branch_rules=None,
        branch_rules_error="missing permission (administration:read)",
        workflows=[],
    )
    assert _verdict(inputs, "branch.protection.present") == "unknown"


# --- §11 partial evidence is never a PASS -------------------------------


def _environment_inputs(details: dict, errors: dict) -> PolicyInputs:  # type: ignore[type-arg]
    return PolicyInputs(
        full_name="o/r",
        default_branch="main",
        protection={
            "required_pull_request_reviews": {"required_approving_review_count": 1},
            "allow_force_pushes": {"enabled": False},
            "allow_deletions": {"enabled": False},
            "required_status_checks": {"strict": True, "contexts": ["ci"]},
            "enforce_admins": {"enabled": True},
        },
        branch_rules=[],
        workflows=[{"name": "ci", "state": "active", "path": "ci.yml"}],
        environments=[{"name": name} for name in sorted({*details, *errors})],
        environment_details=details,
        environment_errors=errors,
    )


APPROVED = {
    "protection_rules": [{"type": "required_reviewers"}],
    "deployment_branch_policy": {"protected_branches": True},
}
UNAPPROVED = {"protection_rules": [], "deployment_branch_policy": None}


def test_one_approved_plus_one_unreadable_environment_is_unknown() -> None:
    """The exact regression: a compliant sibling must not cover a gap.

    A PASS here claims every production environment is gated. One
    environment we could not read makes that claim unsupportable.
    """
    inputs = _environment_inputs(
        {"production": APPROVED}, {"production-eu": "missing permission (actions:read)"}
    )
    assert _verdict(inputs, "deploy.production.approval_required") == "unknown"
    assert _verdict(inputs, "deploy.production.branch_mapping") == "unknown"


def test_a_known_violation_still_outranks_an_unreadable_sibling() -> None:
    """FAIL is evidence we have; it does not degrade into UNKNOWN."""
    inputs = _environment_inputs(
        {"production": UNAPPROVED}, {"production-eu": "github_rate_limited"}
    )
    assert _verdict(inputs, "deploy.production.approval_required") == "fail"


def test_all_readable_and_approved_is_a_pass() -> None:
    inputs = _environment_inputs({"production": APPROVED, "production-eu": APPROVED}, {})
    assert _verdict(inputs, "deploy.production.approval_required") == "pass"
    assert _verdict(inputs, "deploy.production.branch_mapping") == "pass"


def test_all_unreadable_is_unknown() -> None:
    inputs = _environment_inputs(
        {}, {"production": "github_unavailable", "production-eu": "github_forbidden"}
    )
    assert _verdict(inputs, "deploy.production.approval_required") == "unknown"


@pytest.mark.parametrize(
    "reason",
    ["github_rate_limited", "github_unavailable", "missing permission (actions:read)", "timeout"],
)
def test_every_partial_failure_mode_keeps_the_aggregate_off_pass(reason: str) -> None:
    inputs = _environment_inputs({"production": APPROVED}, {"production-eu": reason})
    verdict = _verdict(inputs, "deploy.production.approval_required")
    assert verdict != "pass", f"{reason} must not be absorbed into a PASS"


def test_the_unreadable_reason_reaches_the_evidence_without_secrets() -> None:
    inputs = _environment_inputs(
        {"production": APPROVED}, {"production-eu": "missing permission (actions:read)"}
    )
    result = next(
        item
        for item in evaluate(inputs).results
        if item.rule_id == "deploy.production.approval_required"
    )
    assert "actions:read" in result.observed
    serialized = json.dumps({"observed": result.observed, "evidence": result.evidence})
    assert "ghs_" not in serialized and "PRIVATE KEY" not in serialized
