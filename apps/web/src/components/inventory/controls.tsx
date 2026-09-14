"use client";

/**
 * Pill-shaped filter controls for the inventory toolbar.
 *
 * Native `<select>` and `<input type="search">` underneath — keyboard, screen
 * reader and Playwright's `selectOption` all keep working — dressed as the
 * rounded pills the rest of the product uses for filters.
 */

import { ChevronDown, Search } from "lucide-react";
import { useId } from "react";

const PILL =
  "relative inline-flex h-10 min-w-0 items-center rounded-full border border-border bg-surface shadow-panel transition-colors hover:bg-surface-hover focus-within:ring-2 focus-within:ring-focus";

export function PillSelect({
  label,
  value,
  options,
  onChange,
  placeholder,
  active,
  "data-testid": testId,
}: {
  label: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (value: string) => void;
  placeholder?: string;
  /** Highlights the pill when it narrows the list. */
  active?: boolean;
  "data-testid"?: string;
}) {
  const id = useId();
  return (
    <span className={`${PILL} ${active ? "border-ink/30 bg-surface-2" : ""}`}>
      <label htmlFor={id} className="pointer-events-none shrink-0 pl-4 text-caption text-ink-muted">
        {label}
      </label>
      <select
        id={id}
        data-testid={testId}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="field-sizing-content h-full min-w-0 cursor-pointer appearance-none rounded-full bg-transparent pr-9 pl-2 text-caption font-medium text-ink outline-none"
      >
        {placeholder !== undefined ? <option value="">{placeholder}</option> : null}
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      <ChevronDown
        aria-hidden
        className="pointer-events-none absolute top-1/2 right-3.5 h-4 w-4 -translate-y-1/2 text-ink-muted"
      />
    </span>
  );
}

export function PillSearch({
  label,
  value,
  onChange,
  placeholder,
  "data-testid": testId,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  "data-testid"?: string;
}) {
  const id = useId();
  return (
    <span className={`${PILL} w-56`}>
      <Search aria-hidden className="pointer-events-none ml-4 h-4 w-4 shrink-0 text-ink-muted" />
      <label htmlFor={id} className="sr-only">
        {label}
      </label>
      <input
        id={id}
        type="search"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        minLength={2}
        maxLength={64}
        placeholder={placeholder}
        data-testid={testId}
        className="h-full w-full min-w-0 rounded-full bg-transparent pr-4 pl-2 text-caption text-ink outline-none placeholder:text-ink-muted"
      />
    </span>
  );
}
