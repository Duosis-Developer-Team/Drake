/**
 * Provenance footer for data cards.
 *
 * Every data-bearing card in Drake can disclose where its value came from:
 * source, as-of time, freshness, scope, measurement method, and confidence.
 * Missing values render as an explicit "not configured" — never hidden and
 * never silently defaulted.
 */
export interface ProvenanceInfo {
  source?: string;
  asOf?: string;
  freshness?: string;
  scope?: string;
  measurementMethod?: string;
  confidence?: "exact" | "estimated" | "partial" | "unknown";
}

const FIELDS: { key: keyof ProvenanceInfo; label: string }[] = [
  { key: "source", label: "Source" },
  { key: "asOf", label: "As of" },
  { key: "freshness", label: "Freshness" },
  { key: "scope", label: "Scope" },
  { key: "measurementMethod", label: "Method" },
  { key: "confidence", label: "Confidence" },
];

export function Provenance(props: ProvenanceInfo) {
  return (
    <dl
      data-testid="provenance"
      className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-ink-muted"
    >
      {FIELDS.map(({ key, label }) => {
        const value = props[key];
        return (
          <div key={key} className="flex items-baseline gap-1">
            <dt className="font-medium">{label}:</dt>
            <dd className={value ? "font-mono" : "italic"}>{value ?? "not configured"}</dd>
          </div>
        );
      })}
    </dl>
  );
}
