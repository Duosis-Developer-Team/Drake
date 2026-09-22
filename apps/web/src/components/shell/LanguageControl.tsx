"use client";

/**
 * The language control: EN / TR.
 *
 * Sits beside the theme control and follows the same rules: one control
 * reachable at any width (top bar on md+, drawer footer below), the option
 * labels are the languages' own names and are never translated, and the
 * stored preference is applied by an inline script before first paint so
 * `<html lang>` is right from the start.
 */

import { Languages } from "lucide-react";

import { SegmentedControl } from "@/components/ui/controls";
import { LOCALES, LOCALE_NAMES, useLocale, useT, type Locale } from "@/lib/i18n";

const SHORT: Record<Locale, string> = { en: "EN", tr: "TR" };

export function LanguageControl({ compact = false }: { compact?: boolean }) {
  const { locale, setLocale } = useLocale();
  const t = useT("common");
  return (
    <div className="inline-flex items-center gap-1.5" data-testid="language-control">
      {compact ? <Languages className="h-3.5 w-3.5 text-ink-muted" aria-hidden /> : null}
      <SegmentedControl<Locale>
        label={t("language.label")}
        value={locale}
        size={compact ? "compact" : "default"}
        options={LOCALES.map((value) => ({
          value,
          label: compact ? SHORT[value] : LOCALE_NAMES[value],
        }))}
        onChange={setLocale}
      />
    </div>
  );
}
