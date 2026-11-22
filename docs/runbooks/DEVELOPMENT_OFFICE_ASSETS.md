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

The guides:
- `docs/runbooks/office/office-map-guide.png` with `office-map-layout.json` (1024 × 768);
- `docs/runbooks/office/office-map-guide-768x480.png` with `office-map-layout-768x480.json` (the compact 768 × 480).

```jsonc
{
  "size": [1024, 768],
  "back_wall": 48,          // every room's back wall: its floor starts below it
  "outer_wall": 16,
  "rooms": {
    "room-1": {
      "rect": [16, 16, 336, 224],      // x1, y1, x2, y2
      "kind": "product",               // product | cluster | lobby
      "screen": [140, 22, 72, 32],     // x, y, w, h: a dark panel on the back wall
      "doors": [{ "side": "S", "x": 160, "width": 32 }],   // N: through the back wall
      "free_floor": [40, 96, 272, 112] // where desks go (lobby: where people stand)
    },
    "cluster": { "loading": [512, 416], ... },            // where deliveries are put down
    "lobby": { "entrance": [488, 48],                      // x, width in the outer wall
               "blocked": [[398, 696, 80, 36], ...] }      // furniture nobody walks through
  }
}
```

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
  - The lobby's screen is the Tracesis logo wall.
- **Lobby:** people stand on a spaced grid on its free floor, at least a character's width apart.

## Props

**Chair** (`chair.png`, 32 × 32): the seat, its back and arms, its base.

**Desk** (`desk-front.png`, 64 × 40): the desk with its monitor seen from behind, things on it, its legs; no chair.

A desk at (x, y) is drawn in this order:
1. its chair at (x + 16, y − 14);
2. the person sitting in it, feet at (x + 32, y + 20), so their head and shoulders clear the monitor;
3. the desk front at (x, y), in front of them.

Desks are laid out on a room's free floor, spaced, in one or two rows.

**Box** (`box.png`, 16 × 16): the delivery lying on the loading spot.

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
- the multiples of d.

For 4× art the levels are therefore 1, 2, 3, 4, 8, 12…

| Z | A 4× character is drawn | Its size in the office |
|---|-------------------------|------------------------|
| 1, 2 | shrunk by exactly 4:1 or 2:1 (each block averaged by coverage, opaque when at least half covered), one device pixel per shrunk pixel | 32 × 48 |
| 3 | unshrunk, one device pixel per sprite pixel | a third larger (≈ 43 × 64) |
| 4, 8, … | Z / 4 device pixels per sprite pixel | 32 × 48 |

Furniture and the map are always drawn at Z. The shrunk copies are made once per frame and factor and kept. Nothing is ever resampled by an arbitrary ratio.

**Fit.** The office opens, and "fit" returns, at the largest level where its whole width fits the panel. A phone pans.

**People's names** are not shown all the time:
- **Hover or pick:** a small dark label with the person's name, and a thin outline around them.
- **Zoomed in close:** a compact name under everyone's feet.
- **Commits:** small speech chips.

Labels and chips never overlap: one that would is moved aside or left out.

## `asset-manifest.json`

What the page reads from it:

- `environment.<name>.file`: `office-map`, `chair`, `desk-front`, `box`, and
  the first kit's rooms and corridor tile (for the placeholder map).
- `environment.office-map.layout`: the map's geometry file.
- `characters.frameSize` and `characters.anchor`: the feet, when the frame
  size matches a person's strips (otherwise the defaults above).
- `characters.variants.<person>.sheets.<clip>`: each person's strips.

Paths are relative to `public/office/`: PNG for pictures, one JSON file for
the geometry. Anything else is ignored. A map whose geometry file does not
match its picture's size is drawn with the guide's geometry.
