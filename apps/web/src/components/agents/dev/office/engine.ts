/**
 * The office's simulation and drawing: people walk out of a room's door,
 * along the corridors and in through another's to their desks; they idle in
 * the lobby, and carry a delivery into the cluster's room.
 *
 * The scene is drawn through a camera onto the screen canvas; text (a
 * hovered person's name, commit chips) is drawn at full resolution on top.
 */

import { BODY_ROWS, reduced, type Art, type BodyRow, type Sprite } from "./art";
import type { Intent } from "./director";
import { paintMap, propSprite } from "./placeholder";
import { CHAIR, findPath, SEATED, SIZE, type Desk, type Point, type Rect, type Room, type World } from "./world";

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

interface Box {
  key: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

interface Bubble {
  person: string;
  text: string;
  colour: string;
  until: number;
}

const SPEED = 74;
/** Where someone arriving starts: on the plaza by the street, below the main entrance. */
const STREET = 76;
const IDLE_MIN = 9;
const IDLE_SPREAD = 16;

/** How a character of density `d` is drawn at zoom `z` (see OfficeEngine.spriteScale). */
export function spriteScale(z: number, d: number): { factor: number; k: number } {
  if (d === 4 && z === 3) return { factor: 1, k: 4 / 3 };
  for (let f = 1; f <= d; f *= 2) if (d % f === 0 && z % (d / f) === 0) return { factor: f, k: 1 };
  return { factor: d, k: 1 };
}

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
  /** Whether the first people have been placed (later ones walk in from the street). */
  private populated = false;
  private ground: CanvasPattern | null = null;
  /** The person under the pointer, and the one picked (their name shows, with an outline). */
  hover: string | null = null;
  focus: string | null = null;
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
    return paintMap(this.world, this.art);
  }

  /** The room a repository is in (its own, or the shared one). */
  private roomFor(repository: string): Room | undefined {
    return this.rooms.get(this.world.roomOf[repository] ?? repository);
  }

  setHead(key: string, sprites: Sprite[]) {
    this.art.heads.set(key, sprites);
  }

  hasHead(key: string): boolean {
    return this.art.heads.has(key);
  }

  /**
   * Who is in the office (others leave). The first people stand in the lobby
   * already; anyone who arrives later walks in from the street, across the
   * plaza and through the main entrance.
   */
  setPeople(people: ReadonlyArray<{ key: string; name: string; colour: string }>) {
    const keep = new Set(people.map((p) => p.key));
    for (const key of [...this.actors.keys()]) if (!keep.has(key)) this.actors.delete(key);
    const arriving = this.populated && !this.reduced;
    people.forEach((p, i) => {
      const existing = this.actors.get(p.key);
      if (existing) {
        existing.name = p.name;
        existing.colour = p.colour;
        return;
      }
      const spot = this.world.lobbySpots[i % this.world.lobbySpots.length];
      const seed = hashOf(p.key);
      const actor: Actor = {
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
      };
      this.actors.set(p.key, actor);
      if (arriving) {
        actor.x = this.world.entrance.x;
        actor.y = this.world.h + STREET;
        actor.goal = "";
        const at = this.lobbySpot(actor);
        this.go(actor, at.goal, at.at, "idle", false);
      }
    });
    this.populated = true;
  }

  private deskFor(actor: Actor, repository: string): Desk | null {
    const room = this.roomFor(repository);
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
    // Still outside: up the plaza to just inside the entrance first.
    const outside = actor.y > this.world.h - 2;
    const from = outside ? { x: this.world.entrance.x, y: this.world.h - 4 } : { x: actor.x, y: actor.y };
    const path = findPath(this.world, from, at);
    actor.path = [...(outside ? [from] : []), ...(path.length ? path : [at])];
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
      // Every desk taken: stand in the room, a step apart from the others.
      const room = this.roomFor(actor.intent.room);
      if (room?.standing.length) {
        actor.desk = null;
        const taken = new Set([...this.actors.values()].filter((a) => a !== actor).map((a) => a.goal));
        const i = room.standing.findIndex((_, k) => !taken.has(`stand:${room.id}:${k}`));
        const k = i < 0 ? Math.floor(actor.seed * room.standing.length) : i;
        const goal = `stand:${room.id}:${k}`;
        if (actor.goal !== goal) this.go(actor, goal, room.standing[k], "idle", instant);
        return;
      }
    }
    actor.desk = null;
    if (!actor.goal.startsWith("lobby:")) {
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
    const place = this.roomFor(room);
    if (!actor || this.reduced || !place) {
      this.land();
      return;
    }
    actor.errand = { room, stage: "fetch" };
    const desk = this.deskFor(actor, room);
    const at = desk?.seat ?? place.standing[0] ?? this.world.drop;
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
    // Idle in the lobby: now and then a coffee where they stand, or a few steps to a free spot.
    if (!this.reduced && actor.intent.kind === "lobby" && this.time > actor.wander) {
      const roll = hashOf(actor.key + Math.floor(this.time));
      actor.wander = this.time + IDLE_MIN + roll * IDLE_SPREAD;
      if (actor.pose === "coffee") actor.pose = "idle";
      else if (roll < 0.55) actor.pose = "coffee";
      else {
        const spot = this.lobbySpot({ ...actor, seed: roll });
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
    const d = set.density;
    const { factor, k } = this.spriteScale(d);
    const fw = SIZE.frame.w * k;
    const fh = SIZE.frame.h * k;
    const left = x - (set.anchor.x / d) * k;
    const top = y - (set.anchor.y / d) * k;
    const seated = this.seated(actor);
    const marked = this.hover === actor.key || this.focus === actor.key;
    if (!seated) {
      // A soft shadow under the feet; the person's colour only when they are picked.
      ctx.fillStyle = "rgba(0,0,0,0.3)";
      ctx.beginPath();
      ctx.ellipse(x, y, 9, 3, 0, 0, Math.PI * 2);
      ctx.fill();
      if (marked) {
        ctx.strokeStyle = actor.colour;
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.ellipse(x, y, 12, 4.5, 0, 0, Math.PI * 2);
        ctx.stroke();
      }
    }
    // A picked person gets a thin light outline around the sprite.
    if (marked) ctx.filter = `drop-shadow(0 0 ${Math.max(1, 1 / this.zoom)}px rgba(255,255,255,0.95))`;
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
    ctx.filter = "none";
  }

  /**
   * How a character of density `d` is drawn at the current zoom, keeping
   * every pixel whole: `factor` shrinks the sprite by exact blocks first
   * (1 = not at all), `k` is its size relative to a 32 × 48 character.
   *
   * The sprite is shrunk by the smallest whole factor f (1, 2, 4…) that
   * leaves each of its pixels a whole Z / (d / f) device pixels (k = 1): at
   * Z = 8, 4× art as drawn, 2 pixels a pixel; at Z = 6, shrunk 2:1, 3 pixels
   * a pixel; at Z = 2, shrunk 2:1, one pixel a pixel. The one exception is
   * Z = 3 with 4× art: nothing is shrunk, one device pixel per sprite pixel,
   * the person a third larger than the room's scale (k = 4/3).
   */
  private spriteScale(d: number): { factor: number; k: number } {
    return spriteScale(this.zoom, d);
  }

  /** A person's drawn size relative to a 32 × 48 character (larger at Z = 3 with 4× art). */
  private scaleOf(actor: Actor): number {
    return this.spriteScale((this.art.bodies.get(actor.key) ?? this.art.body).density).k;
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
    this.drawSurroundings(ctx, {
      x: Math.floor(view.x) - 1,
      y: Math.floor(view.y) - 1,
      w: Math.ceil(ctx.canvas.width / z) + 2,
      h: Math.ceil(ctx.canvas.height / z) + 2,
    });
    ctx.drawImage(this.still, 0, 0);
    // The wall screens are HTML under the canvas: open a window onto each, so
    // whoever stands in front of one is drawn over it, as in the room.
    const screens = this.screens();
    for (const s of screens) ctx.clearRect(s.x, s.y, s.w, s.h);
    type Item = { y: number; paint: () => void };
    const items: Item[] = [];
    // Chair, then whoever sits in it, then the desk in front of them.
    const { chair, deskFront } = this.art;
    for (const d of this.world.rooms.flatMap((r) => r.desks)) {
      items.push({ y: d.y + CHAIR.y, paint: () => ctx.drawImage(chair, d.x + CHAIR.x, d.y + CHAIR.y) });
      items.push({ y: d.y + 15, paint: () => ctx.drawImage(deskFront, d.x, d.y) });
    }
    for (const actor of this.actors.values()) {
      // Seated: after the chair, before the desk's top and its monitor.
      const y = this.seated(actor) && actor.desk ? actor.desk.y + 10 : actor.y;
      items.push({ y, paint: () => this.drawActor(ctx, actor) });
    }
    // Furniture stands among them: sorted by where it touches the floor.
    for (const prop of this.world.props) {
      if (prop.still) continue;
      const sprite = propSprite(this.art, prop);
      items.push({ y: prop.y + prop.h - 2, paint: () => ctx.drawImage(sprite, prop.x, prop.y) });
    }
    const landing = this.flashes.length > 0;
    if (landing) {
      const box = this.art.box;
      items.push({ y: this.world.drop.y, paint: () => ctx.drawImage(box, Math.round(this.world.drop.x - 8), Math.round(this.world.drop.y - 14)) });
    }
    items.sort((a, b) => a.y - b.y);
    for (const item of items) item.paint();

    ctx.globalCompositeOperation = "lighter";
    for (const actor of this.actors.values()) {
      if (!this.seated(actor) || !actor.desk) continue;
      // The monitor in front of them lights their face.
      const cx = actor.desk.x + 33;
      const cy = actor.desk.y + 10;
      const g = ctx.createRadialGradient(cx, cy, 1, cx, cy, 24);
      g.addColorStop(0, "rgba(90,150,255,0.2)");
      g.addColorStop(1, "rgba(90,150,255,0)");
      ctx.fillStyle = g;
      ctx.fillRect(cx - 24, cy - 24, 48, 48);
    }
    const cluster = this.world.cluster;
    for (const f of this.flashes) {
      const t = (this.time - f.start) / (f.until - f.start);
      // Three quick blinks that fade: the racks take the delivery in.
      const pulse = (0.55 + 0.45 * Math.sin(t * Math.PI * 6)) * (1 - t);
      ctx.fillStyle = `rgba(110,200,255,${0.45 * pulse})`;
      // The racks along the back wall light up.
      ctx.fillRect(cluster.x + 8, cluster.y + 4, cluster.w - 16, this.world.layout.backWall + 24);
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
   * Where the camera may look (art pixels, office coordinates): the exterior
   * when there is one, centred on the building; else the building alone.
   */
  bounds(): Rect {
    return sceneBounds(this.world, this.art.exterior);
  }

  /**
   * What lies around the building, under the visible area `v`: the exterior,
   * its edges carried on beyond it on a very wide screen; or, until it is
   * drawn, a dark paved ground, the building's shadow and a path to the door.
   */
  private drawSurroundings(ctx: CanvasRenderingContext2D, v: Rect) {
    const ext = this.art.exterior;
    const { w, h } = this.world;
    if (ext) {
      const ew = ext instanceof HTMLImageElement ? ext.naturalWidth : ext.width;
      const eh = ext instanceof HTMLImageElement ? ext.naturalHeight : ext.height;
      const e = { x: -(ew - w) / 2, y: -(eh - h) / 2, w: ew, h: eh };
      const left = Math.max(0, e.x - v.x);
      const right = Math.max(0, v.x + v.w - (e.x + e.w));
      const top = Math.max(0, e.y - v.y);
      const bottom = Math.max(0, v.y + v.h - (e.y + e.h));
      // Its outermost rows and columns, stretched over whatever lies beyond it.
      if (left) ctx.drawImage(ext, 0, 0, 1, eh, e.x - left, e.y, left, eh);
      if (right) ctx.drawImage(ext, ew - 1, 0, 1, eh, e.x + ew, e.y, right, eh);
      if (top) ctx.drawImage(ext, 0, 0, ew, 1, e.x, e.y - top, ew, top);
      if (bottom) ctx.drawImage(ext, 0, eh - 1, ew, 1, e.x, e.y + eh, ew, bottom);
      if (left && top) ctx.drawImage(ext, 0, 0, 1, 1, e.x - left, e.y - top, left, top);
      if (right && top) ctx.drawImage(ext, ew - 1, 0, 1, 1, e.x + ew, e.y - top, right, top);
      if (left && bottom) ctx.drawImage(ext, 0, eh - 1, 1, 1, e.x - left, e.y + eh, left, bottom);
      if (right && bottom) ctx.drawImage(ext, ew - 1, eh - 1, 1, 1, e.x + ew, e.y + eh, right, bottom);
      ctx.drawImage(ext, e.x, e.y);
      return;
    }
    this.ground ??= ctx.createPattern(pavement(), "repeat");
    ctx.fillStyle = this.ground ?? "#121725";
    ctx.fillRect(v.x, v.y, v.w, v.h);
    // A soft shadow under the building, and a path from the street to its door.
    ctx.fillStyle = "rgba(0,0,0,0.28)";
    ctx.fillRect(-6, -4, w + 12, h + 12);
    ctx.fillStyle = "rgba(0,0,0,0.22)";
    ctx.fillRect(-3, -2, w + 6, h + 6);
    const door = this.world.entrance.x;
    ctx.fillStyle = "#1d2436";
    ctx.fillRect(door - 26, h, 52, STREET + 24);
    ctx.fillStyle = "#252d43";
    for (let y = h + 4; y < h + STREET + 24; y += 8) ctx.fillRect(door - 26, y, 52, 1);
  }

  /** Every wall screen's rectangle: product rooms, vacant ones, the cluster and the lobby. */
  screens(): Rect[] {
    const { rooms, vacant, cluster, lobby } = this.world;
    return [...rooms, ...vacant, cluster, lobby].map((r) => r.screen);
  }

  /**
   * Text over the scaled scene, at device resolution. No names by default:
   * the hovered or picked person's name, in a small dark label; zoomed in
   * close, a compact name under everyone's feet; a commit is a small speech
   * chip over the author's head. Nothing overlaps: a label that would cover
   * one already placed steps aside, or is left out.
   * `toScreen` maps a world point to device pixels; `px` is one CSS pixel.
   */
  drawLabels(ctx: CanvasRenderingContext2D, toScreen: (p: Point) => Point, px: number, scale: number) {
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    const placed: Box[] = [];
    const free = (b: Box) => !placed.some((p) => p.x < b.x + b.w && b.x < p.x + p.w && p.y < b.y + b.h && b.y < p.y + p.h);
    const front = [...this.actors.values()].sort((a, b) => this.feet(b).y - this.feet(a).y);
    const short = (name: string, max: number) => (name.length > max ? `${name.slice(0, max - 1)}…` : name);

    // The picked and the hovered person's name, over their head.
    for (const key of [this.focus, this.hover]) {
      const actor = key ? this.actors.get(key) : undefined;
      if (!actor || placed.some((p) => p.key === actor.key)) continue;
      ctx.font = `600 ${Math.round(11 * px)}px ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif`;
      const feet = this.feet(actor);
      const top = toScreen({ x: feet.x, y: feet.y - SIZE.frame.h * this.scaleOf(actor) + (this.seated(actor) ? 4 : 0) });
      const text = short(actor.name, 24);
      const w = ctx.measureText(text).width + 14 * px;
      const h = 18 * px;
      const box = { key: actor.key, x: top.x - w / 2, y: top.y - h - 4 * px, w, h };
      placed.push(box);
      ctx.fillStyle = "rgba(12,16,30,0.92)";
      roundRect(ctx, box.x, box.y, w, h, 5 * px);
      ctx.fill();
      ctx.fillStyle = actor.colour;
      roundRect(ctx, box.x + 5 * px, box.y + h / 2 - 3 * px, 6 * px, 6 * px, 3 * px);
      ctx.fill();
      ctx.fillStyle = "#f3f5fb";
      ctx.fillText(text, box.x + w / 2 + 3 * px, box.y + h / 2 + 0.5 * px);
    }

    // Zoomed in close: a compact name under each person's feet, where there is room.
    if (scale >= 4) {
      ctx.font = `600 ${Math.round(9.5 * px)}px ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif`;
      for (const actor of front) {
        if (placed.some((p) => p.key === actor.key) || this.seated(actor)) continue;
        const feet = this.feet(actor);
        const at = toScreen({ x: feet.x, y: feet.y + 4 });
        const text = short(actor.name.split(" ")[0] ?? actor.name, 14);
        const w = ctx.measureText(text).width + 10 * px;
        const h = 14 * px;
        const box = { key: actor.key, x: at.x - w / 2, y: at.y, w, h };
        if (!free(box)) continue;
        placed.push(box);
        ctx.fillStyle = "rgba(12,16,30,0.78)";
        roundRect(ctx, box.x, box.y, w, h, 7 * px);
        ctx.fill();
        ctx.fillStyle = "#e6e9f2";
        ctx.fillText(text, at.x, box.y + h / 2 + 0.5 * px);
      }
    }

    // Commits: a small speech chip, its tail to the author's head.
    ctx.font = `700 ${Math.round(9.5 * px)}px ui-monospace, SFMono-Regular, Menlo, monospace`;
    for (const bubble of [...this.bubbles].sort((a, b) => b.until - a.until)) {
      const actor = this.actors.get(bubble.person);
      if (!actor) continue;
      const life = Math.max(0, Math.min(1, (bubble.until - this.time) / 0.4));
      const feet = this.feet(actor);
      const head = toScreen({ x: feet.x + 10, y: feet.y - SIZE.frame.h * this.scaleOf(actor) + (this.seated(actor) ? 6 : 2) });
      const w = ctx.measureText(bubble.text).width + 12 * px;
      const h = 15 * px;
      const box = { key: `bubble:${actor.key}`, x: head.x - 4 * px, y: head.y - h - 6 * px, w, h };
      for (let i = 0; i < 3 && !free(box); i += 1) box.y -= h + 3 * px;
      if (!free(box)) continue;
      placed.push(box);
      ctx.globalAlpha = life;
      ctx.fillStyle = "#f5f7fc";
      roundRect(ctx, box.x, box.y, w, h, 4 * px);
      ctx.fill();
      ctx.beginPath();
      ctx.moveTo(box.x + 6 * px, box.y + h - 0.5 * px);
      ctx.lineTo(box.x + 6 * px, box.y + h + 5 * px);
      ctx.lineTo(box.x + 12 * px, box.y + h - 0.5 * px);
      ctx.fill();
      ctx.fillStyle = bubble.colour;
      ctx.fillRect(box.x, box.y + 3 * px, 2 * px, h - 6 * px);
      ctx.fillStyle = "#151a2b";
      ctx.fillText(bubble.text, box.x + w / 2 + 1 * px, box.y + h / 2 + 0.5 * px);
      ctx.globalAlpha = 1;
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
      const k = this.scaleOf(actor);
      const inside = p.x >= f.x - 13 * k && p.x <= f.x + 13 * k && p.y >= f.y - SIZE.frame.h * k + 1 && p.y <= bottom;
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

/** The area the camera may show: the exterior's, centred on the building, or the building's. */
export function sceneBounds(world: { w: number; h: number }, exterior: Sprite | null): Rect {
  if (!exterior) return { x: 0, y: 0, w: world.w, h: world.h };
  const ew = exterior instanceof HTMLImageElement ? exterior.naturalWidth : exterior.width;
  const eh = exterior instanceof HTMLImageElement ? exterior.naturalHeight : exterior.height;
  return { x: -(ew - world.w) / 2, y: -(eh - world.h) / 2, w: ew, h: eh };
}

/** A 16 × 16 tile of dark paving: the ground around the building until the exterior is drawn. */
function pavement(): HTMLCanvasElement {
  const c = document.createElement("canvas");
  c.width = 16;
  c.height = 16;
  const ctx = c.getContext("2d");
  if (!ctx) return c;
  ctx.fillStyle = "#131927";
  ctx.fillRect(0, 0, 16, 16);
  ctx.fillStyle = "#0f1420";
  ctx.fillRect(0, 0, 16, 1);
  ctx.fillRect(0, 8, 16, 1);
  ctx.fillRect(0, 0, 1, 8);
  ctx.fillRect(8, 8, 1, 8);
  ctx.fillStyle = "#171e2e";
  for (const [x, y] of [
    [3, 3],
    [11, 5],
    [5, 12],
    [13, 13],
  ])
    ctx.fillRect(x, y, 1, 1);
  return c;
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
