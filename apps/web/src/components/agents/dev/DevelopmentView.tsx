"use client";

/**
 * Agent Visibility → Development: who changes the repositories — the
 * catalog's, and every other one of the watched GitHub organisations — with
 * which coding agent, and what Claude Code cost and did.
 *
 * Commit facts (from the repositories) and Claude Code's own telemetry sit
 * side by side. A person whose Claude Code does not report here reads
 * "telemetry not connected" — never zero.
 */

import { ArrowUpRight, Bot, Coins, ExternalLink, GitCommitHorizontal, GitPullRequest, Plug, Users } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Snippet } from "@/components/agents/ConnectPanel";
import { useWidth } from "@/components/agents/visuals";
import { Button, SegmentedControl } from "@/components/ui/controls";
import { DataState } from "@/components/state/DataState";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import { Drawer } from "@/components/ui/overlay";
import { useFormat, useLocale, useT } from "@/lib/i18n";
import {
  AGENT_COLOUR,
  AGENT_NAME,
  CODING_AGENTS,
  compact,
  devCommitsPath,
  fetchDevCommits,
  fetchDevConnect,
  fetchDevMap,
  personColour,
  usd,
  type CodingAgent,
  type DevCommit,
  type DevConnect,
  type DevMap as MapData,
  type DevRepository,
  type DevTelemetry,
  type DevWindow,
  type UsageItem,
} from "@/lib/devAgents";

import { Avatar, DevMap, RepositoryMark, repositoryTitle, type Pick } from "./DevMap";

const MAP_MIN_WIDTH = 860;
/** Panel `p-5` on both sides plus its 1px border. */
const PANEL_PADDING = 42;

/* ---------------- small pieces ---------------- */

function Figure({
  icon: Icon,
  label,
  value,
  note,
  children,
}: {
  icon: typeof Bot;
  label: string;
  value: React.ReactNode;
  note: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <div className="min-w-0 px-5 py-4">
      <p className="flex items-center gap-1.5 text-caption text-ink-muted">
        <Icon aria-hidden className="h-3.5 w-3.5" />
        {label}
      </p>
      <p className="mt-1.5 truncate font-[family-name:var(--font-display)] text-[1.75rem] leading-none font-bold tracking-[-0.03em] text-ink">
        {value}
      </p>
      <p className="mt-1.5 truncate text-micro text-ink-muted">{note}</p>
      {children}
    </div>
  );
}

function AgentMix({ agents }: { agents: Partial<Record<CodingAgent, number>> }) {
  const t = useT("devAgents");
  const total = Object.values(agents).reduce((a, b) => a + (b ?? 0), 0);
  if (!total) return null;
  const parts = CODING_AGENTS.filter((a) => agents[a]);
  return (
    <span className="mt-2.5 flex h-1.5 w-full gap-[2px] overflow-hidden rounded-full" role="img"
      aria-label={parts.map((a) => `${t.dyn("agent", a, AGENT_NAME[a])} ${agents[a]}`).join(", ")}>
      {parts.map((a) => (
        <span key={a} className="h-full" style={{ flexGrow: agents[a], background: AGENT_COLOUR[a] }} />
      ))}
    </span>
  );
}

function Bars({ items, colour, unit }: { items: UsageItem[]; colour: string; unit: (i: UsageItem) => string }) {
  const max = Math.max(1, ...items.map((i) => i.calls));
  return (
    <ul className="space-y-1.5">
      {items.map((item) => (
        <li key={item.name} className="grid grid-cols-[minmax(0,9rem)_minmax(0,1fr)_auto] items-center gap-2 text-[12px]">
          <span className="truncate font-mono text-ink-secondary" title={item.name}>
            {item.name.replace(/^mcp__/, "").replace(/__/g, " · ")}
          </span>
          <span className="h-1.5 overflow-hidden rounded-full bg-surface-3">
            <span className="block h-full rounded-full" style={{ width: `${(item.calls / max) * 100}%`, background: colour }} />
          </span>
          <span data-tabular className="font-mono text-[11px] text-ink-muted">{unit(item)}</span>
        </li>
      ))}
    </ul>
  );
}

function Telemetry({ telemetry }: { telemetry: DevTelemetry | null }) {
  const t = useT("devAgents");
  const fmt = useFormat();
  const { locale } = useLocale();
  if (!telemetry) {
    return (
      <div className="rounded-control border border-dashed border-border-strong px-4 py-3.5" data-testid="dev-telemetry-off">
        <p className="text-caption font-semibold text-ink">{t("person.telemetryOff")}</p>
        <p className="mt-0.5 text-micro text-ink-muted">{t("person.telemetryOffHint")}</p>
      </div>
    );
  }
  const tokenParts = [
    [t("telemetry.input"), telemetry.input_tokens, "#6366f1"],
    [t("telemetry.output"), telemetry.output_tokens, AGENT_COLOUR.claude_code],
    [t("telemetry.cacheRead"), telemetry.cache_read_tokens, "#94a3b8"],
    [t("telemetry.cacheWrite"), telemetry.cache_creation_tokens, "#14b8a6"],
  ] as const;
  const edits = telemetry.edits_accepted + telemetry.edits_rejected;
  const notMeasured = <span className="text-ink-muted">{t("telemetry.notMeasured")}</span>;
  const stats: [string, React.ReactNode][] = [
    [t("telemetry.cost"), usd(telemetry.cost_usd, locale)],
    [t("telemetry.tokens"), compact(telemetry.tokens, locale)],
    [t("telemetry.sessions"), fmt.number(telemetry.sessions)],
    [
      t("telemetry.active"),
      telemetry.active_seconds === null
        ? notMeasured
        : t("telemetry.hours", { value: fmt.number(telemetry.active_seconds / 3600, { maximumFractionDigits: 1 }) }),
    ],
    [t("telemetry.prompts"), fmt.number(telemetry.prompts)],
    [
      t("telemetry.lines"),
      telemetry.lines_added === null ? notMeasured : `+${fmt.number(telemetry.lines_added)} −${fmt.number(telemetry.lines_removed ?? 0)}`,
    ],
    [
      t("telemetry.edits"),
      edits ? t("telemetry.editsValue", { accepted: telemetry.edits_accepted, total: edits }) : notMeasured,
    ],
  ];
  return (
    <div className="space-y-4" data-testid="dev-telemetry">
      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4">
        {stats.map(([label, value]) => (
          <div key={label} className="min-w-0">
            <dt className="text-[11px] text-ink-muted">{label}</dt>
            <dd data-tabular className="mt-0.5 truncate font-mono text-[0.9375rem] font-semibold text-ink">{value}</dd>
          </div>
        ))}
      </dl>
      {telemetry.tokens ? (
        <div>
          <span className="flex h-2 w-full gap-[2px] overflow-hidden rounded-full">
            {tokenParts.filter(([, v]) => v).map(([label, v, colour]) => (
              <span key={label} className="h-full" style={{ flexGrow: v, background: colour }} title={`${label} ${compact(v, locale)}`} />
            ))}
          </span>
          <span className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-ink-muted">
            {tokenParts.filter(([, v]) => v).map(([label, v, colour]) => (
              <span key={label} className="inline-flex items-center gap-1">
                <span aria-hidden className="h-1.5 w-1.5 rounded-full" style={{ background: colour }} />
                {label} <span data-tabular className="font-mono">{compact(v, locale)}</span>
              </span>
            ))}
          </span>
        </div>
      ) : null}
      <div className="grid gap-4 sm:grid-cols-2">
        {telemetry.tools?.length ? (
          <section>
            <h4 className="mb-2 text-micro font-semibold text-ink-secondary">{t("telemetry.tools")}</h4>
            <Bars items={telemetry.tools} colour={AGENT_COLOUR.claude_code} unit={(i) => t("telemetry.calls", { count: i.calls })} />
          </section>
        ) : null}
        {telemetry.mcp_tools?.length ? (
          <section>
            <h4 className="mb-2 text-micro font-semibold text-ink-secondary">{t("telemetry.mcp")}</h4>
            <Bars items={telemetry.mcp_tools} colour="#8b5cf6" unit={(i) => t("telemetry.calls", { count: i.calls })} />
          </section>
        ) : null}
        {telemetry.languages?.length ? (
          <section>
            <h4 className="mb-2 text-micro font-semibold text-ink-secondary">{t("telemetry.languages")}</h4>
            <Bars items={telemetry.languages} colour="#0ea5e9" unit={(i) => fmt.number(i.calls)} />
          </section>
        ) : null}
        {telemetry.models?.length ? (
          <section>
            <h4 className="mb-2 text-micro font-semibold text-ink-secondary">{t("telemetry.models")}</h4>
            <Bars items={telemetry.models} colour="#6366f1" unit={(i) => usd(i.cost_usd, locale)} />
          </section>
        ) : null}
      </div>
    </div>
  );
}

function Series({ values, days }: { values: number[]; days: string[] }) {
  const max = Math.max(1, ...values);
  return (
    <div className="flex h-12 items-end gap-[2px]" role="img" aria-label={values.join(", ")}>
      {values.map((v, i) => (
        <span
          key={days[i] ?? i}
          title={`${days[i]} · ${v}`}
          className="min-w-[2px] flex-1 rounded-t-[2px]"
          style={{
            height: `${v ? Math.max(8, (v / max) * 100) : 4}%`,
            background: v ? AGENT_COLOUR.claude_code : "var(--surface-3)",
            opacity: v ? 0.85 : 1,
          }}
        />
      ))}
    </div>
  );
}

function CommitList({ path }: { path: string }) {
  const t = useT("devAgents");
  const fmt = useFormat();
  const { locale } = useLocale();
  const [state, setState] = useState<{ path: string; items: DevCommit[]; total: number } | null>(null);
  useEffect(() => {
    let active = true;
    fetchDevCommits(path)
      .then((body) => active && setState({ path, items: body.items, total: body.total }))
      .catch(() => active && setState({ path, items: [], total: 0 }));
    return () => {
      active = false;
    };
  }, [path]);
  if (!state || state.path !== path) return <p className="text-caption text-ink-muted">{t("commits.loading")}</p>;
  if (state.items.length === 0) return <p className="text-caption text-ink-muted">{t("commits.empty")}</p>;
  const byDay = new Map<string, DevCommit[]>();
  for (const c of state.items) {
    const day = new Date(c.authored_at).toLocaleDateString(locale === "tr" ? "tr-TR" : "en-GB", {
      weekday: "short",
      day: "numeric",
      month: "short",
    });
    byDay.set(day, [...(byDay.get(day) ?? []), c]);
  }
  return (
    <div className="space-y-4" data-testid="dev-commits">
      {[...byDay.entries()].map(([day, commits]) => (
        <section key={day}>
          <h5 className="mb-1.5 font-mono text-[10.5px] font-medium tracking-[0.1em] text-ink-muted uppercase">{day}</h5>
          <ul className="divide-y divide-border rounded-control border border-border">
            {commits.map((c) => (
              <li key={c.sha} className="flex items-start gap-3 px-3 py-2.5">
                <span aria-hidden className="mt-1.5 h-2 w-2 shrink-0 rounded-full" style={{ background: AGENT_COLOUR[c.agent] }} title={t.dyn("agent", c.agent, AGENT_NAME[c.agent])} />
                <span className="min-w-0 flex-1">
                  <a href={c.url} target="_blank" rel="noreferrer" className="block truncate text-caption font-medium text-ink hover:text-brand-accent">
                    {c.title}
                  </a>
                  <span className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-ink-muted">
                    <span className="font-mono">{c.short}</span>
                    <span>{c.author_name || c.author_login}</span>
                    <span>{c.project_name ?? c.repository.split("/")[1] ?? c.repository}</span>
                    {c.services.map((s) => (
                      <span key={s} className="rounded bg-surface-2 px-1 font-mono">{s}</span>
                    ))}
                    {c.evidence ? <span>· {t.dyn("commits.evidence", c.evidence, c.evidence)}</span> : null}
                    {c.pr_url ? (
                      <a href={c.pr_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 text-brand-accent hover:underline">
                        <GitPullRequest aria-hidden className="h-3 w-3" />
                        {t("commits.pr", { number: c.pr_number ?? 0 })}
                      </a>
                    ) : null}
                  </span>
                </span>
                <span data-tabular className="shrink-0 font-mono text-[11px]">
                  <span className="text-healthy">+{fmt.number(c.additions)}</span>{" "}
                  <span className="text-critical">−{fmt.number(c.deletions)}</span>
                </span>
              </li>
            ))}
          </ul>
        </section>
      ))}
      {state.total > state.items.length ? (
        <p className="text-micro text-ink-muted">{t("commits.more", { shown: state.items.length, total: state.total })}</p>
      ) : null}
    </div>
  );
}

/* ---------------- what a click opens ---------------- */

function Detail({ map, pick, window }: { map: MapData; pick: Pick; window: DevWindow }) {
  const t = useT("devAgents");
  const fmt = useFormat();
  const { locale } = useLocale();
  const repoOf = (id: string) => map.repositories.find((r) => r.id === id);

  if (pick.kind === "person") {
    const person = map.people.find((p) => p.key === pick.key);
    if (!person) return null;
    return (
      <div className="space-y-6" data-testid="dev-person-detail">
        <header className="flex items-center gap-4">
          <Avatar person={person} size={56} />
          <div className="min-w-0">
            <h3 className="truncate font-[family-name:var(--font-display)] text-[1.375rem] font-bold tracking-[-0.02em] text-ink">{person.name}</h3>
            {person.logins.length ? (
              <p className="truncate font-mono text-micro text-ink-muted">{t("person.logins", { logins: person.logins.join(", ") })}</p>
            ) : null}
            <p className="mt-1.5 flex flex-wrap gap-1">
              {person.tools.map((tool) => (
                <span key={tool} className="rounded-full px-2 py-0.5 text-[11px] font-semibold"
                  style={{ color: AGENT_COLOUR[tool], background: `color-mix(in srgb, ${AGENT_COLOUR[tool]} 13%, transparent)` }}>
                  {t.dyn("agent", tool, AGENT_NAME[tool])}
                </span>
              ))}
            </p>
          </div>
        </header>
        {!person.linked ? <p className="rounded-control bg-surface-2 px-3 py-2 text-micro text-ink-secondary">{t("person.unlinked")}</p> : null}
        <dl className="grid grid-cols-3 gap-3">
          {[
            [t("person.commits"), fmt.number(person.commits)],
            [t("person.changes"), `+${fmt.number(person.additions)} −${fmt.number(person.deletions)}`],
            [t("person.activeDays"), fmt.number(person.active_days)],
          ].map(([label, value]) => (
            <div key={label} className="rounded-control border border-border px-3 py-2.5">
              <dt className="text-[11px] text-ink-muted">{label}</dt>
              <dd data-tabular className="mt-0.5 font-mono text-[0.9375rem] font-semibold text-ink">{value}</dd>
            </div>
          ))}
        </dl>
        <section>
          <h4 className="mb-2 text-micro font-semibold text-ink-secondary">{t("person.activity")}</h4>
          <Series values={person.series} days={map.days} />
          {person.last_at ? <p className="mt-1 text-[11px] text-ink-muted">{t("person.lastSeen", { when: fmt.relative(person.last_at) })}</p> : null}
        </section>
        <section>
          <h4 className="mb-2 text-micro font-semibold text-ink-secondary">{t("person.repositories")}</h4>
          <ul className="flex flex-wrap gap-2">
            {person.repositories.map((id) => {
              const r = repoOf(id);
              return r ? (
                <li
                  key={id}
                  className={`inline-flex items-center gap-1.5 rounded-full border bg-surface-2 py-1 pr-2.5 pl-1.5 ${
                    r.catalogued ? "border-border" : "border-dashed border-border"
                  }`}
                >
                  <RepositoryMark repo={r} size="sm" />
                  <span className={`text-caption text-ink ${r.catalogued ? "" : "font-mono"}`}>{repositoryTitle(r)}</span>
                </li>
              ) : null;
            })}
          </ul>
        </section>
        <section>
          <h4 className="mb-2.5 text-micro font-semibold text-ink-secondary">{t("person.telemetry")}</h4>
          <Telemetry telemetry={person.telemetry} />
        </section>
        <section>
          <h4 className="mb-2.5 text-micro font-semibold text-ink-secondary">{t("commits.title")}</h4>
          <CommitList path={devCommitsPath({ window, person: person.key })} />
        </section>
      </div>
    );
  }

  if (pick.kind === "edge") {
    const person = map.people.find((p) => p.key === pick.person);
    const repo = repoOf(pick.repository);
    const edge = map.edges.find((e) => e.person === pick.person && e.repository === pick.repository && e.agent === pick.agent);
    if (!person || !repo || !edge) return null;
    return (
      <div className="space-y-6" data-testid="dev-edge-detail">
        <header className="flex items-center gap-3">
          <Avatar person={person} size={44} />
          <span aria-hidden className="h-[3px] w-10 rounded-full" style={{ background: AGENT_COLOUR[edge.agent] }} />
          <span className="grid h-11 w-11 place-items-center rounded-[0.8rem] border border-border bg-surface-2">
            <RepositoryMark repo={repo} />
          </span>
          <div className="min-w-0">
            <h3 className="truncate text-[1.0625rem] font-semibold text-ink">{person.name} → {repositoryTitle(repo)}</h3>
            <p className="text-micro font-semibold" style={{ color: AGENT_COLOUR[edge.agent] }}>{t.dyn("agent", edge.agent, AGENT_NAME[edge.agent])}</p>
          </div>
        </header>
        <dl className="grid grid-cols-3 gap-3">
          {[
            [t("person.commits"), fmt.number(edge.commits)],
            [t("person.changes"), `+${fmt.number(edge.additions)} −${fmt.number(edge.deletions)}`],
            [t("telemetry.cost"), edge.telemetry ? usd(edge.telemetry.cost_usd, locale) : "—"],
          ].map(([label, value]) => (
            <div key={label} className="rounded-control border border-border px-3 py-2.5">
              <dt className="text-[11px] text-ink-muted">{label}</dt>
              <dd data-tabular className="mt-0.5 font-mono text-[0.9375rem] font-semibold text-ink">{value}</dd>
            </div>
          ))}
        </dl>
        {edge.agent === "claude_code" ? (
          <section>
            <h4 className="mb-2.5 text-micro font-semibold text-ink-secondary">{t("person.telemetry")}</h4>
            <Telemetry telemetry={edge.telemetry ? { ...edge.telemetry, tools: person.telemetry?.tools, mcp_tools: person.telemetry?.mcp_tools, languages: person.telemetry?.languages, models: person.telemetry?.models } : null} />
          </section>
        ) : null}
        <section>
          <h4 className="mb-2.5 text-micro font-semibold text-ink-secondary">{t("commits.title")}</h4>
          <CommitList path={devCommitsPath({ window, person: person.key, repository: repo.id, agent: edge.agent })} />
        </section>
      </div>
    );
  }

  if (pick.kind === "repository") {
    const repo = repoOf(pick.id);
    if (!repo) return null;
    return <RepositoryDetail map={map} repo={repo} window={window} />;
  }

  const repo = repoOf(pick.repository);
  return (
    <div className="space-y-5" data-testid="dev-service-detail">
      <h3 className="text-[1.0625rem] font-semibold text-ink">
        {repo ? repositoryTitle(repo) : ""} · <span className="font-mono">{pick.key ?? t("map.repoLevel")}</span>
      </h3>
      {pick.key ? (
        <CommitList path={devCommitsPath({ window, repository: pick.repository, service: pick.key })} />
      ) : (
        <CommitList path={devCommitsPath({ window, repository: pick.repository })} />
      )}
    </div>
  );
}

function RepositoryDetail({ map, repo, window }: { map: MapData; repo: DevRepository; window: DevWindow }) {
  const t = useT("devAgents");
  const { locale } = useLocale();
  const services = map.services.filter((s) => s.repository === repo.id);
  const people = map.people.filter((p) => p.repositories.includes(repo.id));
  const max = Math.max(1, ...services.map((s) => s.commits));
  return (
    <div className="space-y-6" data-testid="dev-repo-detail">
      <header className="flex items-center gap-3">
        <span className="grid h-12 w-12 place-items-center rounded-[0.9rem] border border-border bg-surface-2">
          <RepositoryMark repo={repo} size={repo.catalogued ? "md" : "lg"} />
        </span>
        <div className="min-w-0">
          <h3
            className={`truncate font-bold tracking-[-0.02em] text-ink ${
              repo.catalogued ? "font-[family-name:var(--font-display)] text-[1.375rem]" : "font-mono text-[1.125rem]"
            }`}
          >
            {repositoryTitle(repo)}
          </h3>
          <a href={repo.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-mono text-micro text-ink-muted hover:text-brand-accent">
            {repo.full_name} <ExternalLink aria-hidden className="h-3 w-3" />
          </a>
        </div>
      </header>
      {repo.cost_usd !== null ? (
        <p className="text-caption text-ink-secondary">
          {t("repository.cost", { cost: usd(repo.cost_usd, locale) })}
        </p>
      ) : null}
      {repo.catalogued ? (
        <section>
          <h4 className="mb-2.5 text-micro font-semibold text-ink-secondary">{t("repository.services")}</h4>
          <ul className="space-y-1.5">
            {services.map((s) => (
              <li key={String(s.key)} className="grid grid-cols-[minmax(0,10rem)_minmax(0,1fr)_auto] items-center gap-2 text-[12px]">
                <span className={`truncate font-mono ${s.key ? "text-ink" : "text-ink-muted italic"}`}>{s.key ?? t("map.repoLevel")}</span>
                <span className="h-1.5 overflow-hidden rounded-full bg-surface-3">
                  <span className="block h-full rounded-full bg-brand-accent" style={{ width: `${(s.commits / max) * 100}%` }} />
                </span>
                <span data-tabular className="font-mono text-[11px] text-ink-muted">{s.commits}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : (
        <p className="rounded-control border border-dashed border-border bg-surface-2 px-3 py-2.5 text-micro text-ink-secondary" data-testid="dev-repo-not-catalogued">
          {t("repository.notCatalogued")}
        </p>
      )}
      <section>
        <h4 className="mb-2.5 text-micro font-semibold text-ink-secondary">{t("repository.people")}</h4>
        <ul className="flex flex-wrap gap-2">
          {people.map((p) => (
            <li key={p.key} className="inline-flex items-center gap-2 rounded-full border border-border bg-surface-2 py-1 pr-3 pl-1">
              <Avatar person={p} size={24} />
              <span className="text-caption text-ink">{p.name}</span>
            </li>
          ))}
        </ul>
      </section>
      <section>
        <h4 className="mb-2.5 text-micro font-semibold text-ink-secondary">{t("commits.title")}</h4>
        <CommitList path={devCommitsPath({ window, repository: repo.id })} />
      </section>
    </div>
  );
}

/* ---------------- connecting Claude Code ---------------- */

function ConnectClaudeCode({ connect }: { connect: DevConnect | null }) {
  const t = useT("devAgents");
  if (!connect) return <DataState kind="loading" />;
  const env = [
    "CLAUDE_CODE_ENABLE_TELEMETRY=1",
    "OTEL_METRICS_EXPORTER=otlp",
    "OTEL_LOGS_EXPORTER=otlp",
    `OTEL_EXPORTER_OTLP_PROTOCOL=${connect.protocol}`,
    `OTEL_EXPORTER_OTLP_ENDPOINT=${connect.endpoint ?? "<this deployment>/otlp"}`,
    'OTEL_EXPORTER_OTLP_HEADERS="Authorization=Bearer <ingest token>"',
    "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=delta",
    "OTEL_METRICS_INCLUDE_REPOSITORY=true",
    // The project comes from the repository Claude Code reports (2.1.269+);
    // a fixed tracesis.project would pin every session to one project.
    `OTEL_RESOURCE_ATTRIBUTES="tracesis.person=<your-github-login>"`,
  ];
  const managed = JSON.stringify(
    {
      env: Object.fromEntries(
        env.map((line) => {
          const [k, ...rest] = line.split("=");
          return [k, rest.join("=").replace(/^"|"$/g, "")];
        }),
      ),
    },
    null,
    2,
  );
  return (
    <div className="space-y-5" data-testid="dev-connect">
      <p className="text-body text-ink-secondary">{t("connect.lead")}</p>
      {!connect.endpoint ? <p className="rounded-control bg-warning-soft px-3 py-2 text-caption text-warning">{t("connect.off")}</p> : null}
      {!connect.collecting ? <p className="rounded-control bg-surface-2 px-3 py-2 text-caption text-ink-secondary">{t("connect.notCollecting")}</p> : null}
      <section className="space-y-2">
        <p className="text-caption text-ink">{t("connect.steps.env")}</p>
        <Snippet text={env.map((line) => `export ${line}`).join("\n")} />
        <Snippet text={managed} />
      </section>
      <p className="text-caption text-ink-secondary">
        {t("connect.steps.project", { projects: connect.projects.join(", ") || "—" })}
      </p>
      <p className="text-caption text-ink-secondary">{t("connect.steps.token")}</p>
      <p className="rounded-control border border-border bg-surface-2 px-3 py-2.5 text-micro text-ink-muted">{t("connect.privacy")}</p>
    </div>
  );
}

/* ---------------- the view ---------------- */

function MobileList({ map, onPick }: { map: MapData; onPick: (p: Pick) => void }) {
  const t = useT("devAgents");
  const { locale } = useLocale();
  return (
    <ul className="space-y-3" data-testid="dev-list">
      {map.people.map((person) => (
        <li key={person.key} className="rounded-2xl border border-border bg-surface p-3.5">
          <button type="button" onClick={() => onPick({ kind: "person", key: person.key })} className="flex w-full items-center gap-3 text-left">
            <Avatar person={person} />
            <span className="min-w-0 flex-1">
              <span className="block truncate font-semibold text-ink">{person.name}</span>
              <span className="text-micro text-ink-muted">
                {t("map.commits", { count: person.commits })}
                {person.telemetry ? ` · ${usd(person.telemetry.cost_usd, locale)}` : ""}
              </span>
            </span>
          </button>
          <ul className="mt-2.5 space-y-1.5">
            {map.edges.filter((e) => e.person === person.key).map((e) => {
              const repo = map.repositories.find((r) => r.id === e.repository);
              return (
                <li key={`${e.repository}-${e.agent}`}>
                  <button type="button" onClick={() => onPick({ kind: "edge", person: e.person, repository: e.repository, agent: e.agent })}
                    className="flex w-full items-center gap-2 rounded-control bg-surface-2 px-2.5 py-1.5 text-left text-[12px]">
                    <span aria-hidden className="h-2 w-2 rounded-full" style={{ background: AGENT_COLOUR[e.agent] }} />
                    {repo ? <RepositoryMark repo={repo} size="sm" /> : null}
                    <span className={`truncate text-ink ${repo && !repo.catalogued ? "font-mono" : ""}`}>{repo ? repositoryTitle(repo) : ""}</span>
                    <span className="ml-auto font-mono text-ink-muted">{t("map.edge", { agent: t.dyn("agent", e.agent, AGENT_NAME[e.agent]), count: e.commits })}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </li>
      ))}
    </ul>
  );
}

/** The map with only the catalog's repositories (and who worked there). */
export function catalogOnly(map: MapData): MapData {
  const keep = new Set(map.repositories.filter((r) => r.catalogued).map((r) => r.id));
  const edges = map.edges.filter((e) => keep.has(e.repository));
  const people = new Set(edges.map((e) => e.person));
  return {
    ...map,
    repositories: map.repositories.filter((r) => keep.has(r.id)),
    services: map.services.filter((s) => keep.has(s.repository)),
    edges,
    people: map.people
      .filter((p) => people.has(p.key))
      .map((p) => ({ ...p, repositories: p.repositories.filter((id) => keep.has(id)) })),
  };
}

export function DevelopmentView() {
  const t = useT("devAgents");
  const fmt = useFormat();
  const { locale } = useLocale();
  const [window, setWindow] = useState<DevWindow>("30d");
  const [state, setState] = useState<{ window: DevWindow; map: MapData | null; failed: boolean } | null>(null);
  const [pick, setPick] = useState<Pick | null>(null);
  const [scope, setScope] = useState<"all" | "catalog">("all");
  const [connectOpen, setConnectOpen] = useState(false);
  const [connect, setConnect] = useState<DevConnect | null>(null);
  const [ref, width] = useWidth<HTMLDivElement>(0);
  // The map sits inside a padded panel; the root is measured from first render.
  const mapWidth = width - PANEL_PADDING;
  /** The map draws lines; the narrow list does not, so its legend leaves them out. */
  const lines = mapWidth >= MAP_MIN_WIDTH;

  useEffect(() => {
    let active = true;
    fetchDevMap(window)
      .then((map) => active && setState({ window, map, failed: false }))
      .catch(() => active && setState({ window, map: null, failed: true }));
    return () => {
      active = false;
    };
  }, [window]);
  useEffect(() => {
    fetchDevConnect().then(setConnect).catch(() => setConnect(null));
  }, []);

  const loaded = state?.window === window ? state.map : null;
  const outside = loaded ? loaded.repositories.filter((r) => !r.catalogued).length : 0;
  const map = useMemo(
    () => (loaded && scope === "catalog" && outside ? catalogOnly(loaded) : loaded),
    [loaded, scope, outside],
  );
  const summary = loaded?.summary;
  const pickTitle = useMemo(() => {
    if (!map || !pick) return "";
    if (pick.kind === "person") return map.people.find((p) => p.key === pick.key)?.name ?? "";
    if (pick.kind === "repository") {
      const repo = map.repositories.find((r) => r.id === pick.id);
      return repo ? repositoryTitle(repo) : "";
    }
    if (pick.kind === "edge") return t.dyn("agent", pick.agent, AGENT_NAME[pick.agent]);
    return pick.key ?? t("map.repoLevel");
  }, [map, pick, t]);

  const empty = map && map.people.length === 0;
  const emptyReason = !map
    ? ""
    : !map.configured.commits && !map.configured.telemetry
      ? t("empty.notConfigured")
      : map.configured.repositories === 0
        ? t("empty.noRepos")
        : t("empty.quiet");

  return (
    <div ref={ref} className="space-y-5" data-testid="dev-view">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-3xl text-body text-ink-secondary">{t("lead")}</p>
        <div className="flex flex-wrap items-center gap-2">
          <SegmentedControl
            label={t("window.label")}
            value={window}
            onChange={(next) => {
              setWindow(next);
              setPick(null);
            }}
            options={(["7d", "30d", "90d"] as const).map((w) => ({ value: w, label: t(`window.${w}`) }))}
          />
          <Button icon={Plug} onClick={() => setConnectOpen(true)} data-testid="dev-connect-open">
            {t("connect.open")}
          </Button>
        </div>
      </div>

      {state === null || (state.window !== window && !state.failed) ? (
        <Panel><DataState kind="loading" /></Panel>
      ) : state.failed ? (
        <Panel><DataState kind="error" /></Panel>
      ) : null}

      {map && summary ? (
        <section className="overflow-hidden rounded-panel border border-border bg-surface" aria-label={t("title")}>
          <div className="grid divide-y divide-border sm:grid-cols-2 sm:divide-y-0 lg:grid-cols-4 lg:divide-x">
            <Figure
              icon={GitCommitHorizontal}
              label={t("summary.commits")}
              value={fmt.number(summary.commits)}
              note={t("summary.commitsNote", { repos: summary.repositories, catalogued: summary.catalogued })}
            />
            <Figure
              icon={Bot}
              label={t("summary.withAgent")}
              value={summary.commits ? `${fmt.number((summary.agent_commits / summary.commits) * 100, { maximumFractionDigits: 0 })}%` : "—"}
              note={t("summary.withAgentNote", { count: summary.agent_commits, total: summary.commits })}
            >
              <AgentMix agents={summary.agents} />
            </Figure>
            <Figure
              icon={Users}
              label={t("summary.people")}
              value={fmt.number(summary.people)}
              note={summary.top_agent ? `${t("summary.topAgent")}: ${t.dyn("agent", summary.top_agent, AGENT_NAME[summary.top_agent])}` : t("summary.topAgentNone")}
            />
            {summary.cost_usd !== null ? (
              <Figure
                icon={Coins}
                label={t("summary.cost")}
                value={usd(summary.cost_usd, locale)}
                note={t("summary.costNote", { tokens: compact(summary.tokens ?? 0, locale), sessions: summary.sessions ?? 0 })}
              />
            ) : (
              <div className="flex min-w-0 flex-col justify-center px-5 py-4">
                <p className="flex items-center gap-1.5 text-caption text-ink-muted"><Coins aria-hidden className="h-3.5 w-3.5" />{t("summary.cost")}</p>
                <p className="mt-1.5 text-body font-semibold text-ink">{t("summary.notConnected")}</p>
                <button type="button" onClick={() => setConnectOpen(true)} className="mt-1 inline-flex w-fit items-center gap-1 text-micro font-semibold text-brand-accent hover:underline">
                  {t("summary.notConnectedNote")} <ArrowUpRight aria-hidden className="h-3 w-3" />
                </button>
              </div>
            )}
          </div>
        </section>
      ) : null}

      {map && !empty ? (
        <Panel className="overflow-hidden">
          <PanelHeader
            title={t("map.title")}
            description={t("map.hint")}
            actions={
              outside ? (
                <SegmentedControl
                  label={t("scope.label")}
                  value={scope}
                  onChange={(next) => {
                    setScope(next);
                    setPick(null);
                  }}
                  options={[
                    { value: "all", label: t("scope.all", { count: loaded?.repositories.length ?? 0 }) },
                    { value: "catalog", label: t("scope.catalog", { count: loaded?.summary.catalogued ?? 0 }) },
                  ]}
                />
              ) : undefined
            }
          />
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[11px] text-ink-muted" data-testid="dev-legend">
            {/* On the map, lines are the person's colour and their pattern says how the work was done. */}
            {lines ? (
              <span className="inline-flex items-center gap-1.5">
                <span aria-hidden className="flex gap-0.5">
                  {map.people.slice(0, 3).map((p) => (
                    <span key={p.key} className="h-[3px] w-2 rounded-full" style={{ background: personColour(p.key) }} />
                  ))}
                </span>
                {t("legend.personColour")}
              </span>
            ) : null}
            {lines ? (
              <span className="inline-flex items-center gap-1.5">
                <svg aria-hidden width="22" height="6"><path d="M1 3 H21" stroke="currentColor" strokeWidth="2.5" /></svg>
                {t("legend.solid")}
              </span>
            ) : null}
            {lines && map.edges.some((e) => e.agent === "none" && e.commits) ? (
              <span className="inline-flex items-center gap-1.5">
                <svg aria-hidden width="22" height="6"><path d="M1 3 H21" stroke="currentColor" strokeWidth="2.5" strokeDasharray="5 3" /></svg>
                {t("legend.dashed")}
              </span>
            ) : null}
            {lines && map.edges.some((e) => !e.commits) ? (
              <span className="inline-flex items-center gap-1.5">
                <svg aria-hidden width="22" height="6"><path d="M2 3 H21" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeDasharray="0.5 4" /></svg>
                {t("legend.dotted")}
              </span>
            ) : null}
            {CODING_AGENTS.filter((a) => a !== "none" && summary?.agents[a]).map((a) => (
              <span key={a} className="inline-flex items-center gap-1.5">
                <span aria-hidden className="h-2 w-2 rounded-full" style={{ background: AGENT_COLOUR[a] }} />
                {t.dyn("agent", a, AGENT_NAME[a])}
              </span>
            ))}
            {outside && scope === "all" ? (
              <span className="inline-flex items-center gap-1.5">
                <span aria-hidden className="h-3 w-5 rounded-[4px] border border-dashed border-border-strong" />
                {t("legend.notCatalogued")}
              </span>
            ) : null}
          </div>
          <div className="mt-4">
            {mapWidth >= MAP_MIN_WIDTH ? (
              <DevMap map={map} width={mapWidth} picked={pick} onPick={setPick} />
            ) : (
              <MobileList map={map} onPick={setPick} />
            )}
          </div>
          <p className="mt-4 text-micro text-ink-muted">{t("note")}</p>
        </Panel>
      ) : null}

      {map && empty ? (
        <Panel data-testid="dev-empty">
          <div className="flex flex-col items-center gap-3 py-10 text-center">
            <span className="grid h-14 w-14 place-items-center rounded-2xl bg-surface-2 text-ink-muted"><GitCommitHorizontal aria-hidden className="h-6 w-6" /></span>
            <h3 className="text-[1.125rem] font-semibold text-ink">{t("empty.title")}</h3>
            <p className="max-w-lg text-caption text-ink-secondary">{emptyReason}</p>
            <Button icon={Plug} onClick={() => setConnectOpen(true)}>{t("connect.open")}</Button>
          </div>
        </Panel>
      ) : null}

      <Drawer open={Boolean(pick && map)} onClose={() => setPick(null)} title={pickTitle} width="lg">
        {pick && map ? <Detail map={map} pick={pick} window={window} /> : null}
      </Drawer>
      <Drawer open={connectOpen} onClose={() => setConnectOpen(false)} title={t("connect.title")} width="lg">
        <ConnectClaudeCode connect={connect} />
      </Drawer>
    </div>
  );
}


