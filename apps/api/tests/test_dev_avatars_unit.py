"""People's GitHub pictures: one fixed host, by numeric id, small PNG/JPEG only, cached."""

from typing import Any

import httpx
import pytest
from drake_api.dev_activity.avatars import (
    AVATAR_ORIGIN,
    MAX_BYTES,
    Avatar,
    Avatars,
    Lru,
    read_image,
)
from drake_api.github_app.client import GitHubNotFoundError, GitHubUnavailableError

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def transport(body: bytes, status: int = 200, seen: list[httpx.Request] | None = None) -> Any:
    def handle(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, content=body, headers={"Content-Type": "image/png"})

    return httpx.MockTransport(handle)


class Users:
    def __init__(self, answer: dict[str, Any] | Exception) -> None:
        self.answer = answer
        self.calls: list[str] = []
        self.scopes: list[dict[str, Any]] = []

    async def installation_token(self, installation_id: int, **scope: Any) -> str:
        self.scopes.append({"installation": installation_id, **scope})
        return "token"

    async def get_user(self, token: Any, login: str) -> dict[str, Any]:
        self.calls.append(login)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


async def test_the_picture_is_read_from_the_avatar_host_by_numeric_id() -> None:
    seen: list[httpx.Request] = []
    avatar = await read_image(583231, transport=transport(PNG, seen=seen))
    assert avatar == Avatar(PNG, "image/png")
    [request] = seen
    assert str(request.url).startswith(f"{AVATAR_ORIGIN}/u/583231?")
    assert request.url.params["s"] == "64"


@pytest.mark.parametrize(
    ("body", "status"),
    [
        (b"GIF89a" + b"\x00" * 10, 200),  # not PNG/JPEG
        (b"<svg onload=alert(1)>", 200),  # never SVG
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * (MAX_BYTES + 1), 200),  # too large
        (PNG, 302),  # a redirect is not followed
        (PNG, 404),
    ],
)
async def test_anything_but_a_small_png_or_jpeg_is_no_picture(body: bytes, status: int) -> None:
    assert await read_image(1, transport=transport(body, status)) is None


async def test_jpeg_is_served() -> None:
    avatar = await read_image(1, transport=transport(JPEG))
    assert avatar is not None and avatar.media_type == "image/jpeg"


async def test_a_login_is_looked_up_once_a_day_through_a_metadata_token() -> None:
    clock = [1000.0]
    users = Users({"id": 42, "type": "User"})
    avatars = Avatars(users, transport=transport(PNG), clock=lambda: clock[0])
    first = await avatars.of_login("GencayCoskunTR", 7)
    again = await avatars.of_login("gencaycoskuntr", 7)
    assert first == again == Avatar(PNG, "image/png")
    assert users.calls == ["GencayCoskunTR"]
    assert users.scopes == [{"installation": 7, "permissions": {"metadata": "read"}}]
    clock[0] += 24 * 60 * 60 + 1  # a day later: read again
    await avatars.of_login("gencaycoskuntr", 7)
    assert len(users.calls) == 2


async def test_bots_unknown_logins_and_unsafe_logins_have_no_picture() -> None:
    bot = Avatars(Users({"id": 9, "type": "Bot"}), transport=transport(PNG))
    assert await bot.of_login("dependabot", 1) is None
    missing = Users(GitHubNotFoundError("nope"))
    avatars = Avatars(missing, transport=transport(PNG))
    assert await avatars.of_login("ghost-person", 1) is None
    assert await avatars.of_login("ghost-person", 1) is None
    assert missing.calls == ["ghost-person"]  # an absence is remembered
    for unsafe in ("../etc", "a/b", "me@example.test", "-lead", "x" * 40, ""):
        assert await avatars.of_login(unsafe, 1) is None
    assert missing.calls == ["ghost-person"]
    # No App, or no installation: nobody is asked.
    assert await Avatars(None).of_login("someone", 1) is None
    assert await avatars.of_login("someone", None) is None


async def test_a_github_failure_is_not_remembered() -> None:
    users = Users(GitHubUnavailableError("down"))
    avatars = Avatars(users, transport=transport(PNG))
    assert await avatars.of_login("someone", 1) is None
    users.answer = {"id": 5, "type": "User"}
    assert await avatars.of_login("someone", 1) == Avatar(PNG, "image/png")


def test_the_cache_is_bounded_and_expires() -> None:
    cache = Lru(capacity=2)
    a = Avatar(PNG, "image/png")
    cache.put("a", a, 10, now=0)
    cache.put("b", None, 10, now=0)
    assert cache.get("a", now=1) == (True, a)  # a is now the most recent
    cache.put("c", a, 10, now=1)
    assert len(cache) == 2
    assert cache.get("b", now=1) == (False, None)  # the least recent went first
    assert cache.get("a", now=11) == (False, None)  # expired
