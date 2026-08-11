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

const RAIL: Partial<Record<PanelTone, string>> = {
  critical: "border-l-4 border-l-critical",
  warning: "border-l-4 border-l-warning",
  stale: "border-l-4 border-l-stale",
  unknown: "border-l-4 border-l-unknown",
  success: "border-l-4 border-l-healthy",
  info: "border-l-4 border-l-info",
};

export function Panel({
  children,
  tone = "default",
  flush = false,
  className = "",
  "data-testid": testId,
  as: Element = "section",
  ...rest
}: {
  children: React.ReactNode;
  tone?: PanelTone;
  /** Drop the body padding — for tables and anything edge-to-edge. */
  flush?: boolean;
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
      className={`flex min-w-0 flex-col rounded-panel border border-border bg-surface shadow-panel ${
        RAIL[tone] ?? ""
      } ${flush ? "" : "gap-3 p-4"} ${className}`}
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
        flush ? "border-b border-border px-4 py-3" : ""
      }`}
    >
      <div className="min-w-0">
        <Heading id={id} className="text-section font-semibold text-ink">
          {title}
        </Heading>
        {description ? (
          <p className="mt-0.5 text-caption text-ink-secondary">{description}</p>
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
  return <div className={`min-w-0 flex-1 ${flush ? "" : "px-4 py-3"} ${className}`}>{children}</div>;
}

export function PanelFooter({ children }: { children: React.ReactNode }) {
  return (
    <div className="border-t border-border px-4 py-2 text-micro text-ink-muted">{children}</div>
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
    <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-2 border-b border-border pb-2">
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
