# Personal avatar from a photo

This page describes `dodaios/avatar/`, which turns one authorised photograph
into a desktop companion whose mouth moves while Jarvis speaks.

## What it actually is

It is a **stylised 2D portrait whose mouth follows the voice**. It is built on
the existing desktop-pet system (`docs/pets.md`), so it reuses the overlay, the
states, the control strip and the drag/position handling. Nothing new is added
to the audio path.

It is **not** a photoreal talking head. There is no 3D model, no viseme or
phoneme model, no jaw reconstruction, no head pose, and no lip-sync to the
actual words being spoken. The mouth opens and closes with the loudness of the
speech, exactly as a cartoon pet's does. A still photograph does not become an
animated human being.

## How the mouth moves

`ui/orb/pet_renderer.py` already animates a pet's `talking` row from the live
speech level: the row is drawn closed → widest, and the frame is chosen from
the smoothed output RMS (`level_tap.py` feeds it out of band, latency
compensated). The builder therefore only has to produce a valid sprite sheet
whose `talking` row runs from a closed mouth to an open one. Frame 0 is closed,
the last frame is the widest, and `level_frame` picks the frame whose index
matches the smoothed level — the row is stepped through, not blended.

## Usage

```python
from dodaios.avatar import PortraitSpec, build_portrait_pack

result = build_portrait_pack(
    "C:/Users/me/Pictures/me.jpg",
    "C:/Users/me/AppData/Roaming/dodaios/pets",   # the user-pets root
    name="Me",
)
print(result.pack_dir)   # <root>/<pet_id>
```

The pack is written as `<root>/<pet_id>/portrait.png` plus `pet.json`, which is
the layout `PetStore` uses and the layout the loader requires (the folder name
must equal the manifest `id`). The id is `u` plus the first 16 hex digits of the
photo's SHA-256, so running the builder twice on the same photo overwrites one
pack instead of filling the folder with duplicates.

Pick the pack under **Settings → My Pets**, or set `[ui] pet` to the id.

## Adjusting the crop and the mouth

The defaults suit a typical head-and-shoulders photograph. If the mouth lands
in the wrong place, override the spec — every value is a fraction, so one spec
works at any resolution:

```python
spec = PortraitSpec(
    crop=(0.15, 0.05, 0.85, 0.95),  # left, top, right, bottom of the source
    mouth=(0.50, 0.74),             # mouth centre in the cropped portrait
    mouth_size=(0.32, 0.05),        # width, closed height
    eye_y=0.42,                     # eye line, for the idle blink
    mouth_open_gain=4.0,            # how far the mouth opens at full level
)
build_portrait_pack(photo, root, spec=spec)
```

There is **no automatic face detection**. Detecting a face would mean an
OpenCV wheel (tens of megabytes) on a machine this project targets at 8 GB RAM
and limited disk, so the regions are fractions and the caller adjusts them.
The builder is Pillow-only, which is already a core dependency — it adds
nothing to the install.

## States

The generated pack draws all ten pet states in `PET_STATES` order, so the
figure reacts to Jarvis the way every other pet does: `idle` (with a blink
accent every third loop), `listening`, `thinking`, `talking`, `success`,
`error`, `sleeping`, `working`, `searching`, `held`. The states are described
in `docs/pets.md`.

## Privacy

The builder reads the photo from disk and writes the pack to disk. It performs
no network access, no upload, and no telemetry. The photo is never copied
anywhere else. Deleting the pack folder removes everything it produced.

## Limits, stated plainly

- The mouth moves with **loudness**, not with the words. Sinhala, English and
  every other language get the same mouth movement.
- The portrait is **stylised and small** (48 px per frame, 384 x 480 sheet),
  matching the pet format's `MAX_SHEET_EDGE` of 512. It is a desk companion,
  not a full-screen render.
- Only the mouth and the blink are drawn. Eyebrows, head turns and gaze do not
  move.
- A 3D avatar is **not implemented**. `docs/pets.md` and the overlay styles are
  the supported surfaces; adding a 3D renderer would be a separate project and
  would need to fit the same 8 GB RAM / 3 GB VRAM budget.
