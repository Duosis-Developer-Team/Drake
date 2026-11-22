"use client";

/**
 * A project's own logo, or its monogram.
 *
 * The image comes from the project's repository (`.duosis/media/logo-*.png`,
 * or a site's `public/brand/<repo>-logo*.png`) through
 * `GET /v1/projects/{id}/logo`, which answers 204 when there is nothing safe
 * to show. One fetch per URL is shared by every logo on the page, and a 204
 * is not an error, so a list of logo-less projects leaves the console
 * clean. The monogram is drawn first and stays until the image is ready, so
 * a slow or missing logo never shifts the row or leaves a broken image.
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

import { useEffect, useState } from "react";

import { useResolvedTheme } from "@/components/shell/ThemeControl";

type Size = "sm" | "md" | "lg" | "icon";

type Variant = "wordmark" | "mark";

const SIZES: Record<
  Size,
  { box: string; pad: string; img: string; text: string; frame?: string }
> = {
  sm: {
    box: "h-5 w-5 rounded-[5px]",
    pad: "p-[2px]",
    img: "h-4 max-w-[6rem]",
    text: "text-[0.625rem]",
  },
  md: {
    box: "h-7 w-7 rounded-[7px]",
    pad: "p-[3px]",
    img: "h-6 max-w-[8rem]",
    text: "text-[0.75rem]",
  },
  lg: {
    box: "h-10 w-10 rounded-[10px]",
    pad: "p-[5px]",
    img: "h-8 max-w-[12rem]",
    text: "text-[0.9375rem]",
  },
  /* The mark alone, unframed, filling an app icon the caller draws. */
  icon: {
    box: "h-14 w-14 rounded-[14px]",
    pad: "p-0",
    img: "h-14 max-w-[14rem]",
    text: "text-[1.375rem]",
    frame: "",
  },
};

interface Cut {
  wordmark: string;
  mark: string;
}

// One load per URL, shared by every logo on the page and kept for the
// session; `settled` lets a later mount draw synchronously.
const loads = new Map<string, Promise<Cut | null>>();
const settled = new Map<string, Cut | null>();

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
    const render = (
      sx: number,
      sw: number,
      outWidth: number,
      outHeight: number,
    ) => {
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

function decode(url: string): Promise<HTMLImageElement | null> {
  return new Promise((resolve) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => resolve(null);
    image.src = url;
  });
}

/** The logo at *src*, trimmed, or null for "draw the monogram". */
export function loadLogo(src: string): Promise<Cut | null> {
  let pending = loads.get(src);
  if (!pending) {
    pending = (async (): Promise<Cut | null> => {
      try {
        const response = await fetch(src, { credentials: "include" });
        if (response.status !== 200) return null;
        const blob = await response.blob();
        if (!blob.type.startsWith("image/")) return null;
        const url = URL.createObjectURL(blob);
        const image = await decode(url);
        if (!image) {
          URL.revokeObjectURL(url);
          return null;
        }
        const trimmed = cut(image);
        if (trimmed) {
          URL.revokeObjectURL(url);
          return trimmed;
        }
        return { wordmark: url, mark: url };
      } catch {
        return null;
      }
    })().then((result) => {
      settled.set(src, result);
      return result;
    });
    loads.set(src, pending);
  }
  return pending;
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
  const [result, setResult] = useState<{ src: string; cut: Cut | null } | null>(
    () => (settled.has(src) ? { src, cut: settled.get(src) ?? null } : null),
  );
  useEffect(() => {
    let active = true;
    void loadLogo(src).then((loaded) => {
      if (active) setResult({ src, cut: loaded });
    });
    return () => {
      active = false;
    };
  }, [src]);
  const spec = SIZES[size];
  const rendition = result && result.src === src ? result.cut : null;
  const shown = rendition ? rendition[variant] : null;

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
              ? `${spec.box} ${spec.pad} ${spec.frame ?? "border border-border bg-surface-2"} object-contain`
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
    </span>
  );
}
