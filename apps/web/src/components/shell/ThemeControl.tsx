"use client";

/**
 * The theme control: system, light, dark.
 *
 * Three states rather than a toggle. "System" is the default and stays live —
 * the listener below keeps following the OS after the user has been in the
 * app for hours and the OS flips at sunset, which a two-state toggle stops
 * doing the moment it is touched.
 *
 * Nothing renders differently before hydration: the applied theme is a class
 * the inline script in the root layout has already set, and this component
 * only reads which preference produced it.
 */

import { Monitor, Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";

import { SegmentedControl } from "@/components/ui/controls";
import {
  readPreference,
  resolveTheme,
  watchSystemTheme,
  writePreference,
  type ThemePreference,
} from "@/lib/theme";

const OPTIONS = [
  { value: "system" as const, label: "System", icon: Monitor },
  { value: "light" as const, label: "Light", icon: Sun },
  { value: "dark" as const, label: "Dark", icon: Moon },
];

export function ThemeControl({ compact = false }: { compact?: boolean }) {
  const [preference, setPreference] = useState<ThemePreference>("system");

  useEffect(() => {
    setPreference(readPreference());
  }, []);

  // Follow the OS while the preference is "system".
  useEffect(() => {
    if (preference !== "system") return;
    return watchSystemTheme((theme) => {
      document.documentElement.classList.toggle("dark", theme === "dark");
    });
  }, [preference]);

  return (
    <SegmentedControl
      label="Theme"
      value={preference}
      // Icons only in the top bar; each radio still carries its word as its
      // accessible name and its tooltip.
      iconOnly={compact}
      options={OPTIONS}
      onChange={(next) => {
        setPreference(next);
        writePreference(next);
      }}
    />
  );
}

/** The theme actually applied right now, for anything that must branch on it. */
export function useResolvedTheme(): "light" | "dark" {
  const [theme, setTheme] = useState<"light" | "dark">("light");
  useEffect(() => {
    const read = () =>
      setTheme(document.documentElement.classList.contains("dark") ? "dark" : "light");
    read();
    const observer = new MutationObserver(read);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    const stop = watchSystemTheme(() => {
      if (readPreference() === "system") {
        document.documentElement.classList.toggle("dark", resolveTheme("system") === "dark");
      }
    });
    return () => {
      observer.disconnect();
      stop();
    };
  }, []);
  return theme;
}
