"use client";

/**
 * MCP servers: what agents call. One card per server — how busy it is,
 * which tools it serves (bar per tool, failures in red), which AI
 * clients call it (one share bar, a colour per client) and who used it
 * (the people the calls name, by name — never by email).
 */

import { Plug, Server, Users } from "lucide-react";
import { useEffect, useState } from "react";

import {
  Avatar,
  AvatarStack,
  displayName,
  personColor,
} from "@/components/agents/People";
import { MiniArea } from "@/components/agents/visuals";
import { DataState } from "@/components/state/DataState";
import { Panel } from "@/components/ui/Panel";
import { ProjectLogo } from "@/components/ui/ProjectLogo";
import {
  compact,
  fetchMcpServers,
  STEP_COLOR,
  type McpServer,
  type Window,
} from "@/lib/agents";
import { useFormat, useLocale, useT } from "@/lib/i18n";

const FAILING = 0.05;

/** Clients take colours in their order on the card, so no two collide. */
function clientColor(
  name: string | null,
  clients: { name: string | null }[],
): string {
  if (!name) return "var(--text-muted)";
  const i = clients.filter((c) => c.name).findIndex((c) => c.name === name);
  return `var(--series-${(Math.max(0, i) % 6) + 1})`;
}

function ServerCard({ server }: { server: McpServer }) {
  const t = useT("agents");
  const fmt = useFormat();
  const { locale } = useLocale();
  const name = server.service_name || server.workload || "MCP";
  const ratio = server.calls ? server.errors / server.calls : 0;
  const place = [server.namespace, server.workload].filter(Boolean).join("/");
  const maxTool = Math.max(1, ...server.tools.map((x) => x.calls));
  const clients = server.clients;
  const totalClients = clients.reduce((a, c) => a + c.calls, 0) || 1;
  const shownTools = server.tools.slice(0, 6);

  return (
    <Panel data-testid={`mcp-server-${name}`} className="flex flex-col gap-4">
      <div className="flex items-start gap-3">
        <span className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-control bg-brand-soft text-brand">
          <Plug aria-hidden className="h-5 w-5" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-body font-semibold text-ink">{name}</p>
          <p className="mt-0.5 flex min-w-0 items-center gap-1.5 text-micro text-ink-muted">
            {server.project ? (
              <ProjectLogo
                projectId={server.project.id}
                name={server.project.key}
                size="sm"
                variant="mark"
              />
            ) : null}
            <span className="truncate font-mono">
              {server.project
                ? `${server.project.key}${server.environment ? `/${server.environment}` : ""}`
                : t("mcp.outside")}
              {place ? ` · ${place}` : ""}
            </span>
          </p>
        </div>
        <time
          dateTime={server.last_seen_at}
          title={fmt.utc(server.last_seen_at)}
          className="shrink-0 text-micro text-ink-muted"
        >
          {t("mcp.seen", { when: fmt.relative(server.last_seen_at) })}
        </time>
      </div>

      <dl className="grid grid-cols-4 gap-2">
        {[
          { label: t("mcp.calls"), value: compact(server.calls, locale) },
          {
            label: t("mcp.failed"),
            value: server.calls
              ? `${fmt.number(ratio * 100, { maximumFractionDigits: 1 })}%`
              : "—",
            bad: ratio > FAILING,
          },
          {
            label: t("mcp.avg"),
            value: server.avg_ms === null ? "—" : fmt.latency(server.avg_ms),
          },
          { label: t("mcp.toolCount"), value: String(server.tools.length) },
        ].map((stat) => (
          <div
            key={stat.label}
            className="min-w-0 rounded-control bg-surface-2 px-2.5 py-2"
          >
            <dt className="truncate text-micro text-ink-muted">{stat.label}</dt>
            <dd
              className={`text-body font-semibold tabular-nums ${stat.bad ? "text-critical" : "text-ink"}`}
            >
              {stat.value}
            </dd>
          </div>
        ))}
      </dl>

      <MiniArea
        points={server.series.map((p) => ({
          calls: p.calls,
          errors: p.errors,
        }))}
        color={STEP_COLOR.tool}
        label={t("mcp.activity", { name })}
      />

      <div className="grid gap-5 md:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <section>
          <h3 className="mb-2 text-micro font-semibold tracking-wide text-ink-muted uppercase">
            {t("mcp.tools")}
          </h3>
          <ul className="space-y-2">
            {shownTools.map((tool) => {
              const failed = tool.calls ? tool.errors / tool.calls : 0;
              return (
                <li key={tool.name ?? ""} className="space-y-1">
                  <div className="flex items-baseline justify-between gap-2 text-micro">
                    <span className="flex min-w-0 items-center gap-2">
                      <span
                        className="truncate font-mono text-ink"
                        title={tool.name ?? ""}
                      >
                        {tool.name ?? "—"}
                      </span>
                      <AvatarStack
                        people={tool.users ?? []}
                        testId={`mcp-tool-users-${tool.name ?? ""}`}
                      />
                    </span>
                    <span className="shrink-0 tabular-nums text-ink-secondary">
                      <span className="font-semibold text-ink">
                        {compact(tool.calls, locale)}
                      </span>
                      {tool.errors ? (
                        <span className="text-critical">
                          {" "}
                          ·{" "}
                          {fmt.number(failed * 100, {
                            maximumFractionDigits: 1,
                          })}
                          %
                        </span>
                      ) : null}
                      {tool.avg_ms !== null
                        ? ` · ${fmt.latency(tool.avg_ms)}`
                        : ""}
                    </span>
                  </div>
                  <span
                    className="relative block h-1.5 overflow-hidden rounded-full bg-surface-3"
                    aria-hidden
                  >
                    <span
                      className="absolute inset-y-0 left-0 rounded-full"
                      style={{
                        width: `${(tool.calls / maxTool) * 100}%`,
                        background: STEP_COLOR.tool,
                      }}
                    />
                    {tool.errors ? (
                      <span
                        className="absolute inset-y-0 bg-[var(--status-critical)]"
                        style={{
                          left: `${((tool.calls - tool.errors) / maxTool) * 100}%`,
                          width: `${(tool.errors / maxTool) * 100}%`,
                        }}
                      />
                    ) : null}
                  </span>
                </li>
              );
            })}
          </ul>
          {server.tools.length > shownTools.length ? (
            <p className="mt-2 text-micro text-ink-muted">
              {t("mcp.more", {
                count: server.tools.length - shownTools.length,
              })}
            </p>
          ) : null}
        </section>

        <section>
          <h3 className="mb-2 text-micro font-semibold tracking-wide text-ink-muted uppercase">
            {t("mcp.clients")}
          </h3>
          <div
            className="flex h-2.5 w-full overflow-hidden rounded-full bg-surface-3"
            aria-hidden
          >
            {clients.map((c) => (
              <span
                key={c.name ?? ""}
                className="h-full"
                style={{
                  width: `${(c.calls / totalClients) * 100}%`,
                  background: clientColor(c.name, clients),
                }}
              />
            ))}
          </div>
          <ul className="mt-2.5 space-y-1.5">
            {clients.map((c) => (
              <li
                key={c.name ?? ""}
                className="flex items-center justify-between gap-2 text-micro"
              >
                <span className="flex min-w-0 items-center gap-1.5">
                  <span
                    className="h-2 w-2 shrink-0 rounded-full"
                    style={{
                      background: clientColor(c.name, clients),
                    }}
                  />
                  <span
                    className={`truncate ${c.name ? "font-medium text-ink" : "text-ink-muted italic"}`}
                  >
                    {c.name ?? t("mcp.unknownClient")}
                  </span>
                  <AvatarStack people={c.users ?? []} max={2} />
                </span>
                <span className="shrink-0 tabular-nums text-ink-secondary">
                  {fmt.number((c.calls / totalClients) * 100, {
                    maximumFractionDigits: 0,
                  })}
                  %
                </span>
              </li>
            ))}
          </ul>
        </section>
      </div>

      <UsedBy server={server} />
    </Panel>
  );
}

/** Who used the server: one row per person, busiest first, with the
 * clients they came through and when they were last seen. */
function UsedBy({ server }: { server: McpServer }) {
  const t = useT("agents");
  const fmt = useFormat();
  const { locale } = useLocale();
  const people = server.users ?? [];
  const named = server.attributed_calls ?? 0;
  const busiest = Math.max(1, ...people.map((p) => p.calls));
  return (
    <section
      className="border-t border-border pt-4"
      data-testid={`mcp-used-by-${server.service_name ?? server.id}`}
    >
      <div className="mb-2.5 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h3 className="flex items-center gap-1.5 text-micro font-semibold tracking-wide text-ink-muted uppercase">
          <Users aria-hidden className="h-3.5 w-3.5" />
          {t("mcp.usedBy")}
          {people.length ? (
            <span className="font-normal tracking-normal normal-case">
              · {t("mcp.people", { count: people.length })}
            </span>
          ) : null}
        </h3>
        {people.length ? (
          <p className="text-micro text-ink-muted tabular-nums">
            {named >= server.calls
              ? t("mcp.attributedAll")
              : t("mcp.attributed", {
                  named: compact(named, locale),
                  total: compact(server.calls, locale),
                })}
          </p>
        ) : null}
      </div>
      {people.length === 0 ? (
        <p className="text-micro text-ink-muted">{t("mcp.noPeople")}</p>
      ) : (
        <ul className="grid gap-x-6 gap-y-2.5 sm:grid-cols-2">
          {people.slice(0, 8).map((person) => {
            const name = displayName(person, t("mcp.unnamedPerson"));
            return (
              <li
                key={person.key}
                className="flex min-w-0 items-center gap-2.5"
                data-testid="mcp-person"
              >
                <Avatar person={person} size="md" />
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline justify-between gap-2">
                    <span
                      className="truncate text-caption font-medium text-ink"
                      title={person.id ? `${name} · ${person.id}` : name}
                    >
                      {name}
                    </span>
                    <span className="shrink-0 text-micro tabular-nums text-ink-secondary">
                      <span className="font-semibold text-ink">
                        {compact(person.calls, locale)}
                      </span>
                      {person.errors ? (
                        <span className="text-critical">
                          {" "}
                          · {compact(person.errors, locale)}
                        </span>
                      ) : null}
                    </span>
                  </div>
                  <span
                    className="mt-1 block h-1 overflow-hidden rounded-full bg-surface-3"
                    aria-hidden
                  >
                    <span
                      className="block h-full rounded-full"
                      style={{
                        width: `${(person.calls / busiest) * 100}%`,
                        background: personColor(person.key),
                      }}
                    />
                  </span>
                  <p className="mt-1 truncate text-micro text-ink-muted">
                    {person.clients?.length
                      ? `${t("mcp.via", { clients: person.clients.join(", ") })} · `
                      : ""}
                    <time
                      dateTime={person.last_seen_at}
                      title={fmt.utc(person.last_seen_at)}
                    >
                      {fmt.relative(person.last_seen_at)}
                    </time>
                  </p>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

export function McpServers({ span }: { span: Window }) {
  const t = useT("agents");
  const [servers, setServers] = useState<McpServer[] | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let active = true;
    setServers(null);
    setFailed(false);
    fetchMcpServers(span)
      .then((data) => active && setServers(data.servers))
      .catch(() => active && setFailed(true));
    return () => {
      active = false;
    };
  }, [span]);

  if (failed)
    return (
      <Panel>
        <DataState kind="error" />
      </Panel>
    );
  if (!servers)
    return (
      <Panel>
        <DataState kind="loading" />
      </Panel>
    );
  if (servers.length === 0) {
    return (
      <Panel data-testid="mcp-empty">
        <div className="flex flex-col items-center gap-3 py-10 text-center">
          <span className="inline-flex h-12 w-12 items-center justify-center rounded-panel bg-brand-soft text-brand">
            <Server aria-hidden className="h-6 w-6" />
          </span>
          <h2 className="text-body font-semibold text-ink">
            {t("mcp.emptyTitle")}
          </h2>
          <p className="max-w-lg text-caption text-ink-secondary">
            {t("mcp.emptyBody")}
          </p>
        </div>
      </Panel>
    );
  }
  return (
    <div className="space-y-4">
      <p className="text-caption text-ink-secondary">{t("mcp.hint")}</p>
      <div
        className="grid grid-cols-1 gap-4 xl:grid-cols-2"
        data-testid="mcp-grid"
      >
        {servers.map((server) => (
          <ServerCard key={server.id} server={server} />
        ))}
      </div>
    </div>
  );
}
