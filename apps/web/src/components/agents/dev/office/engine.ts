/**
 * The office's simulation and drawing: people walk the corridors to their
 * desks, type, take coffee in the lobby, and carry a delivery to the cluster.
 *
 * The world is drawn at its native size (one canvas pixel per art pixel);
 * the component scales it to the screen by whole device pixels, then this
 * draws the text (name tags, bubbles) at full resolution on top.
 */

import { BODY_ROWS, reduced, type Art, type BodyRow, type Sprite } from "./art";
import type { Intent } from "./director";
import { DESK_SURFACE, findPath, SEATED, SIZE, type Desk, type Point, type Room, type World } from "./world";

type Dir = "down" | "left" | "right" | "up";

interface Errand {
  room: string;
  stage: "fetch" | "carry";
}

interface Actor {
  key: string;
  name: string;
  colour: string;
  x: number;
  y: number;
  path: Point[];
  dir: Dir;
  walked: number;
  /** What the person is doing on arrival. */
  pose: "idle" | "sit" | "coffee";
  intent: Intent;
  goal: string;
  desk: Desk | null;
  errand: Errand | null;
  carrying: boolean;
  /** When an idle person next wanders (seconds of engine time). */
  wander: number;
  seed: number;
}

interface Flash {
  start: number;
  until: number;
}

interface Bubble {
  person: string;
  text: string;
  colour: string;
  until: number;
}

const SPEED = 74;
const IDLE_MIN = 9;
const IDLE_SPREAD = 16;

function hashOf(key: string): number {
  let h = 2166136261;
  for (let i = 0; i < key.length; i += 1) h = Math.imul(h ^ key.charCodeAt(i), 16777619);
  return (h >>> 0) / 4294967296;
}

export class OfficeEngine {
  readonly world: World;
  private art: Art;
  private still: HTMLCanvasElement;
  private actors = new Map<string, Actor>();
  private flashes: Flash[] = [];
  private bubbles: Bubble[] = [];
  private time = 0;
  private reduced: boolean;
  night = 0;
  hover: string | null = null;
  /** Device pixels per art pixel of the last draw. */
  private zoom = 1;
  private rooms: Map<string, Room>;

  constructor(world: World, art: Art, reducedMotion: boolean) {
    this.world = world;
    this.art = art;
    this.reduced = reducedMotion;
    this.rooms = new Map([...world.rooms, world.cluster, world.lobby].map((r) => [r.id, r]));
    this.still = this.paintStill();
  }

  /* ------------------------------------------------------------ the set */

  private paintStill(): HTMLCanvasElement {
    const { w, h } = this.world;
    const c = document.createElement("canvas");
    c.width = w;
    c.height = h;
    const ctx = c.getContext("2d");
    if (!ctx) return c;
    ctx.imageSmoothingEnabled = false;
    for (let y = 0; y < h; y += SIZE.tile) for (let x = 0; x < w; x += SIZE.tile) ctx.drawImage(this.art.corridor, x, y);
    // The open floor beside the lobby: a rug under the free desks.
    for (const p of this.world.plazas) {
      ctx.fillStyle = "rgba(20,26,44,0.55)";
      ctx.fillRect(p.x + 24, p.y + 40, p.w - 48, p.h - 72);
      ctx.strokeStyle = "rgba(209,163,69,0.55)";
      ctx.lineWidth = 2;
      ctx.strokeRect(p.x + 25, p.y + 41, p.w - 50, p.h - 74);
    }
    for (const room of this.world.rooms)
      ctx.drawImage(room.backdoor ? this.art.productBackdoor : this.art.productOpen, room.x, room.y);
    for (const v of this.world.vacant) {
      ctx.drawImage(this.art.productOpen, v.x, v.y);
      ctx.fillStyle = "rgba(8,10,20,0.55)";
      ctx.fillRect(v.x, v.y, v.w, v.h);
    }
    ctx.drawImage(this.art.cluster, this.world.cluster.x, this.world.cluster.y);
    ctx.drawImage(this.art.lobby, this.world.lobby.x, this.world.lobby.y);
    // Soft shade where the walls meet the corridor floor.
    ctx.fillStyle = "rgba(0,0,0,0.22)";
    for (const room of [...this.world.rooms, this.world.cluster, this.world.lobby])
      ctx.fillRect(room.x, room.y + room.h, room.w, 4);
    return c;
  }

  setHead(key: string, sprites: Sprite[]) {
    this.art.heads.set(key, sprites);
  }

  hasHead(key: string): boolean {
    return this.art.heads.has(key);
  }

  /** Who is in the office (others leave); a newcomer starts at a lobby spot. */
  setPeople(people: ReadonlyArray<{ key: string; name: string; colour: string }>) {
    const keep = new Set(people.map((p) => p.key));
    for (const key of [...this.actors.keys()]) if (!keep.has(key)) this.actors.delete(key);
    people.forEach((p, i) => {
      const existing = this.actors.get(p.key);
      if (existing) {
        existing.name = p.name;
        existing.colour = p.colour;
        return;
      }
      const spot = this.world.lobbySpots[i % this.world.lobbySpots.length];
      const seed = hashOf(p.key);
      this.actors.set(p.key, {
        key: p.key,
        name: p.name,
        colour: p.colour,
        x: spot.x,
        y: spot.y,
        path: [],
        dir: "down",
        walked: 0,
        pose: "idle",
        intent: { kind: "lobby" },
        goal: `lobby:${i}`,
        desk: null,
        errand: null,
        carrying: false,
        wander: this.time + IDLE_MIN * seed,
        seed,
      });
    });
  }

  private deskFor(actor: Actor, roomId: string): Desk | null {
    const room = this.rooms.get(roomId);
    if (!room || room.kind !== "product") return null;
    const own = room.desks.find((d) => d.owner === actor.key);
    if (own) return own;
    const taken = new Set(
      [...this.actors.values()].filter((a) => a !== actor && a.desk).map((a) => a.desk as Desk),
    );
    return room.desks.find((d) => !d.owner && !taken.has(d)) ?? room.desks.find((d) => !taken.has(d)) ?? null;
  }

  private lobbySpot(actor: Actor): { goal: string; at: Point } {
    const spots = this.world.lobbySpots;
    const taken = new Set([...this.actors.values()].filter((a) => a !== actor).map((a) => a.goal));
    const start = Math.floor(actor.seed * spots.length);
    for (let i = 0; i < spots.length; i += 1) {
      const k = (start + i) % spots.length;
      if (!taken.has(`lobby:${k}`)) return { goal: `lobby:${k}`, at: spots[k] };
    }
    return { goal: `lobby:${start}`, at: spots[start] };
  }

  private go(actor: Actor, goal: string, at: Point, pose: Actor["pose"], instant: boolean) {
    actor.goal = goal;
    actor.pose = pose;
    if (instant || this.reduced) {
      actor.x = at.x;
      actor.y = at.y;
      actor.path = [];
      actor.dir = "down";
      return;
    }
    const path = findPath(this.world, { x: actor.x, y: actor.y }, at);
    actor.path = path.length ? path : [at];
  }

  private follow(actor: Actor, instant: boolean) {
    if (actor.errand) return;
    actor.carrying = false;
    if (actor.intent.kind === "work") {
      const desk = this.deskFor(actor, actor.intent.room);
      if (desk) {
        actor.desk = desk;
        const goal = `desk:${actor.intent.room}:${desk.x},${desk.y}`;
        if (actor.goal !== goal) this.go(actor, goal, desk.seat, "sit", instant);
        return;
      }
    }
    actor.desk = null;
    if (!actor.goal.startsWith("lobby:") && !actor.goal.startsWith("coffee")) {
      const spot = this.lobbySpot(actor);
      this.go(actor, spot.goal, spot.at, "idle", instant);
      actor.wander = this.time + IDLE_MIN + actor.seed * IDLE_SPREAD;
    }
  }

  /** Where everyone should be; `instant` places them there (a jump in time). */
  setIntents(intents: Map<string, Intent>, instant = false) {
    for (const actor of this.actors.values()) {
      const next = intents.get(actor.key) ?? { kind: "lobby" as const };
      const same =
        next.kind === actor.intent.kind && (next.kind !== "work" || (actor.intent.kind === "work" && next.room === actor.intent.room));
      actor.intent = next;
      if (instant) {
        actor.errand = null;
        actor.carrying = false;
        actor.goal = "";
      }
      if (!same || instant) this.follow(actor, instant);
    }
  }

  /** A delivery of `room`'s work: its carrier fetches a box and takes it to the cluster. */
  deliver(person: string | null, room: string) {
    const actor = person ? this.actors.get(person) : undefined;
    if (!actor || this.reduced || !this.rooms.has(room)) {
      this.land();
      return;
    }
    actor.errand = { room, stage: "fetch" };
    const desk = this.deskFor(actor, room);
    const at = desk?.seat ?? this.rooms.get(room)?.door ?? this.world.drop;
    actor.desk = desk;
    this.go(actor, `errand:${room}`, at, "idle", false);
  }

  private land() {
    this.flashes.push({ start: this.time, until: this.time + 2.6 });
  }

  say(person: string | null, text: string, colour: string) {
    if (!person || !this.actors.has(person)) return;
    this.bubbles = this.bubbles.filter((b) => b.person !== person);
    this.bubbles.push({ person, text, colour, until: this.time + 2.8 });
  }

  /* ------------------------------------------------------------ the clock */

  tick(dt: number) {
    const step = Math.min(dt, 0.1);
    this.time += step;
    this.flashes = this.flashes.filter((f) => f.until > this.time);
    this.bubbles = this.bubbles.filter((b) => b.until > this.time);
    for (const actor of this.actors.values()) {
      if (actor.path.length) this.walk(actor, step);
      else this.arrived(actor);
    }
  }

  private walk(actor: Actor, dt: number) {
    let budget = SPEED * dt;
    while (budget > 0 && actor.path.length) {
      const next = actor.path[0];
      const dx = next.x - actor.x;
      const dy = next.y - actor.y;
      const d = Math.hypot(dx, dy);
      if (d > 0.01) actor.dir = Math.abs(dx) > Math.abs(dy) * 1.1 ? (dx < 0 ? "left" : "right") : dy < 0 ? "up" : "down";
      if (d <= budget) {
        actor.x = next.x;
        actor.y = next.y;
        actor.walked += d;
        budget -= d;
        actor.path.shift();
      } else {
        actor.x += (dx / d) * budget;
        actor.y += (dy / d) * budget;
        actor.walked += budget;
        budget = 0;
      }
    }
    if (!actor.path.length) actor.dir = "down";
  }

  private arrived(actor: Actor) {
    const errand = actor.errand;
    if (errand?.stage === "fetch") {
      errand.stage = "carry";
      actor.carrying = true;
      this.go(actor, "errand:drop", this.world.drop, "idle", false);
      return;
    }
    if (errand?.stage === "carry") {
      actor.carrying = false;
      actor.errand = null;
      this.land();
      actor.goal = "";
      this.follow(actor, false);
      return;
    }
    // Idle in the lobby: now and then, a walk to the coffee machine and back.
    if (!this.reduced && actor.intent.kind === "lobby" && this.time > actor.wander) {
      actor.wander = this.time + IDLE_MIN + hashOf(actor.key + Math.floor(this.time)) * IDLE_SPREAD;
      if (actor.goal.startsWith("coffee")) {
        const spot = this.lobbySpot(actor);
        this.go(actor, spot.goal, spot.at, "idle", false);
      } else if (![...this.actors.values()].some((a) => a !== actor && a.goal.startsWith("coffee"))) {
        this.go(actor, "coffee", this.world.coffee, "coffee", false);
      } else {
        const spot = this.lobbySpot(actor);
        if (spot.goal !== actor.goal) this.go(actor, spot.goal, spot.at, "idle", false);
      }
    }
  }

  /* ------------------------------------------------------------ drawing */

  private frame(actor: Actor): { row: BodyRow; index: number; flip: boolean } {
    const moving = actor.path.length > 0;
    if (moving) {
      const base = actor.carrying ? "carry" : "walk";
      const side = actor.dir === "left" || actor.dir === "right";
      const row = `${base}-${side ? "left" : actor.dir}` as BodyRow;
      // About one frame per 130 ms at walking pace (the art's timing).
      return { row, index: Math.floor(actor.walked / 9.5) % BODY_ROWS[row], flip: actor.dir === "right" };
    }
    if (actor.carrying) return { row: "carry-down", index: 0, flip: false };
    const animate = !this.reduced;
    const beat = (ms: number) => (animate ? Math.floor((this.time * 1000) / ms + actor.seed * 7) % 2 : 0);
    if (this.seated(actor)) return { row: "sit-type", index: beat(220), flip: false };
    if (actor.pose === "coffee") return { row: "coffee", index: beat(500), flip: false };
    return { row: "idle-down", index: beat(580), flip: false };
  }

  private seated(actor: Actor): boolean {
    return actor.pose === "sit" && !actor.path.length && !actor.errand && actor.desk !== null;
  }

  /** Where an actor's feet are drawn: in the chair when seated, else where they stand. */
  private feet(actor: Actor): Point {
    if (this.seated(actor) && actor.desk) return { x: actor.desk.x + SEATED.x, y: actor.desk.y + SEATED.y };
    return { x: Math.round(actor.x), y: Math.round(actor.y) };
  }

  private drawActor(ctx: CanvasRenderingContext2D, actor: Actor) {
    const { row, index, flip } = this.frame(actor);
    const set = this.art.bodies.get(actor.key) ?? this.art.body;
    const frame = set.frames[row][index] ?? set.frames["idle-down"][0];
    const { x, y } = this.feet(actor);
    // A dense character (2× or 4× the rooms' pixels) takes the same room in the
    // office as any other. Zoomed in far enough, each of its pixels covers whole
    // device pixels; below that, it is drawn from a copy shrunk by an exact
    // whole factor (each 2 × 2 or 4 × 4 block averaged), never resampled.
    const d = set.density;
    const factor = this.zoom < d && d % this.zoom === 0 ? d / this.zoom : 1;
    const fw = SIZE.frame.w;
    const fh = SIZE.frame.h;
    const left = x - set.anchor.x / d;
    const top = y - set.anchor.y / d;
    const seated = this.seated(actor);
    if (!seated) {
      // A shadow, and a ring in the person's colour.
      ctx.fillStyle = "rgba(0,0,0,0.28)";
      ctx.beginPath();
      ctx.ellipse(x, y, 10, 3.5, 0, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = actor.colour;
      ctx.globalAlpha = this.hover === actor.key ? 1 : 0.85;
      ctx.lineWidth = this.hover === actor.key ? 2 : 1.5;
      ctx.beginPath();
      ctx.ellipse(x, y, 12, 4.5, 0, 0, Math.PI * 2);
      ctx.stroke();
      ctx.globalAlpha = 1;
    }
    const draw = (image: Sprite, dx: number, dy: number, w: number, h: number, mirror: boolean) => {
      if (!mirror) {
        ctx.drawImage(image, dx, dy, w, h);
        return;
      }
      ctx.save();
      ctx.translate(dx + w, dy);
      ctx.scale(-1, 1);
      ctx.drawImage(image, 0, 0, w, h);
      ctx.restore();
    };
    draw(factor > 1 ? reduced(frame.image, factor) : frame.image, left, top, fw, fh, flip);
    const heads = set.baked ? undefined : this.art.heads.get(actor.key);
    if (heads) {
      const moving = actor.path.length > 0;
      const facing: Dir = moving ? actor.dir : "down";
      const head = facing === "up" ? heads[2] : facing === "down" ? heads[0] : heads[1];
      const headX = flip ? fw - frame.headX : frame.headX;
      draw(head, left + headX - SIZE.head / 2, top + frame.headTop - 4, SIZE.head, SIZE.head, facing === "left");
    }
  }

  /** Everyone in the office now: where they stand, their colour, whether they are on the move. */
  people(): { key: string; x: number; y: number; colour: string; moving: boolean; working: boolean }[] {
    return [...this.actors.values()].map((a) => {
      const f = this.feet(a);
      return { key: a.key, x: f.x, y: f.y, colour: a.colour, moving: a.path.length > 0, working: a.intent.kind === "work" };
    });
  }

  /**
   * The scene through a camera, straight onto the screen canvas: `z` device
   * pixels per art pixel (whole), the view's top-left at (`x`, `y`) in art
   * pixels. Every sprite pixel lands on whole device pixels.
   */
  draw(ctx: CanvasRenderingContext2D, view: { x: number; y: number; z: number }) {
    const { z } = view;
    this.zoom = z;
    ctx.setTransform(z, 0, 0, z, -Math.round(view.x * z), -Math.round(view.y * z));
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(this.still, 0, 0);
    type Item = { y: number; paint: () => void };
    const items: Item[] = [];
    const desk = this.art.desk;
    for (const d of [...this.world.rooms.flatMap((r) => r.desks), ...this.world.hotDesks]) {
      items.push({ y: d.y, paint: () => ctx.drawImage(desk, 0, 0, SIZE.desk.w, DESK_SURFACE, d.x, d.y, SIZE.desk.w, DESK_SURFACE) });
      items.push({
        y: d.y + 15,
        paint: () =>
          ctx.drawImage(
            desk,
            0,
            DESK_SURFACE,
            SIZE.desk.w,
            SIZE.desk.h - DESK_SURFACE,
            d.x,
            d.y + DESK_SURFACE,
            SIZE.desk.w,
            SIZE.desk.h - DESK_SURFACE,
          ),
      });
    }
    for (const actor of this.actors.values()) {
      // Seated: after the chair, before the desk's top and its monitor.
      const y = this.seated(actor) && actor.desk ? actor.desk.y + 10 : actor.y;
      items.push({ y, paint: () => this.drawActor(ctx, actor) });
    }
    const landing = this.flashes.length > 0;
    if (landing) {
      const box = this.art.box;
      items.push({ y: this.world.drop.y, paint: () => ctx.drawImage(box, Math.round(this.world.drop.x - 8), Math.round(this.world.drop.y - 14)) });
    }
    items.sort((a, b) => a.y - b.y);
    for (const item of items) item.paint();

    // Night falls over everything; screens and racks stay lit.
    if (this.night > 0) {
      ctx.fillStyle = `rgba(6,10,30,${this.night})`;
      ctx.fillRect(0, 0, this.world.w, this.world.h);
    }
    ctx.globalCompositeOperation = "lighter";
    for (const actor of this.actors.values()) {
      if (!this.seated(actor) || !actor.desk) continue;
      // The monitor in front of them lights their face.
      const cx = actor.desk.x + 33;
      const cy = actor.desk.y + 22;
      const g = ctx.createRadialGradient(cx, cy, 1, cx, cy, 24);
      g.addColorStop(0, `rgba(90,150,255,${0.16 + this.night * 0.45})`);
      g.addColorStop(1, "rgba(90,150,255,0)");
      ctx.fillStyle = g;
      ctx.fillRect(cx - 24, cy - 24, 48, 48);
    }
    const cluster = this.world.cluster;
    for (const f of this.flashes) {
      const t = (this.time - f.start) / (f.until - f.start);
      // Three quick blinks that fade: the racks take the delivery in.
      const pulse = (0.55 + 0.45 * Math.sin(t * Math.PI * 6)) * (1 - t);
      ctx.fillStyle = `rgba(110,200,255,${0.5 * pulse})`;
      for (const rack of this.art.racks) ctx.fillRect(cluster.x + rack.x, cluster.y + rack.y, rack.w, rack.h);
      ctx.strokeStyle = `rgba(242,207,122,${0.9 * (1 - t)})`;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.ellipse(this.world.drop.x, this.world.drop.y - 4, 10 + t * 46, 4 + t * 18, 0, 0, Math.PI * 2);
      ctx.stroke();
    }
    ctx.globalCompositeOperation = "source-over";
    ctx.setTransform(1, 0, 0, 1, 0, 0);
  }

  /**
   * Text over the scaled scene, at device resolution: name tags and bubbles.
   * `toScreen` maps a world point to device pixels; `px` is one CSS pixel.
   */
  drawLabels(ctx: CanvasRenderingContext2D, toScreen: (p: Point) => Point, px: number, scale: number) {
    const font = `600 ${Math.round(10.5 * px)}px ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif`;
    ctx.font = font;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    // The front-most person keeps their tag over their head; a tag that would
    // cover one already placed moves up a step (a crowd stacks its names).
    const sorted = [...this.actors.values()].sort((a, b) => this.feet(b).y - this.feet(a).y);
    const placed: { x: number; y: number; w: number; h: number }[] = [];
    const tags: { actor: Actor; label: string; x: number; y: number; w: number; h: number }[] = [];
    for (const actor of sorted) {
      const feet = this.feet(actor);
      const top = toScreen({ x: feet.x, y: feet.y - SIZE.frame.h + 1 });
      const label = actor.name.length > 18 ? `${actor.name.slice(0, 17)}…` : actor.name;
      const w = ctx.measureText(label).width + 10 * px;
      const h = 15 * px;
      const rect = { x: top.x - w / 2, y: top.y - h, w, h };
      for (let i = 0; i < 4 && placed.some((p) => p.x < rect.x + rect.w && rect.x < p.x + p.w && p.y < rect.y + rect.h && rect.y < p.y + p.h); i += 1)
        rect.y -= h + 2 * px;
      placed.push(rect);
      tags.push({ actor, label, ...rect });
    }
    for (const tag of tags.reverse()) {
      const strong = this.hover === tag.actor.key;
      ctx.globalAlpha = strong ? 1 : 0.92;
      ctx.fillStyle = tag.actor.colour;
      roundRect(ctx, tag.x, tag.y, tag.w, tag.h, 4 * px);
      ctx.fill();
      ctx.fillStyle = "#ffffff";
      ctx.fillText(tag.label, tag.x + tag.w / 2, tag.y + tag.h / 2 + 0.5 * px);
      ctx.globalAlpha = 1;
    }
    for (const bubble of this.bubbles) {
      const actor = this.actors.get(bubble.person);
      if (!actor) continue;
      const life = Math.min(1, (bubble.until - this.time) / 0.4);
      const rise = (2.8 - (bubble.until - this.time)) * 4 * scale;
      // Above the person's name tag.
      const tag = tags.find((x) => x.actor === actor);
      const feet = this.feet(actor);
      const at = tag ? { x: tag.x + tag.w / 2, y: tag.y - 3 * px } : toScreen({ x: feet.x, y: feet.y - SIZE.frame.h - 14 });
      ctx.font = `700 ${Math.round(10 * px)}px ui-monospace, SFMono-Regular, Menlo, monospace`;
      const w = ctx.measureText(bubble.text).width + 12 * px;
      const h = 16 * px;
      ctx.globalAlpha = Math.max(0, life);
      ctx.fillStyle = "#0f1424";
      roundRect(ctx, at.x - w / 2, at.y - h - rise, w, h, 5 * px);
      ctx.fill();
      ctx.strokeStyle = bubble.colour;
      ctx.lineWidth = 1.5 * px;
      ctx.stroke();
      ctx.fillStyle = "#ffffff";
      ctx.fillText(bubble.text, at.x, at.y - h / 2 - rise + 0.5 * px);
      ctx.globalAlpha = 1;
      ctx.font = font;
    }
  }

  /** The person under a world point, nearest the front first. */
  hit(p: Point): string | null {
    let best: Actor | null = null;
    let bestY = -Infinity;
    for (const actor of this.actors.values()) {
      const f = this.feet(actor);
      // A seated person shows only above the desk.
      const bottom = this.seated(actor) ? f.y - 16 : f.y + 3;
      const inside = p.x >= f.x - 13 && p.x <= f.x + 13 && p.y >= f.y - SIZE.frame.h + 1 && p.y <= bottom;
      if (inside && f.y > bestY) {
        best = actor;
        bestY = f.y;
      }
    }
    return best?.key ?? null;
  }

  /** Who is where, for the text alternative. */
  places(): { person: string; place: string; moving: boolean; carrying: boolean }[] {
    return [...this.actors.values()].map((a) => ({
      person: a.key,
      place: a.errand ? "cluster" : a.intent.kind === "work" ? a.intent.room : "lobby",
      moving: a.path.length > 0,
      carrying: a.carrying,
    }));
  }

  position(key: string): Point | null {
    const a = this.actors.get(key);
    return a ? { x: a.x, y: a.y } : null;
  }
}

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}
