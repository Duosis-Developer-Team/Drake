"use client";

/**
 * The service map in a panel, with its own read and every state it can be
 * in. Used on the environment page (whole environment), the service page
 * (one service's neighbourhood) and the Services map view.
 */

import { ArrowUpRight } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { ServiceMap } from "@/components/service-map/ServiceMap";
import { DataState } from "@/components/state/DataState";
import { SegmentedControl } from "@/components/ui/controls";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { MAX_OVERVIEW_BINDINGS, OVERVIEW_RANGES, type OverviewRange } from "@/lib/dashboards";
import { useT } from "@/lib/i18n";
import { neighbourhood, useEnvironmentTraffic } from "@/lib/serviceMap";

export function ServiceMapPanel({
  environmentId,
  environmentLabel,
  focusBindingId,
  focusLabel,
  defaultRange = "6h",
  showDashboardLink = true,
  compact = false,
}: {
  environmentId: string | null;
  environmentLabel?: string;
  /** Draw only this service, what it calls and what calls it. */
  focusBindingId?: string;
  focusLabel?: string;
  defaultRange?: OverviewRange;
  showDashboardLink?: boolean;
  compact?: boolean;
}) {
  const t = useT("serviceMap");
  const router = useRouter();
  const [range, setRange] = useState<OverviewRange>(defaultRange);
  const { state, reload } = useEnvironmentTraffic(environmentId, range);

  const graph = useMemo(() => {
    if (state.kind !== "ready") return null;
    return focusBindingId ? neighbourhood(state.graph, focusBindingId) : state.graph;
  }, [state, focusBindingId]);

  const rangeLabel = t(`range.${range}`);
  const subtitle = focusBindingId
    ? t("subtitleNeighbourhood", { service: focusLabel ?? "", range: rangeLabel })
    : t("subtitle", { environment: environmentLabel ?? "", range: rangeLabel });

  let body: React.ReactNode;
  if (state.kind === "loading") {
    body = <DataState kind="loading" />;
  } else if (state.kind === "error") {
    body = <DataState kind="error" description={t("state.error")} onRetry={reload} />;
  } else if (state.kind === "no_bindings") {
    body = <DataState kind="empty" title={t("state.noBindings")} />;
  } else if (!graph || graph.edges.length === 0) {
    const tracesOff =
      state.overview.calls?.state === "not_configured" && state.overview.traffic?.state === "not_configured";
    body = <DataState kind="no-data" title={tracesOff ? t("state.notConfigured") : t("state.noTraffic")} />;
  } else {
    body = (
      <>
        {state.truncated ? (
          <p className="text-caption text-ink-muted">{t("state.truncated", { count: MAX_OVERVIEW_BINDINGS })}</p>
        ) : null}
        <ServiceMap
          graph={graph}
          bucketSeconds={state.overview.bucket_seconds ?? 3600}
          focusId={focusBindingId}
          compact={compact}
          onOpen={(node) => {
            if (node.id !== focusBindingId) router.push(`/service-health/${node.id}`);
          }}
        />
      </>
    );
  }

  return (
    <Panel data-testid="service-map-panel">
      <PanelHeader
        title={t("title")}
        description={subtitle}
        actions={
          <>
            <SegmentedControl
              label={t("window")}
              size="compact"
              value={range}
              options={OVERVIEW_RANGES.map((value) => ({ value, label: value }))}
              onChange={setRange}
            />
            {showDashboardLink && environmentId ? (
              <Link
                href={`/dashboards/${environmentId}`}
                className="inline-flex h-8 items-center gap-1 rounded-control px-2 text-caption font-medium text-brand hover:bg-brand-soft"
              >
                {t("openDashboard")}
                <ArrowUpRight className="h-3.5 w-3.5" aria-hidden />
              </Link>
            ) : null}
          </>
        }
      />
      {body}
    </Panel>
  );
}
