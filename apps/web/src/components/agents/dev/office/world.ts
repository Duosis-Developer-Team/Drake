/**
 * The office's floor plan, in art pixels: one connected building from the
 * map's layout (layout.ts), with the repositories given out to its rooms.
 *
 * Pure: rooms, desks, doors and the walk grid come from the layout and the
 * repositories alone, so the plan is the same for every viewer and testable
 * without a canvas. People walk the open floor of a room, out through its
 * doors, along the corridors and in through another room's doors.
 */

import { DEFAULT_LAYOUT, SLOT_ORDER, type MapRect, type MapSlot, type OfficeLayout } from "./layout";

export const CELL = 8;
export const SIZE = {
  /** The desk's front (`desk-front.png`): monitor from behind, the top, the legs. */
  desk: { w: 64, h: 40 },
  /** The chair (`chair.png`), drawn behind the person sitting in it. */
  chair: { w: 32, h: 32 },
  frame: { w: 32, h: 48 },
  head: 32,
  box: 16,
  tile: 32,
} as const;
/** The row of the desk's front where its top surface starts (its monitor stands above). */
export const DESK_SURFACE = 17;
/** Where the chair is drawn, from the desk's top-left: centred, its base behind the monitor. */
export const CHAIR = { x: 16, y: -14 };
/**
 * A seated person's feet, from the desk's top-left: in the chair, the head
 * just clearing the monitor, the body behind the desk.
 */
export const SEATED = { x: 32, y: 20 };
/** How much floor a desk takes, chair included: from the chair's top to the desk's legs. */
const FOOTPRINT = { top: CHAIR.y, h: SIZE.desk.h - CHAIR.y };
/** Where a character stands in its frame (the art's anchor). */
export const ANCHOR = { x: 16, y: 45 };
/** People stand at least a character's width apart (a 4× character drawn at Z = 3 is ~43 wide). */
export const SPACING = 44;
/** The id of the room the least active repositories share when there are more than rooms. */
export const OTHER = "other";

export type Rect = MapRect;
export interface Point {
  x: number;
  y: number;
}
export interface Desk {
  x: number;
  y: number;
  /** Where one walks to sit: in the chair, behind the desk. */
  seat: Point;
  owner: string | null;
}
export type RoomKind = "product" | "cluster" | "lobby";
export interface Room extends Rect {
  /** A repository id, OTHER, "cluster", "lobby", or the slot's id when vacant. */
  id: string;
  slot: string;
  kind: RoomKind;
  screen: Rect;
  doors: { side: "N" | "S"; x: number; width: number }[];
  desks: Desk[];
  /** Places to stand when every desk is taken. */
  standing: Point[];
  vacant: boolean;
  /** The repositories this room stands for (one, or several for OTHER). */
  members: string[];
}
export interface World {
  w: number;
  h: number;
  cols: number;
  rows: number;
  /** 1 = walkable, per CELL × CELL cell, row-major. */
  grid: Uint8Array;
  /** Product rooms with a repository (or OTHER). */
  rooms: Room[];
  /** Product rooms nobody uses: dark, door closed. */
  vacant: Room[];
  cluster: Room;
  lobby: Room;
  corridors: Rect[];
  lobbySpots: Point[];
  /** The cluster's loading spot. */
  drop: Point;
  /** Just inside the main entrance. */
  entrance: Point;
  /** Repository id → the room it is in. */
  roomOf: Record<string, string>;
  layout: OfficeLayout;
}

export interface RoomInput {
  id: string;
  desks: string[];
  catalogued?: boolean;
  commits?: number;
}

const DESK_GAP = 24;
/** Between two rows of desks: the first's legs, a walkway, the second's chair. */
const ROW_STEP = FOOTPRINT.h + 16;

/**
 * Desks for these owners, spaced and centred on the free floor (as many as
 * fit). The free floor may be borrowed into by a chair's height above it.
 */
function desksIn(free: Rect | null, owners: string[]): { desks: Desk[]; standing: Point[] } {
  if (!free) return { desks: [], standing: [] };
  const floor = { ...free, y: free.y - 16, h: free.h + 16 };
  const perRow = Math.max(1, Math.floor((floor.w + DESK_GAP) / (SIZE.desk.w + DESK_GAP)));
  const rowsFit = floor.h >= ROW_STEP + FOOTPRINT.h ? 2 : 1;
  const capacity = perRow * rowsFit;
  const count = Math.min(capacity, Math.max(2, owners.length));
  const rows = count > perRow ? 2 : 1;
  const height = rows === 1 ? FOOTPRINT.h : ROW_STEP + FOOTPRINT.h;
  // Whole cells, so a seat behind a desk is always free to sit in.
  const top = floor.y + Math.floor((floor.h - height) / 2 / CELL) * CELL;
  const desks: Desk[] = [];
  for (let i = 0; i < count; i += 1) {
    const row = Math.floor(i / perRow);
    const inRow = row === rows - 1 ? count - perRow * row : perRow;
    const rowSpan = inRow * SIZE.desk.w + (inRow - 1) * DESK_GAP;
    const col = i - row * perRow;
    const x = floor.x + Math.round((floor.w - rowSpan) / 2) + col * (SIZE.desk.w + DESK_GAP);
    const y = top - FOOTPRINT.top + row * ROW_STEP;
    desks.push({ x, y, seat: { x: x + SEATED.x, y: y + 4 }, owner: owners[i] ?? null });
  }
  // Anyone without a desk stands in a spaced row along the front of the floor.
  const standing: Point[] = [];
  for (let x = free.x + SPACING / 2; x <= free.x + free.w - SPACING / 2; x += SPACING)
    standing.push({ x, y: free.y + free.h - 6 });
  return { desks, standing };
}

/** Repositories in the order they get rooms: the catalog's first, then the busiest. */
export function byActivity(rooms: RoomInput[]): RoomInput[] {
  return rooms
    .map((r, i) => ({ r, i }))
    .sort(
      (a, b) =>
        Number(Boolean(b.r.catalogued)) - Number(Boolean(a.r.catalogued)) ||
        (b.r.commits ?? 0) - (a.r.commits ?? 0) ||
        b.r.desks.length - a.r.desks.length ||
        a.i - b.i,
    )
    .map((x) => x.r);
}

export function layoutOffice(input: RoomInput[], layout: OfficeLayout = DEFAULT_LAYOUT): World {
  const products = layout.slots.filter((s) => s.kind === "product");
  const order = [
    ...SLOT_ORDER.map((id) => products.find((s) => s.id === id)).filter((s): s is MapSlot => Boolean(s)),
    ...products.filter((s) => !SLOT_ORDER.includes(s.id)),
  ];
  const ranked = byActivity(input);
  // More repositories than rooms: the last room is shared by the least active.
  const own = ranked.length > order.length ? ranked.slice(0, order.length - 1) : ranked;
  const rest = ranked.length > order.length ? ranked.slice(order.length - 1) : [];
  const roomOf: Record<string, string> = {};
  const build = (slot: MapSlot, id: string, members: string[], owners: string[], vacant: boolean): Room => {
    // Only product rooms have desks; the cluster and the lobby keep their floor clear.
    const placed = vacant || slot.kind !== "product" ? { desks: [], standing: [] } : desksIn(slot.free, owners);
    return {
      ...slot.rect,
      id,
      slot: slot.id,
      kind: slot.kind,
      screen: slot.screen,
      doors: slot.doors,
      desks: placed.desks,
      standing: placed.standing,
      vacant,
      members,
    };
  };
  const rooms: Room[] = [];
  const vacant: Room[] = [];
  order.forEach((slot, i) => {
    const repo = own[i];
    if (repo) {
      rooms.push(build(slot, repo.id, [repo.id], repo.desks, false));
      roomOf[repo.id] = repo.id;
    } else if (rest.length && i === order.length - 1) {
      const owners = [...new Set(rest.flatMap((r) => r.desks))];
      rooms.push(build(slot, OTHER, rest.map((r) => r.id), owners, false));
      for (const r of rest) roomOf[r.id] = OTHER;
    } else vacant.push(build(slot, slot.id, [], [], true));
  });
  const clusterSlot = layout.slots.find((s) => s.kind === "cluster") as MapSlot;
  const lobbySlot = layout.slots.find((s) => s.kind === "lobby") as MapSlot;
  const cluster = build(clusterSlot, "cluster", [], [], false);
  const lobby = build(lobbySlot, "lobby", [], [], false);

  const { w, h, backWall } = layout;
  const cols = Math.ceil(w / CELL);
  const rows = Math.ceil(h / CELL);
  const grid = new Uint8Array(cols * rows);
  const mark = (rect: Rect, value: 0 | 1) => {
    const c0 = Math.max(0, Math.floor(rect.x / CELL));
    const r0 = Math.max(0, Math.floor(rect.y / CELL));
    const c1 = Math.min(cols, Math.ceil((rect.x + rect.w) / CELL));
    const r1 = Math.min(rows, Math.ceil((rect.y + rect.h) / CELL));
    for (let r = r0; r < r1; r += 1) for (let c = c0; c < c1; c += 1) grid[r * cols + c] = value;
  };
  for (const c of layout.corridors) mark(c, 1);
  for (const room of [...rooms, cluster, lobby]) {
    // The floor, clear of the walls: a room is left only by its doors.
    mark({ x: room.x + 8, y: room.y + backWall + 4, w: room.w - 16, h: room.h - backWall - 12 }, 1);
    for (const d of room.doors) {
      if (d.side === "S") mark({ x: d.x, y: room.y + room.h - 8, w: d.width, h: 16 }, 1);
      else mark({ x: d.x, y: room.y - 8, w: d.width, h: backWall + 16 }, 1);
    }
    // A desk blocks from its top surface down; the chair behind it stays free to sit in.
    for (const desk of room.desks)
      mark({ x: desk.x + 2, y: desk.y + DESK_SURFACE + 1, w: SIZE.desk.w - 4, h: SIZE.desk.h - DESK_SURFACE - 1 }, 0);
    for (const f of layout.slots.find((s) => s.id === room.slot)?.blocked ?? []) mark(f, 0);
  }
  const entranceDoor = lobbySlot.entrance;
  if (entranceDoor) mark({ x: entranceDoor.x, y: lobby.y + lobby.h - 8, w: entranceDoor.width, h: h - lobby.y - lobby.h + 8 }, 1);

  // Standing room in the lobby: a spaced grid, a character's width and more apart.
  const lobbySpots: Point[] = [];
  const lf = lobbySlot.free ?? { x: lobby.x + 24, y: lobby.y + backWall + 24, w: lobby.w - 48, h: 80 };
  for (let y = lf.y + 8; y <= lf.y + lf.h; y += SPACING)
    for (let x = lf.x + SPACING / 2; x <= lf.x + lf.w - SPACING / 2 + 1; x += SPACING) {
      const p = { x, y };
      if (walkable({ cols, rows, grid } as World, p)) lobbySpots.push(p);
    }
  const loading = clusterSlot.loading ?? { x: cluster.x + cluster.w / 2, y: cluster.y + cluster.h - 48 };
  return {
    w,
    h,
    cols,
    rows,
    grid,
    rooms,
    vacant,
    cluster,
    lobby,
    corridors: layout.corridors,
    lobbySpots,
    drop: loading,
    entrance: entranceDoor
      ? { x: entranceDoor.x + entranceDoor.width / 2, y: lobby.y + lobby.h - 12 }
      : { x: lobby.x + lobby.w / 2, y: lobby.y + lobby.h - 12 },
    roomOf,
    layout,
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
