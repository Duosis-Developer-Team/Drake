/**
 * The office map's geometry: one connected building (768 × 480 art pixels as shipped).
 *
 * An outer wall; three product rooms along the top; a product room either
 * side of the cluster's server room in the middle; a product room either side
 * of the lobby at the bottom, its main entrance in the outer wall; two
 * full-width corridors between the rows. Every room has a back wall with a
 * screen on it, doors, and free floor where desks go.
 *
 * The map's own `office-map.json` (same shape) is what the office uses; the
 * default here is the shipped one, for when that file cannot be read.
 */

export interface MapRect {
  x: number;
  y: number;
  w: number;
  h: number;
}
export interface MapDoor {
  /** Which wall: N is the back wall (onto the corridor behind), S the front. */
  side: "N" | "S";
  x: number;
  width: number;
}
export interface MapSlot {
  id: string;
  kind: "product" | "cluster" | "lobby";
  rect: MapRect;
  screen: MapRect;
  doors: MapDoor[];
  free: MapRect | null;
  /** The cluster's loading spot. */
  loading?: { x: number; y: number };
  /** The lobby's main entrance, in the building's outer wall. */
  entrance?: { x: number; width: number };
  /** Furniture drawn on the map that nobody walks through. */
  blocked: MapRect[];
}
export interface OfficeLayout {
  w: number;
  h: number;
  backWall: number;
  outerWall: number;
  slots: MapSlot[];
  corridors: MapRect[];
}

/** The map's file format: rooms as [x1, y1, x2, y2], screens and floors as [x, y, w, h]. */
interface LayoutFile {
  size?: [number, number];
  back_wall?: number;
  outer_wall?: number;
  rooms?: Record<
    string,
    {
      rect?: number[];
      kind?: string;
      screen?: number[];
      doors?: { side?: string; x?: number; width?: number }[];
      free_floor?: number[];
      loading?: number[];
      entrance?: number[];
      /** [x, y, w, h] each. */
      blocked?: number[][];
    }
  >;
}

/** The shipped map's geometry (768 × 480): what `office-map.json` says when there is none. */
export const DEFAULT_LAYOUT_FILE: LayoutFile = {
  size: [768, 480],
  back_wall: 40,
  outer_wall: 8,
  rooms: {
    "room-1": { rect: [8, 8, 248, 160], kind: "product", screen: [100, 14, 56, 24], doors: [{ side: "S", x: 112, width: 32 }], free_floor: [20, 66, 216, 86] },
    "room-2": { rect: [264, 8, 504, 160], kind: "product", screen: [356, 14, 56, 24], doors: [{ side: "S", x: 368, width: 32 }], free_floor: [276, 66, 216, 86] },
    "room-3": { rect: [520, 8, 760, 160], kind: "product", screen: [612, 14, 56, 24], doors: [{ side: "S", x: 624, width: 32 }], free_floor: [532, 66, 216, 86] },
    "room-4": { rect: [8, 192, 200, 320], kind: "product", screen: [60, 198, 56, 24], doors: [{ side: "N", x: 144, width: 32 }], free_floor: [20, 250, 168, 62] },
    cluster: { rect: [216, 192, 552, 320], kind: "cluster", screen: [320, 198, 96, 28], doors: [{ side: "N", x: 496, width: 32 }, { side: "S", x: 368, width: 32 }], free_floor: [228, 250, 312, 62], loading: [384, 288] },
    "room-5": { rect: [568, 192, 760, 320], kind: "product", screen: [620, 198, 56, 24], doors: [{ side: "N", x: 704, width: 32 }], free_floor: [580, 250, 168, 62] },
    "room-6": { rect: [8, 352, 248, 472], kind: "product", screen: [84, 358, 56, 24], doors: [{ side: "N", x: 192, width: 32 }], free_floor: [20, 410, 216, 54] },
    lobby: { rect: [264, 352, 504, 472], kind: "lobby", screen: [320, 358, 96, 28], doors: [{ side: "N", x: 448, width: 32 }], free_floor: [276, 410, 216, 54], entrance: [364, 40], blocked: [[270, 386, 38, 22], [328, 390, 50, 18], [264, 420, 13, 30], [492, 420, 13, 30], [266, 398, 10, 16], [266, 449, 10, 17], [492, 396, 10, 15], [492, 449, 10, 17]] },
    "room-7": { rect: [520, 352, 760, 472], kind: "product", screen: [596, 358, 56, 24], doors: [{ side: "N", x: 704, width: 32 }], free_floor: [532, 410, 216, 54] },
  },
};

const num = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const quad = (v: unknown): v is number[] => Array.isArray(v) && v.length === 4 && v.every(num);

/** The layout from a map file; null when anything in it does not hold together. */
export function parseLayout(file: unknown): OfficeLayout | null {
  if (!file || typeof file !== "object") return null;
  const f = file as LayoutFile;
  const [w, h] = Array.isArray(f.size) && f.size.length === 2 && f.size.every(num) ? f.size : [0, 0];
  if (w < 256 || h < 256 || w > 4096 || h > 4096) return null;
  const slots: MapSlot[] = [];
  for (const [id, room] of Object.entries(f.rooms ?? {})) {
    if (!room || !quad(room.rect) || !quad(room.screen)) return null;
    const kind = room.kind === "cluster" || room.kind === "lobby" ? room.kind : room.kind === "product" ? "product" : null;
    if (!kind) return null;
    const [x1, y1, x2, y2] = room.rect;
    if (!(x2 > x1 && y2 > y1 && x1 >= 0 && y1 >= 0 && x2 <= w && y2 <= h)) return null;
    const [sx, sy, sw, sh] = room.screen;
    const doors: MapDoor[] = [];
    for (const d of room.doors ?? []) {
      if ((d.side !== "N" && d.side !== "S") || !num(d.x) || !num(d.width) || d.width < 24) return null;
      doors.push({ side: d.side, x: d.x, width: d.width });
    }
    const free = quad(room.free_floor)
      ? { x: room.free_floor[0], y: room.free_floor[1], w: room.free_floor[2], h: room.free_floor[3] }
      : null;
    slots.push({
      id,
      kind,
      rect: { x: x1, y: y1, w: x2 - x1, h: y2 - y1 },
      screen: { x: sx, y: sy, w: sw, h: sh },
      doors,
      free,
      loading: Array.isArray(room.loading) && room.loading.length === 2 && room.loading.every(num) ? { x: room.loading[0], y: room.loading[1] } : undefined,
      entrance:
        Array.isArray(room.entrance) && room.entrance.length === 2 && room.entrance.every(num)
          ? { x: room.entrance[0], width: room.entrance[1] }
          : undefined,
      blocked: (Array.isArray(room.blocked) ? room.blocked : []).filter(quad).map(([bx, by, bw, bh]) => ({ x: bx, y: by, w: bw, h: bh })),
    });
  }
  const cluster = slots.find((s) => s.kind === "cluster");
  const lobby = slots.find((s) => s.kind === "lobby");
  if (!cluster || !lobby || !cluster.loading || slots.filter((s) => s.kind === "product").length < 1) return null;
  const outer = num(f.outer_wall) ? f.outer_wall : 16;
  // The corridors are what lies between the rows of rooms, wall to wall.
  const rows = [...new Set(slots.map((s) => s.rect.y))].sort((a, b) => a - b);
  const corridors: MapRect[] = [];
  for (let i = 1; i < rows.length; i += 1) {
    const above = Math.max(...slots.filter((s) => s.rect.y === rows[i - 1]).map((s) => s.rect.y + s.rect.h));
    if (rows[i] > above) corridors.push({ x: outer, y: above, w: w - 2 * outer, h: rows[i] - above });
  }
  return { w, h, backWall: num(f.back_wall) ? f.back_wall : 48, outerWall: outer, slots, corridors };
}

export const DEFAULT_LAYOUT = parseLayout(DEFAULT_LAYOUT_FILE) as OfficeLayout;

/**
 * The order product rooms are given out in, busiest repository first: the
 * large rooms (six desks) before the two narrow ones beside the cluster —
 * the top row from the middle out, then beside the lobby.
 */
export const SLOT_ORDER = ["room-2", "room-1", "room-3", "room-6", "room-7", "room-4", "room-5"];
