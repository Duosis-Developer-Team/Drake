/**
 * The office map's geometry: one connected building, 1024 × 768 art pixels.
 *
 * An outer wall; three product rooms along the top; a product room either
 * side of the cluster's server room in the middle; a product room either side
 * of the lobby at the bottom, its main entrance in the outer wall; two
 * full-width corridors between the rows. Every room has a 48-pixel back wall
 * with a screen on it, doors, and free floor where desks go.
 *
 * The art's own `office-map.json` (same shape) replaces this when the map is
 * drawn; this default is the guide the map is drawn to.
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

export const DEFAULT_LAYOUT_FILE: LayoutFile = {
  size: [1024, 768],
  back_wall: 48,
  outer_wall: 16,
  rooms: {
    "room-1": { rect: [16, 16, 336, 224], kind: "product", screen: [140, 22, 72, 32], doors: [{ side: "S", x: 160, width: 32 }], free_floor: [40, 96, 272, 112] },
    "room-2": { rect: [352, 16, 672, 224], kind: "product", screen: [476, 22, 72, 32], doors: [{ side: "S", x: 496, width: 32 }], free_floor: [376, 96, 272, 112] },
    "room-3": { rect: [688, 16, 1008, 224], kind: "product", screen: [812, 22, 72, 32], doors: [{ side: "S", x: 832, width: 32 }], free_floor: [712, 96, 272, 112] },
    "room-4": { rect: [16, 272, 272, 464], kind: "product", screen: [108, 278, 72, 32], doors: [{ side: "N", x: 200, width: 32 }], free_floor: [40, 352, 208, 96] },
    cluster: {
      rect: [288, 272, 736, 464],
      kind: "cluster",
      screen: [448, 278, 128, 40],
      doors: [
        { side: "N", x: 664, width: 32 },
        { side: "S", x: 496, width: 32 },
      ],
      loading: [512, 416],
    },
    "room-5": { rect: [752, 272, 1008, 464], kind: "product", screen: [844, 278, 72, 32], doors: [{ side: "N", x: 936, width: 32 }], free_floor: [776, 352, 208, 96] },
    "room-6": { rect: [16, 512, 336, 752], kind: "product", screen: [140, 518, 72, 32], doors: [{ side: "N", x: 264, width: 32 }], free_floor: [40, 592, 272, 144] },
    lobby: {
      rect: [352, 512, 672, 752],
      kind: "lobby",
      screen: [448, 518, 128, 40],
      doors: [{ side: "N", x: 600, width: 32 }],
      entrance: [488, 48],
      free_floor: [392, 602, 240, 80],
      // The drawn lobby's reception desk, sofas, coffee bar and plants.
      blocked: [
        [398, 696, 80, 36],
        [356, 596, 34, 66],
        [634, 596, 34, 66],
        [538, 562, 54, 36],
        [362, 668, 26, 34],
        [638, 674, 28, 34],
      ],
    },
    "room-7": { rect: [688, 512, 1008, 752], kind: "product", screen: [812, 518, 72, 32], doors: [{ side: "N", x: 936, width: 32 }], free_floor: [712, 592, 272, 144] },
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
