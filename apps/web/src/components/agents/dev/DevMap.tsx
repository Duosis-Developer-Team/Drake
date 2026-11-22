"use client";

/**
 * The development map: people → the projects they changed → the services
 * under them. Cards are HTML (readable, focusable, clickable); the arrows
 * between them are one SVG underneath, in the same coordinates.
 *
 * An arrow is one coding agent between one person and one project: its
 * colour is the agent, its width the commits, its chip says both. An arrow
 * with Claude Code telemetry but no commits in the window is dashed — the
 * person worked there, nothing landed yet.
 */

import { useMemo, useState } from "react";

import { TracesisMark } from "@/components/shell/Brand";
import { ProjectLogo } from "@/components/ui/ProjectLogo";
import { useLocale, useT } from "@/lib/i18n";
import {
  AGENT_COLOUR,
  AGENT_NAME,
  compact,
  initials,
  personColour,
  usd,
  type CodingAgent,
  type DevEdge,
  type DevMap as Map_,
  type DevPerson,
  type DevProject,
} from "@/lib/devAgents";

export type Pick =
  | { kind: "person"; key: string }
  | { kind: "project"; id: string }
  | { kind: "edge"; person: string; project: string; agent: CodingAgent }
  | { kind: "service"; project: string; key: string | null };

const PERSON_W = 236;
const PERSON_H = 68;
const PERSON_GAP = 14;
const PROJECT_W = 244;
const PROJECT_H = 86;
const PROJECT_GAP = 22;
const SERVICE_W = 196;
const SERVICE_H = 34;
const SERVICE_GAP = 8;
const GROUP_GAP = 18;
const MAX_PEOPLE = 14;
const MAX_SERVICES = 4;

export function Avatar({ person, size = 36 }: { person: Pick_ | DevPerson; size?: number }) {
  const colour = personColour(person.key);
  return (
    <span
      aria-hidden
      className="relative grid shrink-0 place-items-center rounded-full font-semibold text-white"
      style={{
        width: size,
        height: size,
        fontSize: size * 0.36,
        background: `linear-gradient(145deg, color-mix(in srgb, ${colour} 82%, white), ${colour})`,
        boxShadow: `0 0 0 2px var(--surface-1), 0 0 0 3.5px color-mix(in srgb, ${colour} 55%, transparent)`,
      }}
    >
      {initials(person.name)}
    </span>
  );
}

type Pick_ = { key: string; name: string };

/** A project's own logo; Tracesis, watching itself, wears its own mark. */
export function ProjectMark({
  id,
  projectKey,
  name,
  size = "md",
}: {
  id: string;
  projectKey: string;
  name: string;
  size?: "sm" | "md" | "lg";
}) {
  if (projectKey === "tracesis") {
    return <TracesisMark height={size === "sm" ? 9 : size === "lg" ? 18 : 14} />;
  }
  return <ProjectLogo projectId={id} name={name} size={size} variant="mark" />;
}

function bezier(x1: number, y1: number, x2: number, y2: number) {
  const dx = (x2 - x1) * 0.46;
  const path = `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;
  // The midpoint of that cubic, where the chip sits.
  const mx = 0.125 * x1 + 0.375 * (x1 + dx) + 0.375 * (x2 - dx) + 0.125 * x2;
  const my = 0.5 * y1 + 0.5 * y2;
  return { path, mx, my };
}

export function DevMap({
  map,
  onPick,
  picked,
  width,
}: {
  map: Map_;
  onPick: (pick: Pick) => void;
  picked: Pick | null;
  width: number;
}) {
  const t = useT("devAgents");
  const { locale } = useLocale();
  const [hover, setHover] = useState<Pick | null>(null);

  const layout = useMemo(() => {
    const people = map.people.slice(0, MAX_PEOPLE);
    const projects = map.projects;
    const services: { project: DevProject; key: string | null; commits: number; more: number }[] =
      [];
    for (const project of projects) {
      const own = map.services
        .filter((s) => s.project_id === project.id)
        .sort((a, b) => b.commits - a.commits);
      own.slice(0, MAX_SERVICES).forEach((s, i) =>
        services.push({
          project,
          key: s.key,
          commits: s.commits,
          more: i === MAX_SERVICES - 1 ? Math.max(0, own.length - MAX_SERVICES) : 0,
        }),
      );
    }
    const peopleH = people.length * PERSON_H + Math.max(0, people.length - 1) * PERSON_GAP;
    const projectsH = projects.length * PROJECT_H + Math.max(0, projects.length - 1) * PROJECT_GAP;
    let servicesH = 0;
    let previous: string | null = null;
    for (const s of services) {
      servicesH += (servicesH ? (s.project.id === previous ? SERVICE_GAP : GROUP_GAP) : 0) + SERVICE_H;
      previous = s.project.id;
    }
    const height = Math.max(peopleH, projectsH, servicesH) + 48;
    const top = (h: number) => (height - h) / 2;
    const cols = {
      person: 0,
      project: Math.round(width * 0.56 - PROJECT_W / 2),
      service: width - SERVICE_W,
    };
    const personAt = new Map<string, { x: number; y: number }>();
    people.forEach((p, i) =>
      personAt.set(p.key, { x: cols.person, y: top(peopleH) + i * (PERSON_H + PERSON_GAP) }),
    );
    const projectAt = new Map<string, { x: number; y: number }>();
    projects.forEach((p, i) =>
      projectAt.set(p.id, { x: cols.project, y: top(projectsH) + i * (PROJECT_H + PROJECT_GAP) }),
    );
    const serviceAt: { s: (typeof services)[number]; x: number; y: number }[] = [];
    let y = top(servicesH);
    previous = null;
    services.forEach((s, i) => {
      if (i) y += s.project.id === previous ? SERVICE_GAP : GROUP_GAP;
      serviceAt.push({ s, x: cols.service, y });
      y += SERVICE_H;
      previous = s.project.id;
    });
    return { people, projects, personAt, projectAt, serviceAt, height, hidden: map.people.length - people.length };
  }, [map, width]);

  const maxCommits = Math.max(1, ...map.edges.map((e) => e.commits));
  const edges = map.edges.filter((e) => layout.personAt.has(e.person) && layout.projectAt.has(e.project_id));
  const pairs = new Map<string, DevEdge[]>();
  for (const e of edges) {
    const k = `${e.person}|${e.project_id}`;
    pairs.set(k, [...(pairs.get(k) ?? []), e]);
  }

  const focus = hover ?? picked;
  const lit = (person: string, project: string, agent?: string) => {
    if (!focus) return true;
    if (focus.kind === "person") return focus.key === person;
    if (focus.kind === "project") return focus.id === project;
    if (focus.kind === "edge")
      return focus.person === person && focus.project === project && (!agent || focus.agent === agent);
    return focus.project === project;
  };
  const personLit = (key: string) =>
    !focus ||
    (focus.kind === "person" && focus.key === key) ||
    ((focus.kind === "project" || focus.kind === "service") &&
      edges.some((e) => e.person === key && e.project_id === (focus.kind === "project" ? focus.id : focus.project))) ||
    (focus.kind === "edge" && focus.person === key);
  const projectLit = (id: string) =>
    !focus ||
    (focus.kind === "project" && focus.id === id) ||
    (focus.kind === "service" && focus.project === id) ||
    (focus.kind === "edge" && focus.project === id) ||
    (focus.kind === "person" && edges.some((e) => e.person === focus.key && e.project_id === id));

  const showChips = edges.length <= 16;
  // Chips sit on their arrow's midpoint, then step down until no two touch:
  // arrows from one person fan out and their midpoints crowd together.
  const chipAt = new Map<string, { x: number; y: number }>();
  {
    const placed: { x: number; y: number; w: number }[] = [];
    const items = [...pairs.values()].flatMap((group) =>
      group.map((e, i) => {
        const from = layout.personAt.get(e.person)!;
        const to = layout.projectAt.get(e.project_id)!;
        const offset = (i - (group.length - 1) / 2) * 9;
        const { mx, my } = bezier(
          from.x + PERSON_W,
          from.y + PERSON_H / 2 + offset,
          to.x,
          to.y + PROJECT_H / 2 + offset,
        );
        const label = `${t.dyn("agent", e.agent, AGENT_NAME[e.agent])} · ${e.commits}`;
        const w = label.length * 6.6 + 30 + (e.telemetry?.cost_usd ? 46 : 0);
        return { id: `${e.person}|${e.project_id}|${e.agent}`, x: mx, y: my, w };
      }),
    );
    items.sort((a, b) => a.y - b.y);
    for (const item of items) {
      let y = item.y;
      for (let guard = 0; guard < 40; guard += 1) {
        const hit = placed.find(
          (p) => Math.abs(p.x - item.x) < (p.w + item.w) / 2 + 4 && Math.abs(p.y - y) < 25,
        );
        if (!hit) break;
        y = hit.y + 25;
      }
      placed.push({ x: item.x, y, w: item.w });
      chipAt.set(item.id, { x: item.x, y });
    }
  }
  const top8 = new Set(
    [...edges].sort((a, b) => b.commits - a.commits).slice(0, 8).map((e) => `${e.person}|${e.project_id}|${e.agent}`),
  );

  return (
    <div className="relative" style={{ height: layout.height }} data-testid="dev-map">
      <svg
        aria-hidden
        className="absolute inset-0 overflow-visible"
        width={width}
        height={layout.height}
      >
        <defs>
          {Object.entries(AGENT_COLOUR).map(([agent, colour]) => (
            <marker
              key={agent}
              id={`dev-arrow-${agent}`}
              viewBox="0 0 10 10"
              refX="8"
              refY="5"
              markerUnits="userSpaceOnUse"
              markerWidth="11"
              markerHeight="11"
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" fill={colour} />
            </marker>
          ))}
        </defs>
        {/* project → service */}
        {layout.serviceAt.map(({ s, x, y }) => {
          const from = layout.projectAt.get(s.project.id);
          if (!from) return null;
          const maxS = Math.max(1, ...map.services.map((v) => v.commits));
          const { path } = bezier(from.x + PROJECT_W, from.y + PROJECT_H / 2, x, y + SERVICE_H / 2);
          const on = projectLit(s.project.id);
          return (
            <path
              key={`${s.project.id}-${s.key}`}
              d={path}
              fill="none"
              stroke="var(--border-strong)"
              strokeWidth={1 + 5 * Math.sqrt(s.commits / maxS)}
              strokeLinecap="round"
              opacity={on ? 0.75 : 0.12}
              className="transition-opacity duration-200"
            />
          );
        })}
        {/* person → project, one per agent */}
        {[...pairs.values()].flatMap((group) =>
          group.map((e, i) => {
            const from = layout.personAt.get(e.person)!;
            const to = layout.projectAt.get(e.project_id)!;
            const offset = (i - (group.length - 1) / 2) * 9;
            const { path } = bezier(
              from.x + PERSON_W,
              from.y + PERSON_H / 2 + offset,
              to.x - 4,
              to.y + PROJECT_H / 2 + offset,
            );
            const on = lit(e.person, e.project_id, e.agent);
            const width_ = e.commits
              ? 1.75 + 9 * Math.sqrt(e.commits / maxCommits)
              : 2;
            return (
              <path
                key={`${e.person}-${e.project_id}-${e.agent}`}
                d={path}
                fill="none"
                stroke={AGENT_COLOUR[e.agent]}
                strokeWidth={width_}
                strokeLinecap="round"
                strokeDasharray={e.commits ? undefined : "5 6"}
                markerEnd={`url(#dev-arrow-${e.agent})`}
                opacity={on ? 0.85 : 0.08}
                className="transition-opacity duration-200"
              />
            );
          }),
        )}
      </svg>

      {/* Arrow chips: what the arrow is, and how much. */}
      {[...pairs.values()].flatMap((group) =>
        group.map((e) => {
          const id = `${e.person}|${e.project_id}|${e.agent}`;
          const on = lit(e.person, e.project_id, e.agent);
          const visible = focus ? on : showChips || top8.has(id);
          if (!visible) return null;
          const { x: mx, y: my } = chipAt.get(id) ?? { x: 0, y: 0 };
          const cost = e.telemetry?.cost_usd;
          return (
            <button
              key={`chip-${id}`}
              type="button"
              onMouseEnter={() => setHover({ kind: "edge", person: e.person, project: e.project_id, agent: e.agent })}
              onMouseLeave={() => setHover(null)}
              onClick={() => onPick({ kind: "edge", person: e.person, project: e.project_id, agent: e.agent })}
              data-testid={`dev-edge-${e.person}-${e.agent}`}
              className="absolute z-10 inline-flex -translate-x-1/2 -translate-y-1/2 items-center gap-1.5 rounded-full border bg-surface px-2 py-0.5 text-[11px] font-semibold whitespace-nowrap text-ink shadow-[0_4px_14px_-6px_rgb(0_0_0/0.35)] transition-transform hover:scale-105"
              style={{
                left: mx,
                top: my,
                borderColor: `color-mix(in srgb, ${AGENT_COLOUR[e.agent]} 55%, transparent)`,
              }}
            >
              <span aria-hidden className="h-2 w-2 rounded-full" style={{ background: AGENT_COLOUR[e.agent] }} />
              {t("map.edge", { agent: t.dyn("agent", e.agent, AGENT_NAME[e.agent]), count: e.commits })}
              {cost ? <span className="font-mono text-ink-muted">· {usd(cost, locale)}</span> : null}
            </button>
          );
        }),
      )}

      {/* People */}
      {layout.people.map((p) => {
        const at = layout.personAt.get(p.key)!;
        const on = personLit(p.key);
        return (
          <button
            key={p.key}
            type="button"
            onMouseEnter={() => setHover({ kind: "person", key: p.key })}
            onMouseLeave={() => setHover(null)}
            onClick={() => onPick({ kind: "person", key: p.key })}
            data-testid={`dev-person-${p.key}`}
            className={`absolute flex items-center gap-3 rounded-2xl border bg-surface px-3 text-left transition-[opacity,box-shadow,border-color] duration-200 hover:shadow-[0_12px_30px_-18px_rgb(0_0_0/0.5)] ${
              picked?.kind === "person" && picked.key === p.key
                ? "border-brand-accent/70 ring-1 ring-brand-accent/40"
                : "border-border hover:border-border-strong"
            } ${on ? "" : "opacity-30"}`}
            style={{ left: at.x, top: at.y, width: PERSON_W, height: PERSON_H }}
          >
            <Avatar person={p} />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[0.875rem] font-semibold text-ink">
                {p.name}
                {!p.linked ? (
                  <span className="ml-1.5 rounded-full bg-surface-2 px-1.5 py-px text-[10px] font-medium text-ink-muted">
                    {t("map.unlinked")}
                  </span>
                ) : null}
              </span>
              <span className="mt-1 flex flex-wrap items-center gap-1">
                {p.tools.slice(0, 3).map((tool) => (
                  <span
                    key={tool}
                    className="inline-flex items-center gap-1 rounded-full px-1.5 py-px text-[10px] font-semibold"
                    style={{
                      color: AGENT_COLOUR[tool],
                      background: `color-mix(in srgb, ${AGENT_COLOUR[tool]} 13%, transparent)`,
                    }}
                  >
                    {t.dyn("agent", tool, AGENT_NAME[tool])}
                  </span>
                ))}
              </span>
            </span>
            <span className="text-right">
              <span data-tabular className="block font-mono text-[0.8125rem] font-semibold text-ink">
                {p.commits}
              </span>
              {p.telemetry ? (
                <span data-tabular className="block font-mono text-[10.5px] text-ink-muted">
                  {usd(p.telemetry.cost_usd, locale)}
                </span>
              ) : null}
            </span>
          </button>
        );
      })}

      {/* Projects */}
      {layout.projects.map((project) => {
        const at = layout.projectAt.get(project.id)!;
        const on = projectLit(project.id);
        const share = project.commits ? project.agent_commits / project.commits : 0;
        return (
          <button
            key={project.id}
            type="button"
            onMouseEnter={() => setHover({ kind: "project", id: project.id })}
            onMouseLeave={() => setHover(null)}
            onClick={() => onPick({ kind: "project", id: project.id })}
            data-testid={`dev-project-${project.key}`}
            className={`absolute flex flex-col justify-center gap-2 rounded-2xl border bg-surface px-3.5 text-left transition-[opacity,box-shadow,border-color] duration-200 hover:shadow-[0_14px_34px_-20px_rgb(0_0_0/0.5)] ${
              picked?.kind === "project" && picked.id === project.id
                ? "border-brand-accent/70 ring-1 ring-brand-accent/40"
                : "border-border hover:border-border-strong"
            } ${on ? "" : "opacity-30"}`}
            style={{ left: at.x, top: at.y, width: PROJECT_W, height: PROJECT_H }}
          >
            <span className="flex items-center gap-2.5">
              <span className="grid h-9 w-9 shrink-0 place-items-center rounded-[0.7rem] border border-border bg-surface-2">
                <ProjectMark id={project.id} projectKey={project.key} name={project.name} />
              </span>
              <span className="min-w-0">
                <span className="block truncate text-[0.9375rem] font-semibold text-ink">{project.name}</span>
                <span className="block truncate font-mono text-[10.5px] text-ink-muted">{project.repository}</span>
              </span>
            </span>
            <span className="flex items-center gap-2 text-[11px] text-ink-secondary">
              <span data-tabular>{t("map.commits", { count: project.commits })}</span>
              <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-3">
                <span
                  className="block h-full rounded-full"
                  style={{ width: `${share * 100}%`, background: AGENT_COLOUR.claude_code }}
                />
              </span>
            </span>
          </button>
        );
      })}

      {/* Services */}
      {layout.serviceAt.map(({ s, x, y }) => (
        <button
          key={`${s.project.id}-${s.key}`}
          type="button"
          onMouseEnter={() => setHover({ kind: "service", project: s.project.id, key: s.key })}
          onMouseLeave={() => setHover(null)}
          onClick={() => onPick({ kind: "service", project: s.project.id, key: s.key })}
          className={`absolute flex items-center justify-between gap-2 rounded-full border border-border bg-surface-2 px-3 text-left text-[12px] transition-opacity duration-200 hover:border-border-strong ${
            projectLit(s.project.id) ? "" : "opacity-30"
          }`}
          style={{ left: x, top: y, width: SERVICE_W, height: SERVICE_H }}
        >
          <span className={`truncate font-mono ${s.key ? "text-ink" : "text-ink-muted italic"}`}>
            {s.key ?? t("map.repoLevel")}
          </span>
          <span data-tabular className="font-mono text-[11px] text-ink-muted">
            {s.commits}
            {s.more ? <span className="ml-1">{t("map.more", { count: s.more })}</span> : null}
          </span>
        </button>
      ))}

      {layout.hidden > 0 ? (
        <span className="absolute bottom-0 left-0 text-micro text-ink-muted">
          {t("map.more", { count: layout.hidden })}
        </span>
      ) : null}

      {/* Column labels */}
      {(
        [
          [t("map.people"), 0],
          [t("map.projects"), Math.round(width * 0.56 - PROJECT_W / 2)],
          [t("map.services"), width - SERVICE_W],
        ] as const
      ).map(([label, x]) => (
        <span
          key={label}
          className="absolute top-0 font-mono text-[10.5px] font-medium tracking-[0.14em] text-ink-muted uppercase"
          style={{ left: x }}
        >
          {label}
        </span>
      ))}
      <span className="sr-only">{compact(map.summary.commits, locale)}</span>
    </div>
  );
}
