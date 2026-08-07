"""Read-only CI/CD governance policy engine (ADR-0020 §4).

The engine is pure: it takes already-fetched facts and returns verdicts.
It performs no I/O, so evaluations are deterministic and testable without
a network. It never writes to GitHub — remediation is a human decision in
Sprint 5A.

The decisive rule: absence of evidence is never evidence of compliance. A
missing permission, a rate limit, a timeout, or any unreadable answer
yields `VERDICT_UNKNOWN` — never `VERDICT_PASS`.
"""

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal

Verdict = Literal["pass", "warn", "fail", "unknown"]
Severity = Literal["low", "medium", "high", "critical"]

VERDICT_PASS: Verdict = "pass"  # noqa: S105 - a verdict, not a credential
VERDICT_WARN: Verdict = "warn"
VERDICT_FAIL: Verdict = "fail"
VERDICT_UNKNOWN: Verdict = "unknown"


@dataclass(frozen=True)
class RuleResult:
    rule_id: str
    title: str
    verdict: Verdict
    severity: Severity
    expected: str
    observed: str
    blocking: bool
    remediation: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "title": self.title,
            "verdict": self.verdict,
            "severity": self.severity,
            "expected": self.expected,
            "observed": self.observed,
            "blocking": self.blocking,
            "remediation": self.remediation,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class PolicyEvaluation:
    profile: str
    overall: Verdict
    results: tuple[RuleResult, ...]
    evidence_digest: str

    @property
    def blocking_count(self) -> int:
        return sum(
            1 for result in self.results if result.blocking and result.verdict == VERDICT_FAIL
        )

    @property
    def unknown_count(self) -> int:
        return sum(1 for result in self.results if result.verdict == VERDICT_UNKNOWN)

    def as_json(self) -> list[dict[str, Any]]:
        return [result.as_json() for result in self.results]


@dataclass(frozen=True)
class PolicyInputs:
    """Facts gathered by the reconciler. `*_error` means "we could not
    find out" — a fundamentally different thing from "the answer is no"."""

    full_name: str
    default_branch: str = ""
    protection: dict[str, Any] | None = None
    protection_error: str | None = None
    # Rules ACTUALLY in effect on the default branch, from
    # `GET /repos/{owner}/{repo}/rules/branches/{branch}`. The ruleset LIST
    # endpoint returns summaries with no `rules` member at all, so it can
    # never be evidence that a particular rule is or is not configured.
    branch_rules: list[dict[str, Any]] | None = None
    branch_rules_error: str | None = None
    workflows: list[dict[str, Any]] | None = None
    workflows_error: str | None = None
    environments: list[dict[str, Any]] | None = None
    environments_error: str | None = None
    # Per-environment protection rules, keyed by environment name.
    environment_details: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Why a specific environment could not be read. An entry here means the
    # aggregate verdict cannot be a PASS, however healthy its siblings look.
    environment_errors: dict[str, str] = field(default_factory=dict)
    security_analysis: dict[str, Any] | None = None
    security_analysis_error: str | None = None
    archived: bool = False


# --- profiles ---------------------------------------------------------
# The MINIMUM baseline is central; a profile may only widen it. Drake's
# own eight required check names are never imposed on other repositories.

DEFAULT_PROFILE = "default"
PROFILES: dict[str, dict[str, Any]] = {
    "default": {
        "description": "Minimum security baseline for any DuoSis repository.",
        "require_production_environment": False,
    },
    "service": {
        "description": "Deployable service: production environment gates required.",
        "require_production_environment": True,
    },
    "library": {
        "description": "Library/tooling: no deployment environment expected.",
        "require_production_environment": False,
    },
}

_PRODUCTION_NAMES = ("production", "prod", "live")
_BUILD_HINTS = ("build", "compile", "package")
_TEST_HINTS = ("test", "spec", "check", "typecheck", "lint", "ci", "verify")
_SECURITY_HINTS = ("codeql", "security", "scan", "audit", "gitleaks", "secret", "trivy", "osv")


def _unreadable(
    rule_id: str,
    title: str,
    severity: Severity,
    expected: str,
    reason: str,
    remediation: str,
) -> RuleResult:
    """The single shape for "we could not find out" — never a VERDICT_PASS."""
    return RuleResult(
        rule_id=rule_id,
        title=title,
        verdict=VERDICT_UNKNOWN,
        severity=severity,
        expected=expected,
        observed=f"not determinable: {reason}",
        blocking=False,
        remediation=remediation,
        evidence={"reason": reason},
    )


def _protection_facts(inputs: PolicyInputs) -> dict[str, Any]:
    """Normalize classic branch protection and rulesets into one view."""
    facts: dict[str, Any] = {
        "source": "none",
        "pull_request_required": None,
        "force_push_blocked": None,
        "deletion_blocked": None,
        "required_checks": None,
        "strict_checks": None,
        "admin_enforced": None,
    }
    protection = inputs.protection
    if isinstance(protection, dict):
        facts["source"] = "branch_protection"
        reviews = protection.get("required_pull_request_reviews")
        facts["pull_request_required"] = isinstance(reviews, dict)
        allow_force = protection.get("allow_force_pushes")
        if isinstance(allow_force, dict):
            facts["force_push_blocked"] = not bool(allow_force.get("enabled"))
        allow_deletions = protection.get("allow_deletions")
        if isinstance(allow_deletions, dict):
            facts["deletion_blocked"] = not bool(allow_deletions.get("enabled"))
        checks = protection.get("required_status_checks")
        if isinstance(checks, dict):
            contexts = checks.get("contexts")
            if not isinstance(contexts, list):
                declared = checks.get("checks")
                contexts = (
                    [item.get("context") for item in declared if isinstance(item, dict)]
                    if isinstance(declared, list)
                    else []
                )
            facts["required_checks"] = [str(item) for item in contexts if item]
            facts["strict_checks"] = bool(checks.get("strict"))
        else:
            facts["required_checks"] = []
        enforce_admins = protection.get("enforce_admins")
        if isinstance(enforce_admins, dict):
            facts["admin_enforced"] = bool(enforce_admins.get("enabled"))

    branch_rules = inputs.branch_rules
    if isinstance(branch_rules, list) and branch_rules:
        # Every entry from this endpoint is a rule already resolved as
        # applying to this branch, across repository AND organization
        # rulesets, and already filtered to active enforcement.
        facts["source"] = "ruleset" if facts["source"] == "none" else f"{facts['source']}+ruleset"
        rule_types = {
            str(rule.get("type")) for rule in branch_rules if isinstance(rule, dict)
        }
        if "pull_request" in rule_types:
            facts["pull_request_required"] = True
        if "non_fast_forward" in rule_types:
            facts["force_push_blocked"] = True
        if "deletion" in rule_types:
            facts["deletion_blocked"] = True
        for rule in branch_rules:
            if not isinstance(rule, dict) or rule.get("type") != "required_status_checks":
                continue
            parameters = rule.get("parameters") or {}
            declared = parameters.get("required_status_checks") or []
            contexts = [
                str(item.get("context"))
                for item in declared
                if isinstance(item, dict) and item.get("context")
            ]
            existing = facts["required_checks"] or []
            facts["required_checks"] = sorted({*existing, *contexts})
            if parameters.get("strict_required_status_checks_policy"):
                facts["strict_checks"] = True
    return facts


def _rule_default_branch(inputs: PolicyInputs) -> RuleResult:
    if inputs.default_branch:
        return RuleResult(
            rule_id="repo.default_branch.known",
            title="Default branch is known",
            verdict=VERDICT_PASS,
            severity="low",
            expected="a default branch is reported by the provider",
            observed=f"default branch is '{inputs.default_branch}'",
            blocking=False,
            remediation="",
            evidence={"default_branch": inputs.default_branch},
        )
    return _unreadable(
        "repo.default_branch.known",
        "Default branch is known",
        "medium",
        "a default branch is reported by the provider",
        "repository metadata did not include a default branch",
        "Confirm the installation grants Metadata: read and the repository is accessible.",
    )


def _rule_protection_present(inputs: PolicyInputs, facts: dict[str, Any]) -> RuleResult:
    rule_id = "branch.protection.present"
    title = "Default branch is protected"
    expected = "the default branch is covered by branch protection or an active ruleset"
    remediation = (
        "Protect the default branch with classic branch protection or an active repository ruleset."
    )
    # Absence of evidence is not evidence of absence: if EITHER source was
    # unreadable and the readable ones show nothing, protection may well
    # exist where we cannot see it. Only "both readable, both empty" is a
    # real FAIL.
    unreadable_reason = inputs.protection_error or inputs.branch_rules_error
    if facts["source"] == "none" and unreadable_reason:
        return _unreadable(rule_id, title, "high", expected, unreadable_reason, remediation)
    if facts["source"] == "none":
        return RuleResult(
            rule_id=rule_id,
            title=title,
            verdict=VERDICT_FAIL,
            severity="critical",
            expected=expected,
            observed="no branch protection and no active ruleset cover the default branch",
            blocking=True,
            remediation=remediation,
            evidence={"source": facts["source"]},
        )
    return RuleResult(
        rule_id=rule_id,
        title=title,
        verdict=VERDICT_PASS,
        severity="critical",
        expected=expected,
        observed=f"protected via {facts['source']}",
        blocking=True,
        remediation="",
        evidence={"source": facts["source"]},
    )


def _boolean_rule(
    rule_id: str,
    title: str,
    value: bool | None,
    *,
    severity: Severity,
    expected: str,
    observed_when_true: str,
    observed_when_false: str,
    remediation: str,
    blocking: bool,
    unreadable_reason: str | None,
) -> RuleResult:
    if unreadable_reason is not None and value is None:
        return _unreadable(rule_id, title, severity, expected, unreadable_reason, remediation)
    if value is None:
        return _unreadable(
            rule_id,
            title,
            severity,
            expected,
            "the provider did not report this setting",
            remediation,
        )
    if value:
        return RuleResult(
            rule_id=rule_id,
            title=title,
            verdict=VERDICT_PASS,
            severity=severity,
            expected=expected,
            observed=observed_when_true,
            blocking=blocking,
            remediation="",
        )
    return RuleResult(
        rule_id=rule_id,
        title=title,
        verdict=VERDICT_FAIL,
        severity=severity,
        expected=expected,
        observed=observed_when_false,
        blocking=blocking,
        remediation=remediation,
    )


def _rule_required_checks(inputs: PolicyInputs, facts: dict[str, Any]) -> list[RuleResult]:
    rule_id = "branch.required_checks.present"
    title = "Required status checks are configured"
    expected = "at least one required status check gates the default branch"
    remediation = "Require your build/test checks on the default branch before merging."
    results: list[RuleResult] = []
    checks = facts["required_checks"]
    if checks is None:
        results.append(
            _unreadable(
                rule_id,
                title,
                "high",
                expected,
                inputs.protection_error or "protection settings were not readable",
                remediation,
            )
        )
        results.append(
            _unreadable(
                "branch.required_checks.strict",
                "Required checks must be up to date",
                "medium",
                "required checks use the strict / up-to-date policy",
                inputs.protection_error or "protection settings were not readable",
                "Enable the strict (up-to-date branch) policy for required checks.",
            )
        )
        return results
    if checks:
        results.append(
            RuleResult(
                rule_id=rule_id,
                title=title,
                verdict=VERDICT_PASS,
                severity="high",
                expected=expected,
                observed=f"{len(checks)} required check(s) configured",
                blocking=True,
                remediation="",
                evidence={"required_checks": sorted(checks)},
            )
        )
    else:
        results.append(
            RuleResult(
                rule_id=rule_id,
                title=title,
                verdict=VERDICT_FAIL,
                severity="high",
                expected=expected,
                observed="no required status check is configured",
                blocking=True,
                remediation=remediation,
                evidence={"required_checks": []},
            )
        )
    results.append(
        _boolean_rule(
            "branch.required_checks.strict",
            "Required checks must be up to date",
            facts["strict_checks"],
            severity="medium",
            expected="required checks use the strict / up-to-date policy",
            observed_when_true="strict (up-to-date) policy is enabled",
            observed_when_false="required checks may pass on a stale branch",
            remediation="Enable the strict (up-to-date branch) policy for required checks.",
            blocking=False,
            unreadable_reason=inputs.protection_error,
        )
    )
    return results


def _workflow_gate(
    inputs: PolicyInputs,
    rule_id: str,
    title: str,
    hints: tuple[str, ...],
    severity: Severity,
    expected: str,
    remediation: str,
) -> RuleResult:
    if inputs.workflows is None:
        return _unreadable(
            rule_id,
            title,
            severity,
            expected,
            inputs.workflows_error or "workflow inventory was not readable",
            remediation,
        )
    active = [
        workflow
        for workflow in inputs.workflows
        if str(workflow.get("state", "active")).lower() == "active"
    ]
    matched = [
        str(workflow.get("name") or workflow.get("path") or "")
        for workflow in active
        if any(
            hint in f"{workflow.get('name', '')} {workflow.get('path', '')}".lower()
            for hint in hints
        )
    ]
    if matched:
        return RuleResult(
            rule_id=rule_id,
            title=title,
            verdict=VERDICT_PASS,
            severity=severity,
            expected=expected,
            observed=f"{len(matched)} matching workflow(s)",
            blocking=False,
            remediation="",
            evidence={"workflows": sorted(matched)[:10]},
        )
    return RuleResult(
        rule_id=rule_id,
        title=title,
        verdict=VERDICT_WARN if severity in ("low", "medium") else VERDICT_FAIL,
        severity=severity,
        expected=expected,
        observed="no matching active workflow was found",
        blocking=False,
        remediation=remediation,
        evidence={"active_workflows": len(active)},
    )


def _security_analysis_rule(
    inputs: PolicyInputs, rule_id: str, title: str, key: str, expected: str, remediation: str
) -> RuleResult:
    analysis = inputs.security_analysis
    if not isinstance(analysis, dict):
        return _unreadable(
            rule_id,
            title,
            "high",
            expected,
            inputs.security_analysis_error
            or "security analysis settings are not visible to this installation",
            remediation,
        )
    entry = analysis.get(key)
    status = entry.get("status") if isinstance(entry, dict) else None
    if status is None:
        return _unreadable(
            rule_id,
            title,
            "high",
            expected,
            "the provider did not report this setting",
            remediation,
        )
    if str(status).lower() == "enabled":
        return RuleResult(
            rule_id=rule_id,
            title=title,
            verdict=VERDICT_PASS,
            severity="high",
            expected=expected,
            observed="enabled",
            blocking=False,
            remediation="",
        )
    return RuleResult(
        rule_id=rule_id,
        title=title,
        verdict=VERDICT_FAIL,
        severity="high",
        expected=expected,
        observed=str(status),
        blocking=False,
        remediation=remediation,
    )


def _production_environments(inputs: PolicyInputs) -> list[str]:
    if not inputs.environments:
        return []
    return [
        str(environment.get("name"))
        for environment in inputs.environments
        if any(hint in str(environment.get("name", "")).lower() for hint in _PRODUCTION_NAMES)
    ]


def _rule_production_gates(inputs: PolicyInputs, profile: dict[str, Any]) -> list[RuleResult]:
    approval_id = "deploy.production.approval_required"
    approval_title = "Production deployments require explicit approval"
    approval_expected = "the production environment requires reviewer approval before deploying"
    approval_fix = (
        "Add required reviewers to the production environment so a deployment cannot "
        "run without an explicit human approval."
    )
    mapping_id = "deploy.production.branch_mapping"
    mapping_title = "Production deploys only from the sanctioned branch"
    mapping_expected = "the production environment restricts which branches may deploy"
    mapping_fix = (
        "Set a deployment branch policy on the production environment so only the "
        "sanctioned branch can deploy."
    )

    if inputs.environments is None:
        return [
            _unreadable(
                approval_id,
                approval_title,
                "critical",
                approval_expected,
                inputs.environments_error or "environments were not readable",
                approval_fix,
            ),
            _unreadable(
                mapping_id,
                mapping_title,
                "high",
                mapping_expected,
                inputs.environments_error or "environments were not readable",
                mapping_fix,
            ),
        ]

    production = _production_environments(inputs)
    if not production:
        if profile.get("require_production_environment"):
            return [
                RuleResult(
                    rule_id=approval_id,
                    title=approval_title,
                    verdict=VERDICT_FAIL,
                    severity="critical",
                    expected=approval_expected,
                    observed="no production environment exists to gate deployments",
                    blocking=True,
                    remediation=approval_fix,
                    evidence={"environments": len(inputs.environments)},
                ),
                RuleResult(
                    rule_id=mapping_id,
                    title=mapping_title,
                    verdict=VERDICT_FAIL,
                    severity="high",
                    expected=mapping_expected,
                    observed="no production environment exists",
                    blocking=False,
                    remediation=mapping_fix,
                ),
            ]
        return [
            RuleResult(
                rule_id=approval_id,
                title=approval_title,
                verdict=VERDICT_WARN,
                severity="medium",
                expected=approval_expected,
                observed="no production environment is defined for this repository",
                blocking=False,
                remediation=approval_fix,
                evidence={"environments": len(inputs.environments)},
            ),
            RuleResult(
                rule_id=mapping_id,
                title=mapping_title,
                verdict=VERDICT_WARN,
                severity="low",
                expected=mapping_expected,
                observed="no production environment is defined",
                blocking=False,
                remediation=mapping_fix,
            ),
        ]

    approved: list[str] = []
    unapproved: list[str] = []
    unmapped: list[str] = []
    mapped: list[str] = []
    unreadable: list[str] = []
    for name in production:
        detail = inputs.environment_details.get(name)
        if not isinstance(detail, dict):
            # We asked and did not get an answer. This environment is not
            # evidence of compliance OR of a violation.
            unreadable.append(name)
            continue
        rules = detail.get("protection_rules")
        reviewers = [
            rule
            for rule in (rules or [])
            if isinstance(rule, dict) and rule.get("type") == "required_reviewers"
        ]
        (approved if reviewers else unapproved).append(name)
        branch_policy = detail.get("deployment_branch_policy")
        (mapped if isinstance(branch_policy, dict) and branch_policy else unmapped).append(name)

    # A PASS here is a statement about EVERY production environment. One
    # environment we could not read is enough to make that statement
    # unsupportable, no matter how compliant the ones we did read look.
    # A known violation still outranks an unknown: FAIL survives, PASS does
    # not degrade into it.
    unreadable_note = (
        "; ".join(
            f"{name}: {inputs.environment_errors.get(name, 'not readable')}"
            for name in sorted(unreadable)
        )
        or "environment protection rules were not readable"
    )

    def _aggregate(
        rule_id: str,
        title: str,
        severity: Severity,
        expected: str,
        violating: list[str],
        compliant: list[str],
        violation_text: str,
        compliant_text: str,
        remediation: str,
        blocking: bool,
    ) -> RuleResult:
        if violating:
            return RuleResult(
                rule_id=rule_id,
                title=title,
                verdict=VERDICT_FAIL,
                severity=severity,
                expected=expected,
                observed=f"{violation_text}: {', '.join(sorted(violating))}",
                blocking=blocking,
                remediation=remediation,
                evidence={
                    "compliant": sorted(compliant),
                    "violating": sorted(violating),
                    "unreadable": sorted(unreadable),
                },
            )
        if unreadable:
            return _unreadable(rule_id, title, severity, expected, unreadable_note, remediation)
        return RuleResult(
            rule_id=rule_id,
            title=title,
            verdict=VERDICT_PASS,
            severity=severity,
            expected=expected,
            observed=f"{compliant_text}: {', '.join(sorted(compliant))}",
            blocking=blocking,
            remediation="",
            evidence={"compliant": sorted(compliant), "violating": []},
        )

    return [
        _aggregate(
            approval_id,
            approval_title,
            "critical",
            approval_expected,
            unapproved,
            approved,
            "no required reviewers on",
            "required reviewers configured on",
            approval_fix,
            blocking=True,
        ),
        _aggregate(
            mapping_id,
            mapping_title,
            "high",
            mapping_expected,
            unmapped,
            mapped,
            "no deployment branch policy on",
            "deployment branch policy set on",
            mapping_fix,
            blocking=False,
        ),
    ]


def evaluate(inputs: PolicyInputs, profile_name: str = DEFAULT_PROFILE) -> PolicyEvaluation:
    """Evaluate every rule. Pure, deterministic, and never writes."""
    profile = PROFILES.get(profile_name, PROFILES[DEFAULT_PROFILE])
    facts = _protection_facts(inputs)

    results: list[RuleResult] = [
        _rule_default_branch(inputs),
        _rule_protection_present(inputs, facts),
        _boolean_rule(
            "branch.pull_request.required",
            "Changes go through pull requests",
            facts["pull_request_required"],
            severity="critical",
            expected="direct pushes to the default branch are not allowed",
            observed_when_true="pull-request review is required",
            observed_when_false="changes can land without a pull request",
            remediation="Require pull requests (with review) on the default branch.",
            blocking=True,
            unreadable_reason=inputs.protection_error if facts["source"] == "none" else None,
        ),
        _boolean_rule(
            "branch.force_push.blocked",
            "Force pushes are blocked",
            facts["force_push_blocked"],
            severity="high",
            expected="force pushes to the default branch are blocked",
            observed_when_true="force pushes are blocked",
            observed_when_false="force pushes are permitted",
            remediation="Disable force pushes on the default branch.",
            blocking=True,
            unreadable_reason=inputs.protection_error if facts["source"] == "none" else None,
        ),
        _boolean_rule(
            "branch.deletion.blocked",
            "Branch deletion is blocked",
            facts["deletion_blocked"],
            severity="high",
            expected="the default branch cannot be deleted",
            observed_when_true="branch deletion is blocked",
            observed_when_false="the default branch can be deleted",
            remediation="Disable branch deletion on the default branch.",
            blocking=False,
            unreadable_reason=inputs.protection_error if facts["source"] == "none" else None,
        ),
    ]
    results.extend(_rule_required_checks(inputs, facts))
    results.append(
        _boolean_rule(
            "branch.admin_enforcement",
            "Protection applies to administrators",
            facts["admin_enforced"],
            severity="medium",
            expected="administrators are subject to the same protection",
            observed_when_true="admin enforcement is enabled",
            observed_when_false="administrators can bypass protection",
            remediation="Enable admin enforcement (or an equivalent ruleset bypass policy).",
            blocking=False,
            unreadable_reason=inputs.protection_error if facts["source"] == "none" else None,
        )
    )
    results.append(
        _workflow_gate(
            inputs,
            "ci.build_gate",
            "A build/compile workflow exists",
            _BUILD_HINTS,
            "medium",
            "an active workflow builds the project",
            "Add a CI workflow that builds the project on pull requests.",
        )
    )
    results.append(
        _workflow_gate(
            inputs,
            "ci.test_gate",
            "A test/verification workflow exists",
            _TEST_HINTS,
            "high",
            "an active workflow runs tests or type/lint verification",
            "Add a CI workflow that runs tests and type checks on pull requests.",
        )
    )
    results.append(
        _workflow_gate(
            inputs,
            "ci.security_scan_gate",
            "A security-scanning workflow exists",
            _SECURITY_HINTS,
            "high",
            "an active workflow performs security or dependency scanning",
            "Add a secret/dependency scanning workflow to the pipeline.",
        )
    )
    results.append(
        _security_analysis_rule(
            inputs,
            "security.secret_scanning",
            "Secret scanning is enabled",
            "secret_scanning",
            "secret scanning is enabled for the repository",
            "Enable secret scanning (and push protection) for the repository.",
        )
    )
    results.append(
        _security_analysis_rule(
            inputs,
            "security.dependency_scanning",
            "Dependency scanning is enabled",
            "dependabot_security_updates",
            "dependency/vulnerability scanning is enabled",
            "Enable Dependabot alerts and security updates for the repository.",
        )
    )
    results.extend(_rule_production_gates(inputs, profile))

    if any(result.verdict == VERDICT_FAIL and result.blocking for result in results):
        overall: Verdict = VERDICT_FAIL
    elif any(result.verdict == VERDICT_FAIL for result in results):
        overall = VERDICT_FAIL
    elif any(result.verdict == VERDICT_UNKNOWN for result in results):
        overall = VERDICT_UNKNOWN
    elif any(result.verdict == VERDICT_WARN for result in results):
        overall = VERDICT_WARN
    else:
        overall = VERDICT_PASS

    ordered = tuple(sorted(results, key=lambda item: item.rule_id))
    canonical = json.dumps([item.as_json() for item in ordered], sort_keys=True)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return PolicyEvaluation(
        profile=profile_name if profile_name in PROFILES else DEFAULT_PROFILE,
        overall=overall,
        results=ordered,
        evidence_digest=digest,
    )


RULE_CATALOG: tuple[str, ...] = (
    "repo.default_branch.known",
    "branch.protection.present",
    "branch.pull_request.required",
    "branch.force_push.blocked",
    "branch.deletion.blocked",
    "branch.required_checks.present",
    "branch.required_checks.strict",
    "branch.admin_enforcement",
    "ci.build_gate",
    "ci.test_gate",
    "ci.security_scan_gate",
    "security.secret_scanning",
    "security.dependency_scanning",
    "deploy.production.approval_required",
    "deploy.production.branch_mapping",
)
