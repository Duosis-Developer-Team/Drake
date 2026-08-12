/**
 * The Drake logo.
 *
 * Rendered as a CSS background rather than two `<img>` tags, for one reason
 * that matters: a hidden `<img>` is still fetched. Swapping the themes with
 * `dark:hidden` would download both the light and the dark wordmark on every
 * load, and neither is small. A background-image behind a `.dark` selector
 * fetches only the one that is actually painted.
 *
 * The box has explicit dimensions, so the shell never reflows when the asset
 * lands, and the accessible name lives on the element — the artwork itself
 * carries no text for a reader to recover.
 *
 * Both files are lossless crops of the authoritative masters (see
 * scripts/build_brand_assets.py). Nothing here tints, shadows, outlines or
 * otherwise decorates them.
 */

/** Intrinsic aspect ratios of the derivatives, from the masters' ink boxes. */
const WORDMARK_RATIO = 1391 / 248;
const MARK_RATIO = 415 / 248;

export function DrakeWordmark({
  height = 26,
  className = "",
}: {
  height?: number;
  className?: string;
}) {
  return (
    <span
      role="img"
      aria-label="Drake"
      data-testid="drake-wordmark"
      style={{ height, width: Math.round(height * WORDMARK_RATIO) }}
      className={`block shrink-0 bg-[url('/brand/drake-wordmark-light.webp')] bg-contain bg-left bg-no-repeat dark:bg-[url('/brand/drake-wordmark-dark.webp')] ${className}`}
    />
  );
}

/**
 * The D-and-serpent lockup, for the collapsed rail.
 *
 * A deterministic crop of the same master at the column of minimum ink
 * between the serpent's head and the R — not a redrawn icon.
 */
export function DrakeMark({
  height = 26,
  className = "",
}: {
  height?: number;
  className?: string;
}) {
  return (
    <span
      role="img"
      aria-label="Drake"
      data-testid="drake-mark"
      style={{ height, width: Math.round(height * MARK_RATIO) }}
      className={`block shrink-0 bg-[url('/brand/drake-mark-light.webp')] bg-contain bg-center bg-no-repeat dark:bg-[url('/brand/drake-mark-dark.webp')] ${className}`}
    />
  );
}
