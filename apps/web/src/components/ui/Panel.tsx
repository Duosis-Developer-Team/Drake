/**
 * Panel — the one container in the product.
 *
 * Deliberately not "a card". A monitoring screen is a set of regions, and the
 * regions carry different weight: a dense table wants a plain bordered frame,
 * a signal wants a header with its unit and freshness, and a triage list
 * wants a coloured rail. Those are `tone` and `density`, not three components
 * and not eleven booleans.
 *
 * `flush` exists because a table inside padding is wrong: the row separators
 * have to reach the panel edge or the rows read as floating.
 */

import type { StatusTone } from "@/lib/design/status";
import { toneSpec } from "@/lib/design/status";

export type PanelTone = "default" | StatusTone;

/** Status on a PayFlow-style card is a quiet tinted edge, not a thick left
 *  rail — a 4px rail on a 24px radius bends into a crescent and reads broken. */
const RAIL: Partial<Record<PanelTone, string>> = {
  critical: "ring-1 ring-inset ring-critical/45",
  warning: "ring-1 ring-inset ring-warning/40",
  stale: "ring-1 ring-inset ring-stale/40",
  unknown: "",
  success: "",
  info: "",
};

const RADIUS = {
  panel: "rounded-[1.5rem]",
  canvas: "rounded-[1.75rem]",
} as const;

export type PanelSurface = "default" | "hero";

/** `hero` is the reference's dark "Total Balance" card: a lit gradient slab
 *  meant to hold one lighter card-in-card. Theme-invariant, so its own text
 *  must use `hero-ink`/`hero-ink-muted`. */
const SURFACE: Record<PanelSurface, string> = {
  default: "border border-border bg-surface shadow-panel",
  hero:
    "border border-white/[0.07] bg-[#141414] text-hero-ink bg-[radial-gradient(120%_140%_at_0%_0%,#343434_0%,#1c1c1c_45%,#121212_100%)] shadow-overlay",
};

export function Panel({
  children,
  tone = "default",
  surface = "default",
  flush = false,
  radius = "panel",
  className = "",
  "data-testid": testId,
  as: Element = "section",
  ...rest
}: {
  children: React.ReactNode;
  tone?: PanelTone;
  /** `"hero"` swaps the surface for the theme-invariant obsidian rail
   *  background — reserved for the one lead panel a screen is allowed
   *  (brief §14.2/§12.4's "hero" panel level). Every other caller keeps
   *  the default page-theme surface. */
  surface?: PanelSurface;
  /** Drop the body padding — for tables and anything edge-to-edge. */
  flush?: boolean;
  /** `"canvas"` is the one-dominant-surface-per-screen radius (brief §7.3) —
   *  reserved for a page's single lead panel, e.g. the Command Center
   *  verdict. Every other caller keeps the default. */
  radius?: keyof typeof RADIUS;
  className?: string;
  "data-testid"?: string;
  as?: "section" | "div" | "article";
  "aria-label"?: string;
  "aria-labelledby"?: string;
}) {
  return (
    <Element
      data-testid={testId}
      data-tone={tone}
      // `[&>*]:min-w-0` closes the flexbox horizontal-overflow class of bug at
      // its source: a flex child defaults to `min-width: auto`, so any wrapper
      // around a wide table grows past the panel and the PAGE scrolls
      // sideways instead of the table's own scroller doing it.
      className={`flex min-w-0 flex-col ${RADIUS[radius]} ${SURFACE[surface]} [&>*]:min-w-0 ${
        surface === "default" ? RAIL[tone] ?? "" : ""
      } ${flush ? "" : "gap-6 p-7"} ${className}`}
      {...rest}
    >
      {children}
    </Element>
  );
}

/**
 * A panel's header.
 *
 * `title` is the question the panel answers, not the name of the data source.
 * `meta` is where unit, window and freshness go, so a reader never has to
 * guess what a number is measured in.
 */
export function PanelHeader({
  title,
  description,
  meta,
  actions,
  id,
  level = 2,
  flush = false,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  meta?: React.ReactNode;
  actions?: React.ReactNode;
  id?: string;
  level?: 2 | 3 | 4;
  /** Match a `flush` panel: supply the padding the body dropped. */
  flush?: boolean;
}) {
  const Heading = `h${level}` as const;
  return (
    <div
      className={`flex flex-wrap items-start justify-between gap-x-4 gap-y-2 ${
        flush ? "border-b border-border px-7 pt-6 pb-5" : ""
      }`}
    >
      <div className="min-w-0">
        <Heading id={id} className="text-[1.0625rem] leading-6 font-semibold tracking-[-0.01em] text-ink">
          {title}
        </Heading>
        {description ? (
          <p className="mt-0.5 text-caption text-ink-muted">{description}</p>
        ) : null}
        {meta ? (
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-micro text-ink-muted">
            {meta}
          </div>
        ) : null}
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function PanelBody({
  children,
  className = "",
  flush = false,
}: {
  children: React.ReactNode;
  className?: string;
  flush?: boolean;
}) {
  return <div className={`min-w-0 flex-1 ${flush ? "" : "px-7 py-5"} ${className}`}>{children}</div>;
}

export function PanelFooter({ children }: { children: React.ReactNode }) {
  return (
    <div className="border-t border-border px-7 py-4 text-micro text-ink-muted">{children}</div>
  );
}

/**
 * A heading between panels.
 *
 * Sections are separated by a labelled rule rather than by whitespace: at
 * monitoring density, gaps alone stop reading as structure.
 */
export function SectionHeader({
  title,
  description,
  actions,
  id,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  id?: string;
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-2 pb-1">
      <div className="min-w-0">
        <h2 id={id} className="text-section font-semibold text-ink">
          {title}
        </h2>
        {description ? (
          <p className="mt-0.5 text-caption text-ink-secondary">{description}</p>
        ) : null}
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </div>
  );
}

/** Legacy shim: the pre-Sprint-13 `Card` API, on the new panel. */
export function Card({
  title,
  children,
  footer,
  "data-testid": testId,
}: {
  title?: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
  "data-testid"?: string;
}) {
  return (
    <Panel flush data-testid={testId}>
      {title ? <PanelHeader title={title} flush /> : null}
      <PanelBody>{children}</PanelBody>
      {footer ? <PanelFooter>{footer}</PanelFooter> : null}
    </Panel>
  );
}

export { toneSpec };
