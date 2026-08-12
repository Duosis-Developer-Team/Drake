"use client";

/**
 * Identifiers and time.
 *
 * The things an operator copies into a terminal or compares against a log
 * line. Two rules run through all of them:
 *
 *   Truncation is visual only. The full value is always in the DOM (title +
 *   the copy action), because a digest shortened to 12 characters in the
 *   markup is a digest nobody can use.
 *
 *   Relative time never appears alone. "4m ago" is for scanning; the exact
 *   UTC instant is one hover or one screen-reader stop away, because an
 *   incident timeline is evidence and "4m ago" is not.
 */

import { Check, Copy } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { MISSING, formatRelative, formatUtc } from "@/lib/design/format";

export function InlineCode({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <code
      className={`rounded bg-surface-2 px-1 py-0.5 font-mono text-micro text-ink-secondary ${className}`}
    >
      {children}
    </code>
  );
}

/**
 * An identifier with a copy action.
 *
 * `truncate` shortens the middle rather than the end: the tail of a digest or
 * a pod name is usually the part that distinguishes it from its neighbour.
 */
export function CopyableIdentifier({
  value,
  label,
  truncate,
  className = "",
}: {
  value: string | null | undefined;
  /** What this identifies, for the copy button's accessible name. */
  label: string;
  /** Characters to show. Omit to show all of it. */
  truncate?: number;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 1600);
    return () => clearTimeout(timer);
  }, [copied]);

  const copy = useCallback(() => {
    if (!value) return;
    navigator.clipboard
      ?.writeText(value)
      .then(() => setCopied(true))
      .catch(() => {
        // Clipboard access can be denied; the full value is still selectable.
      });
  }, [value]);

  if (!value) return <span className="text-ink-muted">{MISSING}</span>;

  const shown =
    truncate && value.length > truncate
      ? `${value.slice(0, Math.ceil(truncate / 2))}…${value.slice(-Math.floor(truncate / 2))}`
      : value;

  return (
    <span className={`inline-flex max-w-full items-center gap-1 ${className}`}>
      <code title={value} className="truncate font-mono text-micro text-ink-secondary">
        {shown}
      </code>
      <button
        type="button"
        onClick={copy}
        aria-label={copied ? `${label} copied` : `Copy ${label}`}
        className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded text-ink-muted transition-colors hover:bg-surface-hover hover:text-ink"
      >
        {copied ? (
          <Check className="h-3 w-3 text-healthy" aria-hidden />
        ) : (
          <Copy className="h-3 w-3" aria-hidden />
        )}
      </button>
      <span aria-live="polite" className="sr-only">
        {copied ? `${label} copied to clipboard` : ""}
      </span>
    </span>
  );
}

/**
 * One clock for the whole application.
 *
 * Every relative timestamp on screen needs to advance, and an inventory table
 * has hundreds of them. A `setInterval` per instance means hundreds of timers
 * waking the main thread out of phase with each other; this is a single
 * interval that exists only while something is subscribed to it.
 */
const clockSubscribers = new Set<() => void>();
let clockTimer: ReturnType<typeof setInterval> | null = null;
let clockNow = 0;

function subscribeToClock(onChange: () => void): () => void {
  clockSubscribers.add(onChange);
  if (clockTimer === null) {
    clockNow = Date.now();
    clockTimer = setInterval(() => {
      clockNow = Date.now();
      for (const subscriber of clockSubscribers) subscriber();
    }, 30_000);
  }
  return () => {
    clockSubscribers.delete(onChange);
    if (clockSubscribers.size === 0 && clockTimer !== null) {
      clearInterval(clockTimer);
      clockTimer = null;
    }
  };
}

/**
 * Relative time that stays honest.
 *
 * Renders the absolute value on the server and swaps to relative after mount:
 * "4m ago" computed during SSR is wrong by however long the response sat in
 * flight, and it is a guaranteed hydration mismatch.
 */
export function RelativeTime({
  value,
  className = "",
}: {
  value: string | null | undefined;
  className?: string;
}) {
  const [now, setNow] = useState<Date | null>(null);

  useEffect(() => {
    setNow(new Date());
    return subscribeToClock(() => setNow(new Date(clockNow)));
  }, []);

  if (!value) return <span className="text-ink-muted">{MISSING}</span>;
  const absolute = formatUtc(value);
  return (
    <time dateTime={value} title={absolute} className={`whitespace-nowrap ${className}`}>
      {now ? formatRelative(value, now) : absolute}
      <span className="sr-only"> ({absolute})</span>
    </time>
  );
}

/** The absolute instant, always UTC, always monospaced for column scanning. */
export function Timestamp({
  value,
  className = "",
}: {
  value: string | null | undefined;
  className?: string;
}) {
  if (!value) return <span className="text-ink-muted">{MISSING}</span>;
  return (
    <time dateTime={value} className={`font-mono text-micro whitespace-nowrap ${className}`}>
      {formatUtc(value)}
    </time>
  );
}

/**
 * How current a reading is.
 *
 * Deliberately not a green tick. The tone comes from the state the API
 * reported, and `fresh` gets a neutral dot rather than a healthy one: this
 * indicator answers "how old is this number", not "is the system well".
 */
export function FreshnessIndicator({
  asOf,
  state,
  source,
}: {
  asOf: string | null | undefined;
  /** The API's own word for it. */
  state?: "fresh" | "stale" | "unknown" | "not_configured" | string | null;
  source?: string | null;
}) {
  const stale = state === "stale";
  const unknown = state === "unknown" || !asOf;
  const dot = stale ? "bg-stale" : unknown ? "bg-unknown" : "bg-ink-muted";
  return (
    <span
      data-testid="freshness"
      className={`inline-flex items-center gap-1.5 text-micro ${
        stale ? "text-stale" : "text-ink-muted"
      }`}
    >
      <span aria-hidden className={`h-1.5 w-1.5 shrink-0 rounded-full ${dot}`} />
      {unknown ? (
        <>freshness unknown</>
      ) : (
        <>
          {stale ? "stale as of" : "as of"} <RelativeTime value={asOf} />
        </>
      )}
      {source ? <span className="text-ink-muted">· {source}</span> : null}
    </span>
  );
}
