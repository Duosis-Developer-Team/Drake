/**
 * The office's floor plan, in art pixels (one pixel of the drawing).
 *
 * Pure: rooms, corridors, desks, doors and the walk grid come from the
 * number of product rooms and their desks alone, so the plan is the same
 * for every viewer and testable without a canvas.
 *
 *   [P] : [P] : [P]            further product rooms, three a row (A)
 *   ═══════════ corridor ═══
 *   [P] : [ CLUSTER ] : [P]    row B: the server room centred
 *   ═══════════ corridor ═══
 *   desks [  LOBBY  ] desks    row C: the base, open floor either side
 *   ═══════════ corridor ═══
 *
 * The first two rooms sit beside the cluster; a slot no repository fills is
 * an empty office. ":" is a walkway between rooms, joining the corridors.
 * Product rooms and the cluster are open at the front (their bottom edge);
 * the lobby has a door there. A product room with a corridor behind it also
 * has a back door.
 */

export const CELL = 8;
/** A product room's back wall: its floor starts here (the art's back threshold). */
export const WALL = 72;
export const SIDE = 6;
/** The corridor between two rows of rooms. */
export const CORRIDOR = 32;
/** The walkway between two rooms of a row. */
export const PASS = 24;
export const SIZE = {
  product: { w: 320, h: 224 },
  cluster: { w: 384, h: 224 },
  lobby: { w: 448, h: 192 },
  desk: { w: 64, h: 48 },
  frame: { w: 32, h: 48 },
  head: 32,
  box: 16,
  tile: 32,
} as const;
/** Where a back door sits in a product room, from its left edge (the art's back threshold). */
export const BACKDOOR_X = 198;
/** The lobby's front door: a gap in its low front wall. */
export const LOBBY_DOOR = { x: 180, w: 88 };
export const DOOR_WIDTH = LOBBY_DOOR.w;
/** The row of a desk sprite where its top surface starts: the chair is above it. */
export const DESK_SURFACE = 20;
/**
 * A seated person, in desk coordinates: centred on the chair, head and
 * shoulders above the desk top, the cross-legged legs hidden behind it.
 */
export const SEATED = { x: 34, y: 31 };
/** Where a character stands in its frame (the art's anchor). */
export const ANCHOR = { x: 16, y: 45 };
/** Plaques and slots on the room art, in room coordinates (the art's manifest). */
export const PLAQUE = {
  product: { x: 125, y: 18, w: 36, h: 9 },
  cluster: { x: 167, y: 33, w: 51, h: 15 },
  lobby: { x: 324, y: 155, w: 107, h: 17 },
} as const;
/** Each room's open floor, and what stands on it, in room coordinates. */
const FLOOR: Record<"product" | "cluster" | "lobby", Rect> = {
  product: { x: SIDE + 2, y: WALL + 4, w: SIZE.product.w - 2 * SIDE - 4, h: SIZE.product.h - WALL - 4 },
  cluster: { x: 36, y: 88, w: 312, h: SIZE.cluster.h - 88 },
  lobby: { x: 8, y: 68, w: 432, h: 116 },
};
const FURNITURE: Record<"product" | "cluster" | "lobby", Rect[]> = {
  // The bookcase on the left wall, the plant on the right.
  product: [
    { x: 0, y: 0, w: 28, h: 124 },
    { x: 290, y: 0, w: 30, h: 90 },
  ],
  cluster: [],
  // Sofas with their tables, the plants, the reception desk.
  lobby: [
    { x: 0, y: 40, w: 104, h: 82 },
    { x: 344, y: 40, w: 104, h: 82 },
    { x: 0, y: 80, w: 34, h: 58 },
    { x: 416, y: 72, w: 32, h: 58 },
    { x: 296, y: 124, w: 152, h: 68 },
  ],
};
export const ROW_PER = 3;
/** A strip of corridor above the top row, where its rooms' names sit. */
export const TOP = 16;
/** The corridor along the bottom, in front of the lobby. */
export const BOTTOM = 32;
/**
 * The office is as narrow as its widest row (two rooms and the cluster, a
 * passage between each) so the whole of it fits a laptop's panel at one CSS
 * pixel per art pixel.
 */
export const WORLD_W = SIZE.product.w * 2 + SIZE.cluster.w + PASS * 2;

export interface Point {
  x: number;
  y: number;
}
export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}
export interface Desk {
  x: number;
  y: number;
  /** Where a seated person's feet are. */
  seat: Point;
  owner: string | null;
}
export type RoomKind = "product" | "cluster" | "lobby";
export interface Room extends Rect {
  id: string;
  kind: RoomKind;
  /** A product room with a door in its back wall too. */
  backdoor: boolean;
  /** Just outside the front door, on the corridor. */
  door: Point;
  desks: Desk[];
  plaque: Rect;
}
export interface World {
  w: number;
  h: number;
  cols: number;
  rows: number;
  /** 1 = walkable, per CELL × CELL cell, row-major. */
  grid: Uint8Array;
  rooms: Room[];
  cluster: Room;
  lobby: Room;
  corridors: Rect[];
  /** The open floor either side of the lobby. */
  plazas: Rect[];
  /** Free desks on the plazas (scenery; nobody owns them). */
  hotDesks: Desk[];
  /** Product slots no repository fills (drawn as empty offices). */
  vacant: Rect[];
  lobbySpots: Point[];
  coffee: Point;
  drop: Point;
}

export interface RoomInput {
  id: string;
  desks: string[];
}

const MAX_DESKS = 6;

function desksFor(room: Rect, owners: string[]): Desk[] {
  const out: Desk[] = [];
  const count = Math.min(MAX_DESKS, Math.max(owners.length, 2));
  // One row in the middle of the floor, or two; each row centred.
  const rows = count > 3 ? 2 : 1;
  const perRow = Math.ceil(count / rows);
  const ys = rows === 1 ? [room.y + 108] : [room.y + 88, room.y + 156];
  for (let i = 0; i < count; i += 1) {
    const row = Math.floor(i / perRow);
    const inRow = row === rows - 1 ? count - perRow * row : perRow;
    const col = i - row * perRow;
    const span = inRow * SIZE.desk.w + (inRow - 1) * 24;
    const x = room.x + Math.round((room.w - span) / 2) + col * (SIZE.desk.w + 24);
    const y = ys[row];
    // The seat is where one walks to: in the chair, behind the desk.
    out.push({ x, y, seat: { x: x + SEATED.x, y: y + 16 }, owner: owners[i] ?? null });
  }
  return out;
}

function plaqueOf(room: Rect, kind: RoomKind): Rect {
  const p = PLAQUE[kind];
  return { x: room.x + p.x, y: room.y + p.y, w: p.w, h: p.h };
}

function rowStart(count: number, width: number): number {
  const total = count * width + Math.max(0, count - 1) * PASS;
  return Math.round((WORLD_W - total) / 2);
}

export function layoutOffice(input: RoomInput[], backdoorX = BACKDOOR_X): World {
  const products = input.slice();
  const middle = products.slice(0, 2);
  const upper = products.slice(2);
  const upperRows = Math.ceil(upper.length / ROW_PER);
  const rowH = SIZE.product.h + CORRIDOR;
  const yB = TOP + upperRows * rowH;
  const yC = yB + rowH;
  // The bottom row is as tall as the lobby: the office stays short enough to see whole.
  const h = yC + SIZE.lobby.h + BOTTOM;
  const rooms: Room[] = [];
  const vacant: Rect[] = [];
  const passages: Rect[] = [];

  const make = (id: string, kind: RoomKind, rect: Rect, backdoor: boolean, owners: string[]): Room => ({
    ...rect,
    id,
    kind,
    backdoor,
    door: { x: rect.x + Math.round(rect.w / 2), y: rect.y + rect.h + CORRIDOR / 2 },
    desks: kind === "product" ? desksFor(rect, owners) : [],
    plaque: plaqueOf(rect, kind),
  });

  for (let r = 0; r < upperRows; r += 1) {
    const inRow = upper.slice(r * ROW_PER, r * ROW_PER + ROW_PER);
    const start = rowStart(inRow.length, SIZE.product.w);
    const y = TOP + r * rowH;
    inRow.forEach((room, i) => {
      const rect = { x: start + i * (SIZE.product.w + PASS), y, ...SIZE.product };
      // A back door only where a corridor runs behind (not along the top edge).
      rooms.push(make(room.id, "product", rect, r > 0, room.desks));
      if (i > 0) passages.push({ x: rect.x - PASS, y, w: PASS, h: SIZE.product.h });
    });
    // Around a short row, the floor is open too.
    passages.push({ x: 0, y, w: start, h: SIZE.product.h });
    const end = start + inRow.length * SIZE.product.w + (inRow.length - 1) * PASS;
    passages.push({ x: end, y, w: WORLD_W - end, h: SIZE.product.h });
  }
  // Beside the cluster, left then right; a back door when a corridor runs behind.
  const slots: Rect[] = [
    { x: 0, y: yB, ...SIZE.product },
    { x: WORLD_W - SIZE.product.w, y: yB, ...SIZE.product },
  ];
  slots.forEach((rect, i) => {
    const room = middle[i];
    if (room) rooms.push(make(room.id, "product", rect, upperRows > 0, room.desks));
    else vacant.push(rect);
  });
  const cluster = make(
    "cluster",
    "cluster",
    { x: Math.round((WORLD_W - SIZE.cluster.w) / 2), y: yB, ...SIZE.cluster },
    false,
    [],
  );
  passages.push({ x: SIZE.product.w, y: yB, w: cluster.x - SIZE.product.w, h: SIZE.product.h });
  passages.push({ x: cluster.x + cluster.w, y: yB, w: WORLD_W - SIZE.product.w - cluster.x - cluster.w, h: SIZE.product.h });
  // The lobby at the bottom, its front in line with the rows above, open floor either side.
  const lobby = make(
    "lobby",
    "lobby",
    { x: Math.round((WORLD_W - SIZE.lobby.w) / 2), y: yC, ...SIZE.lobby },
    false,
    [],
  );
  const plazas: Rect[] = [
    { x: 0, y: yC, w: lobby.x, h: SIZE.lobby.h },
    { x: lobby.x + lobby.w, y: yC, w: WORLD_W - lobby.x - lobby.w, h: SIZE.lobby.h },
  ];
  // A few free desks in each, anyone's to use: an open-plan corner.
  const hotDesks: Desk[] = plazas.flatMap((p) =>
    [0, 1].map((i) => {
      const x = p.x + Math.round(p.w / 2) - SIZE.desk.w - 12 + i * (SIZE.desk.w + 24);
      const y = p.y + 72;
      return { x, y, seat: { x: x + SEATED.x, y: y + 16 }, owner: null };
    }),
  );
  const corridors: Rect[] = [{ x: 0, y: 0, w: WORLD_W, h: TOP }, ...passages, ...plazas];
  for (let r = 0; r < upperRows; r += 1)
    corridors.push({ x: 0, y: TOP + r * rowH + SIZE.product.h, w: WORLD_W, h: CORRIDOR });
  corridors.push({ x: 0, y: yB + SIZE.product.h, w: WORLD_W, h: CORRIDOR });
  // The corridor along the bottom, in front of the lobby's door.
  corridors.push({ x: 0, y: yC + SIZE.lobby.h, w: WORLD_W, h: BOTTOM });

  const cols = Math.ceil(WORLD_W / CELL);
  const rows = Math.ceil(h / CELL);
  const grid = new Uint8Array(cols * rows);
  const mark = (rect: Rect, value: 0 | 1) => {
    const c0 = Math.max(0, Math.floor(rect.x / CELL));
    const r0 = Math.max(0, Math.floor(rect.y / CELL));
    const c1 = Math.min(cols, Math.ceil((rect.x + rect.w) / CELL));
    const r1 = Math.min(rows, Math.ceil((rect.y + rect.h) / CELL));
    for (let r = r0; r < r1; r += 1) for (let c = c0; c < c1; c += 1) grid[r * cols + c] = value;
  };
  for (const c of corridors) mark(c, 1);
  const all = [...rooms, cluster, lobby];
  const at = (room: Rect, r: Rect): Rect => ({ x: room.x + r.x, y: room.y + r.y, w: r.w, h: r.h });
  for (const room of all) {
    mark(at(room, FLOOR[room.kind]), 1);
    // Product rooms and the cluster are open at the front; the lobby has a door.
    if (room.kind === "lobby") mark({ x: room.x + LOBBY_DOOR.x, y: room.y + room.h - 16, w: LOBBY_DOOR.w, h: 16 }, 1);
    else mark({ x: room.x + FLOOR[room.kind].x, y: room.y + room.h - 16, w: FLOOR[room.kind].w, h: 16 }, 1);
    if (room.backdoor) mark({ x: room.x + backdoorX - 12, y: room.y, w: 24, h: WALL + 8 }, 1);
    for (const f of FURNITURE[room.kind]) mark(at(room, f), 0);
    // A desk blocks from its surface down; the chair behind it stays free to sit in.
    for (const desk of room.desks) mark({ x: desk.x + 2, y: desk.y + 24, w: SIZE.desk.w - 4, h: SIZE.desk.h - 24 }, 0);
  }
  for (const desk of hotDesks) mark({ x: desk.x + 2, y: desk.y + 24, w: SIZE.desk.w - 4, h: SIZE.desk.h - 24 }, 0);

  const lobbySpots: Point[] = [];
  for (let r = 0; r < 3; r += 1)
    for (let c = 0; c < 5; c += 1)
      lobbySpots.push({ x: lobby.x + 124 + c * 38 + (r % 2) * 18, y: lobby.y + 92 + r * 30 });
  return {
    w: WORLD_W,
    h,
    cols,
    rows,
    grid,
    rooms,
    cluster,
    lobby,
    corridors,
    plazas,
    hotDesks,
    vacant,
    lobbySpots,
    // In front of the coffee machine on the kitchen counter.
    coffee: { x: lobby.x + 258, y: lobby.y + 74 },
    // The cluster's loading square (the art's loading target).
    drop: { x: cluster.x + 192, y: cluster.y + 147 },
  };
}

export function walkable(world: World, p: Point): boolean {
  const c = Math.floor(p.x / CELL);
  const r = Math.floor(p.y / CELL);
  return c >= 0 && r >= 0 && c < world.cols && r < world.rows && world.grid[r * world.cols + c] === 1;
}

/** The nearest walkable cell centre to a point (a desk seat sits on a free cell already). */
export function nearestWalkable(world: World, p: Point): Point {
  if (walkable(world, p)) return p;
  const c0 = Math.floor(p.x / CELL);
  const r0 = Math.floor(p.y / CELL);
  for (let d = 1; d < 40; d += 1) {
    for (let dr = -d; dr <= d; dr += 1)
      for (let dc = -d; dc <= d; dc += 1) {
        if (Math.max(Math.abs(dr), Math.abs(dc)) !== d) continue;
        const q = { x: (c0 + dc) * CELL + CELL / 2, y: (r0 + dr) * CELL + CELL / 2 };
        if (walkable(world, q)) return q;
      }
  }
  return p;
}

/**
 * A walk from `from` to `to` along the grid: A* with diagonal steps (no
 * corner cutting), then thinned to the turns. Empty when there is no way.
 */
export function findPath(world: World, from: Point, to: Point): Point[] {
  const start = nearestWalkable(world, from);
  const goal = nearestWalkable(world, to);
  const { cols, rows, grid } = world;
  const sc = Math.floor(start.x / CELL);
  const sr = Math.floor(start.y / CELL);
  const gc = Math.floor(goal.x / CELL);
  const gr = Math.floor(goal.y / CELL);
  const startIdx = sr * cols + sc;
  const goalIdx = gr * cols + gc;
  if (startIdx === goalIdx) return [to];
  const g = new Float32Array(cols * rows).fill(Infinity);
  const came = new Int32Array(cols * rows).fill(-1);
  const closed = new Uint8Array(cols * rows);
  const h = (i: number) => {
    const dc = Math.abs((i % cols) - gc);
    const dr = Math.abs(Math.floor(i / cols) - gr);
    return Math.max(dc, dr) + 0.41 * Math.min(dc, dr);
  };
  // A binary heap of (f, index).
  const heap: number[] = [];
  const fOf = new Float32Array(cols * rows).fill(Infinity);
  const push = (i: number) => {
    heap.push(i);
    let k = heap.length - 1;
    while (k > 0) {
      const p = (k - 1) >> 1;
      if (fOf[heap[p]] <= fOf[heap[k]]) break;
      [heap[p], heap[k]] = [heap[k], heap[p]];
      k = p;
    }
  };
  const pop = (): number => {
    const top = heap[0];
    const last = heap.pop() as number;
    if (heap.length) {
      heap[0] = last;
      let k = 0;
      for (;;) {
        const l = 2 * k + 1;
        const r = l + 1;
        let m = k;
        if (l < heap.length && fOf[heap[l]] < fOf[heap[m]]) m = l;
        if (r < heap.length && fOf[heap[r]] < fOf[heap[m]]) m = r;
        if (m === k) break;
        [heap[m], heap[k]] = [heap[k], heap[m]];
        k = m;
      }
    }
    return top;
  };
  g[startIdx] = 0;
  fOf[startIdx] = h(startIdx);
  push(startIdx);
  const open = (c: number, r: number) => c >= 0 && r >= 0 && c < cols && r < rows && grid[r * cols + c] === 1;
  let found = false;
  while (heap.length) {
    const cur = pop();
    if (closed[cur]) continue;
    if (cur === goalIdx) {
      found = true;
      break;
    }
    closed[cur] = 1;
    const cc = cur % cols;
    const cr = Math.floor(cur / cols);
    for (let dr = -1; dr <= 1; dr += 1)
      for (let dc = -1; dc <= 1; dc += 1) {
        if (!dr && !dc) continue;
        const nc = cc + dc;
        const nr = cr + dr;
        if (!open(nc, nr)) continue;
        if (dr && dc && (!open(cc + dc, cr) || !open(cc, cr + dr))) continue;
        const ni = nr * cols + nc;
        if (closed[ni]) continue;
        const cost = g[cur] + (dr && dc ? 1.414 : 1);
        if (cost < g[ni]) {
          g[ni] = cost;
          came[ni] = cur;
          fOf[ni] = cost + h(ni);
          push(ni);
        }
      }
  }
  if (!found) return [];
  const cells: number[] = [];
  for (let i = goalIdx; i !== -1; i = came[i]) cells.push(i);
  cells.reverse();
  const points = cells.map((i) => ({ x: (i % cols) * CELL + CELL / 2, y: Math.floor(i / cols) * CELL + CELL / 2 }));
  return [...smooth(world, points).slice(1), to];
}

/** Line of sight over walkable cells, sampled every half cell. */
function clear(world: World, a: Point, b: Point): boolean {
  const steps = Math.ceil(Math.hypot(b.x - a.x, b.y - a.y) / (CELL / 2));
  for (let i = 1; i < steps; i += 1) {
    const t = i / steps;
    const x = a.x + (b.x - a.x) * t;
    const y = a.y + (b.y - a.y) * t;
    // Keep a little clearance: the body is wider than a point.
    if (!walkable(world, { x: x - 3, y }) || !walkable(world, { x: x + 3, y })) return false;
  }
  return true;
}

/** String-pulling: keep a point only when the next one cannot be seen past it. */
export function smooth(world: World, points: Point[]): Point[] {
  if (points.length < 3) return points;
  const out = [points[0]];
  let anchor = points[0];
  for (let i = 1; i < points.length - 1; i += 1) {
    if (!clear(world, anchor, points[i + 1])) {
      out.push(points[i]);
      anchor = points[i];
    }
  }
  out.push(points[points.length - 1]);
  return out;
}

export function roomAt(world: World, p: Point): Room | null {
  for (const room of [...world.rooms, world.cluster, world.lobby])
    if (p.x >= room.x && p.x < room.x + room.w && p.y >= room.y && p.y < room.y + room.h) return room;
  return null;
}
