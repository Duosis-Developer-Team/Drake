/**
 * Theme preference: system, light or dark.
 *
 * Three states, not two. "System" is a real, persisted choice — it means
 * "follow the OS", and it keeps following it when the OS flips at sunset,
 * which a two-state toggle silently stops doing the first time it is used.
 *
 * The applied theme is a `dark` class on `<html>`. The inline script in the
 * root layout sets it before first paint from the same storage key and the
 * same rule as `resolveTheme`, which is why there is no flash — see
 * `THEME_INIT_SCRIPT`.
 */

export type ThemePreference = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

export const THEME_STORAGE_KEY = "drake-theme";
export const THEME_PREFERENCES: ThemePreference[] = ["system", "light", "dark"];

const DARK_QUERY = "(prefers-color-scheme: dark)";

/**
 * Whatever is in storage, as a preference.
 *
 * Absent means "system". So does anything unrecognised — including the
 * literal "light"/"dark" written by the previous two-state toggle, which stay
 * valid preferences rather than being reset.
 */
export function parsePreference(raw: string | null | undefined): ThemePreference {
  return raw === "light" || raw === "dark" ? raw : "system";
}

export function readPreference(): ThemePreference {
  try {
    return parsePreference(localStorage.getItem(THEME_STORAGE_KEY));
  } catch {
    // Storage can be blocked outright. The session still gets a theme.
    return "system";
  }
}

export function systemTheme(): ResolvedTheme {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return "light";
  return window.matchMedia(DARK_QUERY).matches ? "dark" : "light";
}

export function resolveTheme(preference: ThemePreference): ResolvedTheme {
  return preference === "system" ? systemTheme() : preference;
}

export function applyTheme(resolved: ResolvedTheme): void {
  document.documentElement.classList.toggle("dark", resolved === "dark");
}

export function writePreference(preference: ThemePreference): void {
  try {
    if (preference === "system") localStorage.removeItem(THEME_STORAGE_KEY);
    else localStorage.setItem(THEME_STORAGE_KEY, preference);
  } catch {
    // Best effort; the applied class below is what the user actually sees.
  }
  applyTheme(resolveTheme(preference));
}

/** Fires while the preference is "system" and the OS theme changes. */
export function watchSystemTheme(onChange: (theme: ResolvedTheme) => void): () => void {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return () => {};
  }
  const query = window.matchMedia(DARK_QUERY);
  const handler = (event: MediaQueryListEvent) => onChange(event.matches ? "dark" : "light");
  query.addEventListener("change", handler);
  return () => query.removeEventListener("change", handler);
}

/**
 * Runs before first paint, inline in <head>.
 *
 * Deliberately duplicates `parsePreference` + `resolveTheme` rather than
 * importing them: this string has to execute before any bundle is fetched.
 * `theme.test.ts` runs this script and the module against the same inputs and
 * fails if they ever disagree.
 */
export const THEME_INIT_SCRIPT = `(function(){try{
var v=localStorage.getItem(${JSON.stringify(THEME_STORAGE_KEY)});
var p=(v==="light"||v==="dark")?v:"system";
var d=p==="dark"||(p==="system"&&window.matchMedia(${JSON.stringify(DARK_QUERY)}).matches);
document.documentElement.classList.toggle("dark",d);
}catch(e){}})();`;
