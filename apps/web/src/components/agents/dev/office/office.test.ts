import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import {
  advance,
  between,
  DAY_MS,
  isoWithOffset,
  liveIntents,
  localMidnight,
  parseEvents,
  REPLAY_SECONDS,
  replayIntents,
  WORK_AFTER_MS,
  WORK_BEFORE_MS,
} from "./director";
import { footing, overlaps, reachable } from "./furnish";
import { DEFAULT_LAYOUT, DEFAULT_LAYOUT_FILE, parseLayout, SLOT_ORDER } from "./layout";
import {
  byActivity,
  CELL,
  findPath,
  layoutOffice,
  nearestWalkable,
  nextRoom,
  OTHER,
  reachTargets,
  roomAt,
  roomOrder,
  SPACING,
  walkable,
  type World,
} from "./world";

const repos = (n: number, desks = ["a", "b"]) =>
  Array.from({ length: n }, (_, i) => ({ id: `gh:${i}`, desks, catalogued: true, commits: 100 - i }));

function legal(world: World, path: { x: number; y: number }[]) {
  // Every step stays on walkable ground (sampled finely between turns).
  for (let i = 1; i < path.length - 1; i += 1) {
    const a = path[i - 1];
    const b = path[i];
    const steps = Math.ceil(Math.hypot(b.x - a.x, b.y - a.y) / 2);
    for (let s = 0; s <= steps; s += 1) {
      const p = { x: a.x + ((b.x - a.x) * s) / steps, y: a.y + ((b.y - a.y) * s) / steps };
      if (!walkable(world, p)) return false;
    }
  }
  return true;
}

describe("the map's layout", () => {
  it("ships the compact map: nine rooms, the cluster in the middle, two corridors", () => {
    expect([DEFAULT_LAYOUT.w, DEFAULT_LAYOUT.h, DEFAULT_LAYOUT.backWall, DEFAULT_LAYOUT.outerWall]).toEqual([768, 480, 40, 8]);
    expect(DEFAULT_LAYOUT.slots.filter((s) => s.kind === "product")).toHaveLength(7);
    const cluster = DEFAULT_LAYOUT.slots.find((s) => s.kind === "cluster");
    expect(cluster?.loading).toEqual({ x: 384, y: 288 });
    expect(DEFAULT_LAYOUT.corridors.map((c) => [c.y, c.h])).toEqual([
      [160, 32],
      [320, 32],
    ]);
    expect(DEFAULT_LAYOUT.slots.find((s) => s.kind === "lobby")?.blocked.length).toBeGreaterThan(3);
    expect(SLOT_ORDER.every((id) => DEFAULT_LAYOUT.slots.some((s) => s.id === id))).toBe(true);
  });

  it("matches the map file it ships with", () => {
    const shipped = parseLayout(JSON.parse(readFileSync(resolve(__dirname, "../../../../../public/office/office-map.json"), "utf8")));
    expect(shipped).toEqual(DEFAULT_LAYOUT);
  });

  it("refuses a map file that does not hold together", () => {
    expect(parseLayout(null)).toBeNull();
    expect(parseLayout({ size: [1024, 768], rooms: {} })).toBeNull();
    const broken = structuredClone(DEFAULT_LAYOUT_FILE);
    (broken.rooms as Record<string, { rect?: number[] }>)["room-1"].rect = [400, 16, 300, 224];
    expect(parseLayout(broken)).toBeNull();
  });
});

describe("rooms for repositories", () => {
  it("gives the catalog's and the busiest repositories the large rooms first", () => {
    const world = layoutOffice([
      { id: "gh:quiet", desks: ["a"], catalogued: true, commits: 1 },
      { id: "gh:outside", desks: ["b"], catalogued: false, commits: 500 },
      { id: "gh:busy", desks: ["c"], catalogued: true, commits: 90 },
    ]);
    const slotOf = (id: string) => world.rooms.find((r) => r.id === id)?.slot;
    expect(slotOf("gh:busy")).toBe("room-2");
    expect(slotOf("gh:quiet")).toBe("room-1");
    expect(slotOf("gh:outside")).toBe("room-3");
    // The compact map's large rooms seat three in a row; anyone more stands.
    const many = Array.from({ length: 9 }, (_, i) => `p${i}`);
    const [big] = layoutOffice([{ id: "gh:x", desks: many, catalogued: true, commits: 1 }]).rooms;
    expect(big.desks).toHaveLength(3);
    expect(big.standing.length).toBeGreaterThanOrEqual(4);
    expect(byActivity(repos(3)).map((r) => r.id)).toEqual(["gh:0", "gh:1", "gh:2"]);
  });

  it("closes the rooms nobody uses", () => {
    const world = layoutOffice(repos(2));
    expect(world.vacant).toHaveLength(5);
    expect(world.vacant.every((r) => r.vacant && r.desks.length === 0)).toBe(true);
    // A closed room cannot be walked into.
    for (const room of world.vacant)
      expect(walkable(world, { x: room.x + room.w / 2, y: room.y + room.h - 40 })).toBe(false);
  });

  it("puts the least active repositories in one shared room when there are more than rooms", () => {
    const world = layoutOffice(repos(10, ["p"]));
    expect(world.rooms).toHaveLength(7);
    const other = world.rooms.find((r) => r.id === OTHER);
    expect(other?.members).toEqual(["gh:6", "gh:7", "gh:8", "gh:9"]);
    expect(world.roomOf["gh:9"]).toBe(OTHER);
    expect(world.roomOf["gh:0"]).toBe("gh:0");
  });

  it("spaces the desks on the free floor, every seat free to sit in", () => {
    const world = layoutOffice([{ id: "gh:1", desks: ["a", "b", "c", "d", "e", "f", "g"], catalogued: true, commits: 9 }]);
    const [room] = world.rooms;
    expect(room.desks.length).toBeGreaterThanOrEqual(2);
    for (const desk of room.desks) {
      expect(walkable(world, desk.seat)).toBe(true);
      expect(desk.x).toBeGreaterThanOrEqual(room.x);
      expect(desk.x + 64).toBeLessThanOrEqual(room.x + room.w);
    }
    for (let i = 0; i < room.desks.length; i += 1)
      for (let j = i + 1; j < room.desks.length; j += 1) {
        const a = room.desks[i];
        const b = room.desks[j];
        expect(Math.abs(a.x - b.x) >= 64 || Math.abs(a.y - b.y) >= 48).toBe(true);
      }
  });

  it("keeps the lobby's standing spots a character's width and more apart", () => {
    const world = layoutOffice(repos(3));
    expect(world.lobbySpots.length).toBeGreaterThanOrEqual(5);
    for (const a of world.lobbySpots) {
      expect(walkable(world, a)).toBe(true);
      for (const b of world.lobbySpots) if (a !== b) expect(Math.hypot(a.x - b.x, a.y - b.y)).toBeGreaterThanOrEqual(SPACING);
    }
  });
});

describe("the larger guide (1024 × 768)", () => {
  const file = JSON.parse(readFileSync(resolve(__dirname, "../../../../../../../docs/runbooks/office/office-map-layout.json"), "utf8"));
  const layout = parseLayout(file);

  it("reads it whole: the same nine rooms, larger, and seats six in a large room", () => {
    expect(layout).not.toBeNull();
    if (!layout) return;
    expect([layout.w, layout.h, layout.backWall, layout.outerWall]).toEqual([1024, 768, 48, 16]);
    expect(layout.slots).toHaveLength(9);
    expect(layout.corridors.map((c) => c.h)).toEqual([48, 48]);
    const many = Array.from({ length: 9 }, (_, i) => `p${i}`);
    expect(layoutOffice([{ id: "gh:x", desks: many, catalogued: true, commits: 1 }], layout).rooms[0].desks).toHaveLength(6);
  });

  it("seats people, keeps the lobby spaced, and walks every desk from the lobby", () => {
    if (!layout) return;
    const world = layoutOffice(repos(7), layout);
    expect(world.lobbySpots.length).toBeGreaterThanOrEqual(6);
    for (const room of world.rooms) {
      expect(room.desks.length, room.slot).toBeGreaterThanOrEqual(2);
      for (const desk of room.desks) {
        const path = findPath(world, world.lobbySpots[0], desk.seat);
        expect(path.length, room.slot).toBeGreaterThan(0);
        expect(legal(world, [world.lobbySpots[0], ...path])).toBe(true);
      }
    }
    expect(roomAt(world, world.drop)?.kind).toBe("cluster");
    expect(findPath(world, world.rooms[0].desks[0].seat, world.drop).length).toBeGreaterThan(0);
  });
});

describe("walking", () => {
  const world = layoutOffice(repos(7));

  it("walks from the lobby to every desk and to the cluster's loading spot, through doors", () => {
    const from = world.lobbySpots[0];
    for (const room of world.rooms)
      for (const desk of room.desks) {
        const path = findPath(world, from, desk.seat);
        expect(path.length, room.id).toBeGreaterThan(0);
        expect(path[path.length - 1]).toEqual(desk.seat);
        expect(legal(world, [from, ...path])).toBe(true);
      }
    const drop = findPath(world, world.rooms[0].desks[0].seat, world.drop);
    expect(drop.length).toBeGreaterThan(0);
    expect(roomAt(world, drop[drop.length - 1])?.kind).toBe("cluster");
  });

  it("goes out of a room by its door and along a corridor, never through a wall", () => {
    // room-4 (left of the cluster) to room-5 (right of it): out the back door, along the top corridor.
    const left = world.rooms.find((r) => r.slot === "room-4");
    const right = world.rooms.find((r) => r.slot === "room-5");
    if (!left || !right) throw new Error("rooms");
    const path = findPath(world, left.desks[0].seat, right.desks[0].seat);
    expect(path.some((p) => p.y >= 224 && p.y < 272)).toBe(true);
    expect(legal(world, [left.desks[0].seat, ...path])).toBe(true);
  });

  it("can come in by the main entrance", () => {
    expect(walkable(world, world.entrance)).toBe(true);
    expect(findPath(world, world.entrance, world.lobbySpots[0]).length).toBeGreaterThan(0);
  });

  it("starts from the nearest free cell when standing somewhere odd", () => {
    const inWall = { x: 4, y: 4 };
    const near = nearestWalkable(world, inWall);
    expect(walkable(world, near)).toBe(true);
    expect(Math.hypot(near.x - inWall.x, near.y - inWall.y)).toBeLessThan(CELL * 40);
  });
});

describe("the director", () => {
  const day = Date.parse("2026-11-23T00:00:00Z");
  const events = parseEvents([
    { at: "2026-11-23T10:00:00Z", kind: "commit", person: "gencay", repository: "gh:1" },
    { at: "2026-11-23T10:20:00Z", kind: "commit", person: "gencay", repository: "gh:2" },
    { at: "2026-11-23T15:00:00Z", kind: "deployment", person: "gencay", repository: "gh:2" },
    { at: "2026-11-23T09:00:00Z", kind: "commit", person: "enes", repository: "gh:1" },
  ]);

  it("sorts the day and keeps an author at work around their commit", () => {
    expect(events.map((e) => e.person)).toEqual(["enes", "gencay", "gencay", "gencay"]);
    const at = (h: number, m = 0) => day + h * 3_600_000 + m * 60_000;
    expect(replayIntents(events, at(9, 0)).has("gencay")).toBe(false);
    expect(replayIntents(events, at(9, 15)).get("gencay")).toEqual({ kind: "work", room: "gh:1" });
    // The later commit wins once its work has begun.
    expect(replayIntents(events, at(10, 5)).get("gencay")).toEqual({ kind: "work", room: "gh:2" });
    expect(replayIntents(events, at(10, 20) + WORK_AFTER_MS + 1).has("gencay")).toBe(false);
    expect(replayIntents(events, at(9) - WORK_BEFORE_MS - 1).size).toBe(0);
  });

  it("hands out what happened between two moments, once", () => {
    const at = (h: number) => day + h * 3_600_000;
    expect(between(events, at(14), at(16)).map((e) => e.kind)).toEqual(["deployment"]);
    expect(between(events, at(15), at(15))).toEqual([]);
    expect(between(events, at(0), at(24))).toHaveLength(4);
  });

  it("plays a day in REPLAY_SECONDS at 1×, faster at higher speeds", () => {
    expect(advance(0, REPLAY_SECONDS, 1)).toBeCloseTo(DAY_MS);
    expect(advance(0, REPLAY_SECONDS / 4, 4)).toBeCloseTo(DAY_MS);
  });

  it("puts live presence at its repository, one place a person", () => {
    const live = liveIntents([
      { person: "gencay", repository: "gh:1" },
      { person: "enes", repository: "gh:2" },
    ]);
    expect(live.get("enes")).toEqual({ kind: "work", room: "gh:2" });
  });

  it("asks for the viewer's own midnight, with its offset", () => {
    const midnight = localMidnight("2026-11-23");
    expect(midnight.getHours()).toBe(0);
    expect(isoWithOffset(midnight)).toMatch(/^2026-11-23T00:00:00[+-]\d\d:\d\d$/);
  });
});

describe("furniture", () => {
  const counts = [1, 2, 3, 7, 10];

  it("furnishes the same rooms the same way every time", () => {
    expect(layoutOffice(repos(3)).props).toEqual(layoutOffice(repos(3)).props);
  });

  it("gives a room in use a rug under its desks, a lounge or storage, and small things", () => {
    const world = layoutOffice(repos(7));
    for (const room of world.rooms) {
      const mine = world.props.filter((p) => p.slot === room.slot);
      const kinds = new Set(mine.map((p) => p.kind));
      expect(kinds.has("rug"), room.slot).toBe(true);
      expect(mine.filter((p) => p.kind !== "rug").length, room.slot).toBeGreaterThanOrEqual(2);
    }
    // At least one large room has its corner lounge.
    expect(world.props.some((p) => p.kind === "sofa-small" && !p.covered)).toBe(true);
    // The cluster has its spare racks, the lobby its rug.
    expect(world.props.filter((p) => p.slot === "cluster" && p.kind === "server-rack")).toHaveLength(2);
    expect(world.props.some((p) => p.slot === "lobby" && p.kind === "rug")).toBe(true);
  });

  it("leaves an empty room boxes and sheeted furniture, painted with the map", () => {
    const world = layoutOffice(repos(2));
    for (const room of world.vacant) {
      const mine = world.props.filter((p) => p.slot === room.slot);
      expect(mine.some((p) => p.kind === "box"), room.slot).toBe(true);
      expect(mine.every((p) => p.still)).toBe(true);
    }
    expect(world.props.some((p) => p.covered)).toBe(true);
  });

  it("never covers a desk, a chair, a screen, a door or the map's own furniture", () => {
    for (const n of counts) {
      const world = layoutOffice(repos(n, ["a", "b", "c"]));
      const all = [...world.rooms, ...world.vacant, world.cluster, world.lobby];
      for (const p of world.props.filter((q) => q.kind !== "rug")) {
        const room = all.find((r) => r.slot === p.slot);
        if (!room) throw new Error(p.slot);
        expect(overlaps(p, room.screen), `${p.kind} on ${p.slot}'s screen`).toBe(false);
        for (const d of room.desks) {
          expect(overlaps(p, { x: d.x, y: d.y, w: 64, h: 40 }), `${p.kind} on a desk`).toBe(false);
          expect(overlaps(p, { x: d.x + 16, y: d.y - 14, w: 32, h: 32 }), `${p.kind} on a chair`).toBe(false);
        }
        const slot = world.layout.slots.find((s) => s.id === p.slot);
        for (const b of slot?.blocked ?? []) expect(overlaps(p, b), `${p.kind} on the lobby's furniture`).toBe(false);
        // Inside its own room.
        expect(p.x >= room.x && p.x + p.w <= room.x + room.w && p.y >= room.y && p.y + p.h <= room.y + room.h, p.kind).toBe(true);
      }
    }
  });

  it("blocks where furniture stands and keeps every seat, spot, door and the loading square reachable", () => {
    for (const n of counts) {
      const world = layoutOffice(repos(n, ["a", "b", "c"]));
      for (const p of world.props) {
        const foot = footing(p);
        if (!foot || p.still) continue;
        expect(walkable(world, { x: foot.x + foot.w / 2, y: foot.y + foot.h / 2 }), `${p.kind} in ${p.slot}`).toBe(false);
      }
      expect(reachable(world, world.entrance, reachTargets(world)), `${n} repositories`).toBe(true);
      // And walked to, by the same paths people take.
      for (const room of world.rooms)
        for (const desk of room.desks) expect(findPath(world, world.entrance, desk.seat).length, room.slot).toBeGreaterThan(0);
      expect(findPath(world, world.entrance, world.drop).length).toBeGreaterThan(0);
    }
  });
});

describe("the lobby", () => {
  it("never has anyone standing in the reception, the coffee bar or on a sofa", () => {
    const world = layoutOffice(repos(3));
    const blocked = world.layout.slots.find((s) => s.kind === "lobby")?.blocked ?? [];
    expect(world.lobbySpots.length).toBeGreaterThanOrEqual(5);
    for (const spot of world.lobbySpots) {
      const body = { x: spot.x - 14, y: spot.y - 44, w: 28, h: 46 };
      for (const b of blocked) expect(overlaps(body, b), `${spot.x},${spot.y}`).toBe(false);
    }
  });
});

describe("stepping through the rooms", () => {
  it("reads row by row, left to right, the cluster and the lobby included, empty rooms skipped", () => {
    const world = layoutOffice(repos(4));
    const order = roomOrder(world).map((r) => r.slot);
    // Four in use: room-2, room-1, room-3, room-6 (by SLOT_ORDER); room-4, 5, 7 stand empty.
    expect(order).toEqual(["room-1", "room-2", "room-3", "cluster", "room-6", "lobby"]);
    const id = (slot: string) => roomOrder(world).find((r) => r.slot === slot)?.id ?? "";
    expect(nextRoom(world, id("room-3"), 1).slot).toBe("cluster");
    expect(nextRoom(world, id("cluster"), -1).slot).toBe("room-3");
    // Around the ends.
    expect(nextRoom(world, "lobby", 1).slot).toBe("room-1");
    expect(nextRoom(world, id("room-1"), -1).slot).toBe("lobby");
  });
});
