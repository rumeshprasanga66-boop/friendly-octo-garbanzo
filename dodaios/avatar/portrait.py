"""Turn one authorised photograph into a ``jarvis-pet/1`` companion pack.

The pet renderer already animates a mouth from the live speech level: its
``talking`` row is drawn closed -> widest and the frame is picked from the
smoothed output RMS (``ui/orb/pet_renderer.py``). So a personal portrait needs
no new renderer and no new audio plumbing — it needs a valid sprite sheet whose
``talking`` row runs from a closed mouth to an open one. That is all this module
builds.

Deliberately Pillow-only. Face detection would mean an OpenCV wheel (tens of
megabytes) on a machine the user describes as short on disk, so the regions are
fractions of the crop instead, and the caller may override each one. The result
is a *stylised* portrait whose mouth moves with the voice. It is NOT a
photoreal talking head: there is no viseme model, no head pose, no jaw
reconstruction. ``docs/avatar.md`` states the same limit in the same words.

Everything here is deterministic for a given photo and spec, so the tests can
assert exact sheet geometry.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter

log = logging.getLogger("dodaios.avatar.portrait")

#: Frame edge for a generated pack. 10 states at this size give a 480 px tall
#: sheet, inside ``manifest.MAX_SHEET_EDGE`` (512); 64 would need 640 and be
#: rejected. Kept as a constant so the geometry test can assert it.
FRAME_SIZE = 48

#: The talking row's frame count. Frame 0 is a closed mouth, the last frame is
#: the widest, and the renderer interpolates between them by speech level.
TALKING_FRAMES = 4

#: Rows, in ``PET_STATES`` order — a manifest may name another row, but the
#: sheet layout and every built-in pet follow this order.
_SHEET_ROWS: tuple[tuple[str, int, int, bool], ...] = (
    # state, frames, fps, loop
    ("idle", 8, 6, True),
    ("listening", 6, 8, True),
    ("thinking", 8, 8, True),
    ("talking", TALKING_FRAMES, 10, True),
    ("success", 8, 10, False),
    ("error", 6, 9, False),
    ("sleeping", 6, 3, True),
    ("working", 8, 8, True),
    ("searching", 8, 8, True),
    ("held", 6, 10, True),
)

#: Colour used for the drawn mouth interior and the blink. Not the overlay's
#: magenta key (255, 0, 255): a sprite must never contain that exact colour.
_MOUTH_INK = (58, 30, 34)
_LIP_INK = (122, 66, 66)


@dataclass(frozen=True)
class PortraitSpec:
    """How to crop one photo and where its features sit.

    Every region is a fraction of the *cropped* portrait, so one spec works at
    any source resolution. The defaults suit a typical head-and-shoulders
    photograph: eyes a little above the middle, mouth in the lower third.
    """

    #: Crop box as fractions ``(left, top, right, bottom)`` of the source.
    #: ``None`` takes a centred square covering the upper part of the frame.
    crop: tuple[float, float, float, float] | None = None
    #: Mouth centre, ``(x, y)`` as fractions of the cropped portrait.
    mouth: tuple[float, float] = (0.5, 0.72)
    #: Mouth width and closed-mouth height, as fractions of the portrait width.
    mouth_size: tuple[float, float] = (0.30, 0.045)
    #: Eye line height as a fraction of the portrait, for the idle blink.
    eye_y: float = 0.40
    #: Eye horizontal offsets from centre, as fractions of the portrait width.
    eye_dx: float = 0.16
    #: How much taller the mouth gets at the widest talking frame, as a
    #: multiple of the closed height. 1.0 would be no movement.
    mouth_open_gain: float = 4.0
    #: Apply a soft blur to the drawn mouth so it reads as part of the photo
    #: rather than a hard sticker.
    soften: bool = True

    def validate(self) -> None:
        """Raise ``ValueError`` on a spec that cannot produce a sane sheet."""
        if self.crop is not None:
            left, top, right, bottom = self.crop
            if not (0.0 <= left < right <= 1.0 and 0.0 <= top < bottom <= 1.0):
                raise ValueError("crop must be increasing fractions inside 0..1")
        mx, my = self.mouth
        if not (0.0 <= mx <= 1.0 and 0.0 <= my <= 1.0):
            raise ValueError("mouth must be fractions inside 0..1")
        width, height = self.mouth_size
        if width <= 0.0 or height <= 0.0:
            raise ValueError("mouth_size must be positive")
        if self.mouth_open_gain < 1.0:
            raise ValueError("mouth_open_gain must be at least 1.0")


@dataclass
class PortraitResult:
    """What :func:`build_portrait_pack` produced, for the caller to report."""

    pack_dir: Path
    manifest_path: Path
    sheet_path: Path
    manifest: dict[str, Any] = field(default_factory=dict)


def _crop_portrait(photo: Image.Image, spec: PortraitSpec) -> Image.Image:
    """Crop the source to the portrait region and normalise it to RGBA."""
    image = photo.convert("RGBA")
    width, height = image.size
    if spec.crop is None:
        # Centred square from the top: a head-and-shoulders photo keeps the
        # whole head, and a landscape shot is not cropped to a letterbox.
        edge = min(width, height)
        left = (width - edge) // 2
        return image.crop((left, 0, left + edge, edge))
    left, top, right, bottom = spec.crop
    return image.crop(
        (
            round(left * width),
            round(top * height),
            round(right * width),
            round(bottom * height),
        )
    )


def _fit(frame: Image.Image, size: int) -> Image.Image:
    """Cover ``size`` x ``size`` with ``frame``, centre-cropped, no distortion."""
    target = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    source = frame
    # Cover, not contain: a portrait should fill the cell. LANCZOS keeps the
    # photo from aliasing at these small sizes.
    ratio = max(size / source.width, size / source.height)
    scaled = source.resize(
        (max(1, round(source.width * ratio)), max(1, round(source.height * ratio))),
        Image.LANCZOS,
    )
    left = (scaled.width - size) // 2
    top = (scaled.height - size) // 2
    target.alpha_composite(scaled.crop((left, top, left + size, top + size)))
    return target


def _draw_mouth(frame: Image.Image, spec: PortraitSpec, openness: float) -> Image.Image:
    """Return a copy of ``frame`` with the mouth opened by ``openness`` (0..1)."""
    if openness <= 0.0:
        return frame
    size = frame.width
    mouth_w = spec.mouth_size[0] * size
    closed_h = spec.mouth_size[1] * size
    height = closed_h * (1.0 + (spec.mouth_open_gain - 1.0) * openness)
    cx = spec.mouth[0] * size
    cy = spec.mouth[1] * size

    layer = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    box = (cx - mouth_w / 2, cy - height / 2, cx + mouth_w / 2, cy + height / 2)
    draw.ellipse(box, fill=(*_MOUTH_INK, 235))
    # A thin lighter rim reads as a lip without inventing detail.
    draw.arc(box, start=200, end=340, fill=(*_LIP_INK, 170), width=max(1, size // 32))
    if spec.soften:
        layer = layer.filter(ImageFilter.GaussianBlur(radius=max(0.5, size / 64)))

    out = frame.copy()
    out.alpha_composite(layer)
    return out


def _draw_blink(frame: Image.Image, spec: PortraitSpec) -> Image.Image:
    """Return a copy of ``frame`` with the eyes closed (the idle accent frame)."""
    size = frame.width
    layer = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    eye_w = size * 0.16
    eye_h = max(1.0, size * 0.035)
    cy = spec.eye_y * size
    for sign in (-1, 1):
        cx = (0.5 + sign * spec.eye_dx) * size
        box = (cx - eye_w / 2, cy - eye_h / 2, cx + eye_w / 2, cy + eye_h / 2)
        draw.ellipse(box, fill=(*_MOUTH_INK, 210))
    if spec.soften:
        layer = layer.filter(ImageFilter.GaussianBlur(radius=max(0.4, size / 80)))
    out = frame.copy()
    out.alpha_composite(layer)
    return out


def _sheet_for(portrait: Image.Image, spec: PortraitSpec) -> Image.Image:
    """Draw every state's row into one ``jarvis-pet/1`` sheet."""
    size = FRAME_SIZE
    max_frames = max(frames for _, frames, _, _ in _SHEET_ROWS)
    sheet = Image.new("RGBA", (max_frames * size, len(_SHEET_ROWS) * size), (0, 0, 0, 0))

    base = _fit(portrait, size)
    for row, (state, frames, _fps, _loop) in enumerate(_SHEET_ROWS):
        for index in range(frames):
            if state == "talking":
                # Closed -> widest across the row; the renderer interpolates
                # between these cells by the live speech level.
                openness = index / max(1, frames - 1)
                cell = _draw_mouth(base, spec, openness)
            elif state == "idle" and index == frames - 1:
                # Last cell of the idle row is the blink accent
                # (accent_frames: 1, accent_every: 3 in the manifest).
                cell = _draw_blink(base, spec)
            elif state == "sleeping":
                cell = _draw_blink(base, spec) if index % 2 else base
            elif state == "success":
                cell = _draw_mouth(base, spec, 0.55 if index % 2 else 0.25)
            elif state == "error":
                cell = _draw_mouth(base, spec, 0.35)
            else:
                cell = base
            sheet.alpha_composite(cell, (index * size, row * size))
    return sheet


def _manifest_dict(spec: PortraitSpec, name: str, description: str, pet_id: str) -> dict[str, Any]:
    """The ``pet.json`` body describing :data:`_SHEET_ROWS`."""
    animations: dict[str, Any] = {}
    for row, (state, frames, fps, loop) in enumerate(_SHEET_ROWS):
        entry: dict[str, Any] = {
            "row": row,
            "frames": frames,
            "fps": fps,
            "loop": loop,
        }
        if state == "idle":
            # One blink every three idle loops rather than on every pass.
            entry["accent_frames"] = 1
            entry["accent_every"] = 3
        animations[state] = entry
    return {
        "format": "jarvis-pet/1",
        "id": pet_id,
        "name": name,
        "description": description,
        "frame_size": FRAME_SIZE,
        "sheet": "portrait.png",
        "animations": animations,
    }


def build_portrait_pack(
    photo_path: str | Path,
    pack_dir: str | Path,
    *,
    spec: PortraitSpec | None = None,
    name: str = "My portrait",
    description: str = "A stylised portrait whose mouth follows my voice.",
) -> PortraitResult:
    """Build a companion pack from ``photo_path`` into ``pack_dir``.

    Writes ``portrait.png`` and ``pet.json``. The caller is responsible for
    handing the photo to this function only with the owner's authorisation;
    nothing here uploads or transmits the image.
    """
    spec = spec or PortraitSpec()
    spec.validate()

    source = Path(photo_path)
    if not source.is_file():
        raise FileNotFoundError(f"no such photo: {source}")
    root = Path(pack_dir)

    with Image.open(source) as opened:
        opened.load()
        portrait = _crop_portrait(opened, spec)
    if portrait.width < FRAME_SIZE or portrait.height < FRAME_SIZE:
        raise ValueError(
            f"the photo is too small to build a {FRAME_SIZE}px portrait "
            f"({portrait.width}x{portrait.height} pixels after cropping)"
        )

    # A user pet id is 'u' plus 16 hex digits (manifest.USER_ID_RE), and the
    # loader requires the FOLDER to be named after the id — so the id is
    # derived from the photo and the pack lives at <root>/<pet_id>. Re-running
    # on the same photo therefore overwrites one pack instead of littering.
    pet_id = "u" + hashlib.sha256(source.read_bytes()).hexdigest()[:16]
    pack = root / pet_id
    pack.mkdir(parents=True, exist_ok=True)

    sheet = _sheet_for(portrait, spec)
    sheet_path = pack / "portrait.png"
    sheet.save(sheet_path, format="PNG", optimize=True)

    manifest = _manifest_dict(spec, name, description, pet_id)
    manifest_path = pack / "pet.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    log.info("portrait pack written: %s (%dx%d)", sheet_path, sheet.width, sheet.height)
    return PortraitResult(
        pack_dir=pack,
        manifest_path=manifest_path,
        sheet_path=sheet_path,
        manifest=manifest,
    )
