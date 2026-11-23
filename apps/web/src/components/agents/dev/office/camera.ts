/**
 * The office's camera, pure: which zooms keep every pixel whole, the first
 * look, "whole office", a room brought into view, and the move between two
 * views. All in art pixels (the map's) and device pixels (the canvas's).
 */

import type { Density } from "./art";

/** The view's top-left in art pixels, `z` device pixels per art pixel. */
export interface Camera {
  x: number;
  y: number;
  z: number;
}

export interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

/**
 * The zoom levels, in device pixels per art pixel, for the densest character
 * on screen (`density` sprite pixels per art pixel). Below it, only exact
 * divisors (a 4× character shrunk 4:1 or 2:1, block by block), and 3 for 4×
 * art (drawn one device pixel per sprite pixel); from it up, every level at
 * which a whole shrink leaves whole pixels: for 4× art every even level (at
 * 6, shrunk 2:1 and 3 pixels a pixel), else the density's multiples.
 */
export function zoomLevels(density: Density, dpr: number): number[] {
  const out = [1, 2, 4].filter((z) => z < density && density % z === 0);
  if (density === 4) out.push(3);
  const top = Math.max(density * 2, Math.round(8 * dpr));
  const step = density === 4 ? 2 : density;
  for (let z = density; z <= top; z += step) out.push(z);
  return out.sort((a, b) => a - b);
}

/**
 * The largest zoom at which the whole office fits the stage, width and
 * height ("whole office"). A stage too small for any (a phone) gets the
 * smallest, and pans.
 */
export function fitZoom(density: Density, dpr: number, cssWidth: number, cssHeight: number, world: { w: number; h: number }): number {
  const levels = zoomLevels(density, dpr);
  const w = cssWidth * dpr;
  const h = cssHeight * dpr;
  return levels.filter((z) => world.w * z <= w && world.h * z <= h).pop() ?? levels[0];
}

/**
 * The first look's zoom: at least a step closer than the whole office, and
 * close enough that the office spans the stage's whole width — no empty bands
 * at the sides. `deviceWidth` is the stage's width in device pixels.
 */
export function defaultZoom(levels: number[], fit: number, worldWidth: number, deviceWidth: number): number {
  const from = Math.min(levels.length - 1, Math.max(0, levels.indexOf(fit)) + 1);
  for (let i = from; i < levels.length; i += 1) if (worldWidth * levels[i] >= deviceWidth) return levels[i];
  return levels[levels.length - 1];
}

const clamp = (v: number, lo: number, hi: number) => Math.min(Math.max(v, lo), hi);

/**
 * The camera kept inside `bounds` (the exterior when there is one, else the
 * office): along an axis where the view is larger than the bounds, centred
 * on them.
 */
export function clampCamera(cam: Camera, bounds: { x?: number; y?: number; w: number; h: number }, viewW: number, viewH: number): Camera {
  const bx = bounds.x ?? 0;
  const by = bounds.y ?? 0;
  const vw = viewW / cam.z;
  const vh = viewH / cam.z;
  const x = vw >= bounds.w ? bx + (bounds.w - vw) / 2 : clamp(cam.x, bx, bx + bounds.w - vw);
  const y = vh >= bounds.h ? by + (bounds.h - vh) / 2 : clamp(cam.y, by, by + bounds.h - vh);
  return { x, y, z: cam.z };
}

/** "Whole office": the office centred at `z`, kept inside the bounds. */
export function fitCamera(office: { w: number; h: number }, bounds: Box, z: number, viewW: number, viewH: number): Camera {
  return clampCamera({ x: office.w / 2 - viewW / 2 / z, y: office.h / 2 - viewH / 2 / z, z }, bounds, viewW, viewH);
}

/**
 * The first look at `z`: across, centred on the office and never past its
 * walls; down, the band where the rooms of interest are (where people are, or
 * the cluster and the lobby), kept inside the office too.
 */
export function defaultCamera(office: { w: number; h: number }, z: number, viewW: number, viewH: number, interest: Box[]): Camera {
  const vw = viewW / z;
  const vh = viewH / z;
  let centre = office.h / 2;
  if (interest.length) {
    const top = Math.min(...interest.map((r) => r.y));
    const bottom = Math.max(...interest.map((r) => r.y + r.h));
    // All of them when they fit; else their weighted middle.
    centre = bottom - top <= vh ? (top + bottom) / 2 : interest.reduce((sum, r) => sum + r.y + r.h / 2, 0) / interest.length;
  }
  const x = vw >= office.w ? (office.w - vw) / 2 : clamp(office.w / 2 - vw / 2, 0, office.w - vw);
  const y = vh >= office.h ? (office.h - vh) / 2 : clamp(centre - vh / 2, 0, office.h - vh);
  return { x, y, z };
}

/**
 * A room brought into view: the largest level at which it fits with a
 * margin around it, centred in the stage above `reserveBottom` device pixels
 * (the caption).
 */
export function frameRoom(room: Box, levels: number[], viewW: number, viewH: number, reserveBottom: number, margin = 12): Camera {
  const height = Math.max(1, viewH - reserveBottom);
  const z = levels.filter((l) => (room.w + 2 * margin) * l <= viewW && (room.h + 2 * margin) * l <= height).pop() ?? levels[0];
  return { x: room.x + room.w / 2 - viewW / 2 / z, y: room.y + room.h / 2 - height / 2 / z, z };
}

/** Slow in, slow out. */
export function ease(t: number): number {
  return t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2;
}

/**
 * The view part way (`t`, 0 to 1, eased) from one camera to another: the
 * zoom moves evenly in scale, the middle of the view in a straight line. The
 * scene is drawn at the nearest whole level at or below that scale, and
 * `lift` (≥ 1) enlarges it the rest of the way; at the end, lift is 1 and
 * every pixel is whole again.
 */
export function tween(from: Camera, to: Camera, t: number, viewW: number, viewH: number, levels: number[]): { camera: Camera; lift: number } {
  if (t <= 0) return { camera: from, lift: 1 };
  if (t >= 1) return { camera: to, lift: 1 };
  const mid = (c: Camera) => ({ x: c.x + viewW / 2 / c.z, y: c.y + viewH / 2 / c.z });
  const a = mid(from);
  const b = mid(to);
  const s = Math.exp(Math.log(from.z) + (Math.log(to.z) - Math.log(from.z)) * t);
  const z = levels.filter((l) => l <= s + 1e-9).pop() ?? levels[0];
  const c = { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t };
  return { camera: { x: c.x - viewW / 2 / z, y: c.y - viewH / 2 / z, z }, lift: s / z };
}
