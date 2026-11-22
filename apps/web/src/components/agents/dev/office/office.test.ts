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
  nightAt,
  parseEvents,
  REPLAY_SECONDS,
  replayIntents,
  WORK_AFTER_MS,
  WORK_BEFORE_MS,
} from "./director";
import { DEFAULT_LAYOUT, DEFAULT_LAYOUT_FILE, parseLayout, SLOT_ORDER } from "./layout";
import { byActivity, CELL, findPath, layoutOffice, nearestWalkable, OTHER, roomAt, SPACING, walkable, type World } from "./world";

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
  it("reads the guide: nine rooms, the cluster in the middle, two corridors", () => {
    expect(DEFAULT_LAYOUT.w).toBe(1024);
    expect(DEFAULT_LAYOUT.h).toBe(768);
    expect(DEFAULT_LAYOUT.slots.filter((s) => s.kind === "product")).toHaveLength(7);
    const cluster = DEFAULT_LAYOUT.slots.find((s) => s.kind === "cluster");
    expect(cluster?.rect.x).toBeGreaterThan(256);
    expect(cluster?.loading).toEqual({ x: 512, y: 416 });
    expect(DEFAULT_LAYOUT.corridors.map((c) => [c.y, c.h])).toEqual([
      [224, 48],
      [464, 48],
    ]);
    expect(SLOT_ORDER.every((id) => DEFAULT_LAYOUT.slots.some((s) => s.id === id))).toBe(true);
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
    // The large rooms seat six; the narrow ones beside the cluster fewer.
    const many = Array.from({ length: 9 }, (_, i) => `p${i}`);
    expect(layoutOffice([{ id: "gh:x", desks: many, catalogued: true, commits: 1 }]).rooms[0].desks).toHaveLength(6);
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
    expect(world.lobbySpots.length).toBeGreaterThanOrEqual(8);
    for (const a of world.lobbySpots) {
      expect(walkable(world, a)).toBe(true);
      for (const b of world.lobbySpots) if (a !== b) expect(Math.hypot(a.x - b.x, a.y - b.y)).toBeGreaterThanOrEqual(SPACING);
    }
  });
});

describe("the compact map (768 × 480)", () => {
  const file = JSON.parse(readFileSync(resolve(__dirname, "../../../../../../../docs/runbooks/office/office-map-layout-768x480.json"), "utf8"));
  const layout = parseLayout(file);

  it("reads it whole: the same nine rooms, smaller", () => {
    expect(layout).not.toBeNull();
    if (!layout) return;
    expect([layout.w, layout.h, layout.backWall, layout.outerWall]).toEqual([768, 480, 40, 8]);
    expect(layout.slots).toHaveLength(9);
    expect(layout.corridors.map((c) => c.h)).toEqual([32, 32]);
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

  it("dims the office at night only", () => {
    expect(nightAt(12)).toBe(0);
    expect(nightAt(2)).toBe(0.5);
    expect(nightAt(19.5)).toBeGreaterThan(0);
    expect(nightAt(19.5)).toBeLessThan(0.5);
  });
});
