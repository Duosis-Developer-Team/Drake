/**
 * Locale-aware companions to the English-only helpers in lib/protection.
 * Components hand in `useFormat()`; the lib functions stay for code that
 * has no locale.
 */
import type { formattersFor } from "@/lib/i18n";

type Formatters = ReturnType<typeof formattersFor>;

const MISSING = "—";

/**
 * A window (RPO, RTO, verification TTL) the way a policy states it: "7d",
 * "4h", "30m" when it is a whole unit, the full duration otherwise.
 */
export function formatWindowWith(fmt: Formatters, seconds: number | null): string {
  if (seconds === null) return MISSING;
  const whole =
    seconds % 86400 === 0 ||
    (seconds < 86400 && seconds % 3600 === 0) ||
    (seconds < 3600 && seconds % 60 === 0);
  return fmt.duration(seconds, { compact: whole });
}
