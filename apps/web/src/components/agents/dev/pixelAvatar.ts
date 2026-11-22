/**
 * A person's GitHub picture, redrawn as an arcade character.
 *
 * The bytes come from Tracesis (`GET /v1/agents/dev/people/{key}/avatar`),
 * never from GitHub: the browser talks to its own origin only. A 204 — no
 * login, a bot, no picture — means "draw the initials", and is not an error.
 *
 * The picture is shrunk to a GRID × GRID (16 × 16) canvas (its centre square), every
 * pixel snapped to a small palette (four levels per channel, after a little
 * extra contrast so faces stay readable at that size), then scaled back up
 * with smoothing off. The result is a data URL: one load per person per
 * page, shared by every node that shows them.
 */

export const GRID = 16;
const SCALE = 5;
const LEVELS = [0, 85, 170, 255];

const loads = new Map<string, Promise<string | null>>();
const settled = new Map<string, string | null>();

export function avatarUrl(personKey: string): string {
  return `/v1/agents/dev/people/${encodeURIComponent(personKey)}/avatar`;
}

/** Whether a person can have a GitHub picture at all: a login, not a name or a Claude identity. */
export function mayHaveAvatar(personKey: string, bot = false): boolean {
  return !bot && !personKey.startsWith("name:") && !personKey.startsWith("claude:") && personKey !== "unknown";
}

/** Snap one channel to the palette, with a little contrast first. */
export function quantize(value: number): number {
  const contrasted = Math.max(0, Math.min(255, (value - 128) * 1.18 + 128));
  let best = LEVELS[0];
  for (const level of LEVELS) if (Math.abs(level - contrasted) < Math.abs(best - contrasted)) best = level;
  return best;
}

/** The picture as pixel art (a PNG data URL), or null when it cannot be drawn. */
export function pixelate(image: CanvasImageSource & { naturalWidth: number; naturalHeight: number }): string | null {
  const width = image.naturalWidth;
  const height = image.naturalHeight;
  if (!width || !height) return null;
  try {
    const small = document.createElement("canvas");
    small.width = GRID;
    small.height = GRID;
    const context = small.getContext("2d", { willReadFrequently: true });
    if (!context) return null;
    const side = Math.min(width, height);
    context.imageSmoothingEnabled = true;
    context.drawImage(image, (width - side) / 2, (height - side) / 2, side, side, 0, 0, GRID, GRID);
    const frame = context.getImageData(0, 0, GRID, GRID);
    const px = frame.data;
    for (let i = 0; i < px.length; i += 4) {
      px[i] = quantize(px[i]);
      px[i + 1] = quantize(px[i + 1]);
      px[i + 2] = quantize(px[i + 2]);
      px[i + 3] = 255;
    }
    context.putImageData(frame, 0, 0);
    const big = document.createElement("canvas");
    big.width = GRID * SCALE;
    big.height = GRID * SCALE;
    const draw = big.getContext("2d");
    if (!draw) return null;
    draw.imageSmoothingEnabled = false;
    draw.drawImage(small, 0, 0, big.width, big.height);
    return big.toDataURL("image/png");
  } catch {
    return null;
  }
}

function decode(url: string): Promise<HTMLImageElement | null> {
  return new Promise((resolve) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => resolve(null);
    image.src = url;
  });
}

export function settledAvatar(personKey: string): string | null | undefined {
  return settled.get(personKey);
}

/** The person's arcade picture, or null for "draw the initials". Never rejects. */
export function loadAvatar(personKey: string): Promise<string | null> {
  let pending = loads.get(personKey);
  if (!pending) {
    pending = (async (): Promise<string | null> => {
      try {
        const response = await fetch(avatarUrl(personKey), { credentials: "include" });
        if (response.status !== 200) return null;
        const blob = await response.blob();
        if (blob.type !== "image/png" && blob.type !== "image/jpeg") return null;
        const url = URL.createObjectURL(blob);
        try {
          const image = await decode(url);
          return image ? pixelate(image) : null;
        } finally {
          URL.revokeObjectURL(url);
        }
      } catch {
        return null;
      }
    })().then((result) => {
      settled.set(personKey, result);
      return result;
    });
    loads.set(personKey, pending);
  }
  return pending;
}
