"use client";

/**
 * Agent Visibility → Office: the people who change the repositories as a
 * small pixel office. Product rooms around the cluster's server room, the
 * lobby at the bottom, the street outside; live, whoever's Claude Code is
 * working sits at their desk; a replayed day walks each commit's author to
 * their desk and carries each delivery to the cluster.
 *
 * A toolbar row above the stage and, playing a day, the day's bar below it:
 * nothing covers the office. The scene is drawn straight onto a canvas
 * through a camera whose zoom is a whole number of device pixels per art
 * pixel (camera.ts), so every pixel stays crisp. Clicking a room steps into
 * it; the arrows (or ← →) walk on to the next. Wall screens are HTML under
 * the canvas; the text alternative lists who is where.
 */

import { ChevronLeft, ChevronRight, Expand, FolderGit2, Minus, Pause, Play, Plug, Plus, Scan, Shrink } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { isDuosis } from "@/components/clusters/ClusterCard";
import { TracesisMark, TracesisWordmark } from "@/components/shell/Brand";
import { DataState } from "@/components/state/DataState";
import { Button, SegmentedControl } from "@/components/ui/controls";
import { ProjectLogo } from "@/components/ui/ProjectLogo";
import { useT } from "@/lib/i18n";
import {
  fetchDevOffice,
  initials,
  personColour,
  type DevOffice as OfficeData,
  type DevRepository,
  type DevWindow,
} from "@/lib/devAgents";

import { RepositoryMark, type Pick } from "../DevMap";
import { isPortrait, loadAvatar, mayHaveAvatar } from "../pixelAvatar";
import { headFromInitials, headFromPortrait, headFromSprite, loadArt, type Art, type Density } from "./art";
import {
  clampCamera,
  defaultCamera,
  defaultZoom,
  ease,
  fitCamera,
  frameRoom,
  stageZoom,
  tween,
  zoomLevels,
  type Box,
  type Camera,
} from "./camera";
import {
  advance,
  between,
  DAY_MS,
  dayKey,
  isoWithOffset,
  liveIntents,
  localMidnight,
  parseEvents,
  replayIntents,
  SPEEDS,
  type Intent,
  type TimedEvent,
} from "./director";
import { OfficeEngine, sceneBounds } from "./engine";
import { DEFAULT_LAYOUT } from "./layout";
import { layoutOffice, nextRoom, OTHER, roomAt, type Point, type Rect, type Room, type World } from "./world";

export { clampCamera, fitZoom, zoomLevels, type Camera } from "./camera";

type Mode = "live" | "replay";
/** How the camera was last set, so a resize keeps the same kind of view. */
type View = "default" | "fit" | "free" | "room";
const LIVE_POLL_MS = 30_000;
const MIN_HEIGHT = 380;
/** How long stepping into a room (or back out) takes. */
const MOVE_MS = 480;
/** A fresh event shows in the toolbar (or the day's bar) this long. */
const NEWS_MS = 6_000;

interface FeedItem {
  id: number;
  text: string;
  colour: string;
  at: number;
}

function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const query = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    if (!query) return;
    setReduced(query.matches);
    const change = () => setReduced(query.matches);
    query.addEventListener?.("change", change);
    return () => query.removeEventListener?.("change", change);
  }, []);
  return reduced;
}

function signature(intents: Map<string, Intent>): string {
  return [...intents.entries()]
    .map(([k, v]) => `${k}=${v.kind === "work" ? v.room : "lobby"}`)
    .sort()
    .join("|");
}

function clockLabel(ms: number): string {
  const minutes = Math.max(0, Math.min(1439, Math.floor(ms / 60_000)));
  return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
}

/** ProjectLogo's unframed "icon" mark is this many CSS pixels square. */
const ICON_MARK_PX = 56;

/**
 * A room screen's project mark, `height` CSS pixels tall. It is laid out at
 * exactly that size, whatever the logo's own size table says, so it can never
 * spill over the name strip under it.
 */
function ScreenMark({ room, height }: { room: OfficeData["rooms"][number]; height: number }) {
  if (room.catalogued && room.project_id && room.project_key) {
    if (room.project_key === "tracesis") return <TracesisMark height={height} />;
    return (
      <span className="flex shrink-0 items-center justify-center" style={{ width: height, height }}>
        <span className="flex shrink-0" style={{ transform: `scale(${height / ICON_MARK_PX})` }}>
          <ProjectLogo
            projectId={room.project_id}
            name={room.project_name ?? room.name}
            size="icon"
            variant="mark"
          />
        </span>
      </span>
    );
  }
  return <FolderGit2 aria-hidden className="text-[#cfe0ff]" style={{ width: height, height }} strokeWidth={1.75} />;
}

function asRepository(room: OfficeData["rooms"][number]): DevRepository {
  return {
    ...room,
    // Only the mark is drawn from it; the link is not needed here.
    url: "",
    commits: 0,
    agent_commits: 0,
    people: room.desks.length,
    services: 0,
    cost_usd: null,
  };
}

/** A thin divider between groups of the toolbar. */
function Divider() {
  return <span aria-hidden className="mx-0.5 hidden h-5 w-px shrink-0 bg-border sm:block" />;
}

/** An icon button of the toolbar: its name is its tooltip. */
function Tool({
  label,
  icon: Icon,
  onClick,
  testId,
}: {
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  onClick: () => void;
  testId?: string;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      data-testid={testId}
      className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-[0.375rem] text-ink-secondary transition-colors hover:bg-surface-hover hover:text-ink focus-visible:outline-2 focus-visible:outline-brand"
    >
      <Icon className="h-4 w-4" aria-hidden />
    </button>
  );
}

export function DevOffice({
  window: span,
  onWindow,
  onPick,
  onConnect,
  overlay,
  lead,
}: {
  window: DevWindow;
  onWindow: (next: DevWindow) => void;
  onPick: (pick: Pick) => void;
  onConnect: () => void;
  /** The page's own tabs, at the start of the toolbar row. */
  overlay?: React.ReactNode;
  /** What the office is, for the text alternative. */
  lead: string;
}) {
  const t = useT("devOffice");
  const tDev = useT("devAgents");
  const [full, setFull] = useState(false);
  const reduced = usePrefersReducedMotion();
  const [mode, setMode] = useState<Mode>("live");
  const [day, setDay] = useState(() => dayKey(new Date()));
  const [state, setState] = useState<{ key: string; data: OfficeData | null; failed: boolean } | null>(null);
  const [art, setArt] = useState<Art | null>(null);
  const [playing, setPlaying] = useState(true);
  const [speed, setSpeed] = useState<(typeof SPEEDS)[number]>(1);
  const [clock, setClock] = useState(0);
  const [camera, setCamera] = useState<Camera | null>(null);
  const [lift, setLift] = useState(1);
  const [rootH, setRootH] = useState(0);
  const [size, setSize] = useState({ w: 0, h: 0, dpr: 1 });
  const [focusId, setFocusId] = useState<string | null>(null);
  const [captionH, setCaptionH] = useState(0);
  const [now, setNow] = useState(() => Date.now());

  const [places, setPlaces] = useState<ReturnType<OfficeEngine["places"]>>([]);
  const [feed, setFeed] = useState<FeedItem[]>([]);

  const rootRef = useRef<HTMLDivElement | null>(null);
  const stageRef = useRef<HTMLDivElement | null>(null);
  const captionRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const engineRef = useRef<OfficeEngine | null>(null);
  const cameraRef = useRef<Camera | null>(null);
  const viewRef = useRef<View>("default");
  const moveRef = useRef<{ from: Camera; to: Camera; start: number } | null>(null);
  const clockRef = useRef(0);
  const playingRef = useRef(true);
  const speedRef = useRef(1);
  const eventsRef = useRef<TimedEvent[]>([]);
  const dayStartRef = useRef(0);
  const intentSig = useRef("");
  const seenEvents = useRef(new Set<string>());
  const feedId = useRef(0);
  const nameOf = useRef(new Map<string, string>());
  const roomName = useRef(new Map<string, string>());

  cameraRef.current = camera;
  playingRef.current = playing;
  speedRef.current = speed;

  /* ------------------------------------------------------------ data */

  const requestKey = `${mode}|${span}|${mode === "replay" ? day : "today"}`;
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const target = mode === "replay" ? day : dayKey(new Date());
    const load = () => {
      fetchDevOffice(span, isoWithOffset(localMidnight(target)))
        .then((data) => active && setState({ key: requestKey, data, failed: false }))
        .catch(
          () =>
            active &&
            setState((prev) => (prev?.key === requestKey && prev.data ? prev : { key: requestKey, data: null, failed: true })),
        )
        .finally(() => {
          if (active && mode === "live") timer = setTimeout(load, LIVE_POLL_MS);
        });
    };
    load();
    return () => {
      active = false;
      if (timer) clearTimeout(timer);
    };
  }, [requestKey, mode, span, day]);

  useEffect(() => {
    let active = true;
    loadArt()
      .then((loaded) => active && setArt(loaded))
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, []);

  const data = state?.data ?? null;
  const roomsKey = data ? data.rooms.map((r) => `${r.id}:${r.commits ?? 0}:${r.desks.join(",")}`).join(";") : "";
  const world = useMemo<World | null>(
    () =>
      data && art
        ? layoutOffice(
            data.rooms.map((r) => ({ id: r.id, desks: r.desks, catalogued: r.catalogued, commits: r.commits ?? 0 })),
            art.layout ?? DEFAULT_LAYOUT,
          )
        : null,
    // The plan changes with the rooms and their desks only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [roomsKey, art],
  );
  const events = useMemo(() => (data ? parseEvents(data.events) : []), [data]);
  eventsRef.current = events;
  dayStartRef.current = data ? Date.parse(data.day_start) : 0;
  useEffect(() => {
    if (!data) return;
    nameOf.current = new Map(data.people.map((p) => [p.key, p.name]));
    roomName.current = new Map(data.rooms.map((r) => [r.id, r.project_name ?? r.name]));
  }, [data]);
  // The densest character on screen sets the zoom levels (every pixel whole).
  const density: Density = (data && art ? Math.max(1, ...data.people.map((p) => art.bodies.get(p.key)?.density ?? 1)) : 1) as Density;

  /* ------------------------------------------------------------ the engine */

  useEffect(() => {
    if (!world || !art) return;
    engineRef.current = new OfficeEngine(world, art, reduced);
    intentSig.current = "";
    return () => {
      engineRef.current = null;
    };
  }, [world, art, reduced]);

  // People, and a face for anyone without a character of their own.
  useEffect(() => {
    const engine = engineRef.current;
    if (!engine || !data || !art) return;
    engine.setPeople(data.people.map((p) => ({ key: p.key, name: p.name, colour: personColour(p.key) })));
    let active = true;
    for (const p of data.people) {
      if (art.bodies.has(p.key) || engine.hasHead(p.key)) continue;
      const fallback = () => engine.setHead(p.key, headFromInitials(initials(p.name), personColour(p.key)));
      if (!mayHaveAvatar(p.key)) {
        fallback();
        continue;
      }
      void loadAvatar(p.key).then((url) => {
        if (!active || engineRef.current !== engine) return;
        if (!url) return fallback();
        const image = new Image();
        image.onload = () => {
          try {
            engine.setHead(p.key, isPortrait(p.key) ? headFromPortrait(image) : headFromSprite(image));
          } catch {
            fallback();
          }
        };
        image.onerror = fallback;
        image.src = url;
      });
    }
    return () => {
      active = false;
    };
  }, [data, world, art, reduced]);

  const pushFeed = useCallback((text: string, colour: string) => {
    feedId.current += 1;
    const item = { id: feedId.current, text, colour, at: Date.now() };
    setFeed((prev) => [...prev.slice(-3), item]);
  }, []);

  const announce = useCallback(
    (e: TimedEvent) => {
      const engine = engineRef.current;
      if (!engine) return;
      const who = e.person ? (nameOf.current.get(e.person) ?? e.person) : "";
      const room = roomName.current.get(e.repository) ?? e.repository;
      const colour = e.person ? personColour(e.person) : "#d1a345";
      if (e.kind === "commit") {
        engine.say(e.person, e.short ?? "commit", colour);
        pushFeed(t("feed.commit", { person: who, room }), colour);
      } else {
        engine.deliver(e.person, e.repository);
        if (e.person) engine.say(e.person, e.environment ?? "deploy", colour);
        pushFeed(e.person ? t("feed.deployment", { person: who, room }) : t("feed.anonymous", { room }), colour);
      }
    },
    [pushFeed, t],
  );

  // Live: where everyone is, and what is new since the last read.
  useEffect(() => {
    const engine = engineRef.current;
    if (!engine || !data || mode !== "live") return;
    const intents = liveIntents(data.live);
    const sig = signature(intents);
    if (sig !== intentSig.current) {
      engine.setIntents(intents);
      intentSig.current = sig;
    }
    const fresh = events.filter((e) => !seenEvents.current.has(`${e.at}:${e.kind}:${e.short}`));
    const first = seenEvents.current.size === 0;
    for (const e of fresh) seenEvents.current.add(`${e.at}:${e.kind}:${e.short}`);
    // On the first read, the day so far is history, not news.
    if (!first) fresh.forEach(announce);
  }, [data, events, mode, world, art, reduced, announce]);

  // Replay: a new day starts an hour before its first event.
  useEffect(() => {
    if (mode !== "replay" || !data) return;
    const firstAt = events.length ? events[0].at - dayStartRef.current : 9 * 3_600_000;
    const start = Math.max(0, Math.floor((firstAt - 3_600_000) / 3_600_000) * 3_600_000);
    clockRef.current = start;
    setClock(start);
    setPlaying(true);
    setFeed([]);
    intentSig.current = "";
    engineRef.current?.setIntents(replayIntents(events, dayStartRef.current + start), true);
  }, [mode, data, events, world]);

  useEffect(() => {
    seenEvents.current = new Set();
    setFeed([]);
  }, [mode]);

  const seek = (ms: number) => {
    clockRef.current = ms;
    setClock(ms);
    const intents = replayIntents(eventsRef.current, dayStartRef.current + ms);
    intentSig.current = signature(intents);
    engineRef.current?.setIntents(intents, true);
  };

  /* ------------------------------------------------------------ size */

  // The office takes the content area from where it starts to the bottom of
  // the window (the whole screen in full screen): the toolbar, the stage, and
  // the day's bar when playing one. The page never scrolls.
  useEffect(() => {
    const root = rootRef.current;
    const stage = stageRef.current;
    if (!root || !stage) return;
    const measure = () => {
      const dpr = Math.max(1, Math.round((globalThis.devicePixelRatio || 1) * 4) / 4);
      const top = root.getBoundingClientRect().top + (globalThis.scrollY || 0);
      setRootH(Math.max(MIN_HEIGHT, Math.floor((globalThis.innerHeight || 800) - top)));
      const w = Math.floor(stage.clientWidth);
      const h = Math.floor(stage.clientHeight);
      setSize((prev) => (prev.w === w && prev.h === h && prev.dpr === dpr ? prev : { w, h, dpr }));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(stage);
    globalThis.addEventListener?.("resize", measure);
    const onFull = () => {
      setFull(document.fullscreenElement === root);
      requestAnimationFrame(measure);
    };
    document.addEventListener("fullscreenchange", onFull);
    return () => {
      observer.disconnect();
      globalThis.removeEventListener?.("resize", measure);
      document.removeEventListener("fullscreenchange", onFull);
    };
  }, []);

  const toggleFull = () => {
    const el = rootRef.current;
    if (!el) return;
    if (document.fullscreenElement) void document.exitFullscreen?.();
    else void el.requestFullscreen?.();
  };
  // The details open in a drawer outside the office: leave full screen to show it.
  const pick = (p: Pick) => {
    if (document.fullscreenElement) void document.exitFullscreen?.();
    onPick(p);
  };

  /* ------------------------------------------------------------ the camera */

  const levels = useMemo(() => zoomLevels(density, size.dpr), [density, size.dpr]);
  // The whole office as large as it can be: drawn at a whole level, shown a
  // little smaller (`shrink`) when the stage is a few pixels too short for it.
  const stage = useMemo(
    () =>
      world && size.w
        ? stageZoom(levels, Math.round(size.w * size.dpr), Math.round(size.h * size.dpr), world)
        : { z: levels[0], shrink: 1 },
    [world, levels, size.w, size.h, size.dpr],
  );
  // Canvas pixels per CSS pixel; the camera works in canvas pixels.
  const ratio = size.dpr / stage.shrink;
  const deviceW = Math.round(size.w * ratio);
  const deviceH = Math.round(size.h * ratio);
  const fit = stage.z;
  const bounds = useMemo<Box | null>(() => (world && art ? sceneBounds(world, art.exterior) : null), [world, art]);
  const z = camera && levels.includes(camera.z) ? camera.z : fit;
  const focusRoom = useMemo<Room | null>(
    () => (world && focusId ? ([...world.rooms, world.cluster, world.lobby].find((r) => r.id === focusId) ?? null) : null),
    [world, focusId],
  );
  // The caption's height (and a gap) is kept clear under a room in view.
  const reserve = Math.round(((captionH || 104) + 24) * ratio);

  /** Where the camera goes for a kind of view, at the current size. */
  const target = useCallback(
    (view: View, room: Room | null = null): Camera | null => {
      if (!world || !bounds || !deviceW) return null;
      if (view === "room" && room) return frameRoom(room, levels, deviceW, deviceH, reserve);
      if (view === "fit") return fitCamera(world, bounds, fit, deviceW, deviceH);
      // The first look: the whole office as large as it can be, the scene
      // around it edge to edge, on the band where people are (else the cluster
      // and the lobby) when it is closer than that.
      const zoom = defaultZoom(levels, fit, bounds.w, deviceW);
      const intents = data ? liveIntents(mode === "live" ? data.live : []) : new Map<string, Intent>();
      const interest = [...intents.values()].map((i) => {
        const id = i.kind === "work" ? (world.roomOf[i.room] ?? i.room) : "lobby";
        return [...world.rooms, world.lobby].find((r) => r.id === id) ?? world.lobby;
      });
      const rooms = interest.length ? interest : [world.cluster, world.lobby];
      return clampCamera(defaultCamera(world, zoom, deviceW, deviceH, rooms), bounds, deviceW, deviceH);
    },
    [world, bounds, deviceW, deviceH, levels, fit, reserve, data, mode],
  );

  /** Move the camera: eased, or at once when motion is reduced. */
  const moveTo = useCallback(
    (to: Camera | null) => {
      if (!to) return;
      const from = cameraRef.current;
      if (reduced || !from) {
        moveRef.current = null;
        setLift(1);
        setCamera(to);
        return;
      }
      moveRef.current = { from, to, start: performance.now() };
    },
    [reduced],
  );

  // The first look once the office is ready; the same kind of view after a resize.
  useEffect(() => {
    if (!world || !deviceW || !bounds) return;
    const view = viewRef.current;
    if (!cameraRef.current || !levels.includes(cameraRef.current.z) || view !== "free") {
      const next = target(view, focusRoom);
      if (next) {
        moveRef.current = null;
        setLift(1);
        setCamera(next);
      }
      return;
    }
    setCamera((prev) => (prev ? clampCamera(prev, bounds, deviceW, deviceH) : prev));
    // A data refresh does not move the camera: only the plan, the size and the zoom levels do.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [world, bounds, deviceW, deviceH, levels, fit]);

  // A room in view keeps clear of its caption once the caption has its height.
  useEffect(() => {
    if (viewRef.current !== "room" || !focusRoom) return;
    const next = target("room", focusRoom);
    if (!next) return;
    if (moveRef.current) moveRef.current = { ...moveRef.current, to: next };
    else setCamera(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reserve]);

  const showRoom = useCallback(
    (room: Room) => {
      viewRef.current = "room";
      setFocusId(room.id);
      moveTo(target("room", room));
    },
    [moveTo, target],
  );
  const wholeOffice = useCallback(() => {
    viewRef.current = "fit";
    setFocusId(null);
    moveTo(target("fit"));
  }, [moveTo, target]);

  const zoomAt = useCallback(
    (next: number, focus?: { x: number; y: number }) => {
      if (!world || !bounds) return;
      viewRef.current = "free";
      setFocusId(null);
      moveRef.current = null;
      setLift(1);
      setCamera((prev) => {
        const cam = prev ?? { x: 0, y: 0, z: fit };
        const nz = Math.min(levels[levels.length - 1], Math.max(levels[0], next));
        // Keep the point under the focus (device pixels in the view) where it is.
        const fx = focus?.x ?? deviceW / 2;
        const fy = focus?.y ?? deviceH / 2;
        const wx = cam.x + fx / cam.z;
        const wy = cam.y + fy / cam.z;
        return clampCamera({ x: wx - fx / nz, y: wy - fy / nz, z: nz }, bounds, deviceW, deviceH);
      });
    },
    [world, bounds, fit, levels, deviceW, deviceH],
  );
  const zoomBy = (direction: 1 | -1, focus?: { x: number; y: number }) => {
    const i = levels.indexOf(z);
    const next = levels[Math.min(levels.length - 1, Math.max(0, (i < 0 ? 0 : i) + direction))];
    zoomAt(next, focus);
  };

  // In a room: ← → to the room before or after, Esc back to the whole office.
  useEffect(() => {
    if (!focusRoom || !world) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return;
      const el = e.target as HTMLElement | null;
      if (el?.closest?.("input, textarea, select, [contenteditable=true], [role=dialog], [role=radiogroup], [role=tablist]")) return;
      if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        e.preventDefault();
        showRoom(nextRoom(world, focusRoom.id, e.key === "ArrowRight" ? 1 : -1));
      } else if (e.key === "Escape" && !document.fullscreenElement) {
        e.preventDefault();
        wholeOffice();
      }
    };
    globalThis.addEventListener("keydown", onKey);
    return () => globalThis.removeEventListener("keydown", onKey);
  }, [focusRoom, world, showRoom, wholeOffice]);

  // Ctrl/⌘ + wheel zooms (a plain wheel scrolls the page).
  const wheelRef = useRef<(e: WheelEvent) => void>(() => undefined);
  wheelRef.current = (e: WheelEvent) => {
    if (!(e.ctrlKey || e.metaKey)) return;
    e.preventDefault();
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const k = canvas.width / Math.max(1, rect.width);
    zoomBy(e.deltaY < 0 ? 1 : -1, { x: (e.clientX - rect.left) * k, y: (e.clientY - rect.top) * k });
  };
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const handler = (e: WheelEvent) => wheelRef.current(e);
    canvas.addEventListener("wheel", handler, { passive: false });
    return () => canvas.removeEventListener("wheel", handler);
  }, [size.w]);

  /* ------------------------------------------------------------ the loop */

  const levelsRef = useRef(levels);
  levelsRef.current = levels;
  useEffect(() => {
    let frame = 0;
    let last = performance.now();
    let lastUi = 0;
    let lastIntents = 0;
    const loop = (time: number) => {
      frame = requestAnimationFrame(loop);
      const dt = Math.min(0.1, (time - last) / 1000);
      last = time;
      const engine = engineRef.current;
      const canvas = canvasRef.current;
      if (!engine || !canvas) return;
      // A move under way: the scene at the nearest whole level, lifted the rest.
      const move = moveRef.current;
      if (move) {
        const t = Math.min(1, (time - move.start) / MOVE_MS);
        if (t >= 1) {
          moveRef.current = null;
          cameraRef.current = move.to;
          setCamera(move.to);
          setLift(1);
        } else {
          const step = tween(move.from, move.to, ease(t), canvas.width, canvas.height, levelsRef.current);
          cameraRef.current = step.camera;
          setCamera(step.camera);
          setLift(step.lift);
        }
      }
      const cam = cameraRef.current;
      if (!cam) return;
      if (mode === "replay" && playingRef.current && clockRef.current < DAY_MS) {
        const from = clockRef.current;
        const to = Math.min(DAY_MS, advance(from, dt, speedRef.current));
        clockRef.current = to;
        const base = dayStartRef.current;
        between(eventsRef.current, base + from, base + to).forEach(announce);
        if (time - lastIntents > 120) {
          lastIntents = time;
          const intents = replayIntents(eventsRef.current, base + to);
          const sig = signature(intents);
          if (sig !== intentSig.current) {
            intentSig.current = sig;
            engine.setIntents(intents);
          }
        }
        if (to >= DAY_MS) setPlaying(false);
      }
      engine.tick(dt);
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      engine.draw(ctx, cam);
      const ox = Math.round(cam.x * cam.z);
      const oy = Math.round(cam.y * cam.z);
      const px = canvas.width / Math.max(1, canvas.clientWidth || canvas.width);
      engine.drawLabels(ctx, (p: Point) => ({ x: Math.round(p.x * cam.z) - ox, y: Math.round(p.y * cam.z) - oy }), px, cam.z);
      if (time - lastUi > 250) {
        lastUi = time;
        if (mode === "replay") setClock(clockRef.current);
        setPlaces(engine.places());
        setNow(Date.now());
      }
    };
    frame = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(frame);
  }, [mode, announce]);

  /* ------------------------------------------------------------ pointer */

  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const drag = useRef<{ x: number; y: number; cam: Camera; moved: boolean } | null>(null);
  const pinch = useRef<{ distance: number } | null>(null);
  const devicePoint = (clientX: number, clientY: number): { x: number; y: number } | null => {
    const canvas = canvasRef.current;
    if (!canvas) return null;
    const rect = canvas.getBoundingClientRect();
    const k = canvas.width / Math.max(1, rect.width);
    return { x: (clientX - rect.left) * k, y: (clientY - rect.top) * k };
  };
  const toWorld = (clientX: number, clientY: number): Point | null => {
    const cam = cameraRef.current;
    const d = devicePoint(clientX, clientY);
    if (!cam || !d) return null;
    return { x: (d.x + Math.round(cam.x * cam.z)) / cam.z, y: (d.y + Math.round(cam.y * cam.z)) / cam.z };
  };

  const onPointerDown = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const cam = cameraRef.current;
    if (!cam || moveRef.current) return;
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    e.currentTarget.setPointerCapture(e.pointerId);
    if (pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()];
      pinch.current = { distance: Math.hypot(a.x - b.x, a.y - b.y) };
      drag.current = null;
      return;
    }
    drag.current = { x: e.clientX, y: e.clientY, cam, moved: false };
  };
  const onPointerMove = (e: React.PointerEvent<HTMLCanvasElement>) => {
    if (pointers.current.has(e.pointerId)) pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pinch.current && pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()];
      const distance = Math.hypot(a.x - b.x, a.y - b.y);
      const ratio = distance / Math.max(1, pinch.current.distance);
      if (ratio > 1.25 || ratio < 0.8) {
        const mid = devicePoint((a.x + b.x) / 2, (a.y + b.y) / 2) ?? undefined;
        zoomBy(ratio > 1 ? 1 : -1, mid);
        pinch.current = { distance };
      }
      return;
    }
    const d = drag.current;
    if (d && bounds) {
      const dx = e.clientX - d.x;
      const dy = e.clientY - d.y;
      if (Math.abs(dx) + Math.abs(dy) > 4) d.moved = true;
      if (d.moved) {
        const k = ratio / d.cam.z;
        viewRef.current = "free";
        if (focusId) setFocusId(null);
        setCamera(clampCamera({ x: d.cam.x - dx * k, y: d.cam.y - dy * k, z: d.cam.z }, bounds, deviceW, deviceH));
      }
      return;
    }
    const p = toWorld(e.clientX, e.clientY);
    const engine = engineRef.current;
    if (!p || !engine) return;
    const key = engine.hit(p);
    engine.hover = key;
    const room = key || !world ? null : roomAt(world, p);
    e.currentTarget.style.cursor = key || (room && room.id !== focusId) ? "pointer" : "grab";
  };
  const onPointerUp = (e: React.PointerEvent<HTMLCanvasElement>) => {
    pointers.current.delete(e.pointerId);
    if (pinch.current) {
      if (pointers.current.size < 2) pinch.current = null;
      drag.current = null;
      return;
    }
    const d = drag.current;
    drag.current = null;
    if (!d || d.moved) return;
    const p = toWorld(e.clientX, e.clientY);
    const engine = engineRef.current;
    if (!p || !engine || !world) return;
    const key = engine.hit(p);
    engine.focus = key;
    if (key) return pick({ kind: "person", key });
    // A room's floor or screen: step into it.
    const room = roomAt(world, p);
    if (room && room.id !== focusId) showRoom(room);
  };
  const onPointerLeave = () => {
    if (engineRef.current) engineRef.current.hover = null;
  };

  /* ------------------------------------------------------------ view */

  // The caption's own height, for keeping the room clear of it.
  useEffect(() => {
    const el = captionRef.current;
    if (!el) {
      setCaptionH(0);
      return;
    }
    const measure = () => setCaptionH(Math.ceil(el.getBoundingClientRect().height));
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [focusId]);

  const counts = useMemo(() => {
    const commits = events.filter((e) => e.kind === "commit").length;
    return { commits, deliveries: events.length - commits };
  }, [events]);
  const ready = Boolean(data && art && world && camera);
  const loading = !state || (state.key !== requestKey && !state.failed);
  const roomsById = useMemo(() => new Map((data?.rooms ?? []).map((r) => [r.id, r])), [data]);
  const scale = z / ratio;
  const camX = camera ? Math.round(camera.x * z) / z : 0;
  const camY = camera ? Math.round(camera.y * z) / z : 0;
  const box = (r: Rect) => ({
    left: (r.x - camX) * scale,
    top: (r.y - camY) * scale,
    width: r.w * scale,
    height: r.h * scale,
  });
  const today = dayKey(new Date());
  const earliest = dayKey(new Date(Date.now() - 89 * DAY_MS));
  const ticks = useMemo(
    () =>
      data
        ? events.map((e) => ({
            at: (e.at - Date.parse(data.day_start)) / DAY_MS,
            kind: e.kind,
            colour: e.person ? personColour(e.person) : "#d1a345",
          }))
        : [],
    [events, data],
  );

  const placeText = (place: string, moving: boolean, carrying: boolean) => {
    if (carrying || place === "cluster") return t("places.cluster");
    if (moving) return t("places.walking");
    if (place === "lobby") return t("places.lobby");
    const room = roomsById.get(place);
    return t("places.room", { room: room?.project_name ?? room?.name ?? place });
  };
  const atWork = data ? new Set(data.live.map((v) => v.person)).size : 0;
  const news = feed.length && now - feed[feed.length - 1].at < NEWS_MS ? feed[feed.length - 1] : null;
  // Screens are drawn at their slot's size on the map: text and logo follow
  // the screen's height on screen (CSS pixels), whatever the map and zoom.
  const productScreen = (world?.rooms[0] ?? world?.vacant[0])?.screen.h ?? 24;
  const productPx = productScreen * scale;
  const stripFont = Math.max(7, Math.min(14, Math.round(productPx * 0.27)));
  // The project's mark fills most of what the name strip leaves of the screen
  // (its 1px border top and bottom, the strip's text and padding).
  const stripPx = scale >= 0.8 ? stripFont + 3 * scale : 0;
  const screenMark = Math.max(6, Math.round((productPx - 2 - stripPx) * 0.8));
  const clusterPx = (world?.cluster.screen.h ?? 28) * scale;
  const lobbyPx = (world?.lobby.screen.h ?? 28) * scale;
  const duosis = Boolean(data?.cluster && isDuosis({ cluster_ref: data.cluster.ref, site: data.cluster.name }));
  const clusterStatus = data?.cluster
    ? data.cluster.workloads
      ? t("cluster.health", { healthy: data.cluster.healthy, total: data.cluster.workloads })
      : t("cluster.ref", { ref: data.cluster.ref })
    : t("cluster.unknown");

  /* The caption of a room in view: what it is, who is in it, its latest work. */
  const caption = (() => {
    if (!focusRoom || !world || !data) return null;
    const ids = new Set(focusRoom.kind === "product" ? focusRoom.members : []);
    const inRoom = places.filter((p) =>
      focusRoom.kind === "cluster"
        ? p.place === "cluster"
        : focusRoom.kind === "lobby"
          ? p.place === "lobby" && !p.moving
          : ids.has(p.place) && !p.carrying,
    );
    const people = inRoom.map((p) => ({ key: p.person, name: nameOf.current.get(p.person) ?? p.person }));
    const until = mode === "replay" ? dayStartRef.current + clock : Number.POSITIVE_INFINITY;
    const mine = events.filter((e) => e.at <= until && (focusRoom.kind === "cluster" ? e.kind === "deployment" : ids.has(e.repository)));
    const commits = mine.filter((e) => e.kind === "commit").slice(-2).reverse();
    const r = focusRoom.kind === "product" && focusRoom.id !== OTHER ? roomsById.get(focusRoom.id) : undefined;
    const name =
      focusRoom.kind === "cluster"
        ? (data.cluster?.name ?? t("rooms.cluster"))
        : focusRoom.kind === "lobby"
          ? t("rooms.lobby")
          : focusRoom.id === OTHER
            ? t("rooms.other", { count: focusRoom.members.length })
            : (r?.project_name ?? r?.name ?? focusRoom.id);
    const subtitle =
      focusRoom.kind === "cluster"
        ? clusterStatus
        : focusRoom.kind === "lobby"
          ? t("focus.lobby")
          : t("focus.window", { count: focusRoom.members.reduce((sum, id) => sum + (roomsById.get(id)?.commits ?? 0), 0) });
    return { name, subtitle, people, commits, deliveries: mine.filter((e) => e.kind === "deployment").length, repo: r };
  })();

  const replayBar = mode === "replay" && data;
  const latestDay = mode === "replay" || day !== today ? t("focus.latestDay") : t("focus.latest");
  return (
    <div
      ref={rootRef}
      data-testid="dev-office"
      className="flex flex-col overflow-hidden bg-canvas"
      style={{ height: full ? "100%" : rootH || undefined }}
    >
      {/* The toolbar: the page's tabs, then the office's controls, in one row. */}
      <div className="flex shrink-0 flex-wrap items-center gap-x-2 gap-y-1.5 border-b border-border bg-surface px-3 py-1 lg:px-4">
        {overlay && !full ? <div className="min-w-0 max-w-full">{overlay}</div> : null}
        <div role="toolbar" aria-label={t("toolbar")} className="ml-auto flex min-w-0 flex-wrap items-center justify-end gap-1.5">
          <div className="flex min-w-0 items-center gap-2">
            <SegmentedControl
              label={t("mode.label")}
              value={mode}
              onChange={(next) => setMode(next)}
              options={[
                { value: "live", label: t("mode.live") },
                { value: "replay", label: t("mode.replay") },
              ]}
            />
            {mode === "live" ? (
              <span
                className="inline-flex min-w-0 items-center gap-2 text-caption text-ink-secondary"
                data-testid="dev-office-live"
                aria-live="polite"
                title={data && !data.configured.telemetry ? t("live.notConnected") : undefined}
              >
                <span aria-hidden className="relative flex h-2 w-2 shrink-0">
                  {data && !data.configured.telemetry ? (
                    <span className="relative inline-flex h-2 w-2 rounded-full bg-ink-muted" />
                  ) : (
                    <>
                      <span className={`absolute inline-flex h-full w-full rounded-full bg-[#47d27b] opacity-60 ${reduced ? "" : "animate-ping"}`} />
                      <span className="relative inline-flex h-2 w-2 rounded-full bg-[#47d27b]" />
                    </>
                  )}
                </span>
                {news ? (
                  <span className="max-w-[18rem] truncate text-ink">
                    <span aria-hidden className="mr-1.5 inline-block h-2 w-2 rounded-full align-middle" style={{ background: news.colour }} />
                    {news.text}
                  </span>
                ) : data && !data.configured.telemetry ? (
                  <span className="truncate">{t("live.offline")}</span>
                ) : (
                  <span className="whitespace-nowrap">{t("live.working", { count: atWork })}</span>
                )}
              </span>
            ) : null}
          </div>
          <Divider />
          <SegmentedControl
            label={tDev("window.label")}
            value={span}
            onChange={onWindow}
            options={(["7d", "30d", "90d"] as const).map((w) => ({ value: w, label: t(`window.${w}`) }))}
          />
          <Divider />
          <div className="inline-flex shrink-0 items-center gap-0.5 rounded-control border border-border bg-surface p-0.5">
            <Tool label={tDev("connect.open")} icon={Plug} onClick={onConnect} />
            <span aria-hidden className="mx-0.5 h-4 w-px bg-border" />
            <div role="group" aria-label={t("zoom.label")} className="inline-flex items-center gap-0.5">
              <Tool label={t("zoom.out")} icon={Minus} onClick={() => zoomBy(-1)} />
              <Tool label={t("zoom.fit")} icon={Scan} onClick={wholeOffice} testId="dev-office-fit" />
              <Tool label={t("zoom.in")} icon={Plus} onClick={() => zoomBy(1)} />
            </div>
            <span aria-hidden className="mx-0.5 h-4 w-px bg-border" />
            <Tool
              label={full ? t("fullscreen.exit") : t("fullscreen.enter")}
              icon={full ? Shrink : Expand}
              onClick={toggleFull}
              testId="dev-office-fullscreen"
            />
          </div>
        </div>
      </div>

      {/* The stage: the office alone. */}
      <div ref={stageRef} className="relative min-h-0 flex-1 overflow-hidden bg-[#0b0e18]">
        {loading && !ready ? (
          <div className="absolute inset-0 grid place-items-center">
            <DataState kind="loading" />
          </div>
        ) : null}
        {state?.failed && !data ? (
          <div className="absolute inset-0 grid place-items-center">
            <DataState kind="error" />
          </div>
        ) : null}
        {/* The scene: lifted (enlarged about the middle) part of the way while the camera moves. */}
        <div
          className="absolute inset-0"
          style={lift !== 1 ? { transform: `scale(${lift})`, transformOrigin: "50% 50%" } : undefined}
        >
          {ready && world ? (
            // Under the canvas, which leaves a window open over each screen. The
            // screens glow in the dark whatever the page's theme.
            <div className="dark pointer-events-none absolute inset-0 z-0" aria-hidden>
              {/* Each product room's screen: the project's logo, its name under it. */}
              {world.rooms.map((room) => {
                const r = roomsById.get(room.id);
                const other = room.id === OTHER;
                const name = other ? t("rooms.other", { count: room.members.length }) : (r?.project_name ?? r?.name ?? "");
                return (
                  <div
                    key={room.id}
                    className="absolute flex flex-col overflow-hidden rounded-[2px] border border-[#6d9cff]/45 bg-[#07102a] shadow-[0_0_16px_rgba(90,150,255,0.45)]"
                    style={box(room.screen)}
                  >
                    <div className="flex min-h-0 flex-1 items-center justify-center bg-[radial-gradient(circle_at_50%_40%,#183066,#07102a_70%)]">
                      {r && !other ? (
                        <ScreenMark room={r} height={screenMark} />
                      ) : (
                        <span className="font-bold text-[#cfe0ff]" style={{ fontSize: Math.max(8, Math.round(productPx * 0.36)) }}>
                          +{room.members.length}
                        </span>
                      )}
                    </div>
                    {/* Too small to read (a phone's whole-office view): the logo alone. */}
                    {scale >= 0.8 ? (
                      <div
                        className="truncate bg-[#0d1d44] px-1 text-center leading-none font-semibold text-[#dbe7ff]"
                        style={{ fontSize: stripFont, paddingTop: 1.5 * scale, paddingBottom: 1.5 * scale }}
                      >
                        {name}
                      </div>
                    ) : null}
                  </div>
                );
              })}
              {world.vacant.map((room) => (
                <div
                  key={room.slot}
                  className="absolute flex items-center justify-center rounded-[2px] border border-[#2b3550] bg-[#070a14] text-[#5a6584]"
                  style={{ ...box(room.screen), fontSize: stripFont }}
                >
                  {t("rooms.available")}
                </div>
              ))}
              {/* The cluster's screen: which cluster this is, and how it is doing. */}
              <div
                className="absolute flex flex-col items-center justify-center overflow-hidden rounded-[2px] border border-[#47d2c8]/50 bg-[radial-gradient(circle_at_50%_30%,#0f3a4a,#04121c_75%)] text-center shadow-[0_0_20px_rgba(70,210,200,0.45)]"
                style={box(world.cluster.screen)}
              >
                {data?.cluster && duosis ? (
                  // Duosis's own cluster: its logo, as on the cluster pages.
                  <img
                    src="/owners/duosis-logo-dark.svg"
                    alt={data.cluster.name}
                    draggable={false}
                    className="block w-auto max-w-[86%] object-contain"
                    style={{ height: Math.max(10, Math.round(clusterPx * (clusterPx >= 30 ? 0.52 : 0.7))) }}
                    data-testid="dev-office-cluster-logo"
                  />
                ) : (
                  <span
                    className="max-w-full truncate px-1 leading-none font-bold tracking-[0.06em] text-[#e6fffb]"
                    style={{ fontSize: Math.max(8, Math.round(clusterPx * 0.3)) }}
                  >
                    {data?.cluster?.name ?? t("rooms.cluster")}
                  </span>
                )}
                {/* Too small to read (a phone's whole-office view): the name alone. */}
                {clusterPx >= 30 ? (
                  <span
                    className="mt-[3%] inline-flex items-center gap-1 leading-none whitespace-nowrap text-[#86e6cf]"
                    style={{ fontSize: Math.max(7, Math.round(clusterPx * 0.2)) }}
                  >
                    <span
                      aria-hidden
                      className="inline-block rounded-full bg-[#47d27b]"
                      style={{ width: Math.max(4, clusterPx * 0.1), height: Math.max(4, clusterPx * 0.1) }}
                    />
                    {clusterStatus}
                  </span>
                ) : null}
              </div>
              {/* The lobby's logo wall. */}
              <div
                className="absolute flex items-center justify-center rounded-[2px] border border-[#d1a345]/50 bg-[radial-gradient(circle_at_50%_40%,#2a2440,#120f1c_75%)] shadow-[0_0_16px_rgba(209,163,69,0.35)]"
                style={box(world.lobby.screen)}
              >
                {/* The company's front door: Duosis when the office's cluster is Duosis's. */}
                {duosis ? (
                  <img
                    src="/owners/duosis-logo-dark.svg"
                    alt="Duosis" // i18n-ignore: a company name
                    draggable={false}
                    className="block w-auto max-w-[88%] object-contain"
                    style={{ height: Math.max(10, Math.round(lobbyPx * 0.72)) }}
                    data-testid="dev-office-lobby-logo"
                  />
                ) : (
                  <TracesisWordmark size={Math.max(10, Math.round(lobbyPx * 0.46))} />
                )}
              </div>
            </div>
          ) : null}
          {size.w > 0 ? (
            <canvas
              ref={canvasRef}
              width={deviceW}
              height={deviceH}
              // Above the wall screens (z-0): people walk in front of them.
              className="relative z-[1] block touch-none select-none"
              // Every pixel whole, or (shrunk a little to show the whole office) smoothed.
              style={{ width: size.w, height: size.h, imageRendering: stage.shrink < 1 ? "auto" : "pixelated", cursor: "grab" }}
              role="img"
              aria-label={t("title")}
              aria-describedby="dev-office-places"
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={onPointerUp}
              onPointerCancel={onPointerUp}
              onPointerLeave={onPointerLeave}
              data-testid="dev-office-canvas"
            />
          ) : null}
        </div>

        {/* A room in view: on to the room before or after, and what is in it. */}
        {focusRoom && caption ? (
          <>
            <button
              type="button"
              onClick={() => world && showRoom(nextRoom(world, focusRoom.id, -1))}
              aria-label={t("focus.previous")}
              title={t("focus.previous")}
              className="absolute top-1/2 left-3 z-20 grid h-10 w-10 -translate-y-1/2 place-items-center rounded-full border border-white/15 bg-[#0a0f1e]/80 text-white shadow-lg backdrop-blur-sm transition-colors hover:bg-[#151c33]"
              data-testid="dev-office-previous"
            >
              <ChevronLeft className="h-5 w-5" aria-hidden />
            </button>
            <button
              type="button"
              onClick={() => world && showRoom(nextRoom(world, focusRoom.id, 1))}
              aria-label={t("focus.next")}
              title={t("focus.next")}
              className="absolute top-1/2 right-3 z-20 grid h-10 w-10 -translate-y-1/2 place-items-center rounded-full border border-white/15 bg-[#0a0f1e]/80 text-white shadow-lg backdrop-blur-sm transition-colors hover:bg-[#151c33]"
              data-testid="dev-office-next"
            >
              <ChevronRight className="h-5 w-5" aria-hidden />
            </button>
            <div
              ref={captionRef}
              role="region"
              aria-label={caption.name}
              aria-live="polite"
              className="dark absolute bottom-3 left-1/2 z-20 w-[min(640px,calc(100%-1.5rem))] -translate-x-1/2 rounded-xl border border-white/10 bg-[#0a0f1e]/90 px-3.5 py-2.5 text-white shadow-xl backdrop-blur-md"
              data-testid="dev-office-caption"
            >
              <div className="flex items-center gap-3">
                {caption.repo ? (
                  <span className="shrink-0">
                    <RepositoryMark repo={asRepository(caption.repo)} size="lg" />
                  </span>
                ) : null}
                <div className="min-w-0 flex-1">
                  <p className="truncate text-body font-semibold leading-tight">{caption.name}</p>
                  <p className="truncate text-micro text-white/65">{caption.subtitle}</p>
                </div>
                {caption.repo ? (
                  <Button
                    size="compact"
                    variant="ghost"
                    className="text-white/85 hover:bg-white/10 hover:text-white"
                    onClick={() => caption.repo && pick({ kind: "repository", id: caption.repo.id })}
                    data-testid="dev-office-details"
                  >
                    {t("focus.details")}
                  </Button>
                ) : null}
                <Button
                  size="compact"
                  variant="ghost"
                  icon={Scan}
                  className="text-white/85 hover:bg-white/10 hover:text-white"
                  onClick={wholeOffice}
                  data-testid="dev-office-whole"
                >
                  {t("focus.whole")}
                </Button>
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-1.5 text-micro">
                <span className="text-white/60">{t("focus.here", { count: caption.people.length })}</span>
                {caption.people.slice(0, 6).map((p) => (
                  <button
                    key={p.key}
                    type="button"
                    onClick={() => pick({ kind: "person", key: p.key })}
                    className="inline-flex items-center gap-1.5 rounded-full bg-white/8 py-0.5 pr-2 pl-1.5 text-white/90 hover:bg-white/15"
                  >
                    <span aria-hidden className="h-2 w-2 rounded-full" style={{ background: personColour(p.key) }} />
                    {p.name.split(" ")[0]}
                  </button>
                ))}
                {caption.people.length > 6 ? <span className="text-white/60">+{caption.people.length - 6}</span> : null}
              </div>
              {focusRoom.kind === "product" ? (
                <ul className="mt-1.5 space-y-0.5 text-micro">
                  {caption.commits.length ? (
                    caption.commits.map((c) => (
                      <li key={`${c.at}:${c.short}`} className="flex min-w-0 items-center gap-2">
                        <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: c.person ? personColour(c.person) : "#d1a345" }} />
                        <span className="shrink-0 font-mono text-[#9fb5ff]">{c.short ?? ""}</span>
                        <span className="truncate text-white/80">{c.title ?? ""}</span>
                      </li>
                    ))
                  ) : (
                    <li className="text-white/55">{t("focus.quiet")}</li>
                  )}
                  {caption.commits.length ? <li className="sr-only">{latestDay}</li> : null}
                </ul>
              ) : focusRoom.kind === "cluster" ? (
                <p className="mt-1.5 text-micro text-white/70">{t("focus.deliveries", { count: caption.deliveries })}</p>
              ) : null}
              <p className="mt-1.5 hidden text-[0.6875rem] text-white/45 sm:block">{t("focus.keys")}</p>
            </div>
          </>
        ) : null}
      </div>

      {/* Below the stage, playing a day: the clock, the day, the timeline. */}
      {replayBar ? (
        <div
          className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1.5 border-t border-border bg-surface px-3 py-1 text-ink lg:px-4"
          data-testid="dev-office-replay"
        >
          <span className="font-mono text-[1.05rem] font-bold tracking-wide tabular-nums">{clockLabel(clock)}</span>
          <label className="inline-flex items-center">
            <span className="sr-only">{t("replay.day")}</span>
            <input
              type="date"
              value={day}
              min={earliest}
              max={today}
              onChange={(e) => e.target.value && setDay(e.target.value)}
              className="h-7 rounded-control border border-border bg-surface px-1.5 text-caption text-ink"
              data-testid="dev-office-day"
            />
          </label>
          <Button
            size="compact"
            icon={playing ? Pause : Play}
            onClick={() => {
              if (!playing && clockRef.current >= DAY_MS) seek(0);
              setPlaying((p) => !p);
            }}
            data-testid="dev-office-play"
          >
            {playing ? t("replay.pause") : t("replay.play")}
          </Button>
          <SegmentedControl
            size="compact"
            label={t("replay.speed")}
            value={String(speed) as `${(typeof SPEEDS)[number]}`}
            onChange={(next) => setSpeed(Number(next) as (typeof SPEEDS)[number])}
            options={SPEEDS.map((s) => ({ value: String(s) as `${typeof s}`, label: `${s}×` }))}
          />
          <div className="relative min-w-[160px] flex-1">
            <div aria-hidden className="pointer-events-none absolute inset-x-0 -top-1 h-2">
              {ticks.map((tick, i) => (
                <span
                  key={i}
                  className="absolute top-0 w-[2px] rounded-full"
                  style={{
                    left: `${Math.min(100, Math.max(0, tick.at * 100))}%`,
                    height: tick.kind === "deployment" ? 8 : 5,
                    background: tick.colour,
                    opacity: 0.9,
                  }}
                />
              ))}
            </div>
            <input
              type="range"
              min={0}
              max={DAY_MS}
              step={60_000}
              value={clock}
              onChange={(e) => seek(Number(e.target.value))}
              aria-label={t("replay.time")}
              aria-valuetext={clockLabel(clock)}
              className="mt-1.5 w-full accent-[#d1a345]"
              data-testid="dev-office-time"
            />
          </div>
          <span className="max-w-[22rem] min-w-0 truncate text-micro text-ink-secondary" aria-live="polite">
            {news ? (
              <>
                <span aria-hidden className="mr-1.5 inline-block h-2 w-2 rounded-full align-middle" style={{ background: news.colour }} />
                <span className="text-ink">{news.text}</span>
              </>
            ) : (
              <>
                {data.events.length ? t("replay.summary", { commits: counts.commits, deliveries: counts.deliveries }) : t("replay.empty")}
                {clock >= DAY_MS ? ` · ${t("replay.ended")}` : ""}
                {data.truncated ? ` · ${t("replay.truncated")}` : ""}
              </>
            )}
          </span>
        </div>
      ) : null}

      <p className="sr-only">{lead}</p>
      <p className="sr-only">{t("hint")}</p>

      <div id="dev-office-places" className="sr-only">
        <h3>{t("places.title")}</h3>
        <ul>
          {places.map((p) => (
            <li key={p.person}>
              {data?.people.find((x) => x.key === p.person)?.name ?? p.person}: {placeText(p.place, p.moving, p.carrying)}
            </li>
          ))}
        </ul>
      </div>
      {/* Rooms and people reachable without a pointer: a room steps into view. */}
      <div className="sr-only">
        {world
          ? [...world.rooms, world.cluster, world.lobby].map((room) => (
              <button key={room.id} type="button" onClick={() => showRoom(room)}>
                {room.kind === "cluster"
                  ? (data?.cluster?.name ?? t("rooms.cluster"))
                  : room.kind === "lobby"
                    ? t("rooms.lobby")
                    : room.id === OTHER
                      ? t("rooms.other", { count: room.members.length })
                      : (roomsById.get(room.id)?.project_name ?? roomsById.get(room.id)?.name ?? room.id)}
              </button>
            ))
          : null}
        {data?.people.map((p) => (
          <button
            key={p.key}
            type="button"
            onClick={() => pick({ kind: "person", key: p.key })}
            onFocus={() => {
              if (engineRef.current) engineRef.current.focus = p.key;
            }}
            onBlur={() => {
              if (engineRef.current?.focus === p.key) engineRef.current.focus = null;
            }}
          >
            {p.name}
          </button>
        ))}
      </div>
    </div>
  );
}
