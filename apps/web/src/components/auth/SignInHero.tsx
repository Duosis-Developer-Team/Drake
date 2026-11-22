"use client";

/**
 * The sign-in page's story and its backdrop.
 *
 * Deliberately quiet: one surface for the whole page in either theme, the
 * brand's own motif — the stacked spans of the mark, the slow one in gold —
 * drawn faintly behind both halves so they read as one page, a headline in
 * plain ink with a single gold cut under the words that matter, and the
 * capabilities as an editorial list. No glow, no gradient type, no mock data.
 */

import { Lock, Radar, Server } from "lucide-react";

import { useT } from "@/lib/i18n";

/**
 * Spans of a trace, kept clear of the story column: a waterfall that runs
 * down the right edge, behind and past the form, and settles along the foot
 * of the page. The one gold span is the slow call, beside the card's edge.
 */
const BARS: { x: number; y: number; w: number; gold?: boolean }[] = [
  { x: 56, y: 12, w: 40 },
  { x: 62, y: 17, w: 24 },
  { x: 66, y: 22, w: 33 },
  { x: 71, y: 28, w: 18 },
  { x: 74, y: 34, w: 26 },
  { x: 86, y: 40, w: 11, gold: true },
  { x: 78, y: 46, w: 22 },
  { x: 81, y: 52, w: 15 },
  { x: 84, y: 58, w: 16 },
  { x: 76, y: 66, w: 24 },
  { x: 88, y: 73, w: 12 },
  { x: 52, y: 88, w: 30 },
  { x: 64, y: 93, w: 36 },
];

export function SignInBackdrop() {
  return (
    <div
      aria-hidden
      className="pointer-events-none absolute inset-0 overflow-hidden"
    >
      {/* A hint of colour where the eye starts, nothing more. */}
      <div
        className="absolute inset-0"
        style={{
          background:
            "radial-gradient(60rem 36rem at 12% -8%, color-mix(in srgb, var(--brand) 9%, transparent), transparent 70%)",
        }}
      />
      {/* Fine dots, fading toward the edges. */}
      <div
        className="absolute inset-0 opacity-[0.55]"
        style={{
          backgroundImage:
            "radial-gradient(color-mix(in srgb, var(--text-muted) 26%, transparent) 1px, transparent 1px)",
          backgroundSize: "22px 22px",
          maskImage:
            "radial-gradient(ellipse 70% 60% at 40% 40%, black 30%, transparent 85%)",
          WebkitMaskImage:
            "radial-gradient(ellipse 70% 60% at 40% 40%, black 30%, transparent 85%)",
        }}
      />
      <svg
        className="absolute inset-0 h-full w-full"
        preserveAspectRatio="none"
        viewBox="0 0 100 100"
      >
        {BARS.map((bar, i) => (
          <rect
            key={i}
            x={bar.x}
            y={bar.y}
            width={bar.w}
            height={1.15}
            rx={0.55}
            fill={bar.gold ? "var(--gold)" : "var(--brand)"}
            opacity={bar.gold ? 0.32 : 0.05 + (i % 4) * 0.012}
          />
        ))}
      </svg>
    </div>
  );
}

const FEATURES = ["health", "traces", "objectives", "agents"] as const;

/**
 * The mark, drawn large as what it is: a trace. The same five shapes as the
 * logo (Brand.tsx, 450 × 200), each span growing in from the left in turn,
 * then named — the request, the service it calls and, in gold, the slowest
 * call. A faint time grid sits behind, as in any waterfall.
 */
const SPANS = [
  { x: 0, y: 0, w: 450, h: 52, rx: 9, gold: false, delay: 0 },
  { x: 93, y: 80, w: 310, h: 46, rx: 8, gold: false, delay: 320 },
  { x: 211, y: 156, w: 145, h: 44, rx: 8, gold: true, delay: 640 },
];
const STEMS = [
  { x: 93, y: 40, w: 35, h: 50, delay: 260 },
  { x: 176, y: 118, w: 22, h: 45, delay: 580 },
];
const LABELS = [
  { key: "root", x: 466, y: 26, delay: 900 },
  { key: "child", x: 419, y: 103, delay: 1050 },
  { key: "slow", x: 372, y: 178, delay: 1200 },
] as const;

function BrandTrace() {
  const t = useT("shell");
  const grow = (delay: number, axis: "x" | "y") => ({
    transformBox: "fill-box" as const,
    transformOrigin: axis === "x" ? "left center" : "center top",
    animation: `${axis === "x" ? "signin-grow" : "signin-drop"} .7s cubic-bezier(.2,.8,.2,1) ${delay}ms both`,
  });
  return (
    <figure className="m-0" data-testid="signin-brand-trace">
      <svg
        viewBox="-4 -14 584 232"
        className="block h-auto w-full max-w-[24rem]"
        role="img"
        aria-label={t("hero.mark.caption")}
      >
        {/* The time grid: hairlines every 50 units under the spans. */}
        {Array.from({ length: 10 }, (_, i) => (
          <line
            key={i}
            x1={i * 50}
            x2={i * 50}
            y1={-10}
            y2={210}
            stroke="var(--border-subtle)"
            strokeDasharray="2 5"
            vectorEffect="non-scaling-stroke"
          />
        ))}
        {STEMS.map((stem, i) => (
          <rect key={`s${i}`} x={stem.x} y={stem.y} width={stem.w} height={stem.h} fill="var(--brand)" style={grow(stem.delay, "y")} />
        ))}
        {SPANS.map((span, i) => (
          <rect
            key={`b${i}`}
            x={span.x}
            y={span.y}
            width={span.w}
            height={span.h}
            rx={span.rx}
            fill={span.gold ? "var(--gold)" : "var(--brand)"}
            style={grow(span.delay, "x")}
          />
        ))}
        {LABELS.map((label) => (
          <g key={label.key} style={{ animation: `fade-in .5s ease-out ${label.delay}ms both` }}>
            <line
              x1={label.x - 12}
              x2={label.x - 4}
              y1={label.y}
              y2={label.y}
              stroke={label.key === "slow" ? "var(--gold)" : "var(--text-muted)"}
              strokeWidth="1.5"
              vectorEffect="non-scaling-stroke"
            />
            <text
              x={label.x}
              y={label.y}
              dominantBaseline="central"
              className="font-mono"
              fontSize="17"
              fontWeight={label.key === "slow" ? 700 : 500}
              letterSpacing="0.02em"
              fill={label.key === "slow" ? "var(--gold)" : "var(--text-secondary)"}
            >
              {t(`hero.mark.${label.key}`)}
            </text>
          </g>
        ))}
      </svg>
      <figcaption className="mt-3 max-w-[30rem] text-[0.8125rem] leading-relaxed text-ink-muted">
        {t("hero.mark.caption")}
      </figcaption>
    </figure>
  );
}

export function SignInStory() {
  const t = useT("shell");
  return (
    <section data-testid="signin-hero" className="max-w-[44rem]">
      <BrandTrace />

      <h2 className="mt-8 font-[family-name:var(--font-display)] text-[clamp(2.1rem,3vw,3.05rem)] leading-[1.05] font-extrabold tracking-[-0.04em] text-ink">
        {t("hero.titleLead")}
        <br />
        {/* The dash stays with the marked phrase, never opening a line. */}
        <span className="whitespace-nowrap">
          <span className="relative inline-block">
            {t("hero.titleMark")}
            {/* The brand's gold cut, under the one phrase that matters. */}
            <span
              aria-hidden
              className="absolute -bottom-[0.06em] left-0 h-[0.12em] w-full bg-gold"
              style={{ clipPath: "polygon(0 35%, 100% 0, 100% 100%, 0 100%)" }}
            />
          </span>
          <span className="font-bold text-ink-muted"> —</span>
        </span>{" "}
        <span className="font-bold text-ink-muted">{t("hero.titleTail")}</span>
      </h2>

      <p className="mt-6 max-w-[34rem] text-[1rem] leading-relaxed text-ink-secondary">
        {t("hero.body")}
      </p>

      <ol className="mt-10 grid gap-x-10 gap-y-6 sm:grid-cols-2">
        {FEATURES.map((key, i) => (
          <li key={key} className="border-t border-border pt-4">
            <p className="flex items-baseline gap-3">
              <span className="font-mono text-[11px] font-semibold text-brand tabular-nums">
                {String(i + 1).padStart(2, "0")}
              </span>
              <span className="text-[0.9375rem] font-semibold text-ink">
                {t(`hero.features.${key}.title`)}
              </span>
            </p>
            <p className="mt-1.5 pl-[1.85rem] text-[0.875rem] leading-snug text-ink-secondary">
              {t(`hero.features.${key}.body`)}
            </p>
          </li>
        ))}
      </ol>

      <ul className="mt-9 flex flex-wrap gap-x-6 gap-y-2 text-[0.8125rem] text-ink-muted">
        <li className="inline-flex items-center gap-1.5">
          <Server aria-hidden className="h-3.5 w-3.5" />
          {t("hero.trust.selfHosted")}
        </li>
        <li className="inline-flex items-center gap-1.5">
          <Lock aria-hidden className="h-3.5 w-3.5" />
          {t("hero.trust.noContent")}
        </li>
        <li className="inline-flex items-center gap-1.5">
          <Radar aria-hidden className="h-3.5 w-3.5" />
          {t("hero.trust.otel")}
        </li>
      </ul>
    </section>
  );
}
