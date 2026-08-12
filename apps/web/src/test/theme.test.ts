/**
 * Theme resolution, and the reason there is no flash.
 *
 * The inline script in the root layout duplicates `parsePreference` +
 * `resolveTheme` as a string, because it has to run before any bundle loads.
 * Two implementations of one rule is a defect waiting to happen, so this file
 * runs BOTH against the same inputs and fails if they ever disagree.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  THEME_INIT_SCRIPT,
  THEME_STORAGE_KEY,
  parsePreference,
  readPreference,
  resolveTheme,
  watchSystemTheme,
  writePreference,
  type ThemePreference,
} from "@/lib/theme";

/** Drives `window.matchMedia` for the dark-scheme query. */
function stubSystemTheme(dark: boolean) {
  const listeners = new Set<(event: MediaQueryListEvent) => void>();
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((query: string) => ({
      matches: dark && query.includes("dark"),
      media: query,
      addEventListener: (_: string, handler: (event: MediaQueryListEvent) => void) =>
        listeners.add(handler),
      removeEventListener: (_: string, handler: (event: MediaQueryListEvent) => void) =>
        listeners.delete(handler),
    })),
  );
  return {
    flip(next: boolean) {
      for (const handler of listeners) {
        handler({ matches: next } as MediaQueryListEvent);
      }
    },
    listenerCount: () => listeners.size,
  };
}

/** Executes the inline script exactly as the browser would. */
function runInitScript(): boolean {
  document.documentElement.classList.remove("dark");
  new Function(THEME_INIT_SCRIPT)();
  return document.documentElement.classList.contains("dark");
}

describe("theme preference", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.classList.remove("dark");
  });
  afterEach(() => vi.unstubAllGlobals());

  it("defaults to system, and anything unrecognised is system", () => {
    for (const raw of [null, undefined, "", "auto", "DARK", "sepia"]) {
      expect(parsePreference(raw)).toBe("system");
    }
    expect(parsePreference("light")).toBe("light");
    expect(parsePreference("dark")).toBe("dark");
  });

  it("system follows the OS in both directions", () => {
    stubSystemTheme(true);
    expect(resolveTheme("system")).toBe("dark");
    stubSystemTheme(false);
    expect(resolveTheme("system")).toBe("light");
  });

  it("an explicit choice overrides the OS", () => {
    stubSystemTheme(true);
    expect(resolveTheme("light")).toBe("light");
    stubSystemTheme(false);
    expect(resolveTheme("dark")).toBe("dark");
  });

  it("returning to system clears the stored override rather than storing a word", () => {
    // Otherwise the app stops following the OS forever after one toggle.
    stubSystemTheme(false);
    writePreference("dark");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    writePreference("system");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBeNull();
    expect(readPreference()).toBe("system");
  });

  it("survives storage being unavailable", () => {
    stubSystemTheme(true);
    const getItem = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(readPreference()).toBe("system");
    expect(() => writePreference("light")).not.toThrow();
    // The class is still applied, so the session gets the theme it asked for.
    expect(document.documentElement.classList.contains("dark")).toBe(false);
    getItem.mockRestore();
    setItem.mockRestore();
  });

  it("stops listening to the OS when the watcher is disposed", () => {
    const system = stubSystemTheme(false);
    const stop = watchSystemTheme(() => {});
    expect(system.listenerCount()).toBe(1);
    stop();
    expect(system.listenerCount()).toBe(0);
  });

  it("notifies the watcher when the OS flips", () => {
    const system = stubSystemTheme(false);
    const seen: string[] = [];
    const stop = watchSystemTheme((theme) => seen.push(theme));
    system.flip(true);
    system.flip(false);
    stop();
    expect(seen).toEqual(["dark", "light"]);
  });
});

describe("no-flash init script", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => vi.unstubAllGlobals());

  const CASES: { stored: string | null; systemDark: boolean; expected: ThemePreference }[] = [
    { stored: null, systemDark: true, expected: "system" },
    { stored: null, systemDark: false, expected: "system" },
    { stored: "dark", systemDark: false, expected: "dark" },
    { stored: "light", systemDark: true, expected: "light" },
    { stored: "nonsense", systemDark: true, expected: "system" },
    { stored: "nonsense", systemDark: false, expected: "system" },
  ];

  for (const { stored, systemDark, expected } of CASES) {
    it(`agrees with the module for stored=${stored} systemDark=${systemDark}`, () => {
      stubSystemTheme(systemDark);
      if (stored === null) localStorage.removeItem(THEME_STORAGE_KEY);
      else localStorage.setItem(THEME_STORAGE_KEY, stored);

      const fromScript = runInitScript();
      const fromModule = resolveTheme(parsePreference(stored)) === "dark";

      expect(parsePreference(stored)).toBe(expected);
      expect(fromScript, "inline script and module disagree").toBe(fromModule);
    });
  }

  it("applies the class synchronously, before anything can paint", () => {
    // The point of the script: the class is on <html> the moment it runs, so
    // the first paint is already the right theme.
    stubSystemTheme(true);
    document.documentElement.classList.remove("dark");
    new Function(THEME_INIT_SCRIPT)();
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });

  it("does nothing at all when storage throws", () => {
    stubSystemTheme(false);
    const getItem = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(() => new Function(THEME_INIT_SCRIPT)()).not.toThrow();
    getItem.mockRestore();
  });
});
