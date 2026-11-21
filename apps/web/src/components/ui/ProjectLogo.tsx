"use client";

/**
 * A project's own logo, or its monogram.
 *
 * The image comes from the project's repository (`.duosis/media/logo-*.png`)
 * through `GET /v1/projects/{id}/logo`, which answers 404 whenever there is
 * nothing safe to show. The monogram is drawn first and stays until the
 * image has actually loaded, so a slow or missing logo never shifts the row
 * and never leaves a broken-image box.
 *
 * Duosis logos are wordmarks with the symbol on the left. `wordmark` keeps
 * the image's height and takes the width it needs, up to a cap; `mark`
 * shows the symbol alone, the left square of the same file, for rows where
 * the name is already written beside it. Dark surfaces get the
 * light-on-dark file.
 *
 * Repository logos are drawn for a page, not for a 20px row: most carry wide
 * transparent margins. Once the bytes have loaded (same origin, so the
 * canvas is not tainted) the margins are trimmed, and the mark is cut as
 * the left square of what remains. The original file is used as-is when
 * that is not possible (no canvas, an opaque image, a huge one).
 *
 * The logo is decoration: the project's name is always written next to it,
 * so the image carries an empty alt and the monogram is hidden from
 * assistive technology.
 */

import { useState } from "react";

import { useResolvedTheme } from "@/components/shell/ThemeControl";

type Size = "sm" | "md" | "lg";

type Variant = "wordmark" | "mark";

const SIZES: Record<Size, { box: string; pad: string; img: string; text: string }> = {
  sm: { box: "h-5 w-5 rounded-[5px]", pad: "p-[2px]", img: "h-4 max-w-[6rem]", text: "text-[0.625rem]" },
  md: { box: "h-7 w-7 rounded-[7px]", pad: "p-[3px]", img: "h-6 max-w-[8rem]", text: "text-[0.75rem]" },
  lg: { box: "h-10 w-10 rounded-[10px]", pad: "p-[5px]", img: "h-8 max-w-[12rem]", text: "text-[0.9375rem]" },
};

// A 404 is not cached by the browser; remembering it here stops every list
// re-render from asking again in the same session.
const missing = new Set<string>();

interface Cut {
  wordmark: string;
  mark: string;
}

// Trimmed renditions, per source URL, shared by every logo on the page.
const cuts = new Map<string, Cut | null>();

const MAX_PIXELS = 4_000_000;
const ALPHA_FLOOR = 12;

/** The image's opaque bounding box, then that box and its left square as
 * PNG data URLs. Null when the image cannot be read back. */
export function cut(image: HTMLImageElement): Cut | null {
  const width = image.naturalWidth;
  const height = image.naturalHeight;
  if (!width || !height || width * height > MAX_PIXELS) return null;
  try {
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d", { willReadFrequently: true });
    if (!context) return null;
    context.drawImage(image, 0, 0);
    const pixels = context.getImageData(0, 0, width, height).data;
    let top = height;
    let left = width;
    let right = -1;
    let bottom = -1;
    for (let y = 0; y < height; y += 1) {
      for (let x = 0; x < width; x += 1) {
        if (pixels[(y * width + x) * 4 + 3] > ALPHA_FLOOR) {
          if (x < left) left = x;
          if (x > right) right = x;
          if (y < top) top = y;
          if (y > bottom) bottom = y;
        }
      }
    }
    if (right < left || bottom < top) return null;
    const boxWidth = right - left + 1;
    const boxHeight = bottom - top + 1;
    const render = (sx: number, sw: number, outWidth: number, outHeight: number) => {
      const out = document.createElement("canvas");
      out.width = outWidth;
      out.height = outHeight;
      const draw = out.getContext("2d");
      if (!draw) return null;
      draw.drawImage(
        image,
        sx,
        top,
        sw,
        boxHeight,
        Math.round((outWidth - sw) / 2),
        Math.round((outHeight - boxHeight) / 2),
        sw,
        boxHeight,
      );
      return out.toDataURL("image/png");
    };
    const side = Math.min(boxWidth, boxHeight);
    const wordmark = render(left, boxWidth, boxWidth, boxHeight);
    const mark = render(left, side, boxHeight, boxHeight);
    return wordmark && mark ? { wordmark, mark } : null;
  } catch {
    return null;
  }
}

export function logoUrl(projectId: string, theme: "light" | "dark"): string {
  return `/v1/projects/${encodeURIComponent(projectId)}/logo?theme=${theme}`;
}

export function monogram(name: string): string {
  const words = name
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .split(/[\s_\-./]+/)
    .filter(Boolean);
  if (words.length === 0) return "?";
  if (words.length === 1) return words[0].slice(0, 2).toLocaleUpperCase();
  return (words[0][0] + words[1][0]).toLocaleUpperCase();
}

export function ProjectLogo({
  projectId,
  name,
  size = "sm",
  variant = "wordmark",
  className = "",
}: {
  projectId: string;
  /** The project's display name or key, for the monogram. */
  name: string;
  size?: Size;
  variant?: Variant;
  className?: string;
}) {
  const theme = useResolvedTheme();
  const src = logoUrl(projectId, theme);
  const [loaded, setLoaded] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const spec = SIZES[size];
  const absent = missing.has(src) || failed === src;
  const ready = (loaded === src || cuts.has(src)) && !absent;
  const rendition = cuts.get(src);
  const shown = ready ? (rendition ? rendition[variant] : src) : null;

  return (
    <span
      className={`inline-flex shrink-0 items-center ${className}`}
      data-testid="project-logo"
      data-state={shown ? "image" : "monogram"}
      data-variant={variant}
    >
      {shown ? (
        <img
          src={shown}
          alt=""
          decoding="async"
          className={
            variant === "mark"
              ? `${spec.box} ${spec.pad} border border-border bg-surface-2 object-contain`
              : `${spec.img} w-auto object-contain object-left`
          }
        />
      ) : (
        <span
          aria-hidden
          className={`inline-flex items-center justify-center bg-brand-soft font-semibold tracking-tight text-brand ${spec.box} ${spec.text}`}
        >
          {monogram(name)}
        </span>
      )}
      {shown || absent ? null : (
        // The fetch: never shown itself, only read once it has loaded.
        <img
          src={src}
          alt=""
          decoding="async"
          hidden
          onLoad={(event) => {
            if (!cuts.has(src)) cuts.set(src, cut(event.currentTarget));
            setLoaded(src);
          }}
          onError={() => {
            missing.add(src);
            setFailed(src);
          }}
        />
      )}
    </span>
  );
}
