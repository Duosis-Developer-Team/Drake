/**
 * The office's art: the drawings in `public/office/` when they exist (see
 * docs/runbooks/DEVELOPMENT_OFFICE_ASSETS.md), else placeholders drawn here
 * at exactly the same native sizes and frame layouts, so a real drawing
 * replaces a placeholder file by file with nothing else changing.
 *
 * `public/office/manifest.json` (written by scripts/office-assets.py) lists
 * the files present; without it everything is a placeholder, and no file is
 * asked for that is not there.
 */

import { BACKDOOR_X, DOOR_WIDTH, PLAQUE, SIDE, SIZE, WALL } from "./world";

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
  density: 1 | 2;
  /** Where the feet are in a frame, in sprite pixels. */
  anchor: { x: number; y: number };
  baked: boolean;
}

export interface Art {
  productOpen: Sprite;
  productBackdoor: Sprite;
  cluster: Sprite;
  lobby: Sprite;
  corridor: Sprite;
  desk: Sprite;
  box: Sprite;
  /** The shared placeholder body, for anyone without a character of their own. */
  body: BodySet;
  /** People's own characters, by person key. */
  bodies: Map<string, BodySet>;
  /** Per person: [front, facing right, back]; only drawn on the placeholder body. */
  heads: Map<string, Sprite[]>;
  /** Racks to light up when a delivery lands, in cluster-room coordinates. */
  racks: { x: number; y: number; w: number; h: number }[];
  backdoorX: number;
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
  jacket: "#26314f",
  jacket2: "#35436b",
  jacket3: "#1c2440",
  shirt: "#141927",
  trouser: "#1b1f2d",
  shoe: "#0c0e15",
  shoe2: "#2b3040",
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

function hash(n: number): number {
  let x = n | 0;
  x = Math.imul(x ^ (x >>> 16), 0x45d9f3b);
  x = Math.imul(x ^ (x >>> 16), 0x45d9f3b);
  return ((x ^ (x >>> 16)) >>> 0) / 4294967296;
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

function plant(ctx: CanvasRenderingContext2D, x: number, y: number, seed: number) {
  // A pot with a leafy top; (x, y) is the pot's bottom-left.
  r(ctx, x + 1, y - 9, 12, 9, C.pot);
  r(ctx, x, y - 11, 14, 3, C.pot2);
  r(ctx, x + 1, y - 3, 12, 2, C.wood0);
  const leaves = [
    [3, -24, 8, 6],
    [0, -19, 7, 6],
    [7, -20, 8, 7],
    [4, -16, 7, 5],
    [-2, -14, 6, 4],
    [9, -14, 6, 4],
  ];
  leaves.forEach(([lx, ly, lw, lh], i) => {
    r(ctx, x + lx, y + ly, lw, lh, i % 2 ? C.leaf : C.leaf2);
    r(ctx, x + lx + 1, y + ly, lw - 3, 1, C.leaf3);
    if (hash(seed + i) > 0.5) r(ctx, x + lx + lw - 2, y + ly + lh - 2, 2, 2, C.leaf0);
  });
}

function woodFloor(ctx: CanvasRenderingContext2D, x0: number, y0: number, w: number, h: number, light = false) {
  const tones = light ? [C.wood4, C.wood3, C.wood5] : [C.wood2, C.wood3, C.wood2];
  for (let row = 0; row * 8 < h; row += 1) {
    const y = y0 + row * 8;
    r(ctx, x0, y, w, 8, tones[row % 3]);
    r(ctx, x0, y + 7, w, 1, light ? C.wood2 : C.wood1);
    const offset = (row * 29) % 48;
    for (let jx = x0 - offset; jx < x0 + w; jx += 48) if (jx > x0) r(ctx, jx, y, 1, 7, light ? C.wood2 : C.wood1);
    for (let i = 0; i < w / 10; i += 1) {
      const gx = x0 + Math.floor(hash(row * 131 + i) * w);
      r(ctx, gx, y + 2 + Math.floor(hash(row * 17 + i) * 4), 3, 1, light ? C.wood5 : C.wood4);
    }
  }
}

function backWall(ctx: CanvasRenderingContext2D, w: number, tone = C.navy3) {
  r(ctx, 0, 0, w, 8, C.navy4);
  r(ctx, 0, 0, w, 1, C.navy5);
  r(ctx, 0, 8, w, WALL - 8, tone);
  for (let x = 16; x < w; x += 32) r(ctx, x, 10, 1, WALL - 16, C.navy2);
  r(ctx, 0, 8, w, 1, C.navy2);
  r(ctx, 0, WALL - 5, w, 5, C.navy1);
  r(ctx, 0, WALL - 5, w, 1, C.navy4);
}

function sideWalls(ctx: CanvasRenderingContext2D, w: number, h: number) {
  for (const x of [0, w - SIDE]) {
    r(ctx, x, 0, SIDE, h, C.navy4);
    r(ctx, x === 0 ? SIDE - 1 : x, WALL, 1, h - WALL, C.navy1);
    r(ctx, x === 0 ? 0 : w - 1, 0, 1, h, C.navy5);
  }
}

function frontEdge(ctx: CanvasRenderingContext2D, w: number, h: number) {
  // The cut-away front wall: a low edge, open at the door.
  const door = Math.round((w - DOOR_WIDTH) / 2);
  r(ctx, 0, h - 4, door, 4, C.navy4);
  r(ctx, door + DOOR_WIDTH, h - 4, w - door - DOOR_WIDTH, 4, C.navy4);
  r(ctx, 0, h - 4, door, 1, C.navy5);
  r(ctx, door + DOOR_WIDTH, h - 4, w - door - DOOR_WIDTH, 1, C.navy5);
  // A doormat at the door.
  r(ctx, door + 6, h - 12, DOOR_WIDTH - 12, 8, C.navy2);
  for (let i = 0; i < DOOR_WIDTH - 16; i += 4) r(ctx, door + 8 + i, h - 10, 2, 4, C.navy4);
}

function plaque(ctx: CanvasRenderingContext2D, p: { x: number; y: number; w: number; h: number }) {
  r(ctx, p.x - 2, p.y - 2, p.w + 4, p.h + 4, C.gold0);
  r(ctx, p.x - 1, p.y - 1, p.w + 2, p.h + 2, C.gold);
  r(ctx, p.x - 1, p.y - 1, p.w + 2, 1, C.gold2);
  r(ctx, p.x, p.y, p.w, p.h, C.navy0);
}

function windowAt(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number) {
  r(ctx, x - 2, y - 2, w + 4, h + 4, C.ink);
  for (let i = 0; i < h; i += 1) r(ctx, x, y + i, w, 1, i < h / 2 ? C.sky : C.sky2);
  r(ctx, x + Math.floor(w / 2) - 1, y, 2, h, C.navy2);
  r(ctx, x, y + Math.floor(h / 2) - 1, w, 2, C.navy2);
  r(ctx, x + 3, y + 3, 6, 1, C.white);
  r(ctx, x + 3, y + 4, 3, 1, C.white);
  r(ctx, x - 3, y + h + 2, w + 6, 2, C.wood4);
}

/* ---------------------------------------------------------------- rooms */

function drawProduct(backdoor: boolean, backdoorX: number): HTMLCanvasElement {
  const { w, h } = SIZE.product;
  const [c, ctx] = canvas(w, h);
  r(ctx, 0, 0, w, h, C.navy1);
  woodFloor(ctx, SIDE, WALL, w - 2 * SIDE, h - WALL);
  // Light from the window across the floor.
  ctx.fillStyle = "rgba(255,240,210,0.07)";
  ctx.beginPath();
  ctx.moveTo(28, WALL);
  ctx.lineTo(92, WALL);
  ctx.lineTo(132, WALL + 70);
  ctx.lineTo(60, WALL + 70);
  ctx.fill();
  backWall(ctx, w);
  windowAt(ctx, 26, 16, 60, 26);
  plaque(ctx, PLAQUE.product);
  // A whiteboard with the week on it.
  const bx = w - 98;
  r(ctx, bx - 1, 13, 66, 30, C.grey2);
  r(ctx, bx, 14, 64, 28, C.white);
  r(ctx, bx + 4, 18, 22, 1, C.cobalt);
  r(ctx, bx + 4, 21, 30, 1, C.cobalt2);
  r(ctx, bx + 4, 24, 16, 1, C.cobalt2);
  r(ctx, bx + 40, 18, 18, 12, C.amber);
  r(ctx, bx + 42, 31, 14, 8, C.green);
  r(ctx, bx + 8, 30, 20, 6, C.red);
  r(ctx, bx + 2, 42, 60, 2, C.grey);
  if (backdoor) {
    const x = backdoorX - 16;
    r(ctx, x - 3, 0, 38, WALL, C.wood1);
    r(ctx, x, 0, 32, WALL, C.navy0);
    r(ctx, x, WALL - 8, 32, 8, C.wood2);
    r(ctx, x + 2, 0, 28, 6, C.navy2);
    r(ctx, x - 3, 0, 1, WALL, C.wood4);
  }
  sideWalls(ctx, w, h);
  plant(ctx, SIDE + 4, WALL + 22, 3);
  plant(ctx, w - SIDE - 18, WALL + 22, 9);
  // A low filing cabinet by the right wall.
  r(ctx, w - SIDE - 20, WALL + 80, 14, 26, C.grey2);
  r(ctx, w - SIDE - 20, WALL + 80, 14, 2, C.grey);
  r(ctx, w - SIDE - 17, WALL + 88, 8, 1, C.navy1);
  r(ctx, w - SIDE - 17, WALL + 97, 8, 1, C.navy1);
  frontEdge(ctx, w, h);
  return c;
}

function rack(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, seed: number) {
  r(ctx, x, y, w, h, C.ink);
  r(ctx, x + 1, y + 1, w - 2, h - 2, "#1a1f2b");
  r(ctx, x + 1, y + 1, w - 2, 1, C.navy4);
  for (let uy = y + 4; uy < y + h - 4; uy += 6) {
    r(ctx, x + 3, uy, w - 6, 4, "#232a39");
    r(ctx, x + 3, uy, w - 6, 1, "#2e3648");
    const n = hash(seed * 97 + uy);
    r(ctx, x + w - 7, uy + 1, 2, 2, n > 0.66 ? C.amber : n > 0.2 ? C.green : C.cobalt2);
    if (n > 0.5) r(ctx, x + w - 11, uy + 1, 2, 2, C.green);
  }
}

function drawCluster(): { image: HTMLCanvasElement; racks: Art["racks"] } {
  const { w, h } = SIZE.cluster;
  const [c, ctx] = canvas(w, h);
  r(ctx, 0, 0, w, h, C.navy1);
  for (let y = WALL; y < h; y += 16)
    for (let x = SIDE; x < w - SIDE; x += 16) {
      r(ctx, x, y, 16, 16, C.tile);
      r(ctx, x, y, 16, 1, C.tile2);
      r(ctx, x, y, 1, 16, C.tile2);
      r(ctx, x + 15, y, 1, 16, C.tile3);
      if (hash(x * 7 + y) > 0.85) r(ctx, x + 6, y + 6, 4, 4, "#2f3950");
    }
  backWall(ctx, w, C.navy2);
  plaque(ctx, PLAQUE.cluster);
  const racks: Art["racks"] = [];
  // Racks along the back wall either side of the plaque.
  for (const x of [14, 50, 86, w - 122, w - 86, w - 50]) {
    rack(ctx, x, 6, 32, 48, x);
    racks.push({ x, y: 6, w: 32, h: 48 });
  }
  // And along the side walls.
  for (let y = WALL + 10; y < h - 50; y += 44) {
    for (const x of [SIDE + 2, w - SIDE - 26]) {
      rack(ctx, x, y, 24, 40, x + y);
      racks.push({ x, y, w: 24, h: 40 });
    }
  }
  // Cable trays overhead, along the wall.
  r(ctx, SIDE, WALL + 2, w - 2 * SIDE, 3, "#3b4560");
  for (let x = SIDE + 4; x < w - SIDE; x += 12) r(ctx, x, WALL + 2, 2, 3, C.navy1);
  // The loading square.
  const sx = w / 2 - 24;
  const sy = 124;
  for (let i = 0; i < 48; i += 1)
    for (let j = 0; j < 48; j += 1) {
      const edge = i < 4 || j < 4 || i >= 44 || j >= 44;
      if (edge) r(ctx, sx + i, sy + j, 1, 1, ((i + j) >> 2) % 2 ? C.amber : C.ink);
    }
  r(ctx, sx + 4, sy + 4, 40, 40, "#2e3747");
  sideWalls(ctx, w, h);
  ctx.fillStyle = "rgba(90,150,255,0.06)";
  ctx.fillRect(SIDE, WALL, w - 2 * SIDE, h - WALL);
  frontEdge(ctx, w, h);
  return { image: c, racks };
}

function sofa(ctx: CanvasRenderingContext2D, x: number, y: number, facingRight: boolean) {
  // Seen from above, against a side wall, seats toward the room.
  const back = facingRight ? x : x + 50;
  r(ctx, x, y, 64, 72, C.ink);
  r(ctx, x + 1, y + 1, 62, 70, C.sofa3);
  r(ctx, back + 1, y + 1, 13, 70, C.sofa);
  r(ctx, back + 1, y + 1, 13, 2, C.sofa2);
  for (let i = 0; i < 3; i += 1) {
    const cy = y + 4 + i * 22;
    const cx = facingRight ? x + 16 : x + 4;
    r(ctx, cx, cy, 44, 20, C.sofa2);
    r(ctx, cx, cy, 44, 2, "#6475ad");
    r(ctx, cx, cy + 18, 44, 2, C.sofa3);
  }
}

function drawLobby(): HTMLCanvasElement {
  const { w, h } = SIZE.lobby;
  const [c, ctx] = canvas(w, h);
  r(ctx, 0, 0, w, h, C.navy1);
  woodFloor(ctx, SIDE, WALL, w - 2 * SIDE, h - WALL, true);
  // A rug in the middle.
  r(ctx, 112, 78, 224, 96, C.gold0);
  r(ctx, 114, 80, 220, 92, C.rug);
  r(ctx, 120, 86, 208, 80, C.rug2);
  r(ctx, 124, 90, 200, 72, C.rug);
  for (let i = 0; i < 9; i += 1) r(ctx, 134 + i * 22, 124, 6, 6, C.gold);
  backWall(ctx, w, C.navy3);
  // A kitchen counter with the coffee machine.
  r(ctx, 24, 28, 112, 26, C.wood1);
  r(ctx, 24, 28, 112, 3, C.wood5);
  r(ctx, 30, 34, 1, 18, C.wood0);
  r(ctx, 66, 34, 1, 18, C.wood0);
  r(ctx, 102, 34, 1, 18, C.wood0);
  r(ctx, 40, 12, 18, 18, "#2a2b33");
  r(ctx, 40, 12, 18, 2, "#4a4c58");
  r(ctx, 52, 16, 3, 2, C.red);
  r(ctx, 45, 22, 8, 6, C.ink);
  r(ctx, 46, 25, 6, 3, C.white);
  r(ctx, 76, 20, 6, 8, C.white);
  r(ctx, 86, 20, 6, 8, C.cobalt2);
  // A water cooler.
  r(ctx, 150, 14, 14, 38, C.grey);
  r(ctx, 151, 8, 12, 14, C.sky);
  r(ctx, 151, 8, 12, 2, C.sky2);
  r(ctx, 153, 30, 3, 2, C.red);
  r(ctx, 158, 30, 3, 2, C.cobalt);
  // Pictures on the wall.
  for (const [x, colour] of [
    [214, C.cobalt],
    [262, C.gold],
    [310, C.leaf2],
  ] as const) {
    r(ctx, x - 1, 15, 34, 24, C.gold0);
    r(ctx, x, 16, 32, 22, C.navy0);
    r(ctx, x + 4, 24, 24, 10, colour);
    r(ctx, x + 10, 20, 12, 6, C.sky);
  }
  plant(ctx, w - SIDE - 30, WALL - 4, 21);
  plant(ctx, w - SIDE - 54, WALL - 2, 33);
  sideWalls(ctx, w, h);
  sofa(ctx, SIDE, 88, true);
  sofa(ctx, w - SIDE - 64, 88, false);
  // A low table by each sofa.
  r(ctx, 80, 108, 20, 32, C.wood1);
  r(ctx, 80, 108, 20, 2, C.wood5);
  r(ctx, w - 100, 108, 20, 32, C.wood1);
  r(ctx, w - 100, 108, 20, 2, C.wood5);
  plant(ctx, SIDE + 4, h - 10, 5);
  plant(ctx, w - SIDE - 18, h - 10, 11);
  frontEdge(ctx, w, h);
  return c;
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
  // A soft shadow is drawn by the engine; the frame holds the figure only.
  draw(ctx);
  outline(c);
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
    { file?: string; backThreshold?: { x?: number; y?: number } } | undefined
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

/** Sprite pixels per art pixel, from a clip's height: 48 → 1, 96 → 2; anything else is refused. */
export function densityOf(height: number): 1 | 2 | null {
  return height === SIZE.frame.h ? 1 : height === SIZE.frame.h * 2 ? 2 : null;
}

/** Where the feet are in a frame of that density: the manifest's anchor when its frame size matches. */
export function anchorFor(density: 1 | 2, manifest?: KitManifest["characters"]): { x: number; y: number } {
  const size = manifest?.frameSize;
  const anchor = manifest?.anchor;
  if (
    size?.width === SIZE.frame.w * density &&
    size?.height === SIZE.frame.h * density &&
    typeof anchor?.x === "number" &&
    typeof anchor?.y === "number"
  )
    return { x: anchor.x, y: anchor.y };
  return density === 2 ? { x: 32, y: 91 } : { x: 16, y: 45 };
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
  const threshold = env["room-product-backdoor"]?.backThreshold?.x;
  const backdoorX =
    typeof threshold === "number" && threshold > 24 && threshold < SIZE.product.w - 24 ? Math.round(threshold) : BACKDOOR_X;
  const [productOpen, productBackdoor, cluster, lobby, corridor, desk, box] = await Promise.all([
    get("room-product-open"),
    get("room-product-backdoor"),
    get("room-cluster"),
    get("room-lobby"),
    get("floor-corridor"),
    get("desk"),
    get("box"),
  ]);
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
  const drawnCluster = drawCluster();
  return {
    productOpen: productOpen ?? drawProduct(false, backdoorX),
    productBackdoor: productBackdoor ?? drawProduct(true, backdoorX),
    cluster: cluster ?? drawnCluster.image,
    lobby: lobby ?? drawLobby(),
    corridor: corridor ?? drawCorridor(),
    desk: desk ?? drawDesk(),
    box: boxSprite,
    body: { frames: body, density: 1, anchor: { x: 16, y: 46 }, baked: false },
    bodies,
    heads: new Map(),
    // The art's racks: along the back wall either side of the plaque, and the side walls.
    racks: cluster
      ? [
          { x: 46, y: 26, w: 90, h: 56 },
          { x: 250, y: 26, w: 88, h: 56 },
          { x: 0, y: 40, w: 32, h: 146 },
          { x: 352, y: 40, w: 32, h: 146 },
        ]
      : drawnCluster.racks,
    backdoorX,
    real,
  };
}

/* ---------------------------------------------------------------- scenery */

/** The garden beside the lobby (always drawn here; it is not part of the art set). */
export function drawGarden(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, seed: number) {
  r(ctx, x, y, w, h, C.grass);
  for (let i = 0; i < (w * h) / 60; i += 1) {
    const gx = x + Math.floor(hash(seed * 31 + i) * w);
    const gy = y + Math.floor(hash(seed * 57 + i * 3) * h);
    r(ctx, gx, gy, 2, 1, C.grass2);
  }
  // A stepping-stone path and a bench.
  for (let i = 0; i < Math.floor(w / 28); i += 1) {
    const sx = x + 10 + i * 28;
    const sy = y + h / 2 + Math.round(Math.sin(i + seed) * 10);
    r(ctx, sx, sy, 14, 8, C.stone2);
    r(ctx, sx + 1, sy, 12, 6, C.stone);
  }
  r(ctx, x + w / 2 - 24, y + 24, 48, 10, C.wood1);
  r(ctx, x + w / 2 - 24, y + 24, 48, 3, C.wood4);
  r(ctx, x + w / 2 - 22, y + 34, 3, 5, C.wood0);
  r(ctx, x + w / 2 + 19, y + 34, 3, 5, C.wood0);
  for (let i = 0; i < 4; i += 1) {
    const tx = x + 16 + Math.floor(hash(seed + i * 7) * (w - 48));
    const ty = y + 60 + Math.floor(hash(seed + i * 13) * (h - 90));
    tree(ctx, tx, ty);
  }
}

function tree(ctx: CanvasRenderingContext2D, x: number, y: number) {
  r(ctx, x + 10, y + 18, 6, 12, C.wood1);
  r(ctx, x + 4, y + 28, 18, 4, "rgba(0,0,0,0.25)");
  r(ctx, x + 2, y + 2, 22, 18, C.leaf);
  r(ctx, x, y + 6, 26, 10, C.leaf);
  r(ctx, x + 5, y, 16, 6, C.leaf2);
  r(ctx, x + 4, y + 4, 8, 3, C.leaf3);
  r(ctx, x + 14, y + 12, 8, 6, C.leaf0);
}

export const PALETTE = C;
