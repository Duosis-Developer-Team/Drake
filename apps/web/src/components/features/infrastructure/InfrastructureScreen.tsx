"use client";

/**
 * Infrastructure (ADR-0032): every machine of a cluster against every
 * resource, the 24 hours of who used them, and — one click down — a
 * namespace's pods. The API serves one snapshot per cluster from a
 * one-minute cache and refreshes it in the background, so the screen polls
 * gently and opens at once.
 */

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { PageFrame } from "@/components/shell/AppShell";
import { DataState } from "@/components/state/DataState";
import { useFormat, useT } from "@/lib/i18n";
import {
  GiB,
  addSeries,
  fetchClusters,
  fetchNamespacePods,
  fetchSnapshot,
  lastReal,
  limitVerdict,
  peakOf,
  requestVerdict,
  type InfraCluster,
  type InfraSnapshot,
  type NamespacePods,
  type NamespaceSnapshot,
  type NodeSnapshot,
  type SeriesValue,
} from "@/lib/infrastructure";

import { LineChart, Spark, StackedChart, type Band } from "./charts";
import s from "./infrastructure.module.css";

const POLL_MS = 60_000;
const HUES = ["--h1", "--h2", "--h3", "--h4", "--h5", "--h6", "--h7"];
type Measure = "cpu" | "memory";
type Level = "" | "warn" | "crit";

/* ---------- small helpers ---------- */
function useNum() {
  const fmt = useFormat();
  return {
    tag: fmt.tag,
    n: (v: number | null | undefined, d = 1) => (v === null || v === undefined ? "—" : fmt.number(v, { maximumFractionDigits: d, minimumFractionDigits: d })),
    pct: (v: number | null | undefined) =>
      v === null || v === undefined ? "—" : `${fmt.number(v * 100, { maximumFractionDigits: v * 100 < 10 ? 1 : 0 })}%`,
  };
}
/** Names come from the cluster; they are escaped before entering a sentence's markup. */
const esc = (v: string) => v.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const level = (v: number | null): Level => (v === null ? "" : v >= 0.9 ? "crit" : v >= 0.75 ? "warn" : "");

interface EnvRow {
  key: string;
  namespace: string;
  nodes: string[];
  group: NamespaceSnapshot["group"];
  project: NamespaceSnapshot["project"];
  cpu: number | null;
  memory: number | null;
  cpuSeries: SeriesValue[];
  memorySeries: SeriesValue[];
  cpuRequested: number | null;
  memoryRequested: number | null;
  cpuLimit: number | null;
  memoryLimit: number | null;
  pods: number | null;
  restarts: number | null;
}
const add = (a: number | null, b: number | null) => (a === null ? b : b === null ? a : a + b);

/** Environments in scope; the whole cluster merges a namespace's per-node rows. */
function envRows(snap: InfraSnapshot, scope: string): EnvRow[] {
  const map = new Map<string, EnvRow>();
  for (const n of snap.namespaces) {
    if (scope !== "all" && n.node !== scope) continue;
    const key = scope === "all" ? n.namespace : `${n.namespace}@${n.node}`;
    const e = map.get(key);
    if (!e) {
      map.set(key, {
        key, namespace: n.namespace, nodes: [n.node], group: n.group, project: n.project,
        cpu: n.cpu.now, memory: n.memory.now, cpuSeries: n.cpu.series, memorySeries: n.memory.series,
        cpuRequested: n.cpu_requested, memoryRequested: n.memory_requested, cpuLimit: n.cpu_limit,
        memoryLimit: n.memory_limit, pods: n.pods, restarts: n.restarts_24h,
      });
      continue;
    }
    e.nodes.push(n.node);
    e.cpu = add(e.cpu, n.cpu.now);
    e.memory = add(e.memory, n.memory.now);
    e.cpuSeries = addSeries(e.cpuSeries, n.cpu.series);
    e.memorySeries = addSeries(e.memorySeries, n.memory.series);
    e.cpuRequested = add(e.cpuRequested, n.cpu_requested);
    e.memoryRequested = add(e.memoryRequested, n.memory_requested);
    e.cpuLimit = add(e.cpuLimit, n.cpu_limit);
    e.memoryLimit = add(e.memoryLimit, n.memory_limit);
    e.pods = add(e.pods, n.pods);
    e.restarts = add(e.restarts, n.restarts_24h);
  }
  // A namespace with nothing running and nothing used is history, not a tenant.
  return [...map.values()].filter((e) => (e.pods ?? 0) > 0 || (e.cpu ?? 0) > 0.0005 || (e.memory ?? 0) > 1024 * 1024);
}

function envTone(e: EnvRow): Level {
  const mem = e.memory !== null && e.memoryLimit ? e.memory / e.memoryLimit : 0;
  const cpu = e.cpu !== null && e.cpuRequested ? e.cpu / e.cpuRequested : 0;
  if ((e.restarts ?? 0) >= 3 || mem >= 0.95) return "crit";
  if ((e.restarts ?? 0) > 0 || mem >= 0.85 || cpu > 1.1) return "warn";
  return "";
}

interface Group {
  key: string;
  name: string;
  envs: EnvRow[];
  cpu: number;
  memory: number;
  cpuRequested: number;
  memoryRequested: number;
  pods: number;
  warn: number;
  bad: number;
  cpuSeries: SeriesValue[];
  memorySeries: SeriesValue[];
}

/* ---------- the screen ---------- */
export function InfrastructureScreen() {
  const t = useT("infrastructure");
  const [clusters, setClusters] = useState<InfraCluster[] | null>(null);
  const [clusterId, setClusterId] = useState<string | null>(null);
  const [snap, setSnap] = useState<InfraSnapshot | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    fetchClusters()
      .then((list) => {
        setClusters(list);
        setClusterId((current) => current ?? list.find((c) => c.metrics_configured)?.id ?? list[0]?.id ?? null);
      })
      .catch(setError);
  }, []);

  const load = useCallback(async () => {
    if (!clusterId) return;
    try {
      setSnap(await fetchSnapshot(clusterId));
      setError(null);
    } catch (e) {
      setError(e);
    }
  }, [clusterId]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  return (
    <PageFrame>
      <div className={s.root}>
        <div className={s.eyebrow}>
          <span className={s.cap}>{t("title")}</span>
          <span className={s.live}>
            <i />
            {t("refresh")}
          </span>
          {clusters && clusters.length > 1 ? (
            <select aria-label={t("cluster")} className={s.clusterPick} value={clusterId ?? ""} onChange={(e) => { setSnap(null); setClusterId(e.target.value); }}>
              {clusters.map((c) => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </select>
          ) : null}
        </div>
        {clusters && clusters.length === 0 ? (
          <p className={s.notice}>{t("state.noClusters")}</p>
        ) : snap === null ? (
          error ? <DataState kind="error" description={t("loadError")} onRetry={() => void load()} /> : <DataState kind="loading" />
        ) : snap.state === "denied" || snap.state === "not_configured" || snap.state === "unavailable" ? (
          <>
            <h1 className={s.h1}><b>{snap.cluster.name}</b></h1>
            <p className={s.notice}>{t(`state.${snap.state}`)}</p>
          </>
        ) : (
          <Loaded snap={snap} />
        )}
      </div>
    </PageFrame>
  );
}

function Loaded({ snap }: { snap: InfraSnapshot }) {
  const t = useT("infrastructure");
  const { n, pct } = useNum();
  const facts = (node: NodeSnapshot) => {
    const days =
      node.disk.growth_per_day && node.disk.growth_per_day > 0 && node.disk.now !== null
        ? Math.round((1 - node.disk.now) / node.disk.growth_per_day)
        : null;
    const perCore = node.load5.now !== null && node.cores ? node.load5.now / node.cores : null;
    return { days, perCore };
  };
  const issues: string[] = [];
  for (const node of snap.nodes) {
    const { days, perCore } = facts(node);
    if ((node.memory.now ?? 0) >= 0.9) issues.push(`<span class="${s.c}">${t("headline.memory", { node: esc(node.name), value: pct(node.memory.now) })}</span>`);
    if (days !== null && days < 45) issues.push(`<span class="${s.w}">${t("headline.disk", { node: esc(node.name), days })}</span>`);
    if (perCore !== null && perCore > 2) issues.push(`<span class="${s.w}">${t("headline.load", { node: esc(node.name) })}</span>`);
    if ((node.cpu.now ?? 0) >= 0.9) issues.push(`<span class="${s.w}">${t("headline.cpu", { node: esc(node.name), value: pct(node.cpu.now) })}</span>`);
  }
  const pods = snap.nodes.reduce((sum, node) => sum + (node.pods.running ?? 0), 0);
  const shown = issues.slice(0, 2);
  const headline =
    `<b>${t("headline.machines", { count: snap.nodes.length })}</b>, ${t("headline.pods", { count: Math.round(pods) })}. ` +
    (shown.length ? `${shown.join(", ")}${issues.length > 2 ? `; ${t("headline.more", { count: issues.length - 2 })}` : ""}.` : t("headline.calm"));

  return (
    <>
      {/* The headline is built only from catalogue strings and API numbers. */}
      <h1 className={s.h1} dangerouslySetInnerHTML={{ __html: headline }} />
      {snap.state === "partial" ? <p className={s.partial}>{t("state.partial")}</p> : null}
      <Ledger snap={snap} facts={facts} n={n} pct={pct} />
      <WhoUses snap={snap} />
    </>
  );
}

/* ---------- the machine ledger ---------- */
type Key = "cpu" | "memory" | "disk" | "load" | "pods";
const KEYS: Key[] = ["cpu", "memory", "disk", "load", "pods"];

function Ledger({
  snap,
  facts,
  n,
  pct,
}: {
  snap: InfraSnapshot;
  facts: (node: NodeSnapshot) => { days: number | null; perCore: number | null };
  n: (v: number | null | undefined, d?: number) => string;
  pct: (v: number | null | undefined) => string;
}) {
  const t = useT("infrastructure");
  const fmt = useFormat();
  const [open, setOpen] = useState<string | null>(null);
  const [sort, setSort] = useState<Key | null>(null);
  const nsCount = (node: string) => new Set(snap.namespaces.filter((x) => x.node === node).map((x) => x.namespace)).size;

  const cell = (node: NodeSnapshot, key: Key) => {
    const { days, perCore } = facts(node);
    switch (key) {
      case "cpu":
        return { v: pct(node.cpu.now), use: node.cpu.now ?? 0, req: node.cpu.allocatable && node.cpu.requested !== null ? node.cpu.requested / node.cpu.allocatable : null, lv: level(node.cpu.now),
          sub: t("ledger.cores", { used: n(node.cpu.now !== null && node.cores ? node.cpu.now * node.cores : null), total: n(node.cores, 0), requested: n(node.cpu.requested) }), note: null as string | null, sort: node.cpu.now ?? 0 };
      case "memory":
        return { v: pct(node.memory.now), use: node.memory.now ?? 0, req: node.memory.allocatable && node.memory.requested !== null ? node.memory.requested / node.memory.allocatable : null, lv: level(node.memory.now),
          sub: t("ledger.memoryUse", { requested: n(node.memory.requested !== null ? node.memory.requested / GiB : null), total: n(node.memory.allocatable !== null ? node.memory.allocatable / GiB : null) }),
          note: (node.memory.now ?? 0) >= 0.9 ? t("ledger.memoryHigh") : null, sort: node.memory.now ?? 0 };
      case "disk":
        return { v: pct(node.disk.now), use: node.disk.now ?? 0, req: null, lv: days !== null && days < 45 ? "warn" as Level : level(node.disk.now),
          sub: t("ledger.diskUse", { used: n(node.disk.now !== null && node.disk.size_bytes ? (node.disk.now * node.disk.size_bytes) / GiB : null, 0), total: n(node.disk.size_bytes ? node.disk.size_bytes / GiB : null, 0) }),
          note: days !== null && days < 45 ? t("ledger.diskFills", { days }) : null, sort: node.disk.now ?? 0 };
      case "load":
        return { v: n(node.load5.now), use: Math.min(1, (perCore ?? 0) / 3), req: null, lv: (perCore ?? 0) > 2 ? "warn" as Level : "" as Level,
          sub: t("ledger.loadUse", { cores: n(node.cores, 0), perCore: n(perCore) }), note: (perCore ?? 0) > 2 ? t("ledger.loadHigh", { ratio: n(perCore) }) : null, sort: perCore ?? 0 };
      case "pods":
        return { v: n(node.pods.running, 0), use: node.pods.running !== null && node.pods.allocatable ? node.pods.running / node.pods.allocatable : 0, req: null, lv: "" as Level,
          sub: t("ledger.podsUse", { max: n(node.pods.allocatable, 0), namespaces: nsCount(node.name) }), note: null, sort: node.pods.running ?? 0 };
    }
  };
  const nodes = sort ? [...snap.nodes].sort((a, b) => cell(b, sort).sort - cell(a, sort).sort) : snap.nodes;
  const times = snap.node_times ?? [];
  const step = snap.node_step_seconds ?? 300;
  const clock = (i: number) => new Date((times[i] ?? 0) * 1000).toLocaleTimeString(fmt.tag, { hour: "2-digit", minute: "2-digit" });
  const ago = (h: number) => (h === 0 ? t("who.now") : t("who.hoursAgo", { count: h }));

  return (
    <section className={s.ledger} aria-label={t("ledger.machine")}>
      <div className={s.lh}>
        <span className={s.cap}>{t("ledger.machine")}</span>
        {KEYS.map((k) => (
          <button key={k} type="button" aria-sort={sort === k ? "descending" : "none"} onClick={() => setSort(sort === k ? null : k)}>
            <span className={s.cap}>{t(`ledger.${k}`)}</span>
          </button>
        ))}
      </div>
      {nodes.map((node) => (
        <div key={node.name} className={s.lr}>
          <div className={s.who}>
            <h2>{node.name}</h2>
            <p>{node.kubelet_version ? t("ledger.kubelet", { version: node.kubelet_version }) : ""}</p>
          </div>
          {KEYS.map((k) => {
            const c = cell(node, k);
            const id = `${node.name}:${k}`;
            return (
              <button key={k} type="button" className={`${s.cell} ${c.lv ? s[c.lv] : ""}`} aria-expanded={open === id} onClick={() => setOpen(open === id ? null : id)}>
                <span className={`${s.cap} ${s.k}`}>{t(`ledger.${k}`)}</span>
                <span className={s.v}>{c.v}</span>
                <span className={s.meter}>
                  <i style={{ width: `${Math.min(100, c.use * 100)}%` }} />
                  {c.req !== null ? <u style={{ left: `calc(${Math.min(100, c.req * 100)}% - 1px)` }} /> : null}
                </span>
                <span className={s.sub}>{c.sub}</span>
                {c.note ? <span className={s.note}>{c.note}</span> : null}
              </button>
            );
          })}
          {open?.startsWith(`${node.name}:`) ? (
            <NodeDetail node={node} k={open.split(":")[1] as Key} times={times} step={step} clock={clock} ago={ago} facts={facts(node)} n={n} pct={pct} />
          ) : null}
        </div>
      ))}
      <p className={s.key}>{t("ledger.key")}</p>
    </section>
  );
}

function NodeDetail({
  node, k, times, step, clock, ago, facts, n, pct,
}: {
  node: NodeSnapshot; k: Key; times: number[]; step: number; clock: (i: number) => string; ago: (h: number) => string;
  facts: { days: number | null; perCore: number | null }; n: (v: number | null | undefined, d?: number) => string; pct: (v: number | null | undefined) => string;
}) {
  const t = useT("infrastructure");
  if (k === "pods") return <div className={s.insp}><p>{t("ledger.podsUse", { max: n(node.pods.allocatable, 0), namespaces: "" })}</p></div>;
  const data = k === "cpu" ? node.cpu.series : k === "memory" ? node.memory.series : k === "disk" ? node.disk.series : node.load5.series;
  const ratio = k !== "load";
  const fmt = (v: number) => (ratio ? pct(v) : n(v));
  const color = "var(--accent)";
  const lines =
    k === "cpu" && node.cpu.allocatable && node.cpu.requested !== null
      ? [{ value: node.cpu.requested / node.cpu.allocatable, label: t("ledger.requested", { value: pct(node.cpu.requested / node.cpu.allocatable) }), color: "var(--ink-2)", dash: "3 4" }]
      : k === "memory" && node.memory.allocatable && node.memory.requested !== null
        ? [{ value: node.memory.requested / node.memory.allocatable, label: t("ledger.requested", { value: pct(node.memory.requested / node.memory.allocatable) }), color: "var(--ink-2)", dash: "3 4" }]
        : k === "load" && node.cores
          ? [{ value: node.cores, label: t("ledger.coresLine", { value: n(node.cores, 0) }), color: "var(--ink-2)", dash: "3 4" }]
          : [];
  const forecastSteps = k === "disk" && facts.days !== null && facts.days < 45 ? Math.round(times.length / 4) : 0;
  const perStep = k === "disk" && node.disk.growth_per_day ? (node.disk.growth_per_day * step) / 86400 : null;
  const real = data.filter((v): v is number => v !== null);
  const say =
    k === "cpu" ? t("ledger.sayCpu", { node: node.name, value: pct(node.cpu.now), requested: n(node.cpu.requested), share: pct(node.cpu.allocatable && node.cpu.requested !== null ? node.cpu.requested / node.cpu.allocatable : null) })
      : k === "memory" ? t("ledger.sayMemory", { node: node.name, value: pct(node.memory.now) })
        : k === "disk" ? (facts.days !== null && facts.days < 365 ? t("ledger.sayDisk", { rate: pct(node.disk.growth_per_day), days: facts.days }) : t("ledger.sayDiskFlat"))
          : (facts.perCore ?? 0) > 2 ? t("ledger.sayLoad", { ratio: n(facts.perCore) }) : t("ledger.sayLoadOk");
  return (
    <div className={s.insp}>
      <div>
        <LineChart
          data={data}
          lines={lines}
          color={color}
          format={fmt}
          axis={ratio ? [0, 0.25, 0.5, 0.75, 1] : undefined}
          timeLabel={ago}
          label={`${node.name} ${t(`ledger.${k}`)}`}
          forecastPerStep={perStep}
          forecastSteps={forecastSteps}
          tipTitle={(i) => clock(i)}
          tipRows={(i) => [{ label: t(`ledger.${k}`), value: data[i] === null ? "—" : fmt(data[i] as number), color }]}
        />
        <div className={s.legend}>
          <span style={{ ["--c" as string]: color }}><i />{t(`ledger.${k}`)}</span>
          {lines.length ? <span style={{ ["--c" as string]: "var(--ink-2)" }}><i className={s.dash} />{k === "load" ? t("ledger.coresLine", { value: n(node.cores, 0) }) : t("ledger.legendRequested")}</span> : null}
          {forecastSteps ? <span style={{ ["--c" as string]: color }}><i className={s.dash} />{t("ledger.forecast")}</span> : null}
        </div>
      </div>
      <div>
        <h3>{node.name} · {t(`ledger.${k}`)}</h3>
        <p>{say}</p>
        <dl className={s.facts}>
          <div><dt>{t("ledger.chartNow")}</dt><dd>{fmt(lastReal(data) ?? 0)}</dd></div>
          <div><dt>{t("ledger.chartPeak")}</dt><dd>{real.length ? fmt(Math.max(...real)) : "—"}</dd></div>
          <div><dt>{t("ledger.chartLow")}</dt><dd>{real.length ? fmt(Math.min(...real)) : "—"}</dd></div>
        </dl>
      </div>
    </div>
  );
}

/* ---------- who uses it ---------- */
function WhoUses({ snap }: { snap: InfraSnapshot }) {
  const t = useT("infrastructure");
  const fmt = useFormat();
  const { n, pct } = useNum();
  const [scope, setScope] = useState("all");
  const [measure, setMeasure] = useState<Measure>("cpu");
  const [q, setQ] = useState("");
  const [onlyBad, setOnlyBad] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const [openGroup, setOpenGroup] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [focus, setFocus] = useState<string | null>(null);

  const groupName = useCallback(
    (g: NamespaceSnapshot["group"]) => (g.kind === "project" || g.kind === "unclaimed" ? g.name : t.dyn("group", g.kind, g.name)),
    [t],
  );
  const envs = useMemo(() => envRows(snap, scope), [snap, scope]);
  const val = (x: { cpu: number | null; memory: number | null }) => (measure === "cpu" ? x.cpu ?? 0 : (x.memory ?? 0) / GiB);
  const groups = useMemo(() => {
    const m = new Map<string, Group>();
    const zero = () => new Array<SeriesValue>(snap.times.length).fill(null);
    for (const e of envs) {
      const g = m.get(e.group.key) ?? { key: e.group.key, name: groupName(e.group), envs: [], cpu: 0, memory: 0, cpuRequested: 0, memoryRequested: 0, pods: 0, warn: 0, bad: 0, cpuSeries: zero(), memorySeries: zero() };
      g.envs.push(e);
      g.cpu += e.cpu ?? 0;
      g.memory += e.memory ?? 0;
      g.cpuRequested += e.cpuRequested ?? 0;
      g.memoryRequested += e.memoryRequested ?? 0;
      g.pods += e.pods ?? 0;
      g.cpuSeries = addSeries(g.cpuSeries, e.cpuSeries);
      g.memorySeries = addSeries(g.memorySeries, e.memorySeries);
      const tone = envTone(e);
      if (tone === "crit") g.bad += 1;
      else if (tone === "warn") g.warn += 1;
      m.set(g.key, g);
    }
    return [...m.values()].sort((a, b) => val(b) - val(a));
    // val depends on measure only
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [envs, measure, groupName, snap.times.length]);
  const hue = (key: string) => {
    const i = groups.findIndex((g) => g.key === key);
    return i >= 0 && i < HUES.length ? `var(${HUES[i]})` : "var(--h-rest)";
  };
  const nodes = scope === "all" ? snap.nodes : snap.nodes.filter((x) => x.name === scope);
  const capacity = nodes.reduce((sum, x) => sum + (measure === "cpu" ? x.cpu.allocatable ?? 0 : (x.memory.allocatable ?? 0) / GiB), 0);
  const requested = groups.reduce((sum, g) => sum + (measure === "cpu" ? g.cpuRequested : g.memoryRequested / GiB), 0);
  const used = groups.reduce((sum, g) => sum + val(g), 0);
  const unit = (v: number) => (measure === "cpu" ? `${n(v, 2)} ${t("panel.cores")}` : `${n(v, 1)} ${t("panel.gib")}`);
  const short = (v: number) => (measure === "cpu" ? n(v, 2) : `${n(v, 1)} G`);
  const series = (g: Group) => (measure === "cpu" ? g.cpuSeries : g.memorySeries.map((v) => (v === null ? null : v / GiB)));
  // Fold the tail only when there is more than one project in it.
  const keep = groups.length > HUES.length + 1 ? HUES.length : groups.length;
  const top = groups.slice(0, keep);
  const rest = groups.slice(keep);
  const bands: Band[] = top.map((g) => ({ key: g.key, label: g.name, color: hue(g.key), data: series(g) }));
  if (rest.length) {
    let sum = new Array<SeriesValue>(snap.times.length).fill(null);
    for (const g of rest) sum = addSeries(sum, series(g));
    bands.push({ key: "__rest", label: t("who.others", { count: rest.length }), color: "var(--h-rest)", data: sum });
  }
  const clock = (i: number) => t("who.today", { time: new Date((snap.times[i] ?? 0) * 1000).toLocaleTimeString(fmt.tag, { hour: "2-digit", minute: "2-digit" }) });
  const ago = (h: number) => (h === 0 ? t("who.now") : t("who.hoursAgo", { count: h }));

  const fill = (x: NodeSnapshot) => (measure === "cpu" ? x.cpu.now : x.memory.now);
  const allCap = snap.nodes.reduce((sum, x) => sum + (measure === "cpu" ? x.cpu.allocatable ?? 0 : x.memory.allocatable ?? 0), 0);
  const allFill = allCap ? snap.nodes.reduce((sum, x) => sum + (fill(x) ?? 0) * (measure === "cpu" ? x.cpu.allocatable ?? 0 : x.memory.allocatable ?? 0), 0) / allCap : null;

  const matches = (g: Group) =>
    (!q || g.name.toLowerCase().includes(q) || g.envs.some((e) => e.namespace.includes(q))) && (!onlyBad || g.bad > 0 || g.warn > 0);
  const filtered = groups.filter(matches);
  const visible = showAll || q || onlyBad ? filtered : filtered.slice(0, 10);
  const nBad = groups.filter((g) => g.bad || g.warn).length;
  const scopeName = scope === "all" ? t("who.theCluster") : scope;

  const option = (value: string, name: string, f: number | null, sub: string) => (
    <button key={value} type="button" className={`${s.scb} ${(f ?? 0) >= 0.85 ? s.hot : ""}`} aria-pressed={scope === value} onClick={() => { setScope(value); setSelected(null); }}>
      <span className={s.scn}><b>{name}</b><span>{t("who.full", { value: pct(f) })}</span></span>
      <span className={s.scbar}><i style={{ width: `${Math.min(100, (f ?? 0) * 100)}%` }} /></span>
      <span className={s.scs}>{sub}</span>
    </button>
  );

  return (
    <section className={s.occ} aria-label={t("who.title", { scope: t("who.wholeCluster") })}>
      <h2>{t("who.title", { scope: scope === "all" ? t("who.wholeCluster") : scope })}</h2>
      <div className={s.bar}>
        <div className={s.tg}>
          <span className={s.cap}>{t("who.which")}</span>
          {option("all", t("who.wholeOption"), allFill, t("who.machinesCount", { count: snap.nodes.length }))}
          {snap.nodes.map((x) => option(x.name, x.name, fill(x), measure === "cpu" ? `${n(x.cpu.allocatable, 1)} ${t("panel.cores")}` : `${n((x.memory.allocatable ?? 0) / GiB, 1)} GiB`))}
        </div>
        <div className={s.tg}>
          <span className={s.cap}>{t("who.by")}</span>
          {(["cpu", "memory"] as Measure[]).map((m) => (
            <button key={m} type="button" className={s.pill} aria-pressed={measure === m} onClick={() => setMeasure(m)}>{t(`who.${m}`)}</button>
          ))}
        </div>
        <input className={s.search} type="search" value={q} placeholder={t("who.search")} aria-label={t("who.search")} onChange={(e) => setQ(e.target.value.trim().toLowerCase())} />
      </div>

      <div className={s.chHead}>
        <span className={s.cap}>{t("who.chartCaption", { measure: t(`who.${measure}`) })}</span>
        <p dangerouslySetInnerHTML={{ __html: t("who.chartSay", { used: `<b>${unit(used)}</b>`, capShare: pct(capacity ? used / capacity : null), reqShare: pct(requested ? used / requested : null) }) }} />
      </div>
      {bands.length ? (
        <StackedChart
          bands={bands}
          capacity={capacity}
          requested={requested}
          format={short}
          capLabel={t("who.capacity", { value: unit(capacity) })}
          reqLabel={t("who.requested", { value: unit(requested) })}
          timeLabel={ago}
          label={t("who.chartCaption", { measure: t(`who.${measure}`) })}
          focus={focus}
          onFocus={setFocus}
          tipTitle={clock}
          totalLabel={t("who.total")}
        />
      ) : null}

      <div className={s.tbHead}>
        <h3>{t("who.projects")} <em>{t("who.projectsCount", { projects: groups.length, environments: envs.length })}</em></h3>
        <div className={s.tg}>
          <button type="button" className={s.pill} aria-pressed={!onlyBad} onClick={() => setOnlyBad(false)}>{t("who.all")}</button>
          <button type="button" className={s.pill} aria-pressed={onlyBad} onClick={() => setOnlyBad(true)}>{t("who.problems")} {nBad}</button>
        </div>
      </div>
      <div className={s.th}>
        <span className={s.cap}>#</span>
        <span className={s.cap}>{t("who.colProject")}</span>
        <span className={s.cap}>{t("who.colUses", { measure: t(`who.${measure}`) })}</span>
        <span className={s.cap}>{t("who.colTrend")}</span>
        <span className={s.cap}>{t("who.colRequest")}</span>
        <span className={s.cap}>{t("who.colState")}</span>
        <span />
      </div>
      {visible.length === 0 ? <p className={s.key}>{t("who.noResults")}</p> : null}
      {visible.map((g) => {
        const open = openGroup === g.key || (!!q && g.envs.some((e) => e.namespace.includes(q)));
        const v = verdictText(t, n, pct, g.cpu, g.cpuRequested || null);
        const top = Math.max(...groups.map(val), 1e-9);
        const state = g.bad ? ["crit", t("state2.crit", { count: g.bad })] : g.warn ? ["warn", t("state2.warn", { count: g.warn })] : ["ok", t("state2.ok")];
        const focusKey = groups.indexOf(g) < keep ? g.key : "__rest";
        return (
          <div key={g.key}>
            <button type="button" className={`${s.prow} ${focus === focusKey ? s.hot : ""}`} aria-expanded={open} style={{ ["--c" as string]: hue(g.key) }}
              onClick={() => { setOpenGroup(open ? null : g.key); setSelected(null); }} onMouseEnter={() => setFocus(focusKey)} onMouseLeave={() => setFocus(null)}>
              <span className={`${s.rk} ${s.d}`}>{groups.indexOf(g) + 1}</span>
              <span className={s.nm}><i /><span><b>{g.name}</b><small>{t("who.environments", { count: g.envs.length })} · {t("who.podsCount", { count: Math.round(g.pods) })}</small></span></span>
              <span className={s.us}>
                <span className={s.shb}><i style={{ width: `${(val(g) / top) * 100}%` }} /></span>
                <span className={s.ut}><b>{unit(val(g))}</b> · {t("who.ofScope", { share: pct(capacity ? val(g) / capacity : null), scope: scopeName })}</span>
              </span>
              <span className={s.sparkCell}>
                <Spark data={series(g)} color={hue(g.key)} title={g.name} format={unit} tipTitle={clock} />
              </span>
              <span className={`${s.vd} ${s[v.cls]}`}>{v.title}<small>{v.sub}</small></span>
              <span className={`${s.stt} ${s[state[0]]}`}>{state[1]}</span>
              <svg className={s.chev} width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><path d="M5 3l4 4-4 4" fill="none" stroke="currentColor" strokeWidth="1.6" /></svg>
            </button>
            {open ? (
              <div className={s.envs} style={{ ["--c" as string]: hue(g.key) }}>
                <div className={s.eh}><span /><span className={s.cap}>{t("who.envHeader")}</span><span className={s.cap}>CPU</span><span className={s.cap}>{t("who.memory")}</span><span className={s.cap}>{t("who.colState")}</span></div>
                {g.envs.map((e) => {
                  const tone = envTone(e);
                  const cv = verdictText(t, n, pct, e.cpu, e.cpuRequested);
                  const mv = limitText(t, pct, e.memory, e.memoryLimit);
                  return (
                    <div key={e.key}>
                      <button type="button" className={s.erow} aria-expanded={selected === e.key} onClick={() => setSelected(selected === e.key ? null : e.key)}>
                        <span className={s.dot} style={{ ["--c" as string]: tone === "crit" ? "var(--crit)" : tone === "warn" ? "var(--warn)" : "var(--ok)" }} />
                        <span className={s.pn}><b>{e.namespace}</b><span>{e.nodes.join(", ")} · {t("who.podsCount", { count: Math.round(e.pods ?? 0) })}</span></span>
                        <span className={s.tx}><b>{n(e.cpu, 2)}</b> / {n(e.cpuRequested, 2)} {t("panel.cores")}<small className={s[cv.cls]}>{cv.title}</small></span>
                        <span className={s.tx}><b>{n(e.memory !== null ? e.memory / GiB : null, 2)}</b> / {n(e.memoryLimit !== null ? e.memoryLimit / GiB : null, 1)} GiB<small className={s[mv.cls]}>{mv.text}</small></span>
                        <span className={`${s.tx} ${tone ? s[tone] : s.ok}`}>{tone ? issueText(t, pct, e) : t("state2.ok")}<small className={s.muted}>{selected === e.key ? t("who.closePods") : t("who.openPods")}</small></span>
                      </button>
                      {selected === e.key ? <PodPanel clusterId={snap.cluster.id} env={e} groupName={g.name} scope={scope} onClose={() => setSelected(null)} /> : null}
                    </div>
                  );
                })}
              </div>
            ) : null}
          </div>
        );
      })}
      {!showAll && !q && !onlyBad && filtered.length > visible.length ? (
        <button type="button" className={s.more} onClick={() => setShowAll(true)}>{t("who.showMore", { count: filtered.length - visible.length })}</button>
      ) : null}
    </section>
  );
}

type Tr = ReturnType<typeof useT<"infrastructure">>;
function verdictText(t: Tr, n: (v: number | null | undefined, d?: number) => string, pct: (v: number | null | undefined) => string, used: number | null, requested: number | null) {
  const v = requestVerdict(used, requested);
  if (v === "over") return { cls: "warn", title: t("verdict.over"), sub: t("verdict.overSub", { ratio: n((used ?? 0) / (requested ?? 1)) }) };
  if (v === "idle") return { cls: "cool", title: t("verdict.idle"), sub: t("verdict.idleSub", { value: n((requested ?? 0) - (used ?? 0), 2) }) };
  if (v === "fits") return { cls: "ok", title: t("verdict.fits"), sub: t("verdict.fitsSub", { share: pct((used ?? 0) / (requested ?? 1)) }) };
  return { cls: "muted", title: t("verdict.unknown"), sub: t("verdict.unknownSub") };
}
function limitText(t: Tr, pct: (v: number | null | undefined) => string, used: number | null, limit: number | null) {
  const v = limitVerdict(used, limit);
  const share = pct(used !== null && limit ? used / limit : null);
  if (v === "crit") return { cls: "crit", text: t("verdict.limitCrit", { share }) };
  if (v === "near") return { cls: "warn", text: t("verdict.limitNear", { share }) };
  if (v === "ok") return { cls: "muted", text: t("verdict.limitOk", { share }) };
  return { cls: "muted", text: t("verdict.limitNone") };
}
function issueText(t: Tr, pct: (v: number | null | undefined) => string, e: EnvRow) {
  if ((e.restarts ?? 0) > 0) return t("state2.restarts", { count: Math.round(e.restarts ?? 0) });
  if (e.memory !== null && e.memoryLimit && e.memory / e.memoryLimit >= 0.85) return t("state2.memoryLimit", { share: pct(e.memory / e.memoryLimit) });
  return t("state2.overRequest", { share: pct(e.cpu !== null && e.cpuRequested ? e.cpu / e.cpuRequested : null) });
}

/* ---------- one environment's pods ---------- */
function PodPanel({ clusterId, env, groupName, scope, onClose }: { clusterId: string; env: EnvRow; groupName: string; scope: string; onClose: () => void }) {
  const t = useT("infrastructure");
  const fmt = useFormat();
  const { n, pct } = useNum();
  const [data, setData] = useState<NamespacePods | null>(null);
  const [failed, setFailed] = useState(false);
  const [hover, setHover] = useState<number | null>(null);
  useEffect(() => {
    let live = true;
    fetchNamespacePods(clusterId, env.namespace)
      .then((d) => live && setData(d))
      .catch(() => live && setFailed(true));
    return () => { live = false; };
  }, [clusterId, env.namespace]);
  if (failed) return <div className={s.panel}><p className={s.notice}>{t("panel.error")}</p></div>;
  if (!data) return <div className={s.panel}><p className={s.notice}>{t("panel.loading")}</p></div>;
  const pods = data.pods.filter((p) => scope === "all" || p.node === scope);
  const total = pods.reduce((sum, p) => sum + (p.cpu.now ?? 0), 0);
  let envSeries = new Array<SeriesValue>(data.times.length).fill(null);
  for (const p of pods) envSeries = addSeries(envSeries, p.cpu.series);
  const envReq = pods.reduce((sum, p) => sum + (p.cpu_requested ?? 0), 0);
  const envLim = pods.reduce((sum, p) => sum + (p.cpu_limit ?? 0), 0);
  const focus = hover !== null ? pods[hover] : null;
  const seriesNow = focus ? focus.cpu.series : envSeries;
  const req = focus ? focus.cpu_requested ?? 0 : envReq;
  const lim = focus ? focus.cpu_limit ?? 0 : envLim;
  const who = focus ? focus.pod : t("panel.allPods", { namespace: env.namespace });
  const peak = peakOf(seriesNow);
  const clock = (i: number) => new Date((data.times[i] ?? 0) * 1000).toLocaleTimeString(fmt.tag, { hour: "2-digit", minute: "2-digit" });
  const now = lastReal(seriesNow);
  const ratio = now !== null && req ? now / req : null;
  const top = [...pods].sort((a, b) => (b.cpu.now ?? 0) - (a.cpu.now ?? 0))[0];
  const e = env.cpu !== null && env.cpuRequested ? env.cpu / env.cpuRequested : null;
  const mr = env.memory !== null && env.memoryLimit ? env.memory / env.memoryLimit : null;
  const advice: string[] = [];
  if (top && pods.length > 1 && total > 0) advice.push(t("panel.topPod", { pod: `<b>${esc(top.pod)}</b>`, share: pct((top.cpu.now ?? 0) / total) }));
  if (e !== null && e < 0.5) advice.push(t("panel.idleAdvice", { share: `<b>${pct(e)}</b>`, value: `<b>${n((env.cpuRequested ?? 0) - (env.cpu ?? 0) * 1.3, 2)}</b>` }));
  else if (e !== null && e > 1.05) advice.push(t("panel.overAdvice", { share: `<b>${pct(e - 1)}</b>`, value: n((env.cpu ?? 0) * 1.2, 2) }));
  else if (e !== null) advice.push(t("panel.fitsAdvice", { share: pct(e) }));
  if (mr !== null && mr >= 0.85) advice.push(t("panel.memoryAdvice", { share: `<b>${pct(mr)}</b>` }));
  if ((env.restarts ?? 0) > 0) advice.push(t("panel.restartAdvice", { count: `<b>${Math.round(env.restarts ?? 0)}</b>` }));
  const cv = verdictText(t, n, pct, env.cpu, env.cpuRequested);
  return (
    <section className={s.panel} aria-label={env.namespace}>
      <div className={s.ph}>
        <div>
          <span className={s.cap}>{groupName}</span>
          <h3>{env.namespace}</h3>
          <p>{t("who.podsCount", { count: pods.length })} · {env.nodes.join(", ")} · {env.project ? t("panel.linked", { project: env.project.project_name, environment: env.project.environment_key }) : t("panel.unclaimed")}</p>
        </div>
        <div className={s.acts}>
          {env.project ? <Link className={`${s.btn} ${s.btnGo}`} href={`/service-health?project_id=${encodeURIComponent(env.project.project_id)}&environment_id=${encodeURIComponent(env.project.environment_id)}`}>{t("panel.health")}</Link> : null}
          <button type="button" className={s.btn} onClick={onClose}>{t("panel.close")}</button>
        </div>
      </div>
      <div className={s.stats}>
        <div className={e !== null && e > 1.05 ? s.warn : ""}><span className={s.cap}>{t("panel.cpu")}</span><b>{n(env.cpu, 2)}<small>{t("panel.cores")}</small></b><span className={s.x}>{t("panel.cpuSub", { requested: n(env.cpuRequested, 2), limit: n(env.cpuLimit, 2) })}</span></div>
        <div className={mr !== null && mr >= 0.95 ? s.crit : mr !== null && mr >= 0.85 ? s.warn : ""}><span className={s.cap}>{t("panel.memory")}</span><b>{n(env.memory !== null ? env.memory / GiB : null, 2)}<small>{t("panel.gib")}</small></b><span className={s.x}>{t("panel.memorySub", { limit: n(env.memoryLimit !== null ? env.memoryLimit / GiB : null, 2), share: pct(mr) })}</span></div>
        <div className={cv.cls === "cool" ? s.cool : cv.cls === "warn" ? s.warn : ""}><span className={s.cap}>{t("panel.request")}</span><b>{cv.title}</b><span className={s.x}>{cv.sub}</span></div>
        <div className={(env.restarts ?? 0) >= 3 ? s.crit : (env.restarts ?? 0) > 0 ? s.warn : ""}><span className={s.cap}>{t("panel.restarts")}</span><b>{env.restarts ? t("panel.times", { count: Math.round(env.restarts) }) : t("panel.none")}</b><span className={s.x}>{t("panel.last24")}</span></div>
      </div>
      <div className={s.pb}>
        <div className={s.pchart}>
          <h4>{t("panel.chartTitle", { who })}</h4>
          <p>
            {now !== null && peak ? t("panel.chartSay", { now: n(now, 2), peak: n(peak.value, 2), time: clock(peak.index) }) : null}{" "}
            {ratio !== null ? (ratio > 1.05 ? <span className={s.warn}>{t("panel.chartOver", { ratio: n(ratio) })}</span> : ratio < 0.4 ? <span className={s.cool}>{t("panel.chartIdle", { share: pct(ratio) })}</span> : t("panel.chartFits", { share: pct(ratio) })) : null}
          </p>
          <LineChart
            data={seriesNow}
            lines={[
              ...(req ? [{ value: req, label: t("panel.requestTag", { value: n(req, 2) }), color: "var(--ink-2)", dash: "3 4" }] : []),
              ...(lim ? [{ value: lim, label: t("panel.limitTag", { value: n(lim, 2) }), color: "var(--crit)", dash: "7 4" }] : []),
            ]}
            color="var(--accent)"
            format={(v) => n(v, v < 1 ? 2 : 1)}
            timeLabel={(h) => (h === 0 ? t("who.now") : t("who.hoursAgo", { count: h }))}
            peakLabel={(value, i) => t("panel.peakTag", { value, time: clock(i) })}
            label={t("panel.chartTitle", { who })}
            width={700}
            tipTitle={clock}
            tipRows={(i) => [
              { label: t("panel.used"), value: seriesNow[i] === null ? "—" : n(seriesNow[i] as number, 2), color: "var(--accent)" },
              ...(req ? [{ label: t("panel.requestLine"), value: n(req, 2) }] : []),
              ...(lim ? [{ label: t("panel.limitLine"), value: n(lim, 2) }] : []),
            ]}
          />
          <div className={s.legend}>
            <span style={{ ["--c" as string]: "var(--accent)" }}><i />{t("panel.used")}</span>
            <span style={{ ["--c" as string]: "var(--ink-2)" }}><i className={s.dash} />{t("panel.requestLine")}</span>
            <span style={{ ["--c" as string]: "var(--crit)" }}><i className={s.dash} />{t("panel.limitLine")}</span>
          </div>
        </div>
        <div className={s.say}>
          <h4>{t("panel.seeing")}</h4>
          {advice.map((a) => <p key={a} dangerouslySetInnerHTML={{ __html: a }} />)}
        </div>
      </div>
      <div className={s.pt}>
        <table className={s.pods}>
          <thead><tr><th>{t("panel.colPod")}</th><th>{t("panel.colCpu")}</th><th>{t("panel.colMemory")}</th><th>{t("panel.colState")}</th></tr></thead>
          <tbody onMouseLeave={() => setHover(null)}>
            {pods.map((p, i) => {
              const c1 = verdictText(t, n, pct, p.cpu.now, p.cpu_requested);
              const m1 = limitText(t, pct, p.memory, p.memory_limit);
              return (
                <tr key={p.pod} className={hover === i ? s.on : ""} onMouseEnter={() => setHover(i)}>
                  <td><b>{p.pod}</b><small className={s.muted}>{p.node ?? "—"}</small></td>
                  <td>{t("panel.cpuCell", { used: n(p.cpu.now, 2), requested: n(p.cpu_requested, 2) })}<small className={s[c1.cls]}>{c1.cls === "warn" ? c1.sub : c1.title}</small></td>
                  <td>{t("panel.memoryCell", { used: n(p.memory !== null ? p.memory / GiB : null, 2), limit: n(p.memory_limit !== null ? p.memory_limit / GiB : null, 2) })}<small className={s[m1.cls]}>{m1.text}</small></td>
                  <td className={(p.restarts_24h ?? 0) > 0 ? s.crit : ""}>{(p.restarts_24h ?? 0) > 0 ? t("panel.restarted", { count: Math.round(p.restarts_24h ?? 0) }) : t("panel.running")}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <p className={s.hint}>{t("panel.hint")}</p>
      </div>
    </section>
  );
}
