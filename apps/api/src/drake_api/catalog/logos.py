"""Project logos, read from the project's own repository.

Duosis repositories carry their brand under `.duosis/media/`:
`logo-dark.png` for dark backgrounds and `logo-light.png` for light ones.
Tracesis shows that logo next to the project wherever the project appears,
so a list of services reads as Hermes, LogiSlot, Fikir Sepeti rather than
three grey keys.

Read-only and bounded, like every other repository read:

- through the GitHub App installation token, narrowed to the one repository
  and to `metadata: read` + `contents: read`;
- pinned to the default branch's head commit, never "the branch";
- only the two fixed paths, never a listing or a search;
- only raster images (PNG, JPEG, WebP — recognised by their magic bytes, not
  the file name), at most `MAX_BYTES`. SVG is refused: it can carry script.
- cached in Redis: an answer for `CACHE_SECONDS`, an absence for
  `MISS_SECONDS`, so a page of thirty services costs GitHub nothing after
  the first view.

A repository behind a security gate, without granted access, or without the
files simply has no logo; the caller draws a monogram instead.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from drake_api.github_app.client import GitHubNotFoundError

logger = logging.getLogger(__name__)

THEMES = ("dark", "light")
LOGO_PATHS: dict[str, tuple[str, ...]] = {
    "dark": (".duosis/media/logo-dark.png", ".duosis/media/logo-light.png"),
    "light": (".duosis/media/logo-light.png", ".duosis/media/logo-dark.png"),
}
MAX_BYTES = 512 * 1024
CACHE_SECONDS = 6 * 60 * 60
MISS_SECONDS = 60 * 60
PERMISSIONS: dict[str, str] = {"metadata": "read", "contents": "read"}


@dataclass(frozen=True)
class LogoRepository:
    installation_external_id: int
    repository_external_id: int
    owner: str
    name: str
    default_branch: str


@dataclass(frozen=True)
class Logo:
    content: bytes
    media_type: str


class _Client(Protocol):
    async def installation_token(
        self,
        installation_id: int,
        *,
        repository_ids: list[int] | None = ...,
        permissions: dict[str, str] | None = ...,
    ) -> Any: ...

    async def resolve_branch_head(self, token: Any, owner: str, repo: str, branch: str) -> str: ...

    async def get_content(
        self, token: Any, owner: str, repo: str, path: str, ref: str
    ) -> dict[str, Any]: ...


class _Cache(Protocol):
    async def get(self, key: str) -> Any: ...

    async def set(self, key: str, value: str, ex: int | None = ...) -> Any: ...


def sniff(content: bytes) -> str | None:
    """The image type from its first bytes, or None if it is not one we serve."""
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return None


def cache_key(project_id: str, theme: str) -> str:
    return f"catalog:logo:v1:{project_id}:{theme}"


async def _cached(cache: _Cache, key: str) -> tuple[bool, Logo | None]:
    with contextlib.suppress(Exception):
        raw = await cache.get(key)
        if raw:
            value = json.loads(raw)
            if value.get("none"):
                return True, None
            return True, Logo(base64.b64decode(value["b64"]), str(value["media_type"]))
    return False, None


async def _remember(cache: _Cache, key: str, logo: Logo | None) -> None:
    with contextlib.suppress(Exception):
        if logo is None:
            await cache.set(key, json.dumps({"none": True}), ex=MISS_SECONDS)
        else:
            payload = {
                "media_type": logo.media_type,
                "b64": base64.b64encode(logo.content).decode(),
            }
            await cache.set(key, json.dumps(payload), ex=CACHE_SECONDS)


async def read_logo(client: _Client, repository: LogoRepository, theme: str) -> Logo | None:
    """The first usable logo file for `theme`, or None. Never raises on absence."""
    token = await client.installation_token(
        repository.installation_external_id,
        repository_ids=[repository.repository_external_id],
        permissions=dict(PERMISSIONS),
    )
    commit = await client.resolve_branch_head(
        token, repository.owner, repository.name, repository.default_branch
    )
    for path in LOGO_PATHS[theme]:
        try:
            entry = await client.get_content(token, repository.owner, repository.name, path, commit)
        except GitHubNotFoundError:
            continue
        if entry.get("type", "file") != "file" or entry.get("encoding") != "base64":
            continue
        if int(entry.get("size") or 0) > MAX_BYTES:
            continue
        try:
            content = base64.b64decode(str(entry.get("content", "")), validate=False)
        except (binascii.Error, ValueError):
            continue
        if not content or len(content) > MAX_BYTES:
            continue
        media_type = sniff(content)
        if media_type is None:
            continue
        return Logo(content, media_type)
    return None


async def project_logo(
    client: _Client,
    cache: _Cache,
    project_id: str,
    repository: LogoRepository | None,
    theme: str,
) -> Logo | None:
    """Cached logo for a project. Provider failures are logged and read as absent."""
    key = cache_key(project_id, theme)
    hit, logo = await _cached(cache, key)
    if hit:
        return logo
    if repository is None:
        await _remember(cache, key, None)
        return None
    try:
        logo = await read_logo(client, repository, theme)
    except Exception as error:  # a logo is decoration, never an outage
        # Not cached: a transient GitHub failure must not hide the logo for an hour.
        logger.warning("catalog: project logo unavailable (%s)", type(error).__name__)
        return None
    await _remember(cache, key, logo)
    return logo
