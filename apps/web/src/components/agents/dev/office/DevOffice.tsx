"use client";

/**
 * Agent Visibility → Office: the people who change the repositories as a
 * small pixel office. Product rooms around the cluster's server room, the
 * lobby at the bottom; live, whoever's Claude Code is working sits at their
 * desk; a replayed day walks each commit's author to their desk and carries
 * each delivery to the cluster.
 *
 * The scene is drawn straight onto a canvas through a camera whose zoom is a
 * whole number of device pixels per art pixel (an even number when a
 * character is drawn at double density), so every pixel stays crisp. Room
 * names and logos are HTML on top; the text alternative lists who is where.
 */

import { Expand, Minus, Pause, Play, Plug, Plus, Scan, Shrink } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { isDuosis } from "@/components/clusters/ClusterCard";
import { TracesisWordmark } from "@/components/shell/Brand";
import { DataState } from "@/components/state/DataState";
import { Button, SegmentedControl } from "@/components/ui/controls";
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
  advance,
  between,
  DAY_MS,
  dayKey,
  isoWithOffset,
  liveIntents,
  localMidnight,
  nightAt,
  parseEvents,
  replayIntents,
  SPEEDS,
  type Intent,
  type TimedEvent,
} from "./director";
import { OfficeEngine } from "./engine";
import { DEFAULT_LAYOUT } from "./layout";
import { layoutOffice, OTHER, roomAt, type Point, type Rect, type World } from "./world";

type Mode = "live" | "replay";
const LIVE_POLL_MS = 30_000;
const MIN_HEIGHT = 380;

/** The camera: the view's top-left in art pixels, `z` device pixels per art pixel. */
export interface Camera {
  x: number;
  y: number;
  z: number;
}

interface FeedItem {
  id: number;
  text: string;
  colour: string;
}

/**
 * The zoom levels, in device pixels per art pixel, for the densest character
 * on screen (`density` sprite pixels per art pixel). Below it, only exact
 * divisors (a 4× character is shrunk 4:1 or 2:1, block by block); from it
 * up, only its multiples (each sprite pixel 1, 2, 3… device pixels). Every
 * level keeps every pixel whole.
 */
export function zoomLevels(density: Density, dpr: number): number[] {
  const out = [1, 2, 4].filter((z) => z < density && density % z === 0);
  // 4× art also at Z = 3: drawn one device pixel per sprite pixel, unshrunk.
  if (density === 4) out.push(3);
  const top = Math.max(density * 2, Math.round(8 * dpr));
  for (let z = density; z <= top; z += density) out.push(z);
  return out.sort((a, b) => a - b);
}

/**
 * The largest zoom at which the whole office fits the stage, width and
 * height: the first look and "fit". A stage too small for any (a phone)
 * gets the smallest, and pans.
 */
export function fitZoom(
  density: Density,
  dpr: number,
  cssWidth: number,
  cssHeight: number,
  world: { w: number; h: number },
): number {
  const levels = zoomLevels(density, dpr);
  const w = cssWidth * dpr;
  const h = cssHeight * dpr;
  return levels.filter((z) => world.w * z <= w && world.h * z <= h).pop() ?? levels[0];
}

/**
 * The camera kept inside the office. An office smaller than the view is
 * centred across, and down between the room the floating controls take at
 * the top and (playing a day) the replay bar at the bottom — `reserve`, in
 * device pixels.
 */
export function clampCamera(
  cam: Camera,
  world: { w: number; h: number },
  viewW: number,
  viewH: number,
  reserve: { top: number; bottom: number } = { top: 0, bottom: 0 },
): Camera {
  const vw = viewW / cam.z;
  const vh = viewH / cam.z;
  const x = vw >= world.w ? (world.w - vw) / 2 : Math.min(Math.max(0, cam.x), world.w - vw);
  let y: number;
  if (vh >= world.h) {
    const free = vh - world.h;
    const top = reserve.top / cam.z;
    const bottom = reserve.bottom / cam.z;
    // Enough room: centred between the reserves; else shared in their proportion.
    const gap = free >= top + bottom ? top + (free - top - bottom) / 2 : top + bottom > 0 ? (free * top) / (top + bottom) : free / 2;
    y = -gap;
  } else y = Math.min(Math.max(0, cam.y), world.h - vh);
  return { x, y, z: cam.z };
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
  /** The page's own navigation, floated over the scene's top-left. */
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
  const [size, setSize] = useState({ w: 0, h: 0, dpr: 1 });

  const [places, setPlaces] = useState<ReturnType<OfficeEngine["places"]>>([]);
  const [feed, setFeed] = useState<FeedItem[]>([]);

  const wrapRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const engineRef = useRef<OfficeEngine | null>(null);
  const cameraRef = useRef<Camera | null>(null);
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
  // A character drawn at double density needs an even zoom to keep its pixels whole.
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
    const item = { id: feedId.current, text, colour };
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

  /* ------------------------------------------------------------ size and camera */

  // The stage takes the content area from where it starts to the bottom of
  // the window (the whole screen in fullscreen), so the page never scrolls.
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const measure = () => {
      const dpr = Math.max(1, Math.round((globalThis.devicePixelRatio || 1) * 4) / 4);
      const w = Math.floor(el.clientWidth);
      const inFull = document.fullscreenElement === el;
      const top = el.getBoundingClientRect().top + (globalThis.scrollY || 0);
      const h = inFull ? Math.floor(el.clientHeight) : Math.max(MIN_HEIGHT, Math.floor((globalThis.innerHeight || 800) - top));
      setSize((prev) => (prev.w === w && prev.h === h && prev.dpr === dpr ? prev : { w, h, dpr }));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    globalThis.addEventListener?.("resize", measure);
    const onFull = () => {
      setFull(document.fullscreenElement === el);
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
    const el = wrapRef.current;
    if (!el) return;
    if (document.fullscreenElement) void document.exitFullscreen?.();
    else void el.requestFullscreen?.();
  };
  // The details open in a drawer outside the stage: leave fullscreen to show it.
  const pick = (p: Pick) => {
    if (document.fullscreenElement) void document.exitFullscreen?.();
    onPick(p);
  };

  const deviceW = Math.round(size.w * size.dpr);
  const deviceH = Math.round(size.h * size.dpr);
  const levels = useMemo(() => zoomLevels(density, size.dpr), [density, size.dpr]);
  // The whole office, at the largest zoom that keeps every pixel whole.
  const fit = world && size.w ? fitZoom(density, size.dpr, size.w, size.h, world) : levels[0];
  const z = camera && levels.includes(camera.z) ? camera.z : fit;
  const viewCssH = size.h || MIN_HEIGHT;
  // Room kept clear for the floating controls (top) and the replay bar (bottom), in device pixels.
  const align = useMemo(
    () => ({ top: 60 * size.dpr, bottom: mode === "replay" ? 60 * size.dpr : 12 * size.dpr }),
    [mode, size.dpr],
  );

  useEffect(() => {
    if (!world || !deviceW) return;
    setCamera((prev) => {
      if (prev && levels.includes(prev.z)) return clampCamera(prev, world, deviceW, deviceH, align);
      return clampCamera({ x: 0, y: 0, z: fit }, world, deviceW, deviceH, align);
    });
  }, [world, deviceW, deviceH, fit, levels, align]);

  const zoomAt = useCallback(
    (next: number | "fit", focus?: { x: number; y: number }) => {
      if (!world) return;
      setCamera((prev) => {
        const cam = prev ?? { x: 0, y: 0, z: fit };
        const nz = next === "fit" ? fit : Math.min(levels[levels.length - 1], Math.max(levels[0], next));
        // Keep the point under the focus (device pixels in the view) where it is.
        const fx = focus?.x ?? deviceW / 2;
        const fy = focus?.y ?? deviceH / 2;
        const wx = cam.x + fx / cam.z;
        const wy = cam.y + fy / cam.z;
        return clampCamera({ x: wx - fx / nz, y: wy - fy / nz, z: nz }, world, deviceW, deviceH, align);
      });
    },
    [world, fit, levels, deviceW, deviceH, align],
  );
  const zoomBy = (direction: 1 | -1, focus?: { x: number; y: number }) => {
    const i = levels.indexOf(z);
    const next = levels[Math.min(levels.length - 1, Math.max(0, (i < 0 ? 0 : i) + direction))];
    zoomAt(next, focus);
  };

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

  useEffect(() => {
    let frame = 0;
    let last = performance.now();
    let lastUi = 0;
    let lastIntents = 0;
    const loop = (now: number) => {
      frame = requestAnimationFrame(loop);
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      const engine = engineRef.current;
      const canvas = canvasRef.current;
      const cam = cameraRef.current;
      if (!engine || !canvas || !cam) return;
      if (mode === "replay") {
        if (playingRef.current && clockRef.current < DAY_MS) {
          const from = clockRef.current;
          const to = Math.min(DAY_MS, advance(from, dt, speedRef.current));
          clockRef.current = to;
          const base = dayStartRef.current;
          between(eventsRef.current, base + from, base + to).forEach(announce);
          if (now - lastIntents > 120) {
            lastIntents = now;
            const intents = replayIntents(eventsRef.current, base + to);
            const sig = signature(intents);
            if (sig !== intentSig.current) {
              intentSig.current = sig;
              engine.setIntents(intents);
            }
          }
          if (to >= DAY_MS) setPlaying(false);
        }
        engine.night = nightAt(clockRef.current / 3_600_000);
      } else {
        const d = new Date();
        engine.night = nightAt(d.getHours() + d.getMinutes() / 60);
      }
      engine.tick(dt);
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.fillStyle = "#0b0e18";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      engine.draw(ctx, cam);
      const ox = Math.round(cam.x * cam.z);
      const oy = Math.round(cam.y * cam.z);
      const px = canvas.width / Math.max(1, canvas.clientWidth || canvas.width);
      engine.drawLabels(ctx, (p: Point) => ({ x: Math.round(p.x * cam.z) - ox, y: Math.round(p.y * cam.z) - oy }), px, cam.z);
      if (now - lastUi > 250) {
        lastUi = now;
        if (mode === "replay") setClock(clockRef.current);
        setPlaces(engine.places());
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
    if (!cam) return;
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
    if (d && world) {
      const dx = e.clientX - d.x;
      const dy = e.clientY - d.y;
      if (Math.abs(dx) + Math.abs(dy) > 4) d.moved = true;
      if (d.moved) {
        const k = size.dpr / d.cam.z;
        setCamera(clampCamera({ x: d.cam.x - dx * k, y: d.cam.y - dy * k, z: d.cam.z }, world, deviceW, deviceH, align));
      }
      return;
    }
    const p = toWorld(e.clientX, e.clientY);
    const engine = engineRef.current;
    if (!p || !engine) return;
    const key = engine.hit(p);
    engine.hover = key;
    const room = key ? null : world ? roomAt(world, p) : null;
    e.currentTarget.style.cursor = key || room?.kind === "product" ? "pointer" : "grab";
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
    const room = roomAt(world, p);
    // A shared room opens its first repository; an empty one opens nothing.
    if (room?.kind === "product" && !room.vacant) pick({ kind: "repository", id: room.members[0] ?? room.id });
  };
  const onPointerLeave = () => {
    if (engineRef.current) engineRef.current.hover = null;
  };

  /* ------------------------------------------------------------ view */

  const counts = useMemo(() => {
    const commits = events.filter((e) => e.kind === "commit").length;
    return { commits, deliveries: events.length - commits };
  }, [events]);
  const ready = Boolean(data && art && world && camera);
  const loading = !state || (state.key !== requestKey && !state.failed);
  const roomsById = useMemo(() => new Map((data?.rooms ?? []).map((r) => [r.id, r])), [data]);
  const scale = z / size.dpr;
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
  // Screens are drawn at their slot's size on the map: text and logo follow
  // the screen's height on screen (CSS pixels), whatever the map and zoom.
  const productScreen = (world?.rooms[0] ?? world?.vacant[0])?.screen.h ?? 24;
  const productPx = productScreen * scale;
  const stripFont = Math.max(7, Math.min(14, Math.round(productPx * 0.27)));
  // The project's mark is about 22 CSS pixels tall at "lg": fit it above the strip.
  const screenMark = Math.min(2, Math.max(0.5, (productPx * (scale >= 0.8 ? 0.5 : 0.8)) / 22));
  const clusterPx = (world?.cluster.screen.h ?? 28) * scale;
  const lobbyPx = (world?.lobby.screen.h ?? 28) * scale;
  const clusterStatus = data?.cluster
    ? data.cluster.workloads
      ? t("cluster.health", { healthy: data.cluster.healthy, total: data.cluster.workloads })
      : t("cluster.ref", { ref: data.cluster.ref })
    : t("cluster.unknown");

  // Controls float over the scene in compact, translucent panels.
  const chrome = "rounded-xl border border-white/10 bg-[#0a0f1e]/75 shadow-lg backdrop-blur-md";
  const replayBar = mode === "replay" && data;
  return (
    <div data-testid="dev-office">
      <div
        ref={wrapRef}
        className="relative w-full overflow-hidden bg-[#0b0e18]"
        style={{ height: full ? "100%" : viewCssH }}
        title={t("hint")}
      >
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
        {size.w > 0 ? (
          <canvas
            ref={canvasRef}
            width={deviceW}
            height={deviceH}
            className="block touch-none select-none"
            style={{ width: size.w, height: viewCssH, imageRendering: "pixelated", cursor: "grab" }}
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
        {ready && world ? (
          <div className="pointer-events-none absolute inset-0" aria-hidden>
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
                  title={r ? (r.catalogued ? r.full_name : `${r.full_name} · ${t("rooms.notCatalogued")}`) : undefined}
                >
                  <div className="flex min-h-0 flex-1 items-center justify-center bg-[radial-gradient(circle_at_50%_40%,#183066,#07102a_70%)]">
                    {r && !other ? (
                      <span className="flex items-center justify-center" style={{ transform: `scale(${screenMark})` }}>
                        <RepositoryMark repo={asRepository(r)} size="lg" />
                      </span>
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
              {data?.cluster && isDuosis({ cluster_ref: data.cluster.ref, site: data.cluster.name }) ? (
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
                <span className="max-w-full truncate px-1 leading-none font-bold tracking-[0.06em] text-[#e6fffb]" style={{ fontSize: Math.max(8, Math.round(clusterPx * 0.3)) }}>
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
              {data?.cluster && isDuosis({ cluster_ref: data.cluster.ref, site: data.cluster.name }) ? (
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

        {/* Top left: the page's tabs, the mode, and what is going on. */}
        <div className="pointer-events-none absolute top-[3.75rem] left-3 z-20 flex max-w-[calc(100%-1.5rem)] flex-wrap items-center gap-2 sm:top-3 sm:max-w-[calc(100%-25rem)]">
          {overlay ? <div className="pointer-events-auto max-w-full">{overlay}</div> : null}
          <div className={`${chrome} pointer-events-auto flex flex-wrap items-center gap-2 p-1.5 pr-3`}>
            <SegmentedControl
              size="compact"
              label={t("mode.label")}
              value={mode}
              onChange={(next) => setMode(next)}
              options={[
                { value: "live", label: t("mode.live") },
                { value: "replay", label: t("mode.replay") },
              ]}
            />
            {mode === "live" ? (
              <span className="inline-flex items-center gap-2 text-caption text-white/85" data-testid="dev-office-live">
                <span aria-hidden className="relative flex h-2 w-2">
                  <span className={`absolute inline-flex h-full w-full rounded-full bg-[#47d27b] opacity-60 ${reduced ? "" : "animate-ping"}`} />
                  <span className="relative inline-flex h-2 w-2 rounded-full bg-[#47d27b]" />
                </span>
                {t("live.working", { count: atWork })}
              </span>
            ) : null}
          </div>
          {mode === "live" && data && !data.configured.telemetry ? (
            <p className={`${chrome} pointer-events-auto max-w-md px-3 py-2 text-micro text-white/80`}>{t("live.notConnected")}</p>
          ) : null}
        </div>

        {/* Top right: the window, connecting Claude Code, zoom, fullscreen. */}
        <div className={`${chrome} absolute top-3 right-3 z-20 flex flex-wrap items-center justify-end gap-1 p-1`}>
          <SegmentedControl
            size="compact"
            label={tDev("window.label")}
            value={span}
            onChange={onWindow}
            options={(["7d", "30d", "90d"] as const).map((w) => ({ value: w, label: tDev(`window.${w}`) }))}
          />
          <Button size="compact" variant="ghost" icon={Plug} onClick={onConnect} aria-label={tDev("connect.open")} title={tDev("connect.open")} />
          <span aria-hidden className="mx-0.5 h-5 w-px bg-white/15" />
          <div className="flex items-center gap-1" role="group" aria-label={t("zoom.label")}>
            <Button size="compact" variant="ghost" icon={Minus} onClick={() => zoomBy(-1)} aria-label={t("zoom.out")} title={t("zoom.out")} />
            <Button size="compact" variant="ghost" icon={Scan} onClick={() => zoomAt("fit")} aria-label={t("zoom.fit")} title={t("zoom.fit")} />
            <Button size="compact" variant="ghost" icon={Plus} onClick={() => zoomBy(1)} aria-label={t("zoom.in")} title={t("zoom.in")} />
          </div>
          <Button
            size="compact"
            variant="ghost"
            icon={full ? Shrink : Expand}
            onClick={toggleFull}
            aria-label={full ? t("fullscreen.exit") : t("fullscreen.enter")}
            title={full ? t("fullscreen.exit") : t("fullscreen.enter")}
            data-testid="dev-office-fullscreen"
          />
        </div>

        {feed.length ? (
          <ol
            className="pointer-events-none absolute left-3 z-10 space-y-1"
            style={{ bottom: replayBar ? 76 : 12 }}
            aria-live="polite"
          >
            {/* A phone keeps the scene clear: the latest two only. */}
            {feed.slice(size.w < 640 ? -2 : -4).map((item) => (
              <li
                key={item.id}
                className="flex max-w-[min(360px,80vw)] items-center gap-2 rounded-full bg-[#0a0f1e]/85 py-1 pr-3 pl-1.5 text-micro text-white shadow backdrop-blur-sm"
              >
                <span aria-hidden className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: item.colour }} />
                <span className="truncate">{item.text}</span>
              </li>
            ))}
          </ol>
        ) : null}

        {/* Bottom: playing a day. */}
        {replayBar ? (
          <div
            className={`${chrome} absolute right-3 bottom-3 left-3 z-20 flex flex-wrap items-center gap-3 px-3 py-2 text-white`}
            data-testid="dev-office-replay"
          >
            <span className="font-mono text-[1.05rem] font-bold tracking-wide text-white tabular-nums">{clockLabel(clock)}</span>
            <label className="inline-flex items-center text-caption text-white/85">
              <span className="sr-only">{t("replay.day")}</span>
              <input
                type="date"
                value={day}
                min={earliest}
                max={today}
                onChange={(e) => e.target.value && setDay(e.target.value)}
                className="h-6 rounded-control border border-white/15 bg-white/10 px-1.5 text-micro text-white [color-scheme:dark]"
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
            <span className="text-micro text-white/75">
              {data.events.length ? t("replay.summary", { commits: counts.commits, deliveries: counts.deliveries }) : t("replay.empty")}
              {clock >= DAY_MS ? ` · ${t("replay.ended")}` : ""}
              {data.truncated ? ` · ${t("replay.truncated")}` : ""}
            </span>
          </div>
        ) : null}
      </div>

      <p className="sr-only">{lead}</p>

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
      {/* Rooms and people reachable without a pointer. */}
      <div className="sr-only">
        {data?.rooms.map((r) => (
          <button key={r.id} type="button" onClick={() => pick({ kind: "repository", id: r.id })}>
            {r.project_name ?? r.name}
          </button>
        ))}
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
