"""Python and TypeScript manifest validators must agree.

Two validators for one contract is two chances to disagree, and the one
that matters is the server's — a manifest the browser accepted and the API
rejected is a confusing bug, while the reverse is a security hole. These
tests pin both directions against the same fixtures the contract package
uses, and against the rule catalogue in `policy.ts` itself.
"""

import re
from pathlib import Path

import pytest
from drake_api.github_app import manifest

CONTRACTS = Path(__file__).resolve().parents[3] / "packages" / "contracts"
VALID_DIR = CONTRACTS / "fixtures" / "valid"
INVALID_DIR = CONTRACTS / "fixtures" / "invalid"
POLICY_TS = CONTRACTS / "src" / "policy.ts"


def _fixtures(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.yaml"))


def test_the_fixture_corpus_is_not_empty() -> None:
    """Guards against a parity suite that silently tests nothing."""
    assert len(_fixtures(VALID_DIR)) >= 4
    assert len(_fixtures(INVALID_DIR)) >= 7


@pytest.mark.parametrize("fixture", _fixtures(VALID_DIR), ids=lambda path: path.name)
def test_every_valid_contract_fixture_is_accepted(fixture: Path) -> None:
    result = manifest.validate_content(fixture.read_text(encoding="utf-8"))
    assert result.valid, [finding.as_json() for finding in result.findings]


@pytest.mark.parametrize("fixture", _fixtures(INVALID_DIR), ids=lambda path: path.name)
def test_every_invalid_contract_fixture_is_rejected(fixture: Path) -> None:
    result = manifest.validate_content(fixture.read_text(encoding="utf-8"))
    assert not result.valid
    assert result.findings
    assert result.document is None, "an invalid manifest must not be handed on as parsed"


# The rule each invalid fixture is meant to trip, as the TypeScript policy
# names it. Schema-level rejections are reported as `schema` on both sides.
EXPECTED_RULES = {
    "ambiguous-environment.yaml": "schema",
    "inline-sql.yaml": "inline-sql",
    "insecure-config.yaml": "plaintext-endpoint",
    "invalid-enum.yaml": "schema",
    "missing-required.yaml": "schema",
    # Not `schema`: JSON Schema cannot express it. An omitted role and an
    # explicit `primary` are structurally different and semantically one
    # association, so `uniqueItems` sees two unique items.
    "owner-duplicate-role.yaml": "owner-duplicate",
    "unknown-field.yaml": "schema",
    "wrong-api-version.yaml": "schema",
}


@pytest.mark.parametrize("fixture", _fixtures(INVALID_DIR), ids=lambda path: path.name)
def test_the_reported_rule_matches_the_typescript_rule(fixture: Path) -> None:
    expected = EXPECTED_RULES.get(fixture.name)
    assert expected is not None, f"{fixture.name} has no expected rule; add it deliberately"
    result = manifest.validate_content(fixture.read_text(encoding="utf-8"))
    assert expected in {finding.rule for finding in result.findings}


def _typescript_rule_ids() -> set[str]:
    """Read the rule catalogue out of policy.ts.

    Parsing the source keeps the two catalogues honest without running
    Node: if someone adds a rule on one side only, this fails.
    """
    source = POLICY_TS.read_text(encoding="utf-8")
    return set(re.findall(r'^\s*id:\s*"([a-z0-9-]+)"', source, re.MULTILINE))


def test_both_implementations_carry_the_same_policy_rules() -> None:
    python_rules = {rule_id for rule_id, _message, _pattern in manifest._VALUE_RULES} | {
        rule_id for rule_id, _message, _pattern in manifest._KEY_RULES
    }
    assert python_rules == _typescript_rule_ids(), (
        "the Python and TypeScript policy rule catalogues have drifted"
    )


@pytest.mark.parametrize(
    ("value", "rule"),
    [
        ("postgres://user:hunter2@db.internal:5432/app", "credential-in-url"),
        ("api_key=AKIAIOSFODNN7EXAMPLE", "credential-assignment"),
        ("-----BEGIN RSA PRIVATE KEY-----", "private-key-material"),
        ("Bearer abcdefghijklmnopqrstuvwxyz012345", "bearer-token"),
        ("AKIAIOSFODNN7EXAMPLE", "cloud-access-key-id"),
        ("select id from users where 1=1", "inline-sql"),
        ("http://metrics.internal/scrape", "plaintext-endpoint"),
    ],
)
def test_each_value_rule_fires_on_its_own_shape(value: str, rule: str) -> None:
    findings = manifest.check_policy({"spec": {"probe": value}})
    assert rule in {finding.rule for finding in findings}


def test_a_finding_never_echoes_the_offending_value() -> None:
    """Findings are rendered in the UI and written to audit metadata."""
    secret = "hunter2-do-not-leak"
    findings = manifest.check_policy(
        {"spec": {"dsn": f"postgres://user:{secret}@db.internal:5432/app"}}
    )
    assert findings
    rendered = " ".join(f"{f.path} {f.rule} {f.message}" for f in findings)
    assert secret not in rendered


@pytest.mark.parametrize("key", ["insecure", "skipVerify", "skip_verify", "disable_tls"])
def test_an_insecure_flag_is_caught_by_its_key_not_its_value(key: str) -> None:
    findings = manifest.check_policy({"spec": {"tls": {key: True}}})
    assert "insecure-flag" in {finding.rule for finding in findings}


def test_the_key_rule_matches_typescript_including_where_it_does_not_fire() -> None:
    """Parity means agreeing about the gaps too.

    Neither implementation matches the camelCase `insecureSkipVerify`; the
    schema's `additionalProperties: false` rejects it wherever the shape is
    typed. Pinned here so the two cannot drift apart silently, and recorded
    in the backlog rather than changed mid-sprint.
    """
    assert manifest.check_policy({"spec": {"tls": {"insecureSkipVerify": True}}}) == []


def test_yaml_is_parsed_safely() -> None:
    """A repository controls this text; it must not construct objects."""
    hostile = "!!python/object/apply:os.system ['echo pwned']\n"
    result = manifest.validate_content(hostile)
    assert not result.valid
    assert {finding.rule for finding in result.findings} <= {
        "yaml-parse",
        "not-an-object",
        "schema",
    }


def test_an_oversized_manifest_is_refused_before_parsing() -> None:
    result = manifest.validate_content("# padding\n" + "x" * (manifest.MAX_MANIFEST_BYTES + 1))
    assert not result.valid
    assert [finding.rule for finding in result.findings] == ["manifest-too-large"]


def test_a_yaml_error_message_does_not_quote_the_document() -> None:
    result = manifest.validate_content("spec: [unclosed\n  secret: hunter2\n")
    assert not result.valid
    rendered = " ".join(finding.message for finding in result.findings)
    assert "hunter2" not in rendered


# --- repository identity -------------------------------------------------


def _alpha() -> dict:
    return manifest.validate_content(
        (VALID_DIR / "project-alpha.yaml").read_text(encoding="utf-8")
    ).document  # type: ignore[return-value]


def test_a_matching_repository_identity_produces_no_findings() -> None:
    assert manifest.check_repository_identity(_alpha(), "example-org", "alpha", "dev") == []


@pytest.mark.parametrize(
    ("owner", "name", "branch"),
    [
        ("another-org", "alpha", "dev"),
        ("example-org", "beta", "dev"),
        ("example-org", "alpha", "main"),
    ],
)
def test_a_mismatched_repository_identity_is_reported(owner: str, name: str, branch: str) -> None:
    findings = manifest.check_repository_identity(_alpha(), owner, name, branch)
    assert findings
    assert {finding.rule for finding in findings} == {"repository-identity"}


def test_identity_comparison_is_case_insensitive_on_owner_and_name() -> None:
    assert manifest.check_repository_identity(_alpha(), "Example-Org", "ALPHA", "dev") == []


# --- ownership consistency -------------------------------------------------
# A structural rule rather than a pattern rule, so it sits outside the
# `_VALUE_RULES`/`_KEY_RULES` catalogue above. Parity is held by the shared
# fixture corpus: both validators must reject `owner-duplicate-role.yaml`
# with rule `owner-duplicate`.


def _owners(*entries: dict) -> dict:
    return {"spec": {"owners": list(entries)}}


def test_the_same_team_in_two_roles_is_two_associations() -> None:
    findings = manifest.check_owner_consistency(
        _owners(
            {"team": "alpha-team", "role": "primary"}, {"team": "alpha-team", "role": "secondary"}
        )
    )
    assert findings == []


def test_an_omitted_role_collides_with_an_explicit_primary() -> None:
    """The case JSON Schema `uniqueItems` cannot see: two structurally
    different objects meaning one association."""
    findings = manifest.check_owner_consistency(
        _owners({"team": "alpha-team"}, {"team": "alpha-team", "role": "primary"})
    )
    assert [finding.rule for finding in findings] == ["owner-duplicate"]
    assert findings[0].path == "spec.owners[1]"


def test_an_exact_duplicate_owner_is_refused() -> None:
    findings = manifest.check_owner_consistency(
        _owners(
            {"team": "alpha-team", "role": "primary"}, {"team": "alpha-team", "role": "primary"}
        )
    )
    assert [finding.rule for finding in findings] == ["owner-duplicate"]


def test_two_omitted_roles_for_one_team_are_refused() -> None:
    findings = manifest.check_owner_consistency(_owners({"team": "a"}, {"team": "a"}))
    assert [finding.rule for finding in findings] == ["owner-duplicate"]


def test_different_teams_do_not_collide() -> None:
    findings = manifest.check_owner_consistency(_owners({"team": "a"}, {"team": "b"}))
    assert findings == []


def test_the_owner_finding_never_echoes_the_team_name() -> None:
    findings = manifest.check_owner_consistency(
        _owners({"team": "alpha-team"}, {"team": "alpha-team"})
    )
    assert "alpha-team" not in findings[0].message


def test_owner_consistency_tolerates_a_malformed_document() -> None:
    """Schema errors are reported by the schema. This must not raise on the
    way there, because both run and the caller sees every reason at once."""
    assert manifest.check_owner_consistency(None) == []
    assert manifest.check_owner_consistency({"spec": None}) == []
    assert manifest.check_owner_consistency({"spec": {"owners": "not-a-list"}}) == []
    assert manifest.check_owner_consistency({"spec": {"owners": ["not-an-object"]}}) == []
