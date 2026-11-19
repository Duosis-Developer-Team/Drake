"use client";

/**
 * One service, opened from the dashboard without leaving it (ADR-0033): its
 * hours, its answers, its routes, who it calls and is called by, what it
 * asks of its databases, and what needs attention about it. Every number is
 * from the same overview read the dashboard drew; the service health page
 * is one explicit link away, never a surprise navigation.
 */

import { ExternalLink, X } from "lucide-react";
import Link from "next/link";
import { useEffect } from "react";

import { SLOW_MS, answered, type Overview, type ServiceLine } from "@/lib/dashboards";
import type { useT } from "@/lib/i18n";

import s from "./dashboards.module.css";
import { useTip } from "./tip";
import { HourTicks, useN } from "./widgets";

type T = ReturnType<typeof useT<"dashboards">>;

export function ServiceDrawer({
  line,
  lines,
  overview,
  colour,
  onClose,
  t,
}: {
  line: ServiceLine;
  lines: ServiceLine[];
  overview: Overview | null;
  colour: string;
  onClose: () => void;
  t: T;
}) {
  const { n, ms, pct, hour } = useN();
  const tip = useTip();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const id = line.bindingId;
  const name = (other: string) => lines.find((l) => l.bindingId === other)?.name ?? other;
  const traffic = answered(overview?.traffic)?.find((x) => x.binding_id === id)?.buckets ?? [];
  const routes = (answered(overview?.routes) ?? []).filter((r) => r.binding_id === id).sort((a, b) => b.requests - a.requests).slice(0, 8);
  const calls = answered(overview?.calls) ?? [];
  const outgoing = calls.filter((c) => c.from_binding_id === id);
  const incoming = calls.filter((c) => c.to_binding_id === id);
  const dbs = (answered(overview?.databases) ?? []).filter((d) => d.binding_id === id).sort((a, b) => b.calls - a.calls);
  const findings = (overview?.findings.items ?? []).filter((f) => f.binding_ids.includes(id));
  const codes = Object.entries(line.codes ?? {}).sort((a, b) => b[1] - a[1]);
  const codeTotal = codes.reduce((a, [, v]) => a + v, 0) || 1;
  const minutes = (overview?.bucket_seconds ?? 3600) / 60;
  const peak = Math.max(1e-9, ...traffic.map((b) => b.requests / minutes));
  const buckets = traffic.map((b) => b.t);
  const lx = (v: number) => (Math.log10(Math.max(1, v)) / Math.log10(10_000)) * 100;
  const bucketTip = (e: React.MouseEvent, i: number) => {
    const b = traffic[i];
    tip.show(e, [
      [hour(b.t)],
      [t("detail.perMinute"), n(b.requests / minutes, 1)],
      [t("detail.requests"), n(b.requests)],
      [t("detail.errors"), n(b.errors)],
      ["p95", b.p95_ms === null ? "—" : ms(b.p95_ms)],
    ]);
  };

  return (
    <div className={s.drawerShade} onClick={onClose} role="presentation">
      <aside className={s.drawer} onClick={(e) => e.stopPropagation()} aria-label={line.name} style={{ ["--c" as string]: colour }}>
        <header className={s.dHead}>
          <div>
            <span className={s.dKind}>{line.row.binding?.workload_kind} · {line.row.binding?.namespace}</span>
            <h2><i />{line.name}</h2>
          </div>
          <button type="button" className={s.btn} onClick={onClose} aria-label={t("detail.close")}><X size={14} /></button>
        </header>

        <div className={s.dKpis}>
          <div><span>{t("detail.requests")}</span><b>{n(line.requests)}</b></div>
          <div><span>{t("detail.errorRate")}</span><b>{line.requests ? pct((line.errors ?? 0) / line.requests, 2) : "—"}</b></div>
          <div className={!line.longLived && (line.worstP95 ?? 0) >= SLOW_MS ? s.warnK : ""}><span>{t("detail.worstP95")}</span><b>{line.longLived ? t("services.streaming") : ms(line.worstP95)}</b></div>
          <div className={line.slowBuckets ? s.warnK : ""}><span>{t("detail.slowHours")}</span><b>{n(line.slowBuckets)}</b></div>
        </div>

        <section className={s.dSec}>
          <h3>{t("detail.trafficTitle")}<small>{t("detail.peak", { value: n(peak, 1) })}</small></h3>
          <div className={s.dBars}>
            {traffic.map((b, i) => (
              <i key={b.t} style={{ height: `${Math.max(3, (b.requests / minutes / peak) * 100)}%` }} onMouseMove={(e) => bucketTip(e, i)} onMouseLeave={tip.hide} />
            ))}
          </div>
          <HourTicks buckets={buckets} every={6} />
        </section>

        <section className={s.dSec}>
          <h3>{t("detail.latencyTitle")}<small>{t("detail.latencyNote")}</small></h3>
          <div className={s.dLat}>
            <u style={{ bottom: `${lx(1000)}%` }}><span>1 {t("detail.second")}</span></u>
            {traffic.map((b, i) => (
              <i
                key={b.t}
                className={b.p95_ms !== null && b.p95_ms >= SLOW_MS ? s.slowDot : ""}
                style={{ height: `${b.p95_ms === null ? 0 : Math.max(2, lx(b.p95_ms))}%` }}
                onMouseMove={(e) => bucketTip(e, i)}
                onMouseLeave={tip.hide}
              />
            ))}
          </div>
          <HourTicks buckets={buckets} every={6} />
        </section>

        {codes.length > 0 && (
          <section className={s.dSec}>
            <h3>{t("detail.codesTitle")}</h3>
            {codes.map(([code, count]) => (
              <div key={code} className={s.dRow}>
                <code>{code}</code>
                <span className={s.dTrack}><em className={code.startsWith("5") ? s.bad5 : code.startsWith("4") ? s.c4 : ""} style={{ width: `${(count / codeTotal) * 100}%` }} /></span>
                <b>{n(count)}</b>
                <em>{pct(count / codeTotal, 1)}</em>
              </div>
            ))}
          </section>
        )}

        {routes.length > 0 && (
          <section className={s.dSec}>
            <h3>{t("detail.routesTitle")}</h3>
            <div className={s.dTable}>
              <div className={s.dTh}><span>{t("detail.route")}</span><span>{t("detail.requests")}</span><span>p50</span><span>p95</span><span>p99</span><span>4xx</span></div>
              {routes.map((r) => (
                <div key={r.route} className={s.dTr}>
                  <code title={r.route}>{r.route}</code>
                  <span>{n(r.requests)}</span>
                  <span>{ms(r.p50_ms)}</span>
                  <span className={(r.p95_ms ?? 0) >= SLOW_MS ? s.slowText : ""}>{r.p95_ms === null ? t("services.streaming") : ms(r.p95_ms)}</span>
                  <span>{ms(r.p99_ms)}</span>
                  <span>{n(r.client_errors)}</span>
                </div>
              ))}
            </div>
          </section>
        )}

        {(incoming.length > 0 || outgoing.length > 0) && (
          <section className={s.dSec}>
            <h3>{t("detail.callsTitle")}</h3>
            {incoming.map((c) => (
              <div key={`in${c.from_binding_id}`} className={s.dRow}>
                <span>← {name(c.from_binding_id)}</span>
                <span />
                <b>{n(c.calls)}</b>
                <em className={(c.p95_ms ?? 0) >= SLOW_MS ? s.slowText : ""}>{ms(c.p95_ms)}</em>
              </div>
            ))}
            {outgoing.map((c) => (
              <div key={`out${c.to_binding_id}`} className={s.dRow}>
                <span>→ {name(c.to_binding_id)}</span>
                <span />
                <b>{n(c.calls)}</b>
                <em className={(c.p95_ms ?? 0) >= SLOW_MS ? s.slowText : ""}>{ms(c.p95_ms)}</em>
              </div>
            ))}
          </section>
        )}

        {dbs.length > 0 && (
          <section className={s.dSec}>
            <h3>{t("detail.dbTitle")}</h3>
            {dbs.map((d) => (
              <div key={`${d.database}${d.operation}`} className={s.dRow}>
                <code className={d.operation === "connect" ? s.slowText : ""}>{d.operation}</code>
                <span>{d.database ?? d.system}</span>
                <b>{n(d.calls)}</b>
                <em>{ms(d.p95_ms)}</em>
              </div>
            ))}
          </section>
        )}

        {findings.length > 0 && (
          <section className={s.dSec}>
            <h3>{t("detail.findingsTitle")}</h3>
            {findings.map((f, i) => (
              <p key={i} className={s.dFinding}>{t(`detail.finding.${f.kind}` as never)}</p>
            ))}
          </section>
        )}

        <Link href={`/service-health/${id}`} className={`${s.btn} ${s.dLink}`}>
          <ExternalLink size={14} />
          {t("detail.openHealth")}
        </Link>
      </aside>
    </div>
  );
}
