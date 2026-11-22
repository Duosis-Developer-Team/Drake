/**
 * Where each person should be, from the office's data: pure, so live and
 * replay decide the same way and are testable without a canvas.
 *
 * Live: someone whose Claude Code reported from a repository in the last
 * minutes works at their desk there; everyone else is in the lobby.
 * Replay: a commit keeps its author at the desk for a while of the day;
 * a delivery is an errand (fetch a box, carry it to the cluster) the engine
 * runs when the clock passes it.
 */

export type Intent = { kind: "lobby" } | { kind: "work"; room: string };

export interface TimedEvent {
  at: number;
  kind: "commit" | "deployment";
  person: string | null;
  repository: string;
  title?: string;
  short?: string | null;
  environment?: string | null;
  agent?: string;
}

export const DAY_MS = 86_400_000;
/** At 1×, a whole day plays in this many seconds. */
export const REPLAY_SECONDS = 80;
/** A commit keeps its author at work for this long of the day before it. */
export const WORK_BEFORE_MS = 50 * 60_000;
/** ...and this long after it. */
export const WORK_AFTER_MS = 10 * 60_000;
export const SPEEDS = [1, 2, 4] as const;

export function parseEvents(
  events: ReadonlyArray<{
    at: string;
    kind: "commit" | "deployment";
    person: string | null;
    repository: string;
    title?: string;
    short?: string | null;
    environment?: string | null;
    agent?: string;
  }>,
): TimedEvent[] {
  return events
    .map((e) => ({ ...e, at: Date.parse(e.at) }))
    .filter((e) => Number.isFinite(e.at))
    .sort((a, b) => a.at - b.at);
}

/** Who is at work where at `t` of a replayed day; absent means "in the lobby". */
export function replayIntents(events: TimedEvent[], t: number): Map<string, Intent> {
  const out = new Map<string, Intent>();
  const latest = new Map<string, TimedEvent>();
  for (const e of events) {
    if (e.kind !== "commit" || !e.person) continue;
    // A commit "starts" its work a while before it is made.
    if (e.at - WORK_BEFORE_MS > t) break;
    if (t <= e.at + WORK_AFTER_MS) {
      const seen = latest.get(e.person);
      if (!seen || seen.at <= e.at) latest.set(e.person, e);
    }
  }
  for (const [person, e] of latest) out.set(person, { kind: "work", room: e.repository });
  return out;
}

/** Who is at work where now, from live presence (one place a person). */
export function liveIntents(live: ReadonlyArray<{ person: string; repository: string }>): Map<string, Intent> {
  const out = new Map<string, Intent>();
  for (const v of live) out.set(v.person, { kind: "work", room: v.repository });
  return out;
}

/** The events whose moment lies in (from, to]. */
export function between(events: TimedEvent[], from: number, to: number): TimedEvent[] {
  if (to <= from) return [];
  const out: TimedEvent[] = [];
  for (const e of events) {
    if (e.at > to) break;
    if (e.at > from) out.push(e);
  }
  return out;
}

/** The replay clock after `seconds` of real time at `speed`. */
export function advance(t: number, seconds: number, speed: number): number {
  return t + seconds * speed * (DAY_MS / REPLAY_SECONDS);
}

/** The viewer's local midnight of a `YYYY-MM-DD` day, as an ISO instant with its offset. */
export function localMidnight(day: string): Date {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(y, (m || 1) - 1, d || 1, 0, 0, 0, 0);
}

export function isoWithOffset(date: Date): string {
  const offset = -date.getTimezoneOffset();
  const sign = offset >= 0 ? "+" : "-";
  const abs = Math.abs(offset);
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}` +
    `${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`
  );
}

export function dayKey(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/**
 * How dark the office is at a local hour (0 = day, up to ~0.5 at night):
 * a soft dusk from 18:00, night until 06:00, dawn to 08:00.
 */
export function nightAt(hour: number): number {
  const h = ((hour % 24) + 24) % 24;
  if (h >= 8 && h < 18) return 0;
  if (h >= 18 && h < 21) return ((h - 18) / 3) * 0.5;
  if (h >= 6 && h < 8) return ((8 - h) / 2) * 0.5;
  return 0.5;
}
