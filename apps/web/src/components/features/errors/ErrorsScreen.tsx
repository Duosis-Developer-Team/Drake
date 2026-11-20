"use client";

/**
 * Error tracking (ADR-0036). One environment at a time: every exception,
 * failed request and error log line its services produced, grouped into
 * issues, newest first. An issue opens in place with its sample: the
 * message, the stack trace, the endpoints, the trace to follow.
 */

import { AlertOctagon, CheckCircle2, ChevronDown, ExternalLink, FileWarning, ServerCrash } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { PageFrame, PageHeader } from "@/components/shell/AppShell";
import { DataState } from "@/components/state/DataState";
import { fetchDashboards, type DashboardListItem } from "@/lib/dashboards";
import { ISSUE_KINDS, errorsPath, fetchErrors, type ErrorRange, type ErrorsRead, type Issue, type IssueKind } from "@/lib/errors";
import { useFormat, useT } from "@/lib/i18n";
import { fetchServiceHealthList, type ServiceHealthRow } from "@/lib/serviceHealth";

import s from "./errors.module.css";

const ICONS: Record<IssueKind, typeof AlertOctagon> = {
  exception: AlertOctagon,
  failed_request: ServerCrash,
  error_log: FileWarning,
};

function Trend({ values, label }: { values: number[]; label: string }) {
  const peak = Math.max(1, ...values);
  return (
    <span className={s.trend} role="img" aria-label={label}>
      {values.map((v, i) => (
        <i key={i} className={v ? s.hot : ""} style={{ height: `${v ? Math.max(14, (v / peak) * 100) : 6}%` }} />
      ))}
    </span>
  );
}

export function ErrorsScreen() {
  const t = useT("errors");
  const fmt = useFormat();
  const [environments, setEnvironments] = useState<DashboardListItem[] | null>(null);
  const [environmentId, setEnvironmentId] = useState<string>("");
  const [rows, setRows] = useState<ServiceHealthRow[] | null>(null);
  const [range, setRange] = useState<ErrorRange>("24h");
  const [read, setRead] = useState<ErrorsRead | null>(null);
  const [state, setState] = useState<"idle" | "loading" | "failed">("idle");
  const [kind, setKind] = useState<IssueKind | "all">("all");
  const [service, setService] = useState<string>("all");
  const [opened, setOpened] = useState<string | null>(null);

  useEffect(() => {
    fetchDashboards()
      .then((page) => {
        const shown = page.items.filter((i) => i.services > 0);
        setEnvironments(shown);
        setEnvironmentId((current) => current || shown[0]?.environment_id || "");
      })
      .catch(() => setState("failed"));
  }, []);

  useEffect(() => {
    if (!environmentId) return;
    let cancelled = false;
    setRows(null);
    setRead(null);
    setService("all");
    fetchServiceHealthList({ environmentId })
      .then((list) => !cancelled && setRows(list.items))
      .catch(() => !cancelled && setState("failed"));
    return () => {
      cancelled = true;
    };
  }, [environmentId]);

  const bindings = useMemo(() => (rows ?? []).flatMap((r) => (r.binding ? [{ id: r.binding.id, name: r.display_name || r.service_key }] : [])), [rows]);
  const nameOf = useCallback((id: string) => bindings.find((b) => b.id === id)?.name ?? id, [bindings]);
  const path = useMemo(() => errorsPath(bindings.map((b) => b.id), range), [bindings, range]);

  useEffect(() => {
    if (!path) return;
    let cancelled = false;
    setState("loading");
    fetchErrors(path)
      .then((body) => {
        if (cancelled) return;
        setRead(body);
        setState("idle");
      })
      .catch(() => !cancelled && setState("failed"));
    return () => {
      cancelled = true;
    };
  }, [path]);

  const issues = (read?.issues ?? []).filter((i) => (kind === "all" || i.kind === kind) && (service === "all" || i.binding_id === service));
  const ago = (at: string) => fmt.relative(at);
  const environment = environments?.find((e) => e.environment_id === environmentId);
  const unavailable = read ? ISSUE_KINDS.filter((k) => read.sections[k]?.state === "unavailable") : [];

  return (
    <PageFrame>
      <PageHeader title={t("title")} />
      <div className={s.root}>
        <div className={s.bar}>
          <label className={s.field}>
            <span>{t("environment")}</span>
            <select value={environmentId} onChange={(e) => setEnvironmentId(e.target.value)} aria-label={t("environment")}>
              {(environments ?? []).map((e) => (
                <option key={e.environment_id} value={e.environment_id}>
                  {e.title}
                </option>
              ))}
            </select>
          </label>
          <div className={s.segment} role="group" aria-label={t("rangeLabel")}>
            {(["6h", "24h"] as ErrorRange[]).map((r) => (
              <button key={r} type="button" aria-pressed={range === r} onClick={() => setRange(r)}>
                {t(`range.${r}`)}
              </button>
            ))}
          </div>
        </div>

        {state === "failed" ? (
          <DataState kind="error" title={t("loadError")} />
        ) : environments !== null && environments.length === 0 ? (
          <DataState kind="empty" title={t("noEnvironments")} />
        ) : !read ? (
          <DataState kind="loading" />
        ) : (
          <>
            <div className={s.kpis}>
              <button type="button" className={`${s.kpi} ${kind === "all" ? s.on : ""}`} onClick={() => setKind("all")}>
                <span>{t("kpi.all")}</span>
                <b>{fmt.number(read.summary.total_issues)}</b>
                <small>{t("kpi.allNote", { environment: environment?.title ?? "" })}</small>
              </button>
              {ISSUE_KINDS.map((k) => {
                const Icon = ICONS[k];
                const section = read.sections[k];
                return (
                  <button key={k} type="button" className={`${s.kpi} ${s[`k_${k}`]} ${kind === k ? s.on : ""}`} onClick={() => setKind(kind === k ? "all" : k)}>
                    <span><Icon size={14} />{t(`kind.${k}`)}</span>
                    <b>{section?.state === "ok" ? fmt.number(read.summary.events[k]) : "—"}</b>
                    <small>
                      {section?.state === "not_configured"
                        ? t("notConfigured")
                        : section?.state === "unavailable"
                          ? t("unavailable")
                          : t("kpi.issues", { count: read.summary.issues[k] })}
                    </small>
                  </button>
                );
              })}
            </div>

            {unavailable.length ? <p className={s.warn}>{t("partial")}</p> : null}

            <div className={s.filters}>
              <select value={service} onChange={(e) => setService(e.target.value)} aria-label={t("service")}>
                <option value="all">{t("allServices")}</option>
                {bindings.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.name}
                  </option>
                ))}
              </select>
              <span className={s.asof}>{t("asOf", { ago: ago(read.as_of) })}</span>
            </div>

            {issues.length === 0 ? (
              <div className={s.clean}>
                <CheckCircle2 size={34} />
                <b>{kind === "all" ? t("clean.title", { range: t(`range.${range}`) }) : t("clean.kind", { kind: t(`kind.${kind}`) })}</b>
                <p>{t("clean.body")}</p>
                {read.summary.events.exception === 0 ? <p className={s.hint}>{t("hintExceptions")}</p> : null}
              </div>
            ) : (
              <ol className={s.list}>
                {issues.map((issue, i) => (
                  <IssueRow key={issue.id} issue={issue} index={i} open={opened === issue.id} onToggle={() => setOpened(opened === issue.id ? null : issue.id)} service={nameOf(issue.binding_id)} ago={ago} t={t} />
                ))}
              </ol>
            )}
            {read.summary.events.exception === 0 && issues.length > 0 ? <p className={s.hint}>{t("hintExceptions")}</p> : null}
          </>
        )}
      </div>
    </PageFrame>
  );
}

function IssueRow({
  issue,
  index,
  open,
  onToggle,
  service,
  ago,
  t,
}: {
  issue: Issue;
  index: number;
  open: boolean;
  onToggle: () => void;
  service: string;
  ago: (at: string) => string;
  t: ReturnType<typeof useT<"errors">>;
}) {
  const fmt = useFormat();
  const Icon = ICONS[issue.kind];
  return (
    <li className={`${s.issue} ${s[`k_${issue.kind}`]}`} style={{ animationDelay: `${Math.min(index, 12) * 30}ms` }}>
      <button type="button" className={s.row} aria-expanded={open} onClick={onToggle}>
        <span className={s.icon}><Icon size={16} /></span>
        <span className={s.main}>
          <code className={s.titleText}>{issue.title}</code>
          <span className={s.meta}>
            <em>{t(`kind.${issue.kind}`)}</em> · {service} · {t("lastSeen", { ago: ago(issue.last_seen) })}
          </span>
        </span>
        <Trend values={issue.trend} label={t("trendLabel", { count: issue.count })} />
        <b className={s.count}>{fmt.number(issue.count)}</b>
        <ChevronDown size={16} className={open ? s.flip : ""} />
      </button>
      {open ? (
        <div className={s.detail}>
          <dl className={s.facts}>
            <div><dt>{t("detail.service")}</dt><dd>{service}</dd></div>
            <div><dt>{t("detail.firstSeen")}</dt><dd>{ago(issue.first_seen)}</dd></div>
            <div><dt>{t("detail.lastSeen")}</dt><dd>{ago(issue.last_seen)}</dd></div>
            <div><dt>{t("detail.events")}</dt><dd>{fmt.number(issue.count)}</dd></div>
            {issue.code ? <div><dt>{t("detail.code")}</dt><dd>{issue.code}</dd></div> : null}
            {issue.routes?.length ? <div><dt>{t("detail.routes")}</dt><dd>{issue.routes.join(", ")}</dd></div> : null}
          </dl>
          {issue.message ? (
            <>
              <span className={s.cap}>{t("detail.sample")}</span>
              <pre className={s.pre}>{issue.message}</pre>
            </>
          ) : null}
          {issue.stacktrace ? (
            <>
              <span className={s.cap}>{t("detail.stack")}</span>
              <pre className={`${s.pre} ${s.stack}`}>{issue.stacktrace}</pre>
            </>
          ) : null}
          <div className={s.actions}>
            {issue.trace_id ? <span className={s.trace}>{t("detail.trace")} <code>{issue.trace_id}</code></span> : null}
            <Link href={`/service-health/${issue.binding_id}`} className={s.link}>
              <ExternalLink size={14} />
              {t("detail.openHealth")}
            </Link>
          </div>
          <p className={s.note}>{t("detail.windowNote")}</p>
        </div>
      ) : null}
    </li>
  );
}
