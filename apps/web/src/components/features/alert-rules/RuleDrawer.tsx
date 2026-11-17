"use client";

/**
 * Writing a rule. The left side is the rule; the right side is the server
 * replaying it over the last day of stored samples, so the person sees
 * "this would have fired 3 times" before saving — the same `step` the
 * runner will use, not a browser-side estimate.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import {
  RATIO_SIGNALS,
  SIGNALS,
  createRule,
  formatSignal,
  previewRule,
  updateRule,
  type AlertRule,
  type Operator,
  type RuleDraft,
  type RuleOptions,
  type RulePreview,
  type Severity,
  type Signal,
} from "@/lib/alertRules";
import { ApiError } from "@/lib/api";
import { useFormat, useT } from "@/lib/i18n";

import s from "./alertRules.module.css";
import { SignalChart, hhmm, severityTone } from "./instruments";

type TemplateKey = "errors" | "latency" | "cpu" | "memory" | "restarts" | "traffic";

const TEMPLATES: Record<
  TemplateKey,
  { signal: Signal; operator: Operator; threshold: number; for_seconds: number; severity: Severity }
> = {
  errors: { signal: "error_ratio", operator: ">", threshold: 0.05, for_seconds: 300, severity: "critical" },
  latency: { signal: "latency_p95_seconds", operator: ">", threshold: 0.8, for_seconds: 300, severity: "warning" },
  cpu: { signal: "cpu_utilization", operator: ">", threshold: 0.9, for_seconds: 900, severity: "warning" },
  memory: { signal: "memory_utilization", operator: ">", threshold: 0.85, for_seconds: 600, severity: "warning" },
  restarts: { signal: "restarts", operator: ">", threshold: 3, for_seconds: 0, severity: "critical" },
  traffic: { signal: "request_rate", operator: "<", threshold: 0.2, for_seconds: 600, severity: "warning" },
};
const TEMPLATE_KEYS = Object.keys(TEMPLATES) as TemplateKey[];

/** Slider range in the signal's stored unit. */
const RANGES: Record<Signal, { min: number; max: number; step: number }> = {
  error_ratio: { min: 0.005, max: 0.5, step: 0.005 },
  latency_p95_seconds: { min: 0.05, max: 5, step: 0.05 },
  request_rate: { min: 0, max: 100, step: 0.1 },
  restarts: { min: 0, max: 20, step: 1 },
  cpu_utilization: { min: 0.05, max: 1, step: 0.01 },
  memory_utilization: { min: 0.05, max: 1, step: 0.01 },
  unavailable_replicas: { min: 0, max: 10, step: 1 },
};

const DURATIONS = [0, 60, 120, 300, 600, 900, 1800, 3600];

export function useDuration() {
  const common = useT("common");
  return (seconds: number): string => {
    const minutes = Math.round(seconds / 60);
    if (minutes < 60) return common("time.minutesShort", { count: minutes });
    const hours = Math.floor(minutes / 60);
    const rest = minutes % 60;
    return rest
      ? `${common("time.hoursShort", { count: hours })} ${common("time.minutesShort", { count: rest })}`
      : common("time.hoursShort", { count: hours });
  };
}

/** Shown value ↔ stored value: ratios are edited as percentages. */
function toShown(signal: Signal, value: number): number {
  return RATIO_SIGNALS.has(signal) ? Math.round(value * 10000) / 100 : value;
}
function fromShown(signal: Signal, value: number): number {
  return RATIO_SIGNALS.has(signal) ? value / 100 : value;
}

export function RuleDrawer({
  open,
  rule,
  options,
  csrfToken,
  defaultProjectId,
  initialTemplate,
  onClose,
  onSaved,
}: {
  open: boolean;
  rule: AlertRule | null;
  options: RuleOptions | null;
  csrfToken: string | null;
  defaultProjectId: string | null;
  initialTemplate: TemplateKey | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const t = useT("alertRules");
  const fmt = useFormat();
  const duration = useDuration();
  const editing = rule !== null;

  const [template, setTemplate] = useState<TemplateKey | null>("errors");
  const [name, setName] = useState("");
  const [nameTouched, setNameTouched] = useState(false);
  const [projectId, setProjectId] = useState<string>("");
  const [environmentId, setEnvironmentId] = useState<string>("");
  const [serviceId, setServiceId] = useState<string>("");
  const [signal, setSignal] = useState<Signal>("error_ratio");
  const [operator, setOperator] = useState<Operator>(">");
  const [threshold, setThreshold] = useState(0.05);
  const [forSeconds, setForSeconds] = useState(300);
  const [severity, setSeverity] = useState<Severity>("critical");
  const [preview, setPreview] = useState<RulePreview | null>(null);
  const [previewState, setPreviewState] = useState<"idle" | "loading" | "error">("idle");
  const [saving, setSaving] = useState<"idle" | "saving" | "saved">("idle");
  const [error, setError] = useState<string | null>(null);
  const firstField = useRef<HTMLInputElement>(null);

  // Reset the form whenever the drawer opens.
  useEffect(() => {
    if (!open) return;
    setError(null);
    setSaving("idle");
    setPreview(null);
    if (rule) {
      setTemplate(null);
      setName(rule.name);
      setNameTouched(true);
      setProjectId(rule.project_id);
      setEnvironmentId(rule.environment_id ?? "");
      setServiceId(rule.service_id ?? "");
      setSignal(rule.signal);
      setOperator(rule.operator);
      setThreshold(rule.threshold);
      setForSeconds(rule.for_seconds);
      setSeverity(rule.severity);
    } else {
      const key = initialTemplate ?? "errors";
      const preset = TEMPLATES[key];
      setTemplate(key);
      setName("");
      setNameTouched(false);
      setProjectId(defaultProjectId ?? options?.projects[0]?.id ?? "");
      setEnvironmentId("");
      setServiceId("");
      setSignal(preset.signal);
      setOperator(preset.operator);
      setThreshold(preset.threshold);
      setForSeconds(preset.for_seconds);
      setSeverity(preset.severity);
    }
    const focus = window.setTimeout(() => firstField.current?.focus(), 0);
    return () => window.clearTimeout(focus);
  }, [open, rule, initialTemplate, defaultProjectId, options]);

  const project = options?.projects.find((p) => p.id === projectId);
  const environment = project?.environments.find((e) => e.id === environmentId);
  const service = environment?.services.find((sv) => sv.id === serviceId);

  const targetText = rule
    ? [rule.target.project, rule.target.environment ?? t("scope.allEnvironments"), rule.target.service ?? t("scope.allServices")].join(" / ")
    : [project?.key ?? "—", environment?.key ?? t("scope.allEnvironments"), service?.key ?? t("scope.allServices")].join(" / ");

  const draft: RuleDraft | null = projectId
    ? {
        project_id: projectId,
        environment_id: environmentId || null,
        service_id: serviceId || null,
        signal,
        operator,
        threshold,
        for_seconds: forSeconds,
        severity,
      }
    : null;
  const draftKey = draft ? JSON.stringify(draft) : "";

  // Debounced server replay.
  useEffect(() => {
    if (!open || !draft || !csrfToken) return;
    const controller = new AbortController();
    setPreviewState("loading");
    const timer = window.setTimeout(() => {
      previewRule(csrfToken, draft, controller.signal)
        .then((result) => {
          setPreview(result);
          setPreviewState("idle");
        })
        .catch((reason: unknown) => {
          if (controller.signal.aborted) return;
          void reason;
          setPreviewState("error");
        });
    }, 350);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
    // draftKey captures every field of the draft.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, draftKey, csrfToken]);

  const show = (value: number) => formatSignal(signal, value, fmt.number);
  const signalName = t.dyn("signal", signal);
  const sentenceKey =
    forSeconds > 0
      ? operator === "<"
        ? "sentence.belowFor"
        : "sentence.aboveFor"
      : operator === "<"
        ? "sentence.below"
        : "sentence.above";
  const sentence = t(sentenceKey, {
    signal: signalName,
    threshold: show(threshold),
    duration: duration(forSeconds),
  });
  const action = t(severity === "critical" ? "sentence.critical" : "sentence.warning");

  const defaultName = template ? t(`templates.${template}.name`) : signalName;
  const effectiveName = (nameTouched ? name : defaultName).trim();

  const pickTemplate = (key: TemplateKey) => {
    const preset = TEMPLATES[key];
    setTemplate(key);
    setSignal(preset.signal);
    setOperator(preset.operator);
    setThreshold(preset.threshold);
    setForSeconds(preset.for_seconds);
    setSeverity(preset.severity);
  };

  const changeSignal = (next: Signal) => {
    setTemplate(null);
    setSignal(next);
    const range = RANGES[next];
    setThreshold((current) => Math.min(range.max, Math.max(range.min, current)));
    if (next === "request_rate") setOperator("<");
  };

  const save = async () => {
    if (!csrfToken || !draft || !effectiveName) return;
    setSaving("saving");
    setError(null);
    try {
      if (rule) {
        await updateRule(csrfToken, rule.id, {
          name: effectiveName,
          signal,
          operator,
          threshold,
          for_seconds: forSeconds,
          severity,
          expected_version: rule.version,
        });
      } else {
        await createRule(csrfToken, { ...draft, name: effectiveName });
      }
      setSaving("saved");
      onSaved();
      window.setTimeout(onClose, 500);
    } catch (reason: unknown) {
      setSaving("idle");
      if (reason instanceof ApiError && reason.status === 409) setError(t("drawer.conflict"));
      else
        setError(
          t("drawer.saveError", {
            message: reason instanceof Error ? reason.message : String(reason),
          }),
        );
    }
  };

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const now = useMemo(() => Date.now(), [preview]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!open) return null;

  const range = RANGES[signal];
  const seriesEpisodes =
    preview?.series ? preview.episodes.filter((e) => e.binding_id === preview.series?.binding_id) : [];
  const previewTone = preview && preview.count > 0 ? severityTone(severity) : "var(--ok)";

  return (
    <>
      <div className={s.scrim} onClick={onClose} aria-hidden="true" />
      <section
        className={`${s.root} ${s.drawer}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="alert-rule-drawer-title"
      >
        <div className={s.dh}>
          <h2 id="alert-rule-drawer-title">
            {editing ? t("drawer.editTitle") : t("drawer.createTitle")}
          </h2>
          <button type="button" className={s.btn} onClick={onClose}>
            {t("drawer.close")}
          </button>
        </div>
        <div className={s.db}>
          <form className={s.form} onSubmit={(event) => event.preventDefault()}>
            {!editing ? (
              <div className={s.f}>
                <span className={`${s.cap} ${s.capLabel}`}>{t("drawer.templates")}</span>
                <div className={s.tpls}>
                  {TEMPLATE_KEYS.map((key) => (
                    <button
                      key={key}
                      type="button"
                      className={s.tpl}
                      aria-pressed={template === key}
                      onClick={() => pickTemplate(key)}
                    >
                      {t(`templates.${key}.name`)}
                      <small>{t(`templates.${key}.detail`)}</small>
                    </button>
                  ))}
                </div>
              </div>
            ) : null}

            <div className={s.f}>
              <label className={s.cap} htmlFor="ar-name">
                {t("drawer.name")}
              </label>
              <input
                id="ar-name"
                ref={firstField}
                className={s.input}
                maxLength={120}
                value={nameTouched ? name : defaultName}
                placeholder={t("drawer.namePlaceholder")}
                onChange={(event) => {
                  setNameTouched(true);
                  setName(event.target.value);
                }}
              />
            </div>

            <div className={s.f}>
              <span className={`${s.cap} ${s.capLabel}`}>{t("drawer.target")}</span>
              {editing ? (
                <p className={s.plain}>
                  <b>{targetText}</b>
                  <br />
                  <span>{t("drawer.targetFixed")}</span>
                </p>
              ) : (
                <div className={s.three}>
                  <select
                    aria-label={t("drawer.project")}
                    className={s.input}
                    value={projectId}
                    onChange={(event) => {
                      setProjectId(event.target.value);
                      setEnvironmentId("");
                      setServiceId("");
                    }}
                  >
                    {(options?.projects ?? []).map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.key}
                      </option>
                    ))}
                  </select>
                  <select
                    aria-label={t("drawer.environment")}
                    className={s.input}
                    value={environmentId}
                    onChange={(event) => {
                      setEnvironmentId(event.target.value);
                      setServiceId("");
                    }}
                  >
                    <option value="">{t("drawer.allEnvironments")}</option>
                    {(project?.environments ?? []).map((e) => (
                      <option key={e.id} value={e.id}>
                        {e.key}
                      </option>
                    ))}
                  </select>
                  <select
                    aria-label={t("drawer.service")}
                    className={s.input}
                    value={serviceId}
                    disabled={!environment}
                    onChange={(event) => setServiceId(event.target.value)}
                  >
                    <option value="">{t("drawer.allServices")}</option>
                    {(environment?.services ?? []).map((sv) => (
                      <option key={sv.id} value={sv.id}>
                        {sv.key}
                      </option>
                    ))}
                  </select>
                </div>
              )}
            </div>

            <div className={s.two}>
              <div className={s.f}>
                <label className={s.cap} htmlFor="ar-signal">
                  {t("drawer.signal")}
                </label>
                <select
                  id="ar-signal"
                  className={s.input}
                  value={signal}
                  onChange={(event) => changeSignal(event.target.value as Signal)}
                >
                  {SIGNALS.map((key) => (
                    <option key={key} value={key}>
                      {t.dyn("signal", key)}
                    </option>
                  ))}
                </select>
              </div>
              <div className={s.f}>
                <label className={s.cap} htmlFor="ar-direction">
                  {t("drawer.direction")}
                </label>
                <select
                  id="ar-direction"
                  className={s.input}
                  value={operator}
                  onChange={(event) => {
                    setTemplate(null);
                    setOperator(event.target.value as Operator);
                  }}
                >
                  <option value=">">{t("drawer.above")}</option>
                  <option value="<">{t("drawer.below")}</option>
                </select>
              </div>
            </div>

            <div className={s.f}>
              <label className={s.cap} htmlFor="ar-threshold">
                {t("drawer.threshold")}
              </label>
              <div className={s.thr}>
                <input
                  id="ar-threshold"
                  type="range"
                  min={range.min}
                  max={range.max}
                  step={range.step}
                  value={threshold}
                  onChange={(event) => {
                    setTemplate(null);
                    setThreshold(Number(event.target.value));
                  }}
                />
                <output className={s.d} htmlFor="ar-threshold">
                  {show(threshold)}
                </output>
              </div>
              <input
                aria-label={t("drawer.threshold")}
                type="number"
                className={s.input}
                style={{ marginTop: 8 }}
                min={0}
                max={RATIO_SIGNALS.has(signal) ? 100 : undefined}
                step="any"
                value={toShown(signal, threshold)}
                onChange={(event) => {
                  const value = Number(event.target.value);
                  if (Number.isFinite(value) && value >= 0) {
                    setTemplate(null);
                    setThreshold(fromShown(signal, value));
                  }
                }}
              />
            </div>

            <div className={s.f}>
              <label className={s.cap} htmlFor="ar-for">
                {t("drawer.duration")}
              </label>
              <select
                id="ar-for"
                className={s.input}
                value={forSeconds}
                onChange={(event) => {
                  setTemplate(null);
                  setForSeconds(Number(event.target.value));
                }}
              >
                {(DURATIONS.includes(forSeconds)
                  ? DURATIONS
                  : [...DURATIONS, forSeconds].sort((a, b) => a - b)
                ).map((value) => (
                  <option key={value} value={value}>
                    {value === 0 ? t("drawer.durationNone") : duration(value)}
                  </option>
                ))}
              </select>
            </div>

            <div className={s.f}>
              <span className={`${s.cap} ${s.capLabel}`}>{t("drawer.severity")}</span>
              <div className={s.segment} role="group" aria-label={t("drawer.severity")}>
                {(["critical", "warning"] as Severity[]).map((value) => (
                  <button
                    key={value}
                    type="button"
                    className={s.tpl}
                    aria-pressed={severity === value}
                    onClick={() => setSeverity(value)}
                  >
                    {t(value === "critical" ? "drawer.critical" : "drawer.warning")}
                  </button>
                ))}
              </div>
            </div>

            <p className={s.plain}>
              <b>{targetText}</b>: {sentence}, <b>{action}</b>. {t("sentence.routing")}
            </p>
          </form>

          <div className={s.pv} style={{ ["--c" as string]: previewTone }} aria-live="polite">
            <span className={s.cap}>{t("preview.caption")}</span>
            {previewState === "error" ? (
              <p className={s.err}>{t("preview.error")}</p>
            ) : preview ? (
              <>
                <div className={s.pvN}>
                  <b>{preview.count}</b>
                  <span>
                    {t("preview.wouldFire", { count: preview.count })}
                    {" · "}
                    {preview.count
                      ? t("preview.totalMinutes", { count: preview.minutes })
                      : t("preview.quietDay")}
                  </span>
                </div>
                {preview.series && preview.series.series.length > 1 ? (
                  <SignalChart
                    points={preview.series.series}
                    episodes={seriesEpisodes}
                    threshold={threshold}
                    operator={operator}
                    severity={severity}
                    now={now}
                    format={show}
                    timeLabel={(value) => hhmm(new Date(value), fmt.tag)}
                    thresholdLabel={show(threshold)}
                    nowLabel={t("register.timelineNow")}
                    width={440}
                    height={200}
                    label={t("preview.caption")}
                  />
                ) : (
                  <p className={s.coverage}>{t("preview.noHistory")}</p>
                )}
                {preview.episodes.length ? (
                  <ul className={s.pvL}>
                    <li className={s.cap} style={{ borderTop: 0 }}>
                      <span>{t("preview.start")}</span>
                      <span>{t("drawer.service")}</span>
                      <span>{t("preview.peak")}</span>
                      <span>{t("preview.duration")}</span>
                    </li>
                    {preview.episodes.slice(0, 8).map((episode) => (
                      <li key={episode.binding_id + episode.fired_at}>
                        <span>{hhmm(new Date(episode.fired_at), fmt.tag)}</span>
                        <span>{episode.service}</span>
                        <span>{show(episode.peak ?? threshold)}</span>
                        <span>{duration((episode.minutes ?? 0) * 60)}</span>
                      </li>
                    ))}
                  </ul>
                ) : null}
                {preview.suggestion ? (
                  <p className={s.nudge}>
                    <span>
                      {t("preview.suggestion", {
                        threshold: show(preview.suggestion.threshold),
                        count: preview.suggestion.count,
                      })}
                    </span>
                    <button
                      type="button"
                      className={s.btn}
                      onClick={() => {
                        setTemplate(null);
                        setThreshold(preview.suggestion?.threshold ?? threshold);
                      }}
                    >
                      {t("preview.useSuggestion", { threshold: show(preview.suggestion.threshold) })}
                    </button>
                  </p>
                ) : null}
                <p className={s.coverage}>
                  {t("preview.coverage", {
                    hours: fmt.number(preview.history_hours, { maximumFractionDigits: 1 }),
                    bindings: preview.bindings,
                  })}
                </p>
              </>
            ) : (
              <p className={s.coverage}>{t("preview.loading")}</p>
            )}
          </div>
        </div>
        <div className={s.df}>
          <span className={`${s.cap} ${s.d}`}>{t("drawer.checkedEvery")}</span>
          {error ? <span className={s.err}>{error}</span> : null}
          <span style={{ display: "flex", gap: 8 }}>
            <button type="button" className={s.btn} onClick={onClose}>
              {t("drawer.cancel")}
            </button>
            <button
              type="button"
              className={`${s.btn} ${s.go}`}
              disabled={!csrfToken || !draft || !effectiveName || saving !== "idle"}
              onClick={() => void save()}
            >
              {saving === "saving"
                ? t("drawer.saving")
                : saving === "saved"
                  ? t("drawer.saved")
                  : t("drawer.save")}
            </button>
          </span>
        </div>
      </section>
    </>
  );
}

export type { TemplateKey };
export { TEMPLATE_KEYS };
