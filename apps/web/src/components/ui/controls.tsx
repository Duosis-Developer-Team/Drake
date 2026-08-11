"use client";

/**
 * Form and action controls.
 *
 * Small, typed variants rather than boolean flags, and every one of them
 * inherits the single focus ring from `globals.css` — no component restyles
 * focus, so it cannot go missing on one screen.
 */

import { Check, ChevronDown, Search, X } from "lucide-react";
import { useId } from "react";

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
type ButtonSize = "default" | "compact";

const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  primary: "bg-brand text-ink-inverse hover:bg-brand-hover active:bg-brand-active",
  secondary: "border border-border bg-surface text-ink hover:bg-surface-hover",
  ghost: "text-ink-secondary hover:bg-surface-hover hover:text-ink",
  danger: "border border-critical/40 bg-critical-soft text-critical hover:bg-critical/15",
};

export function Button({
  variant = "secondary",
  size = "default",
  icon: Icon,
  children,
  className = "",
  ...rest
}: {
  variant?: ButtonVariant;
  size?: ButtonSize;
  icon?: React.ComponentType<{ className?: string }>;
  children?: React.ReactNode;
  className?: string;
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      {...rest}
      className={`inline-flex shrink-0 items-center justify-center gap-1.5 rounded-control font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
        size === "compact" ? "h-7 px-2 text-caption" : "h-9 px-3 text-body"
      } ${BUTTON_VARIANTS[variant]} ${className}`}
    >
      {Icon ? <Icon className="h-4 w-4 shrink-0" aria-hidden /> : null}
      {children}
    </button>
  );
}

/**
 * An icon-only action.
 *
 * `label` is required and becomes both the accessible name and the tooltip —
 * there is no way to render one of these without a name.
 */
export function IconButton({
  label,
  icon: Icon,
  active = false,
  className = "",
  ...rest
}: {
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  active?: boolean;
  className?: string;
} & Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "aria-label">) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      {...rest}
      className={`inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-control border transition-colors ${
        active
          ? "border-brand bg-surface-selected text-brand"
          : "border-border text-ink-secondary hover:bg-surface-hover hover:text-ink"
      } ${className}`}
    >
      <Icon className="h-4 w-4" aria-hidden />
    </button>
  );
}

/**
 * A small set of mutually exclusive choices.
 *
 * A radio group under the hood, so arrow keys work and a screen reader
 * announces "3 of 4" rather than reading four unrelated buttons.
 */
export function SegmentedControl<T extends string>({
  label,
  value,
  options,
  onChange,
  size = "default",
  iconOnly = false,
}: {
  label: string;
  value: T;
  options: { value: T; label: string; icon?: React.ComponentType<{ className?: string }> }[];
  onChange: (value: T) => void;
  size?: "default" | "compact";
  /** Show icons only. The label still names the option for assistive tech
   *  and as the tooltip — an icon with no name is not a control. */
  iconOnly?: boolean;
}) {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className="inline-flex shrink-0 items-center gap-0.5 rounded-control border border-border bg-surface-2 p-0.5"
    >
      {options.map((option) => {
        const selected = option.value === value;
        const Icon = option.icon;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={selected}
            title={iconOnly ? option.label : undefined}
            onClick={() => onChange(option.value)}
            className={`inline-flex items-center gap-1.5 rounded-[0.375rem] font-medium transition-colors ${
              iconOnly
                ? size === "compact"
                  ? "h-6 w-7 justify-center"
                  : "h-7 w-8 justify-center"
                : size === "compact"
                  ? "h-6 px-2 text-micro"
                  : "h-7 px-2.5 text-caption"
            } ${
              selected
                ? "bg-surface text-ink shadow-panel"
                : "text-ink-secondary hover:text-ink"
            }`}
          >
            {Icon ? <Icon className="h-3.5 w-3.5" aria-hidden /> : null}
            {iconOnly ? <span className="sr-only">{option.label}</span> : option.label}
          </button>
        );
      })}
    </div>
  );
}

export function SearchInput({
  value,
  onChange,
  placeholder = "Search",
  label,
  className = "",
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  label: string;
  className?: string;
}) {
  const id = useId();
  return (
    <div className={`relative min-w-0 ${className}`}>
      <label htmlFor={id} className="sr-only">
        {label}
      </label>
      <Search
        aria-hidden
        className="pointer-events-none absolute top-1/2 left-2.5 h-4 w-4 -translate-y-1/2 text-ink-muted"
      />
      <input
        id={id}
        type="search"
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        className="h-9 w-full rounded-control border border-border bg-surface pr-8 pl-8 text-body text-ink placeholder:text-ink-muted"
      />
      {value ? (
        <button
          type="button"
          aria-label="Clear search"
          onClick={() => onChange("")}
          className="absolute top-1/2 right-1.5 inline-flex h-6 w-6 -translate-y-1/2 items-center justify-center rounded text-ink-muted hover:text-ink"
        >
          <X className="h-3.5 w-3.5" aria-hidden />
        </button>
      ) : null}
    </div>
  );
}

export function Select<T extends string>({
  label,
  value,
  options,
  onChange,
  hideLabel = false,
  placeholder,
}: {
  label: string;
  value: T | "";
  options: { value: T; label: string }[];
  onChange: (value: T | "") => void;
  hideLabel?: boolean;
  placeholder?: string;
}) {
  const id = useId();
  return (
    <div className="flex min-w-0 items-center gap-2">
      <label
        htmlFor={id}
        className={hideLabel ? "sr-only" : "shrink-0 text-caption text-ink-secondary"}
      >
        {label}
      </label>
      <div className="relative min-w-0">
        <select
          id={id}
          value={value}
          onChange={(event) => onChange(event.target.value as T | "")}
          className="h-9 w-full min-w-0 appearance-none rounded-control border border-border bg-surface pr-8 pl-2.5 text-body text-ink"
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
          className="pointer-events-none absolute top-1/2 right-2 h-4 w-4 -translate-y-1/2 text-ink-muted"
        />
      </div>
    </div>
  );
}

/**
 * The row above a table.
 *
 * One row, sticky nowhere, and it owns the "N of M shown" count so every
 * filtered list in the product says how much it is hiding.
 */
export function FilterBar({
  children,
  summary,
  onReset,
}: {
  children: React.ReactNode;
  summary?: React.ReactNode;
  onReset?: () => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="filter-bar">
      {children}
      {summary ? (
        <span className="ml-auto text-caption text-ink-muted" data-tabular>
          {summary}
        </span>
      ) : null}
      {onReset ? (
        <Button variant="ghost" size="compact" icon={X} onClick={onReset}>
          Clear filters
        </Button>
      ) : null}
    </div>
  );
}

export function Checkbox({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="inline-flex cursor-pointer items-center gap-2 text-caption text-ink">
      <span className="relative inline-flex h-4 w-4 items-center justify-center">
        <input
          type="checkbox"
          checked={checked}
          onChange={(event) => onChange(event.target.checked)}
          className="peer h-4 w-4 appearance-none rounded border border-border-strong bg-surface checked:border-brand checked:bg-brand"
        />
        <Check
          aria-hidden
          className="pointer-events-none absolute h-3 w-3 text-ink-inverse opacity-0 peer-checked:opacity-100"
        />
      </span>
      {label}
    </label>
  );
}
