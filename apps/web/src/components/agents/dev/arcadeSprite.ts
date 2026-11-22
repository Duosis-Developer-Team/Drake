/**
 * An arcade character for a person, drawn from their GitHub picture.
 *
 * The picture is not shrunk into a mosaic. It is only *read*: skin, hair,
 * shirt and background colours, whether the hair reaches the shoulders,
 * whether there is a beard. Those choose the parts of a hand-drawn 16 × 16
 * sprite — head, hair, eyes, brows, mouth, shirt — snapped to a small retro
 * palette, shaded on one side and outlined, so the result reads as a game
 * character rather than a censored photo. Everything else (hairstyle when the
 * picture cannot tell, a default skin tone) comes from a hash of the person's
 * key, so the same person always gets the same character.
 *
 * Pure: pixels in, pixels out. The canvas glue lives in pixelAvatar.ts.
 */

export const SPRITE = 16;
/** The sample the picture is read from: SAMPLE × SAMPLE RGBA. */
export const SAMPLE = 32;

type RGB = readonly [number, number, number];

const OUTLINE: RGB = [28, 20, 38];
const INK: RGB = [28, 20, 38];
const SHADOW: RGB = [42, 26, 58];

/** Skin tones, light to dark. */
const SKIN: readonly RGB[] = [
  [255, 222, 196],
  [244, 194, 156],
  [222, 160, 120],
  [192, 128, 92],
  [154, 96, 66],
  [108, 68, 46],
];

/** Everything else snaps to this palette. */
const PALETTE: readonly RGB[] = [
  [28, 20, 38],
  [58, 42, 72],
  [64, 70, 80],
  [112, 120, 138],
  [154, 156, 170],
  [230, 230, 238],
  [88, 58, 44],
  [138, 90, 60],
  [192, 136, 80],
  [232, 192, 112],
  [244, 228, 168],
  [168, 58, 58],
  [224, 96, 60],
  [240, 154, 60],
  [58, 106, 58],
  [90, 160, 74],
  [42, 74, 122],
  [60, 122, 208],
  [106, 192, 232],
  [122, 74, 176],
  [208, 106, 168],
  [46, 92, 92],
  [74, 168, 160],
];

/** Hair colours, natural first, then a few dyed ones. */
const HAIR_PALETTE: readonly RGB[] = [
  [46, 38, 52],
  [70, 46, 34],
  [104, 66, 44],
  [138, 60, 40],
  [196, 104, 52],
  [214, 170, 92],
  [236, 222, 176],
  [150, 150, 160],
  [228, 228, 236],
  [60, 110, 200],
  [210, 100, 170],
  [90, 160, 90],
  [130, 80, 190],
];

/** Hair when the picture shows none: dark brown, black, brown, auburn, blonde. */
const HAIR_DEFAULTS: readonly RGB[] = [
  [70, 46, 34],
  [46, 38, 52],
  [104, 66, 44],
  [138, 60, 40],
  [214, 170, 92],
];

/** Backgrounds that stay clear of most hair and shirts. */
const BACKDROPS: readonly RGB[] = [
  [60, 122, 208],
  [74, 168, 160],
  [208, 106, 168],
  [240, 154, 60],
  [122, 74, 176],
  [90, 160, 74],
];

export type HairStyle = "short" | "spiky" | "swept" | "curly" | "long" | "bald";

export interface Features {
  skin: RGB;
  hair: RGB;
  shirt: RGB;
  backdrop: RGB;
  style: HairStyle;
  beard: boolean;
}

export function hash(text: string): number {
  let h = 2166136261;
  for (let i = 0; i < text.length; i += 1) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

/** Perceptual-ish RGB distance ("redmean"). */
export function distance(a: RGB, b: RGB): number {
  const r = (a[0] + b[0]) / 2;
  const dr = a[0] - b[0];
  const dg = a[1] - b[1];
  const db = a[2] - b[2];
  return Math.sqrt((2 + r / 256) * dr * dr + 4 * dg * dg + (2 + (255 - r) / 256) * db * db);
}

function nearest(colour: RGB, palette: readonly RGB[]): RGB {
  let best = palette[0];
  let bestDistance = Infinity;
  for (const candidate of palette) {
    const d = distance(colour, candidate);
    if (d < bestDistance) {
      best = candidate;
      bestDistance = d;
    }
  }
  return best;
}

function mix(a: RGB, b: RGB, t: number): RGB {
  return [
    Math.round(a[0] + (b[0] - a[0]) * t),
    Math.round(a[1] + (b[1] - a[1]) * t),
    Math.round(a[2] + (b[2] - a[2]) * t),
  ];
}

const shade = (c: RGB): RGB => mix(c, SHADOW, 0.32);
const light = (c: RGB): RGB => mix(c, [255, 255, 255], 0.28);
const luma = (c: RGB) => 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2];
/** Near-black is drawn a step lighter so the outline still shows around it. */
const lift = (c: RGB): RGB => (luma(c) < 40 ? [48, 42, 58] : c);

function hsl(c: RGB): { h: number; s: number; l: number } {
  const r = c[0] / 255;
  const g = c[1] / 255;
  const b = c[2] / 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  if (max === min) return { h: 0, s: 0, l };
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  let h = max === r ? (g - b) / d + (g < b ? 6 : 0) : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  h *= 60;
  return { h, s, l };
}

/** Median colour of a rectangle of the sample, given in fractions of its side. */
function region(px: ArrayLike<number>, x0: number, y0: number, x1: number, y1: number): RGB {
  const rs: number[] = [];
  const gs: number[] = [];
  const bs: number[] = [];
  for (let y = Math.floor(y0 * SAMPLE); y < Math.ceil(y1 * SAMPLE); y += 1) {
    for (let x = Math.floor(x0 * SAMPLE); x < Math.ceil(x1 * SAMPLE); x += 1) {
      const i = (y * SAMPLE + x) * 4;
      rs.push(px[i]);
      gs.push(px[i + 1]);
      bs.push(px[i + 2]);
    }
  }
  const median = (v: number[]) => v.sort((a, b) => a - b)[Math.floor(v.length / 2)] ?? 0;
  return [median(rs), median(gs), median(bs)];
}

interface Cluster {
  colour: RGB;
  share: number;
}

/** The picture's main colours, largest first. */
export function clusters(px: ArrayLike<number>, fromRow = 0): Cluster[] {
  const found: { sum: [number, number, number]; n: number; colour: RGB }[] = [];
  const total = SAMPLE * (SAMPLE - fromRow);
  for (let i = fromRow * SAMPLE * 4; i < SAMPLE * SAMPLE * 4; i += 4) {
    const c: RGB = [px[i], px[i + 1], px[i + 2]];
    const hit = found.find((f) => distance(f.colour, c) < 60);
    if (hit) {
      hit.sum[0] += c[0];
      hit.sum[1] += c[1];
      hit.sum[2] += c[2];
      hit.n += 1;
      hit.colour = [hit.sum[0] / hit.n, hit.sum[1] / hit.n, hit.sum[2] / hit.n];
    } else {
      found.push({ sum: [c[0], c[1], c[2]], n: 1, colour: c });
    }
  }
  return found
    .map((f) => ({ colour: f.colour.map(Math.round) as unknown as RGB, share: f.n / total }))
    .sort((a, b) => b.share - a.share);
}

function isSkinLike(c: RGB): boolean {
  const { h, s, l } = hsl(c);
  return (h <= 45 || h >= 345) && s >= 0.18 && s <= 0.85 && l >= 0.32 && l <= 0.9;
}

/** Median colour of one row of the sample between two fractions of its width. */
function band(px: ArrayLike<number>, y: number, x0: number, x1: number): RGB {
  return region(px, x0, y / SAMPLE, x1, (y + 1) / SAMPLE);
}

const STYLES: readonly HairStyle[] = ["short", "spiky", "swept", "curly"];

/** What the character looks like when there is no usable picture: all from the key. */
export function defaults(key: string, accent?: RGB): Features {
  const h = hash(key);
  const base: Features = {
    skin: SKIN[h % SKIN.length],
    hair: HAIR_DEFAULTS[(h >>> 4) % HAIR_DEFAULTS.length],
    shirt: lift(accent ? nearest(accent, PALETTE) : PALETTE[16 + ((h >>> 8) % 4)]),
    backdrop: BACKDROPS[(h >>> 12) % BACKDROPS.length],
    style: STYLES[(h >>> 16) % STYLES.length],
    beard: false,
  };
  return { ...base, backdrop: clearBackdrop(base.backdrop, base, h) };
}

/** A backdrop that the hair, the shirt and the face all stand out from. */
function clearBackdrop(wanted: RGB, f: Pick<Features, "hair" | "shirt" | "skin">, seed: number): RGB {
  const clear = (b: RGB) => distance(b, f.hair) >= 80 && distance(b, f.shirt) >= 80 && distance(b, f.skin) >= 80;
  if (clear(wanted)) return wanted;
  for (let i = 0; i < BACKDROPS.length; i += 1) {
    const b = BACKDROPS[(seed + i) % BACKDROPS.length];
    if (clear(b)) return b;
  }
  return wanted;
}

/** Read a SAMPLE × SAMPLE RGBA picture into features. */
export function readFeatures(px: ArrayLike<number>, key: string): Features {
  const seed = hash(key);
  const fallback = defaults(key);
  const main = clusters(px);
  const corners = [region(px, 0, 0, 0.1, 0.1), region(px, 0.9, 0, 1, 0.1), region(px, 0, 0.9, 0.1, 1), region(px, 0.9, 0.9, 1, 1)];
  const bg = corners.slice().sort((a, b) => luma(a) - luma(b))[1];

  // A flat picture — GitHub's identicon, a logo, a cartoon — has no face to
  // read: two colours cover nearly all of it. Its liveliest colour dresses
  // the character instead.
  const flat = (main[0]?.share ?? 0) + (main[1]?.share ?? 0) >= 0.82;
  if (flat) {
    const accent = main
      .filter((c) => c.share >= 0.02)
      .map((c) => c.colour)
      .sort((a, b) => hsl(b).s * (1 - Math.abs(hsl(b).l - 0.5)) - hsl(a).s * (1 - Math.abs(hsl(a).l - 0.5)))[0];
    return defaults(key, accent);
  }

  // Find the face: down the middle, the first skin-coloured row that is not
  // the background is the forehead. The rows just above it are the hair —
  // read there rather than from the top, because dark hair against a dark
  // background has no edge to find.
  let faceTop = -1;
  for (let y = 1; y < SAMPLE * 0.7; y += 1) {
    const row = band(px, y, 0.4, 0.6);
    if (isSkinLike(row) && distance(row, bg) > 50 && isSkinLike(band(px, y + 1, 0.4, 0.6))) {
      faceTop = y;
      break;
    }
  }
  if (faceTop < 2) return { ...fallback, shirt: nearest(region(px, 0.2, 0.86, 0.8, 1), PALETTE) };

  const skinRaw = region(px, 0.38, (faceTop + 3) / SAMPLE, 0.62, (faceTop + 7) / SAMPLE);
  const skin = nearest(isSkinLike(skinRaw) ? skinRaw : band(px, faceTop, 0.4, 0.6), SKIN);
  // The hair: the darkest of the few rows above the forehead that is not skin.
  let hairRaw: RGB = band(px, faceTop - 1, 0.38, 0.62);
  for (let y = faceTop - 1; y >= Math.max(0, faceTop - 4); y -= 1) {
    const row = band(px, y, 0.38, 0.62);
    if (distance(row, skinRaw) > 45 && luma(row) < luma(hairRaw)) hairRaw = row;
  }
  let style: HairStyle = fallback.style;
  let hair: RGB = nearest(hairRaw, HAIR_PALETTE);
  const hairOnBackground = distance(hairRaw, bg) < 45;
  const hasHair = distance(hairRaw, skinRaw) > 45 && !(hairOnBackground && luma(bg) > 90);
  const pick = (["short", "swept", "spiky"] as const)[seed % 3];
  if (!hasHair) {
    style = "bald";
    hair = fallback.hair;
  } else if (hairOnBackground) {
    // Dark hair on a dark background: its colour is clear, its shape is not.
    style = pick;
  } else {
    // How far the hair spreads just above the forehead, how tall it stands,
    // and whether it falls past the cheeks.
    let wide = 0;
    for (let x = 0; x < SAMPLE; x += 1) {
      const i = ((faceTop - 2) * SAMPLE + x) * 4;
      if (distance([px[i], px[i + 1], px[i + 2]], hairRaw) < 55) wide += 1;
    }
    let tall = 0;
    for (let y = faceTop - 1; y >= 0 && distance(band(px, y, 0.4, 0.6), hairRaw) < 55; y -= 1) tall += 1;
    const cheeks = mix(
      region(px, 0.14, (faceTop + 5) / SAMPLE, 0.28, (faceTop + 11) / SAMPLE),
      region(px, 0.72, (faceTop + 5) / SAMPLE, 0.86, (faceTop + 11) / SAMPLE),
      0.5,
    );
    if (distance(cheeks, hairRaw) < 50 && distance(cheeks, bg) > 50) style = "long";
    else if (wide >= SAMPLE * 0.6 || tall >= SAMPLE * 0.25) style = "curly";
    else style = pick;
  }

  // A beard: below the eyes, a short dark run in the middle of the face with
  // skin again under it (lips, chin). A dark shirt has no skin under it.
  let beard = false;
  let beardColour: RGB = hair;
  const dark = (c: RGB) => luma(c) < luma(skinRaw) * 0.62 && !isSkinLike(c);
  const last = Math.min(Math.floor(SAMPLE * 0.9), faceTop + 20);
  for (let y = faceTop + 8; y < last && !beard; y += 1) {
    if (!dark(band(px, y, 0.4, 0.6))) continue;
    let run = 0;
    while (y + run < last && dark(band(px, y + run, 0.4, 0.6))) run += 1;
    if (run <= 5 && y + run < last && isSkinLike(band(px, y + run, 0.4, 0.6))) {
      beard = true;
      beardColour = band(px, y, 0.4, 0.6);
    }
    y += run;
  }
  if (style === "bald" && beard) hair = nearest(beardColour, HAIR_PALETTE);

  // The shirt: the largest colour of the lower third that is not skin
  // (crossed arms, a neck, a hand on the chin).
  const clothes = clusters(px, Math.floor(SAMPLE * 0.68)).find((c) => c.share >= 0.12 && !isSkinLike(c.colour));
  let shirt = clothes ? nearest(clothes.colour, PALETTE) : fallback.shirt;
  if (distance(shirt, skin) < 40) shirt = fallback.shirt;
  const f = { skin, hair: lift(hair), shirt: lift(shirt), style, beard };
  // The backdrop is always an arcade colour: the picture's own when it has a
  // lively one, the person's otherwise.
  const { s: bgSat, l: bgLight } = hsl(bg);
  const wanted = bgSat > 0.25 && bgLight > 0.2 && bgLight < 0.85 ? nearest(bg, BACKDROPS) : BACKDROPS[(seed >>> 12) % BACKDROPS.length];
  return { ...f, backdrop: clearBackdrop(wanted, f, seed) };
}

// --------------------------------------------------------------------------
// The sprite
// --------------------------------------------------------------------------

type Cell = "bg" | "skin" | "skinShade" | "hair" | "hairShade" | "hairLight" | "eye" | "brow" | "blush" | "mouth" | "shirt" | "shirtShade" | "shine";

const rows = (spec: Record<number, number[][]>): [number, number][] => {
  const out: [number, number][] = [];
  for (const [y, spans] of Object.entries(spec)) for (const [a, b] of spans) for (let x = a; x <= b; x += 1) out.push([x, Number(y)]);
  return out;
};

const HAIR: Record<HairStyle, [number, number][]> = {
  short: rows({ 1: [[5, 10]], 2: [[4, 11]], 3: [[3, 12]], 4: [[3, 7], [11, 12]], 5: [[3, 4], [12, 12]], 6: [[3, 3], [12, 12]] }),
  spiky: rows({
    0: [[5, 5], [8, 8], [11, 11]],
    1: [[4, 6], [7, 9], [10, 11]],
    2: [[3, 12]],
    3: [[3, 12]],
    4: [[3, 5], [7, 7], [10, 12]],
    5: [[3, 3], [12, 12]],
  }),
  swept: rows({ 1: [[4, 11]], 2: [[3, 12]], 3: [[3, 12]], 4: [[3, 9], [12, 12]], 5: [[3, 5], [12, 12]], 6: [[3, 3]] }),
  curly: rows({
    0: [[5, 10]],
    1: [[3, 12]],
    2: [[2, 13]],
    3: [[2, 13]],
    4: [[2, 5], [7, 7], [10, 13]],
    5: [[2, 4], [11, 13]],
    6: [[2, 3], [12, 13]],
    7: [[2, 2], [13, 13]],
  }),
  long: rows({
    1: [[4, 11]],
    2: [[3, 12]],
    3: [[2, 13]],
    4: [[2, 8], [11, 13]],
    5: [[2, 4], [11, 13]],
    6: [[2, 3], [12, 13]],
    7: [[2, 3], [12, 13]],
    8: [[2, 3], [12, 13]],
    9: [[2, 3], [12, 13]],
    10: [[2, 3], [12, 13]],
    11: [[2, 3], [12, 13]],
    12: [[2, 4], [11, 13]],
  }),
  bald: rows({ 6: [[3, 3], [12, 12]], 7: [[3, 3], [12, 12]] }),
};

const HAIR_LIGHT: Record<HairStyle, [number, number][]> = {
  short: [[6, 2], [7, 2], [5, 3]],
  spiky: [[5, 1], [8, 1], [6, 2]],
  swept: [[6, 2], [7, 2], [8, 2], [5, 3]],
  curly: [[5, 1], [6, 1], [4, 2]],
  long: [[6, 2], [7, 2], [4, 3]],
  bald: [],
};

/** The character as a SPRITE × SPRITE grid of cells. */
export function layout(features: Pick<Features, "style" | "beard">): Cell[][] {
  const grid: Cell[][] = Array.from({ length: SPRITE }, () => Array<Cell>(SPRITE).fill("bg"));
  const set = (x: number, y: number, cell: Cell) => {
    if (x >= 0 && y >= 0 && x < SPRITE && y < SPRITE) grid[y][x] = cell;
  };
  // Shoulders and shirt, with an open collar.
  for (let x = 3; x <= 12; x += 1) set(x, 13, "shirt");
  for (let y = 14; y < SPRITE; y += 1) for (let x = 2; x <= 13; x += 1) set(x, y, "shirt");
  for (let y = 13; y < SPRITE; y += 1) {
    set(12, y, "shirtShade");
    set(13, y, "shirtShade");
  }
  set(7, 13, "skin");
  set(8, 13, "skinShade");
  // Neck, in the head's shadow.
  for (let x = 6; x <= 9; x += 1) set(x, 12, "skinShade");
  // Head with rounded corners, a shaded right side and jaw; ears.
  for (let y = 3; y <= 11; y += 1) for (let x = 4; x <= 11; x += 1) set(x, y, "skin");
  for (const [x, y] of [[4, 3], [11, 3], [4, 11], [11, 11]] as const) set(x, y, "bg");
  for (let y = 4; y <= 10; y += 1) set(11, y, "skinShade");
  for (let x = 5; x <= 10; x += 1) set(x, 11, "skinShade");
  for (const [x, y] of [[3, 7], [3, 8], [12, 7], [12, 8]] as const) set(x, y, x === 12 ? "skinShade" : "skin");
  if (features.style === "bald") {
    for (let x = 5; x <= 10; x += 1) set(x, 2, "skin");
    set(10, 2, "skinShade");
    set(6, 3, "shine");
  }
  // Hair over the head.
  for (const [x, y] of HAIR[features.style]) set(x, y, x >= 11 ? "hairShade" : "hair");
  for (const [x, y] of HAIR_LIGHT[features.style]) set(x, y, "hairLight");
  // Face.
  for (const [x, y] of [[5, 6], [6, 6], [9, 6], [10, 6]] as const) set(x, y, "brow");
  for (const [x, y] of [[6, 7], [6, 8], [9, 7], [9, 8]] as const) set(x, y, "eye");
  if (features.beard) {
    for (let y = 9; y <= 11; y += 1) for (let x = 4; x <= 11; x += 1) if (grid[y][x] !== "bg") set(x, y, x >= 11 ? "hairShade" : "hair");
    set(4, 8, "hair");
    set(11, 8, "hairShade");
    set(7, 10, "mouth");
    set(8, 10, "mouth");
  } else {
    set(5, 9, "blush");
    set(10, 9, "blush");
    set(7, 10, "mouth");
    set(8, 10, "mouth");
  }
  return grid;
}

/** The sprite as SPRITE × SPRITE RGBA pixels, outlined. */
export function render(features: Features): Uint8ClampedArray<ArrayBuffer> {
  const grid = layout(features);
  const colour: Record<Cell, RGB> = {
    bg: features.backdrop,
    skin: features.skin,
    skinShade: shade(features.skin),
    hair: features.hair,
    hairShade: shade(features.hair),
    hairLight: light(features.hair),
    eye: INK,
    brow: luma(features.hair) > 150 ? shade(shade(features.hair)) : features.hair,
    blush: mix(features.skin, [224, 96, 80], 0.35),
    mouth: mix(shade(features.skin), [150, 50, 60], 0.45),
    shirt: features.shirt,
    shirtShade: shade(features.shirt),
    shine: light(light(features.skin)),
  };
  const out = new Uint8ClampedArray(new ArrayBuffer(SPRITE * SPRITE * 4));
  const filled = (x: number, y: number) => x >= 0 && y >= 0 && x < SPRITE && y < SPRITE && grid[y][x] !== "bg";
  for (let y = 0; y < SPRITE; y += 1) {
    for (let x = 0; x < SPRITE; x += 1) {
      let c = colour[grid[y][x]];
      if (grid[y][x] === "bg" && (filled(x - 1, y) || filled(x + 1, y) || filled(x, y - 1) || filled(x, y + 1))) c = OUTLINE;
      const i = (y * SPRITE + x) * 4;
      out[i] = c[0];
      out[i + 1] = c[1];
      out[i + 2] = c[2];
      out[i + 3] = 255;
    }
  }
  return out;
}
