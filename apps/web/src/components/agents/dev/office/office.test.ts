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
import { CELL, findPath, layoutOffice, nearestWalkable, roomAt, SIZE, walkable, WORLD_W, type World } from "./world";

const rooms = (n: number, desks = ["a", "b"]) =>
  Array.from({ length: n }, (_, i) => ({ id: `gh:${i}`, desks }));

function overlaps(a: { x: number; y: number; w: number; h: number }, b: { x: number; y: number; w: number; h: number }) {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
}

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

describe("the office's floor plan", () => {
  it("puts the cluster in the middle, the lobby at the bottom and two rooms beside the cluster first", () => {
    const world = layoutOffice(rooms(2));
    expect(world.w).toBe(WORLD_W);
    expect(world.rooms.map((r) => r.y)).toEqual([world.cluster.y, world.cluster.y]);
    expect(world.rooms[0].x).toBeLessThan(world.cluster.x);
    expect(world.rooms[1].x).toBeGreaterThan(world.cluster.x + world.cluster.w);
    expect(world.lobby.y).toBeGreaterThan(world.cluster.y + world.cluster.h);
    expect(world.cluster.x + world.cluster.w / 2).toBe(WORLD_W / 2);
    // With nothing above, the side rooms have no back door to lead nowhere.
    expect(world.rooms.every((r) => !r.backdoor)).toBe(true);
  });

  it("stacks further rooms above, three a row, and gives the middle row back doors", () => {
    const world = layoutOffice(rooms(9));
    expect(world.rooms).toHaveLength(9);
    const rows = new Set(world.rooms.map((r) => r.y));
    expect(rows.size).toBe(4); // two upper rows of three, one of one, the middle row
    expect(world.rooms.filter((r) => r.y === world.cluster.y).every((r) => r.backdoor)).toBe(true);
    for (const a of [...world.rooms, world.cluster, world.lobby])
      for (const b of [...world.rooms, world.cluster, world.lobby])
        if (a !== b) expect(overlaps(a, b)).toBe(false);
  });

  it("puts the third and fourth rooms beside the lobby, fronts in line", () => {
    const world = layoutOffice(rooms(4));
    const beside = world.rooms.slice(2);
    expect(beside.every((r) => r.y + r.h === world.lobby.y + world.lobby.h)).toBe(true);
    expect(beside[0].x + beside[0].w).toBeLessThan(world.lobby.x);
    expect(beside[1].x).toBeGreaterThan(world.lobby.x + world.lobby.w);
    // Their back doors open onto the corridor above.
    expect(beside.every((r) => r.backdoor)).toBe(true);
  });

  it("leaves an empty office where no repository fills a slot", () => {
    expect(layoutOffice(rooms(1)).vacant).toHaveLength(3);
    expect(layoutOffice([]).vacant).toHaveLength(4);
    expect(layoutOffice(rooms(6)).vacant).toHaveLength(0);
  });

  it("gives each person a desk, up to six a room, and their seat is free to sit in", () => {
    const world = layoutOffice([{ id: "gh:1", desks: ["a", "b", "c", "d", "e", "f", "g"] }]);
    const [room] = world.rooms;
    expect(room.desks.map((d) => d.owner)).toEqual(["a", "b", "c", "d", "e", "f"]);
    for (const desk of room.desks) {
      expect(walkable(world, desk.seat)).toBe(true);
      expect(desk.x + SIZE.desk.w).toBeLessThanOrEqual(room.x + room.w);
    }
  });
});

describe("walking", () => {
  const world = layoutOffice(rooms(5));

  it("finds a way from the lobby to every desk and to the cluster's loading square", () => {
    const from = world.lobbySpots[0];
    for (const room of world.rooms)
      for (const desk of room.desks) {
        const path = findPath(world, from, desk.seat);
        expect(path.length, room.id).toBeGreaterThan(0);
        expect(path[path.length - 1]).toEqual(desk.seat);
        expect(legal(world, [from, ...path])).toBe(true);
      }
    const drop = findPath(world, world.rooms[2].desks[0].seat, world.drop);
    expect(drop.length).toBeGreaterThan(0);
    expect(roomAt(world, drop[drop.length - 1])?.kind).toBe("cluster");
  });

  it("goes through doors, never through walls", () => {
    // From the left middle room to the right one: out of the front door, along the corridor.
    const [left, right] = world.rooms;
    const path = findPath(world, left.desks[0].seat, right.desks[0].seat);
    const corridorY = left.y + left.h;
    expect(path.some((p) => p.y > corridorY)).toBe(true);
    expect(legal(world, [left.desks[0].seat, ...path])).toBe(true);
  });

  it("is thinned to its turns", () => {
    const path = findPath(world, world.lobbySpots[0], world.lobbySpots[4]);
    expect(path.length).toBeLessThanOrEqual(2);
  });

  it("starts from the nearest free cell when standing somewhere odd", () => {
    const inWall = { x: world.cluster.x + 4, y: world.cluster.y + 4 };
    const near = nearestWalkable(world, inWall);
    expect(walkable(world, near)).toBe(true);
    expect(Math.hypot(near.x - inWall.x, near.y - inWall.y)).toBeLessThan(CELL * 12);
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
