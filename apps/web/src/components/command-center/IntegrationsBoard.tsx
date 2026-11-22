"use client";

/**
 * Integrations at a glance: one tile per provider, each with the scopes it
 * is connected for.
 *
 * A provider that reports for four projects is one thing to look at, not
 * four rows: its tile carries its state, its scopes as chips with their own
 * state dots, and when it last reported. Providers nobody connected stay
 * visible but dimmed and compact — an absence is not a failure, and it is
 * not hidden either. Every tile opens Integrations.
 */

import { Activity, BellRing, DatabaseBackup, Github, Plug, Server, type LucideIcon } from "lucide-react";
import Link from "next/link";

import { PanelHeader } from "@/components/ui/Panel";
import { RelativeTime } from "@/components/ui/identifiers";
import { DeniedState, ErrorState, LoadingSkeleton, NotConfiguredState } from "@/components/ui/states";
import type { IntegrationHealth } from "@/lib/catalog";
import { compareTone, humanize, toneForHealth, toneSpec, type StatusTone } from "@/lib/design/status";
import { useT } from "@/lib/i18n";
import type { Resource } from "@/lib/useResource";

type Kind = "metrics" | "alerting" | "source" | "cluster" | "protection" | "other";

/**
 * Keyed by the provider's display name (`humanize` of its type), the same
 * words the tile shows. Browser code never spells a provider's lowercase
 * identifier (src/test/provider-guard.test.ts): the browser talks to the
 * Tracesis API, never to a provider.
 */
const PROVIDERS: Record<string, { icon: LucideIcon; kind: Kind }> = {
  Prometheus: { icon: Activity, kind: "metrics" },
  Alertmanager: { icon: BellRing, kind: "alerting" },
  Github: { icon: Github, kind: "source" },
  "Cluster agent": { icon: Server, kind: "cluster" },
  "Backup reporter": { icon: DatabaseBackup, kind: "protection" },
};

interface Provider {
  type: string;
  entries: IntegrationHealth[];
  configured: IntegrationHealth[];
  worst: StatusTone | null;
  lastOk: string | null;
}

function providersOf(all: IntegrationHealth[]): Provider[] {
  const byType = new Map<string, IntegrationHealth[]>();
  for (const entry of all) byType.set(entry.integration_type, [...(byType.get(entry.integration_type) ?? []), entry]);
  const providers = [...byType.entries()].map(([type, entries]) => {
    const configured = entries.filter((e) => e.configuration_state === "configured");
    const tones = configured.map((e) => toneForHealth(e.observed_state)).sort(compareTone);
    const lastOk = configured
      .map((e) => e.last_success_at)
      .filter((v): v is string => Boolean(v))
      .sort()
      .at(-1) ?? null;
    return { type, entries, configured, worst: tones[0] ?? null, lastOk };
  });
  // Connected providers first, worst first among them; the rest by name.
  return providers.sort(
    (a, b) =>
      Number(b.configured.length > 0) - Number(a.configured.length > 0) ||
      (a.worst && b.worst ? compareTone(a.worst, b.worst) : 0) ||
      a.type.localeCompare(b.type),
  );
}

function ProviderTile({ provider }: { provider: Provider }) {
  const t = useT("commandCenter");
  const meta = PROVIDERS[humanize(provider.type)] ?? { icon: Plug, kind: "other" as const };
  const Icon = meta.icon;
  const connected = provider.configured.length > 0;
  const spec = toneSpec(provider.worst ?? "not-applicable");
  const name = humanize(provider.type);
  const stateOf = (entry: IntegrationHealth): string =>
    entry.configuration_state === "configured"
      ? t.dyn("token", entry.observed_state, humanize(entry.observed_state))
      : t("integrations.notConnected");
  const chips = [...provider.entries].sort(
    (a, b) =>
      Number(b.configuration_state === "configured") - Number(a.configuration_state === "configured") ||
      a.scope.ref.localeCompare(b.scope.ref),
  );
  return (
    <li className="min-w-0" data-testid={`integration-tile-${provider.type}`}>
      <Link
        href="/integrations"
        className={`group flex h-full flex-col gap-3 rounded-xl border p-4 transition-[border-color,box-shadow,transform] duration-200 hover:-translate-y-0.5 hover:border-border-strong hover:shadow-[0_14px_30px_-22px_rgb(0_0_0/0.5)] ${
          connected ? "border-border bg-surface" : "border-dashed border-border bg-transparent opacity-70 hover:opacity-100"
        }`}
      >
        <span className="flex items-center gap-3">
          <span
            aria-hidden
            className={`relative flex h-10 w-10 shrink-0 items-center justify-center rounded-xl ${
              connected ? "bg-brand-soft text-brand-accent" : "bg-surface-2 text-ink-muted"
            }`}
          >
            <Icon className="h-5 w-5" />
            {connected ? (
              <span className={`absolute -right-0.5 -bottom-0.5 h-3 w-3 rounded-full ring-2 ring-surface ${spec.dot}`} />
            ) : null}
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-body font-semibold text-ink">{name}</span>
            <span className="block truncate text-micro text-ink-muted">
              {t(`integrations.kind.${meta.kind}`)}
              {" · "}
              <span className={connected ? `font-semibold ${spec.text}` : ""}>
                {connected
                  ? t("integrations.connectedOf", { connected: provider.configured.length, total: provider.entries.length })
                  : t("integrations.notConnected")}
              </span>
            </span>
          </span>
        </span>
        <ul className="flex flex-wrap gap-1.5" aria-label={name}>
          {chips.map((entry) => {
            const on = entry.configuration_state === "configured";
            return (
              <li
                key={`${entry.scope.type}:${entry.scope.ref}`}
                title={t("integrations.providerState", { name: `${entry.scope.type}:${entry.scope.ref}`, state: stateOf(entry) })}
                className={`inline-flex max-w-full items-center gap-1.5 rounded-full px-2 py-0.5 font-mono text-[11px] ${
                  on ? "bg-surface-2 text-ink-secondary" : "border border-dashed border-border text-ink-muted"
                }`}
              >
                <span
                  aria-hidden
                  className={`h-1.5 w-1.5 shrink-0 rounded-full ${on ? toneSpec(toneForHealth(entry.observed_state)).dot : "bg-surface-3"}`}
                />
                <span className="truncate">{entry.scope.ref}</span>
                <span className="sr-only">{stateOf(entry)}</span>
              </li>
            );
          })}
        </ul>
        {connected ? (
          <span className="mt-auto text-micro text-ink-muted">
            {t("integrations.lastOk")}{" "}
            {provider.lastOk ? (
              <RelativeTime value={provider.lastOk} className="font-medium text-ink-secondary" />
            ) : (
              <span className="text-warning">{t("integrations.never")}</span>
            )}
          </span>
        ) : null}
      </Link>
    </li>
  );
}

const SEGMENT: Record<string, string> = {
  success: "bg-healthy",
  warning: "bg-warning",
  critical: "bg-critical",
  stale: "bg-stale",
  unknown: "bg-unknown",
  off: "bg-surface-3",
};

export function IntegrationsBoard({ resource }: { resource: Resource<{ integrations: IntegrationHealth[] }> }) {
  const t = useT("commandCenter");
  const all = resource.data?.integrations ?? [];
  const configured = all.filter((entry) => entry.configuration_state === "configured");
  const ok = configured.filter((entry) => entry.observed_state === "ok").length;
  const providers = providersOf(all);
  const segments = new Map<string, number>();
  for (const entry of all) {
    const key = entry.configuration_state === "configured" ? toneForHealth(entry.observed_state) : "off";
    const bucket = key in SEGMENT ? key : "unknown";
    segments.set(bucket, (segments.get(bucket) ?? 0) + 1);
  }

  return (
    <div data-testid="integrations-panel" className="@container/integrations flex min-w-0 flex-col">
      <PanelHeader
        flush
        title={t("integrations.title")}
        description={t("integrations.description")}
        actions={
          <Link href="/integrations" className="rounded text-caption font-medium text-brand hover:underline">
            {t("integrations.manage")}
          </Link>
        }
      />
      {resource.loading && !resource.data ? (
        <div className="px-6 pb-6">
          <LoadingSkeleton variant="table" rows={3} />
        </div>
      ) : resource.denied ? (
        <div className="px-4 py-2">
          <DeniedState compact />
        </div>
      ) : !resource.data ? (
        <div className="px-4 py-2">
          <ErrorState compact description={resource.error ?? undefined} onRetry={resource.reload} />
        </div>
      ) : all.length === 0 || configured.length === 0 ? (
        <div className="px-4 py-2">
          <NotConfiguredState compact title={t("integrations.emptyTitle")} description={t("integrations.emptyDescription")} />
        </div>
      ) : (
        <div className="flex flex-col gap-5 px-6 pb-6">
          <div>
            <div className="flex items-end justify-between gap-4">
              <p>
                <span data-tabular className="font-[family-name:var(--font-display)] text-[2.25rem] leading-none font-bold tracking-[-0.04em] text-ink">
                  {configured.length}
                </span>
                <span className="text-xl font-semibold tracking-tight text-ink-muted">/{all.length}</span>
                <span className="ml-2 text-caption text-ink-muted">{t("integrations.connected")}</span>
              </p>
              <span className="text-caption text-ink-muted">
                <span data-tabular className="font-semibold text-healthy">
                  {ok}
                </span>{" "}
                {t("integrations.reportingOk")}
              </span>
            </div>
            <span className="mt-3 flex h-2 w-full gap-[2px] overflow-hidden rounded-full" aria-label={t("integrations.byState")} role="img">
              {["critical", "warning", "stale", "unknown", "success", "off"]
                .filter((key) => segments.get(key))
                .map((key) => (
                  <span key={key} className={`h-full ${SEGMENT[key]}`} style={{ flexGrow: segments.get(key) }} />
                ))}
            </span>
          </div>
          <ul className="grid gap-3 @min-[26rem]/integrations:grid-cols-2 @min-[48rem]/integrations:grid-cols-3">
            {providers.map((provider) => (
              <ProviderTile key={provider.type} provider={provider} />
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
