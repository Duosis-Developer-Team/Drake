"""Who wrote a commit and with which agent, which services it touched, and
which catalog project a Claude Code session belongs to — from the evidence
alone, never guessed."""

import uuid

import pytest
from drake_api.dev_activity import classify
from drake_api.dev_activity.collector import Project, Target, commit_row, resolve_project

CLAUDE_COMMIT = """feat(web): app tiles that fill the row

Body text that is never kept.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
"""


@pytest.mark.parametrize(
    ("message", "login", "expected"),
    [
        (CLAUDE_COMMIT, "gencaycoskuntr", ("claude_code", "trailer")),
        (
            "fix: x\n\nco-authored-by: claude <noreply@anthropic.com>",
            "a",
            ("claude_code", "trailer"),
        ),
        (
            "fix: x\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)",
            "a",
            ("claude_code", "pr_marker"),
        ),
        (
            "fix: y\n\nCo-authored-by: Copilot <175728472+Copilot@users.noreply.github.com>",
            "a",
            ("copilot", "trailer"),
        ),
        ("Implement the thing", "copilot-swe-agent[bot]", ("copilot", "bot_author")),
        ("Implement the thing", "devin-ai-integration[bot]", ("devin", "bot_author")),
        ("Implement the thing", "chatgpt-codex-connector[bot]", ("codex", "bot_author")),
        (
            "fix: z\n\nCo-authored-by: Cursor Agent <cursoragent@cursor.com>",
            "a",
            ("cursor", "trailer"),
        ),
        (
            "fix: z\n\nCo-authored-by: aider (gpt-5) <noreply@aider.chat>",
            "a",
            ("other_ai", "trailer"),
        ),
        # A human co-author is not an agent.
        ("fix: pair\n\nCo-authored-by: Enes Erman <enes@example.test>", "a", ("none", None)),
        ("plain commit", "a", ("none", None)),
    ],
)
def test_the_agent_is_read_from_the_commit(message: str, login: str, expected: tuple) -> None:
    assert classify.classify(message, login) == expected


def test_automation_is_not_a_person_or_an_agent() -> None:
    assert classify.is_automation("dependabot[bot]")
    assert classify.is_automation("github-actions[bot]")
    assert classify.is_automation("renovate")
    assert not classify.is_automation("copilot-swe-agent[bot]")
    assert not classify.is_automation("gencaycoskuntr")


def test_the_pr_comes_from_the_first_line_only() -> None:
    assert classify.pr_number("feat: tiles (#216)\n\nbody (#999)") == 216
    assert classify.pr_number("Merge pull request #237 from org/branch") == 237
    assert classify.pr_number("feat: tiles\n\nsee (#12)") is None


def test_only_the_title_is_kept() -> None:
    assert classify.title_of(CLAUDE_COMMIT) == "feat(web): app tiles that fill the row"
    assert len(classify.title_of("x" * 500)) == 200
    assert classify.title_of("") == ""


def test_paths_map_to_services_by_directory_never_by_file_name() -> None:
    keys = ["core-service", "auth-service", "frontend"]
    services, unmapped = classify.map_services(
        [
            "backend/core-service/app/main.py",
            "backend/auth_service/routes.py",
            "frontend/src/App.tsx",
            "README.md",
            "core-service.md",
        ],
        keys,
        "hermes",
    )
    assert services == ["core-service", "auth-service", "frontend"]
    assert unmapped == 2


def test_a_project_prefix_on_a_service_key_is_optional_in_paths() -> None:
    services, unmapped = classify.map_services(
        ["apps/api/src/x.py", "apps/web-admin/page.tsx"],
        ["logislot-api", "logislot-web-admin"],
        "logislot",
    )
    assert services == ["logislot-api", "logislot-web-admin"]
    assert unmapped == 0


def _target() -> Target:
    return Target(
        project_id=uuid.uuid4(),
        project_key="hermes",
        owner="Duosis-Developer-Team",
        name="Hermes",
        branch="dev",
        repository_external_id=1,
        installation_external_id=2,
        services=["core-service"],
    )


def _item(**over: object) -> dict:
    base = {
        "sha": "a" * 40,
        "author": {"login": "gencaycoskuntr"},
        "parents": [{"sha": "b"}],
        "commit": {
            "author": {
                "name": "Gencay",
                "email": "someone@example.test",
                "date": "2026-11-20T10:00:00Z",
            },
            "message": CLAUDE_COMMIT,
        },
    }
    base.update(over)
    return base


def test_a_commit_row_never_carries_an_email() -> None:
    detail = {
        "stats": {"additions": 10, "deletions": 2},
        "files": [{"filename": "backend/core-service/a.py"}],
    }
    row = commit_row(_target(), _item(), detail)
    assert row is not None
    assert "@" not in repr(row)
    assert (row["agent"], row["services"], row["additions"]) == (
        "claude_code",
        ["core-service"],
        10,
    )
    assert row["title"] == "feat(web): app tiles that fill the row"


def test_merge_commits_and_automation_are_skipped() -> None:
    assert commit_row(_target(), _item(parents=[{"sha": "1"}, {"sha": "2"}]), {}) is None
    assert commit_row(_target(), _item(author={"login": "dependabot[bot]"}), {}) is None


def test_a_name_that_is_an_address_is_dropped() -> None:
    item = _item()
    item["commit"]["author"]["name"] = "me@example.test"  # type: ignore[index]
    row = commit_row(_target(), item, {})
    assert row is not None and row["author_name"] == ""


def test_a_session_belongs_to_a_project_only_by_what_it_says() -> None:
    projects = [
        Project(uuid.uuid4(), "hermes", "Duosis-Developer-Team/Hermes"),
        Project(uuid.uuid4(), "tracesis", "Duosis-Developer-Team/Tracesis"),
    ]
    assert resolve_project(projects, "Tracesis", "") is projects[1]
    assert (
        resolve_project(projects, "", "https://github.com/Duosis-Developer-Team/Hermes.git main")
        is projects[0]
    )
    assert resolve_project(projects, "", "") is None
    assert resolve_project(projects, "elsewhere", "github.com/someone/else") is None


def test_usage_reads_stay_under_the_reader_ceiling() -> None:
    """The reader profile refuses max_result_rows above 1000; asking for more
    fails every usage read, silently, and the map shows no cost at all."""
    import re
    from pathlib import Path

    from drake_api.dev_activity.collector import MAX_USAGE_ROWS

    config = Path(__file__).resolve().parents[3] / "deploy/otel/templates/clickhouse-config.yaml"
    ceilings = [
        int(v) for v in re.findall(r"<max>(\d+)</max></max_result_rows>", config.read_text())
    ]
    assert ceilings, "the reader profile's max_result_rows ceiling must be declared"
    assert 0 < MAX_USAGE_ROWS <= min(ceilings)
