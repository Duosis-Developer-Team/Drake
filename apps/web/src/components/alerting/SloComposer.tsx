"use client";

/**
 * Setting objectives from the screen.
 *
 * Two ways in. The suggestions are cards for every service that has none
 * yet, with one click for each indicator at a target a healthy service
 * already meets — so a fresh objective starts quiet instead of paging. The
 * composer walks four steps: where (application, environment, service, as
 * tiles with their logos), what (availability or latency, explained), the
 * target (a slider over the server's own short list, with the error budget
 * it leaves shown as time and as requests), and a review card. The client
 * never names a template or a query.
 */

import { Activity, Check, Plus, Sparkles, Timer } from "lucide-react";
import { useMemo, useState } from "react";

import { kindColour } from "@/components/apps/AppLauncher";
import { useProjectsByKey } from "@/components/alerting/SloBoard";
import { ProjectLogo } from "@/components/ui/ProjectLogo";
import { Button } from "@/components/ui/controls";
import { Drawer } from "@/components/ui/overlay";
import { compareEnvironments } from "@/components/explore/ServiceCascade";
import { ApiError } from "@/lib/api";
import {
  createSlo,
  deleteSlo,
  formatTarget,
  updateSlo,
  type SloDraft,
  type SloOptions,
  type SloService,
} from "@/lib/alerting";
import { useFormat, useT } from "@/lib/i18n";

type Indicator = "availability" | "latency";

function thresholdLabel(ms: number): string {
  return ms >= 1000 ? `${ms / 1000} s` : `${ms} ms`;
}

export function suggestedName(
  service: SloService,
  indicator: Indicator,
  thresholdMs?: number,
): string {
  return indicator === "latency"
    ? `${service.service_key} latency < ${thresholdLabel(thresholdMs ?? 1000)}`
    : `${service.service_key} availability`;
}

function failure(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

export function SloSuggestions({
  options,
  csrfToken,
  onCreated,
}: {
  options: SloOptions;
  csrfToken: string;
  onCreated: () => void;
}) {
  const t = useT("alerting");
  const projects = useProjectsByKey();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const bare = useMemo(
    () =>
      options.services
        .filter((s) => s.objectives === 0)
        .sort(
          (a, b) =>
            a.project_key.localeCompare(b.project_key) ||
            compareEnvironments(a.environment_key, b.environment_key) ||
            a.service_key.localeCompare(b.service_key),
        ),
    [options.services],
  );
  if (!options.can_manage || bare.length === 0) return null;

  const add = async (service: SloService, indicator: Indicator) => {
    const preset = options.suggested[indicator];
    const threshold =
      indicator === "latency" ? options.suggested.latency.threshold_ms : null;
    const draft: SloDraft = {
      environment_service_id: service.environment_service_id,
      display_name: suggestedName(service, indicator, threshold ?? undefined),
      indicator,
      objective_ratio: preset.objective_ratio,
      window_seconds: preset.window_seconds,
      threshold_ms: threshold,
    };
    setBusy(`${service.environment_service_id}:${indicator}`);
    setError(null);
    try {
      await createSlo(csrfToken, draft);
      onCreated();
    } catch (cause) {
      setError(failure(cause, t("sloCompose.failed")));
    } finally {
      setBusy(null);
    }
  };

  const latency = options.suggested.latency;
  const shown = bare.slice(0, 9);
  return (
    <section
      data-testid="slo-suggestions"
      className="rounded-2xl border border-border bg-surface-2/40 p-5 sm:p-6"
    >
      <header className="mb-4 flex flex-wrap items-start gap-3">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-brand-soft text-brand-accent">
          <Sparkles aria-hidden className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="text-[1.0625rem] font-semibold tracking-[-0.015em] text-ink">
            {t("sloCompose.suggest.title")}
          </h2>
          <p className="mt-0.5 max-w-3xl text-caption text-ink-muted">
            {t("sloCompose.suggest.description", {
              availability: formatTarget(options.suggested.availability.objective_ratio),
              latency: formatTarget(latency.objective_ratio),
              threshold: thresholdLabel(latency.threshold_ms),
            })}
          </p>
        </div>
      </header>
      {error ? (
        <p role="alert" className="mb-3 text-caption text-critical">
          {error}
        </p>
      ) : null}
      <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {shown.map((service) => {
          const project = projects.get(service.project_key);
          return (
            <li
              key={service.environment_service_id}
              className="relative flex flex-col overflow-hidden rounded-xl border border-border bg-surface p-4 pl-5"
            >
              <span
                aria-hidden
                className="absolute inset-y-0 left-0 w-[3px]"
                style={{ background: kindColour(service.environment_key) }}
              />
              <div className="flex items-center gap-3">
                <ProjectLogo
                  projectId={service.project_id}
                  name={project?.name ?? service.project_key}
                  size="md"
                  variant="mark"
                />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-body font-semibold text-ink">
                    {service.service_key}
                  </span>
                  <span className="block truncate font-mono text-micro text-ink-muted">
                    {service.project_key}/{service.environment_key}
                  </span>
                </span>
              </div>
              <div className="mt-3 grid gap-1.5">
                {(["availability", "latency"] as const).map((indicator) => {
                  const Icon = indicator === "latency" ? Timer : Activity;
                  return (
                    <button
                      key={indicator}
                      type="button"
                      disabled={busy !== null}
                      onClick={() => add(service, indicator)}
                      data-testid={`suggest-${indicator}-${service.service_key}-${service.environment_key}`}
                      className="group flex items-center gap-2 rounded-lg border border-border bg-surface-2/60 px-2.5 py-2 text-left text-caption font-medium text-ink transition-colors hover:border-brand-accent/60 hover:bg-brand-soft disabled:opacity-50"
                    >
                      <Icon aria-hidden className="h-3.5 w-3.5 shrink-0 text-brand-accent" />
                      <span className="min-w-0 truncate">
                        {indicator === "latency"
                          ? t("sloCompose.suggest.latency", {
                              ratio: formatTarget(latency.objective_ratio),
                              threshold: thresholdLabel(latency.threshold_ms),
                            })
                          : t("sloCompose.suggest.availability", {
                              ratio: formatTarget(options.suggested.availability.objective_ratio),
                            })}
                      </span>
                      <Plus
                        aria-hidden
                        className="ml-auto h-3.5 w-3.5 shrink-0 text-ink-muted group-hover:text-brand-accent"
                      />
                    </button>
                  );
                })}
              </div>
            </li>
          );
        })}
      </ul>
      {bare.length > shown.length ? (
        <p className="mt-3 text-micro text-ink-muted">
          {t("sloCompose.suggest.more", { count: bare.length - shown.length })}
        </p>
      ) : null}
    </section>
  );
}

/** A numbered step of the composer, with its question as the heading. */
function Step({
  n,
  title,
  hint,
  done,
  children,
}: {
  n: number;
  title: string;
  hint?: string;
  done: boolean;
  children: React.ReactNode;
}) {
  return (
    <section className="relative pl-11">
      <span
        aria-hidden
        className={`absolute top-0 left-0 grid h-7 w-7 place-items-center rounded-full font-mono text-[12px] font-semibold ${
          done ? "bg-brand-accent text-ink-inverse" : "border border-border-strong text-ink-secondary"
        }`}
      >
        {done ? <Check className="h-3.5 w-3.5" /> : n}
      </span>
      <h3 className="text-body font-semibold text-ink">{title}</h3>
      {hint ? <p className="mt-0.5 text-caption text-ink-muted">{hint}</p> : null}
      <div className="mt-3">{children}</div>
    </section>
  );
}

function chip(active: boolean) {
  return `inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-caption font-medium transition-colors ${
    active
      ? "border-brand-accent/70 bg-brand-soft text-ink"
      : "border-border bg-surface text-ink-secondary hover:border-border-strong hover:text-ink"
  }`;
}

function tile(active: boolean) {
  return `rounded-xl border text-left transition-colors ${
    active ? "border-brand-accent/70 bg-brand-soft" : "border-border bg-surface hover:border-border-strong"
  }`;
}

/** Seconds as people say an allowance: "3 h 36 min", "43 min". */
function allowance(seconds: number, tr: boolean): string {
  const s = Math.max(0, Math.round(seconds));
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  const unit = tr ? { d: "g", h: "sa", m: "dk", s: "sn" } : { d: "d", h: "h", m: "min", s: "s" };
  if (d) return `${d} ${unit.d} ${h} ${unit.h}`;
  if (h) return `${h} ${unit.h} ${m} ${unit.m}`;
  if (m) return `${m} ${unit.m}`;
  return `${s} ${unit.s}`;
}

export function SloComposer({
  open,
  onClose,
  options,
  csrfToken,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  options: SloOptions;
  csrfToken: string;
  onCreated: () => void;
}) {
  const t = useT("alerting");
  const fmt = useFormat();
  const projectNames = useProjectsByKey();
  const tr = fmt.tag.startsWith("tr");
  const [projectId, setProjectId] = useState("");
  const [environmentId, setEnvironmentId] = useState("");
  const [serviceId, setServiceId] = useState("");
  const [indicator, setIndicator] = useState<Indicator>("availability");
  const [objective, setObjective] = useState(
    String(options.suggested.availability.objective_ratio),
  );
  const [windowSeconds, setWindowSeconds] = useState(
    String(options.suggested.availability.window_seconds),
  );
  const [threshold, setThreshold] = useState(
    String(options.suggested.latency.threshold_ms),
  );
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const projects = useMemo(() => {
    const seen = new Map<string, string>();
    for (const s of options.services) seen.set(s.project_id, s.project_key);
    return [...seen]
      .map(([value, key]) => ({ value, key, label: projectNames.get(key)?.name ?? key }))
      .sort((a, b) => a.label.localeCompare(b.label));
  }, [options.services, projectNames]);
  const environments = useMemo(() => {
    const seen = new Map<string, string>();
    for (const s of options.services)
      if (s.project_id === projectId)
        seen.set(s.environment_id, s.environment_key);
    return [...seen]
      .map(([value, label]) => ({ value, label }))
      .sort((a, b) => compareEnvironments(a.label, b.label));
  }, [options.services, projectId]);
  const services = useMemo(
    () =>
      options.services
        .filter(
          (s) =>
            s.project_id === projectId && s.environment_id === environmentId,
        )
        .sort((a, b) => a.service_key.localeCompare(b.service_key)),
    [options.services, projectId, environmentId],
  );
  const service =
    options.services.find((s) => s.environment_service_id === serviceId) ??
    null;
  const thresholdMs = Number(threshold);
  const ratio = Number(objective);
  const windowS = Number(windowSeconds);
  const effectiveName =
    name.trim() ||
    (service ? suggestedName(service, indicator, thresholdMs) : "");
  const targetIndex = Math.max(
    0,
    options.objectives.findIndex((r) => r === ratio),
  );

  const pickApp = (id: string) => {
    setProjectId(id);
    const envs = new Set(
      options.services
        .filter((s) => s.project_id === id)
        .map((s) => s.environment_id),
    );
    setEnvironmentId(envs.size === 1 ? [...envs][0] : "");
    setServiceId("");
  };
  const pickIndicator = (next: Indicator) => {
    setIndicator(next);
    setObjective(String(options.suggested[next].objective_ratio));
  };

  const submit = async () => {
    if (!service) return;
    setSaving(true);
    setError(null);
    try {
      await createSlo(csrfToken, {
        environment_service_id: service.environment_service_id,
        display_name: effectiveName,
        indicator,
        objective_ratio: ratio,
        window_seconds: windowS,
        threshold_ms: indicator === "latency" ? thresholdMs : null,
      });
      onCreated();
      onClose();
    } catch (cause) {
      setError(failure(cause, t("sloCompose.failed")));
    } finally {
      setSaving(false);
    }
  };

  const budgetSeconds = (1 - ratio) * windowS;
  const per10k = Math.round((1 - ratio) * 10000);
  const projectName = service
    ? (projectNames.get(service.project_key)?.name ?? service.project_key)
    : "";

  return (
    <Drawer
      open={open}
      onClose={onClose}
      width="lg"
      title={t("sloCompose.title")}
      description={t("sloCompose.description")}
      footer={
        <div className="flex items-center justify-end gap-2">
          <Button onClick={onClose}>{t("sloCompose.cancel")}</Button>
          <Button
            variant="primary"
            disabled={!service || saving}
            onClick={submit}
            data-testid="slo-compose-save"
          >
            {t("sloCompose.save")}
          </Button>
        </div>
      }
    >
      <div className="space-y-8 py-1" data-testid="slo-composer">
        <Step
          n={1}
          title={t("sloCompose.step.where")}
          hint={t("sloCompose.step.whereHint")}
          done={Boolean(service)}
        >
          <div
            className="grid grid-cols-2 gap-2 sm:grid-cols-3"
            role="group"
            aria-label={t("sloCompose.application")}
          >
            {projects.map((p) => (
              <button
                key={p.value}
                type="button"
                aria-pressed={projectId === p.value}
                onClick={() => pickApp(p.value)}
                data-testid={`compose-app-${p.key}`}
                className={`flex items-center gap-2.5 p-2.5 ${tile(projectId === p.value)}`}
              >
                <ProjectLogo projectId={p.value} name={p.label} size="md" variant="mark" />
                <span className="min-w-0 truncate text-body font-semibold text-ink">
                  {p.label}
                </span>
              </button>
            ))}
          </div>
          {projectId ? (
            <div
              className="mt-3 flex flex-wrap gap-2"
              role="group"
              aria-label={t("sloCompose.environment")}
            >
              {environments.map((e) => (
                <button
                  key={e.value}
                  type="button"
                  aria-pressed={environmentId === e.value}
                  onClick={() => {
                    setEnvironmentId(e.value);
                    setServiceId("");
                  }}
                  data-testid={`compose-env-${e.label}`}
                  className={chip(environmentId === e.value)}
                >
                  <span
                    aria-hidden
                    className="h-2 w-2 rounded-full"
                    style={{ background: kindColour(e.label) }}
                  />
                  <span className="font-mono">{e.label}</span>
                </button>
              ))}
            </div>
          ) : (
            <p className="mt-3 text-micro text-ink-muted">
              {t("sloCompose.pickApplication")}
            </p>
          )}
          {environmentId ? (
            <div
              className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3"
              role="group"
              aria-label={t("sloCompose.service")}
              data-testid="slo-compose-service"
            >
              {services.map((s) => (
                <button
                  key={s.environment_service_id}
                  type="button"
                  aria-pressed={serviceId === s.environment_service_id}
                  onClick={() => setServiceId(s.environment_service_id)}
                  data-testid={`compose-service-${s.service_key}`}
                  className={`px-3 py-2.5 ${tile(serviceId === s.environment_service_id)}`}
                >
                  <span className="block truncate text-caption font-semibold text-ink">
                    {s.service_key}
                  </span>
                  <span className="block text-[11px] text-ink-muted">
                    {s.objectives
                      ? t("sloCompose.hasObjectives", { count: s.objectives })
                      : t("sloCompose.noObjectives")}
                  </span>
                </button>
              ))}
            </div>
          ) : null}
        </Step>

        <Step n={2} title={t("sloCompose.step.what")} done={Boolean(service)}>
          <div
            className="grid gap-3 sm:grid-cols-2"
            role="radiogroup"
            aria-label={t("sloCompose.indicator")}
          >
            {(["availability", "latency"] as const).map((value) => {
              const Icon = value === "latency" ? Timer : Activity;
              const active = indicator === value;
              return (
                <button
                  key={value}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  onClick={() => pickIndicator(value)}
                  data-testid={`compose-indicator-${value}`}
                  className={`p-4 ${tile(active)}`}
                >
                  <span className="flex items-center gap-2">
                    <span
                      className={`grid h-8 w-8 place-items-center rounded-lg ${
                        active ? "bg-brand-accent text-ink-inverse" : "bg-surface-2 text-ink-secondary"
                      }`}
                    >
                      <Icon aria-hidden className="h-4 w-4" />
                    </span>
                    <span className="text-body font-semibold text-ink first-letter:uppercase">
                      {t.dyn("indicator", value, value)}
                    </span>
                  </span>
                  <span className="mt-2 block text-caption text-ink-secondary">
                    {t(`sloCompose.explain.${value}`)}
                  </span>
                  <span className="mt-2 block font-mono text-[11px] text-ink-muted">
                    {t(`sloCompose.formula.${value}`)}
                  </span>
                </button>
              );
            })}
          </div>
          {indicator === "latency" ? (
            <div
              className="mt-3 flex flex-wrap items-center gap-2"
              role="group"
              aria-label={t("sloCompose.threshold")}
            >
              <span className="mr-1 text-caption text-ink-secondary">
                {t("sloCompose.threshold")}
              </span>
              {options.thresholds_ms.map((ms) => (
                <button
                  key={ms}
                  type="button"
                  aria-pressed={thresholdMs === ms}
                  onClick={() => setThreshold(String(ms))}
                  data-testid={`compose-threshold-${ms}`}
                  className={chip(thresholdMs === ms)}
                >
                  <span className="font-mono">{thresholdLabel(ms)}</span>
                </button>
              ))}
            </div>
          ) : null}
        </Step>

        <Step n={3} title={t("sloCompose.step.target")} done={Boolean(service)}>
          <div className="rounded-xl border border-border bg-surface p-4">
            <div className="flex items-baseline justify-between gap-3">
              <span className="text-caption text-ink-secondary">
                {t("sloCompose.objective")}
              </span>
              <span
                data-tabular
                className="font-[family-name:var(--font-display)] text-[1.75rem] leading-none font-bold text-ink"
              >
                {formatTarget(ratio)}
              </span>
            </div>
            <input
              type="range"
              min={0}
              max={Math.max(0, options.objectives.length - 1)}
              step={1}
              value={targetIndex}
              onChange={(event) =>
                setObjective(String(options.objectives[Number(event.target.value)]))
              }
              aria-label={t("sloCompose.objective")}
              aria-valuetext={formatTarget(ratio)}
              data-testid="compose-target"
              className="mt-3 w-full accent-[var(--brand-accent)]"
            />
            <div className="mt-1 flex justify-between font-mono text-[10.5px] text-ink-muted">
              {options.objectives.map((r) => (
                <span key={r}>{formatTarget(r)}</span>
              ))}
            </div>
            <div
              className="mt-4 flex flex-wrap items-center gap-2"
              role="group"
              aria-label={t("sloCompose.window")}
            >
              <span className="mr-1 text-caption text-ink-secondary">
                {t("sloCompose.window")}
              </span>
              {options.windows_seconds.map((w) => (
                <button
                  key={w}
                  type="button"
                  aria-pressed={windowS === w}
                  onClick={() => setWindowSeconds(String(w))}
                  className={chip(windowS === w)}
                >
                  {fmt.duration(w, { compact: true })}
                </button>
              ))}
            </div>
            <div className="mt-4 grid grid-cols-2 gap-3" data-testid="compose-budget-preview">
              <div className="rounded-lg bg-surface-2 p-3">
                <p className="text-[11px] text-ink-muted">
                  {indicator === "latency"
                    ? t("sloCompose.preview.allowanceLatency")
                    : t("sloCompose.preview.allowance")}
                </p>
                <p data-tabular className="mt-1 font-mono text-body font-semibold text-ink">
                  {allowance(budgetSeconds, tr)}
                </p>
                <p className="mt-0.5 text-[11px] text-ink-muted">
                  {indicator === "latency"
                    ? t("sloCompose.preview.allowanceLatencyHint", {
                        window: fmt.duration(windowS, { compact: true }),
                      })
                    : t("sloCompose.preview.allowanceHint", {
                        window: fmt.duration(windowS, { compact: true }),
                      })}
                </p>
              </div>
              <div className="rounded-lg bg-surface-2 p-3">
                <p className="text-[11px] text-ink-muted">{t("sloCompose.preview.per10k")}</p>
                <p data-tabular className="mt-1 font-mono text-body font-semibold text-ink">
                  {fmt.number(per10k)}
                </p>
                <p className="mt-0.5 text-[11px] text-ink-muted">
                  {indicator === "latency"
                    ? t("sloCompose.preview.per10kLatency", {
                        threshold: thresholdLabel(thresholdMs),
                      })
                    : t("sloCompose.preview.per10kAvailability")}
                </p>
              </div>
            </div>
          </div>
        </Step>

        <Step n={4} title={t("sloCompose.step.review")} done={false}>
          <label className="block space-y-1.5">
            <span className="text-caption text-ink-secondary">{t("sloCompose.name")}</span>
            <input
              value={name}
              maxLength={120}
              placeholder={effectiveName || t("sloCompose.namePlaceholder")}
              onChange={(event) => setName(event.target.value)}
              className="h-10 w-full rounded-lg border border-border bg-surface px-3 text-body text-ink"
            />
          </label>
          {service ? (
            <div
              className="mt-3 rounded-xl border border-brand-accent/40 bg-brand-soft/60 p-4"
              data-testid="slo-compose-summary"
            >
              <p className="flex flex-wrap items-center gap-2 text-caption font-semibold text-ink">
                <ProjectLogo
                  projectId={service.project_id}
                  name={projectName}
                  size="sm"
                  variant="mark"
                />
                {effectiveName}
                <span className="font-mono text-[11px] font-normal text-ink-muted">
                  {service.project_key}/{service.environment_key}
                </span>
              </p>
              <p className="mt-2 text-caption text-ink-secondary">
                {indicator === "latency"
                  ? t("sloCompose.summary.latency", {
                      ratio: formatTarget(ratio),
                      threshold: thresholdLabel(thresholdMs),
                      window: fmt.duration(windowS, { compact: true }),
                      budget: formatTarget(1 - ratio),
                    })
                  : t("sloCompose.summary.availability", {
                      ratio: formatTarget(ratio),
                      window: fmt.duration(windowS, { compact: true }),
                      budget: formatTarget(1 - ratio),
                    })}
              </p>
            </div>
          ) : (
            <p className="mt-3 text-caption text-ink-muted">{t("sloCompose.pickService")}</p>
          )}
          {!options.measuring ? (
            <p className="mt-3 text-caption text-warning">{t("sloCompose.notMeasuring")}</p>
          ) : null}
          {error ? (
            <p role="alert" className="mt-3 text-caption text-critical">
              {error}
            </p>
          ) : null}
        </Step>
      </div>
    </Drawer>
  );
}

export function NewSloButton({ onClick }: { onClick: () => void }) {
  const t = useT("alerting");
  return (
    <Button
      variant="primary"
      icon={Sparkles}
      onClick={onClick}
      data-testid="slo-new"
    >
      {t("sloCompose.open")}
    </Button>
  );
}

/** Pause, resume or delete an objective set in the screen. One declared
 * in the project manifest says where it is changed instead. */
export function SloManageActions({
  slo,
  csrfToken,
  onChanged,
  onDeleted,
}: {
  slo: {
    id: string;
    version: number;
    enabled: boolean;
    source?: "traces" | "manifest";
  };
  csrfToken: string | null;
  onChanged: () => void;
  onDeleted: () => void;
}) {
  const t = useT("alerting");
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (slo.source !== "traces") {
    return slo.source === "manifest" ? (
      <p className="text-caption text-ink-muted">
        {t("sloCompose.manage.manifest")}
      </p>
    ) : null;
  }
  if (!csrfToken) return null;
  const run = async (action: () => Promise<unknown>, then: () => void) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      then();
    } catch (cause) {
      setError(failure(cause, t("sloCompose.manage.failed")));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="slo-manage">
      <Button
        size="compact"
        disabled={busy}
        onClick={() =>
          run(
            () =>
              updateSlo(csrfToken, slo.id, slo.version, {
                enabled: !slo.enabled,
              }),
            onChanged,
          )
        }
      >
        {slo.enabled
          ? t("sloCompose.manage.disable")
          : t("sloCompose.manage.enable")}
      </Button>
      {confirming ? (
        <span className="inline-flex items-center gap-2 text-caption text-ink-secondary">
          {t("sloCompose.manage.confirmDelete")}
          <Button
            size="compact"
            variant="danger"
            disabled={busy}
            onClick={() => run(() => deleteSlo(csrfToken, slo.id), onDeleted)}
            data-testid="slo-delete-confirm"
          >
            {t("sloCompose.manage.delete")}
          </Button>
          <Button
            size="compact"
            variant="ghost"
            onClick={() => setConfirming(false)}
          >
            {t("sloCompose.cancel")}
          </Button>
        </span>
      ) : (
        <Button
          size="compact"
          variant="ghost"
          disabled={busy}
          onClick={() => setConfirming(true)}
        >
          {t("sloCompose.manage.delete")}
        </Button>
      )}
      {error ? (
        <span role="alert" className="text-caption text-critical">
          {error}
        </span>
      ) : null}
    </div>
  );
}
