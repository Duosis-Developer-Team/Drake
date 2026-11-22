"use client";

/**
 * The development map: people → the repositories they changed → the
 * catalog services under them. A catalogued repository wears its project
 * (logo, name) and has services; any other repository of the watched GitHub
 * organisations is shown by its own name, and has none. Cards are HTML
 * (readable, focusable, clickable); the arrows between them are one SVG
 * underneath, in the same coordinates.
 *
 * An arrow is one coding agent between one person and one repository: its
 * colour is the agent, its width the commits, its chip says both. An arrow
 * with Claude Code telemetry but no commits in the window is dashed — the
 * person worked there, nothing landed yet.
 */

import { FolderGit2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

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
  type DevRepository,
} from "@/lib/devAgents";

import { avatarBackdrop, crispSize, loadAvatar, mayHaveAvatar, settledAvatar } from "./pixelAvatar";

export type Pick =
  | { kind: "person"; key: string }
  | { kind: "repository"; id: string }
  | { kind: "edge"; person: string; repository: string; agent: CodingAgent }
  | { kind: "service"; repository: string; key: string | null };

const PERSON_W = 236;
const PERSON_H = 68;
const PERSON_GAP = 14;
const PROJECT_W = 244;
const PROJECT_H = 86;
/** A repository outside the catalog: no project, so a lighter card. */
const REPO_H = 70;
const PROJECT_GAP = 22;
const SERVICE_W = 196;
const SERVICE_H = 34;
const SERVICE_GAP = 8;
const GROUP_GAP = 18;
const MAX_PEOPLE = 14;
const MAX_SERVICES = 4;
const MAX_REPOSITORIES = 18;

const cardHeight = (repo: DevRepository) => (repo.catalogued ? PROJECT_H : REPO_H);

/**
 * A person: an arcade character coloured from their GitHub picture when
 * Tracesis has one, their initials otherwise. The tile is a rounded square
 * with a stepped "pixel" border in the person's colour; nothing moves.
 */
export function Avatar({ person, size = 36 }: { person: Pick_ | DevPerson; size?: number }) {
  const colour = personColour(person.key);
  const wanted = mayHaveAvatar(person.key, "bot" in person ? person.bot : false);
  const [pixels, setPixels] = useState<string | null>(() => (wanted ? (settledAvatar(person.key) ?? null) : null));
  useEffect(() => {
    if (!wanted) return;
    let active = true;
    void loadAvatar(person.key).then((url) => {
      if (active) setPixels(url);
    });
    return () => {
      active = false;
    };
  }, [person.key, wanted]);
  if (wanted && pixels) {
    const step = Math.max(1, Math.round(size / 18));
    const sprite = crispSize(size, typeof window === "undefined" ? 1 : window.devicePixelRatio || 1);
    return (
      <span
        aria-hidden
        data-testid={`dev-avatar-pixel-${person.key}`}
        className="relative grid shrink-0 place-items-center overflow-hidden"
        style={{
          width: size,
          height: size,
          borderRadius: Math.round(size * 0.22),
          background: avatarBackdrop(person.key) ?? "var(--surface-2)",
          // A stepped border: the surface, then the person's colour, then a
          // darker edge — read as a sprite frame rather than a photo ring.
          boxShadow: `0 0 0 ${step}px var(--surface-1), 0 0 0 ${step * 2}px ${colour}, 0 0 0 ${step * 3}px color-mix(in srgb, ${colour} 45%, black)`,
        }}
      >
        <img
          src={pixels}
          alt=""
          width={sprite}
          height={sprite}
          draggable={false}
          className="block"
          style={{ width: sprite, height: sprite, imageRendering: "pixelated" }}
        />
      </span>
    );
  }
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

/** A repository's mark: its project's logo when catalogued, a plain repository icon otherwise. */
export function RepositoryMark({ repo, size = "md" }: { repo: DevRepository; size?: "sm" | "md" | "lg" }) {
  if (repo.catalogued && repo.project_id && repo.project_key) {
    return (
      <ProjectMark id={repo.project_id} projectKey={repo.project_key} name={repo.project_name ?? repo.name} size={size} />
    );
  }
  const px = size === "sm" ? 13 : size === "lg" ? 22 : 17;
  return (
    <FolderGit2
      aria-hidden
      className="text-ink-muted"
      style={{ width: px, height: px }}
      strokeWidth={1.75}
      data-testid={`dev-repo-icon-${repo.name}`}
    />
  );
}

/** What a repository is called: its project's name when catalogued, its own otherwise. */
export const repositoryTitle = (repo: DevRepository) =>
  repo.catalogued && repo.project_name ? repo.project_name : repo.name;

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
    const repositories = map.repositories.slice(0, MAX_REPOSITORIES);
    const services: { repo: DevRepository; key: string | null; commits: number; more: number }[] = [];
    for (const repo of repositories) {
      if (!repo.catalogued) continue; // services are the catalog's
      const own = map.services
        .filter((s) => s.repository === repo.id)
        .sort((a, b) => b.commits - a.commits);
      own.slice(0, MAX_SERVICES).forEach((s, i) =>
        services.push({
          repo,
          key: s.key,
          commits: s.commits,
          more: i === MAX_SERVICES - 1 ? Math.max(0, own.length - MAX_SERVICES) : 0,
        }),
      );
    }
    const peopleH = people.length * PERSON_H + Math.max(0, people.length - 1) * PERSON_GAP;
    const reposH =
      repositories.reduce((h, r) => h + cardHeight(r), 0) +
      Math.max(0, repositories.length - 1) * PROJECT_GAP;
    let servicesH = 0;
    let previous: string | null = null;
    for (const s of services) {
      servicesH += (servicesH ? (s.repo.id === previous ? SERVICE_GAP : GROUP_GAP) : 0) + SERVICE_H;
      previous = s.repo.id;
    }
    const height = Math.max(peopleH, reposH, servicesH) + 48;
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
    const projectAt = new Map<string, { x: number; y: number; h: number }>();
    let ry = top(reposH);
    for (const r of repositories) {
      projectAt.set(r.id, { x: cols.project, y: ry, h: cardHeight(r) });
      ry += cardHeight(r) + PROJECT_GAP;
    }
    const serviceAt: { s: (typeof services)[number]; x: number; y: number }[] = [];
    let y = top(servicesH);
    previous = null;
    services.forEach((s, i) => {
      if (i) y += s.repo.id === previous ? SERVICE_GAP : GROUP_GAP;
      serviceAt.push({ s, x: cols.service, y });
      y += SERVICE_H;
      previous = s.repo.id;
    });
    return {
      people,
      repositories,
      personAt,
      projectAt,
      serviceAt,
      height,
      hidden: map.people.length - people.length,
      hiddenRepos: map.repositories.length - repositories.length,
    };
  }, [map, width]);

  const maxCommits = Math.max(1, ...map.edges.map((e) => e.commits));
  const edges = map.edges.filter((e) => layout.personAt.has(e.person) && layout.projectAt.has(e.repository));
  const pairs = new Map<string, DevEdge[]>();
  for (const e of edges) {
    const k = `${e.person}|${e.repository}`;
    pairs.set(k, [...(pairs.get(k) ?? []), e]);
  }

  const focus = hover ?? picked;
  const lit = (person: string, repository: string, agent?: string) => {
    if (!focus) return true;
    if (focus.kind === "person") return focus.key === person;
    if (focus.kind === "repository") return focus.id === repository;
    if (focus.kind === "edge")
      return focus.person === person && focus.repository === repository && (!agent || focus.agent === agent);
    return focus.repository === repository;
  };
  const personLit = (key: string) =>
    !focus ||
    (focus.kind === "person" && focus.key === key) ||
    ((focus.kind === "repository" || focus.kind === "service") &&
      edges.some((e) => e.person === key && e.repository === (focus.kind === "repository" ? focus.id : focus.repository))) ||
    (focus.kind === "edge" && focus.person === key);
  const projectLit = (id: string) =>
    !focus ||
    (focus.kind === "repository" && focus.id === id) ||
    (focus.kind === "service" && focus.repository === id) ||
    (focus.kind === "edge" && focus.repository === id) ||
    (focus.kind === "person" && edges.some((e) => e.person === focus.key && e.repository === id));

  const showChips = edges.length <= 16;
  // Chips sit on their arrow's midpoint, then step down until no two touch:
  // arrows from one person fan out and their midpoints crowd together.
  const chipAt = new Map<string, { x: number; y: number }>();
  {
    const placed: { x: number; y: number; w: number }[] = [];
    const items = [...pairs.values()].flatMap((group) =>
      group.map((e, i) => {
        const from = layout.personAt.get(e.person)!;
        const to = layout.projectAt.get(e.repository)!;
        const offset = (i - (group.length - 1) / 2) * 9;
        const { mx, my } = bezier(
          from.x + PERSON_W,
          from.y + PERSON_H / 2 + offset,
          to.x,
          to.y + to.h / 2 + offset,
        );
        const label = `${t.dyn("agent", e.agent, AGENT_NAME[e.agent])} · ${e.commits}`;
        const w = label.length * 6.6 + 30 + (e.telemetry?.cost_usd ? 46 : 0);
        return { id: `${e.person}|${e.repository}|${e.agent}`, x: mx, y: my, w };
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
    [...edges].sort((a, b) => b.commits - a.commits).slice(0, 8).map((e) => `${e.person}|${e.repository}|${e.agent}`),
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
        {/* repository → service (catalogued only) */}
        {layout.serviceAt.map(({ s, x, y }) => {
          const from = layout.projectAt.get(s.repo.id);
          if (!from) return null;
          const maxS = Math.max(1, ...map.services.map((v) => v.commits));
          const { path } = bezier(from.x + PROJECT_W, from.y + from.h / 2, x, y + SERVICE_H / 2);
          const on = projectLit(s.repo.id);
          return (
            <path
              key={`${s.repo.id}-${s.key}`}
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
        {/* person → repository, one per agent */}
        {[...pairs.values()].flatMap((group) =>
          group.map((e, i) => {
            const from = layout.personAt.get(e.person)!;
            const to = layout.projectAt.get(e.repository)!;
            const offset = (i - (group.length - 1) / 2) * 9;
            const { path } = bezier(
              from.x + PERSON_W,
              from.y + PERSON_H / 2 + offset,
              to.x - 4,
              to.y + to.h / 2 + offset,
            );
            const on = lit(e.person, e.repository, e.agent);
            const width_ = e.commits
              ? 1.75 + 9 * Math.sqrt(e.commits / maxCommits)
              : 2;
            return (
              <path
                key={`${e.person}-${e.repository}-${e.agent}`}
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
          const id = `${e.person}|${e.repository}|${e.agent}`;
          const on = lit(e.person, e.repository, e.agent);
          const visible = focus ? on : showChips || top8.has(id);
          if (!visible) return null;
          const { x: mx, y: my } = chipAt.get(id) ?? { x: 0, y: 0 };
          const cost = e.telemetry?.cost_usd;
          return (
            <button
              key={`chip-${id}`}
              type="button"
              onMouseEnter={() => setHover({ kind: "edge", person: e.person, repository: e.repository, agent: e.agent })}
              onMouseLeave={() => setHover(null)}
              onClick={() => onPick({ kind: "edge", person: e.person, repository: e.repository, agent: e.agent })}
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

      {/* Repositories: the catalog's wear their project; the others their own name. */}
      {layout.repositories.map((repo) => {
        const at = layout.projectAt.get(repo.id)!;
        const on = projectLit(repo.id);
        const share = repo.commits ? repo.agent_commits / repo.commits : 0;
        const owner = repo.full_name.split("/")[0];
        return (
          <button
            key={repo.id}
            type="button"
            onMouseEnter={() => setHover({ kind: "repository", id: repo.id })}
            onMouseLeave={() => setHover(null)}
            onClick={() => onPick({ kind: "repository", id: repo.id })}
            data-testid={`dev-repo-${repo.project_key ?? repo.name}`}
            data-catalogued={repo.catalogued ? "true" : "false"}
            className={`absolute flex flex-col justify-center gap-2 rounded-2xl border px-3.5 text-left transition-[opacity,box-shadow,border-color] duration-200 hover:shadow-[0_14px_34px_-20px_rgb(0_0_0/0.5)] ${
              repo.catalogued ? "bg-surface" : "bg-surface-2/60"
            } ${
              picked?.kind === "repository" && picked.id === repo.id
                ? "border-brand-accent/70 ring-1 ring-brand-accent/40"
                : repo.catalogued
                  ? "border-border hover:border-border-strong"
                  : "border-dashed border-border hover:border-border-strong"
            } ${on ? "" : "opacity-30"}`}
            style={{ left: at.x, top: at.y, width: PROJECT_W, height: at.h }}
          >
            <span className="flex items-center gap-2.5">
              <span
                className={`grid shrink-0 place-items-center border border-border ${
                  repo.catalogued ? "h-9 w-9 rounded-[0.7rem] bg-surface-2" : "h-7 w-7 rounded-[0.55rem] bg-surface"
                }`}
              >
                <RepositoryMark repo={repo} />
              </span>
              <span className="min-w-0 flex-1">
                <span
                  className={`block truncate font-semibold text-ink ${
                    repo.catalogued ? "text-[0.9375rem]" : "font-mono text-[0.8125rem]"
                  }`}
                >
                  {repositoryTitle(repo)}
                </span>
                {repo.catalogued ? (
                  <span className="block truncate font-mono text-[10.5px] text-ink-muted">{repo.full_name}</span>
                ) : (
                  <span className="flex min-w-0 items-center gap-1.5 text-[10.5px] text-ink-muted">
                    <span className="truncate font-mono">{owner}</span>
                    <span className="shrink-0 rounded-full border border-border px-1.5 leading-[15px]">
                      {t("map.notCatalogued")}
                    </span>
                  </span>
                )}
              </span>
            </span>
            <span className="flex items-center gap-2 text-[11px] text-ink-secondary">
              <span data-tabular>{t("map.commits", { count: repo.commits })}</span>
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
          key={`${s.repo.id}-${s.key}`}
          type="button"
          onMouseEnter={() => setHover({ kind: "service", repository: s.repo.id, key: s.key })}
          onMouseLeave={() => setHover(null)}
          onClick={() => onPick({ kind: "service", repository: s.repo.id, key: s.key })}
          className={`absolute flex items-center justify-between gap-2 rounded-full border border-border bg-surface-2 px-3 text-left text-[12px] transition-opacity duration-200 hover:border-border-strong ${
            projectLit(s.repo.id) ? "" : "opacity-30"
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
      {layout.hiddenRepos > 0 ? (
        <span
          className="absolute bottom-0 text-micro text-ink-muted"
          style={{ left: Math.round(width * 0.56 - PROJECT_W / 2) }}
        >
          {t("map.more", { count: layout.hiddenRepos })}
        </span>
      ) : null}

      {/* Column labels */}
      {(
        [
          [t("map.people"), 0],
          [t("map.repositories"), Math.round(width * 0.56 - PROJECT_W / 2)],
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
