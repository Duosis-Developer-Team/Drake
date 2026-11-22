"use client";

/**
 * Every environment the caller may see has a dashboard (ADR-0033): the
 * default one Tracesis draws, or the one the team saved.
 *
 * Reached through the app launcher: the apps as icons, then the chosen
 * app's environments as cards that already show how each is doing (health
 * of its services, its traffic over the day), each opening its dashboard.
 * The chosen app lives in the URL, so "All dashboards" comes back to it.
 */

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { AppGrid, AppShelf, EnvCard, EnvGrid } from "@/components/apps/AppLauncher";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";
import { DataState } from "@/components/state/DataState";
import { fetchDashboards, type DashboardListItem } from "@/lib/dashboards";
import { useT } from "@/lib/i18n";
import { groupApps, pulseOf, usePlatformOverview } from "@/lib/platform";
import type { ServiceHealthRow } from "@/lib/serviceHealth";
import { useResource } from "@/lib/useResource";

export function DashboardIndex() {
  const t = useT("dashboards");
  const ta = useT("apps");
  const [items, setItems] = useState<DashboardListItem[] | null>(null);
  const [failed, setFailed] = useState(false);
  const services = useResource<{ items: ServiceHealthRow[] }>("/v1/service-health/services");
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname() || "/dashboards";

  useEffect(() => {
    fetchDashboards()
      .then((page) => setItems(page.items))
      .catch(() => setFailed(true));
  }, []);

  const shown = useMemo(() => (items ?? []).filter((i) => i.services > 0), [items]);
  const apps = useMemo(() => {
    const names = new Map(shown.map((i) => [i.project_id, i.project_name]));
    const withDashboard = new Set(shown.map((i) => i.environment_id));
    const rows = (services.data?.items ?? []).filter((r) => withDashboard.has(r.environment_id));
    return groupApps(rows, names);
  }, [shown, services.data]);
  const { overview, failed: trafficFailed } = usePlatformOverview(services.data?.items ?? null);

  const requested = params.get("project");
  const selectedId =
    apps.find((a) => a.projectId === requested)?.projectId ?? (apps.length === 1 ? apps[0].projectId : null);
  const selected = apps.find((a) => a.projectId === selectedId) ?? null;
  const select = (projectId: string | null) => {
    const next = new URLSearchParams(params.toString());
    if (projectId) next.set("project", projectId);
    else next.delete("project");
    const query = next.toString();
    router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
  };

  const loading = items === null || (services.loading && !services.data);
  return (
    <PageFrame width="wide">
      <PageHeader title={t("indexTitle")} description={ta("pickApp")} />
      {failed ? (
        <DataState kind="error" title={t("loadError")} />
      ) : loading ? (
        <DataState kind="loading" />
      ) : apps.length === 0 ? (
        <DataState kind="empty" title={t("indexEmpty")} description={t("indexEmptyBody")} />
      ) : (
        <div className="flex flex-col gap-6">
          <AppGrid apps={apps} selectedId={selectedId} onSelect={select} />
          {selected ? (
            <AppShelf key={selected.projectId} app={selected} onClose={() => select(null)} title={ta("pickEnv")}>
              <EnvGrid count={selected.envs.length}>
                {selected.envs.map((env) => {
                  const item = shown.find((i) => i.environment_id === env.environmentId);
                  return (
                    <EnvCard
                      key={env.environmentId}
                      env={env}
                      pulse={pulseOf(overview, env.bindingIds)}
                      pending={!overview && !trafficFailed}
                      href={`/dashboards/${env.environmentId}`}
                      cta={item?.saved ? t("tileSaved", { version: item.version }) : ta("openDashboard")}
                    />
                  );
                })}
              </EnvGrid>
            </AppShelf>
          ) : null}
        </div>
      )}
    </PageFrame>
  );
}
