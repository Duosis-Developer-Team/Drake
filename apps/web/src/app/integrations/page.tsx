"use client";

import Link from "next/link";
import type { LucideIcon } from "lucide-react";
import {
  ArrowRight,
  Boxes,
  CircleCheck,
  DatabaseBackup,
  Github,
  Layers,
  Plug,
  TriangleAlert,
} from "lucide-react";

import { LoadGate, useApi } from "@/components/catalog/primitives";
import { Donut } from "@/components/charts/visuals";
import {
  IconBubble,
  KpiTile,
  PILL_BUTTON,
  ShareBar,
  StateCard,
} from "@/components/features/configure/kit";
import { RichMessage } from "@/components/github/primitives";
import { PageFrame, PageHeader } from "@/components/shell/AppShell";
import { StatusBadge, type HealthStatus } from "@/components/state/StatusBadge";
import { Panel, PanelHeader } from "@/components/ui/Panel";
import type { IntegrationHealth } from "@/lib/catalog";
import { useFormat, useT, type Translator } from "@/lib/i18n";

const OBSERVED_STATUS: Record<string, HealthStatus> = {
  ok: "healthy",
  degraded: "critical",
  stale: "stale",
  unknown: "unknown",
  not_configured: "unknown",
};

/** Providers the screen knows by name; their words live in `provider.<type>`. */
const PROVIDER_ICONS: Record<string, LucideIcon> = {
  github: Github,
  "cluster-agent": Boxes,
  "backup-reporter": DatabaseBackup,
};

function providerSpec(type: string, t: Translator<"integrations">) {
  const known = t.has(`provider.${type}.name`);
  return {
    // An unknown connector type is an identifier, not copy: shown as itself,
    // lightly tidied.
    name: known
      ? t.dyn(`provider.${type}`, "name", type)
      : type.replace(/[-_]+/g, " ").replace(/^\w/, (c) => c.toUpperCase()),
    icon: PROVIDER_ICONS[type] ?? Plug,
    blurb: known ? t.dyn(`provider.${type}`, "blurb", "") : t("provider.genericBlurb"),
  };
}

/** Where the screen has a place to manage a provider, the tile links to it.
 *  Every other connector is configured by an operator out of band. */
const MANAGE_HREF: Record<string, string> = { github: "/integrations/github" };

function scopeKey(integration: IntegrationHealth) {
  return `${integration.scope.type}/${integration.scope.ref}`;
}

function IntegrationsOverview({
  integrations,
}: {
  integrations: IntegrationHealth[];
}) {
  const t = useT("integrations");
  const providers = new Set(integrations.map((i) => i.integration_type)).size;
  const scopes = new Set(integrations.map(scopeKey)).size;
  const configured = integrations.filter(
    (i) => i.configuration_state === "configured",
  ).length;
  const ok = integrations.filter((i) => i.observed_state === "ok").length;
  const attention = integrations.filter(
    (i) =>
      i.observed_state === "degraded" ||
      i.observed_state === "stale" ||
      i.last_error_code,
  ).length;

  return (
    <div className="page-grid" data-testid="integrations-stats">
      <KpiTile icon={Plug} label={t("stats.providers")} value={providers}>
        <p className="text-micro text-ink-muted">
          {t("stats.connectorsAcross", { connectors: integrations.length, scopes })}
        </p>
      </KpiTile>
      <KpiTile
        icon={Layers}
        tone="info"
        label={t("stats.connected")}
        value={configured}
        suffix={t("stats.of", { total: integrations.length })}
      >
        <ShareBar
          value={configured}
          total={integrations.length}
          tone="info"
          label={t("stats.configured")}
        />
      </KpiTile>
      <KpiTile
        icon={CircleCheck}
        tone="success"
        label={t("stats.answeringOk")}
        value={ok}
        suffix={t("stats.of", { total: integrations.length })}
      >
        <ShareBar
          value={ok}
          total={integrations.length}
          tone="success"
          label={t("stats.reportingNormally")}
        />
      </KpiTile>
      <KpiTile
        icon={TriangleAlert}
        tone={attention > 0 ? "warning" : undefined}
        label={t("stats.needsAttention")}
        value={attention}
      >
        <p className="text-micro text-ink-muted">{t("stats.needsAttentionHint")}</p>
      </KpiTile>
    </div>
  );
}

function ProviderTile({
  type,
  entries,
}: {
  type: string;
  entries: IntegrationHealth[];
}) {
  const t = useT("integrations");
  const fmt = useFormat();
  const spec = providerSpec(type, t);
  const connected = entries.filter(
    (e) => e.configuration_state === "configured",
  ).length;
  const manageHref = MANAGE_HREF[type];
  const latest = entries
    .map((e) => e.last_success_at)
    .filter((value): value is string => Boolean(value))
    .sort()
    .at(-1);

  return (
    <article
      aria-label={spec.name}
      className="flex h-full flex-col rounded-[1.5rem] border border-border bg-surface shadow-panel"
    >
      <div className="flex items-center gap-4 px-6 pt-6">
        <span
          aria-hidden
          className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl ${
            connected > 0
              ? "bg-accent text-ink-inverse"
              : "bg-surface-3 text-ink-secondary"
          }`}
        >
          <spec.icon className="h-6 w-6" />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-[1.0625rem] font-semibold text-ink">
            {spec.name}
          </h3>
          <p className="truncate text-caption text-ink-muted">{spec.blurb}</p>
        </div>
      </div>

      <div className="mt-5 flex items-center justify-between gap-3 px-6">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex gap-1" aria-hidden>
            {entries.map((entry) => (
              <span
                key={scopeKey(entry)}
                className={`h-1.5 w-5 rounded-full ${
                  entry.configuration_state === "configured"
                    ? "bg-info"
                    : "bg-surface-3"
                }`}
              />
            ))}
          </div>
          <span className="truncate text-micro text-ink-muted">
            {t("provider.scopes", { connected, total: entries.length })}
          </span>
        </div>
        <span
          className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-micro font-medium whitespace-nowrap ${
            connected > 0
              ? "bg-info-soft text-info"
              : "bg-surface-3 text-ink-muted"
          }`}
        >
          <span
            aria-hidden
            className={`h-1.5 w-1.5 rounded-full ${connected > 0 ? "bg-info" : "bg-ink-muted"}`}
          />
          {connected > 0 ? t("provider.connected") : t("provider.notConnected")}
        </span>
      </div>

      <ul className="mt-4 flex-1 divide-y divide-border border-t border-border">
        {entries.map((integration) => (
          <li
            key={scopeKey(integration)}
            className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 px-6 py-3.5"
          >
            <div className="min-w-0">
              <p className="truncate text-body font-medium text-ink">
                {integration.scope.ref}
              </p>
              <p className="truncate text-micro text-ink-muted">
                <RichMessage
                  template={t("provider.lastSync")}
                  parts={{
                    type: integration.scope.type,
                    time: integration.last_success_at ? (
                      <time
                        dateTime={integration.last_success_at}
                        title={fmt.utc(integration.last_success_at)}
                      >
                        {fmt.relative(integration.last_success_at)}
                      </time>
                    ) : (
                      <span>{t("provider.never")}</span>
                    ),
                  }}
                />
              </p>
              {integration.last_error_code ? (
                <p className="mt-0.5 font-mono text-micro text-critical">
                  {integration.last_error_code}
                </p>
              ) : null}
            </div>
            <div className="flex shrink-0 flex-wrap items-center gap-1.5">
              <span className="rounded-full border border-border bg-surface-2 px-2 py-0.5 font-mono text-micro text-ink-secondary">
                {integration.configuration_state}
              </span>
              <StatusBadge
                status={
                  OBSERVED_STATUS[integration.observed_state] ?? "unknown"
                }
                label={integration.observed_state}
                size="compact"
              />
            </div>
          </li>
        ))}
      </ul>

      {/* mt-auto: when the gallery rows stretch to the aside's height, the
          footer stays on the card's bottom edge instead of floating. */}
      <div className="mt-auto flex items-center justify-between gap-3 border-t border-border px-6 py-4">
        <span className="min-w-0 truncate text-micro text-ink-muted">
          {latest ? (
            <RichMessage
              template={t("provider.latestSuccess")}
              parts={{ time: <time dateTime={latest}>{fmt.relative(latest)}</time> }}
            />
          ) : (
            t("provider.noSuccess")
          )}
        </span>
        {manageHref ? (
          <Link href={manageHref} className={PILL_BUTTON}>
            {t("provider.manage")}
            <ArrowRight aria-hidden className="h-3.5 w-3.5" />
          </Link>
        ) : (
          <span
            className="rounded-full bg-surface-2 px-3 py-1.5 text-micro font-medium whitespace-nowrap text-ink-muted"
            title={t("provider.operatorManagedHint")}
          >
            {t("provider.operatorManaged")}
          </span>
        )}
      </div>
    </article>
  );
}

function HealthSummary({
  integrations,
}: {
  integrations: IntegrationHealth[];
}) {
  const t = useT("integrations");
  const tc = useT("common");
  const count = (state: string) =>
    integrations.filter((i) => i.observed_state === state).length;
  const ok = count("ok");
  const degraded = count("degraded");
  const stale = count("stale");
  const rest = integrations.length - ok - degraded - stale;
  const scopes = [...new Set(integrations.map(scopeKey))];

  return (
    <div className="page-aside">
      <Panel className="h-full">
        <PanelHeader
          title={t("health.title")}
          description={t("health.description")}
        />
        <Donut
          label={t("health.donutLabel")}
          size={148}
          thickness={16}
          slices={[
            { name: t("health.ok"), value: ok, tone: "success" },
            { name: tc("health.degraded"), value: degraded, tone: "critical" },
            { name: tc("health.stale"), value: stale, tone: "stale" },
            { name: t("health.notReporting"), value: rest, tone: "unknown" },
          ]}
        />
      </Panel>
      <Panel className="h-full">
        <PanelHeader
          title={t("health.coverageTitle")}
          description={t("health.coverageDescription")}
        />
        <ul className="space-y-4">
          {scopes.map((key) => {
            const inScope = integrations.filter((i) => scopeKey(i) === key);
            const connected = inScope.filter(
              (i) => i.configuration_state === "configured",
            );
            return (
              <li key={key} className="flex items-center gap-3">
                <IconBubble icon={Layers} size="sm" />
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="truncate text-caption font-medium text-ink">
                      {key}
                    </span>
                    <span data-tabular className="text-micro text-ink-muted">
                      {connected.length}/{inScope.length}
                    </span>
                  </div>
                  <div className="mt-1.5 flex gap-1" aria-hidden>
                    {inScope.map((i) => {
                      const spec = providerSpec(i.integration_type, t);
                      const Icon = spec.icon;
                      const on = i.configuration_state === "configured";
                      return (
                        <span
                          key={i.integration_type}
                          title={t(
                            on ? "provider.coverageConnected" : "provider.coverageNotConnected",
                            { name: spec.name },
                          )}
                          className={`flex h-6 flex-1 items-center justify-center rounded-full ${
                            on
                              ? "bg-info-soft text-info"
                              : "bg-surface-3 text-ink-muted"
                          }`}
                        >
                          <Icon className="h-3 w-3" />
                        </span>
                      );
                    })}
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      </Panel>
    </div>
  );
}

export default function IntegrationsPage() {
  const t = useT("integrations");
  const [health, retry] = useApi<{
    integrations: IntegrationHealth[];
    next_cursor: string | null;
  }>("/v1/integrations/health");

  return (
    <PageFrame>
      <PageHeader
        title={t("overview.title")}
        description={t("overview.description")}
        actions={
          <Link href="/integrations/github" className={PILL_BUTTON}>
            <Github aria-hidden className="h-4 w-4" />
            {t("overview.githubLink")}
          </Link>
        }
      />
      <div className="space-y-6">
        <LoadGate value={health} retry={retry}>
          {(body) => {
            if (body.integrations.length === 0) {
              return (
                <Panel>
                  <StateCard
                    kind="empty"
                    icon={Plug}
                    title={t("overview.empty.title")}
                    description={t("overview.empty.description")}
                  />
                </Panel>
              );
            }
            const byType = new Map<string, IntegrationHealth[]>();
            for (const integration of body.integrations) {
              byType.set(integration.integration_type, [
                ...(byType.get(integration.integration_type) ?? []),
                integration,
              ]);
            }
            return (
              <>
                <IntegrationsOverview integrations={body.integrations} />
                <div className="page-split">
                  <section
                    aria-labelledby="provider-gallery"
                    className="page-main"
                  >
                    <h2
                      id="provider-gallery"
                      className="-mb-2 text-[1.0625rem] font-semibold text-ink"
                    >
                      {t("overview.providersHeading")}
                    </h2>
                    <div
                      className="grid grid-cols-1 items-stretch gap-6 lg:grid-cols-2"
                      data-testid="integration-table"
                    >
                      {[...byType.entries()].map(([type, entries]) => (
                        <ProviderTile
                          key={type}
                          type={type}
                          entries={entries}
                        />
                      ))}
                    </div>
                  </section>
                  <HealthSummary integrations={body.integrations} />
                </div>
              </>
            );
          }}
        </LoadGate>
      </div>
    </PageFrame>
  );
}
