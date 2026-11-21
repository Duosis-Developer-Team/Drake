"use client";

/**
 * The global Traces and Logs screens.
 *
 * Requests and log lines are stored per bound service, and the API answers
 * them per binding, so these screens are a service picker in front of the
 * same explorers the service page uses. The choice lives in the URL
 * (`?binding=`), so a view can be linked. Before a choice, the services worth
 * opening first are listed from the last hour's signals: failing or busiest
 * for traces, error-writing for logs.
 */

import { ArrowUpRight } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";

import { LoadGate, useApi } from "@/components/catalog/primitives";
import { LogExplorer } from "@/components/service-health/LogExplorer";
import { TraceExplorer } from "@/components/service-health/TraceExplorer";
import { DataState } from "@/components/state/DataState";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { toneSpec } from "@/lib/design/status";
import { useFormat, useT } from "@/lib/i18n";
import {
  serviceHealthListPath,
  type ServiceHealthPage,
  type ServiceHealthRow,
} from "@/lib/serviceHealth";
import {
  boundIds,
  judgeSignal,
  signalsPath,
  type SignalItem,
  type SignalPage,
} from "@/lib/signals";
import { useResource } from "@/lib/useResource";
import { ProjectLogo } from "@/components/ui/ProjectLogo";
import {
  admitted,
  ServiceCascade,
  type Cascade,
} from "@/components/explore/ServiceCascade";

type Kind = "traces" | "logs";

function QuickList({
  kind,
  rows,
  onPick,
}: {
  kind: Kind;
  rows: ServiceHealthRow[];
  onPick: (bindingId: string) => void;
}) {
  const t = useT("explore");
  const fmt = useFormat();
  const signals = useResource<SignalPage>(signalsPath(boundIds(rows)), {
    refreshMs: 60_000,
  });
  const items = useMemo(() => {
    const byId = new Map(
      (signals.data?.items ?? []).map((item) => [item.binding_id, item]),
    );
    const score = (item: SignalItem | undefined) =>
      kind === "traces"
        ? (item?.traces?.errors ?? 0) * 1000 + (item?.traces?.requests ?? 0)
        : (item?.logs?.errors ?? 0) * 1000 + (item?.logs?.warnings ?? 0);
    return rows
      .filter((row) => row.binding)
      .map((row) => ({ row, item: byId.get(row.binding!.id) }))
      .sort((a, b) => score(b.item) - score(a.item))
      .slice(0, 12);
  }, [rows, signals.data, kind]);

  return (
    <Panel flush>
      <PanelHeader flush title={t(`${kind}.quick`)} />
      <ul className="divide-y divide-border">
        {items.map(({ row, item }) => {
          const verdict = item ? judgeSignal(item) : null;
          const traces = item?.traces;
          const logs = item?.logs;
          const parts: { text: string; tone?: string }[] = [];
          if (kind === "traces" && traces) {
            parts.push({
              text: t("stats.requests", { count: fmt.number(traces.requests) }),
            });
            if (traces.requests > 0) {
              parts.push({
                text: t("stats.failed", {
                  ratio: `${fmt.number((traces.error_ratio ?? 0) * 100, { maximumFractionDigits: 1 })}%`,
                }),
                tone:
                  verdict?.cause === "requests"
                    ? toneSpec(verdict.tone).text
                    : undefined,
              });
              parts.push({
                text: t("stats.p95", { value: fmt.latency(traces.p95_ms) }),
              });
            }
          }
          if (kind === "logs" && logs) {
            parts.push({
              text:
                logs.errors > 0
                  ? t("stats.errorLines", { count: fmt.number(logs.errors) })
                  : t("stats.quiet"),
              tone: logs.errors > 0 ? "text-warning" : undefined,
            });
          }
          return (
            <li key={row.environment_service_id}>
              <button
                type="button"
                onClick={() => onPick(row.binding!.id)}
                className="flex w-full flex-wrap items-center gap-x-4 gap-y-1 px-5 py-3 text-left transition-colors hover:bg-surface-hover"
              >
                <ProjectLogo
                  projectId={row.project_id}
                  name={row.project_key}
                  size="md"
                  variant="mark"
                />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-body font-semibold text-ink">
                    {row.display_name || row.service_key}
                  </span>
                  <span className="block truncate font-mono text-micro text-ink-muted">
                    {row.project_key}/{row.environment_key}
                  </span>
                </span>
                <span className="flex flex-wrap items-center gap-x-3 font-mono text-[0.8125rem] text-ink-secondary">
                  {parts.length === 0 ? (
                    <span className="text-ink-muted">—</span>
                  ) : null}
                  {parts.map((part) => (
                    <span key={part.text} className={part.tone}>
                      {part.text}
                    </span>
                  ))}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}

export function ServiceExplorer({ kind }: { kind: Kind }) {
  const t = useT("explore");
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname() || `/${kind}`;
  const binding = params.get("binding") ?? "";
  const [page, retry] = useApi<ServiceHealthPage>(serviceHealthListPath({}));
  const [scope, setScope] = useState<Cascade>({
    projectId: null,
    environmentId: null,
  });

  const pick = (next: string) => {
    const query = new URLSearchParams(params.toString());
    if (next) query.set("binding", next);
    else query.delete("binding");
    const qs = query.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  };

  return (
    <LoadGate value={page} retry={retry}>
      {(data) => {
        const bound = data.items.filter((row) => row.binding);
        if (bound.length === 0) {
          return (
            <Panel>
              <DataState kind="empty" title={t("none")} />
            </Panel>
          );
        }
        const current =
          bound.find((row) => row.binding!.id === binding) ?? null;
        return (
          <div className="space-y-4">
            <Panel>
              <ServiceCascade
                rows={bound}
                bindingId={binding}
                onPick={pick}
                onScope={setScope}
              />
              {current ? (
                <div className="mt-3 border-t border-border pt-3">
                  <Link
                    href={`/service-health/${current.binding!.id}`}
                    className="inline-flex items-center gap-1 text-caption font-medium text-brand hover:underline"
                  >
                    {t("openService")}
                    <ArrowUpRight className="h-3.5 w-3.5" aria-hidden />
                  </Link>
                </div>
              ) : null}
            </Panel>
            {current ? (
              kind === "traces" ? (
                <TraceExplorer key={binding} bindingId={binding} />
              ) : (
                <LogExplorer key={binding} bindingId={binding} />
              )
            ) : (
              <QuickList
                kind={kind}
                rows={admitted(bound, scope)}
                onPick={pick}
              />
            )}
          </div>
        );
      }}
    </LoadGate>
  );
}
