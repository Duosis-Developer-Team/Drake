"use client";

import {
  ArrowUpRight,
  Fingerprint,
  Globe,
  Radar,
  ServerCog,
  ShieldAlert,
  Tag,
} from "lucide-react";
import Link from "next/link";

import { LifecycleBadge } from "@/components/agents/Governance";
import { ProjectLogo } from "@/components/ui/ProjectLogo";
import { AgentGlyph, MiniArea } from "@/components/agents/visuals";
import { useFormat, useLocale, useT } from "@/lib/i18n";
import {
  callsOf,
  compact,
  errorRatio,
  providerColor,
  providerName,
  type Agent,
  type Confidence,
  usd,
} from "@/lib/agents";

export const CONFIDENCE_ICON: Record<Confidence, typeof Tag> = {
  stable: Fingerprint,
  named: Tag,
  service: ServerCog,
  observed: Radar,
};

export function ConfidenceBadge({ confidence }: { confidence: Confidence }) {
  const t = useT("agents");
  const Icon = CONFIDENCE_ICON[confidence];
  return (
    <span
      title={t.dyn("confidence", `${confidence}Hint`, confidence)}
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-micro font-medium ${
        confidence === "observed"
          ? "bg-warning-soft text-warning"
          : confidence === "service"
            ? "bg-surface-3 text-ink-secondary"
            : "bg-brand-soft text-brand"
      }`}
    >
      <Icon aria-hidden className="h-3 w-3" />
      {t.dyn("confidence", confidence, confidence)}
    </span>
  );
}

export function AgentCard({ agent }: { agent: Agent }) {
  const t = useT("agents");
  const fmt = useFormat();
  const { locale } = useLocale();
  const calls = callsOf(agent);
  const ratio = errorRatio(agent);
  const tokens = agent.totals.input_tokens + agent.totals.output_tokens;
  const main = agent.providers[0]?.provider ?? null;
  const where = agent.project
    ? `${agent.project.key}${agent.environment ? `/${agent.environment.key}` : ""}`
    : agent.namespace || agent.service_name || t("card.outside");

  return (
    <Link
      href={`/agents/${agent.id}`}
      data-testid={`agent-card-${agent.id}`}
      className="group relative flex min-w-0 flex-col gap-4 rounded-panel border border-border bg-surface p-5 transition-all hover:-translate-y-0.5 hover:border-border-strong hover:shadow-md"
    >
      <div className="flex items-start gap-3.5">
        <AgentGlyph
          name={agent.display_name}
          providers={agent.providers}
          observed={agent.confidence === "observed"}
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <p className="truncate text-body font-semibold text-ink">
              {agent.display_name}
            </p>
            <ArrowUpRight
              aria-hidden
              className="h-3.5 w-3.5 shrink-0 text-ink-muted opacity-0 transition-opacity group-hover:opacity-100"
            />
          </div>
          <p className="mt-0.5 flex min-w-0 items-center gap-1.5 text-micro text-ink-muted">
            {agent.project ? (
              <ProjectLogo
                projectId={agent.project.id}
                name={agent.project.key}
                size="sm"
                variant="mark"
              />
            ) : null}
            <span className="truncate font-mono">{where}</span>
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            <ConfidenceBadge confidence={agent.confidence} />
            <LifecycleBadge agent={agent} compactMode />
            {agent.ingest === "external" ? (
              <span className="inline-flex items-center gap-1 rounded-full bg-surface-3 px-2 py-0.5 text-micro font-medium text-ink-secondary">
                <Globe aria-hidden className="h-3 w-3" />
                {t("card.external")}
              </span>
            ) : null}
            {agent.guardrails_firing ? (
              <span className="inline-flex items-center gap-1 rounded-full bg-critical-soft px-2 py-0.5 text-micro font-semibold text-critical">
                <ShieldAlert aria-hidden className="h-3 w-3" />
                {t("guard.firing", { count: agent.guardrails_firing })}
              </span>
            ) : null}
            {agent.new ? (
              <span className="rounded-full bg-healthy-soft px-2 py-0.5 text-micro font-semibold text-healthy">
                {t("card.new")}
              </span>
            ) : null}
          </div>
        </div>
      </div>

      <MiniArea
        points={agent.series.map((p) => ({
          calls: p.calls ?? 0,
          errors: p.errors,
        }))}
        color={main ? providerColor(main) : "var(--series-1)"}
        label={t("card.activity", { name: agent.display_name })}
      />

      <dl className="grid grid-cols-4 gap-2 border-t border-border pt-3">
        {[
          { label: t("card.calls"), value: compact(calls, locale) },
          {
            label: t("card.tools"),
            value: compact(agent.totals.tool_calls, locale),
          },
          {
            label: t("card.failed"),
            value:
              ratio === null
                ? "—"
                : `${fmt.number(ratio * 100, { maximumFractionDigits: 1 })}%`,
            bad: (ratio ?? 0) > 0.05,
          },
          agent.cost_usd > 0
            ? { label: t("card.cost"), value: usd(agent.cost_usd, locale) }
            : { label: t("card.tokens"), value: compact(tokens, locale) },
        ].map((stat) => (
          <div key={stat.label} className="min-w-0">
            <dt className="truncate text-micro text-ink-muted">{stat.label}</dt>
            <dd
              className={`text-body font-semibold tabular-nums ${stat.bad ? "text-critical" : "text-ink"}`}
            >
              {stat.value}
            </dd>
          </div>
        ))}
      </dl>

      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 flex-wrap gap-1.5">
          {agent.models.length === 0 ? (
            <span className="text-micro text-ink-muted">
              {t("card.noModel")}
            </span>
          ) : (
            agent.models.slice(0, 2).map((m) => (
              <span
                key={`${m.provider}-${m.model}`}
                title={providerName(m.provider)}
                className="inline-flex max-w-40 items-center gap-1.5 rounded-full border border-border px-2 py-0.5 text-micro text-ink-secondary"
              >
                <span
                  className="h-1.5 w-1.5 shrink-0 rounded-full"
                  style={{ background: providerColor(m.provider) }}
                />
                <span className="truncate font-mono">{m.model}</span>
              </span>
            ))
          )}
          {agent.models.length > 2 ? (
            <span className="rounded-full bg-surface-3 px-2 py-0.5 text-micro text-ink-muted">
              {t("card.more", { count: agent.models.length - 2 })}
            </span>
          ) : null}
        </div>
        <time
          dateTime={agent.last_seen_at}
          title={fmt.utc(agent.last_seen_at)}
          className="shrink-0 text-micro text-ink-muted"
        >
          {t("card.lastSeen", { when: fmt.relative(agent.last_seen_at) })}
        </time>
      </div>
    </Link>
  );
}
