"use client";

/**
 * The locale context and the two hooks every component uses.
 *
 *   const t = useT("incidents");        // scoped, typed to that namespace
 *   t("state.open")                     // "Open" / "Açık"
 *   t("row.duration", { value: "4m" })  // interpolation
 *   t.dyn("reason", code, fallback)     // a backend token you cannot type
 *
 *   const { locale, setLocale } = useLocale();
 *   const fmt = useFormat();            // relative/utc/duration in this locale
 *
 * There is no provider requirement: a component rendered without a
 * `LocaleProvider` (every existing unit test) sees English. That is what
 * keeps the change additive — the tests that assert English strings keep
 * asserting them, and one test per area renders in Turkish to prove wiring.
 */

import { createContext, useCallback, useContext, useMemo, useSyncExternalStore } from "react";

import { lookup, type MessageTree, type Paths } from "./define";
import { formatMessage, type MessageVars } from "./format";
import {
  DEFAULT_LOCALE,
  LOCALE_TAGS,
  readLocale,
  subscribeLocale,
  writeLocale,
  type Locale,
} from "./locale";
import { MESSAGES, type Messages, type NamespaceName } from "./messages";

export type { Locale } from "./locale";
export { LOCALES, LOCALE_NAMES, DEFAULT_LOCALE } from "./locale";

interface LocaleContextValue {
  locale: Locale;
  setLocale: (locale: Locale) => void;
}

const LocaleContext = createContext<LocaleContextValue | null>(null);

function getServerSnapshot(): Locale {
  return DEFAULT_LOCALE;
}

/**
 * Reads the persisted preference through `useSyncExternalStore`, which is
 * the one React primitive that renders the server snapshot during hydration
 * and then re-renders with the client value without a mismatch warning.
 */
export function LocaleProvider({
  children,
  locale: forced,
}: {
  children: React.ReactNode;
  /** Pin a locale (tests, previews). Omit to follow the stored preference. */
  locale?: Locale;
}) {
  const stored = useSyncExternalStore(subscribeLocale, readLocale, getServerSnapshot);
  const locale = forced ?? stored;
  const setLocale = useCallback((next: Locale) => writeLocale(next), []);
  const value = useMemo(() => ({ locale, setLocale }), [locale, setLocale]);
  return <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>;
}

export function useLocale(): LocaleContextValue {
  const context = useContext(LocaleContext);
  if (context) return context;
  return { locale: DEFAULT_LOCALE, setLocale: writeLocale };
}

export type Translator<N extends NamespaceName> = {
  (key: Paths<Messages[N]["en"]>, vars?: MessageVars): string;
  /**
   * A key built at runtime from a backend token, e.g. `t.dyn("reason", code)`.
   * Untyped on purpose; returns `fallback` (or the token itself) when the
   * catalogue has no entry, so an unexpected token is still visible.
   */
  dyn: (prefix: string, token: string | null | undefined, fallback?: string) => string;
  /** Whether the catalogue has this dynamic key. */
  has: (key: string) => boolean;
  locale: Locale;
};

function translatorFor<N extends NamespaceName>(namespace: N, locale: Locale): Translator<N> {
  const trees = MESSAGES[namespace] as { en: MessageTree; tr: MessageTree };
  const tree = trees[locale];
  const fallback = trees.en;
  const resolve = (key: string): string | undefined => lookup(tree, key) ?? lookup(fallback, key);
  const t = ((key: string, vars?: MessageVars) => {
    const template = resolve(key);
    if (template === undefined) {
      if (process.env.NODE_ENV !== "production") {
        console.warn(`[i18n] missing message ${String(namespace)}.${key}`);
      }
      return key;
    }
    return vars ? formatMessage(template, vars, locale) : template;
  }) as unknown as Translator<N>;
  t.dyn = (prefix, token, fallbackText) => {
    if (token === null || token === undefined || token === "") return fallbackText ?? "";
    return resolve(`${prefix}.${token}`) ?? fallbackText ?? token;
  };
  t.has = (key) => resolve(key) !== undefined;
  t.locale = locale;
  return t;
}

export function useT<N extends NamespaceName>(namespace: N): Translator<N> {
  const { locale } = useLocale();
  return useMemo(() => translatorFor(namespace, locale), [namespace, locale]);
}

/** For code outside React (a `lib/` formatter) that is handed a locale. */
export function getTranslator<N extends NamespaceName>(namespace: N, locale: Locale): Translator<N> {
  return translatorFor(namespace, locale);
}

/** Locale-aware formatting that the catalogue deliberately does not do. */
export function useFormat() {
  const { locale } = useLocale();
  return useMemo(() => formattersFor(locale), [locale]);
}

const MISSING = "—";

export function formattersFor(locale: Locale) {
  const common = translatorFor("common", locale);
  const tag = LOCALE_TAGS[locale];
  const numberFormat = new Intl.NumberFormat(tag);
  const short = (key: "secondsShort" | "minutesShort" | "hoursShort" | "daysShort" | "monthsShort", count: number) =>
    common(`time.${key}`, { count });

  return {
    locale,
    tag,
    number: (value: number | null | undefined, options?: Intl.NumberFormatOptions): string =>
      value === null || value === undefined || Number.isNaN(value)
        ? MISSING
        : options
          ? new Intl.NumberFormat(tag, options).format(value)
          : numberFormat.format(value),
    /** "4m ago" / "4 dk önce"; always pair it with the exact timestamp. */
    relative: (value: string | number | Date | null | undefined, now: Date = new Date()): string => {
      if (value === null || value === undefined || value === "") return MISSING;
      const date = value instanceof Date ? value : new Date(value);
      if (Number.isNaN(date.getTime())) return String(value);
      const delta = (date.getTime() - now.getTime()) / 1000;
      const abs = Math.abs(delta);
      if (abs < 45) return common("time.justNow");
      const text =
        abs < 3600
          ? short("minutesShort", Math.round(abs / 60))
          : abs < 86400
            ? short("hoursShort", Math.round(abs / 3600))
            : abs < 2592000
              ? short("daysShort", Math.round(abs / 86400))
              : short("monthsShort", Math.round(abs / 2592000));
      return common(delta < 0 ? "time.ago" : "time.in", { value: text });
    },
    /** "3h 12m" / "3 sa 12 dk". */
    duration: (seconds: number | null | undefined, options: { compact?: boolean } = {}): string => {
      if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return MISSING;
      const compact = options.compact ?? false;
      const abs = Math.abs(seconds);
      if (abs < 1) return common("time.millisecondsShort", { count: Math.round(abs * 1000) });
      if (abs < 60) return short("secondsShort", Math.round(abs));
      if (abs < 3600) {
        const m = Math.floor(abs / 60);
        const s = Math.round(abs % 60);
        return compact ? short("minutesShort", m) : `${short("minutesShort", m)} ${short("secondsShort", s)}`;
      }
      if (abs < 86400) {
        const h = Math.floor(abs / 3600);
        const m = Math.round((abs % 3600) / 60);
        return compact ? short("hoursShort", h) : `${short("hoursShort", h)} ${short("minutesShort", m)}`;
      }
      const d = Math.floor(abs / 86400);
      const h = Math.round((abs % 86400) / 3600);
      return compact ? short("daysShort", d) : `${short("daysShort", d)} ${short("hoursShort", h)}`;
    },
    /** A UTC instant, printed with its zone: the product's one timestamp form. */
    utc: (value: string | number | Date | null | undefined): string => {
      if (value === null || value === undefined || value === "") return MISSING;
      const date = value instanceof Date ? value : new Date(value);
      if (Number.isNaN(date.getTime())) return String(value);
      return `${date.toISOString().slice(0, 19).replace("T", " ")} UTC`;
    },
    /** A localized calendar date, for prose rather than evidence. */
    date: (value: string | number | Date | null | undefined, options?: Intl.DateTimeFormatOptions): string => {
      if (value === null || value === undefined || value === "") return MISSING;
      const date = value instanceof Date ? value : new Date(value);
      if (Number.isNaN(date.getTime())) return String(value);
      return new Intl.DateTimeFormat(tag, { dateStyle: "medium", timeZone: "UTC", ...options }).format(date);
    },
  };
}
