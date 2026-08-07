"""Policy engine and onboarding state-machine unit tests.

The engine is pure, so every case here is deterministic and needs no
network. The governing invariant under test: an unreadable answer is
UNKNOWN, never PASS.
"""

import pytest
from drake_api.github_app import onboarding
from drake_api.github_app.policy import (
    DEFAULT_PROFILE,
    RULE_CATALOG,
    PolicyInputs,
    evaluate,
)


def _verdict(inputs: PolicyInputs, rule_id: str, profile: str = DEFAULT_PROFILE) -> str:
    evaluation = evaluate(inputs, profile)
    for result in evaluation.results:
        if result.rule_id == rule_id:
            return result.verdict
    raise AssertionError(f"rule {rule_id} was not evaluated")


def _healthy_protection() -> dict:
    return {
        "required_pull_request_reviews": {"required_approving_review_count": 1},
        "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False},
        "required_status_checks": {"strict": True, "contexts": ["build", "test"]},
        "enforce_admins": {"enabled": True},
    }


def _healthy_inputs() -> PolicyInputs:
    return PolicyInputs(
        full_name="Duosis-Developer-Team/Hermes",
        default_branch="main",
        protection=_healthy_protection(),
        branch_rules=[],
        workflows=[
            {"name": "build", "path": ".github/workflows/build.yml", "state": "active"},
            {"name": "test suite", "path": ".github/workflows/test.yml", "state": "active"},
            {"name": "codeql", "path": ".github/workflows/codeql.yml", "state": "active"},
        ],
        environments=[{"name": "production"}],
        environment_details={
            "production": {
                "protection_rules": [{"type": "required_reviewers"}],
                "deployment_branch_policy": {"protected_branches": True},
            }
        },
        security_analysis={
            "secret_scanning": {"status": "enabled"},
            "dependabot_security_updates": {"status": "enabled"},
        },
    )


def test_fully_governed_repository_passes_everything() -> None:
    evaluation = evaluate(_healthy_inputs())
    assert evaluation.overall == "pass", [
        (result.rule_id, result.verdict, result.observed)
        for result in evaluation.results
        if result.verdict != "pass"
    ]
    assert evaluation.blocking_count == 0
    assert evaluation.unknown_count == 0


def test_every_catalogued_rule_is_evaluated() -> None:
    evaluated = {result.rule_id for result in evaluate(_healthy_inputs()).results}
    assert evaluated == set(RULE_CATALOG)


def test_missing_protection_is_a_blocking_failure() -> None:
    inputs = PolicyInputs(
        full_name="o/r", default_branch="main", protection=None, branch_rules=[], workflows=[]
    )
    evaluation = evaluate(inputs)
    protection = next(
        result for result in evaluation.results if result.rule_id == "branch.protection.present"
    )
    assert protection.verdict == "fail"
    assert protection.blocking is True
    assert evaluation.overall == "fail"
    assert evaluation.blocking_count >= 1


def test_missing_permission_is_unknown_never_pass() -> None:
    inputs = PolicyInputs(
        full_name="o/r",
        default_branch="main",
        protection=None,
        protection_error="missing permission (administration:read)",
        branch_rules=None,
        branch_rules_error="missing permission (administration:read)",
        workflows=None,
        workflows_error="missing permission (actions:read)",
        environments=None,
        environments_error="missing permission (actions:read)",
    )
    evaluation = evaluate(inputs)
    for result in evaluation.results:
        assert result.verdict != "pass" or result.rule_id == "repo.default_branch.known", (
            f"{result.rule_id} passed without evidence"
        )
    assert evaluation.unknown_count >= 8
    assert evaluation.overall in ("unknown", "fail")
    unreadable = next(
        result for result in evaluation.results if result.rule_id == "branch.protection.present"
    )
    assert unreadable.verdict == "unknown"
    assert "administration:read" in unreadable.observed


def test_rate_limit_and_timeout_are_unknown() -> None:
    for reason in ("github_rate_limited", "github_unavailable"):
        inputs = PolicyInputs(
            full_name="o/r",
            default_branch="main",
            protection=None,
            protection_error=reason,
            branch_rules=None,
            branch_rules_error=reason,
        )
        assert _verdict(inputs, "branch.protection.present") == "unknown"


def test_force_push_allowed_is_a_blocking_failure() -> None:
    protection = _healthy_protection()
    protection["allow_force_pushes"] = {"enabled": True}
    inputs = _healthy_inputs()
    inputs = PolicyInputs(**{**inputs.__dict__, "protection": protection})
    result = next(
        item for item in evaluate(inputs).results if item.rule_id == "branch.force_push.blocked"
    )
    assert result.verdict == "fail"
    assert result.blocking is True
    assert result.remediation


def test_missing_required_checks_fails_and_strict_is_reported() -> None:
    protection = _healthy_protection()
    protection["required_status_checks"] = {"strict": False, "contexts": []}
    inputs = PolicyInputs(**{**_healthy_inputs().__dict__, "protection": protection})
    evaluation = evaluate(inputs)
    present = next(
        item for item in evaluation.results if item.rule_id == "branch.required_checks.present"
    )
    strict = next(
        item for item in evaluation.results if item.rule_id == "branch.required_checks.strict"
    )
    assert present.verdict == "fail" and present.blocking is True
    assert strict.verdict == "fail" and strict.blocking is False


def test_rulesets_can_satisfy_protection_without_classic_branch_protection() -> None:
    """Shaped like `GET /repos/{o}/{r}/rules/branches/{branch}` really is.

    That endpoint returns the effective rules directly — each entry is a
    rule object with `type`, `ruleset_id` and `ruleset_source_type` — not a
    ruleset wrapper with a nested `rules` array.
    """
    inputs = PolicyInputs(
        full_name="o/r",
        default_branch="main",
        protection=None,
        branch_rules=[
            {"type": "pull_request", "ruleset_id": 42, "ruleset_source_type": "Repository"},
            {"type": "non_fast_forward", "ruleset_id": 42, "ruleset_source_type": "Repository"},
            {"type": "deletion", "ruleset_id": 42, "ruleset_source_type": "Repository"},
            {
                "type": "required_status_checks",
                "ruleset_id": 42,
                "ruleset_source_type": "Organization",
                "parameters": {
                    "required_status_checks": [{"context": "ci"}],
                    "strict_required_status_checks_policy": True,
                },
            },
        ],
        workflows=[],
        environments=[],
    )
    evaluation = evaluate(inputs)
    assert _verdict(inputs, "branch.protection.present") == "pass"
    assert _verdict(inputs, "branch.pull_request.required") == "pass"
    assert _verdict(inputs, "branch.force_push.blocked") == "pass"
    assert _verdict(inputs, "branch.required_checks.present") == "pass"
    source = next(
        item for item in evaluation.results if item.rule_id == "branch.protection.present"
    )
    assert source.evidence["source"] == "ruleset"


def test_no_effective_rules_is_an_honest_fail() -> None:
    """An empty effective-rules answer is a real "nothing applies here".

    Rulesets that exist but are disabled, scoped to tags, or targeted at
    other branches simply do not appear in this response, so there is no
    way for an irrelevant ruleset to be mistaken for protection.
    """
    inputs = PolicyInputs(
        full_name="o/r",
        default_branch="main",
        protection=None,
        branch_rules=[],
        workflows=[],
    )
    assert _verdict(inputs, "branch.protection.present") == "fail"


def test_production_without_approval_is_blocking() -> None:
    inputs = PolicyInputs(
        **{
            **_healthy_inputs().__dict__,
            "environment_details": {
                "production": {"protection_rules": [], "deployment_branch_policy": None}
            },
        }
    )
    evaluation = evaluate(inputs)
    approval = next(
        item for item in evaluation.results if item.rule_id == "deploy.production.approval_required"
    )
    mapping = next(
        item for item in evaluation.results if item.rule_id == "deploy.production.branch_mapping"
    )
    assert approval.verdict == "fail" and approval.blocking is True
    assert mapping.verdict == "fail"
    assert evaluation.overall == "fail"


def test_service_profile_requires_a_production_environment() -> None:
    inputs = PolicyInputs(
        **{**_healthy_inputs().__dict__, "environments": [], "environment_details": {}}
    )
    assert _verdict(inputs, "deploy.production.approval_required", "service") == "fail"
    # The default profile only warns: not every repository deploys.
    assert _verdict(inputs, "deploy.production.approval_required", DEFAULT_PROFILE) == "warn"


def test_library_profile_does_not_demand_deployment_gates() -> None:
    inputs = PolicyInputs(
        **{**_healthy_inputs().__dict__, "environments": [], "environment_details": {}}
    )
    assert _verdict(inputs, "deploy.production.approval_required", "library") == "warn"


def test_missing_security_scanning_is_reported_not_ignored() -> None:
    inputs = PolicyInputs(
        **{
            **_healthy_inputs().__dict__,
            "security_analysis": {
                "secret_scanning": {"status": "disabled"},
                "dependabot_security_updates": {"status": "disabled"},
            },
            "workflows": [{"name": "build", "path": "b.yml", "state": "active"}],
        }
    )
    assert _verdict(inputs, "security.secret_scanning") == "fail"
    assert _verdict(inputs, "security.dependency_scanning") == "fail"
    assert _verdict(inputs, "ci.security_scan_gate") == "fail"


def test_invisible_security_analysis_is_unknown() -> None:
    inputs = PolicyInputs(**{**_healthy_inputs().__dict__, "security_analysis": None})
    assert _verdict(inputs, "security.secret_scanning") == "unknown"
    assert _verdict(inputs, "security.dependency_scanning") == "unknown"


def test_inactive_workflows_do_not_satisfy_a_gate() -> None:
    inputs = PolicyInputs(
        **{
            **_healthy_inputs().__dict__,
            "workflows": [
                {"name": "build", "path": "build.yml", "state": "disabled_manually"},
                {"name": "test", "path": "test.yml", "state": "disabled_inactivity"},
            ],
        }
    )
    assert _verdict(inputs, "ci.build_gate") == "warn"
    assert _verdict(inputs, "ci.test_gate") == "fail"


def test_unknown_default_branch_is_unknown() -> None:
    inputs = PolicyInputs(full_name="o/r", default_branch="")
    assert _verdict(inputs, "repo.default_branch.known") == "unknown"


def test_snapshots_are_deterministic_and_evidence_is_stable() -> None:
    first = evaluate(_healthy_inputs())
    second = evaluate(_healthy_inputs())
    assert first.evidence_digest == second.evidence_digest
    assert [item.rule_id for item in first.results] == sorted(
        item.rule_id for item in first.results
    )

    changed = PolicyInputs(**{**_healthy_inputs().__dict__, "default_branch": "trunk"})
    assert evaluate(changed).evidence_digest != first.evidence_digest


def test_every_non_pass_result_carries_actionable_metadata() -> None:
    inputs = PolicyInputs(full_name="o/r", default_branch="main", protection=None, workflows=[])
    for result in evaluate(inputs).results:
        assert result.rule_id and result.title and result.expected and result.observed
        assert result.severity in ("low", "medium", "high", "critical")
        if result.verdict in ("fail", "warn", "unknown"):
            assert result.remediation, f"{result.rule_id} has no remediation guidance"


def test_results_never_leak_credential_shapes() -> None:
    inputs = PolicyInputs(
        full_name="o/r",
        default_branch="main",
        protection_error="missing permission (administration:read)",
        workflows=[{"name": "ghs_shouldnotmatter", "path": "x.yml", "state": "active"}],
    )
    serialized = str(evaluate(inputs).as_json())
    for marker in ("PRIVATE KEY", "Bearer ", "Authorization"):
        assert marker not in serialized


# --- onboarding state machine ------------------------------------------


def test_state_machine_allows_the_documented_paths() -> None:
    assert onboarding.can_transition(onboarding.DISCOVERED, onboarding.VALIDATING)
    assert onboarding.can_transition(onboarding.VALIDATING, onboarding.READY)
    assert onboarding.can_transition(onboarding.READY, onboarding.DEGRADED)
    assert onboarding.can_transition(onboarding.DEGRADED, onboarding.READY)
    for state in onboarding.STATES:
        assert onboarding.can_transition(state, onboarding.DISABLED)


def test_state_machine_refuses_undocumented_paths() -> None:
    with pytest.raises(onboarding.InvalidTransitionError):
        onboarding.transition(onboarding.DISCOVERED, onboarding.READY, "shortcut")
    with pytest.raises(onboarding.InvalidTransitionError):
        onboarding.transition(onboarding.DISABLED, onboarding.READY, "resurrect")
    with pytest.raises(onboarding.InvalidTransitionError):
        onboarding.transition(onboarding.READY, "deleted", "unknown state")


def test_same_state_transition_is_a_legal_no_op() -> None:
    change = onboarding.transition(onboarding.READY, onboarding.READY, "reconciled")
    assert change.changed is False


def test_resolve_target_precedence_is_fail_closed() -> None:
    # A security gate outranks everything, including a good reconcile.
    state, reason = onboarding.resolve_target(
        security_gate="manual_env_review",
        access_state="accessible",
        reconciled=True,
        had_error=False,
    )
    assert state == onboarding.BLOCKED
    assert reason == "security_gate_manual_env_review"

    # Lost access beats a transient error.
    state, _ = onboarding.resolve_target(
        security_gate=None, access_state="removed", reconciled=True, had_error=True
    )
    assert state == onboarding.DISABLED

    # A transient error is DEGRADED, never BLOCKED.
    state, reason = onboarding.resolve_target(
        security_gate=None, access_state="accessible", reconciled=True, had_error=True
    )
    assert state == onboarding.DEGRADED
    assert reason == "reconciliation_error"

    state, _ = onboarding.resolve_target(
        security_gate=None, access_state="accessible", reconciled=True, had_error=False
    )
    assert state == onboarding.READY

    state, _ = onboarding.resolve_target(
        security_gate=None, access_state="accessible", reconciled=False, had_error=False
    )
    assert state == onboarding.DISCOVERED
