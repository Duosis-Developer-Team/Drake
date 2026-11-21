"use client";

/** Where the costs on screen come from, and what they leave out. */

import { Info, TriangleAlert } from "lucide-react";
import { useEffect, useState } from "react";

import { fetchPrices, type PriceStatus } from "@/lib/agents";
import { useFormat, useT } from "@/lib/i18n";

function sourceName(source: string): string {
  return source.includes("BerriAI/litellm")
    ? "LiteLLM"
    : source.replace(/^https:\/\//, "").split("/")[0];
}

export function PriceNote() {
  const t = useT("agents");
  const fmt = useFormat();
  const [status, setStatus] = useState<PriceStatus | null>(null);
  useEffect(() => {
    fetchPrices()
      .then((body) => setStatus(body && Array.isArray(body.unpriced) ? body : null))
      .catch(() => setStatus(null));
  }, []);
  if (!status) return null;
  const good = status.last_good_fetch;
  const lead = !status.enabled
    ? t("prices.off")
    : !good
      ? t("prices.none")
      : good.status === "fallback"
        ? t("prices.fallback")
        : t("prices.live", {
            source: sourceName(good.source),
            when: fmt.relative(good.fetched_at),
          });
  return (
    <div
      className="flex flex-col gap-1 text-micro text-ink-muted"
      data-testid="price-note"
    >
      <p className="flex flex-wrap items-center gap-x-1.5">
        <Info aria-hidden className="h-3.5 w-3.5 shrink-0" />
        <span>{lead}</span>
        {status.enabled && good ? (
          <span>
            · {t("prices.priced", { count: fmt.number(status.priced_models) })}
          </span>
        ) : null}
        {status.enabled && good ? (
          <span className="text-ink-muted/80">· {t("prices.basis")}</span>
        ) : null}
      </p>
      {status.unpriced.length ? (
        <p className="flex items-center gap-1.5 text-warning">
          <TriangleAlert aria-hidden className="h-3.5 w-3.5 shrink-0" />
          <span className="truncate font-mono">
            {t("prices.unpriced", {
              models: status.unpriced
                .slice(0, 6)
                .map((u) => u.model)
                .join(", "),
            })}
            {status.unpriced.length > 6
              ? ` +${status.unpriced.length - 6}`
              : ""}
          </span>
        </p>
      ) : null}
    </div>
  );
}
