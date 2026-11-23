/**
 * Furniture for the office's rooms: a rug under the desks, a corner lounge,
 * storage along a wall, a lamp or a water cooler, plants and bins between
 * desks; a few spare racks in the cluster; boxes and sheeted furniture in a
 * room nobody uses; a rug and plants in the lobby.
 *
 * Pure and deterministic: the same room slot is always furnished the same
 * way. A prop goes where it overlaps no desk, chair, screen, door or piece of
 * the map's own furniture, keeps clear of where people sit and stand, and
 * leaves every seat, standing spot, lobby spot and the loading square
 * reachable from the entrance — every placement is checked by walking the
 * grid, and one that would cut anything off is tried elsewhere or left out.
 */

import type { MapRect } from "./layout";

interface Point {
  x: number;
  y: number;
}

export type PropKind =
  | "rug"
  | "plant-tall"
  | "plant-small"
  | "cabinet"
  | "printer"
  | "sofa-small"
  | "coffee-table"
  | "floor-lamp"
  | "water-cooler"
  | "whiteboard-stand"
  | "bin"
  | "bookshelf"
  | "server-rack"
  | "box";

/**
 * Each prop's size (art pixels, the map's 1× density) and how many of its
 * bottom rows stand on the floor and block walking (0: flat, walked over).
 */
export const PROPS: Record<PropKind, { w: number; h: number; foot: number }> = {
  rug: { w: 96, h: 48, foot: 0 },
  "plant-tall": { w: 16, h: 32, foot: 6 },
  "plant-small": { w: 12, h: 16, foot: 5 },
  cabinet: { w: 24, h: 32, foot: 10 },
  printer: { w: 24, h: 24, foot: 10 },
  "sofa-small": { w: 48, h: 28, foot: 14 },
  "coffee-table": { w: 32, h: 20, foot: 10 },
  "floor-lamp": { w: 12, h: 32, foot: 4 },
  "water-cooler": { w: 12, h: 28, foot: 6 },
  "whiteboard-stand": { w: 32, h: 36, foot: 6 },
  bin: { w: 8, h: 10, foot: 5 },
  bookshelf: { w: 32, h: 40, foot: 12 },
  "server-rack": { w: 24, h: 40, foot: 14 },
  // The delivery box (`box.png`), stacked in an empty room.
  box: { w: 16, h: 16, foot: 10 },
};

/** The kinds drawn from `props/<kind>.png` (the box is the delivery's own). */
export const PROP_FILES = (Object.keys(PROPS) as PropKind[]).filter((k) => k !== "box");

export interface Prop extends MapRect {
  kind: PropKind;
  slot: string;
  /** Under a dust sheet (a room nobody uses). */
  covered: boolean;
  /** Painted with the map, under everyone: rugs, and an empty room's things. */
  still: boolean;
}

export interface Grid {
  cols: number;
  rows: number;
  /** 1 = walkable, per CELL × CELL cell. */
  grid: Uint8Array;
}

export interface FurnishRoom {
  slot: string;
  kind: "product" | "cluster" | "lobby";
  vacant: boolean;
  rect: MapRect;
  /** The walkable floor inside the walls. */
  floor: MapRect;
  desks: { x: number; y: number }[];
  /** Kept clear: seats, standing spots, the loading square, lobby spots. */
  keep: Point[];
  /** Nothing may overlap these: the screen, the map's own furniture. */
  avoid: MapRect[];
  /** Kept free to walk through (a rug may lie there): doors and the way in, the loading square. */
  passage: MapRect[];
  /** The side of the room the lounge goes to. */
  side: "left" | "right";
}

const CELL = 8;
/** How far a prop's footing keeps from a place someone sits or stands. */
const CLEAR = 10;

/** A number from 0 to 1 for a key: the same key, the same number. */
export function hashOf(key: string): number {
  let h = 2166136261;
  for (let i = 0; i < key.length; i += 1) h = Math.imul(h ^ key.charCodeAt(i), 16777619);
  return (h >>> 0) / 4294967296;
}

/** A well-mixed number from 0 to 1 for a key (keys differing in one letter differ here). */
export function chance(key: string): number {
  return seeded(key)();
}

/** A small deterministic random sequence for a key. */
function seeded(key: string): () => number {
  let s = Math.floor(hashOf(key) * 4294967296) | 0;
  return () => {
    s = (s + 0x6d2b79f5) | 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function overlaps(a: MapRect, b: MapRect): boolean {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
}

/** The part of a prop that stands on the floor: its bottom rows, a little narrower. */
export function footing(p: { kind: PropKind; x: number; y: number }): MapRect | null {
  const s = PROPS[p.kind];
  if (!s.foot) return null;
  const inset = s.w > 10 ? 2 : 1;
  return { x: p.x + inset, y: p.y + s.h - s.foot, w: s.w - 2 * inset, h: s.foot };
}

function cellsOf(g: Grid, r: MapRect): number[] {
  const out: number[] = [];
  const c0 = Math.max(0, Math.floor(r.x / CELL));
  const r0 = Math.max(0, Math.floor(r.y / CELL));
  const c1 = Math.min(g.cols, Math.ceil((r.x + r.w) / CELL));
  const r1 = Math.min(g.rows, Math.ceil((r.y + r.h) / CELL));
  for (let row = r0; row < r1; row += 1) for (let c = c0; c < c1; c += 1) out.push(row * g.cols + c);
  return out;
}

/** Whether every target can be walked to from `from`, cell to cell (no corner cutting). */
export function reachable(g: Grid, from: Point, targets: Point[]): boolean {
  const start = Math.floor(from.y / CELL) * g.cols + Math.floor(from.x / CELL);
  if (!g.grid[start]) return false;
  const seen = new Uint8Array(g.cols * g.rows);
  const queue = [start];
  seen[start] = 1;
  for (let i = 0; i < queue.length; i += 1) {
    const cell = queue[i];
    const c = cell % g.cols;
    const neighbours = [cell - g.cols, cell + g.cols, c > 0 ? cell - 1 : -1, c < g.cols - 1 ? cell + 1 : -1];
    for (const n of neighbours)
      if (n >= 0 && n < seen.length && !seen[n] && g.grid[n]) {
        seen[n] = 1;
        queue.push(n);
      }
  }
  return targets.every((t) => seen[Math.floor(t.y / CELL) * g.cols + Math.floor(t.x / CELL)] === 1);
}

interface Item {
  kind: PropKind;
  dx: number;
  dy: number;
  covered?: boolean;
}

/**
 * Furnish the rooms. `g` is the walk grid: each prop's footing is marked
 * unwalkable in it. Everything in `targets` stays reachable from `origin`.
 */
export function furnish(rooms: FurnishRoom[], g: Grid, origin: Point, targets: Point[], backWall: number): Prop[] {
  const props: Prop[] = [];
  // Only what can be reached now must stay reachable.
  const goals = targets.filter((t) => reachable(g, origin, [t]));

  for (const room of rooms) {
    const random = seeded(room.slot);
    const pick = <T>(list: readonly T[]): T => list[Math.floor(random() * list.length) % list.length];
    const F = room.floor;
    const mine: Prop[] = [];
    // Sprites nothing may overlap: desks and their chairs, the room's avoid list.
    const solid: MapRect[] = [
      ...room.avoid,
      ...room.passage,
      ...room.desks.flatMap((d) => [
        { x: d.x, y: d.y, w: 64, h: 40 },
        { x: d.x + 16, y: d.y - 14, w: 32, h: 32 },
      ]),
    ];
    const still = room.vacant;

    const fits = (items: Item[], at: Point): boolean => {
      for (const item of items) {
        const s = PROPS[item.kind];
        const r = { x: at.x + item.dx, y: at.y + item.dy, w: s.w, h: s.h };
        // Inside the room: the footing on the floor, the top leaning on the back wall's
        // lower half at most (its windows stay clear).
        if (r.x < F.x - 2 || r.x + r.w > F.x + F.w + 2) return false;
        if (r.y < room.rect.y + backWall - 20 || r.y + r.h > F.y + F.h + 4) return false;
        const foot = footing({ kind: item.kind, x: r.x, y: r.y });
        if (foot && (foot.y < F.y - 2 || foot.y + foot.h > F.y + F.h + 2)) return false;
        if (item.kind === "rug") {
          // Flat: walked over, so only the screen and the map's furniture are in its way.
          if (room.avoid.some((a) => overlaps(a, r))) return false;
          if (mine.some((p) => p.kind === "rug" && overlaps(p, r))) return false;
          continue;
        }
        if (solid.some((a) => overlaps(a, r))) return false;
        if (mine.some((p) => p.kind !== "rug" && overlaps(p, r))) return false;
        if (foot) {
          const wide = { x: foot.x - CLEAR, y: foot.y - CLEAR, w: foot.w + 2 * CLEAR, h: foot.h + 2 * CLEAR };
          if (room.keep.some((k) => k.x >= wide.x && k.x <= wide.x + wide.w && k.y >= wide.y && k.y <= wide.y + wide.h)) return false;
          if (!still && cellsOf(g, foot).some((c) => !g.grid[c])) return false;
        }
      }
      return true;
    };

    const tryPlace = (items: Item[], candidates: Point[]): Point | null => {
      for (const at of candidates) {
        if (!fits(items, at)) continue;
        const cells = still
          ? []
          : items.flatMap((item) => {
              const foot = footing({ kind: item.kind, x: at.x + item.dx, y: at.y + item.dy });
              return foot ? cellsOf(g, foot) : [];
            });
        const before = cells.map((c) => g.grid[c]);
        for (const c of cells) g.grid[c] = 0;
        if (cells.length && !reachable(g, origin, goals)) {
          cells.forEach((c, i) => (g.grid[c] = before[i]));
          continue;
        }
        for (const item of items) {
          const s = PROPS[item.kind];
          const prop: Prop = {
            kind: item.kind,
            x: at.x + item.dx,
            y: at.y + item.dy,
            w: s.w,
            h: s.h,
            slot: room.slot,
            covered: Boolean(item.covered),
            still: still || item.kind === "rug",
          };
          mine.push(prop);
        }
        return at;
      }
      return null;
    };

    /* Where to try: along the back wall, down a side wall, anywhere on the floor. */
    const step = 4;
    const backY = (h: number, foot: number) => F.y - h + foot;
    const along = (w: number, from: "left" | "right"): number[] => {
      const xs: number[] = [];
      for (let x = F.x; x + w <= F.x + F.w; x += step) xs.push(x);
      if (F.x + F.w - w > xs[xs.length - 1]) xs.push(F.x + F.w - w);
      return from === "left" ? xs : xs.reverse();
    };
    const back = (kind: PropKind, from: "left" | "right"): Point[] => {
      const s = PROPS[kind];
      return along(s.w, from).flatMap((x) => [0, step, 2 * step].map((dy) => ({ x, y: backY(s.h, s.foot) + dy })));
    };
    const side = (kind: PropKind, which: "left" | "right"): Point[] => {
      const s = PROPS[kind];
      const x = which === "left" ? F.x : F.x + F.w - s.w;
      const out: Point[] = [];
      for (let y = backY(s.h, s.foot); y + s.h <= F.y + F.h + 2; y += step) out.push({ x, y }, { x: which === "left" ? x + step : x - step, y });
      return out;
    };
    const anywhere = (kind: PropKind, from: "left" | "right"): Point[] => {
      const s = PROPS[kind];
      const out: Point[] = [];
      for (let y = backY(s.h, s.foot); y + s.h <= F.y + F.h + 2; y += step) for (const x of along(s.w, from)) out.push({ x, y });
      return out;
    };
    const lounge = room.side;
    const other = lounge === "left" ? "right" : "left";
    const one = (kind: PropKind, candidates: Point[], covered = false) => tryPlace([{ kind, dx: 0, dy: 0, covered }], candidates);

    if (room.kind === "product" && !room.vacant) {
      // A rug under the desks.
      if (room.desks.length) {
        const x0 = Math.min(...room.desks.map((d) => d.x));
        const x1 = Math.max(...room.desks.map((d) => d.x + 64));
        const y0 = Math.min(...room.desks.map((d) => d.y - 14));
        const y1 = Math.max(...room.desks.map((d) => d.y + 40));
        const cx = Math.round((x0 + x1) / 2 - 48);
        const cy = Math.round((y0 + y1) / 2 - 24);
        one("rug", [0, 4, -4, 8, -8, 12].flatMap((dy) => [0, -8, 8].map((dx) => ({ x: cx + dx, y: cy + dy }))));
      }
      // A corner lounge: a small sofa against the back wall, a table before it, a tall plant beside it.
      const plantAt = lounge === "left" ? 50 : -18;
      const sofas = back("sofa-small", lounge).slice(0, 18);
      if (
        !tryPlace(
          [
            { kind: "sofa-small", dx: 0, dy: 0 },
            { kind: "coffee-table", dx: 8, dy: 26 },
            { kind: "plant-tall", dx: plantAt, dy: -4 },
          ],
          sofas,
        )
      )
        tryPlace(
          [
            { kind: "sofa-small", dx: 0, dy: 0 },
            { kind: "coffee-table", dx: 8, dy: 26 },
          ],
          sofas,
        );
      // Storage along the other wall, the printer beside it.
      const storage = pick(["bookshelf", "cabinet"] as const);
      const stored = one(storage, [...side(storage, other), ...back(storage, other).slice(0, 24)]);
      if (stored) one("printer", [...side("printer", other), ...back("printer", other)]);
      // A lamp, and a water cooler or a mobile whiteboard.
      one(pick(["water-cooler", "whiteboard-stand"] as const), [...back("water-cooler", lounge), ...side("water-cooler", lounge)]);
      one("floor-lamp", [...side("floor-lamp", lounge), ...side("floor-lamp", other), ...back("floor-lamp", other)]);
      // Small plants between desks of a row, a bin at a row's end.
      const sorted = [...room.desks].sort((a, b) => a.y - b.y || a.x - b.x);
      for (let i = 1; i < sorted.length; i += 1) {
        const a = sorted[i - 1];
        const b = sorted[i];
        if (a.y !== b.y || random() < 0.35) continue;
        const gap = b.x - (a.x + 64);
        if (gap >= 10) one("plant-small", [{ x: a.x + 64 + Math.floor((gap - 12) / 2), y: a.y + 24 }]);
      }
      const end = sorted[random() < 0.5 ? 0 : sorted.length - 1];
      if (end) one("bin", [{ x: end.x - 10, y: end.y + 28 }, { x: end.x + 66, y: end.y + 28 }, { x: end.x + 66, y: end.y + 20 }]);
      one("plant-small", [...side("plant-small", other).reverse(), ...side("plant-small", lounge).reverse()]);
    } else if (room.kind === "product") {
      // A room nobody uses: a stack of boxes, a lone plant, sheeted furniture.
      const corner = pick(["left", "right"] as const);
      const away = corner === "left" ? "right" : "left";
      const base = anywhere("box", corner).filter((p) => p.y + 16 >= F.y + F.h - 18);
      const at = tryPlace(
        [
          { kind: "box", dx: 0, dy: 0 },
          { kind: "box", dx: 17, dy: 2 },
        ],
        base,
      );
      if (at) mine.push({ kind: "box", x: at.x + 2, y: at.y - 10, w: 16, h: 16, slot: room.slot, covered: false, still: true });
      one(pick(["sofa-small", "cabinet", "bookshelf"] as const), back("sofa-small", away).slice(0, 30), true);
      one("plant-tall", back("plant-tall", corner).slice(0, 20));
    } else if (room.kind === "cluster") {
      // Two spare racks on the floor, away from the loading square; a cabinet.
      const racks = pick(["left", "right"] as const);
      const pair: Item[] = [
        { kind: "server-rack", dx: 0, dy: 0 },
        { kind: "server-rack", dx: 26, dy: 0 },
      ];
      tryPlace(pair, anywhere("server-rack", racks).filter((p) => p.y + 40 <= F.y + F.h - 6));
      one("cabinet", [...side("cabinet", racks === "left" ? "right" : "left"), ...back("cabinet", racks === "left" ? "right" : "left")]);
    } else if (room.kind === "lobby") {
      // A rug in the middle, plants and a lamp by the walls.
      const cx = Math.round(room.rect.x + room.rect.w / 2 - 48);
      const cy = Math.round(F.y + F.h / 2 - 24);
      one("rug", [0, 4, -4, 8, 12].map((dy) => ({ x: cx, y: cy + dy })));
      one("floor-lamp", [...back("floor-lamp", "right"), ...back("floor-lamp", "left")]);
      one("plant-tall", [...back("plant-tall", "right"), ...back("plant-tall", "left")]);
      one("plant-small", [...anywhere("plant-small", "right").reverse()]);
    }
    props.push(...mine);
  }
  return props;
}
