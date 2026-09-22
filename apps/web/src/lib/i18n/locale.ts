/**
 * Locale preference: English or Turkish.
 *
 * Stored the same way the theme is (localStorage, one key), applied to
 * `<html lang>` before first paint by `LOCALE_INIT_SCRIPT`, and read by the
 * `LocaleProvider` through `useSyncExternalStore` so hydration renders the
 * server's English snapshot first and switches without a mismatch.
 *
 * English is the default and the source language: every key is authored
 * in `en` and the type system requires `tr` to cover all of it — a missing
 * Turkish string is a typecheck failure, not a runtime fallback.
 */

export type Locale = "en" | "tr";

export const LOCALES: readonly Locale[] = ["en", "tr"];
export const DEFAULT_LOCALE: Locale = "en";
export const LOCALE_STORAGE_KEY = "drake-locale";

/** How each locale names itself — never translated. */
export const LOCALE_NAMES: Record<Locale, string> = {
  en: "English",
  tr: "Türkçe",
};

/** BCP 47 tags for `Intl` and `<html lang>`. */
export const LOCALE_TAGS: Record<Locale, string> = {
  en: "en",
  tr: "tr",
};

export function parseLocale(raw: string | null | undefined): Locale {
  return raw === "tr" ? "tr" : DEFAULT_LOCALE;
}

export function readLocale(): Locale {
  try {
    return parseLocale(localStorage.getItem(LOCALE_STORAGE_KEY));
  } catch {
    return DEFAULT_LOCALE;
  }
}

const listeners = new Set<() => void>();

export function writeLocale(locale: Locale): void {
  try {
    if (locale === DEFAULT_LOCALE) localStorage.removeItem(LOCALE_STORAGE_KEY);
    else localStorage.setItem(LOCALE_STORAGE_KEY, locale);
  } catch {
    // Storage blocked; the in-memory subscribers still switch for this session.
  }
  if (typeof document !== "undefined") document.documentElement.lang = LOCALE_TAGS[locale];
  for (const listener of listeners) listener();
}

/** Subscribe to locale changes from this tab and from other tabs. */
export function subscribeLocale(listener: () => void): () => void {
  listeners.add(listener);
  const onStorage = (event: StorageEvent) => {
    if (event.key === LOCALE_STORAGE_KEY || event.key === null) listener();
  };
  if (typeof window !== "undefined") window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    if (typeof window !== "undefined") window.removeEventListener("storage", onStorage);
  };
}

/**
 * Runs before first paint, inline in <head>, so screen readers and the
 * browser's own UI (spellcheck, translate prompts) see the right language
 * from the start. Duplicates `parseLocale` on purpose: it must execute before
 * any bundle is fetched.
 */
export const LOCALE_INIT_SCRIPT = `(function(){try{var l=localStorage.getItem(${JSON.stringify(
  LOCALE_STORAGE_KEY,
)});document.documentElement.lang=l==="tr"?"tr":"en";}catch(e){}})();`;
