/**
 * The office's art: the drawings in `public/office/` when they exist (see
 * docs/runbooks/DEVELOPMENT_OFFICE_ASSETS.md), else placeholders at exactly
 * the same sizes and frame layouts, so a real drawing replaces a placeholder
 * file by file with nothing else changing.
 *
 * `public/office/asset-manifest.json` lists the files present; nothing is
 * asked for that it does not name.
 */

import { parseLayout, type OfficeLayout } from "./layout";
import { SIZE } from "./world";

export type Sprite = HTMLCanvasElement | HTMLImageElement;
export type BodyRow =
  | "walk-down"
  | "walk-left"
  | "walk-up"
  | "idle-down"
  | "carry-down"
  | "carry-left"
  | "carry-up"
  | "sit-type"
  | "coffee";
export const BODY_ROWS: Record<BodyRow, number> = {
  "walk-down": 4,
  "walk-left": 4,
  "walk-up": 4,
  "idle-down": 2,
  "carry-down": 4,
  "carry-left": 4,
  "carry-up": 4,
  "sit-type": 2,
  coffee: 2,
};

export interface Frame {
  image: Sprite;
  /** Where the body's own (featureless) head is: its top row and centre column (placeholder only). */
  headTop: number;
  headX: number;
}

/**
 * A character: nine clips of frames. A person's own set has their head
 * drawn in ("baked"); the shared placeholder takes a head on top. `density`
 * is sprite pixels per art pixel: 1 for 32 × 48 frames, 2 for 64 × 96.
 */
export interface BodySet {
  frames: Record<BodyRow, Frame[]>;
  density: Density;
  /** Where the feet are in a frame, in sprite pixels. */
  anchor: { x: number; y: number };
  baked: boolean;
}

export interface Art {
  /** The whole office drawn as one map (`office-map.png`), when it exists. */
  map: Sprite | null;
  /** The map's own geometry (`office-map.json`), when it comes with one. */
  layout: OfficeLayout | null;
  /** The room drawings of the first kit, used to compose a map until the map exists. */
  rooms: { product: Sprite | null; backdoor: Sprite | null; cluster: Sprite | null; lobby: Sprite | null };
  corridor: Sprite;
  /** The chair, drawn behind a seated person. */
  chair: Sprite;
  /** The desk's front, drawn in front of them. */
  deskFront: Sprite;
  box: Sprite;
  /** The shared placeholder body, for anyone without a character of their own. */
  body: BodySet;
  /** People's own characters, by person key. */
  bodies: Map<string, BodySet>;
  /** Per person: [front, facing right, back]; only drawn on the placeholder body. */
  heads: Map<string, Sprite[]>;
  /** Which files are the user's drawings rather than placeholders. */
  real: Set<string>;
}

/* ---------------------------------------------------------------- palette */

const C = {
  ink: "#121624",
  navy0: "#171c2b",
  navy1: "#1f2638",
  navy2: "#283149",
  navy3: "#323d5b",
  navy4: "#3f4c72",
  navy5: "#53628f",
  wood0: "#4e321f",
  wood1: "#6b4529",
  wood2: "#805434",
  wood3: "#95643f",
  wood4: "#ad7a4f",
  wood5: "#c69264",
  cobalt: "#3567d6",
  cobalt2: "#5d8ff0",
  sky: "#94c2ff",
  sky2: "#c9e1ff",
  gold0: "#8a6420",
  gold: "#d1a345",
  gold2: "#f2cf7a",
  leaf0: "#24502f",
  leaf: "#357544",
  leaf2: "#55a35f",
  leaf3: "#7cc77c",
  pot: "#a75b34",
  pot2: "#c7774a",
  white: "#e8edf6",
  grey: "#98a2b8",
  grey2: "#5f6880",
  red: "#e2574c",
  amber: "#f2b441",
  green: "#47d27b",
  skin: "#e3ad86",
  skin2: "#c98f69",
  // The placeholder body: lighter than the floors, so a newcomer reads on them.
  jacket: "#5b70a8",
  jacket2: "#7a8fc6",
  jacket3: "#47598b",
  shirt: "#e3e8f4",
  trouser: "#3c4560",
  shoe: "#20242f",
  shoe2: "#4a5168",
  card: "#c28b55",
  card2: "#9b6a3c",
  tape: "#ead09a",
  rug: "#6e2f3d",
  rug2: "#8c3d4d",
  sofa: "#3a4a7c",
  sofa2: "#4c5e98",
  sofa3: "#2b375e",
  tile: "#2a3242",
  tile2: "#353f53",
  tile3: "#202735",
  grass: "#2d5636",
  grass2: "#376641",
  stone: "#7c8497",
  stone2: "#5c6476",
};

/* ---------------------------------------------------------------- helpers */

function canvas(w: number, h: number): [HTMLCanvasElement, CanvasRenderingContext2D] {
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  const ctx = c.getContext("2d");
  if (!ctx) throw new Error("no 2d context");
  ctx.imageSmoothingEnabled = false;
  return [c, ctx];
}

function r(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, colour: string) {
  ctx.fillStyle = colour;
  ctx.fillRect(Math.round(x), Math.round(y), Math.round(w), Math.round(h));
}

/** A 1-pixel dark outline around everything opaque (outside only). */
function outline(c: HTMLCanvasElement, colour = C.ink) {
  const ctx = c.getContext("2d");
  if (!ctx) return;
  const { width: w, height: h } = c;
  const src = ctx.getImageData(0, 0, w, h);
  const out = ctx.createImageData(w, h);
  out.data.set(src.data);
  const [cr, cg, cb] = [1, 3, 5].map((i) => parseInt(colour.slice(i, i + 2), 16));
  const opaque = (x: number, y: number) => x >= 0 && y >= 0 && x < w && y < h && src.data[(y * w + x) * 4 + 3] > 40;
  for (let y = 0; y < h; y += 1)
    for (let x = 0; x < w; x += 1) {
      if (opaque(x, y)) continue;
      if (opaque(x - 1, y) || opaque(x + 1, y) || opaque(x, y - 1) || opaque(x, y + 1)) {
        const i = (y * w + x) * 4;
        out.data[i] = cr;
        out.data[i + 1] = cg;
        out.data[i + 2] = cb;
        out.data[i + 3] = 255;
      }
    }
  ctx.putImageData(out, 0, 0);
}

function drawCorridor(): HTMLCanvasElement {
  const [c, ctx] = canvas(SIZE.tile, SIZE.tile);
  r(ctx, 0, 0, 32, 32, "#1d2333");
  for (const [x, y] of [
    [0, 0],
    [16, 0],
    [0, 16],
    [16, 16],
  ]) {
    r(ctx, x, y, 16, 16, (x + y) % 32 ? "#20273a" : "#1e2436");
    r(ctx, x, y, 16, 1, "#262e44");
    r(ctx, x, y, 1, 16, "#262e44");
  }
  r(ctx, 9, 21, 2, 1, "#2b344d");
  r(ctx, 25, 6, 1, 1, "#2b344d");
  return c;
}

function drawDesk(): HTMLCanvasElement {
  const { w, h } = SIZE.desk;
  const [c, ctx] = canvas(w, h);
  // The chair, behind the desk.
  r(ctx, 22, 2, 20, 16, "#2a2f3d");
  r(ctx, 24, 3, 16, 2, "#414860");
  r(ctx, 24, 6, 16, 10, "#343a4d");
  // The monitor's back, on the left, glowing faintly.
  r(ctx, 3, 3, 24, 19, "rgba(120,170,255,0.25)");
  r(ctx, 4, 4, 22, 17, "#1a1e2b");
  r(ctx, 5, 5, 20, 1, "#2c3347");
  r(ctx, 13, 21, 4, 3, "#2a2f3d");
  // The top and the front of the desk.
  r(ctx, 0, 22, 64, 13, C.wood4);
  r(ctx, 0, 22, 64, 2, C.wood5);
  r(ctx, 0, 34, 64, 12, C.wood1);
  r(ctx, 0, 34, 64, 1, C.wood0);
  r(ctx, 40, 37, 18, 7, C.wood2);
  r(ctx, 47, 40, 4, 1, C.gold);
  r(ctx, 1, 46, 3, 2, C.wood0);
  r(ctx, 60, 46, 3, 2, C.wood0);
  // A mug and a notebook.
  r(ctx, 49, 24, 5, 6, C.white);
  r(ctx, 54, 26, 2, 2, C.white);
  r(ctx, 30, 25, 12, 7, C.cobalt);
  r(ctx, 31, 26, 10, 1, C.cobalt2);
  outline(c);
  return c;
}

function drawBox(): HTMLCanvasElement {
  const [c, ctx] = canvas(SIZE.box, SIZE.box);
  r(ctx, 1, 3, 14, 12, C.card);
  r(ctx, 1, 3, 14, 3, "#d6a06a");
  r(ctx, 1, 12, 14, 3, C.card2);
  r(ctx, 7, 3, 2, 12, C.tape);
  r(ctx, 1, 6, 14, 1, C.card2);
  outline(c);
  return c;
}

/* ---------------------------------------------------------------- the body */

type Dir = "down" | "left" | "up";

function torso(ctx: CanvasRenderingContext2D, dir: Dir, bob: number) {
  const y = 24 - bob;
  if (dir === "left") {
    r(ctx, 11, y, 11, 14, C.jacket);
    r(ctx, 11, y, 2, 14, C.jacket2);
    r(ctx, 19, y + 1, 3, 12, C.jacket3);
    r(ctx, 11, y, 4, 3, C.shirt);
    return;
  }
  r(ctx, 8, y + 1, 16, 13, C.jacket);
  r(ctx, 9, y, 14, 2, C.jacket);
  r(ctx, 8, y + 1, 2, 12, C.jacket2);
  r(ctx, 22, y + 1, 2, 12, C.jacket3);
  if (dir === "down") {
    r(ctx, 14, y, 4, 7, C.shirt);
    r(ctx, 13, y + 1, 1, 6, C.jacket2);
    r(ctx, 18, y + 1, 1, 6, C.jacket2);
    r(ctx, 15, y + 9, 1, 1, C.grey2);
    r(ctx, 15, y + 12, 1, 1, C.grey2);
  } else {
    r(ctx, 12, y, 8, 2, C.jacket3);
    r(ctx, 15, y + 2, 1, 11, C.jacket3);
  }
}

function headShape(ctx: CanvasRenderingContext2D, cx: number, bob: number) {
  // The plain head the person's own covers.
  const y = 3 - bob;
  r(ctx, cx - 7, y + 2, 14, 16, C.skin);
  r(ctx, cx - 6, y, 12, 20, C.skin);
  r(ctx, cx - 5, y + 18, 10, 3, C.skin2);
  r(ctx, cx - 2, y + 20, 4, 3, C.skin2);
}

function legs(ctx: CanvasRenderingContext2D, dir: Dir, step: number) {
  // step: -1, 0, 1 — which leg is forward.
  if (dir === "left") {
    const a = 12 + step * 3;
    const b = 16 - step * 3;
    r(ctx, a, 38, 5, 7, C.trouser);
    r(ctx, b, 38, 5, 7, C.trouser);
    r(ctx, a - 2, 45, 7, 2, C.shoe);
    r(ctx, b - 2, 45, 7, 2, C.shoe);
    r(ctx, a - 2, 45, 3, 1, C.shoe2);
    return;
  }
  const lift = (leg: number) => (step === leg ? 1 : 0);
  r(ctx, 10, 38, 5, 7 - lift(-1), C.trouser);
  r(ctx, 17, 38, 5, 7 - lift(1), C.trouser);
  r(ctx, 10, 45 - lift(-1), 5, 2, C.shoe);
  r(ctx, 17, 45 - lift(1), 5, 2, C.shoe);
  if (dir === "down") {
    r(ctx, 10, 45 - lift(-1), 2, 1, C.shoe2);
    r(ctx, 17, 45 - lift(1), 2, 1, C.shoe2);
  }
}

function arms(ctx: CanvasRenderingContext2D, dir: Dir, swing: number, bob: number) {
  const y = 25 - bob;
  if (dir === "left") {
    r(ctx, 14 - swing, y + 1, 4, 10, C.jacket2);
    r(ctx, 14 - swing, y + 11, 4, 2, C.skin);
    return;
  }
  r(ctx, 5, y + 1 + swing, 3, 10, C.jacket);
  r(ctx, 24, y + 1 - swing, 3, 10, C.jacket3);
  if (dir === "down") {
    r(ctx, 5, y + 11 + swing, 3, 2, C.skin);
    r(ctx, 24, y + 11 - swing, 3, 2, C.skin);
  }
}

function bodyFrame(draw: (ctx: CanvasRenderingContext2D) => void): HTMLCanvasElement {
  const [c, ctx] = canvas(SIZE.frame.w, SIZE.frame.h);
  // A soft shadow is drawn by the engine; the frame holds the figure only,
  // ringed in a soft light line so it stands out on a dark floor.
  draw(ctx);
  outline(c, "#b9c4e4");
  return c;
}

const STEPS = [0, 1, 0, -1];
const BOB = [0, 1, 0, 1];

function walkFrames(dir: Dir, carry: boolean, box: HTMLCanvasElement): HTMLCanvasElement[] {
  return STEPS.map((step, i) =>
    bodyFrame((ctx) => {
      const bob = BOB[i];
      legs(ctx, dir, step);
      if (dir === "up") headShape(ctx, 16, bob);
      torso(ctx, dir, bob);
      if (carry) {
        if (dir === "down") {
          r(ctx, 6, 28 - bob, 4, 6, C.jacket);
          r(ctx, 22, 28 - bob, 4, 6, C.jacket3);
          ctx.drawImage(box, 8, 27 - bob);
        } else if (dir === "left") {
          ctx.drawImage(box, 2, 27 - bob);
          r(ctx, 10, 29 - bob, 5, 4, C.jacket2);
        } else {
          r(ctx, 5, 29 - bob, 3, 7, C.jacket);
          r(ctx, 24, 29 - bob, 3, 7, C.jacket3);
          r(ctx, 3, 33 - bob, 3, 3, C.card);
          r(ctx, 26, 33 - bob, 3, 3, C.card);
        }
      } else arms(ctx, dir, dir === "left" ? step * 2 : step, bob);
      if (dir !== "up") headShape(ctx, dir === "left" ? 15 : 16, bob);
    }),
  );
}

function placeholderBody(box: HTMLCanvasElement): Record<BodyRow, HTMLCanvasElement[]> {
  return {
    "walk-down": walkFrames("down", false, box),
    "walk-left": walkFrames("left", false, box),
    "walk-up": walkFrames("up", false, box),
    "idle-down": [0, 1].map((bob) =>
      bodyFrame((ctx) => {
        legs(ctx, "down", 0);
        torso(ctx, "down", bob);
        arms(ctx, "down", 0, bob);
        headShape(ctx, 16, bob);
      }),
    ),
    "carry-down": walkFrames("down", true, box),
    "carry-left": walkFrames("left", true, box),
    "carry-up": walkFrames("up", true, box),
    "sit-type": [0, 1].map((f) =>
      bodyFrame((ctx) => {
        torso(ctx, "down", 0);
        // Forearms toward the keyboard, the hands taking turns.
        r(ctx, 6, 27, 3, 8, C.jacket);
        r(ctx, 23, 27, 3, 8, C.jacket3);
        r(ctx, 8, 34, 6, 3, C.jacket);
        r(ctx, 18, 34, 6, 3, C.jacket3);
        r(ctx, 12, 36 - f, 3, 2, C.skin);
        r(ctx, 17, 35 + f, 3, 2, C.skin);
        headShape(ctx, 16, 0);
      }),
    ),
    coffee: [0, 1].map((f) =>
      bodyFrame((ctx) => {
        legs(ctx, "down", 0);
        torso(ctx, "down", 0);
        r(ctx, 5, 26, 3, 10, C.jacket);
        r(ctx, 5, 36, 3, 2, C.skin);
        // The other hand brings the mug up.
        r(ctx, 22, 27 - f * 2, 4, 6, C.jacket3);
        r(ctx, 20, 25 - f * 2, 3, 3, C.skin);
        r(ctx, 19, 27 - f * 3, 5, 5, C.white);
        r(ctx, 24, 28 - f * 3, 1, 2, C.white);
        headShape(ctx, 16, 0);
      }),
    ),
  };
}

/** Where a body frame's own head is: its top row and centre, from its pixels. */
function headOf(image: Sprite): { headTop: number; headX: number } {
  try {
    const [, ctx] = canvas(SIZE.frame.w, SIZE.frame.h);
    ctx.drawImage(image, 0, 0);
    const data = ctx.getImageData(0, 0, SIZE.frame.w, SIZE.frame.h).data;
    for (let y = 0; y < SIZE.frame.h; y += 1) {
      let sum = 0;
      let n = 0;
      for (let x = 4; x < SIZE.frame.w - 4; x += 1)
        if (data[(y * SIZE.frame.w + x) * 4 + 3] > 40) {
          sum += x;
          n += 1;
        }
      if (n >= 3) {
        return { headTop: y, headX: Math.round(sum / n) };
      }
    }
  } catch {
    // An image that cannot be read keeps the default.
  }
  return { headTop: 2, headX: 16 };
}

/* ---------------------------------------------------------------- heads */

/** A head from a hand-made portrait (a bust): the face, cropped, made small. */
export function headFromPortrait(image: HTMLImageElement): Sprite[] {
  const size = SIZE.head;
  const [front, ctx] = canvas(size, size);
  const w = image.naturalWidth;
  const h = image.naturalHeight;
  const side = Math.min(w, h);
  // Portraits keep the head in the upper middle: the face and the hair.
  const sx = (w - side) / 2 + side * 0.2;
  const sy = (h - side) / 2 + side * 0.03;
  const crop = side * 0.6;
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(image, sx, sy, crop, crop, 3, 2, 26, 26);
  // Keep a rounded head shape and drop what is around it.
  const data = ctx.getImageData(0, 0, size, size);
  for (let y = 0; y < size; y += 1)
    for (let x = 0; x < size; x += 1) {
      const dx = (x + 0.5 - 16) / 13;
      const dy = (y + 0.5 - 15.5) / 13.5;
      const i = (y * size + x) * 4;
      if (dx * dx + dy * dy > 1) data.data[i + 3] = 0;
      else if (data.data[i + 3] > 0) {
        // A few levels per channel: reads as drawn rather than as a photo.
        for (let k = 0; k < 3; k += 1) data.data[i + k] = Math.min(255, Math.round(data.data[i + k] / 24) * 24);
        data.data[i + 3] = data.data[i + 3] > 100 ? 255 : 0;
      }
    }
  ctx.putImageData(data, 0, 0);
  outline(front);
  return [front, front, backOf(front)];
}

/** The back of a head: the hair's colour over the same shape. */
function backOf(front: HTMLCanvasElement): HTMLCanvasElement {
  const size = SIZE.head;
  const [c, ctx] = canvas(size, size);
  const src = front.getContext("2d")?.getImageData(0, 0, size, size).data;
  let rs = 0;
  let gs = 0;
  let bs = 0;
  let n = 0;
  if (src)
    for (let y = 2; y < 10; y += 1)
      for (let x = 6; x < 26; x += 1) {
        const i = (y * size + x) * 4;
        if (src[i + 3] < 200) continue;
        const lum = src[i] + src[i + 1] + src[i + 2];
        if (lum > 360) continue;
        rs += src[i];
        gs += src[i + 1];
        bs += src[i + 2];
        n += 1;
      }
  const hair = n ? `rgb(${Math.round(rs / n)},${Math.round(gs / n)},${Math.round(bs / n)})` : "#2b2420";
  ctx.drawImage(front, 0, 0);
  ctx.globalCompositeOperation = "source-in";
  ctx.fillStyle = hair;
  ctx.fillRect(0, 0, size, size);
  ctx.globalCompositeOperation = "source-over";
  ctx.fillStyle = "rgba(255,255,255,0.12)";
  ctx.fillRect(10, 5, 8, 2);
  ctx.fillStyle = "rgba(0,0,0,0.18)";
  ctx.fillRect(4, 22, 24, 6);
  outline(c);
  return c;
}

/** A head from an arcade sprite (16 × 16): its top rows, doubled. */
export function headFromSprite(image: HTMLImageElement): Sprite[] {
  const [front, ctx] = canvas(SIZE.head, SIZE.head);
  ctx.drawImage(image, 2, 0, 12, 12, 4, 4, 24, 24);
  const data = ctx.getImageData(0, 0, SIZE.head, SIZE.head);
  for (let y = 0; y < SIZE.head; y += 1)
    for (let x = 0; x < SIZE.head; x += 1) {
      const dx = (x + 0.5 - 16) / 12.5;
      const dy = (y + 0.5 - 16) / 12.5;
      if (dx * dx + dy * dy > 1) data.data[(y * SIZE.head + x) * 4 + 3] = 0;
    }
  ctx.putImageData(data, 0, 0);
  outline(front);
  return [front, front, backOf(front)];
}

/** A head when there is no face at all: the person's colour and initials. */
export function headFromInitials(text: string, colour: string): Sprite[] {
  const [front, ctx] = canvas(SIZE.head, SIZE.head);
  ctx.fillStyle = colour;
  ctx.beginPath();
  ctx.arc(16, 16, 12, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "rgba(255,255,255,0.25)";
  ctx.fillRect(10, 6, 8, 2);
  ctx.fillStyle = "#ffffff";
  ctx.font = "bold 10px monospace";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(text.slice(0, 2), 16, 17);
  // Hard edges: drop the half-covered pixels the text and the arc leave.
  const data = ctx.getImageData(0, 0, SIZE.head, SIZE.head);
  for (let i = 3; i < data.data.length; i += 4) data.data[i] = data.data[i] > 110 ? 255 : 0;
  ctx.putImageData(data, 0, 0);
  outline(front);
  // The side and the back carry no letters (a mirrored side would read backwards).
  const [back, bctx] = canvas(SIZE.head, SIZE.head);
  bctx.drawImage(front, 0, 0);
  bctx.globalCompositeOperation = "source-atop";
  bctx.fillStyle = colour;
  bctx.fillRect(4, 4, 24, 24);
  return [front, back, back];
}

/* ---------------------------------------------------------------- reducing */

/**
 * Shrink pixel art by an exact whole factor: each `factor` × `factor` block
 * becomes one pixel, its colour the coverage-weighted average of the block
 * (premultiplied alpha), opaque when the block is at least half covered.
 * No resampling, so nothing shimmers or blurs; the result keeps binary alpha.
 */
export function boxReduce(
  src: Uint8ClampedArray,
  width: number,
  height: number,
  factor: number,
): { data: Uint8ClampedArray<ArrayBuffer>; width: number; height: number } {
  const w = Math.floor(width / factor);
  const h = Math.floor(height / factor);
  const out = new Uint8ClampedArray(new ArrayBuffer(w * h * 4));
  const area = factor * factor;
  for (let y = 0; y < h; y += 1)
    for (let x = 0; x < w; x += 1) {
      let r = 0;
      let g = 0;
      let b = 0;
      let a = 0;
      for (let dy = 0; dy < factor; dy += 1)
        for (let dx = 0; dx < factor; dx += 1) {
          const i = ((y * factor + dy) * width + x * factor + dx) * 4;
          const alpha = src[i + 3];
          r += src[i] * alpha;
          g += src[i + 1] * alpha;
          b += src[i + 2] * alpha;
          a += alpha;
        }
      const o = (y * w + x) * 4;
      if (a * 2 >= 255 * area && a > 0) {
        out[o] = Math.round(r / a);
        out[o + 1] = Math.round(g / a);
        out[o + 2] = Math.round(b / a);
        out[o + 3] = 255;
      }
    }
  return { data: out, width: w, height: h };
}

const shrunk = new WeakMap<object, Map<number, HTMLCanvasElement>>();

/** A frame shrunk by an exact whole factor, made once and kept. */
export function reduced(image: Sprite, factor: number): Sprite {
  let byFactor = shrunk.get(image);
  if (!byFactor) {
    byFactor = new Map();
    shrunk.set(image, byFactor);
  }
  const cached = byFactor.get(factor);
  if (cached) return cached;
  const width = image instanceof HTMLImageElement ? image.naturalWidth : image.width;
  const height = image instanceof HTMLImageElement ? image.naturalHeight : image.height;
  try {
    const [, src] = canvas(width, height);
    src.drawImage(image, 0, 0);
    const small = boxReduce(src.getImageData(0, 0, width, height).data, width, height, factor);
    const [c, ctx] = canvas(small.width, small.height);
    ctx.putImageData(new ImageData(small.data, small.width, small.height), 0, 0);
    byFactor.set(factor, c);
    return c;
  } catch {
    return image;
  }
}

/* ---------------------------------------------------------------- loading */

function load(src: string): Promise<HTMLImageElement | null> {
  return new Promise((resolve) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => resolve(null);
    image.src = src;
  });
}

function slice(image: HTMLImageElement, w: number, h: number, count: number): HTMLCanvasElement[] {
  const out: HTMLCanvasElement[] = [];
  for (let i = 0; i < count; i += 1) {
    const [c, ctx] = canvas(w, h);
    ctx.drawImage(image, i * w, 0, w, h, 0, 0, w, h);
    out.push(c);
  }
  return out;
}

/** The kit's `asset-manifest.json`, the parts read here. */
export interface KitManifest {
  environment?: Record<
    string,
    { file?: string; layout?: string } | undefined
  >;
  characters?: {
    frameSize?: { width?: number; height?: number };
    anchor?: { x?: number; y?: number };
    variants?: Record<string, { sheets?: Record<string, string> } | undefined>;
  };
}

const BASE = "/office/";
const SAFE_PATH = /^[A-Za-z0-9_-]+(\/[A-Za-z0-9_.-]+)*\.png$/;
const SAFE_KEY = /^[A-Za-z0-9._:-]{1,100}$/;

/** A manifest path the page may load: relative, inside the office folder, a PNG. */
export function safePath(path: unknown): string | null {
  return typeof path === "string" && SAFE_PATH.test(path) && !path.includes("..") ? path : null;
}

export type Density = 1 | 2 | 4;
const DEFAULT_ANCHOR: Record<Density, { x: number; y: number }> = {
  1: { x: 16, y: 45 },
  2: { x: 32, y: 91 },
  4: { x: 64, y: 180 },
};

/** Sprite pixels per art pixel, from a clip's height: 48 → 1, 96 → 2, 192 → 4; anything else is refused. */
export function densityOf(height: number): Density | null {
  for (const d of [1, 2, 4] as const) if (height === SIZE.frame.h * d) return d;
  return null;
}

/** Where the feet are in a frame of that density: the manifest's anchor when its frame size matches. */
export function anchorFor(density: Density, manifest?: KitManifest["characters"]): { x: number; y: number } {
  const size = manifest?.frameSize;
  const anchor = manifest?.anchor;
  if (
    size?.width === SIZE.frame.w * density &&
    size?.height === SIZE.frame.h * density &&
    typeof anchor?.x === "number" &&
    typeof anchor?.y === "number"
  )
    return { x: anchor.x, y: anchor.y };
  return DEFAULT_ANCHOR[density];
}

/** A person's own character from their strips; null unless every clip is there and well formed. */
async function character(
  sheets: Record<string, string> | undefined,
  manifest: KitManifest["characters"],
): Promise<BodySet | null> {
  if (!sheets) return null;
  const rows = Object.keys(BODY_ROWS) as BodyRow[];
  const images = await Promise.all(
    rows.map((row) => {
      const path = safePath(sheets[`body-${row}`]);
      return path ? load(`${BASE}${path}`) : Promise.resolve(null);
    }),
  );
  if (images.some((i) => !i)) return null;
  const density = densityOf((images[0] as HTMLImageElement).naturalHeight);
  if (!density) return null;
  const fw = SIZE.frame.w * density;
  const fh = SIZE.frame.h * density;
  const frames = {} as Record<BodyRow, Frame[]>;
  for (let i = 0; i < rows.length; i += 1) {
    const image = images[i] as HTMLImageElement;
    const count = BODY_ROWS[rows[i]];
    if (image.naturalHeight !== fh || image.naturalWidth !== fw * count) return null;
    frames[rows[i]] = slice(image, fw, fh, count).map((f) => ({ image: f, headTop: 0, headX: 0 }));
  }
  return { frames, density, anchor: anchorFor(density, manifest), baked: true };
}

/**
 * A chair and a desk's front from the placeholder desk (64 × 48, chair
 * drawn in): the chair's rows become `chair.png`'s 32 × 32, the rest the
 * desk's 64 × 40 front, so the two sit where the drawn ones would.
 */
function splitDesk(desk: Sprite): { chair: HTMLCanvasElement; deskFront: HTMLCanvasElement } {
  const [chair, c] = canvas(SIZE.chair.w, SIZE.chair.h);
  c.drawImage(desk, 16, 0, 32, 20, 0, 8, 32, 20);
  const [deskFront, d] = canvas(SIZE.desk.w, SIZE.desk.h);
  d.drawImage(desk, 0, 8, 64, 40, 0, 0, 64, 40);
  return { chair, deskFront };
}

const SAFE_JSON = /^[A-Za-z0-9_-]+\.json$/;

/** The office's art: the user's drawings where present, placeholders otherwise. */
export async function loadArt(): Promise<Art> {
  let manifest: KitManifest = {};
  try {
    const response = await fetch(`${BASE}asset-manifest.json`, { cache: "no-cache" });
    if (response.ok) manifest = (await response.json()) as KitManifest;
  } catch {
    // No manifest: everything is a placeholder.
  }
  const real = new Set<string>();
  const env = manifest.environment ?? {};
  const get = async (name: string): Promise<HTMLImageElement | null> => {
    const path = safePath(env[name]?.file);
    if (!path) return null;
    const image = await load(`${BASE}${path}`);
    if (image) real.add(path);
    return image;
  };
  const [map, product, backdoor, cluster, lobby, corridor, desk, chair, deskFront, box] = await Promise.all([
    get("office-map"),
    get("room-product-open"),
    get("room-product-backdoor"),
    get("room-cluster"),
    get("room-lobby"),
    get("floor-corridor"),
    get("desk"),
    get("chair"),
    get("desk-front"),
    get("box"),
  ]);
  // The map's own geometry, when it brings one; else the guide's.
  let layout: OfficeLayout | null = null;
  const layoutFile = env["office-map"]?.layout;
  if (map && typeof layoutFile === "string" && SAFE_JSON.test(layoutFile)) {
    try {
      const response = await fetch(`${BASE}${layoutFile}`, { cache: "no-cache" });
      if (response.ok) layout = parseLayout(await response.json());
    } catch {
      layout = null;
    }
  }
  const split = splitDesk(desk ?? drawDesk());
  const boxSprite = box ?? drawBox();
  const placeholders = placeholderBody(drawBox());
  const body = {} as Record<BodyRow, Frame[]>;
  for (const row of Object.keys(BODY_ROWS) as BodyRow[])
    body[row] = placeholders[row].map((f) => ({ image: f, ...headOf(f) }));
  const bodies = new Map<string, BodySet>();
  await Promise.all(
    Object.entries(manifest.characters?.variants ?? {}).map(async ([key, variant]) => {
      if (!SAFE_KEY.test(key)) return;
      const set = await character(variant?.sheets, manifest.characters);
      if (set) bodies.set(key, set);
    }),
  );
  return {
    map,
    layout: map && layout && layout.w === map.naturalWidth && layout.h === map.naturalHeight ? layout : null,
    rooms: { product, backdoor, cluster, lobby },
    corridor: corridor ?? drawCorridor(),
    chair: chair ?? split.chair,
    deskFront: deskFront ?? split.deskFront,
    box: boxSprite,
    body: { frames: body, density: 1, anchor: { x: 16, y: 46 }, baked: false },
    bodies,
    heads: new Map(),
    real,
  };
}
