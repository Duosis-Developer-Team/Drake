"""People's GitHub profile pictures, read server-side for the development map.

The browser never talks to GitHub: the map asks Tracesis, and Tracesis asks
GitHub — once a day per person at most.

- The login is resolved to GitHub's numeric user id through the App
  (`GET /users/{login}`, an installation token narrowed to `metadata: read`).
- The picture is then read from one fixed host, `avatars.githubusercontent.com`,
  by that numeric id alone — never from a URL GitHub's answer carries, so the
  answer cannot steer the request elsewhere. No redirects are followed.
- Only PNG and JPEG are served (recognised by their magic bytes, never by a
  header), at most `MAX_BYTES`; anything else is "no avatar".
- Kept in a small in-process LRU: a picture for a day, an absence for an
  hour. A GitHub failure is not remembered, so a blip never hides a face.

Bots have no face here, and a person with no GitHub login has none either:
the map draws their initials.
"""

from __future__ import annotations

import logging
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from drake_api.catalog.logos import sniff
from drake_api.github_app.client import GitHubNotFoundError

logger = logging.getLogger(__name__)

AVATAR_ORIGIN = "https://avatars.githubusercontent.com"
AVATAR_SIZE = 64
MAX_BYTES = 200 * 1024
HIT_SECONDS = 24 * 60 * 60
MISS_SECONDS = 60 * 60
CAPACITY = 512
PERMISSIONS: dict[str, str] = {"metadata": "read"}
SERVED = ("image/png", "image/jpeg")

#: A GitHub login: letters, digits and single hyphens, at most 39 characters.
LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}")


@dataclass(frozen=True)
class Avatar:
    content: bytes
    media_type: str


class _Users(Protocol):
    async def installation_token(
        self,
        installation_id: int,
        *,
        repository_ids: list[int] | None = ...,
        permissions: dict[str, str] | None = ...,
    ) -> Any: ...

    async def get_user(self, token: Any, login: str) -> dict[str, Any]: ...


class Lru:
    """A bounded map whose entries expire; the least recently used goes first."""

    def __init__(self, capacity: int = CAPACITY) -> None:
        self._capacity = capacity
        self._items: OrderedDict[str, tuple[float, Avatar | None]] = OrderedDict()

    def get(self, key: str, now: float) -> tuple[bool, Avatar | None]:
        item = self._items.get(key)
        if item is None:
            return False, None
        expires, value = item
        if expires <= now:
            del self._items[key]
            return False, None
        self._items.move_to_end(key)
        return True, value

    def put(self, key: str, value: Avatar | None, ttl: float, now: float) -> None:
        self._items[key] = (now + ttl, value)
        self._items.move_to_end(key)
        while len(self._items) > self._capacity:
            self._items.popitem(last=False)

    def __len__(self) -> int:
        return len(self._items)


async def read_image(
    user_id: int, *, transport: httpx.AsyncBaseTransport | None = None, timeout: float = 5.0
) -> Avatar | None:
    """The picture of GitHub user `user_id`, or None when it is not a small PNG/JPEG."""
    async with httpx.AsyncClient(
        base_url=AVATAR_ORIGIN, timeout=timeout, follow_redirects=False, transport=transport
    ) as client:
        request = client.build_request(
            "GET",
            f"/u/{int(user_id)}",
            params={"s": str(AVATAR_SIZE), "v": "4"},
            headers={"Accept": "image/png, image/jpeg", "User-Agent": "tracesis-control-plane"},
        )
        response = await client.send(request, stream=True)
        try:
            if response.status_code != 200:
                return None
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > MAX_BYTES:
                    return None
                chunks.append(chunk)
        finally:
            await response.aclose()
    content = b"".join(chunks)
    media_type = sniff(content)
    if media_type not in SERVED:
        return None
    return Avatar(content, media_type)


class Avatars:
    """Login → picture, cached. One per process."""

    def __init__(
        self,
        users: _Users | None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        cache: Lru | None = None,
        clock: Any = time.monotonic,
    ) -> None:
        self._users = users
        self._transport = transport
        self._cache = cache or Lru()
        self._clock = clock

    async def of_login(self, login: str, installation_id: int | None) -> Avatar | None:
        login = login.strip()
        if not LOGIN.fullmatch(login) or self._users is None or installation_id is None:
            return None
        key = login.lower()
        now = self._clock()
        hit, avatar = self._cache.get(key, now)
        if hit:
            return avatar
        try:
            token = await self._users.installation_token(
                installation_id, permissions=dict(PERMISSIONS)
            )
            user = await self._users.get_user(token, login)
            user_id = user.get("id")
            if not isinstance(user_id, int) or user_id <= 0 or user.get("type") == "Bot":
                avatar = None
            else:
                avatar = await read_image(user_id, transport=self._transport)
        except Exception as error:  # a face is decoration, never an outage
            if isinstance(error, GitHubNotFoundError):  # no such user: an absence
                self._cache.put(key, None, MISS_SECONDS, now)
            else:
                logger.warning("dev activity: avatar unavailable (%s)", type(error).__name__)
            return None
        self._cache.put(key, avatar, HIT_SECONDS if avatar else MISS_SECONDS, now)
        return avatar
