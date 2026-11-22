import { describe, expect, it } from "vitest";

import { anchorFor, densityOf, safePath } from "./art";
import { clampCamera, defaultZoom, fitZoom, zoomLevels } from "./DevOffice";

describe("the camera", () => {
  it("zooms by whole device pixels, even ones when a character is drawn at double density", () => {
    expect(zoomLevels(1, 2).slice(0, 4)).toEqual([1, 2, 3, 4]);
    expect(zoomLevels(2, 2).every((z) => z % 2 === 0)).toBe(true);
    expect(zoomLevels(2, 1)[0]).toBe(2);
  });

  it("opens at about one CSS pixel an art pixel, more when the office fits", () => {
    expect(defaultZoom(1, 2, 1100, 1152)).toBe(2);
    expect(defaultZoom(2, 2, 1100, 1152)).toBe(2);
    expect(defaultZoom(1, 1, 3000, 1152)).toBe(2); // a wide screen: the office fits at 2×
    expect(defaultZoom(2, 3, 360, 1152) % 2).toBe(0);
  });

  it("fits the whole width at the largest whole zoom", () => {
    expect(fitZoom(1, 2, 1200, 1152)).toBe(2);
    expect(fitZoom(2, 1, 600, 1152)).toBe(2); // never below one step
  });

  it("keeps the view inside the office, or centres an office smaller than the view", () => {
    const world = { w: 1000, h: 800 };
    expect(clampCamera({ x: -50, y: 900, z: 2 }, world, 1000, 600)).toEqual({ x: 0, y: 500, z: 2 });
    expect(clampCamera({ x: 10, y: 10, z: 1 }, world, 1200, 900).x).toBe(-100);
  });
});

describe("reading the art kit", () => {
  it("tells the density from a clip's height and refuses anything else", () => {
    expect(densityOf(48)).toBe(1);
    expect(densityOf(96)).toBe(2);
    expect(densityOf(64)).toBeNull();
  });

  it("puts the feet where the kit says, for its frame size only", () => {
    const v1 = { frameSize: { width: 32, height: 48 }, anchor: { x: 16, y: 45 } };
    expect(anchorFor(1, v1)).toEqual({ x: 16, y: 45 });
    // A double-density strip under a manifest written for 32 × 48: the known default.
    expect(anchorFor(2, v1)).toEqual({ x: 32, y: 91 });
    expect(anchorFor(2, { frameSize: { width: 64, height: 96 }, anchor: { x: 31, y: 90 } })).toEqual({ x: 31, y: 90 });
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
