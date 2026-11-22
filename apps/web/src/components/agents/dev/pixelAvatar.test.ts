import { afterEach, describe, expect, it, vi } from "vitest";

import { defaults, layout, readFeatures, render, SAMPLE, SPRITE } from "@/components/agents/dev/arcadeSprite";
import { avatarUrl, crispSize, isPortrait, loadAvatar, mayHaveAvatar } from "@/components/agents/dev/pixelAvatar";
import { installFetchMock } from "@/test/mock-api";

afterEach(() => vi.unstubAllGlobals());

describe("arcade avatars", () => {
  it("asks Tracesis, never GitHub, by the person's key", () => {
    expect(avatarUrl("gencay")).toBe("/v1/agents/dev/people/gencay/avatar");
    expect(avatarUrl("a b")).toBe("/v1/agents/dev/people/a%20b/avatar");
  });

  it("only asks for people who can have a GitHub picture", () => {
    expect(mayHaveAvatar("gencaycoskuntr")).toBe(true);
    expect(mayHaveAvatar("dependabot[bot]", true)).toBe(false);
    expect(mayHaveAvatar("name:enes")).toBe(false);
    expect(mayHaveAvatar("claude:abc123")).toBe(false);
    expect(mayHaveAvatar("unknown")).toBe(false);
  });

  /** A SAMPLE × SAMPLE picture painted by a function of (x, y). */
  const picture = (paint: (x: number, y: number) => [number, number, number]) => {
    const px = new Uint8ClampedArray(SAMPLE * SAMPLE * 4);
    for (let y = 0; y < SAMPLE; y += 1)
      for (let x = 0; x < SAMPLE; x += 1) {
        const [r, g, b] = paint(x, y);
        px.set([r, g, b, 255], (y * SAMPLE + x) * 4);
      }
    return px;
  };
  const SKIN: [number, number, number] = [224, 166, 128];
  const HAIR: [number, number, number] = [30, 24, 22];
  const BG: [number, number, number] = [210, 214, 220];
  const SHIRT: [number, number, number] = [40, 70, 130];
  /** A portrait: background, hair, a face from row 9, a dark beard run with lips under it, a shirt. */
  const portrait = (beard: boolean) =>
    picture((x, y) => {
      const centre = x >= 9 && x <= 22;
      if (y >= 25) return SHIRT;
      if (!centre) return BG;
      if (y < 4) return BG;
      if (y < 9) return HAIR;
      if (beard && (y === 19 || y === 20)) return HAIR;
      return SKIN;
    });

  it("draws a character, not a mosaic: the same sprite for everyone, coloured from the picture", () => {
    const shaven = readFeatures(portrait(false), "someone");
    expect(shaven.beard).toBe(false);
    expect(shaven.style).not.toBe("bald");
    const bearded = readFeatures(portrait(true), "someone");
    expect(bearded.beard).toBe(true);
    const pixels = render(shaven);
    expect(pixels).toHaveLength(SPRITE * SPRITE * 4);
    // Eyes are ink, the corner is backdrop, and an outline separates them.
    const at = (x: number, y: number) => Array.from(pixels.slice((y * SPRITE + x) * 4, (y * SPRITE + x) * 4 + 3));
    expect(at(6, 7)).toEqual([28, 20, 38]);
    expect(at(0, 0)).toEqual([...shaven.backdrop]);
  });

  it("reads GitHub's identicons as no face, and dresses the character in their colour", () => {
    const identicon = picture((x, y) => (x > 8 && x < 22 && y > 8 && y < 22 && (x + y) % 2 === 0 ? [80, 200, 160] : [240, 240, 240]));
    const f = readFeatures(identicon, "octo");
    expect(f).toEqual(defaults("octo", f.shirt));
    expect(f.beard).toBe(false);
  });

  it("shows the sprite only at whole-pixel scales, so its pixels stay square", () => {
    expect(crispSize(36, 1)).toBe(32);
    expect(crispSize(36, 2)).toBe(32);
    expect(crispSize(44, 2)).toBe(40);
    expect(crispSize(56, 2)).toBe(56);
    expect(crispSize(24, 1)).toBe(16);
    expect(crispSize(10, 1)).toBe(16);
  });

  it("is stable per person and keeps the face clear of the hair", () => {
    expect(defaults("enes")).toEqual(defaults("enes"));
    for (const style of ["short", "spiky", "swept", "curly", "long", "bald"] as const) {
      const grid = layout({ style, beard: false });
      expect(grid[7][6]).toBe("eye");
      expect(grid[7][9]).toBe("eye");
      expect(grid[10][7]).toBe("mouth");
    }
  });

  it("draws the initials when there is no picture (204)", async () => {
    const calls = installFetchMock({ "/v1/agents/dev/people/nopicture/avatar": { status: 204, body: null } });
    expect(await loadAvatar("nopicture")).toBeNull();
    expect(await loadAvatar("nopicture")).toBeNull();
    expect(calls).toHaveLength(1); // one load per person per page
    expect(calls[0].path).toBe("/v1/agents/dev/people/nopicture/avatar");
  });

  it("keeps a hand-made portrait as it is, and says so", async () => {
    const png = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 1, 2, 3]);
    const fetchMock = vi.fn(async () =>
      new Response(png, { status: 200, headers: { "Content-Type": "image/png", "X-Tracesis-Avatar": "portrait" } }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const url = await loadAvatar("portrait-person");
    expect(url).toMatch(/^data:image\/png;base64,/);
    expect(isPortrait("portrait-person")).toBe(true);
    expect(isPortrait("someone-else")).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    // Always revalidated, so a portrait set later replaces a cached picture.
    expect(fetchMock).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ cache: "no-cache" }));
  });

  it("never shows a portrait of a type it does not expect", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response("<svg/>", { status: 200, headers: { "Content-Type": "image/svg+xml", "X-Tracesis-Avatar": "portrait" } }),
      ),
    );
    expect(await loadAvatar("svg-person")).toBeNull();
    expect(isPortrait("svg-person")).toBe(false);
  });
});
