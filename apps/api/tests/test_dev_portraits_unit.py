"""Portraits: recognised by their bytes, sized from their headers, refused when unfit."""

from __future__ import annotations

import struct
import zlib

import pytest
from drake_api.dev_activity.portraits import PortraitError, check_key, inspect, main


def png(width: int, height: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IEND", b"")


def jpeg(width: int, height: int) -> bytes:
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    dht = b"\xff\xc4" + struct.pack(">H", 5) + b"\x00\x00\x00"
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app0 + dht + sof + b"\xff\xd9"


def webp_vp8x(width: int, height: int) -> bytes:
    body = b"VP8X" + struct.pack("<I", 10) + b"\x00\x00\x00\x00"
    body += (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WEBP" + body


def webp_vp8l(width: int, height: int) -> bytes:
    bits = (width - 1) | ((height - 1) << 14)
    body = b"VP8L" + struct.pack("<I", 5) + b"\x2f" + bits.to_bytes(4, "little")
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WEBP" + body


def webp_vp8(width: int, height: int) -> bytes:
    frame = b"\x00\x00\x00" + b"\x9d\x01\x2a" + struct.pack("<HH", width, height)
    body = b"VP8 " + struct.pack("<I", len(frame)) + frame
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WEBP" + body


@pytest.mark.parametrize(
    ("content", "media_type"),
    [
        (png(512, 512), "image/png"),
        (jpeg(512, 500), "image/jpeg"),
        (webp_vp8x(512, 512), "image/webp"),
        (webp_vp8l(300, 310), "image/webp"),
        (webp_vp8(256, 256), "image/webp"),
    ],
)
def test_sized_from_the_header(content: bytes, media_type: str) -> None:
    image = inspect(content)
    assert image.media_type == media_type
    assert 0.9 <= image.width / image.height <= 1.1


def test_reads_the_exact_size() -> None:
    assert (inspect(jpeg(640, 600)).width, inspect(jpeg(640, 600)).height) == (640, 600)
    assert (inspect(webp_vp8l(300, 310)).width, inspect(webp_vp8l(300, 310)).height) == (300, 310)


@pytest.mark.parametrize(
    ("content", "why"),
    [
        (b"", "empty"),
        (b"GIF89a" + b"\x00" * 40, "not a PNG"),
        (png(512, 300), "not square"),
        (png(32, 32), "64-2048"),
        (png(4096, 4096), "64-2048"),
        (png(512, 512) + b"\x00" * (512 * 1024), "at most 512 KB"),
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 30, "IHDR"),
    ],
)
def test_refuses_what_cannot_be_a_portrait(content: bytes, why: str) -> None:
    with pytest.raises(PortraitError, match=why):
        inspect(content)


def test_a_person_key_is_never_an_email() -> None:
    assert check_key("gencay") == "gencay"
    for bad in ("a@b.c", "", "x/y", "a b", "k" * 101):
        with pytest.raises(PortraitError):
            check_key(bad)


def test_the_cli_refuses_a_bad_file_before_touching_the_database(tmp_path, capsys) -> None:
    path = tmp_path / "enes.png"
    path.write_bytes(png(512, 200))
    assert main(["set", "eneserman", str(path)]) == 2
    assert "not square" in capsys.readouterr().err
    assert main(["set", "a@b", str(path)]) == 2
