"use client";

/**
 * Writing a rule. The left side is the rule; the right side is the server
 * replaying it over the last day of stored samples, so the person sees
 * "this would have fired 3 times" before saving — the same `step` the
 * runner will use, not a browser-side estimate.
 */

import {
  Activity,
  ArrowDownToLine,
  ArrowUpFromLine,
  BellRing,
  FileCheck2,
  Gauge,
  History,
  ScrollText,
  Send,
  SlidersHorizontal,
  Siren,
  Sparkles,
  Target,
  TrendingDown,
  TrendingUp,
  TriangleAlert,
  Waypoints,
  Workflow,
  Zap,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";

import { kindColour } from "@/components/apps/AppLauncher";
import { ProjectLogo } from "@/components/ui/ProjectLogo";

import {
  ANOMALY_OPERATORS,
  RATIO_SIGNALS,
  SIGNALS,
  SIGNAL_SOURCE,
  SIGNAL_SOURCES,
  createRule,
  fetchRuleRoutes,
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
  type SignalSource,
} from "@/lib/alertRules";
import { ApiError } from "@/lib/api";
import { useFormat, useT } from "@/lib/i18n";
import { useProjectNames } from "@/lib/platform";

import s from "./alertRules.module.css";
import { SignalChart, hhmm, severityTone } from "./instruments";
import { Choice, FieldLabel, FlowNode } from "./workflow";

const SOURCE_ICON: Record<SignalSource, LucideIcon> = { metrics: Gauge, logs: ScrollText, traces: Waypoints };

/** The workflow's steps, each folding on its own. */
type NodeKey = "start" | "scope" | "signal" | "condition" | "action" | "review";
const ALL_NODES: NodeKey[] = ["start", "scope", "signal", "condition", "action", "review"];

type TemplateKey =
  | "errors"
  | "latency"
  | "cpu"
  | "memory"
  | "restarts"
  | "traffic"
  | "fatalLogs"
  | "errorLogs"
  | "slowRoute"
  | "textInLogs"
  | "unusualErrors"
  | "unusualLatency";

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
  fatalLogs: { signal: "fatal_logs", operator: ">", threshold: 0, for_seconds: 0, severity: "critical" },
  errorLogs: { signal: "error_logs", operator: ">", threshold: 20, for_seconds: 300, severity: "warning" },
  slowRoute: { signal: "slowest_route_p95_seconds", operator: ">", threshold: 1, for_seconds: 300, severity: "warning" },
  textInLogs: { signal: "matching_logs", operator: ">", threshold: 0, for_seconds: 0, severity: "critical" },
  unusualErrors: { signal: "error_logs", operator: "above_normal", threshold: 3, for_seconds: 600, severity: "warning" },
  unusualLatency: { signal: "latency_p95_seconds", operator: "above_normal", threshold: 3, for_seconds: 600, severity: "warning" },
};
const TEMPLATE_KEYS = Object.keys(TEMPLATES) as TemplateKey[];

/** The signal a template watches, for its icon. */
export function templateSignal(key: TemplateKey): Signal {
  return TEMPLATES[key].signal;
}

/** Slider range in the signal's stored unit. */
const RANGES: Record<Signal, { min: number; max: number; step: number }> = {
  error_ratio: { min: 0.005, max: 0.5, step: 0.005 },
  latency_p95_seconds: { min: 0.05, max: 5, step: 0.05 },
  request_rate: { min: 0, max: 100, step: 0.1 },
  restarts: { min: 0, max: 20, step: 1 },
  cpu_utilization: { min: 0.05, max: 1, step: 0.01 },
  memory_utilization: { min: 0.05, max: 1, step: 0.01 },
  unavailable_replicas: { min: 0, max: 10, step: 1 },
  error_logs: { min: 0, max: 500, step: 1 },
  fatal_logs: { min: 0, max: 50, step: 1 },
  slow_requests: { min: 0, max: 500, step: 1 },
  slowest_route_p95_seconds: { min: 0.1, max: 10, step: 0.1 },
  matching_logs: { min: 0, max: 200, step: 1 },
};
/** An anomaly rule's threshold is a sensitivity: spreads away from normal. */
const SENSITIVITY = { min: 1, max: 10, step: 0.5 };

const DURATIONS = [0, 60, 120, 300, 600, 900, 1800, 3600];

/** Which sentence describes a rule: fixed or against normal, held or at once. */
export function sentenceFor(operator: Operator, forSeconds: number) {
  const held = forSeconds > 0;
  if (operator === "above_normal") return held ? "sentence.aboveNormalFor" : "sentence.aboveNormal";
  if (operator === "below_normal") return held ? "sentence.belowNormalFor" : "sentence.belowNormal";
  if (operator === "<") return held ? "sentence.belowFor" : "sentence.below";
  return held ? "sentence.aboveFor" : "sentence.above";
}

/** A signal's name with what it is narrowed to. */
export function filteredName(name: string, text: string | null, endpoint: string | null): string {
  if (text) return `${name} “${text}”`;
  if (endpoint) return `${name} · ${endpoint}`;
  return name;
}

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
  const [logContains, setLogContains] = useState("");
  const [route, setRoute] = useState("");
  const [routeChoices, setRouteChoices] = useState<string[]>([]);
  const [preview, setPreview] = useState<RulePreview | null>(null);
  const [previewState, setPreviewState] = useState<"idle" | "loading" | "error">("idle");
  const [saving, setSaving] = useState<"idle" | "saving" | "saved">("idle");
  const [error, setError] = useState<string | null>(null);
  const sheetRef = useRef<HTMLElement>(null);
  const [openNodes, setOpenNodes] = useState<Set<NodeKey>>(() => new Set(ALL_NODES));
  const names = useProjectNames();

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
      setLogContains(rule.log_contains ?? "");
      setRoute(rule.route ?? "");
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
      setLogContains("");
      setRoute("");
    }
    setOpenNodes(new Set(ALL_NODES));
    const focus = window.setTimeout(() => sheetRef.current?.focus(), 0);
    return () => window.clearTimeout(focus);
  }, [open, rule, initialTemplate, defaultProjectId, options]);

  const project = options?.projects.find((p) => p.id === projectId);
  const environment = project?.environments.find((e) => e.id === environmentId);
  const service = environment?.services.find((sv) => sv.id === serviceId);

  const targetText = rule
    ? [rule.target.project, rule.target.environment ?? t("scope.allEnvironments"), rule.target.service ?? t("scope.allServices")].join(" / ")
    : [project?.key ?? "—", environment?.key ?? t("scope.allEnvironments"), service?.key ?? t("scope.allServices")].join(" / ");

  const source = SIGNAL_SOURCE[signal];
  const text = source === "logs" ? logContains.trim() : "";
  const endpoint = source === "traces" ? route.trim() : "";
  const anomaly = ANOMALY_OPERATORS.has(operator);
  // A text rule needs its text; nothing else is incomplete.
  const incomplete = signal === "matching_logs" && !text;
  const draft: RuleDraft | null =
    projectId && !incomplete
      ? {
          project_id: projectId,
          environment_id: environmentId || null,
          service_id: serviceId || null,
          signal,
          operator,
          threshold,
          for_seconds: forSeconds,
          severity,
          log_contains: text || null,
          route: endpoint || null,
        }
      : null;

  // The endpoints the target served, for a single-endpoint rule.
  useEffect(() => {
    if (!open || source !== "traces" || !projectId) return;
    let cancelled = false;
    fetchRuleRoutes({
      project_id: projectId,
      environment_id: environmentId || null,
      service_id: serviceId || null,
    })
      .then((body) => !cancelled && setRouteChoices(body.routes))
      .catch(() => !cancelled && setRouteChoices([]));
    return () => {
      cancelled = true;
    };
  }, [open, source, projectId, environmentId, serviceId]);
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
  const signalName = filteredName(t.dyn("signal", signal), text, endpoint);
  const sentenceKey = sentenceFor(operator, forSeconds);
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
    setLogContains("");
    setRoute("");
  };

  const changeSignal = (next: Signal) => {
    setTemplate(null);
    setSignal(next);
    if (!anomaly) {
      const range = RANGES[next];
      setThreshold((current) => Math.min(range.max, Math.max(range.min, current)));
    }
    if (next === "request_rate" && !anomaly) setOperator("<");
    // A text-only signal has no stored history to call normal.
    if (next === "matching_logs" && anomaly) {
      setOperator(">");
      setThreshold(0);
    }
  };

  const changeOperator = (next: Operator) => {
    setTemplate(null);
    const wasAnomaly = anomaly;
    setOperator(next);
    if (ANOMALY_OPERATORS.has(next) && !wasAnomaly) setThreshold(3);
    if (!ANOMALY_OPERATORS.has(next) && wasAnomaly) {
      // Back to a fixed threshold: the template value for this signal, or
      // the low end of its range.
      const preset = Object.values(TEMPLATES).find(
        (tpl) => tpl.signal === signal && !ANOMALY_OPERATORS.has(tpl.operator),
      );
      setThreshold(preset?.threshold ?? RANGES[signal].min);
    }
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
          log_contains: text || null,
          route: endpoint || null,
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
  const toggle = (key: NodeKey) =>
    setOpenNodes((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  const projectName = (id: string, key: string) => names.get(id) ?? key;
  const conditionSummary = t(sentenceKey, {
    signal: signalName,
    threshold: show(threshold),
    duration: duration(forSeconds),
  });
  const fireBadge =
    previewState === "error" || !preview ? null : (
      <span
        className={`shrink-0 rounded-full px-2.5 py-1 font-mono text-[11px] font-semibold ${
          preview.count ? "bg-warning-soft text-warning" : "bg-healthy-soft text-healthy"
        }`}
      >
        {t("flow.firedBadge", { count: preview.count })}
      </span>
    );
  let step = 0;

  return (
    <>
      <div className={s.scrim} onClick={onClose} aria-hidden="true" />
      <section
        ref={sheetRef}
        tabIndex={-1}
        className={`${s.root} fixed inset-y-0 right-0 z-[51] flex w-[min(1180px,100%)] flex-col border-l border-border bg-canvas shadow-[var(--shadow-overlay)] outline-none`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="alert-rule-drawer-title"
      >
        <header className="flex items-center gap-4 border-b border-border bg-surface px-6 py-4">
          <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-brand-soft text-brand-accent">
            <Workflow aria-hidden className="h-5 w-5" />
          </span>
          <div className="min-w-0 flex-1">
            <h2
              id="alert-rule-drawer-title"
              className="m-0 font-[family-name:var(--font-display)] text-[1.25rem] font-bold tracking-[-0.02em] text-ink"
            >
              {editing ? t("drawer.editTitle") : t("drawer.createTitle")}
            </h2>
            <p className="m-0 truncate text-caption text-ink-muted">{t("flow.lede")}</p>
          </div>
          <button type="button" className={s.btn} onClick={onClose}>
            {t("drawer.close")}
          </button>
        </header>

        <div className="grid min-h-0 flex-1 overflow-y-auto lg:grid-cols-[minmax(0,1fr)_400px] lg:overflow-hidden">
          <form className="min-w-0 px-6 py-6 lg:overflow-y-auto" onSubmit={(event) => event.preventDefault()}>
            <p className="mb-4 flex items-center gap-2 font-mono text-[10.5px] font-medium tracking-[0.14em] text-ink-muted uppercase">
              <Zap aria-hidden className="h-3.5 w-3.5 text-gold" />
              {t("flow.trigger")}
            </p>
            <ol className="m-0 list-none p-0">
              {!editing ? (
                <FlowNode
                  index={++step}
                  icon={Sparkles}
                  title={t("drawer.templates")}
                  summary={template ? t(`templates.${template}.name`) : t("flow.custom")}
                  state={template ? "done" : "todo"}
                  open={openNodes.has("start")}
                  onToggle={() => toggle("start")}
                  testId="flow-start"
                >
                  <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                    {TEMPLATE_KEYS.map((key) => {
                      const SourceIcon = SOURCE_ICON[SIGNAL_SOURCE[TEMPLATES[key].signal]];
                      return (
                        <button
                          key={key}
                          type="button"
                          aria-pressed={template === key}
                          onClick={() => pickTemplate(key)}
                          className={`flex items-start gap-2.5 rounded-xl border px-3 py-2.5 text-left transition-colors outline-none focus-visible:ring-2 focus-visible:ring-focus ${
                            template === key
                              ? "border-brand-accent/70 bg-brand-soft ring-1 ring-brand-accent/40"
                              : "border-border bg-surface hover:border-border-strong hover:bg-surface-hover"
                          }`}
                        >
                          <SourceIcon aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-brand-accent" />
                          <span className="min-w-0">
                            <span className="block text-caption font-semibold text-ink">
                              {t(`templates.${key}.name`)}
                            </span>
                            <span className="block font-mono text-[11px] text-ink-muted">
                              {t(`templates.${key}.detail`)}
                            </span>
                          </span>
                        </button>
                      );
                    })}
                  </div>
                </FlowNode>
              ) : null}

              <FlowNode
                index={++step}
                icon={Target}
                title={t("drawer.target")}
                summary={targetText}
                state={projectId ? "done" : "todo"}
                open={openNodes.has("scope")}
                onToggle={() => toggle("scope")}
                testId="flow-scope"
              >
                {rule ? (
                  <div className="flex items-center gap-3 rounded-xl border border-border bg-surface-2 px-3.5 py-3">
                    <ProjectLogo projectId={rule.project_id} name={rule.target.project} size="md" variant="mark" />
                    <span className="min-w-0">
                      <b className="block truncate text-body text-ink">{targetText}</b>
                      <span className="text-caption text-ink-muted">{t("drawer.targetFixed")}</span>
                    </span>
                  </div>
                ) : (
                  <div className="flex flex-col gap-4">
                    <div>
                      <FieldLabel>{t("drawer.project")}</FieldLabel>
                      <div
                        role="radiogroup"
                        aria-label={t("drawer.project")}
                        className="grid grid-cols-2 gap-2 sm:grid-cols-3"
                      >
                        {(options?.projects ?? []).map((p) => (
                          <Choice
                            key={p.id}
                            selected={projectId === p.id}
                            onSelect={() => {
                              setProjectId(p.id);
                              setEnvironmentId("");
                              setServiceId("");
                            }}
                            className="flex items-center gap-2.5 px-3 py-2.5"
                          >
                            <ProjectLogo projectId={p.id} name={projectName(p.id, p.key)} size="md" variant="mark" />
                            <span className="min-w-0">
                              <span className="block truncate text-caption font-semibold text-ink">
                                {projectName(p.id, p.key)}
                              </span>
                              <span className="block font-mono text-[11px] text-ink-muted">
                                {t("flow.envCount", { count: p.environments.length })}
                              </span>
                            </span>
                          </Choice>
                        ))}
                      </div>
                    </div>
                    {project ? (
                      <div>
                        <FieldLabel>{t("drawer.environment")}</FieldLabel>
                        <div role="radiogroup" aria-label={t("drawer.environment")} className="flex flex-wrap gap-2">
                          <Choice
                            selected={!environmentId}
                            onSelect={() => {
                              setEnvironmentId("");
                              setServiceId("");
                            }}
                            className="px-3 py-1.5 text-caption font-medium"
                          >
                            {t("drawer.allEnvironments")}
                          </Choice>
                          {project.environments.map((e) => (
                            <Choice
                              key={e.id}
                              selected={environmentId === e.id}
                              onSelect={() => {
                                setEnvironmentId(e.id);
                                setServiceId("");
                              }}
                              className="inline-flex items-center gap-2 px-3 py-1.5 font-mono text-caption"
                            >
                              <span
                                aria-hidden
                                className="h-2 w-2 rounded-full"
                                style={{ background: kindColour(e.key) }}
                              />
                              {e.key}
                            </Choice>
                          ))}
                        </div>
                      </div>
                    ) : null}
                    {environment ? (
                      <div>
                        <FieldLabel>{t("drawer.service")}</FieldLabel>
                        <div role="radiogroup" aria-label={t("drawer.service")} className="flex flex-wrap gap-2">
                          <Choice
                            selected={!serviceId}
                            onSelect={() => setServiceId("")}
                            className="px-3 py-1.5 text-caption font-medium"
                          >
                            {t("drawer.allServices")}
                          </Choice>
                          {environment.services.map((sv) => (
                            <Choice
                              key={sv.id}
                              selected={serviceId === sv.id}
                              onSelect={() => setServiceId(sv.id)}
                              className="px-3 py-1.5 font-mono text-caption"
                            >
                              {sv.key}
                            </Choice>
                          ))}
                        </div>
                      </div>
                    ) : null}
                  </div>
                )}
              </FlowNode>

              <FlowNode
                index={++step}
                icon={Activity}
                title={t("drawer.signal")}
                summary={signalName}
                state={incomplete ? "invalid" : "done"}
                open={openNodes.has("signal")}
                onToggle={() => toggle("signal")}
                testId="flow-signal"
              >
                <div role="radiogroup" aria-label={t("drawer.signal")} className="flex flex-col gap-4">
                  {SIGNAL_SOURCES.map((group) => {
                    const SourceIcon = SOURCE_ICON[group];
                    return (
                      <div key={group} role="group" aria-label={t(`sources.${group}`)}>
                        <p className="mb-2 flex items-center gap-1.5 font-mono text-[10.5px] font-medium tracking-[0.14em] text-ink-muted uppercase">
                          <SourceIcon aria-hidden className="h-3.5 w-3.5" />
                          {t(`sources.${group}`)}
                        </p>
                        <div className="grid gap-2 sm:grid-cols-2">
                          {SIGNALS.filter((key) => SIGNAL_SOURCE[key] === group).map((key) => (
                            <Choice
                              key={key}
                              selected={signal === key}
                              onSelect={() => changeSignal(key)}
                              className="px-3 py-2.5"
                            >
                              <span className="block text-caption font-semibold text-ink">{t.dyn("signal", key)}</span>
                              <span className="mt-0.5 block text-[11.5px] leading-snug text-ink-muted">
                                {t.dyn("signalHint", key)}
                              </span>
                            </Choice>
                          ))}
                        </div>
                      </div>
                    );
                  })}
                </div>

                {source === "logs" ? (
                  <div className="mt-4">
                    <FieldLabel htmlFor="ar-contains">
                      {signal === "matching_logs" ? t("drawer.containsRequired") : t("drawer.contains")}
                    </FieldLabel>
                    <input
                      id="ar-contains"
                      className={s.input}
                      maxLength={200}
                      placeholder={t("drawer.containsPlaceholder")}
                      value={logContains}
                      disabled={anomaly}
                      onChange={(event) => {
                        setTemplate(null);
                        setLogContains(event.target.value);
                      }}
                    />
                    <small className="mt-1.5 block text-micro text-ink-muted">
                      {anomaly ? t("drawer.filterNotWithNormal") : t("drawer.containsHint")}
                    </small>
                    {incomplete ? (
                      <p className="mt-2 text-caption font-medium text-warning">{t("drawer.needsText")}</p>
                    ) : null}
                  </div>
                ) : null}

                {source === "traces" ? (
                  <div className="mt-4">
                    <FieldLabel htmlFor="ar-route">{t("drawer.route")}</FieldLabel>
                    <input
                      id="ar-route"
                      className={s.input}
                      maxLength={300}
                      list="ar-route-choices"
                      placeholder={t("drawer.routePlaceholder")}
                      value={route}
                      disabled={anomaly}
                      onChange={(event) => {
                        setTemplate(null);
                        setRoute(event.target.value);
                      }}
                    />
                    <datalist id="ar-route-choices">
                      {routeChoices.map((choice) => (
                        <option key={choice} value={choice} />
                      ))}
                    </datalist>
                    <small className="mt-1.5 block text-micro text-ink-muted">
                      {anomaly ? t("drawer.filterNotWithNormal") : t("drawer.routeHint")}
                    </small>
                  </div>
                ) : null}
              </FlowNode>

              <FlowNode
                index={++step}
                icon={SlidersHorizontal}
                title={t("flow.condition")}
                summary={conditionSummary}
                state={draft ? "done" : "todo"}
                open={openNodes.has("condition")}
                onToggle={() => toggle("condition")}
                badge={fireBadge}
                testId="flow-condition"
              >
                <FieldLabel>{t("drawer.direction")}</FieldLabel>
                <div
                  role="radiogroup"
                  aria-label={t("drawer.direction")}
                  className="grid grid-cols-2 gap-2 sm:grid-cols-4"
                >
                  {(
                    [
                      [">", t("drawer.above"), TrendingUp],
                      ["<", t("drawer.below"), TrendingDown],
                      ["above_normal", t("drawer.aboveNormal"), ArrowUpFromLine],
                      ["below_normal", t("drawer.belowNormal"), ArrowDownToLine],
                    ] as const
                  ).map(([value, label, DirIcon]) => (
                    <Choice
                      key={value}
                      selected={operator === value}
                      onSelect={() => changeOperator(value)}
                      disabled={
                        ANOMALY_OPERATORS.has(value) && (Boolean(text || endpoint) || signal === "matching_logs")
                      }
                      className="flex flex-col items-center gap-1 px-2 py-2.5 text-center text-caption font-medium"
                    >
                      <DirIcon aria-hidden className="h-4 w-4 text-brand-accent" />
                      {label}
                    </Choice>
                  ))}
                </div>

                <div className="mt-5 rounded-xl border border-border bg-surface-2 px-4 py-3.5">
                  <div className="flex items-baseline justify-between gap-3">
                    <label
                      htmlFor="ar-threshold"
                      className="font-mono text-[10.5px] font-medium tracking-[0.14em] text-ink-muted uppercase"
                    >
                      {anomaly ? t("drawer.sensitivity") : t("drawer.threshold")}
                    </label>
                    <output
                      htmlFor="ar-threshold"
                      className="font-[family-name:var(--font-display)] text-[1.375rem] font-bold tracking-[-0.02em] text-ink tabular-nums"
                    >
                      {anomaly ? t("drawer.sensitivityValue", { value: fmt.number(threshold) }) : show(threshold)}
                    </output>
                  </div>
                  <input
                    id="ar-threshold"
                    type="range"
                    className="mt-2 w-full accent-[var(--brand-accent)]"
                    min={anomaly ? SENSITIVITY.min : range.min}
                    max={anomaly ? SENSITIVITY.max : range.max}
                    step={anomaly ? SENSITIVITY.step : range.step}
                    value={threshold}
                    onChange={(event) => {
                      setTemplate(null);
                      setThreshold(Number(event.target.value));
                    }}
                  />
                  <div className="flex justify-between font-mono text-[10.5px] text-ink-muted">
                    <span>{anomaly ? fmt.number(SENSITIVITY.min) : show(range.min)}</span>
                    <span>{anomaly ? fmt.number(SENSITIVITY.max) : show(range.max)}</span>
                  </div>
                  {anomaly ? (
                    <small className="mt-2 block text-micro text-ink-muted">{t("drawer.sensitivityHint")}</small>
                  ) : (
                    <input
                      aria-label={t("drawer.threshold")}
                      type="number"
                      className={`${s.input} mt-2.5 max-w-[10rem]`}
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
                  )}
                </div>

                <div className="mt-5">
                  <FieldLabel>{t("drawer.duration")}</FieldLabel>
                  <div role="radiogroup" aria-label={t("drawer.duration")} className="flex flex-wrap gap-2">
                    {(DURATIONS.includes(forSeconds)
                      ? DURATIONS
                      : [...DURATIONS, forSeconds].sort((a, b) => a - b)
                    ).map((value) => (
                      <Choice
                        key={value}
                        selected={forSeconds === value}
                        onSelect={() => {
                          setTemplate(null);
                          setForSeconds(value);
                        }}
                        className="px-3 py-1.5 font-mono text-caption"
                      >
                        {value === 0 ? t("drawer.durationNone") : duration(value)}
                      </Choice>
                    ))}
                  </div>
                </div>
              </FlowNode>

              <FlowNode
                index={++step}
                icon={BellRing}
                title={t("flow.action")}
                summary={action}
                state="done"
                open={openNodes.has("action")}
                onToggle={() => toggle("action")}
                testId="flow-action"
              >
                <div role="radiogroup" aria-label={t("drawer.severity")} className="grid gap-2 sm:grid-cols-2">
                  {(["critical", "warning"] as Severity[]).map((value) => {
                    const SevIcon = value === "critical" ? Siren : TriangleAlert;
                    return (
                      <Choice
                        key={value}
                        selected={severity === value}
                        onSelect={() => setSeverity(value)}
                        className="flex items-start gap-3 px-3.5 py-3"
                      >
                        <span
                          className={`grid h-8 w-8 shrink-0 place-items-center rounded-lg ${
                            value === "critical" ? "bg-critical-soft text-critical" : "bg-warning-soft text-warning"
                          }`}
                        >
                          <SevIcon aria-hidden className="h-4 w-4" />
                        </span>
                        <span className="min-w-0">
                          <span className="block text-caption font-semibold text-ink">
                            {t(value === "critical" ? "drawer.critical" : "drawer.warning")}
                          </span>
                          <span className="mt-0.5 block text-[11.5px] leading-snug text-ink-muted">
                            {t(value === "critical" ? "flow.criticalHint" : "flow.warningHint")}
                          </span>
                        </span>
                      </Choice>
                    );
                  })}
                </div>
                <Link
                  href="/notification-policies"
                  className="mt-3 flex items-center gap-3 rounded-xl border border-dashed border-border-strong px-3.5 py-3 text-caption text-ink-secondary transition-colors hover:bg-surface-hover"
                >
                  <Send aria-hidden className="h-4 w-4 shrink-0 text-brand-accent" />
                  <span className="min-w-0 flex-1">{t("sentence.routing")}</span>
                  <span className="shrink-0 font-semibold text-brand-accent">{t("flow.routingLink")}</span>
                </Link>
              </FlowNode>

              <FlowNode
                index={++step}
                icon={FileCheck2}
                title={t("flow.review")}
                summary={effectiveName || t("drawer.namePlaceholder")}
                state={effectiveName && draft ? "done" : "todo"}
                open={openNodes.has("review")}
                onToggle={() => toggle("review")}
                last
                testId="flow-review"
              >
                <FieldLabel htmlFor="ar-name">{t("drawer.name")}</FieldLabel>
                <input
                  id="ar-name"
                  className={s.input}
                  maxLength={120}
                  value={nameTouched ? name : defaultName}
                  placeholder={t("drawer.namePlaceholder")}
                  onChange={(event) => {
                    setNameTouched(true);
                    setName(event.target.value);
                  }}
                />
                <p className="mt-4 rounded-xl border border-brand-accent/30 bg-brand-soft px-4 py-3 text-caption leading-relaxed text-ink">
                  <b>{targetText}</b>: {sentence}, <b>{action}</b>. {t("sentence.routing")}
                </p>
              </FlowNode>
            </ol>
          </form>

          <aside
            className="border-t border-border bg-surface px-6 py-6 lg:overflow-y-auto lg:border-t-0 lg:border-l"
            style={{ ["--c" as string]: previewTone }}
            aria-live="polite"
          >
            <p className="flex items-center gap-2 font-mono text-[10.5px] font-medium tracking-[0.14em] text-ink-muted uppercase">
              <History aria-hidden className="h-3.5 w-3.5" />
              {t("preview.caption")}
            </p>
            {previewState === "error" ? (
              <p className={s.err}>{t("preview.error")}</p>
            ) : preview ? (
              <>
                <div className={s.pvN}>
                  <b>{preview.count}</b>
                  <span>
                    {t("preview.wouldFire", { count: preview.count })}
                    {" · "}
                    {preview.count ? t("preview.totalMinutes", { count: preview.minutes }) : t("preview.quietDay")}
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
                    width={360}
                    height={190}
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
              <p className={s.coverage}>{incomplete ? "—" : t("preview.loading")}</p>
            )}

            <div className="mt-6 rounded-xl border border-border bg-surface-2 p-4">
              <p className="mb-3 font-mono text-[10.5px] font-medium tracking-[0.14em] text-ink-muted uppercase">
                {t("flow.summary")}
              </p>
              <dl className="m-0 grid gap-2.5 text-caption">
                {(
                  [
                    [Target, t("drawer.target"), targetText],
                    [Activity, t("drawer.signal"), signalName],
                    [SlidersHorizontal, t("flow.condition"), conditionSummary],
                    [BellRing, t("flow.action"), action],
                  ] as const
                ).map(([RowIcon, label, value]) => (
                  <div key={label} className="flex items-start gap-2.5">
                    <RowIcon aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0 text-brand-accent" />
                    <dt className="sr-only">{label}</dt>
                    <dd className="m-0 min-w-0 text-ink-secondary">{value}</dd>
                  </div>
                ))}
              </dl>
            </div>
          </aside>
        </div>

        <footer className="flex flex-wrap items-center gap-3 border-t border-border bg-surface px-6 py-3.5">
          <span className="font-mono text-[11px] text-ink-muted">
            {t("drawer.checkedEvery", { duration: duration(options?.evaluation_interval_seconds ?? 60) })}
          </span>
          {error ? <span className={s.err}>{error}</span> : null}
          <span className="ml-auto flex gap-2">
            <button type="button" className={s.btn} onClick={onClose}>
              {t("drawer.cancel")}
            </button>
            <button
              type="button"
              className={`${s.btn} ${s.go}`}
              disabled={!csrfToken || !draft || !effectiveName || saving !== "idle"}
              onClick={() => void save()}
            >
              {saving === "saving" ? t("drawer.saving") : saving === "saved" ? t("drawer.saved") : t("drawer.save")}
            </button>
          </span>
        </footer>
      </section>
    </>
  );
}

export type { TemplateKey };
export { TEMPLATE_KEYS };
