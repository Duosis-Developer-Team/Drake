"use client";

import type { Window } from "@/lib/agents";
import { useLocale } from "@/lib/i18n";

/** Hour labels for the activity charts, always in UTC. */
export function useHourLabel(span: Window) {
  const { locale } = useLocale();
  return (iso: string, short: boolean) => {
    const d = new Date(iso);
    const f = (o: Intl.DateTimeFormatOptions) =>
      new Intl.DateTimeFormat(locale, {
        ...o,
        hour12: false,
        timeZone: "UTC",
      }).format(d);
    if (short && span === "7d") return f({ day: "numeric", month: "short" });
    if (short) return f({ hour: "2-digit", minute: "2-digit" });
    return `${f({ day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })} UTC`;
  };
}

/** Over seven days the axis names days: one label per UTC midnight. */
export const dayTicks = (span: Window) =>
  span === "7d"
    ? (iso: string) => new Date(iso).getUTCHours() === 0
    : undefined;
