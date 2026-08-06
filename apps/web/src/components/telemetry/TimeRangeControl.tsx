"use client";

/**
 * URL-backed time range presets. Rendered ONLY on telemetry-capable screens;
 * catalog/admin screens show nothing rather than a misleading control. The
 * selected preset lives in `?range=` so views are shareable; invalid values
 * fall back to the safe default (24h).
 */

import { Clock } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { RANGE_PRESETS, parseRangePreset } from "@/lib/telemetry";

export function TimeRangeControl() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const active = parseRangePreset(searchParams.get("range"));

  const select = (key: string) => {
    const params = new URLSearchParams(searchParams.toString());
    params.set("range", key);
    router.replace(`${pathname}?${params.toString()}`, { scroll: false });
  };

  return (
    <div
      role="group"
      aria-label="Time range"
      className="hidden h-9 items-center gap-0.5 rounded-lg border border-border p-0.5 sm:inline-flex"
    >
      <Clock className="mx-1 h-4 w-4 text-ink-muted" aria-hidden />
      {RANGE_PRESETS.map((preset) => (
        <button
          key={preset.key}
          type="button"
          onClick={() => select(preset.key)}
          aria-pressed={active === preset.key}
          className={`rounded-md px-2 py-1 text-xs font-medium transition-colors ${
            active === preset.key
              ? "bg-accent text-white"
              : "text-ink-secondary hover:bg-surface-sunken"
          }`}
        >
          {preset.label}
        </button>
      ))}
    </div>
  );
}
