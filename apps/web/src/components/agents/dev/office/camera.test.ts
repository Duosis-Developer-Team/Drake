import { describe, expect, it } from "vitest";

import { anchorFor, boxReduce, densityOf, safePath } from "./art";
import { clampCamera, defaultCamera, defaultZoom, fitCamera, fitZoom, frameRoom, stageZoom, tween, zoomLevels, type Camera } from "./camera";
import { spriteScale } from "./engine";
import { layoutOffice, roomOrder } from "./world";

const office = { w: 768, h: 480 };
/** The exterior around the office: 1280 × 800, the office centred in it. */
const exterior = { x: -256, y: -160, w: 1280, h: 800 };
/** The stage on a 14-inch MacBook Pro, 1512 × 860 at 2×, sidebar open: 1280 × 761 CSS under the toolbar. */
const mac = { w: 2560, h: 1522 };

describe("the camera", () => {
  it("only zooms where every pixel of the densest character is whole", () => {
    expect(zoomLevels(1, 2).slice(0, 4)).toEqual([1, 2, 3, 4]);
    // 4× characters: shrunk 4:1 or 2:1, one pixel a pixel at 3, then every even level.
    expect(zoomLevels(4, 2).slice(0, 7)).toEqual([1, 2, 3, 4, 6, 8, 10]);
    expect(zoomLevels(2, 1).slice(0, 3)).toEqual([1, 2, 4]);
    expect(zoomLevels(2, 2)).not.toContain(3);
    // Close enough to see a 4× face pixel for pixel.
    expect(Math.max(...zoomLevels(4, 1))).toBeGreaterThanOrEqual(8);
  });

  it("draws a 4× character crisp at every level: shrunk by a whole factor, whole pixels", () => {
    expect(spriteScale(8, 4)).toEqual({ factor: 1, k: 1 });
    expect(spriteScale(4, 4)).toEqual({ factor: 1, k: 1 });
    expect(spriteScale(6, 4)).toEqual({ factor: 2, k: 1 });
    expect(spriteScale(2, 4)).toEqual({ factor: 2, k: 1 });
    expect(spriteScale(1, 4)).toEqual({ factor: 4, k: 1 });
    // The one exception: unshrunk at 3, a third larger.
    expect(spriteScale(3, 4)).toEqual({ factor: 1, k: 4 / 3 });
    expect(spriteScale(5, 1)).toEqual({ factor: 1, k: 1 });
  });

  it("fits the whole office at Z = 3 on a 14-inch MacBook, live and playing a day", () => {
    const world = layoutOffice([1, 2, 3, 4, 5].map((i) => ({ id: `gh:${i}`, desks: ["a"] })));
    // Under the toolbar (43): 761 tall; with the day's bar too (39): 722.
    expect(fitZoom(4, 2, 1280, 761, world)).toBe(3);
    expect(fitZoom(4, 2, 1280, 722, world)).toBe(3);
    // 1440 × 900 too; a 1× screen of the same size at 1.
    expect(fitZoom(4, 2, 1208, 801, world)).toBe(3);
    expect(fitZoom(4, 1, 1208, 801, world)).toBe(1);
    // Too short for Z = 3: the height decides.
    expect(fitZoom(4, 2, 1280, 700, world)).toBe(2);
    // A phone gets the smallest whole level, and pans.
    expect(fitZoom(4, 3, 358, 700, world)).toBe(1);
  });

  it("opens at the whole office as large as it can be, never cut at the top or the bottom", () => {
    const levels = zoomLevels(4, 2);
    // A 13-inch MacBook Air, sidebar open: 1280 × 663 CSS under the toolbar.
    // Z = 3 needs 1440 device pixels down: drawn at 3, shown 8% smaller.
    const air = stageZoom(levels, 2560, 1326, office);
    expect(air.z).toBe(3);
    expect(air.shrink).toBeCloseTo(1326 / 1440, 5);
    // Full screen on it (1512 × 939): Z = 4 is 2% too tall: 4, a hair smaller.
    const full = stageZoom(levels, 3024, 1878, office);
    expect(full.z).toBe(4);
    expect(full.shrink).toBeGreaterThan(0.97);
    // Where a whole level fits, nothing is shrunk.
    expect(stageZoom(levels, 2560, 1522, office)).toEqual({ z: 3, shrink: 1 });
    // Never more than 15% smaller: a short stage takes the level below.
    expect(stageZoom(levels, 2560, 1100, office)).toEqual({ z: 2, shrink: 1 });
    // A phone gets the smallest whole level, and pans.
    expect(stageZoom(zoomLevels(4, 3), 1074, 2100, office)).toEqual({ z: 1, shrink: 1 });
  });

  it("opens no closer than the whole office unless the scene leaves the sides empty", () => {
    const levels = zoomLevels(4, 2);
    // The exterior (1280 wide) covers the MacBook's width at 3: the whole office.
    expect(defaultZoom(levels, 3, exterior.w, mac.w)).toBe(3);
    // A very wide stage: closer until the scene spans it.
    expect(defaultZoom(levels, 2, exterior.w, 3200)).toBe(3);
    // Without an exterior, the office itself spans the width.
    expect(defaultZoom(levels, 3, office.w, mac.w)).toBe(4);
  });

  it("shows no space beyond the office's sides in the first look, and the band where people are", () => {
    const top = { x: 8, y: 8, w: 240, h: 152 };
    const lobby = { x: 264, y: 352, w: 240, h: 120 };
    for (const [w, h] of [
      [2560, 1522],
      [2416, 1602],
    ]) {
      const cam = defaultCamera(office, 4, w, h, [lobby]);
      expect(cam.x).toBeGreaterThanOrEqual(0);
      expect(cam.x + w / 4).toBeLessThanOrEqual(768);
      expect(cam.y).toBeGreaterThanOrEqual(0);
      expect(cam.y + h / 4).toBeLessThanOrEqual(480);
      // The lobby, at the bottom, is in view.
      expect(cam.y + h / 4).toBeGreaterThanOrEqual(lobby.y + lobby.h);
    }
    // People at the top: the top in view.
    expect(defaultCamera(office, 4, mac.w, mac.h, [top]).y).toBe(0);
    // Nobody: the cluster and the lobby.
    const cluster = { x: 216, y: 192, w: 336, h: 128 };
    const cam = defaultCamera(office, 4, mac.w, mac.h, [cluster, lobby]);
    expect(cam.y).toBeLessThanOrEqual(cluster.y);
    expect(cam.y + mac.h / 4).toBeGreaterThanOrEqual(lobby.y + lobby.h);
  });

  it("keeps the view inside the exterior, else the office, or centres what is smaller than the view", () => {
    const world = { w: 1000, h: 800 };
    expect(clampCamera({ x: -50, y: 900, z: 2 }, world, 1000, 600)).toEqual({ x: 0, y: 500, z: 2 });
    expect(clampCamera({ x: 10, y: 10, z: 1 }, world, 1200, 900).x).toBe(-100);
    // With the exterior, the street and the parking show at the edges, never beyond.
    expect(clampCamera({ x: -400, y: -300, z: 3 }, exterior, mac.w, mac.h)).toEqual({ x: -256, y: -160, z: 3 });
    // "Whole office": the office in the middle, the exterior around it.
    const fit = fitCamera(office, exterior, 3, mac.w, mac.h);
    expect(fit.x + mac.w / 6).toBeCloseTo(384);
    expect(fit.y + mac.h / 6).toBeCloseTo(240);
  });

  it("brings a room into view at the largest whole level that fits it, clear of the caption", () => {
    const levels = zoomLevels(4, 2);
    const world = layoutOffice([1, 2, 3, 4].map((i) => ({ id: `gh:${i}`, desks: ["a", "b"] })));
    const reserve = 2 * (104 + 24);
    for (const room of roomOrder(world)) {
      const cam = frameRoom(room, levels, mac.w, mac.h, reserve);
      const view = { w: mac.w / cam.z, h: (mac.h - reserve) / cam.z };
      // The room and its margin are inside the view above the caption…
      expect(room.x - 12).toBeGreaterThanOrEqual(cam.x - 0.001);
      expect(room.x + room.w + 12).toBeLessThanOrEqual(cam.x + view.w + 0.001);
      expect(room.y - 12).toBeGreaterThanOrEqual(cam.y - 0.001);
      expect(room.y + room.h + 12).toBeLessThanOrEqual(cam.y + view.h + 0.001);
      // …and the next level up would not fit it.
      const next = levels[levels.indexOf(cam.z) + 1];
      expect((room.w + 24) * next > mac.w || (room.h + 24) * next > mac.h - reserve, room.slot).toBe(true);
    }
    // A large room comes in well closer than the whole office.
    expect(frameRoom(roomOrder(world)[0], levels, mac.w, mac.h, reserve).z).toBeGreaterThanOrEqual(6);
  });

  it("moves between two views evenly, at whole levels lifted the rest of the way, crisp at the end", () => {
    const levels = zoomLevels(4, 2);
    const from: Camera = { x: 0, y: 0, z: 3 };
    const to: Camera = { x: 100, y: 50, z: 8 };
    const start = tween(from, to, 0, mac.w, mac.h, levels);
    expect(start.camera).toEqual(from);
    expect(start.lift).toBe(1);
    const end = tween(from, to, 1, mac.w, mac.h, levels);
    expect(end.camera.z).toBe(8);
    expect(end.camera.x).toBeCloseTo(100);
    expect(end.camera.y).toBeCloseTo(50);
    expect(end.lift).toBeCloseTo(1);
    for (const t of [0.2, 0.5, 0.8]) {
      const step = tween(from, to, t, mac.w, mac.h, levels);
      expect(levels).toContain(step.camera.z);
      expect(step.lift).toBeGreaterThanOrEqual(1);
      // Never lifted past the next level: the scale moves smoothly.
      expect(step.camera.z * step.lift).toBeLessThan(levels[levels.indexOf(step.camera.z) + 1] + 1e-9);
    }
  });
});

describe("shrinking a character by a whole factor", () => {
  const px = (r: number, g: number, b: number, a: number) => [r, g, b, a];

  it("averages each block, weighted by what covers it, and keeps alpha binary", () => {
    // 2 × 2 → 1: red, red, blue, clear.
    const src = new Uint8ClampedArray([...px(255, 0, 0, 255), ...px(255, 0, 0, 255), ...px(0, 0, 255, 255), ...px(0, 255, 0, 0)]);
    const { data, width, height } = boxReduce(src, 2, 2, 2);
    expect([width, height]).toEqual([1, 1]);
    // The clear pixel's colour never leaks in.
    expect([...data]).toEqual([170, 0, 85, 255]);
  });

  it("drops a block less than half covered", () => {
    const src = new Uint8ClampedArray([...px(9, 9, 9, 255), ...px(0, 0, 0, 0), ...px(0, 0, 0, 0), ...px(0, 0, 0, 0)]);
    expect(boxReduce(src, 2, 2, 2).data[3]).toBe(0);
  });

  it("turns a 128 × 192 frame into the 32 × 48 of the office", () => {
    const src = new Uint8ClampedArray(128 * 192 * 4);
    const { width, height } = boxReduce(src, 128, 192, 4);
    expect([width, height]).toEqual([32, 48]);
  });
});

describe("reading the art kit", () => {
  it("tells the density from a clip's height and refuses anything else", () => {
    expect(densityOf(48)).toBe(1);
    expect(densityOf(96)).toBe(2);
    expect(densityOf(192)).toBe(4);
    expect(densityOf(64)).toBeNull();
  });

  it("puts the feet where the kit says, for its frame size only", () => {
    const v1 = { frameSize: { width: 32, height: 48 }, anchor: { x: 16, y: 45 } };
    expect(anchorFor(1, v1)).toEqual({ x: 16, y: 45 });
    // A denser strip under a manifest written for another size: the known default.
    expect(anchorFor(2, v1)).toEqual({ x: 32, y: 91 });
    expect(anchorFor(4, v1)).toEqual({ x: 64, y: 180 });
    expect(anchorFor(4, { frameSize: { width: 128, height: 192 }, anchor: { x: 63, y: 179 } })).toEqual({ x: 63, y: 179 });
  });

  it("loads only relative PNG paths inside the office folder", () => {
    expect(safePath("characters/gencay/body-walk-left.png")).toBe("characters/gencay/body-walk-left.png");
    expect(safePath("../secret.png")).toBeNull();
    expect(safePath("/etc/passwd.png")).toBeNull();
    expect(safePath("data:image/png;base64,AAAA.png")).toBeNull();
    expect(safePath("//elsewhere/a.png")).toBeNull();
    expect(safePath("room.svg")).toBeNull();
    expect(safePath(42)).toBeNull();
  });
});
