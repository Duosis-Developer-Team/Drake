#!/usr/bin/env python3
"""Check an office art kit and install it into the web app.

    python3 scripts/office-assets.py KIT_DIR            # check, then install
    python3 scripts/office-assets.py KIT_DIR --check    # check only
    python3 scripts/office-assets.py KIT_DIR --rescale  # shrink fake-upscaled art first

A kit is the shape docs/runbooks/DEVELOPMENT_OFFICE_ASSETS.md describes:
rooms and props at its root, each person's character strips under
`characters/<person>/body-<clip>.png`, and `asset-manifest.json`. Characters
come at one of three densities: 32 x 48 frames (feet on row 45, x 16),
64 x 96 (row 91, x 32) or 128 x 192 (row 180, x 64). Everything is checked
before anything is written: sizes, frame counts, binary alpha, where the feet
stand.

`--rescale` is for drawings exported larger than their native size (each art
pixel a k x k block): it finds k, checks the blocks are flat, and samples one
pixel per block. Standard library only; no image packages.
"""

from __future__ import annotations

import argparse
import json
import shutil
import struct
import sys
import zlib
from pathlib import Path

ENVIRONMENT = {
    "room-product-open": (320, 224, False),
    "room-product-backdoor": (320, 224, False),
    "room-cluster": (384, 224, False),
    "room-lobby": (448, 192, False),
    "floor-corridor": (32, 32, False),
    "desk": (64, 48, True),
    "box": (16, 16, True),
    # The whole office drawn as one map (its size is whatever its office-map.json
    # says; this is the shipped one), and the two props around a seated person.
    "office-map": (768, 480, False),
    "chair": (32, 32, True),
    "desk-front": (64, 40, True),
}
CLIPS = {
    "body-walk-down": 4,
    "body-walk-left": 4,
    "body-walk-up": 4,
    "body-idle-down": 2,
    "body-carry-down": 4,
    "body-carry-left": 4,
    "body-carry-up": 4,
    "body-sit-type": 2,
    "body-coffee": 2,
}
FRAME = (32, 48)
ANCHOR = {1: (16, 45), 2: (32, 91), 4: (64, 180)}
DENSITIES = tuple(FRAME[1] * d for d in ANCHOR)
SKIP = {"preview.png", "README.md", ".DS_Store"}
SAFE_PERSON = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


# --------------------------------------------------------------------------- PNG


def read_png(path: Path) -> tuple[int, int, list[bytearray]]:
    """(width, height, rows of RGBA bytes) for an 8-bit, non-interlaced PNG."""
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat, palette, trns = 8, b"", None, None
    width = height = depth = ctype = interlace = 0
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        kind = data[pos + 4 : pos + 8]
        body = data[pos + 8 : pos + 8 + length]
        if kind == b"IHDR":
            width, height, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", body)
        elif kind == b"PLTE":
            palette = body
        elif kind == b"tRNS":
            trns = body
        elif kind == b"IDAT":
            idat += body
        pos += 12 + length
    if depth != 8 or interlace:
        raise ValueError(f"unsupported PNG (bit depth {depth}, interlace {interlace})")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    raw = zlib.decompress(idat)
    stride = width * channels
    rows: list[bytearray] = []
    prev = bytearray(stride)
    i = 0
    for _ in range(height):
        kind_filter = raw[i]
        line = bytearray(raw[i + 1 : i + 1 + stride])
        i += 1 + stride
        for x in range(stride):
            a = line[x - channels] if x >= channels else 0
            b = prev[x]
            c = prev[x - channels] if x >= channels else 0
            if kind_filter == 1:
                line[x] = (line[x] + a) & 255
            elif kind_filter == 2:
                line[x] = (line[x] + b) & 255
            elif kind_filter == 3:
                line[x] = (line[x] + (a + b) // 2) & 255
            elif kind_filter == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        prev = line
        rgba = bytearray()
        for x in range(width):
            px = line[x * channels : (x + 1) * channels]
            if ctype == 6:
                rgba += px
            elif ctype == 2:
                rgba += px + b"\xff"
            elif ctype == 0:
                rgba += bytes((px[0], px[0], px[0], 255))
            elif ctype == 4:
                rgba += bytes((px[0], px[0], px[0], px[1]))
            else:  # palette
                index = px[0]
                assert palette is not None
                alpha = trns[index] if trns and index < len(trns) else 255
                rgba += palette[index * 3 : index * 3 + 3] + bytes((alpha,))
        rows.append(rgba)
    return width, height, rows


def write_png(path: Path, width: int, height: int, rows: list[bytearray]) -> None:
    raw = b"".join(b"\x00" + bytes(r) for r in rows)

    def chunk(kind: bytes, body: bytes) -> bytes:
        crc = zlib.crc32(kind + body) & 0xFFFFFFFF
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc)

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def alpha(rows: list[bytearray], x: int, y: int) -> int:
    return rows[y][x * 4 + 3]


# --------------------------------------------------------------------------- rescale


def downscale(width: int, height: int, rows: list[bytearray], native: tuple[int, int]):
    """Shrink k x k blocks to single pixels, when the image is native size times k."""
    nw, nh = native
    if (width, height) == native:
        return width, height, rows
    k = width // nw
    if k < 2 or width != nw * k or height != nh * k:
        raise ValueError(f"{width}x{height} is not a whole multiple of {nw}x{nh}")
    flat = total = 0
    out = []
    for by in range(nh):
        line = bytearray()
        for bx in range(nw):
            cx, cy = bx * k + k // 2, by * k + k // 2
            centre = rows[cy][cx * 4 : cx * 4 + 4]
            for dy in range(0, k, max(1, k // 3)):
                for dx in range(0, k, max(1, k // 3)):
                    total += 1
                    px = rows[by * k + dy][(bx * k + dx) * 4 : (bx * k + dx) * 4 + 4]
                    flat += sum(abs(a - b) for a, b in zip(px, centre, strict=True)) < 24
            line += centre
        out.append(line)
    if flat / max(1, total) < 0.85:
        raise ValueError(f"the {k}x{k} blocks are not flat: not pixel art scaled by {k}")
    return nw, nh, out


# --------------------------------------------------------------------------- checks


class Report:
    def __init__(self) -> None:
        self.problems: list[str] = []
        self.notes: list[str] = []

    def fail(self, where: str, what: str) -> None:
        self.problems.append(f"{where}: {what}")


def binary_alpha(rows: list[bytearray], width: int, height: int) -> bool:
    return all(alpha(rows, x, y) in (0, 255) for y in range(height) for x in range(width))


def map_size(kit: Path) -> tuple[int, int]:
    """The map's size as its geometry file gives it, else the shipped one."""
    try:
        size = json.loads((kit / MAP_LAYOUT).read_text()).get("size") or []
        if len(size) == 2 and all(isinstance(v, int) and 256 <= v <= 4096 for v in size):
            return int(size[0]), int(size[1])
    except (OSError, ValueError):
        pass
    return ENVIRONMENT["office-map"][0], ENVIRONMENT["office-map"][1]


def check_environment(kit: Path, report: Report, rescale: bool, staged: dict[str, tuple]) -> None:
    expected = {**ENVIRONMENT, "office-map": (*map_size(kit), False)}
    for name, (w, h, transparent) in expected.items():
        path = kit / f"{name}.png"
        if not path.exists():
            report.notes.append(f"{name}.png missing: the office draws a placeholder")
            continue
        try:
            width, height, rows = read_png(path)
            if rescale:
                width, height, rows = downscale(width, height, rows, (w, h))
        except ValueError as error:
            report.fail(path.name, str(error))
            continue
        if (width, height) != (w, h):
            report.fail(path.name, f"{width}x{height}, expected {w}x{h}")
            continue
        if transparent and not binary_alpha(rows, width, height):
            report.fail(path.name, "alpha is not binary (only fully clear or fully opaque pixels)")
        if not transparent and any(
            alpha(rows, x, y) != 255 for y in range(height) for x in range(width)
        ):
            report.fail(path.name, "must be opaque")
        staged[path.name] = (width, height, rows)


def check_character(
    person_dir: Path, report: Report, rescale: bool, staged: dict[str, tuple]
) -> int | None:
    person = person_dir.name
    if not set(person) <= SAFE_PERSON:
        report.fail(person, "a person key may hold letters, digits, '.', '_' and '-' only")
        return None
    density = None
    for clip, frames in CLIPS.items():
        path = person_dir / f"{clip}.png"
        where = f"characters/{person}/{clip}.png"
        if not path.exists():
            report.fail(where, "missing")
            continue
        try:
            width, height, rows = read_png(path)
            if rescale and height not in DENSITIES:
                width, height, rows = downscale(width, height, rows, (FRAME[0] * frames, FRAME[1]))
        except ValueError as error:
            report.fail(where, str(error))
            continue
        d = height // FRAME[1]
        if height not in DENSITIES:
            report.fail(where, f"height {height}: frames are 48, 96 or 192 tall")
            continue
        if density is None:
            density = d
        elif d != density:
            report.fail(where, f"density {d}x, the other clips are {density}x")
            continue
        fw = FRAME[0] * d
        if width != fw * frames:
            report.fail(where, f"{width} wide: {frames} frames of {fw} are {fw * frames}")
            continue
        if not binary_alpha(rows, width, height):
            report.fail(where, "alpha is not binary")
        ax, ay = ANCHOR[d]
        for f in range(frames):
            xs = [
                x for x in range(f * fw, (f + 1) * fw) for y in range(height) if alpha(rows, x, y)
            ]
            ys = [
                y for y in range(height) for x in range(f * fw, (f + 1) * fw) if alpha(rows, x, y)
            ]
            if not ys:
                report.fail(where, f"frame {f + 1} is empty")
                continue
            if abs(max(ys) - ay) > d:
                report.fail(where, f"frame {f + 1}: feet on row {max(ys)}, expected {ay}")
            centre = (min(xs) + max(xs)) / 2 - f * fw
            if abs(centre - ax) > 4 * d:
                report.fail(where, f"frame {f + 1}: centred on x {centre:.0f}, expected about {ax}")
        staged[f"characters/{person}/{clip}.png"] = (width, height, rows)
    return density


def manifest_for(kit: Path, people: dict[str, int]) -> dict:
    """The kit's manifest, made to describe exactly what is installed."""
    path = kit / "asset-manifest.json"
    manifest = json.loads(path.read_text()) if path.exists() else {}
    densities = set(people.values())
    density = densities.pop() if len(densities) == 1 else 1
    characters = manifest.setdefault("characters", {})
    characters["root"] = "characters"
    characters["frameSize"] = {"width": FRAME[0] * density, "height": FRAME[1] * density}
    characters["anchor"] = {"x": ANCHOR[density][0], "y": ANCHOR[density][1]}
    characters["hasBakedHead"] = True
    characters["variants"] = {
        person: {
            **(characters.get("variants", {}).get(person) or {}),
            "sheets": {clip: f"characters/{person}/{clip}.png" for clip in CLIPS},
        }
        for person in sorted(people)
    }
    environment = manifest.setdefault("environment", {})
    for name in ENVIRONMENT:
        if (kit / f"{name}.png").exists():
            environment.setdefault(name, {})["file"] = f"{name}.png"
    if (kit / "office-map.png").exists() and (kit / MAP_LAYOUT).exists():
        environment["office-map"]["layout"] = MAP_LAYOUT
    return manifest


MAP_LAYOUT = "office-map.json"


def _inside(box: tuple, rect: list) -> bool:
    """Whether an [x, y, w, h] rect lies within an (x1, y1, x2, y2) box."""
    x1, y1, x2, y2 = box
    return x1 <= rect[0] and y1 <= rect[1] and rect[0] + rect[2] <= x2 and rect[1] + rect[3] <= y2


def check_map_layout(kit: Path, report: Report) -> None:
    """The map's geometry: rooms inside the map; screens, doors and floors inside their room."""
    path = kit / MAP_LAYOUT
    if not (kit / "office-map.png").exists():
        return
    if not path.exists():
        report.notes.append(f"{MAP_LAYOUT} missing: the office uses the guide's geometry")
        return
    try:
        layout = json.loads(path.read_text())
    except ValueError as error:
        report.fail(MAP_LAYOUT, f"not JSON: {error}")
        return
    w, h = (layout.get("size") or [0, 0])[:2]
    # The picture's own size, from its header.
    mw, mh = struct.unpack(">II", (kit / "office-map.png").read_bytes()[16:24])
    if (w, h) != (mw, mh):
        report.fail(MAP_LAYOUT, f"size {w}x{h}, the map is {mw}x{mh}")
    rooms = layout.get("rooms") or {}
    kinds = [r.get("kind") for r in rooms.values()]
    if kinds.count("cluster") != 1 or kinds.count("lobby") != 1 or "product" not in kinds:
        report.fail(MAP_LAYOUT, "needs one cluster, one lobby and product rooms")
    for name, room in rooms.items():
        x1, y1, x2, y2 = (room.get("rect") or [0, 0, 0, 0])[:4]
        if not (0 <= x1 < x2 <= w and 0 <= y1 < y2 <= h):
            report.fail(MAP_LAYOUT, f"{name}: rect outside the map")
            continue
        box = (x1, y1, x2, y2)
        if not _inside(box, room.get("screen") or [-1, -1, 0, 0]):
            report.fail(MAP_LAYOUT, f"{name}: screen outside the room")
        if room.get("free_floor") and not _inside(box, room["free_floor"]):
            report.fail(MAP_LAYOUT, f"{name}: free floor outside the room")
        for door in room.get("doors") or []:
            if door.get("side") not in ("N", "S") or not (
                x1 <= door.get("x", -1) <= x2 - door.get("width", 0)
            ):
                report.fail(MAP_LAYOUT, f"{name}: a door off its wall")
        if room.get("kind") == "cluster" and not room.get("loading"):
            report.fail(MAP_LAYOUT, f"{name}: the cluster needs its loading spot")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("kit", type=Path)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "apps/web/public/office",
    )
    parser.add_argument("--check", action="store_true", help="check only; write nothing")
    parser.add_argument(
        "--rescale", action="store_true", help="shrink fake-upscaled art to its native size"
    )
    args = parser.parse_args()
    kit: Path = args.kit
    if not kit.is_dir():
        print(f"{kit}: not a folder", file=sys.stderr)
        return 2
    report = Report()
    staged: dict[str, tuple] = {}
    check_environment(kit, report, args.rescale, staged)
    check_map_layout(kit, report)
    people: dict[str, int] = {}
    root = kit / "characters"
    for person_dir in sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []:
        density = check_character(person_dir, report, args.rescale, staged)
        if density:
            people[person_dir.name] = density
    for note in report.notes:
        print(f"note: {note}")
    if report.problems:
        for problem in report.problems:
            print(f"FAIL {problem}", file=sys.stderr)
        print(f"{len(report.problems)} problem(s); nothing written.", file=sys.stderr)
        return 1
    by_density = {d: sorted(p for p, v in people.items() if v == d) for d in ANCHOR}
    print(
        f"ok: {len(staged)} files; characters "
        + " ".join(f"{d}x {v}" for d, v in by_density.items())
    )
    if args.check:
        return 0
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    for relative, (width, height, rows) in staged.items():
        target = out / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        source = kit / relative
        if args.rescale:
            write_png(target, width, height, rows)
        else:
            shutil.copyfile(source, target)
    if (kit / MAP_LAYOUT).exists() and (kit / "office-map.png").exists():
        shutil.copyfile(kit / MAP_LAYOUT, out / MAP_LAYOUT)
    (out / "asset-manifest.json").write_text(
        json.dumps(manifest_for(kit, people), indent=2, ensure_ascii=False) + "\n"
    )
    for stale in ("preview.png", "README.md"):
        (out / stale).unlink(missing_ok=True)
    print(f"installed into {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
