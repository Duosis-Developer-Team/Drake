/**
 * The Tracery logo.
 *
 * The mark is a trace waterfall: a root span across the top, a child span
 * hanging off it, and a last span in gold — the slowest call, the one the
 * product exists to find. It is drawn inline as SVG from theme tokens
 * (`--brand`, `--gold`), so it follows light and dark without a second asset
 * and costs no request.
 *
 * Geometry comes from the approved artwork ("Katmanlı iz ve altın vurgu",
 * 2026-11-21) at a 450 × 200 viewBox. Do not redraw it; change it here only
 * when the artwork changes.
 *
 * The wordmark is the product name in Plus Jakarta Sans ExtraBold, lowercase,
 * tight tracking — the same setting as the artwork. It is real text so it
 * stays crisp at every size and is read by assistive technology once (the
 * mark itself is decorative).
 */

const MARK_RATIO = 450 / 200;

function MarkShapes() {
  return (
    <>
      <rect x="0" y="0" width="450" height="52" rx="9" fill="var(--brand)" />
      <rect x="93" y="40" width="35" height="50" fill="var(--brand)" />
      <rect x="93" y="80" width="310" height="46" rx="8" fill="var(--brand)" />
      <rect x="176" y="118" width="22" height="75" fill="var(--brand)" />
      <rect x="211" y="156" width="145" height="44" rx="8" fill="var(--gold)" />
    </>
  );
}

/** The mark alone, for the collapsed rail and small surfaces. */
export function TraceryMark({ height = 14, className = "" }: { height?: number; className?: string }) {
  return (
    <span
      role="img"
      aria-label="Tracery" // i18n-ignore: the product name is a proper noun
      data-testid="brand-mark"
      className={`inline-flex shrink-0 ${className}`}
      style={{ height, width: Math.round(height * MARK_RATIO) }}
    >
      <svg viewBox="0 0 450 200" width="100%" height="100%" aria-hidden focusable="false">
        <MarkShapes />
      </svg>
    </span>
  );
}

/**
 * Mark plus wordmark. `size` is the cap height of the wordmark in px; the
 * mark is drawn at the proportion of the approved lockup.
 */
export function TraceryWordmark({ size = 20, className = "" }: { size?: number; className?: string }) {
  const markHeight = Math.round(size * 0.62);
  return (
    <span
      data-testid="brand-wordmark"
      className={`inline-flex shrink-0 items-center text-ink ${className}`}
      style={{ gap: Math.round(size * 0.42) }}
    >
      <svg
        viewBox="0 0 450 200"
        height={markHeight}
        width={Math.round(markHeight * MARK_RATIO)}
        aria-hidden
        focusable="false"
      >
        <MarkShapes />
      </svg>
      <span
        className="font-[family-name:var(--font-display)] leading-none font-extrabold"
        style={{ fontSize: size, letterSpacing: "-0.045em" }}
      >
        tracery{/* i18n-ignore: the product name is a proper noun */}
      </span>
    </span>
  );
}
