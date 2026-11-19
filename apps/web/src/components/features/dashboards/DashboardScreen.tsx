"use client";

/**
 * One environment's dashboard (ADR-0033): the approved v18 design, drawn from
 * the overview read, laid out by the stored layout. Someone with
 * `dashboard.manage` can reorder, resize and hide widgets; every save is a
 * new version, and an old version is restored by saving it again.
 */

import { ArrowDown, ArrowLeft, ArrowUp, Eye, EyeOff, History, Pencil } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { PageFrame } from "@/components/shell/AppShell";
import { DataState } from "@/components/state/DataState";
import {
  OVERVIEW_RANGES,
  fetchDashboard,
  fetchDashboardVersions,
  fetchOverview,
  moveWidget,
  overviewPath,
  sameLayout,
  saveDashboard,
  serviceLines,
  setWidget,
  answered,
  type Dashboard,
  type DashboardVersion,
  type Overview,
  type OverviewRange,
  type WidgetKey,
  type WidgetSlot,
} from "@/lib/dashboards";
import { useFormat, useT } from "@/lib/i18n";
import { fetchSnapshot, type NamespaceSnapshot } from "@/lib/infrastructure";
import { fetchServiceHealthList, type ServiceHealthRow } from "@/lib/serviceHealth";
import { useSession } from "@/lib/session";

import s from "./dashboards.module.css";
import {
  CapacityWidget,
  DatabasesWidget,
  EndpointsWidget,
  FindingsWidget,
  HeatmapWidget,
  LogsWidget,
  OverviewWidget,
  ServicesWidget,
  TrafficGraphWidget,
} from "./widgets";

const POLL_MS = 60_000;

export function DashboardScreen({ environmentId }: { environmentId: string }) {
  const t = useT("dashboards");
  const fmt = useFormat();
  const { state: session } = useSession();
  const csrfToken = session.status === "authenticated" ? session.me.csrf_token : null;

  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [rows, setRows] = useState<ServiceHealthRow[] | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [namespaces, setNamespaces] = useState<NamespaceSnapshot[] | null>(null);
  const [error, setError] = useState<"not_found" | "failed" | null>(null);
  const [range, setRange] = useState<OverviewRange>("24h");
  const [epMode, setEpMode] = useState<"busy" | "slow">("busy");

  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<WidgetSlot[]>([]);
  const [title, setTitle] = useState("");
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState<"idle" | "saving" | "conflict" | "failed">("idle");
  const [versions, setVersions] = useState<DashboardVersion[] | null>(null);
  const [showVersions, setShowVersions] = useState(false);

  useEffect(() => {
    let cancelled = false;
    Promise.all([fetchDashboard(environmentId), fetchServiceHealthList({ environmentId })])
      .then(([d, list]) => {
        if (cancelled) return;
        setDashboard(d);
        setRows(list.items);
      })
      .catch((e: { status?: number }) => !cancelled && setError(e?.status === 404 ? "not_found" : "failed"));
    return () => {
      cancelled = true;
    };
  }, [environmentId]);

  const bindings = useMemo(() => (rows ?? []).flatMap((r) => (r.binding ? [r.binding] : [])), [rows]);
  const path = useMemo(() => overviewPath(bindings.map((b) => b.id), range), [bindings, range]);

  const loadOverview = useCallback(() => {
    if (!path) return;
    fetchOverview(path)
      .then(setOverview)
      .catch(() => setOverview(null));
  }, [path]);
  useEffect(() => {
    loadOverview();
    const id = setInterval(loadOverview, POLL_MS);
    return () => clearInterval(id);
  }, [loadOverview]);

  // Capacity comes from the infrastructure read of the environment's cluster.
  useEffect(() => {
    const first = bindings[0];
    if (!first) return;
    const names = new Set(bindings.map((b) => b.namespace));
    fetchSnapshot(first.cluster.id)
      .then((snap) => setNamespaces(snap.namespaces.filter((x) => names.has(x.namespace))))
      .catch(() => setNamespaces(null));
  }, [bindings]);

  const lines = useMemo(() => serviceLines(rows ?? [], overview), [rows, overview]);

  if (error) {
    return (
      <PageFrame>
        <DataState kind={error === "not_found" ? "empty" : "error"} title={error === "not_found" ? t("notFound") : t("loadError")} />
      </PageFrame>
    );
  }
  if (!dashboard || !rows) {
    return (
      <PageFrame>
        <DataState kind="loading" />
      </PageFrame>
    );
  }

  const layout = editing ? draft : dashboard.layout;
  const dirty = editing && (!sameLayout(draft, dashboard.layout) || title.trim() !== dashboard.title);

  const startEdit = () => {
    setDraft(dashboard.layout);
    setTitle(dashboard.title);
    setNote("");
    setSaving("idle");
    setEditing(true);
  };
  const save = async (next: WidgetSlot[], nextTitle: string, nextNote: string | null) => {
    if (!csrfToken) return;
    setSaving("saving");
    try {
      const saved = await saveDashboard(csrfToken, environmentId, {
        title: nextTitle.trim() || dashboard.title,
        layout: next,
        note: nextNote,
        expected_version: dashboard.version,
      });
      setDashboard(saved);
      setEditing(false);
      setSaving("idle");
      setVersions(null);
    } catch (e) {
      setSaving((e as { status?: number })?.status === 409 ? "conflict" : "failed");
    }
  };
  const openVersions = async () => {
    setShowVersions((v) => !v);
    if (!versions) {
      try {
        setVersions((await fetchDashboardVersions(environmentId)).items);
      } catch {
        setVersions([]);
      }
    }
  };

  const widget = (key: WidgetKey): { title: string; sub?: string; body: React.ReactNode; tools?: React.ReactNode } | null => {
    if (!overview && key !== "services") return { title: t(`widget.${key}`), body: <DataState kind="loading" /> };
    switch (key) {
      case "overview":
        return { title: t("widget.overview"), sub: t("rangeNote", { range: t(`range.${range}`) }), body: <OverviewWidget overview={overview!} t={t} /> };
      case "services":
        return { title: t("widget.services"), body: <ServicesWidget lines={lines} t={t} /> };
      case "traffic_graph":
        return answered(overview!.traffic) ? { title: t("widget.traffic_graph"), body: <TrafficGraphWidget lines={lines} overview={overview!} t={t} /> } : sectionState(t, "traffic_graph", overview!.traffic.state);
      case "findings":
        return { title: t("widget.findings"), sub: t("findings.sub", { count: overview!.findings.items.length }), body: <FindingsWidget overview={overview!} lines={lines} t={t} /> };
      case "latency_heatmap":
        return answered(overview!.latency) ? { title: t("widget.latency_heatmap"), sub: t("heat.sub"), body: <HeatmapWidget overview={overview!} t={t} /> } : sectionState(t, key, overview!.latency.state);
      case "endpoints":
        return answered(overview!.routes)
          ? {
              title: t("widget.endpoints"),
              tools: (
                <div className={s.pills}>
                  {(["busy", "slow"] as const).map((m) => (
                    <button key={m} type="button" aria-pressed={epMode === m} onClick={() => setEpMode(m)}>
                      {t(`endpoints.${m}`)}
                    </button>
                  ))}
                </div>
              ),
              body: <EndpointsWidget overview={overview!} lines={lines} mode={epMode} t={t} />,
            }
          : sectionState(t, key, overview!.routes.state);
      case "capacity":
        return namespaces && namespaces.length
          ? { title: t("widget.capacity"), sub: t("capacity.sub"), body: <CapacityWidget namespaces={namespaces} t={t} /> }
          : { title: t("widget.capacity"), body: <p className={s.state}>{t("capacity.none")}</p> };
      case "databases":
        return answered(overview!.databases) ? { title: t("widget.databases"), sub: t("databases.sub"), body: <DatabasesWidget overview={overview!} lines={lines} t={t} /> } : sectionState(t, key, overview!.databases.state);
      case "logs":
        return answered(overview!.log_lines) ? { title: t("widget.logs"), sub: t("logs.sub"), body: <LogsWidget overview={overview!} t={t} /> } : sectionState(t, key, overview!.log_lines.state);
    }
  };

  const visible = layout.filter((w) => !w.hidden);
  const hidden = layout.filter((w) => w.hidden);

  return (
    <PageFrame width="wide">
      <div className={s.root}>
        <div className={s.head}>
          <div>
            <Link href="/dashboards" className={s.back}><ArrowLeft size={14} />{t("all")}</Link>
            <h1 className={s.title}>{dashboard.title}</h1>
            <p className={s.sub}>
              {t("subtitle", { project: dashboard.project_name, environment: dashboard.environment_key, services: bindings.length })}
              {dashboard.saved && dashboard.updated_at ? ` · ${t("savedAt", { version: dashboard.version, when: fmt.date(dashboard.updated_at, { dateStyle: "medium", timeStyle: "short" }) })}` : ` · ${t("default")}`}
            </p>
          </div>
          <div className={s.tools}>
            <div className={s.pills} role="group" aria-label={t("rangeLabel")}>
              {OVERVIEW_RANGES.map((r) => (
                <button key={r} type="button" aria-pressed={range === r} onClick={() => setRange(r)}>{t(`range.${r}`)}</button>
              ))}
            </div>
            {dashboard.saved && (
              <button type="button" className={s.btn} onClick={openVersions}><History size={14} />{t("versions.open")}</button>
            )}
            {dashboard.can_manage && !editing && (
              <button type="button" className={`${s.btn} ${s.primary}`} onClick={startEdit}><Pencil size={14} />{t("edit.start")}</button>
            )}
          </div>
        </div>

        {showVersions && (
          <div className={s.card} style={{ marginBottom: 20 }}>
            <header><div><h2>{t("versions.title")}</h2><p>{t("versions.sub")}</p></div></header>
            <div className={s.body}>
              {versions === null ? <DataState kind="loading" /> : versions.map((v) => (
                <div key={v.version} className={s.dbRow} style={{ gridTemplateColumns: "60px minmax(0, 1fr) 200px 140px" }}>
                  <strong style={{ textAlign: "left" }}>v{v.version}</strong>
                  <span>{v.note ?? v.title}</span>
                  <em>{fmt.date(v.saved_at, { dateStyle: "medium", timeStyle: "short" })}</em>
                  {dashboard.can_manage && v.version !== dashboard.version ? (
                    <button type="button" className={s.btn} disabled={saving === "saving"} onClick={() => save(v.layout, v.title, t("versions.restoredNote", { version: v.version }))}>{t("versions.restore")}</button>
                  ) : (
                    <em>{v.version === dashboard.version ? t("versions.current") : ""}</em>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {editing && (
          <>
            <div className={s.banner}>
              <b>{t("edit.editing")}</b>
              <input value={title} maxLength={120} onChange={(e) => setTitle(e.target.value)} aria-label={t("edit.title")} />
              <input value={note} maxLength={200} onChange={(e) => setNote(e.target.value)} placeholder={t("edit.note")} aria-label={t("edit.note")} />
              <span>{saving === "conflict" ? t("edit.conflict") : saving === "failed" ? t("edit.failed") : t("edit.hint")}</span>
              <button type="button" className={s.btn} onClick={() => setEditing(false)}>{t("edit.cancel")}</button>
              <button type="button" className={`${s.btn} ${s.primary}`} disabled={!dirty || saving === "saving"} onClick={() => save(draft, title, note || null)}>{t("edit.save")}</button>
            </div>
            {hidden.length > 0 && (
              <div className={s.hiddenList}>
                {hidden.map((w) => (
                  <button key={w.key} type="button" onClick={() => setDraft(setWidget(draft, w.key, { hidden: false }))}>+ {t(`widget.${w.key}`)}</button>
                ))}
              </div>
            )}
          </>
        )}

        <div className={s.grid}>
          {visible.map((slot, index) => {
            const w = widget(slot.key);
            if (!w) return null;
            return (
              <section key={slot.key} className={`${s.card} ${s[slot.size]} ${editing ? s.edit : ""}`} style={{ animationDelay: `${index * 70}ms` }} aria-label={w.title}>
                {slot.key !== "overview" || editing ? (
                  <header>
                    <div><h2>{w.title}</h2>{w.sub && <p>{w.sub}</p>}</div>
                    {editing ? (
                      <div className={s.editbar}>
                        <button type="button" aria-label={t("edit.up")} disabled={index === 0} onClick={() => setDraft(moveWidget(draft, slot.key, -1))}><ArrowUp size={14} /></button>
                        <button type="button" aria-label={t("edit.down")} disabled={index === visible.length - 1} onClick={() => setDraft(moveWidget(draft, slot.key, 1))}><ArrowDown size={14} /></button>
                        {(["s", "m", "l"] as const).map((size) => (
                          <button key={size} type="button" aria-pressed={slot.size === size} aria-label={t(`edit.size.${size}`)} onClick={() => setDraft(setWidget(draft, slot.key, { size }))}>{size.toUpperCase()}</button>
                        ))}
                        <button type="button" aria-label={t("edit.hide")} disabled={visible.length === 1} onClick={() => setDraft(setWidget(draft, slot.key, { hidden: true }))}><EyeOff size={14} /></button>
                      </div>
                    ) : (
                      w.tools
                    )}
                  </header>
                ) : null}
                <div className={slot.key === "overview" ? "" : s.body}>{w.body}</div>
              </section>
            );
          })}
        </div>
        {editing && hidden.length === 0 && <p className={s.sub} style={{ marginTop: 14 }}><Eye size={12} /> {t("edit.allShown")}</p>}
      </div>
    </PageFrame>
  );
}

function sectionState(t: ReturnType<typeof useT<"dashboards">>, key: WidgetKey, state: string) {
  return {
    title: t(`widget.${key}`),
    body: <p className={s.state}>{state === "not_configured" ? t("state.not_configured") : state === "unavailable" ? t("state.unavailable") : t("state.denied")}</p>,
  };
}
