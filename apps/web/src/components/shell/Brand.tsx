/**
 * The Tracesis logo.
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
 * The wordmark is "tracesis" in Plus Jakarta Sans ExtraBold, lowercase, tight
 * tracking. As in the Duosis wordmark, "sis" is set apart in colour and its
 * i carries a slanted cut instead of a dot — here cobalt letters and a gold
 * cut, the two colours of the mark. The letters are real text (crisp at any
 * size); assistive technology reads the name once, from the label.
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
export function TracesisMark({ height = 14, className = "" }: { height?: number; className?: string }) {
  return (
    <span
      role="img"
      aria-label="Tracesis" // i18n-ignore: the product name is a proper noun
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
 * Mark plus wordmark. `size` is the font size of the wordmark in px; the
 * mark is drawn at the proportion of the approved lockup.
 */
export function TracesisWordmark({ size = 20, className = "" }: { size?: number; className?: string }) {
  const markHeight = Math.round(size * 0.62);
  return (
    <span
      role="img"
      aria-label="Tracesis" // i18n-ignore: the product name is a proper noun
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
        aria-hidden
        className="font-[family-name:var(--font-display)] leading-none font-extrabold"
        style={{ fontSize: size, letterSpacing: "-0.045em" }}
      >
        {/* i18n-ignore: the product name is a proper noun */}
        trace
        <span style={{ color: "var(--brand)" }}>
          s
          <span className="relative inline-block">
            {"\u0131"}
            <span
              className="absolute"
              style={{
                left: "0.015em",
                top: "0.01em",
                width: "0.17em",
                height: "0.23em",
                background: "var(--gold)",
                clipPath: "polygon(0 38%, 100% 0, 100% 100%, 0 100%)",
              }}
            />
          </span>
          s
        </span>
      </span>
    </span>
  );
}
