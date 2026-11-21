"use client";

/**
 * Setting objectives from the screen.
 *
 * Two ways in. The suggestions list every service that has none yet, with
 * one click for each indicator at a target a healthy service already meets
 * — so a fresh objective starts quiet instead of paging. The composer is the
 * full form: application, then environment, then service (the same order as
 * everywhere else), the indicator, and target, window and threshold from the
 * server's own short lists. The client never names a template or a query.
 */

import { Plus, Sparkles } from "lucide-react";
import { useMemo, useState } from "react";

import { Panel, PanelHeader } from "@/components/ui/Panel";
import { ProjectLogo } from "@/components/ui/ProjectLogo";
import { Button, SegmentedControl, Select } from "@/components/ui/controls";
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
  return (
    <Panel flush data-testid="slo-suggestions">
      <PanelHeader
        flush
        title={t("sloCompose.suggest.title")}
        description={t("sloCompose.suggest.description", {
          availability: formatTarget(
            options.suggested.availability.objective_ratio,
          ),
          latency: formatTarget(latency.objective_ratio),
          threshold: thresholdLabel(latency.threshold_ms),
        })}
      />
      {error ? (
        <p role="alert" className="px-7 pb-3 text-caption text-critical">
          {error}
        </p>
      ) : null}
      <ul className="divide-y divide-border">
        {bare.slice(0, 12).map((service) => (
          <li
            key={service.environment_service_id}
            className="flex flex-wrap items-center gap-x-4 gap-y-2 px-7 py-3"
          >
            <ProjectLogo
              projectId={service.project_id}
              name={service.project_key}
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
            <span className="flex flex-wrap gap-2">
              {(["availability", "latency"] as const).map((indicator) => (
                <Button
                  key={indicator}
                  size="compact"
                  icon={Plus}
                  disabled={busy !== null}
                  onClick={() => add(service, indicator)}
                  data-testid={`suggest-${indicator}-${service.service_key}-${service.environment_key}`}
                >
                  {indicator === "latency"
                    ? t("sloCompose.suggest.latency", {
                        ratio: formatTarget(latency.objective_ratio),
                        threshold: thresholdLabel(latency.threshold_ms),
                      })
                    : t("sloCompose.suggest.availability", {
                        ratio: formatTarget(
                          options.suggested.availability.objective_ratio,
                        ),
                      })}
                </Button>
              ))}
            </span>
          </li>
        ))}
      </ul>
      {bare.length > 12 ? (
        <p className="border-t border-border px-7 py-3 text-micro text-ink-muted">
          {t("sloCompose.suggest.more", { count: bare.length - 12 })}
        </p>
      ) : null}
    </Panel>
  );
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
      .map(([value, label]) => ({ value, label }))
      .sort((a, b) => a.label.localeCompare(b.label));
  }, [options.services]);
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
        .map((s) => ({ value: s.environment_service_id, label: s.service_key }))
        .sort((a, b) => a.label.localeCompare(b.label)),
    [options.services, projectId, environmentId],
  );
  const service =
    options.services.find((s) => s.environment_service_id === serviceId) ??
    null;
  const thresholdMs = Number(threshold);
  const effectiveName =
    name.trim() ||
    (service ? suggestedName(service, indicator, thresholdMs) : "");

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
        objective_ratio: Number(objective),
        window_seconds: Number(windowSeconds),
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

  return (
    <Drawer
      open={open}
      onClose={onClose}
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
      <div className="space-y-5" data-testid="slo-composer">
        <fieldset className="space-y-2.5">
          <legend className="mb-2 text-micro font-semibold tracking-[0.08em] text-ink-muted uppercase">
            {t("sloCompose.target")}
          </legend>
          <Select
            label={t("sloCompose.application")}
            value={projectId}
            placeholder={t("sloCompose.choose")}
            options={projects}
            onChange={(v) => {
              setProjectId(v);
              const envs = new Set(
                options.services
                  .filter((s) => s.project_id === v)
                  .map((s) => s.environment_id),
              );
              setEnvironmentId(envs.size === 1 ? [...envs][0] : "");
              setServiceId("");
            }}
          />
          <Select
            label={t("sloCompose.environment")}
            value={environmentId}
            placeholder={
              projectId
                ? t("sloCompose.choose")
                : t("sloCompose.pickApplication")
            }
            options={environments}
            onChange={(v) => {
              setEnvironmentId(v);
              setServiceId("");
            }}
          />
          <Select
            label={t("sloCompose.service")}
            value={serviceId}
            placeholder={
              environmentId
                ? t("sloCompose.choose")
                : t("sloCompose.pickEnvironment")
            }
            options={services}
            onChange={setServiceId}
            data-testid="slo-compose-service"
          />
        </fieldset>

        <div className="space-y-2">
          <SegmentedControl
            label={t("sloCompose.indicator")}
            value={indicator}
            onChange={pickIndicator}
            options={[
              {
                value: "availability",
                label: t.dyn("indicator", "availability", "availability"),
              },
              {
                value: "latency",
                label: t.dyn("indicator", "latency", "latency"),
              },
            ]}
          />
          <p className="text-caption text-ink-muted">
            {indicator === "latency"
              ? t("sloCompose.explain.latency")
              : t("sloCompose.explain.availability")}
          </p>
        </div>

        <div className="grid gap-2.5">
          <Select
            label={t("sloCompose.objective")}
            value={objective}
            options={options.objectives.map((r) => ({
              value: String(r),
              label: formatTarget(r),
            }))}
            onChange={(v) => v && setObjective(v)}
          />
          {indicator === "latency" ? (
            <Select
              label={t("sloCompose.threshold")}
              value={threshold}
              options={options.thresholds_ms.map((ms) => ({
                value: String(ms),
                label: thresholdLabel(ms),
              }))}
              onChange={(v) => v && setThreshold(v)}
            />
          ) : null}
          <Select
            label={t("sloCompose.window")}
            value={windowSeconds}
            options={options.windows_seconds.map((s) => ({
              value: String(s),
              label: fmt.duration(s, { compact: true }),
            }))}
            onChange={(v) => v && setWindowSeconds(v)}
          />
        </div>

        <label className="block space-y-1.5">
          <span className="text-caption text-ink-secondary">
            {t("sloCompose.name")}
          </span>
          <input
            value={name}
            maxLength={120}
            placeholder={effectiveName || t("sloCompose.namePlaceholder")}
            onChange={(event) => setName(event.target.value)}
            className="h-9 w-full rounded-control border border-border bg-surface px-2.5 text-body text-ink"
          />
        </label>

        {service ? (
          <p
            className="rounded-control bg-surface-2 px-3 py-2.5 text-caption text-ink-secondary"
            data-testid="slo-compose-summary"
          >
            {indicator === "latency"
              ? t("sloCompose.summary.latency", {
                  ratio: formatTarget(Number(objective)),
                  threshold: thresholdLabel(thresholdMs),
                  window: fmt.duration(Number(windowSeconds), {
                    compact: true,
                  }),
                  budget: formatTarget(1 - Number(objective)),
                })
              : t("sloCompose.summary.availability", {
                  ratio: formatTarget(Number(objective)),
                  window: fmt.duration(Number(windowSeconds), {
                    compact: true,
                  }),
                  budget: formatTarget(1 - Number(objective)),
                })}
          </p>
        ) : null}
        {!options.measuring ? (
          <p className="text-caption text-warning">
            {t("sloCompose.notMeasuring")}
          </p>
        ) : null}
        {error ? (
          <p role="alert" className="text-caption text-critical">
            {error}
          </p>
        ) : null}
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
