import { describe, expect, it } from "vitest";

import { anchorFor, boxReduce, densityOf, safePath } from "./art";
import { clampCamera, fitZoom, zoomLevels } from "./DevOffice";
import { layoutOffice } from "./world";

describe("the camera", () => {
  it("only zooms where every pixel of the densest character is whole", () => {
    expect(zoomLevels(1, 2).slice(0, 4)).toEqual([1, 2, 3, 4]);
    // 4× characters: shrunk 4:1 or 2:1, or drawn 1:1, 2:1, 3:1…
    expect(zoomLevels(4, 2).slice(0, 6)).toEqual([1, 2, 3, 4, 8, 12]);
    expect(zoomLevels(2, 1).slice(0, 3)).toEqual([1, 2, 4]);
    expect(zoomLevels(2, 2)).not.toContain(3);
    // Close enough to see a 4× face pixel for pixel.
    expect(Math.max(...zoomLevels(4, 1))).toBeGreaterThanOrEqual(8);
  });

  it("shows the whole office at Z = 3 on a 14-inch MacBook, the sidebar open", () => {
    const world = layoutOffice([1, 2, 3, 4, 5].map((i) => ({ id: `gh:${i}`, desks: ["a"] })));
    // 1512 × 860 CSS at 2×: the stage is 1280 wide (sidebar 232) and 804 tall (header 56).
    expect(fitZoom(4, 2, 1280, 804, world)).toBe(3);
    // 1440 × 900 too; a 1× screen of the same size at 1.
    expect(fitZoom(4, 2, 1208, 844, world)).toBe(3);
    expect(fitZoom(4, 1, 1208, 844, world)).toBe(1);
    // Too short for Z = 3: the height decides.
    expect(fitZoom(4, 2, 1280, 700, world)).toBe(2);
    // A phone gets the smallest whole level, and pans.
    expect(fitZoom(4, 3, 358, 780, world)).toBe(1);
  });

  it("keeps the view inside the office, or centres an office smaller than the view", () => {
    const world = { w: 1000, h: 800 };
    expect(clampCamera({ x: -50, y: 900, z: 2 }, world, 1000, 600)).toEqual({ x: 0, y: 500, z: 2 });
    expect(clampCamera({ x: 10, y: 10, z: 1 }, world, 1200, 900).x).toBe(-100);
  });

  it("keeps a small office clear of the floating controls, centred in the rest", () => {
    const small = { w: 100, h: 100 };
    // 100 spare: 40 kept at the top, 20 at the bottom, the other 40 shared.
    expect(clampCamera({ x: 0, y: 0, z: 1 }, small, 100, 200, { top: 40, bottom: 20 }).y).toBe(-60);
    // Not enough spare for both: shared in their proportion.
    expect(clampCamera({ x: 0, y: 0, z: 1 }, small, 100, 130, { top: 60, bottom: 60 }).y).toBe(-15);
    // Reserves are device pixels: halved in art pixels at Z = 2.
    expect(clampCamera({ x: 0, y: 0, z: 2 }, small, 200, 400, { top: 80, bottom: 0 }).y).toBe(-70);
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
