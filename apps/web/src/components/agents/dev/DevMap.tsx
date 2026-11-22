"use client";

/**
 * The development map: people → the repositories they changed → the
 * catalog services under them. A catalogued repository wears its project
 * (logo, name) and has services; any other repository of the watched GitHub
 * organisations is shown by its own name, and has none. Cards are HTML
 * (readable, focusable, clickable); the arrows between them are one SVG
 * underneath, in the same coordinates.
 *
 * An arrow is one person's work in one repository with one coding agent.
 * Its colour is the person, so one person's work can be followed across the
 * map; it is solid with a coding agent, dashed by hand, and dotted when
 * Claude Code reported sessions but no commit landed in the window. Its
 * width is the commits (kept within a few pixels); its chip names the agent.
 * Every arrow has its own port at both cards, so arrows never merge into a
 * ribbon, and both columns are ordered to cross as little as possible.
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
const PERSON_GAP = 22;
const PROJECT_W = 244;
const PROJECT_H = 86;
/** A repository outside the catalog: no project, so a lighter card. */
const REPO_H = 70;
const PROJECT_GAP = 26;
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

/** Where the repositories' column sits, as a share of the map's width (its centre). */
const REPO_COLUMN = 0.585;
const repoColumn = (width: number) => Math.round(width * REPO_COLUMN - PROJECT_W / 2);
/** Ports keep clear of a card's rounded corners. */
const PORT_PAD = 11;
const PORT_GAP_MIN = 3;
const PORT_GAP_MAX = 9;
const STROKE_MIN = 1.5;
const STROKE_MAX = 4;
const ARROW = 7;
/** Above this many arrows, only the largest carry a chip until something is focused. */
const ALL_CHIPS_UP_TO = 10;
const DEFAULT_CHIPS = 6;
const AGENT_ORDER = Object.keys(AGENT_COLOUR) as CodingAgent[];

/**
 * Stack strokes of the given widths around `centre`, inside a band `band`
 * pixels tall, at least PORT_GAP_MIN apart: every arrow gets its own port, so
 * parallel arrows stay separate lines instead of merging into one ribbon.
 * When they cannot all fit, they are thinned (never under 1px), not overlapped.
 */
export function ports(widths: number[], centre: number, band: number): { y: number; w: number }[] {
  const n = widths.length;
  if (!n) return [];
  let ws = widths.slice();
  let sum = ws.reduce((a, b) => a + b, 0);
  const gap = n > 1 ? Math.min(PORT_GAP_MAX, Math.max(PORT_GAP_MIN, (band - sum) / (n - 1))) : 0;
  if (sum + gap * (n - 1) > band) {
    const room = Math.max(n, band - gap * (n - 1));
    ws = ws.map((w) => Math.max(1, (w * room) / sum));
    sum = ws.reduce((a, b) => a + b, 0);
  }
  let y = centre - (sum + gap * (n - 1)) / 2;
  return ws.map((w) => {
    const at = { y: y + w / 2, w };
    y += w + gap;
    return at;
  });
}

/**
 * Order both columns to cross as little as possible: each repository moves
 * to the weighted average row of the people who worked on it, then each
 * person to the average row of their repositories, a few times over. The
 * catalog's repositories stay above the others; ties go by name.
 */
export function orderByBarycentre(
  people: DevPerson[],
  repositories: DevRepository[],
  edges: { person: string; repository: string; commits: number }[],
  sweeps = 3,
): { people: DevPerson[]; repositories: DevRepository[] } {
  const weight = new Map<string, { person: string; repository: string; w: number }>();
  for (const e of edges) {
    const k = `${e.person}|${e.repository}`;
    const cur = weight.get(k);
    if (cur) cur.w += Math.max(1, e.commits);
    else weight.set(k, { person: e.person, repository: e.repository, w: Math.max(1, e.commits) });
  }
  const pairs = [...weight.values()];
  const bary = (key: string, side: "person" | "repository", rank: Map<string, number>, fallback: number) => {
    let sum = 0;
    let total = 0;
    for (const pair of pairs) {
      if ((side === "person" ? pair.person : pair.repository) !== key) continue;
      const at = rank.get(side === "person" ? pair.repository : pair.person);
      if (at === undefined) continue;
      sum += pair.w * at;
      total += pair.w;
    }
    return total ? sum / total : fallback;
  };
  const byName = (a: string, b: string) => a.localeCompare(b);
  let ps = people.slice();
  let rs = repositories.slice();
  for (let sweep = 0; sweep < sweeps; sweep += 1) {
    const personRank = new Map(ps.map((p, i) => [p.key, i / Math.max(1, ps.length - 1)]));
    const rb = new Map(rs.map((r, i) => [r.id, bary(r.id, "repository", personRank, 2 + i / rs.length)]));
    rs = rs
      .slice()
      .sort(
        (a, b) =>
          Number(b.catalogued) - Number(a.catalogued) ||
          rb.get(a.id)! - rb.get(b.id)! ||
          byName(repositoryTitle(a), repositoryTitle(b)),
      );
    const repoRank = new Map(rs.map((r, i) => [r.id, i / Math.max(1, rs.length - 1)]));
    const pb = new Map(ps.map((p, i) => [p.key, bary(p.key, "person", repoRank, 2 + i / ps.length)]));
    ps = ps.slice().sort((a, b) => pb.get(a.key)! - pb.get(b.key)! || byName(a.name, b.name));
  }
  return { people: ps, repositories: rs };
}

function curve(x1: number, y1: number, x2: number, y2: number) {
  // Control points stay level with the ends, so an arrow leaves and enters straight.
  const dx = (x2 - x1) * 0.5;
  const path = `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;
  const at = (t: number) => {
    const u = 1 - t;
    return {
      x: u * u * u * x1 + 3 * u * u * t * (x1 + dx) + 3 * u * t * t * (x2 - dx) + t * t * t * x2,
      y: (u * u * u + 3 * u * u * t) * y1 + (3 * u * t * t + t * t * t) * y2,
    };
  };
  return { path, at };
}

const edgeId = (e: { person: string; repository: string; agent: CodingAgent }) =>
  `${e.person}|${e.repository}|${e.agent}`;

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
  // Keyboard focus lights things exactly like the pointer does.
  const follow = (pick: Pick) => ({
    onMouseEnter: () => setHover(pick),
    onMouseLeave: () => setHover(null),
    onFocus: () => setHover(pick),
    onBlur: () => setHover(null),
  });

  const layout = useMemo(() => {
    const { people, repositories } = orderByBarycentre(
      map.people.slice(0, MAX_PEOPLE),
      map.repositories.slice(0, MAX_REPOSITORIES),
      map.edges,
    );
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
    const height = Math.max(peopleH, reposH, servicesH) + 56;
    const top = (h: number) => 28 + (height - 28 - h) / 2;
    const cols = { person: 0, project: repoColumn(width), service: width - SERVICE_W };
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

    // Every arrow gets its own port at both ends: a person's arrows leave in
    // the order of where they land, a repository's arrive in the order of
    // where they come from, so no two cross at a card and none overlap.
    const maxCommits = Math.max(1, ...map.edges.map((e) => e.commits));
    const edges = map.edges.filter((e) => personAt.has(e.person) && projectAt.has(e.repository));
    const strokeOf = (e: DevEdge) =>
      e.commits ? STROKE_MIN + (STROKE_MAX - STROKE_MIN) * Math.sqrt(e.commits / maxCommits) : STROKE_MIN;
    const agentRank = (a: CodingAgent) => AGENT_ORDER.indexOf(a);
    const repoMid = (id: string) => projectAt.get(id)!.y + projectAt.get(id)!.h / 2;
    const start = new Map<string, { y: number; w: number }>();
    for (const p of people) {
      const own = edges
        .filter((e) => e.person === p.key)
        .sort((a, b) => repoMid(a.repository) - repoMid(b.repository) || agentRank(a.agent) - agentRank(b.agent));
      const at = personAt.get(p.key)!;
      ports(own.map(strokeOf), at.y + PERSON_H / 2, PERSON_H - 2 * PORT_PAD).forEach((port, i) =>
        start.set(edgeId(own[i]), port),
      );
    }
    const end = new Map<string, { y: number; w: number }>();
    for (const r of repositories) {
      const own = edges
        .filter((e) => e.repository === r.id)
        .sort((a, b) => start.get(edgeId(a))!.y - start.get(edgeId(b))!.y);
      const at = projectAt.get(r.id)!;
      ports(
        own.map((e) => start.get(edgeId(e))!.w),
        at.y + at.h / 2,
        at.h - 2 * PORT_PAD,
      ).forEach((port, i) => end.set(edgeId(own[i]), port));
    }
    const arrows = edges.map((e) => {
      const from = start.get(edgeId(e))!;
      const to = end.get(edgeId(e))!;
      return {
        e,
        w: Math.min(from.w, to.w),
        ...curve(cols.person + PERSON_W, from.y, cols.project - ARROW, to.y),
      };
    });

    const maxS = Math.max(1, ...map.services.map((v) => v.commits));
    const links: { id: string; repo: string; w: number; path: string }[] = [];
    for (const r of repositories) {
      const own = serviceAt.filter((v) => v.s.repo.id === r.id);
      if (!own.length) continue;
      const at = projectAt.get(r.id)!;
      ports(
        own.map((v) => 1 + 1.5 * Math.sqrt(v.s.commits / maxS)),
        at.y + at.h / 2,
        at.h - 2 * PORT_PAD,
      ).forEach((port, i) => {
        const v = own[i];
        links.push({
          id: `${r.id}-${v.s.key}`,
          repo: r.id,
          w: port.w,
          path: curve(cols.project + PROJECT_W, port.y, v.x, v.y + SERVICE_H / 2).path,
        });
      });
    }
    return {
      people,
      repositories,
      personAt,
      projectAt,
      serviceAt,
      arrows,
      links,
      height,
      hidden: map.people.length - people.length,
      hiddenRepos: map.repositories.length - repositories.length,
    };
  }, [map, width]);

  const edges = layout.arrows.map((a) => a.e);
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

  // Chips: all of them on a small map; on a busy one only the largest few,
  // and every chip of whatever is focused — twenty chips in the gap would
  // hide the very lines they label. Each sits on its own arrow, at the first
  // point along it that touches no other chip.
  const biggest = new Set([...edges].sort((a, b) => b.commits - a.commits).slice(0, DEFAULT_CHIPS).map(edgeId));
  const chipLabel = (e: DevEdge) =>
    t("map.edge", { agent: t.dyn("agent", e.agent, AGENT_NAME[e.agent]), count: e.commits });
  const chips: { e: DevEdge; x: number; y: number }[] = [];
  {
    const placed: { x: number; y: number; w: number }[] = [];
    const clear = (x: number, y: number, w: number) =>
      !placed.some((p) => Math.abs(p.x - x) < (p.w + w) / 2 + 6 && Math.abs(p.y - y) < 26);
    const wanted = layout.arrows
      .filter(({ e }) =>
        focus ? lit(e.person, e.repository, e.agent) : edges.length <= ALL_CHIPS_UP_TO || biggest.has(edgeId(e)),
      )
      .sort((a, b) => b.e.commits - a.e.commits);
    for (const { e, at } of wanted) {
      const w = chipLabel(e).length * 6.6 + 30 + (e.telemetry?.cost_usd ? 46 : 0);
      let spot: { x: number; y: number } | null = null;
      for (const along of [0.5, 0.42, 0.58, 0.34, 0.66, 0.27, 0.73]) {
        const p = at(along);
        if (clear(p.x, p.y, w)) {
          spot = p;
          break;
        }
      }
      if (!spot) {
        const p = at(0.5);
        let y = p.y;
        for (let guard = 0; guard < 30 && !clear(p.x, y, w); guard += 1) y += 26;
        spot = { x: p.x, y };
      }
      placed.push({ ...spot, w });
      chips.push({ e, ...spot });
    }
  }
  const colours = [...new Set(layout.people.map((p) => personColour(p.key)))];

  return (
    <div className="relative" style={{ height: layout.height }} data-testid="dev-map">
      <svg aria-hidden className="absolute inset-0 overflow-visible" width={width} height={layout.height}>
        <defs>
          {colours.map((colour, i) => (
            <marker
              key={colour}
              id={`dev-arrow-${i}`}
              viewBox="0 0 10 10"
              refX="1"
              refY="5"
              markerUnits="userSpaceOnUse"
              markerWidth={ARROW}
              markerHeight={ARROW}
              orient="auto"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" fill={colour} />
            </marker>
          ))}
        </defs>
        {/* repository → service (catalogued only): quiet and neutral */}
        {layout.links.map((l) => (
          <path
            key={l.id}
            d={l.path}
            fill="none"
            stroke="var(--border-strong)"
            strokeWidth={l.w}
            strokeLinecap="round"
            opacity={projectLit(l.repo) ? 0.7 : 0.12}
            className="transition-opacity duration-200"
          />
        ))}
        {/* person → repository, in the person's colour */}
        {layout.arrows.map(({ e, w, path }) => {
          const colour = personColour(e.person);
          const on = lit(e.person, e.repository, e.agent);
          const pick: Pick = { kind: "edge", person: e.person, repository: e.repository, agent: e.agent };
          return (
            <g key={edgeId(e)}>
              <path
                d={path}
                fill="none"
                stroke={colour}
                strokeWidth={w}
                strokeLinecap={e.commits ? "butt" : "round"}
                strokeDasharray={!e.commits ? "0.5 5" : e.agent === "none" ? "7 5" : undefined}
                markerEnd={`url(#dev-arrow-${colours.indexOf(colour)})`}
                opacity={on ? 0.92 : 0.16}
                className="transition-opacity duration-200"
                data-testid={`dev-arrow-${e.person}-${e.agent}`}
              />
              {/* A wider invisible stroke: easier to point at than the line itself. */}
              <path
                d={path}
                fill="none"
                stroke="transparent"
                strokeWidth={Math.max(9, w + 6)}
                pointerEvents="stroke"
                className="cursor-pointer"
                onMouseEnter={() => setHover(pick)}
                onMouseLeave={() => setHover(null)}
                onClick={() => onPick(pick)}
              />
            </g>
          );
        })}
      </svg>

      {/* Arrow chips: which agent, how many commits, what it cost. */}
      {chips.map(({ e, x, y }) => {
        const cost = e.telemetry?.cost_usd;
        const pick: Pick = { kind: "edge", person: e.person, repository: e.repository, agent: e.agent };
        return (
          <button
            key={`chip-${edgeId(e)}`}
            type="button"
            {...follow(pick)}
            onClick={() => onPick(pick)}
            data-testid={`dev-edge-${e.person}-${e.agent}`}
            className="absolute z-10 inline-flex -translate-x-1/2 -translate-y-1/2 items-center gap-1.5 rounded-full border bg-surface px-2 py-0.5 text-[11px] font-semibold whitespace-nowrap text-ink shadow-[0_4px_14px_-6px_rgb(0_0_0/0.35)] transition-transform hover:scale-105"
            style={{
              left: x,
              top: y,
              borderColor: `color-mix(in srgb, ${personColour(e.person)} 60%, transparent)`,
            }}
          >
            <span aria-hidden className="h-2 w-2 rounded-full" style={{ background: AGENT_COLOUR[e.agent] }} />
            {chipLabel(e)}
            {cost ? <span className="font-mono text-ink-muted">· {usd(cost, locale)}</span> : null}
          </button>
        );
      })}

      {/* People */}
      {layout.people.map((p) => {
        const at = layout.personAt.get(p.key)!;
        const on = personLit(p.key);
        return (
          <button
            key={p.key}
            type="button"
            {...follow({ kind: "person", key: p.key })}
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
            {...follow({ kind: "repository", id: repo.id })}
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
          {...follow({ kind: "service", repository: s.repo.id, key: s.key })}
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
          style={{ left: repoColumn(width) }}
        >
          {t("map.more", { count: layout.hiddenRepos })}
        </span>
      ) : null}

      {/* Column labels */}
      {(
        [
          [t("map.people"), 0],
          [t("map.repositories"), repoColumn(width)],
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
