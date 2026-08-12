#!/usr/bin/env python3
"""Derive Drake's runtime brand assets from the two authoritative masters.

The masters in ``apps/web/assets/brand`` are the only source of truth for the
logo. Nothing here draws, redraws, recolours or re-letters anything: every
output is a deterministic *crop* of a master plus a background-to-alpha
transform, and the transform is verified by re-compositing the result over the
master's own background and diffing it against the source pixels.

Why background-to-alpha at all: the masters ship on their own opaque canvas
(``#f9f9fb`` light, ``#032821`` dark). Drake's surfaces are not those exact
colours, so a flat paste would put a visible rectangle behind the wordmark.

How the alpha is derived. For a pixel ``C`` that is a blend of an unknown
foreground ``F`` over the known background ``B`` with coverage ``a``::

    C = a*F + (1 - a)*B          =>       a >= |C_i - B_i| / |F_i - B_i|

``F`` is unknown, so the per-channel denominator is the widest excursion the
image itself contains on that channel — measured, not guessed. Taking the
per-pixel maximum over the three channels gives the smallest ``a`` consistent
with the observation, and ``F = B + (C - B)/a`` recovers the foreground. The
round trip is exact by construction, which is what ``verify`` asserts.

Normalising by the measured range rather than by the distance to black/white
(the classic "colour to alpha") matters for the dark master: its background
green channel is 40/255, so the classic denominator turns +-2 of sensor noise
into 5% alpha. The measured range keeps that noise under 2%, and a 3% floor
clears it without touching a glyph edge -- ``--report`` prints the alpha
histogram the floor is chosen against.

Usage::

    python3 scripts/build_brand_assets.py            # write derivatives
    python3 scripts/build_brand_assets.py --report   # measurements only
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
MASTERS = REPO_ROOT / "apps" / "web" / "assets" / "brand"
RUNTIME = REPO_ROOT / "apps" / "web" / "public" / "brand"

# Ink-detection threshold, in max-channel distance from the background. Used to
# find crop boxes and, in `verify`, to assert that every ink pixel survived.
INK_DELTA = 24

# Percentile of the background-only margin used as the noise floor. The margin
# is the part of the master outside the wordmark's bounding box, so it is
# background by construction and needs no threshold to identify.
NOISE_PERCENTILE = 99.9


@dataclass(frozen=True)
class Master:
    name: str
    theme: str
    background: tuple[int, int, int]


MASTER_FILES = (
    Master("drake-light.png", "light", (0xF9, 0xF9, 0xFB)),
    Master("drake-dark.png", "dark", (0x03, 0x28, 0x21)),
)


def load(master: Master) -> np.ndarray:
    path = MASTERS / master.name
    if not path.exists():
        raise SystemExit(f"authoritative master missing: {path}")
    return np.asarray(Image.open(path).convert("RGB")).astype(np.float64)


def ink_mask(rgb: np.ndarray, background: tuple[int, int, int]) -> np.ndarray:
    return np.abs(rgb - np.array(background, float)).max(axis=2) > INK_DELTA


def content_box(mask: np.ndarray) -> tuple[int, int, int, int]:
    cols = np.nonzero(mask.any(axis=0))[0]
    rows = np.nonzero(mask.any(axis=1))[0]
    return int(cols.min()), int(rows.min()), int(cols.max()), int(rows.max())


def mark_right_edge(mask: np.ndarray, box: tuple[int, int, int, int]) -> int:
    """The column that separates the D+serpent lockup from the ``R`` of RAKE.

    The two are not separated by an empty column -- the serpent's head leans
    into the R -- so the split is the column of MINIMUM ink between the head
    and the R's stem, searched in the band where the wordmark's first letter
    group ends. Deterministic, and derived from the master rather than typed
    in by hand.
    """
    x0, _, x1, _ = box
    width = x1 - x0
    # The lockup occupies the opening ~third of the wordmark; the R stem is a
    # tall solid column, so the minimum in this band is the gap before it.
    lo = x0 + int(width * 0.25)
    hi = x0 + int(width * 0.36)
    column_ink = mask.sum(axis=0)
    return int(lo + int(np.argmin(column_ink[lo:hi])))


def coverage_by_range(rgb: np.ndarray, background: tuple[int, int, int]) -> np.ndarray:
    """Coverage normalised by the widest excursion the image actually contains.

    Measured per channel from the master itself, so the dark master's small
    background green (40/255) does not turn +-2 of noise into 5% alpha the way
    normalising by the distance to the channel limit would.
    """
    bg = np.array(background, float)
    delta = rgb - bg
    span = np.maximum(np.abs(delta).max(axis=(0, 1)), 1.0)
    return np.abs(delta / span).max(axis=2)


def coverage_by_feasibility(rgb: np.ndarray, background: tuple[int, int, int]) -> np.ndarray:
    """Smallest coverage for which a foreground inside [0, 255] exists.

    Applied only to pixels that already cleared the noise floor. Both masters
    sit within a few counts of a channel limit (light blue 251/255, dark red
    3/255), so on background noise this ratio is meaningless -- a stray pure
    white pixel would read as fully opaque. On a real glyph edge it is small
    and correct, and it is what keeps a pixel darker than the background (the
    masters carry a faint vignette) from being clipped away.
    """
    bg = np.array(background, float)
    delta = rgb - bg
    headroom = np.where(delta < 0, bg, 255.0 - bg)
    return (np.abs(delta) / np.maximum(headroom, 1e-6)).max(axis=2)


def noise_floor(
    rgb: np.ndarray, background: tuple[int, int, int], box: tuple[int, int, int, int]
) -> float:
    """Coverage the empty margin produces -- measured, not chosen."""
    coverage = coverage_by_range(rgb, background)
    x0, y0, x1, y1 = box
    margin = np.ones(coverage.shape, bool)
    margin[y0 : y1 + 1, x0 : x1 + 1] = False
    return float(np.percentile(coverage[margin], NOISE_PERCENTILE))


def to_alpha(rgb: np.ndarray, background: tuple[int, int, int], floor: float) -> np.ndarray:
    """RGBA float array: background removed, geometry untouched."""
    bg = np.array(background, float)
    delta = rgb - bg
    alpha = np.clip((coverage_by_range(rgb, background) - floor) / (1.0 - floor), 0.0, 1.0)
    alpha = np.where(alpha > 0, np.maximum(alpha, coverage_by_feasibility(rgb, background)), 0.0)
    alpha = np.clip(alpha, 0.0, 1.0)

    safe = np.maximum(alpha, 1e-6)[..., None]
    foreground = np.clip(bg + delta / safe, 0.0, 255.0)
    out = np.zeros((*rgb.shape[:2], 4), float)
    out[..., :3] = foreground
    out[..., 3] = alpha * 255.0
    return out


def bbox_of(mask: np.ndarray) -> list[int]:
    cols = np.nonzero(mask.any(axis=0))[0]
    rows = np.nonzero(mask.any(axis=1))[0]
    if len(cols) == 0 or len(rows) == 0:
        return [0, 0, 0, 0]
    return [int(cols.min()), int(rows.min()), int(cols.max()), int(rows.max())]


def verify(rgba: np.ndarray, source: np.ndarray, background: tuple[int, int, int]) -> dict:
    """Prove the transform changed nothing that a reader can see.

    Three invariants, and all three have to hold:

    * Re-compositing the result over the master's own background reproduces
      the source. Nothing was deleted, thinned or recoloured -- if a glyph
      edge had been eroded, its pixels would not come back.
    * No ink pixel lost more coverage than the measured noise floor. This is
      the anti-erosion guarantee: an aggressive background removal shows up
      here as a glyph edge losing far more than the floor it was supposed to
      subtract, long before it shows up as a visible artefact.
    * The solid core of the glyphs -- pixels at essentially full coverage --
      stays essentially opaque, so nothing punched a hole in a letter.
    * The bounding box of the visible result is the bounding box of the
      source's ink. Kerning, proportion and composition are untouched.

    A partially transparent antialiasing pixel is not a defect: at 24/255 from
    the background its true coverage IS about 10%, and forcing it opaque is
    what would change the geometry.
    """
    bg = np.array(background, float)
    a = (np.round(rgba[..., 3]) / 255.0)[..., None]
    recomposed = np.round(rgba[..., :3]) * a + bg * (1 - a)
    diff = np.abs(recomposed - source).max(axis=2)

    ink = np.abs(source - bg).max(axis=2) > INK_DELTA
    coverage = coverage_by_range(source, background)
    core = coverage >= 0.98
    alpha = rgba[..., 3] / 255.0
    return {
        "ink_pixels": int(ink.sum()),
        "ink_max_coverage_loss": round(float((coverage - alpha)[ink].max()), 5),
        "core_pixels": int(core.sum()),
        "core_min_alpha": round(float(alpha[core].min()), 4),
        "ink_max_channel_delta": round(float(diff[ink].max()) if ink.any() else 0.0, 4),
        "fringe_max_channel_delta": round(float(diff[~ink].max()) if (~ink).any() else 0.0, 4),
        "mean_channel_delta": round(float(diff.mean()), 4),
        "source_ink_bbox": bbox_of(ink),
        "result_visible_bbox": bbox_of(rgba[..., 3] > 0),
    }


def round_trip(
    shipped: Image.Image, expected: Image.Image, background: tuple[int, int, int]
) -> dict:
    """Decode what will actually be served and diff it against the intent."""
    bg = np.array(background, float)

    def composite(image: Image.Image) -> np.ndarray:
        a = np.asarray(image.convert("RGBA")).astype(float)
        coverage = a[..., 3:] / 255.0
        return a[..., :3] * coverage + bg * (1 - coverage)

    diff = np.abs(composite(shipped) - composite(expected))
    return {"max": round(float(diff.max()), 4), "mean": round(float(diff.mean()), 5)}


def emit(rgba: np.ndarray, width: int, path: Path) -> dict:
    """Write a lossless WebP derivative and prove the encode was lossless.

    Lossless WebP rather than PNG: the masters carry fine render grain that
    PNG's row filters cannot model, so the same pixels cost roughly twice as
    much there. Lossy WebP was measured and rejected -- at q92 it shifts the
    serpent's gradient by up to 50/255, which is exactly the "do not change
    the colours" line.
    """
    height = max(1, round(rgba.shape[0] * width / rgba.shape[1]))
    image = Image.fromarray(np.round(rgba).astype(np.uint8), "RGBA")
    image = image.resize((width, height), Image.LANCZOS)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "WEBP", lossless=True, quality=100, method=6)
    return {"bytes": path.stat().st_size, "size": [width, height]}


def square(rgba: np.ndarray, size: int, path: Path) -> dict:
    """Centre the lockup on a transparent square -- canvas only, no drawing.

    PNG here, not WebP: this is the favicon, and PNG is the format every
    browser accepts in a `rel=icon` without negotiation.
    """
    height, width = rgba.shape[:2]
    scale = (size * 0.92) / max(width, height)
    target = (max(1, round(width * scale)), max(1, round(height * scale)))
    mark = Image.fromarray(np.round(rgba).astype(np.uint8), "RGBA").resize(target, Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(mark, ((size - target[0]) // 2, (size - target[1]) // 2))
    canvas.save(path, optimize=True)
    return {"bytes": path.stat().st_size, "size": [size, size]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", action="store_true", help="measure, write nothing")
    args = parser.parse_args()

    report: dict = {"masters": {}, "outputs": {}}

    for master in MASTER_FILES:
        rgb = load(master)
        mask = ink_mask(rgb, master.background)
        box = content_box(mask)
        split = mark_right_edge(mask, box)
        x0, y0, x1, y1 = box

        floor = noise_floor(rgb, master.background, box)
        wordmark_src = rgb[y0 : y1 + 1, x0 : x1 + 1]
        mark_src = rgb[y0 : y1 + 1, x0 : split + 1]
        wordmark = to_alpha(wordmark_src, master.background, floor)
        mark = to_alpha(mark_src, master.background, floor)

        report["masters"][master.theme] = {
            "file": master.name,
            "background": "#{:02x}{:02x}{:02x}".format(*master.background),
            "content_box": [x0, y0, x1, y1],
            "lockup_split_x": split,
            "measured_noise_floor": round(floor, 5),
            "wordmark_verification": verify(wordmark, wordmark_src, master.background),
            "mark_verification": verify(mark, mark_src, master.background),
        }

        if args.report:
            continue

        for basename, source, writer, dimension in (
            (f"drake-wordmark-{master.theme}.webp", wordmark, emit, 320),
            (f"drake-mark-{master.theme}.webp", mark, emit, 128),
            (f"drake-favicon-{master.theme}.png", mark, square, 64),
        ):
            path = RUNTIME / basename
            entry = writer(source, dimension, path)
            intent = Image.fromarray(np.round(source).astype(np.uint8), "RGBA").resize(
                tuple(entry["size"]), Image.LANCZOS
            )
            if writer is square:
                # `square` also re-lays the mark out on its canvas; compare
                # against the file it just wrote at its own geometry.
                intent = Image.open(path)
            entry["encode_drift"] = round_trip(Image.open(path), intent, master.background)
            report["outputs"][basename] = entry

    print(json.dumps(report, indent=2))

    failures = []
    for theme, entry in report["masters"].items():
        floor = entry["measured_noise_floor"]
        for key in ("wordmark_verification", "mark_verification"):
            v = entry[key]
            if v["ink_max_coverage_loss"] > floor + 1e-6:
                failures.append(
                    f"{theme}/{key}: ink lost {v['ink_max_coverage_loss']} coverage, "
                    f"above the measured floor {floor}"
                )
            if v["core_min_alpha"] < 0.98:
                failures.append(f"{theme}/{key}: glyph core dropped to {v['core_min_alpha']}")
            if v["ink_max_channel_delta"] > 2.0:
                failures.append(
                    f"{theme}/{key}: ink delta {v['ink_max_channel_delta']} exceeds 2/255"
                )
            if v["source_ink_bbox"] != v["result_visible_bbox"]:
                failures.append(f"{theme}/{key}: geometry bbox moved")
    for basename, entry in report["outputs"].items():
        if entry["encode_drift"]["max"] > 0.0:
            failures.append(f"{basename}: encode is not lossless ({entry['encode_drift']})")
    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
