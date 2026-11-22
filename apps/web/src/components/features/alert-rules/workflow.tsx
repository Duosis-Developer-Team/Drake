"use client";

/**
 * The composer's workflow pieces: a step on the rail (a node), and the
 * choice tiles inside one. A rule reads top to bottom like the automation it
 * is — where, what, when, then what — each step a card on one connected
 * rail, its marker filled once the step is complete.
 *
 * Presentation only: every value still comes from, and goes to, the
 * composer's own state.
 */

import { Check, ChevronDown, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

export type NodeState = "done" | "todo" | "invalid";

export function FlowNode({
  index,
  icon: Icon,
  title,
  summary,
  state,
  open,
  onToggle,
  badge,
  last = false,
  testId,
  children,
}: {
  index: number;
  icon: LucideIcon;
  title: string;
  /** What the step says when folded. */
  summary?: ReactNode;
  state: NodeState;
  open: boolean;
  onToggle: () => void;
  badge?: ReactNode;
  last?: boolean;
  testId?: string;
  children: ReactNode;
}) {
  const marker =
    state === "done"
      ? "border-brand-accent bg-brand-accent text-ink-inverse"
      : state === "invalid"
        ? "border-warning bg-warning-soft text-warning"
        : "border-border-strong bg-surface text-ink-secondary";
  return (
    <li className="relative pb-4 pl-14 last:pb-0" data-testid={testId}>
      {!last ? (
        <span
          aria-hidden
          className={`absolute top-[3.25rem] bottom-0 left-[1.3125rem] w-0.5 rounded-full ${
            state === "done" ? "bg-brand-accent/55" : "bg-border"
          }`}
        />
      ) : null}
      <span
        aria-hidden
        className={`absolute top-2.5 left-0 grid h-11 w-11 place-items-center rounded-full border-2 font-mono text-caption font-semibold transition-colors ${marker}`}
      >
        {state === "done" ? <Check className="h-4 w-4" strokeWidth={3} /> : index}
      </span>
      <section
        className={`rounded-2xl border bg-surface transition-[border-color,box-shadow] ${
          open ? "border-border-strong shadow-[0_14px_32px_-24px_rgb(0_0_0/0.45)]" : "border-border"
        }`}
      >
        <h3 className="m-0">
          <button
            type="button"
            aria-expanded={open}
            onClick={onToggle}
            className="flex w-full items-center gap-3 rounded-2xl px-4 py-3 text-left outline-none focus-visible:ring-2 focus-visible:ring-focus"
          >
            <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-surface-2 text-brand-accent">
              <Icon aria-hidden className="h-[18px] w-[18px]" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-body font-semibold text-ink">{title}</span>
              {!open && summary ? (
                <span className="mt-0.5 block truncate text-caption text-ink-secondary">{summary}</span>
              ) : null}
            </span>
            {badge}
            <ChevronDown
              aria-hidden
              className={`h-4 w-4 shrink-0 text-ink-muted transition-transform ${open ? "rotate-180" : ""}`}
            />
          </button>
        </h3>
        {open ? <div className="border-t border-border px-4 pt-4 pb-5">{children}</div> : null}
      </section>
    </li>
  );
}

/** A tile in a single-choice group. */
export function Choice({
  selected,
  onSelect,
  disabled = false,
  className = "",
  children,
}: {
  selected: boolean;
  onSelect: () => void;
  disabled?: boolean;
  className?: string;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      disabled={disabled}
      onClick={onSelect}
      className={`rounded-xl border text-left transition-colors outline-none focus-visible:ring-2 focus-visible:ring-focus disabled:cursor-not-allowed disabled:opacity-45 ${
        selected
          ? "border-brand-accent/70 bg-brand-soft text-ink ring-1 ring-brand-accent/40"
          : "border-border bg-surface text-ink-secondary hover:border-border-strong hover:bg-surface-hover"
      } ${className}`}
    >
      {children}
    </button>
  );
}

export function FieldLabel({ children, htmlFor }: { children: ReactNode; htmlFor?: string }) {
  const cls = "mb-2 block font-mono text-[10.5px] font-medium tracking-[0.14em] text-ink-muted uppercase";
  return htmlFor ? (
    <label htmlFor={htmlFor} className={cls}>
      {children}
    </label>
  ) : (
    <span className={cls}>{children}</span>
  );
}
