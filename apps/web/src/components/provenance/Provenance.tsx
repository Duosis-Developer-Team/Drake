"use client";

/**
 * Provenance footer for data cards.
 *
 * Every data-bearing card in Drake can disclose where its value came from:
 * source, as-of time, freshness, scope, measurement method, and confidence.
 * Missing values render as an explicit "not configured" — never hidden and
 * never silently defaulted.
 */
import { useT } from "@/lib/i18n";

export interface ProvenanceInfo {
  source?: string;
  asOf?: string;
  freshness?: string;
  scope?: string;
  measurementMethod?: string;
  confidence?: "exact" | "estimated" | "partial" | "unknown";
}

/** Field → its `ui.provenance.*` label key. */
const FIELDS = [
  { key: "source", label: "provenance.source" },
  { key: "asOf", label: "provenance.asOf" }, // i18n-ignore: a catalogue key, not copy
  { key: "freshness", label: "provenance.freshness" },
  { key: "scope", label: "provenance.scope" },
  { key: "measurementMethod", label: "provenance.method" },
  { key: "confidence", label: "provenance.confidence" },
] as const satisfies readonly { key: keyof ProvenanceInfo; label: string }[];

export function Provenance(props: ProvenanceInfo) {
  const t = useT("ui");
  return (
    <dl
      data-testid="provenance"
      className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-ink-muted"
    >
      {FIELDS.map(({ key, label }) => {
        const value = props[key];
        return (
          <div key={key} className="flex items-baseline gap-1">
            <dt className="font-medium">{t(label)}:</dt>
            <dd className={value ? "font-mono" : "italic"}>
              {value ?? t("provenance.notConfigured")}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}
