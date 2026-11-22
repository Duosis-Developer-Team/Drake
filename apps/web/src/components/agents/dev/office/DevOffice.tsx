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

import { Maximize2, Minus, Pause, Play, Plus } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { TracesisMark } from "@/components/shell/Brand";
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
import { headFromInitials, headFromPortrait, headFromSprite, loadArt, type Art } from "./art";
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
import { layoutOffice, roomAt, type Point, type Rect, type World } from "./world";

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

/** The zoom levels: multiples of `step` (2 when a character is drawn at double density). */
export function zoomLevels(step: 1 | 2, dpr: number): number[] {
  const top = Math.max(step * 2, Math.round(8 * dpr));
  const out: number[] = [];
  for (let z = step; z <= top; z += step) out.push(z);
  return out;
}

/**
 * The zoom a first look opens at: about one CSS pixel per art pixel (people
 * stay readable; a phone pans), larger when the whole width fits at more.
 */
export function defaultZoom(step: 1 | 2, dpr: number, cssWidth: number, worldW: number): number {
  const levels = zoomLevels(step, dpr);
  const near = levels.reduce((best, z) => (Math.abs(z - dpr) <= Math.abs(best - dpr) ? z : best), levels[0]);
  return Math.max(near, fitZoom(step, dpr, cssWidth, worldW));
}

/** The largest zoom at which the whole width of the office fits. */
export function fitZoom(step: 1 | 2, dpr: number, cssWidth: number, worldW: number): number {
  const levels = zoomLevels(step, dpr);
  const device = cssWidth * dpr;
  return levels.filter((z) => worldW * z <= device).pop() ?? levels[0];
}

export function clampCamera(cam: Camera, world: { w: number; h: number }, viewW: number, viewH: number): Camera {
  const vw = viewW / cam.z;
  const vh = viewH / cam.z;
  const x = vw >= world.w ? (world.w - vw) / 2 : Math.min(Math.max(0, cam.x), world.w - vw);
  const y = vh >= world.h ? (world.h - vh) / 2 : Math.min(Math.max(0, cam.y), world.h - vh);
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

export function DevOffice({ window: span, onPick }: { window: DevWindow; onPick: (pick: Pick) => void }) {
  const t = useT("devOffice");
  const reduced = usePrefersReducedMotion();
  const [mode, setMode] = useState<Mode>("live");
  const [day, setDay] = useState(() => dayKey(new Date()));
  const [state, setState] = useState<{ key: string; data: OfficeData | null; failed: boolean } | null>(null);
  const [art, setArt] = useState<Art | null>(null);
  const [playing, setPlaying] = useState(true);
  const [speed, setSpeed] = useState<(typeof SPEEDS)[number]>(1);
  const [clock, setClock] = useState(0);
  const [camera, setCamera] = useState<Camera | null>(null);
  const [size, setSize] = useState({ w: 0, dpr: 1 });
  const [hover, setHover] = useState<{ key: string; x: number; y: number } | null>(null);
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
  const roomsKey = data ? data.rooms.map((r) => `${r.id}:${r.desks.join(",")}`).join(";") : "";
  const world = useMemo<World | null>(
    () => (data && art ? layoutOffice(data.rooms.map((r) => ({ id: r.id, desks: r.desks })), art.backdoorX) : null),
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
  const step: 1 | 2 = data && art && data.people.some((p) => art.bodies.get(p.key)?.density === 2) ? 2 : 1;

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

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const measure = () => {
      const dpr = Math.max(1, Math.round((globalThis.devicePixelRatio || 1) * 4) / 4);
      const w = Math.floor(el.clientWidth);
      setSize((prev) => (prev.w === w && prev.dpr === dpr ? prev : { w, dpr }));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const deviceW = Math.round(size.w * size.dpr);
  const opening = world && size.w ? defaultZoom(step, size.dpr, size.w, world.w) : step;
  const z = camera && camera.z % step === 0 ? camera.z : opening;
  const viewCssH = world
    ? Math.max(MIN_HEIGHT, Math.min(Math.round((world.h * z) / size.dpr), Math.round((globalThis.innerHeight || 900) * 0.72)))
    : MIN_HEIGHT;
  const deviceH = Math.round(viewCssH * size.dpr);

  useEffect(() => {
    if (!world || !deviceW) return;
    setCamera((prev) => {
      if (prev && prev.z % step === 0) return clampCamera(prev, world, deviceW, deviceH);
      // Open on the cluster with the lobby in view.
      const zz = prev ? Math.max(step, Math.round(prev.z / step) * step) : opening;
      const cx = world.cluster.x + world.cluster.w / 2 - deviceW / zz / 2;
      const cy = world.lobby.y + world.lobby.h + 24 - deviceH / zz;
      return clampCamera({ x: cx, y: cy, z: zz }, world, deviceW, deviceH);
    });
  }, [world, deviceW, deviceH, opening, step]);

  const zoomAt = useCallback(
    (next: number | "fit", focus?: { x: number; y: number }) => {
      if (!world) return;
      setCamera((prev) => {
        const cam = prev ?? { x: 0, y: 0, z: opening };
        const levels = zoomLevels(step, size.dpr);
        const nz =
          next === "fit"
            ? fitZoom(step, size.dpr, size.w, world.w)
            : Math.min(levels[levels.length - 1], Math.max(levels[0], next));
        // Keep the point under the focus (device pixels in the view) where it is.
        const fx = focus?.x ?? deviceW / 2;
        const fy = focus?.y ?? deviceH / 2;
        const wx = cam.x + fx / cam.z;
        const wy = cam.y + fy / cam.z;
        return clampCamera({ x: wx - fx / nz, y: wy - fy / nz, z: nz }, world, deviceW, deviceH);
      });
    },
    [world, opening, step, size.dpr, size.w, deviceW, deviceH],
  );
  const zoomBy = (direction: 1 | -1, focus?: { x: number; y: number }) => {
    const levels = zoomLevels(step, size.dpr);
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
        setCamera(clampCamera({ x: d.cam.x - dx * k, y: d.cam.y - dy * k, z: d.cam.z }, world, deviceW, deviceH));
      }
      return;
    }
    const p = toWorld(e.clientX, e.clientY);
    const engine = engineRef.current;
    if (!p || !engine) return;
    const key = engine.hit(p);
    engine.hover = key;
    const rect = e.currentTarget.getBoundingClientRect();
    setHover(key ? { key, x: e.clientX - rect.left, y: e.clientY - rect.top } : null);
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
    if (key) return onPick({ kind: "person", key });
    const room = roomAt(world, p);
    if (room?.kind === "product") onPick({ kind: "repository", id: room.id });
  };
  const onPointerLeave = () => {
    if (engineRef.current) engineRef.current.hover = null;
    setHover(null);
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
  const nameSize = Math.max(10, Math.min(15, Math.round(11 * scale)));

  return (
    <div className="space-y-3" data-testid="dev-office">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
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
            <span className="inline-flex items-center gap-2 text-caption text-ink-secondary" data-testid="dev-office-live">
              <span aria-hidden className="relative flex h-2 w-2">
                <span className={`absolute inline-flex h-full w-full rounded-full bg-[#47d27b] opacity-60 ${reduced ? "" : "animate-ping"}`} />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-[#47d27b]" />
              </span>
              {t("live.working", { count: atWork })}
              <span className="text-micro text-ink-muted">· {t("live.refresh")}</span>
            </span>
          ) : (
            <label className="inline-flex items-center gap-2 text-caption text-ink-secondary">
              {t("replay.day")}
              <input
                type="date"
                value={day}
                min={earliest}
                max={today}
                onChange={(e) => e.target.value && setDay(e.target.value)}
                className="h-8 rounded-control border border-border bg-surface px-2 text-caption text-ink"
                data-testid="dev-office-day"
              />
            </label>
          )}
        </div>
        <div className="flex items-center gap-1" role="group" aria-label={t("zoom.label")}>
          <Button size="compact" icon={Minus} onClick={() => zoomBy(-1)} aria-label={t("zoom.out")} title={t("zoom.out")} />
          <Button size="compact" icon={Maximize2} onClick={() => zoomAt("fit")} aria-label={t("zoom.fit")} title={t("zoom.fit")} />
          <Button size="compact" icon={Plus} onClick={() => zoomBy(1)} aria-label={t("zoom.in")} title={t("zoom.in")} />
        </div>
      </div>

      {mode === "live" && data && !data.configured.telemetry ? (
        <p className="rounded-control border border-border bg-surface-2 px-3 py-2 text-caption text-ink-secondary">{t("live.notConnected")}</p>
      ) : null}

      <div ref={wrapRef} className="relative overflow-hidden rounded-panel border border-border bg-[#0b0e18]" style={{ height: viewCssH }}>
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
            {world.rooms.map((room) => {
              const r = roomsById.get(room.id);
              if (!r) return null;
              const name = r.project_name ?? r.name;
              return (
                <div key={room.id}>
                  {/* The project's mark on the room's plaque. */}
                  <div className="absolute flex items-center justify-center overflow-hidden" style={box(room.plaque)}>
                    <span className="flex items-center justify-center" style={{ transform: `scale(${Math.min(1.4, Math.max(0.55, scale * 0.9))})` }}>
                      <RepositoryMark repo={asRepository(r)} size="sm" />
                    </span>
                  </div>
                  {/* The room's name, on its top edge. */}
                  <div
                    className="absolute flex justify-center"
                    style={{ left: (room.x - camX) * scale, top: (room.y - camY) * scale - nameSize * 0.6, width: room.w * scale }}
                  >
                    <span
                      className="max-w-[90%] truncate rounded-full border border-[#d1a345]/60 bg-[#0f1424]/90 px-2.5 py-0.5 font-semibold text-[#f3e2b3] shadow"
                      style={{ fontSize: nameSize, lineHeight: 1.25 }}
                      title={r.catalogued ? r.full_name : `${r.full_name} · ${t("rooms.notCatalogued")}`}
                    >
                      {name}
                      {r.catalogued ? null : <span className="ml-1.5 font-normal text-[#8a93a8]">· {t("rooms.notCatalogued")}</span>}
                    </span>
                  </div>
                </div>
              );
            })}
            <div
              className="absolute flex items-center justify-center font-bold tracking-[0.14em] text-[#2a1d05] uppercase"
              style={{ ...box(world.cluster.plaque), fontSize: Math.max(7, Math.round(7.5 * scale)), lineHeight: 1 }}
            >
              {t("rooms.cluster")}
            </div>
            <div className="absolute flex items-center justify-center" style={box(world.lobby.plaque)}>
              <TracesisMark height={Math.max(8, Math.round(world.lobby.plaque.h * scale * 0.62))} />
            </div>
            {world.vacant.map((v, i) => (
              <div
                key={`vacant-${i}`}
                className="absolute flex justify-center"
                style={{ left: (v.x - camX) * scale, top: (v.y - camY) * scale - nameSize * 0.6, width: v.w * scale }}
              >
                <span className="rounded-full bg-[#0f1424]/80 px-2.5 py-0.5 text-[#8a93a8]" style={{ fontSize: nameSize, lineHeight: 1.25 }}>
                  {t("rooms.vacant")}
                </span>
              </div>
            ))}
            <div
              className="absolute flex justify-center"
              style={{ left: (world.lobby.x - camX) * scale, top: (world.lobby.y - camY) * scale - nameSize * 0.6, width: world.lobby.w * scale }}
            >
              <span
                className="rounded-full bg-[#d1a345] px-2.5 py-0.5 font-bold tracking-[0.12em] text-[#1a1409] uppercase shadow"
                style={{ fontSize: nameSize - 1, lineHeight: 1.25 }}
              >
                {t("rooms.lobby")}
              </span>
            </div>
          </div>
        ) : null}
        {hover && data ? (
          <div
            className="pointer-events-none absolute z-10 rounded-control border border-border bg-surface px-2.5 py-1.5 text-micro shadow-lg"
            style={{ left: Math.min(hover.x + 14, size.w - 220), top: Math.max(8, hover.y - 46) }}
          >
            <p className="font-semibold text-ink">{data.people.find((p) => p.key === hover.key)?.name ?? hover.key}</p>
            <p className="text-ink-secondary">
              {(() => {
                const place = places.find((p) => p.person === hover.key);
                return place ? placeText(place.place, place.moving, place.carrying) : "";
              })()}
            </p>
          </div>
        ) : null}
        {feed.length ? (
          <ol className="pointer-events-none absolute bottom-3 left-3 z-10 space-y-1" aria-live="polite">
            {/* A phone keeps the scene clear: the latest two only. */}
            {feed.slice(size.w < 640 ? -2 : -4).map((item) => (
              <li
                key={item.id}
                className="flex max-w-[min(360px,80vw)] items-center gap-2 rounded-full bg-[#0f1424]/90 py-1 pr-3 pl-1.5 text-micro text-white shadow"
              >
                <span aria-hidden className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: item.colour }} />
                <span className="truncate">{item.text}</span>
              </li>
            ))}
          </ol>
        ) : null}
        {mode === "replay" && data ? (
          <div className="absolute top-3 right-3 z-10 rounded-control bg-[#0f1424]/90 px-3 py-1.5 font-mono text-[1.05rem] font-bold tracking-wide text-white shadow">
            {clockLabel(clock)}
          </div>
        ) : null}
      </div>

      {mode === "replay" && data ? (
        <div className="flex flex-wrap items-center gap-3 rounded-panel border border-border bg-surface px-3 py-2.5" data-testid="dev-office-replay">
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
          <div className="relative min-w-[200px] flex-1">
            <div aria-hidden className="pointer-events-none absolute inset-x-0 -top-1 h-2">
              {ticks.map((tick, i) => (
                <span
                  key={i}
                  className="absolute top-0 w-[2px] rounded-full"
                  style={{
                    left: `${Math.min(100, Math.max(0, tick.at * 100))}%`,
                    height: tick.kind === "deployment" ? 8 : 5,
                    background: tick.colour,
                    opacity: 0.85,
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
          <span className="text-micro text-ink-muted">
            {events.length ? t("replay.summary", { commits: counts.commits, deliveries: counts.deliveries }) : t("replay.empty")}
            {clock >= DAY_MS ? ` · ${t("replay.ended")}` : ""}
          </span>
          {data.truncated ? <span className="text-micro text-ink-muted">{t("replay.truncated")}</span> : null}
        </div>
      ) : null}

      <p className="text-micro text-ink-muted">{t("hint")}</p>

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
          <button key={r.id} type="button" onClick={() => onPick({ kind: "repository", id: r.id })}>
            {r.project_name ?? r.name}
          </button>
        ))}
        {data?.people.map((p) => (
          <button key={p.key} type="button" onClick={() => onPick({ kind: "person", key: p.key })}>
            {p.name}
          </button>
        ))}
      </div>
    </div>
  );
}
