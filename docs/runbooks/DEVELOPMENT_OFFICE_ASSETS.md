# Development office — the art kit

Agents → Office draws the people who change the repositories as one pixel
office building:

- **Product rooms:** seven, for the busiest repositories.
- **Cluster:** the server room in the middle, its screen naming the actual cluster.
- **Lobby:** at the bottom, with the main entrance.
- **Corridors:** joining the rows of rooms; people walk out of a room's doors, along the corridors and in through another room's doors.

This is the contract for its art: what files there are, their sizes, where
things are on them, and how a new kit is checked and installed.

The art lives in `apps/web/public/office/`. Every file is optional: anything
missing is drawn by a placeholder of the same size and layout.

## Installing a kit

```sh
python3 scripts/office-assets.py path/to/kit --check   # check only
python3 scripts/office-assets.py path/to/kit           # check, then install
python3 scripts/office-assets.py path/to/kit --rescale # art exported larger than native
```

The script checks everything before it writes anything:
- sizes and frame counts;
- binary alpha (every pixel fully clear or fully opaque);
- that the map is opaque;
- where the feet stand in every frame;
- the map's geometry (below).

Then it copies the files and writes `asset-manifest.json` to describe exactly what was installed.

`--rescale` is for pixel art exported at k × its native size. It finds k, checks that the k × k blocks are flat, and keeps one pixel per block.

## Layout of a kit

```
asset-manifest.json
office-map.png             the whole office, one picture, opaque
office-map.json            its geometry (below)
office-exterior.png      1280 × 800    opaque, optional: the scenery around the building
props/<kind>.png                       transparent, optional: the rooms' furniture (below)
chair.png                   32 × 32    transparent
desk-front.png              64 × 40    transparent
box.png                     16 × 16    transparent
floor-corridor.png          32 × 32    (only for the composed placeholder map)
room-*.png                             (the first kit's rooms: only for the placeholder map)
characters/<person>/body-<clip>.png
```

`<person>` is the person's key on the development map (their GitHub login,
or the alias they were given), e.g. `gencay`, `eneserman`.

Style: pixel art, top-down 3/4 view, a crisp one-pixel grid with no
anti-aliasing, no text, no logos. The office is dark navy and warm wood with
cobalt and gold accents, lit from the top left.

## The map and its geometry

`office-map.png` is drawn to a guide; `office-map.json` says where everything is on it, in art pixels. Everything the office does with the map comes from this file: where rooms are, where people walk, where desks go, where screens show.

The shipped map is the compact 768 × 480, drawn to
`docs/runbooks/office/office-map-guide-768x480.png` with
`office-map-layout-768x480.json`. The larger 1024 × 768 guide
(`office-map-guide.png`, `office-map-layout.json`) still works with the same
engine: a map of any size is fine as long as its JSON describes it.

```jsonc
{
  "size": [768, 480],
  "back_wall": 40,          // every room's back wall: its floor starts below it
  "outer_wall": 8,
  "rooms": {
    "room-1": {
      "rect": [8, 8, 248, 160],        // x1, y1, x2, y2
      "kind": "product",               // product | cluster | lobby
      "screen": [100, 14, 56, 24],     // x, y, w, h: a dark panel on the back wall
      "doors": [{ "side": "S", "x": 112, "width": 32 }],   // N: through the back wall
      "free_floor": [20, 66, 216, 86]  // where desks go (lobby: where people stand)
    },
    "cluster": { "loading": [384, 288], ... },            // where deliveries are put down
    "lobby": { "entrance": [364, 40],                      // x, width in the outer wall
               "blocked": [[270, 386, 38, 22], ...] }      // furniture nobody walks through
  }
}
```

The lobby's `blocked` list covers the coffee bar, the reception desk, both
sofas, and the plants by the side walls.

- **Rooms:** there must be exactly one cluster, one lobby, and at least one product room.
- **Corridors:** whatever lies between two rows of rooms, wall to wall.
- **Walking:** a room's floor is walkable inside its walls, but it is entered and left only by its doors. Anything under `blocked` is not walked through.
- **Repositories → rooms:**
  - Repositories take product rooms busiest first, the catalog's before the rest. The large rooms go before the two narrow ones beside the cluster.
  - A room nobody uses is dimmed and its door shut.
  - When there are more repositories than rooms, the least active share the last room, "Other projects".
- **Screens:**
  - Each product room's screen shows its project's logo with the name under it, glowing.
  - The cluster's screen names the cluster (the one the visible projects deploy to most) and how many of its workloads are healthy.
  - The lobby's screen is the company's logo wall: Duosis when the office's cluster is Duosis's, else the Tracesis wordmark.
- **Lobby:** people stand on its free floor, a character's width and more apart, the middle first. Nobody stands where their body would overlap the map's furniture (the reception, the coffee bar, a sofa, a plant) or in a doorway.

## The exterior

`office-exterior.png` (optional, 1280 × 800, opaque, the map's density and style) is the street, the pavement, trees, parked cars and the plaza around the building. The building's footprint is left as one flat colour, exactly centred: x 256–1024, y 160–640. The map is drawn over it there. The plaza leads from the main entrance (the lobby's `entrance`, x ≈ 364–400 on the map) down to the street.

- **Coordinates:** the office keeps its own. The exterior is drawn at (−256, −160).
- **Camera:** may show the exterior. It is kept inside the exterior's bounds, and inside the office's when there is no exterior.
- **Very wide screens:** beyond the exterior, its outermost rows and columns are carried on, so the streets run on and the grass continues.
- **Arrivals:** someone who appears in the office after it has loaded walks in from the street, up the plaza and through the main entrance. Everyone already there when the page opens starts in the lobby.
- **No exterior yet:** a dark paved ground, the building's shadow and a path to the entrance.

## Props

**Chair** (`chair.png`, 32 × 32): the seat, its back and arms, its base.

**Desk** (`desk-front.png`, 64 × 40): the desk with its monitor seen from behind, things on it, its legs; no chair.

A desk at (x, y) is drawn in this order:
1. its chair at (x + 16, y − 14);
2. the person sitting in it, feet at (x + 32, y + 20), so their head and shoulders clear the monitor;
3. the desk front at (x, y), in front of them.

Desks are laid out on a room's free floor, spaced, in one or two rows. When a single row leaves room for a lounge, the row keeps to one side.

**Box** (`box.png`, 16 × 16): the delivery lying on the loading spot, and the boxes stacked in an empty room.

### Furniture

Each prop is its own transparent PNG in `props/`, at the map's density and style, alpha binary. Each one is optional; until a prop is drawn, a quiet placeholder of the same size stands in for it.

| Prop | Size | Prop | Size |
|------|------|------|------|
| `rug` | 96 × 48 | `floor-lamp` | 12 × 32 |
| `plant-tall` | 16 × 32 | `water-cooler` | 12 × 28 |
| `plant-small` | 12 × 16 | `whiteboard-stand` | 32 × 36 |
| `cabinet` | 24 × 32 | `bin` | 8 × 10 |
| `printer` | 24 × 24 | `bookshelf` | 32 × 40 |
| `sofa-small` | 48 × 28 | `server-rack` | 24 × 40 |
| `coffee-table` | 32 × 20 | | |

Every room is furnished the same way each time, chosen by its slot:

| Room | Furniture |
|------|-----------|
| Product room | A rug under the desks. A corner lounge where there is room: a small sofa, a coffee table, a tall plant. A bookshelf or cabinet with a printer along a wall. A water cooler or mobile whiteboard, and a floor lamp. Small plants between desks and a bin at a row's end. |
| A room nobody uses | Stacked boxes, a lone plant, and a sofa or cabinet under a dust sheet. |
| Cluster | Two spare racks on the floor, away from the loading square, and a cabinet. |
| Lobby | A rug in the middle, a tall plant and a floor lamp, clear of the reception, the coffee bar and the sofas. |

**Drawing order:**
- Rugs lie under everyone; the rest are sorted by where they touch the floor, with people, chairs and desks.
- An empty room's things are painted with the map and dimmed with it.

**Walking:** a prop's bottom rows block walking. A prop never covers:
- a desk, a chair or a screen;
- a door or the way through it;
- the loading square;
- the map's own furniture.

It also keeps clear of every seat and standing spot. Each placement is checked by walking the grid from the entrance: every seat, standing spot, lobby spot, door and the loading square stays reachable, or the prop goes elsewhere or is left out.

## Characters

Each person has their own full character: nine clips, each one row of
frames left to right. The head is drawn in: no separate head files.

| Clip | Frames |
|------|--------|
| `body-walk-down`, `body-walk-left`, `body-walk-up` | 4 |
| `body-carry-down`, `body-carry-left`, `body-carry-up` (the box in their arms) | 4 |
| `body-idle-down`, `body-sit-type`, `body-coffee` | 2 |

Walking right is the left clip mirrored. All nine clips of a person share one
density:

| Density | Frame | Feet (anchor) | Strips |
|---------|-------|---------------|--------|
| 1× | 32 × 48 | (16, 45) | 128 × 48 and 64 × 48 |
| 2× | 64 × 96 | (32, 91) | 256 × 96 and 128 × 96 |
| 4× (current kit) | 128 × 192 | (64, 180) | 512 × 192 and 256 × 192 |

The density is read from each person's strips, so people of different
densities can share an office. Someone without a folder (a newcomer) walks as
a shared placeholder body with a head made from their portrait, their GitHub
picture, or their initials.

**Zoom.** The office is drawn at Z device pixels per art pixel, Z whole. The levels keep every pixel whole. For the densest character's density d they are:
- the divisors of d below it;
- 3, when d is 4;
- from d up, every level at which a whole shrink leaves whole pixels: for 4× art every even level, else the multiples of d.

For 4× art the levels are therefore 1, 2, 3, 4, 6, 8, 10, 12…

| Z | A 4× character is drawn | Its size in the office |
|---|-------------------------|------------------------|
| 1, 2 | shrunk by exactly 4:1 or 2:1 (each block averaged by coverage, opaque when at least half covered), one device pixel per shrunk pixel | 32 × 48 |
| 3 | unshrunk, one device pixel per sprite pixel | a third larger (≈ 43 × 64) |
| 4, 8, 12… | Z / 4 device pixels per sprite pixel | 32 × 48 |
| 6, 10, 14… | shrunk 2:1, Z / 2 device pixels per pixel | 32 × 48 |

Furniture, the map and the exterior are always drawn at Z. The shrunk copies are made once per frame and factor and kept. Nothing is ever resampled by an arbitrary ratio, except during a move between two views (below).

The office is always at full brightness, in its true colours, live and replayed: no day or night tint. The monitors still light the faces at the desks, and the racks still flash when a delivery lands.

**The page.** The Office tab takes the whole content area under the app header, with no page scroll. Nothing is laid over the office.
- **Toolbar** (one row, 34 px controls): the page's tabs, where the other Agents tabs are. Then the office's controls, grouped with thin dividers:
  - live/replay, with how many people are at work (a new commit or delivery shows there for a few seconds);
  - the 7/30/90-day window;
  - connect, zoom out, whole office, zoom in, full screen (icon buttons, each named by its tooltip).
- **Stage:** the office alone.
- **Day's bar** (replay only, below the stage): clock, date, play, speed, timeline, and the latest event or the day's totals.

A narrow screen wraps the toolbar onto more rows. The stage takes whatever height is left.

**Full screen** uses the browser's Fullscreen API on the whole office (toolbar, stage, day's bar; the page tabs are hidden). Esc leaves it, and so does picking a person (their drawer opens on the page).

**First look.**
- **Zoom:** a step closer than the whole office (the level "+" would go to from it), and at least close enough that the office spans the stage's whole width. No empty bands at the sides.
- **Across:** centred, never past the office's walls.
- **Down:** the band where people are (their rooms), else the cluster and the lobby, kept inside the office.
- **Panning:** drag (or pinch, or Ctrl/⌘ + wheel to zoom) to see the rest.

**Whole office** returns, animated, to the largest level where the whole office fits the stage both ways, with the exterior around it.

| Screen | Stage (CSS) | Whole office | First look |
|--------|-------------|--------------|------------|
| 14" MacBook Pro, 1512 × 860 at 2×, sidebar open | 1280 × 761 (722 playing a day) | Z = 3 | Z = 4 |
| 1440 × 900 at 2× | 1208 × 801 | Z = 3 | Z = 4 |
| The same window at 1× | 1208 × 801 | Z = 1 | Z = 2 |
| Phone, 390 wide at 2× | 390 × ≈ 665 | Z = 1 | Z = 2, and it pans |

At Z = 3 the 768 × 480 map is 2304 × 1440 device pixels, 1152 × 720 CSS at 2×.

**A room in view.** Clicking a room (its floor or its screen) brings it into view: the largest whole level at which the room and a small margin fit, centred above its caption.
- **The move:** it eases there over about half a second. On the way, the office is drawn at the nearest whole level below the current scale and enlarged the rest; at the end every pixel is whole again. With reduced motion it jumps.
- **The caption:**
  - the room's logo and name;
  - its commits in the window (the cluster: its health; the lobby: what it is for);
  - who is in it, each a button that opens their details;
  - its latest commits that day (the cluster: that day's deliveries);
  - **Details** (a product's drawer) and **Whole office**.
- **Next room:** the arrows at the stage's edges (or ← →) go to the next room in reading order: row by row from the top, left to right, the cluster and the lobby included, empty rooms skipped, wrapping around.
- **Leaving:** Esc or "Whole office" goes back to the whole office. Dragging or zooming leaves the room view where it is.
- **People:** clicking a person still opens their details.

**People's names** are not shown all the time:
- **Hover or pick:** a small dark label with the person's name, and a thin outline around them.
- **Zoomed in close:** a compact name under everyone's feet.
- **Commits:** small speech chips.

Labels and chips never overlap: one that would is moved aside or left out.

## `asset-manifest.json`

What the page reads from it:

- `environment.<name>.file`: `office-map`, `office-exterior`, `chair`,
  `desk-front`, `box`, and the first kit's rooms and corridor tile (for the
  placeholder map).
- `environment.office-map.layout`: the map's geometry file.
- `props.<kind>`: each drawn prop's file, `props/<kind>.png` (one of the wrong
  size is ignored and its placeholder drawn).
- `characters.frameSize` and `characters.anchor`: the feet, when the frame
  size matches a person's strips (otherwise the defaults above).
- `characters.variants.<person>.sheets.<clip>`: each person's strips.

Paths are relative to `public/office/`: PNG for pictures, one JSON file for
the geometry. Anything else is ignored. A map whose geometry file does not
match its picture's size is drawn with the guide's geometry.
