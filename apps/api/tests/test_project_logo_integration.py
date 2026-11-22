"""Project logos (real PostgreSQL + Redis, fake GitHub).

The logo is read from the project's own repository, `.duosis/media/`, through
the GitHub App: narrowed to one repository and `metadata`/`contents` read,
pinned to the default branch's head commit, raster images only, cached in
Redis. Anything else is a 204 the UI turns into a monogram; 404 is an invisible project.
"""

import base64
import uuid
from pathlib import Path
from typing import Any

import pytest
from drake_api.catalog import logos
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from test_catalog_api_integration import login_all
from test_github_integration import (
    HERMES_ID,
    LOGISLOT_ID,
    _seed_admin,
    deliver,
    github_harness,
    installation_payload,
)

pytestmark = pytest.mark.integration

# A real PNG signature followed by an IHDR chunk header: enough to be sniffed.
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 32
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


def file_entry(content: bytes) -> dict[str, Any]:
    return {
        "type": "file",
        "encoding": "base64",
        "size": len(content),
        "content": base64.b64encode(content).decode(),
    }


async def make_project(engine: AsyncEngine, repo_external_id: int | None) -> uuid.UUID:
    """A project, bound to the given GitHub repository when one is named."""
    suffix = uuid.uuid4().hex[:8]
    async with engine.begin() as connection:
        org = (
            await connection.execute(
                text(
                    "SELECT id FROM scopes WHERE scope_type='organization' AND external_ref='root'"
                )
            )
        ).scalar_one()
        scope = (
            await connection.execute(
                text(
                    "INSERT INTO scopes (scope_type, external_ref, parent_id) "
                    "VALUES ('project', :ref, :parent) RETURNING id"
                ),
                {"ref": f"p-{suffix}", "parent": org},
            )
        ).scalar_one()
        project = (
            await connection.execute(
                text(
                    "INSERT INTO projects (project_key, display_name, repo_provider, "
                    "repo_owner, repo_name, tenant_model, catalog_source_kind, scope_id) "
                    "VALUES (:k, 'Logo', 'github', 'Duosis-Developer-Team', :k, 'none', "
                    "'manifest', :scope) RETURNING id"
                ),
                {"k": f"logo{suffix}", "scope": scope},
            )
        ).scalar_one()
        if repo_external_id is not None:
            repository = (
                await connection.execute(
                    text("SELECT id FROM github_repositories WHERE external_id = :id"),
                    {"id": repo_external_id},
                )
            ).scalar_one()
            await connection.execute(
                text(
                    "UPDATE github_repositories SET access_state = 'accessible', "
                    "default_branch = 'main', security_gate = NULL WHERE id = :id"
                ),
                {"id": repository},
            )
            await connection.execute(
                text(
                    "INSERT INTO github_repository_projects "
                    "(repository_id, project_id, scope_id, commit_sha, manifest_digest) "
                    "VALUES (:r, :p, :s, :sha, 'sha256:test')"
                ),
                {"r": repository, "p": project, "s": scope, "sha": "a" * 40},
            )
    return uuid.UUID(str(project))


async def installed(harness: Any, engine: AsyncEngine) -> None:
    await _seed_admin(harness, engine)
    await login_all(harness, ["user-plain"])
    async with harness.api_client() as client:
        response = await deliver(client, "installation", installation_payload(), str(uuid.uuid4()))
        assert response.status_code == 202


def provider_calls(fake: Any) -> list[str]:
    return [call for call in fake.calls if "/repos/" in call or "access_tokens" in call]


async def test_logo_is_read_narrowly_pinned_and_cached(engine: AsyncEngine, tmp_path: Path) -> None:
    harness, fake = github_harness(tmp_path)
    await installed(harness, engine)
    fake.trees["Hermes"] = {".duosis/media/logo-dark.png": file_entry(PNG)}
    project = await make_project(engine, HERMES_ID)
    fake.calls.clear()
    fake.token_requests.clear()

    async with harness.api_client() as client:
        await harness.login(client, "user-owner")
        first = await client.get(f"/v1/projects/{project}/logo?theme=dark")
        assert first.status_code == 200, first.text
        assert first.content == PNG
        assert first.headers["content-type"] == "image/png"
        assert first.headers["x-content-type-options"] == "nosniff"
        assert first.headers["cache-control"].startswith("private")

        # Least privilege: one repository, read-only metadata + contents.
        assert fake.token_requests, "the read must mint a token"
        for body in fake.token_requests:
            assert body.get("repository_ids") == [HERMES_ID]
            assert body.get("permissions") == {"metadata": "read", "contents": "read"}
        # Pinned: the fake answers Contents only at the head commit (any
        # other ref is a 404), so a 200 above proves the read named it.
        reads = [call for call in fake.calls if "/contents/" in call]
        assert reads, fake.calls
        assert all(call.startswith("GET ") for call in fake.calls if "/repos/" in call)

        # The light theme falls back to the dark file when it is the only one.
        light = await client.get(f"/v1/projects/{project}/logo?theme=light")
        assert light.status_code == 200
        assert light.content == PNG

        # Served from Redis afterwards: GitHub sees nothing more.
        fake.calls.clear()
        again = await client.get(f"/v1/projects/{project}/logo?theme=dark")
        assert again.status_code == 200
        assert again.content == PNG
        assert provider_calls(fake) == []

        bad_theme = await client.get(f"/v1/projects/{project}/logo?theme=neon")
        assert bad_theme.status_code == 422


async def test_non_raster_oversized_and_missing_logos_are_no_content(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, fake = github_harness(tmp_path)
    await installed(harness, engine)
    # An SVG named .png is still an SVG: refused by its bytes, not its name.
    fake.trees["Hermes"] = {".duosis/media/logo-dark.png": file_entry(SVG)}
    disguised = await make_project(engine, HERMES_ID)

    async with harness.api_client() as client:
        await harness.login(client, "user-owner")
        assert (await client.get(f"/v1/projects/{disguised}/logo")).status_code == 204

    async with engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM github_repository_projects WHERE project_id = :p"), {"p": disguised}
        )
    big = PNG + b"\x00" * (logos.MAX_BYTES + 1)
    fake.trees["logislot"] = {".duosis/media/logo-dark.png": file_entry(big)}
    oversized = await make_project(engine, LOGISLOT_ID)
    unbound = await make_project(engine, None)
    fake.calls.clear()

    async with harness.api_client() as client:
        await harness.login(client, "user-owner")
        assert (await client.get(f"/v1/projects/{oversized}/logo")).status_code == 204
        fake.calls.clear()
        # No repository: no provider call at all.
        assert (await client.get(f"/v1/projects/{unbound}/logo")).status_code == 204
        assert provider_calls(fake) == []


async def test_gated_repository_makes_no_provider_call(engine: AsyncEngine, tmp_path: Path) -> None:
    harness, fake = github_harness(tmp_path)
    await installed(harness, engine)
    fake.trees["Hermes"] = {".duosis/media/logo-dark.png": file_entry(PNG)}
    project = await make_project(engine, HERMES_ID)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE github_repositories SET security_gate = 'manual_review' "
                "WHERE external_id = :id"
            ),
            {"id": HERMES_ID},
        )
    fake.calls.clear()

    async with harness.api_client() as client:
        await harness.login(client, "user-owner")
        assert (await client.get(f"/v1/projects/{project}/logo")).status_code == 204
    assert provider_calls(fake) == [], fake.calls


async def test_invisible_project_is_404_without_a_provider_call(
    engine: AsyncEngine, tmp_path: Path
) -> None:
    harness, fake = github_harness(tmp_path)
    await installed(harness, engine)
    fake.trees["Hermes"] = {".duosis/media/logo-dark.png": file_entry(PNG)}
    project = await make_project(engine, HERMES_ID)
    fake.calls.clear()

    async with harness.api_client() as client:
        await harness.login(client, "user-plain")
        assert (await client.get(f"/v1/projects/{project}/logo")).status_code == 404
        assert (await client.get(f"/v1/projects/{uuid.uuid4()}/logo")).status_code == 404
    assert provider_calls(fake) == []

    async with harness.api_client() as client:
        assert (await client.get(f"/v1/projects/{project}/logo")).status_code == 401


async def test_provider_outage_is_not_cached(engine: AsyncEngine, tmp_path: Path) -> None:
    harness, fake = github_harness(tmp_path)
    await installed(harness, engine)
    fake.trees["Hermes"] = {".duosis/media/logo-dark.png": file_entry(PNG)}
    project = await make_project(engine, HERMES_ID)

    async with harness.api_client() as client:
        await harness.login(client, "user-owner")
        fake.mode = "unavailable"
        assert (await client.get(f"/v1/projects/{project}/logo")).status_code == 204
        # Recovery shows the logo at once: an outage is not remembered as "none".
        fake.mode = "ok"
        recovered = await client.get(f"/v1/projects/{project}/logo")
        assert recovered.status_code == 200
        assert recovered.content == PNG


async def test_a_site_logo_under_public_brand_is_found(engine: AsyncEngine, tmp_path: Path) -> None:
    """A web app keeps its brand with the site: `public/brand/<repo>-logo.png`
    for dark backgrounds, `-logo-light.png` for light ones."""
    harness, fake = github_harness(tmp_path)
    await installed(harness, engine)
    dark = PNG + b"dark"
    light = PNG + b"light"
    fake.trees["logislot"] = {
        "public/brand/logislot-logo.png": file_entry(dark),
        "public/brand/logislot-logo-light.png": file_entry(light),
    }
    project = await make_project(engine, LOGISLOT_ID)

    async with harness.api_client() as client:
        await harness.login(client, "user-owner")
        on_dark = await client.get(f"/v1/projects/{project}/logo?theme=dark")
        on_light = await client.get(f"/v1/projects/{project}/logo?theme=light")
    assert on_dark.status_code == 200 and on_dark.content == dark
    assert on_light.status_code == 200 and on_light.content == light


def test_candidate_paths_are_fixed_and_derived() -> None:
    assert logos.candidate_paths("dark", "Fikir-Sepeti") == (
        ".duosis/media/logo-dark.png",
        ".duosis/media/logo-light.png",
        "public/brand/fikirsepeti-logo.png",
        "public/brand/fikirsepeti-logo-light.png",
    )
    assert logos.candidate_paths("light", "../../etc")[2] == "public/brand/etc-logo-light.png"
