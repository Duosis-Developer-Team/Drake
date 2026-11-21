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

export function SignInStory() {
  const t = useT("shell");
  return (
    <section data-testid="signin-hero" className="max-w-[44rem]">
      <p className="flex items-center gap-2.5 font-mono text-[11px] font-medium tracking-[0.18em] text-ink-muted uppercase">
        <span aria-hidden className="h-2 w-2 rounded-[2px] bg-gold" />
        {t("hero.eyebrow")}
      </p>

      <h2 className="mt-6 font-[family-name:var(--font-display)] text-[clamp(2.3rem,3.3vw,3.4rem)] leading-[1.05] font-extrabold tracking-[-0.04em] text-ink">
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

      <p className="mt-7 max-w-[34rem] text-[1.0625rem] leading-relaxed text-ink-secondary">
        {t("hero.body")}
      </p>

      <ol className="mt-12 grid gap-x-10 gap-y-7 sm:grid-cols-2">
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

      <ul className="mt-12 flex flex-wrap gap-x-6 gap-y-2 text-[0.8125rem] text-ink-muted">
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
