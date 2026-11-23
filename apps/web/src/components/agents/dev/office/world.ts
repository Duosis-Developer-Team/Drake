/**
 * The office's floor plan, in art pixels: one connected building from the
 * map's layout (layout.ts), with the repositories given out to its rooms.
 *
 * Pure: rooms, desks, doors and the walk grid come from the layout and the
 * repositories alone, so the plan is the same for every viewer and testable
 * without a canvas. People walk the open floor of a room, out through its
 * doors, along the corridors and in through another room's doors.
 */

import { chance, furnish, overlaps, type FurnishRoom, type Prop } from "./furnish";
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
  /** The rooms' furniture (furnish.ts). */
  props: Prop[];
  layout: OfficeLayout;
}

export interface RoomInput {
  id: string;
  desks: string[];
  catalogued?: boolean;
  commits?: number;
}

/** Between two desks of a row: room for a plant, not for a person (they sit from behind). */
const DESK_GAP = 12;
/** Between two rows of desks: the first's legs, a walkway, the second's chair. */
const ROW_STEP = FOOTPRINT.h + 16;
/** The floor a corner lounge needs beside the desks: a small sofa, a plant, a gap. */
const LOUNGE = 72;

/** The side of a room its lounge goes to: the same for a slot, every time. */
export function loungeSide(slot: string): "left" | "right" {
  return chance(`lounge:${slot}`) < 0.5 ? "left" : "right";
}

/**
 * Desks for these owners, spaced and centred on the free floor (as many as
 * fit). The free floor may be borrowed into by a chair's height above it.
 */
function desksIn(free: Rect | null, owners: string[], lounge: "left" | "right"): { desks: Desk[]; standing: Point[] } {
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
  // A single row with room to spare keeps to one side: the lounge takes the other.
  const widest = Math.min(count, perRow) * SIZE.desk.w + (Math.min(count, perRow) - 1) * DESK_GAP;
  const shift = rows === 1 && floor.w - widest >= LOUNGE ? Math.round((floor.w - widest) / 2) - 8 : 0;
  const desks: Desk[] = [];
  for (let i = 0; i < count; i += 1) {
    const row = Math.floor(i / perRow);
    const inRow = row === rows - 1 ? count - perRow * row : perRow;
    const rowSpan = inRow * SIZE.desk.w + (inRow - 1) * DESK_GAP;
    const col = i - row * perRow;
    const x =
      floor.x + Math.round((floor.w - rowSpan) / 2) + (lounge === "left" ? shift : -shift) + col * (SIZE.desk.w + DESK_GAP);
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
    const placed =
      vacant || slot.kind !== "product" ? { desks: [], standing: [] } : desksIn(slot.free, owners, loungeSide(slot.id));
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

  // Standing room in the lobby: spaced out, the middle of the floor first, and
  // nobody standing in the reception, the coffee bar or on a sofa (a standing
  // character, about 44 tall, is kept off the map's furniture).
  const lf = lobbySlot.free ?? { x: lobby.x + 24, y: lobby.y + backWall + 24, w: lobby.w - 48, h: 80 };
  // The map's furniture, and the way through the lobby's doors.
  const lobbyBlocked = [
    ...(lobbySlot.blocked ?? []),
    ...lobby.doors.map((d) =>
      d.side === "N"
        ? { x: d.x - 4, y: lobby.y, w: d.width + 8, h: backWall + 26 }
        : { x: d.x - 4, y: lobby.y + lobby.h - 30, w: d.width + 8, h: 30 },
    ),
  ];
  const candidates: Point[] = [];
  for (let y = lf.y + 4; y <= lf.y + lf.h; y += 4)
    for (let x = lf.x + 12; x <= lf.x + lf.w - 12; x += 4) {
      const p = { x, y };
      const body = { x: x - 14, y: y - 44, w: 28, h: 46 };
      if (walkable({ cols, rows, grid } as World, p) && !lobbyBlocked.some((b) => overlaps(b, body))) candidates.push(p);
    }
  const middle = { x: lobby.x + lobby.w / 2, y: lf.y + lf.h * 0.55 };
  const far = (p: Point) => Math.hypot(p.x - middle.x, (p.y - middle.y) * 1.5);
  candidates.sort((a, b) => far(a) - far(b) || a.x - b.x || a.y - b.y);
  const lobbySpots: Point[] = [];
  for (const p of candidates) if (lobbySpots.every((q) => Math.hypot(q.x - p.x, q.y - p.y) >= SPACING)) lobbySpots.push(p);

  const loading = clusterSlot.loading ?? { x: cluster.x + cluster.w / 2, y: cluster.y + cluster.h - 48 };
  const entrance = entranceDoor
    ? { x: entranceDoor.x + entranceDoor.width / 2, y: lobby.y + lobby.h - 12 }
    : { x: lobby.x + lobby.w / 2, y: lobby.y + lobby.h - 12 };

  // Furniture: off every seat, spot, door and the loading square, never
  // cutting anywhere off from the entrance.
  const plan: FurnishRoom[] = [...rooms, ...vacant, cluster, lobby].map((room) => {
    const slot = layout.slots.find((s) => s.id === room.slot) as MapSlot;
    const doors = room.doors.map((d) =>
      d.side === "N"
        ? { x: d.x - 4, y: room.y, w: d.width + 8, h: backWall + 26 }
        : { x: d.x - 4, y: room.y + room.h - 34, w: d.width + 8, h: 34 },
    );
    const avoid: Rect[] = [room.screen, ...(slot.blocked ?? [])];
    const passage: Rect[] = doors;
    if (room.kind === "lobby" && entranceDoor)
      passage.push({ x: entranceDoor.x - 6, y: room.y + room.h - 40, w: entranceDoor.width + 12, h: 40 });
    if (room.kind === "cluster") {
      passage.push({ x: loading.x - 36, y: loading.y - 40, w: 72, h: 64 });
      // The racks drawn along its back wall: nothing in front of them.
      avoid.push({ x: room.x, y: room.y, w: room.w, h: backWall + 18 });
    }
    const keep: Point[] =
      room.kind === "lobby" ? lobbySpots : room.kind === "cluster" ? [loading] : [...room.desks.map((d) => d.seat), ...room.standing];
    return {
      slot: room.slot,
      kind: room.kind,
      vacant: room.vacant,
      rect: { x: room.x, y: room.y, w: room.w, h: room.h },
      floor: { x: room.x + 8, y: room.y + backWall + 4, w: room.w - 16, h: room.h - backWall - 12 },
      desks: room.desks,
      keep,
      avoid,
      passage,
      side: loungeSide(room.slot),
    };
  });
  const props = furnish(plan, { cols, rows, grid }, entrance, reachTargets({ rooms, cluster, lobby, lobbySpots, drop: loading, layout } as World), backWall);
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
    entrance,
    roomOf,
    props,
    layout,
  };
}

/**
 * Everywhere that must be reachable from the entrance: every seat and
 * standing spot, the lobby's spots, the loading square, and just inside every
 * door of a room in use.
 */
export function reachTargets(world: Pick<World, "rooms" | "cluster" | "lobby" | "lobbySpots" | "drop" | "layout">): Point[] {
  const { backWall } = world.layout;
  const doorways = [...world.rooms, world.cluster, world.lobby].flatMap((room) =>
    room.doors.map((d) => ({ x: d.x + d.width / 2, y: d.side === "N" ? room.y + backWall + 6 : room.y + room.h - 14 })),
  );
  return [
    ...world.rooms.flatMap((r) => [...r.desks.map((d) => d.seat), ...r.standing]),
    ...world.lobbySpots,
    world.drop,
    ...doorways,
  ];
}

/**
 * The rooms one steps through with the arrows: everything but empty rooms,
 * row by row from the top, left to right within a row.
 */
export function roomOrder(world: World): Room[] {
  return [...world.rooms, world.cluster, world.lobby].sort((a, b) => a.y - b.y || a.x - b.x);
}

/** The room before or after `id` in reading order, wrapping around. */
export function nextRoom(world: World, id: string, step: 1 | -1): Room {
  const order = roomOrder(world);
  const i = order.findIndex((r) => r.id === id);
  const from = i < 0 ? (step > 0 ? -1 : 0) : i;
  return order[(((from + step) % order.length) + order.length) % order.length];
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
