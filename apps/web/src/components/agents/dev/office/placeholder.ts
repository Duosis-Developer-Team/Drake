/**
 * The office map until the drawn one exists: composed from the first kit's
 * room drawings, placed on the map's slots, so the office already reads as
 * one building — an outer wall, rooms with doors, corridors between them.
 *
 * Everything here is scenery: where people walk comes from the layout
 * (world.ts), never from these pixels.
 */

import { sheeted, type Art, type Sprite } from "./art";
import type { Prop } from "./furnish";
import type { Rect, Room, World } from "./world";

const WALL = "#1b2133";
const WALL_LIGHT = "#2c3550";
const WALL_DARK = "#0d111c";
const GOLD = "#b8873a";
const GOLD_LIGHT = "#e2b25c";

/** The first kit's room drawings and where their parts are (in their own pixels). */
const KIT = {
  product: { w: 320, h: 224, wall: 72 },
  /** The doorway in the back wall of the back-door room. */
  doorway: { x: 178, w: 40 },
  /** A strip of plain floor that repeats seamlessly (the tiles repeat every 35 rows). */
  floorStrip: { y: 154, h: 35 },
  cluster: { w: 384, h: 224, top: 3 },
  lobby: { w: 448, h: 192, wall: 62, left: 160, right: 288 },
} as const;

function fill(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, colour: string) {
  ctx.fillStyle = colour;
  ctx.fillRect(x, y, w, h);
}

/** A wall seen from above: a dark top, a lighter lip, a gold line. */
function wallStrip(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number) {
  fill(ctx, x, y, w, h, WALL);
  if (w >= h) {
    fill(ctx, x, y, w, 1, WALL_LIGHT);
    fill(ctx, x, y + h - 2, w, 1, GOLD);
    fill(ctx, x, y + h - 1, w, 1, WALL_DARK);
  } else {
    fill(ctx, x, y, 1, h, WALL_LIGHT);
    fill(ctx, x + w - 1, y, 1, h, WALL_DARK);
  }
}

/** A room's front wall along its bottom edge, open at its doors. */
function frontWall(ctx: CanvasRenderingContext2D, room: Room, closed: boolean) {
  const y = room.y + room.h - 6;
  const gaps = room.doors.filter((d) => d.side === "S").map((d) => [d.x, d.x + d.width] as const);
  let x = room.x;
  for (const [a, b] of [...gaps, [room.x + room.w, room.x + room.w] as const]) {
    if (a > x) wallStrip(ctx, x, y, a - x, 6);
    if (b > a) {
      // The door frame, and the door itself when the room is closed.
      fill(ctx, a - 2, y - 2, 2, 8, GOLD);
      fill(ctx, b, y - 2, 2, 8, GOLD);
      if (closed) door(ctx, a, y - 2, b - a, 8);
    }
    x = Math.max(x, b);
  }
}

/** A closed door. */
function door(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number) {
  fill(ctx, x, y, w, h, "#5a3a22");
  fill(ctx, x, y, w, 1, "#7a5232");
  fill(ctx, x + w / 2 - 1, y, 2, h, "#3d2716");
  fill(ctx, x + w - 6, y + Math.floor(h / 2), 2, 2, GOLD_LIGHT);
}

/** The building's floor of corridors, and the outer wall around everything. */
function building(ctx: CanvasRenderingContext2D, world: World, corridor: Sprite) {
  const { w, h, outerWall } = world.layout;
  fill(ctx, 0, 0, w, h, WALL_DARK);
  for (const c of world.corridors)
    for (let y = c.y; y < c.y + c.h; y += 32)
      for (let x = c.x; x < c.x + c.w; x += 32)
        ctx.drawImage(corridor, 0, 0, Math.min(32, c.x + c.w - x), Math.min(32, c.y + c.h - y), x, y, Math.min(32, c.x + c.w - x), Math.min(32, c.y + c.h - y));
  // The outer wall, a gold line on its inside.
  wallStrip(ctx, 0, 0, w, outerWall);
  wallStrip(ctx, 0, h - outerWall, w, outerWall);
  wallStrip(ctx, 0, 0, outerWall, h);
  wallStrip(ctx, w - outerWall, 0, outerWall, h);
  fill(ctx, outerWall, outerWall - 1, w - 2 * outerWall, 1, GOLD);
  fill(ctx, outerWall - 1, outerWall, 1, h - 2 * outerWall, GOLD);
  fill(ctx, w - outerWall, outerWall, 1, h - 2 * outerWall, GOLD);
  fill(ctx, outerWall, h - outerWall, w - 2 * outerWall, 1, GOLD);
  // The walls between the rooms of a row.
  const rows = new Map<number, Room[]>();
  for (const room of [...world.rooms, ...world.vacant, world.cluster, world.lobby]) {
    const row = rows.get(room.y) ?? [];
    row.push(room);
    rows.set(room.y, row);
  }
  for (const row of rows.values()) {
    row.sort((a, b) => a.x - b.x);
    for (let i = 1; i < row.length; i += 1) {
      const a = row[i - 1];
      const b = row[i];
      const gap = b.x - (a.x + a.w);
      const top = Math.max(a.y, b.y);
      const bottom = Math.min(a.y + a.h, b.y + b.h);
      if (gap > 0) wallStrip(ctx, a.x + a.w, top, gap, bottom - top);
    }
  }
}

/** A product room: the kit's room, cut to the map's back wall, with its doors. */
function productRoom(ctx: CanvasRenderingContext2D, room: Room, art: Art, backWall: number) {
  const kit = art.rooms.product;
  const { x, y, w, h } = room;
  if (!kit) {
    fill(ctx, x, y, w, h, "#2a3350");
    fill(ctx, x, y, w, backWall, "#1f2740");
  } else {
    const cut = KIT.product.wall - backWall;
    const cols = Math.min(w, KIT.product.w);
    // Narrow rooms keep the kit's left side and get their own right wall.
    ctx.drawImage(kit, 0, cut, cols, KIT.product.h - cut, x, y, cols, KIT.product.h - cut);
    for (let fy = y + KIT.product.h - cut; fy < y + h; fy += KIT.floorStrip.h) {
      const rows = Math.min(KIT.floorStrip.h, y + h - fy);
      ctx.drawImage(kit, 0, KIT.floorStrip.y, cols, rows, x, fy, cols, rows);
    }
    if (w < KIT.product.w) wallStrip(ctx, x + w - 6, y, 6, h);
  }
  for (const d of room.doors.filter((d) => d.side === "N")) {
    const back = art.rooms.backdoor;
    if (back)
      ctx.drawImage(back, KIT.doorway.x, KIT.product.wall - backWall, KIT.doorway.w, backWall, d.x - 4, y, KIT.doorway.w, backWall);
    else fill(ctx, d.x, y, d.width, backWall, WALL_DARK);
    if (room.vacant) door(ctx, d.x, y + backWall - 40, d.width, 40);
  }
  frontWall(ctx, room, room.vacant);
}

function clusterRoom(ctx: CanvasRenderingContext2D, room: Room, art: Art, backWall: number) {
  const kit = art.rooms.cluster;
  const { x, y, w, h } = room;
  if (!kit) {
    fill(ctx, x, y, w, h, "#1d3550");
  } else {
    // The kit's room in the middle, its loading square on the map's loading spot.
    const off = Math.round((w - KIT.cluster.w) / 2);
    const rows = Math.min(h, KIT.cluster.h - KIT.cluster.top);
    ctx.drawImage(kit, 0, KIT.cluster.top, KIT.cluster.w, rows, x + off, y, KIT.cluster.w, rows);
    // Wider than the kit: another row of racks either side.
    ctx.drawImage(kit, 0, KIT.cluster.top, off, rows, x, y, off, rows);
    ctx.drawImage(kit, KIT.cluster.w - off, KIT.cluster.top, off, rows, x + w - off, y, off, rows);
  }
  for (const d of room.doors.filter((d) => d.side === "N")) {
    const back = art.rooms.backdoor;
    if (back) ctx.drawImage(back, KIT.doorway.x, KIT.product.wall - backWall, KIT.doorway.w, backWall, d.x - 4, y, KIT.doorway.w, backWall);
  }
  frontWall(ctx, room, false);
}

/** Warm planks, for the lobby floor the kit's drawing does not cover. */
function planks(ctx: CanvasRenderingContext2D, r: Rect) {
  const tones = ["#8a5a36", "#93623c", "#82532f"];
  for (let i = 0; i * 8 < r.h; i += 1) {
    const y = r.y + i * 8;
    const rows = Math.min(8, r.y + r.h - y);
    fill(ctx, r.x, y, r.w, rows, tones[i % 3]);
    fill(ctx, r.x, y + rows - 1, r.w, 1, "#6a4426");
    for (let jx = r.x + ((i * 29) % 48); jx < r.x + r.w; jx += 48) fill(ctx, jx, y, 1, rows - 1, "#6a4426");
  }
}

function lobbyRoom(ctx: CanvasRenderingContext2D, room: Room, art: Art, backWall: number, world: World) {
  const kit = art.rooms.lobby;
  const { x, y, w, h } = room;
  const { left, right, wall } = KIT.lobby;
  if (kit) {
    // The kit's lobby, its middle taken out to fit: the sofas, the kitchen, the reception.
    const top = wall - backWall;
    const rows = KIT.lobby.h - top - 6;
    ctx.drawImage(kit, 0, top, left, rows, x, y, left, rows);
    ctx.drawImage(kit, right, top, KIT.lobby.w - right, rows, x + left, y, KIT.lobby.w - right, rows);
    planks(ctx, { x: x + 6, y: y + rows, w: w - 12, h: h - rows });
  } else {
    fill(ctx, x, y, w, backWall, "#3a2a1c");
    planks(ctx, { x, y: y + backWall, w, h: h - backWall });
  }
  for (const d of room.doors.filter((d) => d.side === "N")) {
    const back = art.rooms.backdoor;
    if (back) ctx.drawImage(back, KIT.doorway.x, KIT.product.wall - backWall, KIT.doorway.w, backWall, d.x - 4, y, KIT.doorway.w, backWall);
  }
  // Side walls, and the main entrance in the building's outer wall.
  wallStrip(ctx, x, y, 6, h);
  wallStrip(ctx, x + w - 6, y, 6, h);
  const entrance = world.layout.slots.find((s) => s.kind === "lobby")?.entrance;
  if (entrance) {
    const ey = world.h - world.layout.outerWall;
    fill(ctx, entrance.x, ey, entrance.width, world.layout.outerWall, "#3d5a73");
    fill(ctx, entrance.x, ey, entrance.width, 2, "#9cc4e8");
    fill(ctx, entrance.x + entrance.width / 2 - 1, ey, 2, world.layout.outerWall, "#25384a");
    fill(ctx, entrance.x - 3, ey - 2, 3, world.layout.outerWall + 2, GOLD);
    fill(ctx, entrance.x + entrance.width, ey - 2, 3, world.layout.outerWall + 2, GOLD);
    // A doormat inside.
    fill(ctx, entrance.x + 4, ey - 10, entrance.width - 8, 8, "#2b3045");
    for (let i = entrance.x + 7; i < entrance.x + entrance.width - 7; i += 4) fill(ctx, i, ey - 8, 2, 4, "#3e4663");
  }
}

/** The whole office as one picture: the drawn map when there is one, else composed. */
/** The sprite a prop is drawn with: its drawing (or placeholder), sheeted if covered. */
export function propSprite(art: Art, prop: Prop): Sprite {
  const base = prop.kind === "box" ? art.box : (art.props.get(prop.kind) ?? art.box);
  return prop.covered ? sheeted(base) : base;
}

/**
 * What lies under everyone, painted with the map: rugs, and the things in a
 * room nobody uses (dimmed with it). Back to front.
 */
function stillProps(ctx: CanvasRenderingContext2D, world: World, art: Art) {
  const still = world.props.filter((p) => p.still).sort((a, b) => Number(b.kind === "rug") - Number(a.kind === "rug") || a.y + a.h - (b.y + b.h));
  for (const prop of still) ctx.drawImage(propSprite(art, prop), prop.x, prop.y);
}

export function paintMap(world: World, art: Art): HTMLCanvasElement {
  const c = document.createElement("canvas");
  c.width = world.w;
  c.height = world.h;
  const ctx = c.getContext("2d");
  if (!ctx) return c;
  ctx.imageSmoothingEnabled = false;
  if (art.map) {
    ctx.drawImage(art.map, 0, 0);
    stillProps(ctx, world, art);
    // The drawn map shows every room open; an unused one is dimmed, its door shut.
    const { backWall } = world.layout;
    for (const room of world.vacant) {
      ctx.fillStyle = "rgba(6,9,20,0.45)";
      ctx.fillRect(room.x, room.y, room.w, room.h);
      for (const d of room.doors)
        if (d.side === "N") door(ctx, d.x, room.y, d.width, backWall);
        else door(ctx, d.x, room.y + room.h - 6, d.width, 8);
    }
    return c;
  }
  const { backWall } = world.layout;
  building(ctx, world, art.corridor);
  for (const room of [...world.rooms, ...world.vacant]) productRoom(ctx, room, art, backWall);
  clusterRoom(ctx, world.cluster, art, backWall);
  lobbyRoom(ctx, world.lobby, art, backWall, world);
  stillProps(ctx, world, art);
  for (const room of world.vacant) {
    ctx.fillStyle = "rgba(6,9,20,0.45)";
    ctx.fillRect(room.x, room.y, room.w, room.h - 6);
  }
  return c;
}
