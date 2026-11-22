# Development office — the art kit

Agents → Office draws the people who change the repositories as a small
pixel office: product rooms around the cluster's server room, the lobby at
the bottom, corridors between. This is the contract for its art: what files
there are, their sizes, where the anchors are, and how a new kit is checked
and installed.

The art lives in `apps/web/public/office/`. Every file is optional: anything
missing is drawn by a placeholder of exactly the same size and layout, so a
drawing replaces a placeholder file by file.

## Installing a kit

```sh
python3 scripts/office-assets.py path/to/kit --check   # check only
python3 scripts/office-assets.py path/to/kit           # check, then install
python3 scripts/office-assets.py path/to/kit --rescale # art exported larger than native
```

Everything is checked before anything is written: sizes, frame counts,
binary alpha (every pixel fully clear or fully opaque), opaque rooms, where
the feet stand in every frame. The script copies the files, writes
`asset-manifest.json` to describe exactly what was installed, and leaves
`preview.png` and `README.md` out of the public folder. `--rescale` is for
pixel art exported at k × its native size: it finds k, checks the k × k
blocks are flat, and keeps one pixel per block.

## Layout of a kit

```
asset-manifest.json
room-product-open.png      320 × 224   opaque
room-product-backdoor.png  320 × 224   opaque
room-cluster.png           384 × 224   opaque
room-lobby.png             448 × 192   opaque
floor-corridor.png          32 × 32    opaque, seamless
desk.png                    64 × 48    transparent
box.png                     16 × 16    transparent
characters/<person>/body-<clip>.png
```

`<person>` is the person's key on the development map (their GitHub login,
or the alias they were given), e.g. `gencay`, `eneserman`.

Style: pixel art, top-down 3/4 view, a crisp one-pixel grid with no
anti-aliasing, no text, no logos. The office is dark navy and warm wood with
cobalt and gold accents, lit from the top left.

## Rooms and props

The engine walks people on the open floor, so it relies on where things are
in the room art. These are the anchors of the current kit (room
coordinates). A kit that moves them needs the constants in
`apps/web/src/components/agents/dev/office/world.ts` to move too.

| Room | Anchor | Use |
|------|--------|-----|
| product | back wall ends at y 72 | the floor starts there |
| product | plaque `{x 125, y 18, w 36, h 9}` | the project's logo is drawn on it |
| product | bookcase (left wall, x < 28, y < 124), plant (right wall, x > 290, y < 90) | not walked through |
| product-backdoor | `backThreshold {x 198, y 72}` | the back door; people use it to reach the corridor behind |
| cluster | plaque `{x 167, y 33, w 51, h 15}` | reads CLUSTER |
| cluster | `loadingTarget {x 192, y 147}` | where deliveries are put down |
| cluster | racks on the back wall (x 46–136, 250–338) and the side walls | they light up when a delivery lands |
| lobby | `logoSlot {x 324, y 155, w 107, h 17}` | the Tracesis mark |
| lobby | front door gap x 180–268 | the way in and out |
| lobby | sofas with tables, plants, reception desk (x ≥ 296, y ≥ 124) | not walked through |

Product rooms and the cluster are open at the front (their bottom edge, no
wall drawn). The lobby has a low front wall with a door gap.

**Desk** (`desk.png`, 64 × 48): the chair at the back (rows 0–19), the desk
top from row 20, the monitor seen from behind in the middle of the desk. A
seated person (the cross-legged `body-sit-type` pose, keyboard on the lap)
is drawn after the chair and before the desk top, feet at desk (34, 31): the
chair sits behind them, head, shoulders and chest show above the desk, the
monitor stands in front of their hands and the legs are hidden.

**Box** (`box.png`, 16 × 16): the delivery lying on the loading square.

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

Whatever its density, a character takes 32 × 48 art pixels of the office.
The density is read from each person's strips, so people of different
densities can share an office.

**Zoom.** The office is drawn at Z device pixels per art pixel, Z whole. The
levels are the divisors of the densest character's density below it and its
multiples from it up (for 4×: 1, 2, 4, 8, 12…), so every pixel is always a
whole number of device pixels. At Z ≥ the density, a character's pixels are
drawn nearest-neighbour, Z / density device pixels each (Z = 4 shows a 4×
face pixel for pixel). Below it, the frame is first shrunk by the exact
factor (4:1 or 2:1): each block averaged, weighted by its coverage, opaque
when at least half covered. That copy is made once per frame and factor and
kept. Nothing is ever resampled by an arbitrary ratio.

The office opens, and "fit" returns, at the largest level where its whole
width fits the panel. It is 1072 art pixels wide, so a 1440-wide Retina
laptop shows all of it at Z = 2 (one CSS pixel per art pixel). A phone shows
part of it at Z = 1 and pans.

Someone without a folder (a newcomer) walks as a shared placeholder body
with a head made from their portrait, their GitHub picture, or their
initials, until their character is added.

## `asset-manifest.json`

What the page reads from it:

- `environment.<name>.file`: the room and prop files.
- `environment.room-product-backdoor.backThreshold.x`: the back door.
- `characters.frameSize` and `characters.anchor`: the feet, when the frame
  size matches a person's strips (otherwise the defaults above).
- `characters.variants.<person>.sheets.<clip>`: each person's strips.

Paths are relative to `public/office/`, PNG only; anything else is ignored.
