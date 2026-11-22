"""Hand-made portraits for the development map, set by an operator.

A portrait is one square image per person, made once (the team's own
pixel-art portraits), that replaces the person's GitHub picture everywhere
on the map. Run inside the API pod:

    python -m drake_api.dev_activity.portraits list
    python -m drake_api.dev_activity.portraits set <person_key> <file>
    python -m drake_api.dev_activity.portraits remove <person_key>

The person key is the map's: an alias person's key when an alias groups
several identities (`gencay` for gencaycoskuntr + coskungencay), otherwise
the GitHub login in lower case. `list` prints every person on the map with
whether a portrait is set.

The file is recognised by its bytes, never its name: PNG, JPEG or WebP,
square (width/height between 0.9 and 1.1), 64-2048 pixels a side, at most
512 KB. 512 x 512 is the size to aim for.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

from drake_api.catalog.logos import sniff
from drake_api.db import get_engine
from drake_api.dev_activity import repository as repo
from drake_api.settings import get_settings

MAX_BYTES = 512 * 1024
MIN_SIDE = 64
MAX_SIDE = 2048
MEDIA_TYPES = ("image/png", "image/jpeg", "image/webp")
#: The shape of a person key: no '@' (never an e-mail), no slash or space.
PERSON_KEY = re.compile(r"[^@\s/]{1,100}")


class PortraitError(ValueError):
    pass


@dataclass(frozen=True)
class Image:
    media_type: str
    width: int
    height: int


def _png(content: bytes) -> tuple[int, int]:
    if len(content) < 24 or content[12:16] != b"IHDR":
        raise PortraitError("a PNG without an IHDR header")
    width, height = struct.unpack(">II", content[16:24])
    return width, height


def _jpeg(content: bytes) -> tuple[int, int]:
    i = 2
    while i + 4 <= len(content):
        if content[i] != 0xFF:
            i += 1
            continue
        marker = content[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7 or marker == 0xFF:
            i += 1 if marker == 0xFF else 2
            continue
        length = struct.unpack(">H", content[i + 2 : i + 4])[0]
        # Start-of-frame markers carry the size; DHT (C4), JPG (C8) and DAC (CC) do not.
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if i + 9 > len(content):
                break
            height, width = struct.unpack(">HH", content[i + 5 : i + 9])
            return width, height
        i += 2 + length
    raise PortraitError("a JPEG without a frame header")


def _webp(content: bytes) -> tuple[int, int]:
    chunk = content[12:16]
    if chunk == b"VP8X" and len(content) >= 30:
        width = 1 + int.from_bytes(content[24:27], "little")
        height = 1 + int.from_bytes(content[27:30], "little")
        return width, height
    if chunk == b"VP8 " and len(content) >= 30 and content[23:26] == b"\x9d\x01\x2a":
        width, height = struct.unpack("<HH", content[26:30])
        return width & 0x3FFF, height & 0x3FFF
    if chunk == b"VP8L" and len(content) >= 25 and content[20] == 0x2F:
        bits = int.from_bytes(content[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    raise PortraitError("a WebP without a size header")


def inspect(content: bytes) -> Image:
    """The image's type and size, or a PortraitError saying why it cannot be a portrait."""
    if not content:
        raise PortraitError("the file is empty")
    if len(content) > MAX_BYTES:
        raise PortraitError(
            f"the file is {len(content) // 1024} KB; at most {MAX_BYTES // 1024} KB"
        )
    media_type = sniff(content)
    if media_type not in MEDIA_TYPES:
        raise PortraitError("not a PNG, JPEG or WebP image")
    reader = {"image/png": _png, "image/jpeg": _jpeg, "image/webp": _webp}[media_type]
    width, height = reader(content)
    if min(width, height) < MIN_SIDE or max(width, height) > MAX_SIDE:
        raise PortraitError(f"{width}x{height}: each side must be {MIN_SIDE}-{MAX_SIDE} pixels")
    if not 0.9 <= width / height <= 1.1:
        raise PortraitError(f"{width}x{height} is not square")
    return Image(media_type, width, height)


def check_key(person_key: str) -> str:
    if not PERSON_KEY.fullmatch(person_key):
        raise PortraitError("a person key is 1-100 characters without '@', '/' or spaces")
    return person_key


async def _list() -> int:
    async with get_engine(get_settings()).connect() as connection:
        people = await repo.map_people(connection)
        portraits = {p["person_key"]: p for p in await repo.list_portraits(connection)}
    keys = sorted(set(people) | set(portraits))
    print(f"{'person key':<28} {'logins':<36} portrait")
    for key in keys:
        logins = ", ".join(sorted(people.get(key, set()), key=str.lower)) or "-"
        p = portraits.get(key)
        shown = (
            f"{p['media_type']} {p['width']}x{p['height']} {p['bytes'] // 1024} KB" if p else "-"
        )
        missing = "" if key in people else "  (not on the map now)"
        print(f"{key:<28} {logins:<36} {shown}{missing}")
    return 0


async def _set(person_key: str, path: Path) -> int:
    check_key(person_key)
    content = path.read_bytes()
    image = inspect(content)
    async with get_engine(get_settings()).begin() as connection:
        people = await repo.map_people(connection)
        await repo.set_portrait(
            connection,
            person_key=person_key,
            media_type=image.media_type,
            content=content,
            width=image.width,
            height=image.height,
        )
    note = "" if person_key in people else " (nobody with this key is on the map yet)"
    print(f"set {person_key}: {image.media_type} {image.width}x{image.height}{note}")
    return 0


async def _remove(person_key: str) -> int:
    check_key(person_key)
    async with get_engine(get_settings()).begin() as connection:
        removed = await repo.delete_portrait(connection, person_key)
    print(f"removed {person_key}" if removed else f"{person_key} had no portrait")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m drake_api.dev_activity.portraits",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="every person on the map, and whether a portrait is set")
    put = commands.add_parser("set", help="set (or replace) a person's portrait")
    put.add_argument("person_key")
    put.add_argument("file", type=Path)
    drop = commands.add_parser("remove", help="remove a person's portrait")
    drop.add_argument("person_key")
    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            return asyncio.run(_list())
        if args.command == "set":
            return asyncio.run(_set(args.person_key, args.file))
        return asyncio.run(_remove(args.person_key))
    except (PortraitError, OSError) as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
