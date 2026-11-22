/**
 * A person's GitHub picture, turned into an arcade character.
 *
 * The bytes come from Tracesis (`GET /v1/agents/dev/people/{key}/avatar`),
 * never from GitHub: the browser talks to its own origin only. A 204 — no
 * login, a bot, no picture — means "draw the initials", and is not an error.
 *
 * The picture is not pixelated into a mosaic: it is read at SAMPLE × SAMPLE
 * for skin, hair, shirt, background, hair length and a beard, and a
 * hand-drawn 16 × 16 sprite is coloured from that (arcadeSprite.ts). The
 * result is a data URL: one load per person per page, shared by every node
 * that shows them.
 */

import { readFeatures, render, SAMPLE, SPRITE } from "./arcadeSprite";

const loads = new Map<string, Promise<string | null>>();
const settled = new Map<string, string | null>();
const backdrops = new Map<string, string>();
const portraits = new Set<string>();
const PORTRAIT_TYPES = new Set(["image/png", "image/jpeg", "image/webp"]);

export function avatarUrl(personKey: string): string {
  return `/v1/agents/dev/people/${encodeURIComponent(personKey)}/avatar`;
}

/** Whether a person can have a GitHub picture at all: a login, not a name or a Claude identity. */
export function mayHaveAvatar(personKey: string, bot = false): boolean {
  return !bot && !personKey.startsWith("name:") && !personKey.startsWith("claude:") && personKey !== "unknown";
}

/** The person's character (a PNG data URL), or null when it cannot be drawn. */
export function arcade(
  image: CanvasImageSource & { naturalWidth: number; naturalHeight: number },
  personKey: string,
): string | null {
  const width = image.naturalWidth;
  const height = image.naturalHeight;
  if (!width || !height) return null;
  try {
    const sample = document.createElement("canvas");
    sample.width = SAMPLE;
    sample.height = SAMPLE;
    const read = sample.getContext("2d", { willReadFrequently: true });
    if (!read) return null;
    const side = Math.min(width, height);
    read.imageSmoothingEnabled = true;
    read.imageSmoothingQuality = "high";
    read.drawImage(image, (width - side) / 2, (height - side) / 2, side, side, 0, 0, SAMPLE, SAMPLE);
    const features = readFeatures(read.getImageData(0, 0, SAMPLE, SAMPLE).data, personKey);

    // Kept at its native 16 × 16: the tile scales it by whole pixels, so
    // every sprite pixel stays square (see the Avatar component).
    const sprite = document.createElement("canvas");
    sprite.width = SPRITE;
    sprite.height = SPRITE;
    const paint = sprite.getContext("2d");
    if (!paint) return null;
    paint.putImageData(new ImageData(render(features), SPRITE, SPRITE), 0, 0);
    backdrops.set(personKey, `rgb(${features.backdrop.join(" ")})`);
    return sprite.toDataURL("image/png");
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

/** The character's backdrop colour, to fill the tile around the sprite. */
export function avatarBackdrop(personKey: string): string | undefined {
  return backdrops.get(personKey);
}

/** The largest size at or under `size` CSS pixels that scales a 16-pixel sprite by whole device pixels. */
export function crispSize(size: number, devicePixelRatio = 1): number {
  const unit = SPRITE / Math.max(1, devicePixelRatio);
  return Math.max(unit, Math.floor(size / unit + 1e-6) * unit);
}

/** Whether the person's face is a hand-made portrait (shown as is) rather than an arcade sprite. */
export function isPortrait(personKey: string): boolean {
  return portraits.has(personKey);
}

async function dataUrl(blob: Blob): Promise<string | null> {
  try {
    const bytes = new Uint8Array(await blob.arrayBuffer());
    let binary = "";
    for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    return `data:${blob.type};base64,${btoa(binary)}`;
  } catch {
    return null;
  }
}

export function settledAvatar(personKey: string): string | null | undefined {
  return settled.get(personKey);
}

/**
 * The person's face, or null for "draw the initials". Never rejects.
 *
 * A hand-made portrait (`X-Tracesis-Avatar: portrait`) is kept as it is; a
 * GitHub picture is redrawn as an arcade character.
 */
export function loadAvatar(personKey: string): Promise<string | null> {
  let pending = loads.get(personKey);
  if (!pending) {
    pending = (async (): Promise<string | null> => {
      try {
        const response = await fetch(avatarUrl(personKey), { credentials: "include" });
        if (response.status !== 200) return null;
        const blob = await response.blob();
        if (response.headers.get("X-Tracesis-Avatar") === "portrait") {
          if (!PORTRAIT_TYPES.has(blob.type)) return null;
          const url = await dataUrl(blob);
          if (url) portraits.add(personKey);
          return url;
        }
        if (blob.type !== "image/png" && blob.type !== "image/jpeg") return null;
        const url = URL.createObjectURL(blob);
        try {
          const image = await decode(url);
          return image ? arcade(image, personKey) : null;
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
