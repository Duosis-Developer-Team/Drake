"use client";

/**
 * Every environment the caller may see has a dashboard (ADR-0033): the
 * default one Drake draws, or the one the team saved.
 */

import Link from "next/link";
import { useEffect, useState } from "react";

import { PageFrame, PageHeader } from "@/components/shell/AppShell";
import { DataState } from "@/components/state/DataState";
import { fetchDashboards, type DashboardListItem } from "@/lib/dashboards";
import { useT } from "@/lib/i18n";

import s from "./dashboards.module.css";

export function DashboardIndex() {
  const t = useT("dashboards");
  const [items, setItems] = useState<DashboardListItem[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    fetchDashboards()
      .then((page) => setItems(page.items))
      .catch(() => setFailed(true));
  }, []);

  const shown = (items ?? []).filter((i) => i.services > 0);
  return (
    <PageFrame>
      <PageHeader title={t("indexTitle")} />
      <div className={s.root}>
        {failed ? (
          <DataState kind="error" title={t("loadError")} />
        ) : items === null ? (
          <DataState kind="loading" />
        ) : shown.length === 0 ? (
          <DataState kind="empty" title={t("indexEmpty")} description={t("indexEmptyBody")} />
        ) : (
          <div className={s.list}>
            {shown.map((item, i) => (
              <Link key={item.environment_id} href={`/dashboards/${item.environment_id}`} className={s.tile} style={{ animationDelay: `${i * 50}ms` }}>
                <b>{item.title}</b>
                <span>{t("tileServices", { count: item.services })}</span>
                <em>{item.saved ? t("tileSaved", { version: item.version }) : t("default")}</em>
              </Link>
            ))}
          </div>
        )}
      </div>
    </PageFrame>
  );
}
