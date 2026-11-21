"use client";

/**
 * The left half of the sign-in screen: what Tracesis is, at a glance.
 *
 * It is always the deep night panel of the brand, in light and dark alike,
 * so the cobalt-and-gold mark sits on the background it was drawn for. The
 * product preview is illustrative — a synthetic trace and synthetic numbers,
 * labelled as such — because nothing about a tenant may be shown before
 * someone has signed in.
 */

import {
  Activity,
  Bot,
  DatabaseBackup,
  Lock,
  Radar,
  ShieldCheck,
  Sparkles,
  Target,
  Waypoints,
} from "lucide-react";

import { TracesisWordmark } from "@/components/shell/Brand";
import { useT } from "@/lib/i18n";

/** Brand tokens for the night panel, whatever the page theme is. */
const NIGHT = {
  "--brand": "#8197ff",
  "--gold": "#e9c46a",
  "--color-ink": "#f4f2ff",
  "--text-primary": "#f4f2ff",
} as React.CSSProperties;

const SPANS = [
  { label: "api-gateway", start: 0, width: 100, tone: "brand" },
  { label: "checkout", start: 4, width: 82, tone: "brand" },
  { label: "inventory.reserve", start: 9, width: 18, tone: "soft" },
  { label: "payments.charge", start: 30, width: 34, tone: "soft" },
  { label: "postgres SELECT", start: 33, width: 9, tone: "muted" },
  { label: "claude · plan", start: 66, width: 22, tone: "gold" },
] as const;

function TracePreview() {
  const t = useT("shell");
  return (
    <div className="relative rounded-2xl border border-white/10 bg-white/[0.04] p-4 shadow-2xl shadow-black/40 backdrop-blur-md">
      <div className="mb-3 flex items-center justify-between text-[11px] text-white/60">
        <span className="flex items-center gap-2">
          <span className="flex gap-1">
            <span className="h-2 w-2 rounded-full bg-white/15" />
            <span className="h-2 w-2 rounded-full bg-white/15" />
            <span className="h-2 w-2 rounded-full bg-white/15" />
          </span>
          <span className="font-mono">{t("hero.preview.trace")} 4bf92f35</span>
        </span>
        <span className="flex items-center gap-3">
          <span className="text-[10px] tracking-wide text-white/30 uppercase">
            {t("hero.preview.illustrative")}
          </span>
          <span className="flex items-center gap-1.5 font-medium text-emerald-300">
            <span className="relative flex h-1.5 w-1.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-300 opacity-70" />
              <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-300" />
            </span>
            {t("hero.preview.live")}
          </span>
        </span>
      </div>
      <div className="relative space-y-2 overflow-hidden">
        {SPANS.map((span, i) => (
          <div
            key={span.label}
            className="grid grid-cols-[9rem_minmax(0,1fr)] items-center gap-3"
          >
            <span
              className="truncate font-mono text-[11px] text-white/55"
              style={{ paddingLeft: Math.min(i, 3) * 8 }}
            >
              {span.label}
            </span>
            <span className="relative h-3.5">
              <span
                className="absolute inset-y-0 origin-left rounded-[4px] motion-safe:animate-[signin-grow_900ms_cubic-bezier(.2,.8,.2,1)_both]"
                style={{
                  left: `${span.start}%`,
                  width: `${span.width}%`,
                  animationDelay: `${150 + i * 120}ms`,
                  background:
                    span.tone === "gold"
                      ? "linear-gradient(90deg, #e9c46a, #f3d58c)"
                      : span.tone === "brand"
                        ? "linear-gradient(90deg, #5b72f2, #8197ff)"
                        : span.tone === "soft"
                          ? "rgba(129,151,255,0.55)"
                          : "rgba(255,255,255,0.22)",
                  boxShadow:
                    span.tone === "gold"
                      ? "0 0 18px rgba(233,196,106,0.45)"
                      : undefined,
                }}
              />
            </span>
          </div>
        ))}
        <span
          aria-hidden
          className="pointer-events-none absolute inset-y-0 w-16 bg-gradient-to-r from-transparent via-white/10 to-transparent motion-safe:animate-[signin-scan_4.5s_linear_infinite]"
        />
      </div>
    </div>
  );
}

function Chip({
  icon: Icon,
  label,
  value,
  accent,
  className,
  delay,
  children,
}: {
  icon: typeof Activity;
  label: string;
  value: string;
  accent: string;
  className: string;
  delay: string;
  children?: React.ReactNode;
}) {
  return (
    <div
      className={`absolute hidden rounded-xl border border-white/10 bg-[#0e1430]/85 px-3 py-2.5 shadow-xl shadow-black/40 backdrop-blur-md xl:block motion-safe:animate-[signin-float_7s_ease-in-out_infinite] ${className}`}
      style={{ animationDelay: delay }}
    >
      <div className="flex items-center gap-2.5">
        <span
          className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-lg"
          style={{ background: `${accent}22`, color: accent }}
        >
          <Icon aria-hidden className="h-3.5 w-3.5" />
        </span>
        <div className="min-w-0">
          <p className="text-[10px] font-medium tracking-wide text-white/50 uppercase">
            {label}
          </p>
          <p className="text-[13px] font-semibold whitespace-nowrap text-white/90 tabular-nums">
            {value}
          </p>
        </div>
        {children}
      </div>
    </div>
  );
}

function Sparkline() {
  const points = [14, 12, 13, 9, 11, 7, 9, 6, 8, 5];
  const d = points
    .map((y, i) => `${i === 0 ? "M" : "L"}${i * 7} ${y}`)
    .join(" ");
  return (
    <svg
      width="64"
      height="18"
      viewBox="0 0 63 18"
      aria-hidden
      className="ml-1"
    >
      <path
        d={d}
        fill="none"
        stroke="#8197ff"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

const FEATURES = [
  { key: "health", icon: Activity },
  { key: "traces", icon: Waypoints },
  { key: "objectives", icon: Target },
  { key: "agents", icon: Bot },
] as const;

export function SignInHero() {
  const t = useT("shell");
  return (
    <section
      aria-label="Tracesis" // i18n-ignore: the product name is a proper noun
      className="relative isolate hidden overflow-hidden bg-[#070a17] text-white lg:flex lg:flex-col"
      style={NIGHT}
      data-testid="signin-hero"
    >
      {/* Light: a cobalt glow top left, a faint gold one low right, and a
          grid that fades out — depth without a picture to load. */}
      <div aria-hidden className="absolute inset-0 -z-10">
        <div className="absolute -top-40 -left-40 h-[36rem] w-[36rem] rounded-full bg-[#3f57e0]/30 blur-[120px]" />
        <div className="absolute right-[-10rem] bottom-[-12rem] h-[30rem] w-[30rem] rounded-full bg-[#e9c46a]/12 blur-[120px]" />
        <svg
          className="absolute inset-0 h-full w-full opacity-[0.07]"
          aria-hidden
        >
          <defs>
            <pattern
              id="signin-grid"
              width="44"
              height="44"
              patternUnits="userSpaceOnUse"
            >
              <path
                d="M44 0H0V44"
                fill="none"
                stroke="white"
                strokeWidth="0.6"
              />
            </pattern>
            <radialGradient id="signin-fade" cx="35%" cy="40%" r="75%">
              <stop offset="0%" stopColor="white" />
              <stop offset="100%" stopColor="black" />
            </radialGradient>
            <mask id="signin-mask">
              <rect width="100%" height="100%" fill="url(#signin-fade)" />
            </mask>
          </defs>
          <rect
            width="100%"
            height="100%"
            fill="url(#signin-grid)"
            mask="url(#signin-mask)"
          />
        </svg>
      </div>

      <div className="flex flex-1 flex-col px-12 pt-10 pb-8 xl:px-16">
        <TracesisWordmark size={26} />

        <div className="mt-auto max-w-2xl pt-12">
          <span className="inline-flex items-center gap-2 rounded-full border border-white/12 bg-white/[0.05] px-3 py-1 text-[12px] font-medium text-white/75">
            <Sparkles aria-hidden className="h-3.5 w-3.5 text-[#e9c46a]" />
            {t("hero.eyebrow")}
          </span>
          <h2 className="mt-5 font-[family-name:var(--font-display)] text-[clamp(2.1rem,3.2vw,3.25rem)] leading-[1.08] font-extrabold tracking-[-0.035em]">
            {t("hero.title")}
            <br />
            <span className="bg-gradient-to-r from-[#a8b6ff] via-[#8197ff] to-[#e9c46a] bg-clip-text text-transparent">
              {t("hero.titleAccent")}
            </span>
          </h2>
          <p className="mt-5 max-w-xl text-[15px] leading-relaxed text-white/65">
            {t("hero.body")}
          </p>
        </div>

        <div className="relative mt-10 mr-6 max-w-2xl xl:mr-24">
          <TracePreview />
          <Chip
            icon={ShieldCheck}
            label={t("hero.preview.slo")}
            value={t("hero.preview.sloValue")}
            accent="#5fd4a0"
            className="-top-9 right-[-4.5rem]"
            delay="0s"
          />
          <Chip
            icon={Activity}
            label={t("hero.preview.latency")}
            value="182 ms"
            accent="#8197ff"
            className="-bottom-7 left-[-1.5rem]"
            delay="1.2s"
          >
            <Sparkline />
          </Chip>
          <Chip
            icon={Bot}
            label={t("hero.preview.agents")}
            value={t("hero.preview.agentsValue")}
            accent="#e9c46a"
            className="right-[-5.5rem] -bottom-10"
            delay="2.4s"
          />
        </div>

        <ul className="mt-12 grid max-w-2xl grid-cols-2 gap-x-8 gap-y-5">
          {FEATURES.map(({ key, icon: Icon }) => (
            <li key={key} className="flex gap-3">
              <span className="mt-0.5 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-white/10 bg-white/[0.05] text-[#a8b6ff]">
                <Icon aria-hidden className="h-4 w-4" />
              </span>
              <span>
                <span className="block text-[14px] font-semibold text-white/90">
                  {t(`hero.features.${key}.title`)}
                </span>
                <span className="mt-0.5 block text-[13px] leading-snug text-white/55">
                  {t(`hero.features.${key}.body`)}
                </span>
              </span>
            </li>
          ))}
        </ul>

        <div className="mt-auto flex flex-wrap items-center gap-x-6 gap-y-2 pt-10 text-[12px] text-white/45">
          <span className="inline-flex items-center gap-1.5">
            <DatabaseBackup aria-hidden className="h-3.5 w-3.5" />
            {t("hero.trust.selfHosted")}
          </span>
          <span className="inline-flex items-center gap-1.5">
            <Lock aria-hidden className="h-3.5 w-3.5" />
            {t("hero.trust.noContent")}
          </span>
          <span className="inline-flex items-center gap-1.5">
            <Radar aria-hidden className="h-3.5 w-3.5" />
            {t("hero.trust.otel")}
          </span>
          <span className="ml-auto">{t("hero.footer")}</span>
        </div>
      </div>
    </section>
  );
}
