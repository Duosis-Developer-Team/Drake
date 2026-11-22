"""Who wrote a commit and with which agent, which services it touched, and
which catalog project a Claude Code session belongs to — from the evidence
alone, never guessed."""

import uuid

import pytest
from drake_api.dev_activity import classify
from drake_api.dev_activity.collector import (
    Project,
    Repository,
    Target,
    commit_row,
    repository_of,
    resolve_repository,
)
from drake_api.dev_activity.repository import repository_id
from drake_api.settings import Settings

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


ORG = "Duosis-Developer-Team"


def _projects() -> list[Project]:
    return [
        Project(uuid.uuid4(), "hermes", f"{ORG}/Hermes"),
        Project(uuid.uuid4(), "tracesis", f"{ORG}/Tracesis"),
    ]


def test_a_session_belongs_to_a_project_only_by_what_it_says() -> None:
    projects = _projects()
    known = {f"{ORG}/hermes".lower(): 11, f"{ORG}/tracesis".lower(): 12}
    tracesis = resolve_repository(projects, known, tag="Tracesis")
    assert tracesis == Repository(f"{ORG}/tracesis".lower(), 12, projects[1].id)
    # The old fallback still works: any vcs.* value naming a catalog repository.
    hermes = resolve_repository(
        projects, known, vcs="https://github.com/Duosis-Developer-Team/Hermes.git main"
    )
    assert hermes is not None and hermes.project_id == projects[0].id
    assert resolve_repository(projects, known) is None
    assert (
        resolve_repository(projects, known, tag="elsewhere", vcs="github.com/someone/else") is None
    )


def test_an_org_repository_outside_the_catalog_is_kept_as_a_repository() -> None:
    projects = _projects()
    known = {f"{ORG}/project-zabake".lower(): 77}
    found = resolve_repository(projects, known, [ORG], owner=ORG, name="project-zabake", url="")
    assert found == Repository(f"{ORG}/project-zabake".lower(), 77, None)
    # Not known to the App yet: kept by name, no id.
    fresh = resolve_repository(projects, known, [ORG.lower()], owner=ORG, name="brand-new")
    assert fresh == Repository(f"{ORG}/brand-new".lower(), None, None)
    # Without the org being watched, only the catalog's count.
    assert resolve_repository(projects, known, [], owner=ORG, name="project-zabake") is None
    # A catalogued repository named by its attributes keeps its project.
    hermes = resolve_repository(projects, known, [ORG], owner=ORG, name="hermes")
    assert hermes is not None and hermes.project_id == projects[0].id


def test_a_repository_outside_the_watched_orgs_is_dropped() -> None:
    projects = _projects()
    assert resolve_repository(projects, {}, [ORG], owner="someone", name="side-project") is None
    url = "https://github.com/someone/side-project.git"
    assert resolve_repository(projects, {}, [ORG], url=url) is None
    # tracesis.project still wins over the repository attributes.
    tagged = resolve_repository(projects, {}, [ORG], tag="hermes", owner="someone", name="x")
    assert tagged is not None and tagged.project_id == projects[0].id


def test_the_repository_is_read_from_owner_name_or_url() -> None:
    hermes = f"{ORG}/hermes".lower()
    assert repository_of(ORG, "Hermes", "") == hermes
    assert repository_of("", f"{ORG}/Hermes", "") == hermes
    assert repository_of(ORG, "Hermes.git", "") == hermes
    url = f"https://github.com/{ORG}/Fikir-Sepeti.git"
    assert repository_of("", "", url) == f"{ORG}/fikir-sepeti".lower()
    assert repository_of("", "", f"git@github.com:{ORG}/logislot.git") == f"{ORG}/logislot".lower()
    assert repository_of("", "", "") is None
    # Nothing that could be an address or a path ever becomes a repository.
    assert repository_of("me@example.test", "x", "") is None
    assert repository_of("x", "a b", "") is None


def test_a_non_catalogued_commit_has_no_services() -> None:
    target = Target(
        project_id=None,
        project_key=None,
        owner=ORG,
        name="project-zabake",
        branch="main",
        repository_external_id=77,
        installation_external_id=2,
        services=[],
    )
    detail = {"stats": {"additions": 1, "deletions": 0}, "files": [{"filename": "a/b.py"}]}
    row = commit_row(target, _item(), detail)
    assert row is not None
    assert (row["project_id"], row["repository_external_id"], row["services"]) == (None, 77, [])
    assert row["unmapped_files"] == 0 and row["repository"] == f"{ORG}/project-zabake"


def test_repository_node_ids_are_stable() -> None:
    assert repository_id(77, f"{ORG}/X") == "gh:77"
    assert repository_id(None, f"{ORG}/X") == f"repo:{ORG.lower()}/x"


def test_watched_orgs_are_github_logins() -> None:
    settings = Settings(dev_activity_orgs=[" Duosis-Developer-Team "])
    assert settings.dev_activity_orgs == ["Duosis-Developer-Team"]
    with pytest.raises(ValueError):
        Settings(dev_activity_orgs=["not an org/"])
    assert Settings().dev_activity_orgs == []


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


def test_usage_reads_are_paged_in_a_fixed_order() -> None:
    from drake_api.dev_activity.collector import usage_statements

    for sql in usage_statements("otel"):
        assert "ORDER BY" in sql and "OFFSET {offset:UInt32}" in sql


def test_usage_pages_read_until_a_short_page() -> None:
    import asyncio
    from types import SimpleNamespace

    from drake_api.dev_activity import collector

    offsets: list[str] = []

    class Store:
        async def query(self, statement, parameters, **_kw):  # type: ignore[no-untyped-def]
            offsets.append(parameters["offset"])
            size = collector.MAX_USAGE_ROWS if len(offsets) < 3 else 7
            return SimpleNamespace(rows=[{"n": i} for i in range(size)])

    rows = asyncio.run(collector._read_pages(Store(), "SELECT 1", {"start": "x"}, "days"))  # type: ignore[arg-type]
    assert len(rows) == 2 * collector.MAX_USAGE_ROWS + 7
    assert offsets == ["0", str(collector.MAX_USAGE_ROWS), str(2 * collector.MAX_USAGE_ROWS)]
