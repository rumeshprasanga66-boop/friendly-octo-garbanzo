"""The portrait pack builder: geometry, the talking row, and real validation.

The important assertions here are not "it produced a file". They are that the
generated sheet and manifest pass the repository's OWN validator
(``dodaios.ui.pets.manifest.parse_manifest`` + ``check_cells``), and that the
talking row really does run from a closed mouth to an open one — because the
pet renderer animates the mouth from that row and nothing else.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from dodaios.avatar.portrait import (
    FRAME_SIZE,
    TALKING_FRAMES,
    PortraitSpec,
    build_portrait_pack,
)
from dodaios.ui.pets.loader import load_pet
from dodaios.ui.pets.manifest import (
    MAX_SHEET_EDGE,
    check_cells,
    parse_manifest,
)
from dodaios.ui.pets.states import MAX_FRAMES_PER_STATE, PET_STATES


def _photo(path: Path, size: tuple[int, int] = (640, 480)) -> Path:
    """A synthetic 'photo': a gradient with a lighter oval where a face would be."""
    image = Image.new("RGB", size, (150, 140, 130))
    draw = ImageDraw.Draw(image)
    draw.ellipse(
        (size[0] * 0.3, size[1] * 0.1, size[0] * 0.7, size[1] * 0.75),
        fill=(196, 160, 138),
    )
    image.save(path)
    return path


def test_sheet_is_within_the_repos_own_limits(tmp_path: Path) -> None:
    photo = _photo(tmp_path / "me.png")
    result = build_portrait_pack(photo, tmp_path / "pack")

    with Image.open(result.sheet_path) as sheet:
        width, height = sheet.size
        assert width <= MAX_SHEET_EDGE, "the repo rejects a sheet wider than 512"
        assert height <= MAX_SHEET_EDGE, "the repo rejects a sheet taller than 512"
        # One row per state, in PET_STATES order.
        assert height == len(PET_STATES) * FRAME_SIZE
        assert width % FRAME_SIZE == 0


def test_generated_pack_passes_the_real_manifest_validator(tmp_path: Path) -> None:
    """The pack must satisfy the repo's parser, not merely be well-formed JSON."""
    photo = _photo(tmp_path / "me.png")
    result = build_portrait_pack(photo, tmp_path / "pack")

    data = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    manifest = parse_manifest(data, builtin=False)
    assert manifest.frame_size == FRAME_SIZE
    assert set(manifest.animations) == set(PET_STATES)
    with Image.open(result.sheet_path) as sheet:
        # Raises PetManifestError if any animation reaches outside the sheet.
        check_cells(manifest, *sheet.size)


def test_pack_loads_through_the_real_pet_loader(tmp_path: Path) -> None:
    """End to end through the loader the overlay actually uses."""
    photo = _photo(tmp_path / "me.png")
    result = build_portrait_pack(photo, tmp_path / "pack")

    pack = load_pet(result.pack_dir, builtin=False)
    assert pack is not None
    assert pack.manifest.frame_size == FRAME_SIZE
    # Every state resolves, including any this builder does not draw separately.
    for state in PET_STATES:
        resolved, spec = pack.manifest.spec_for(state)
        assert spec.frames >= 1
        assert resolved in PET_STATES


def _dark_pixels(sheet: Image.Image, row: int, index: int) -> int:
    """Count mouth-ink pixels in one cell of the sheet."""
    cell = sheet.crop(
        (
            index * FRAME_SIZE,
            row * FRAME_SIZE,
            (index + 1) * FRAME_SIZE,
            (row + 1) * FRAME_SIZE,
        )
    )
    # The mouth ink is dark; the synthetic photo is a light gradient.
    return sum(1 for pixel in cell.get_flattened_data() if sum(pixel) < 210)


def test_talking_row_opens_from_closed_to_widest(tmp_path: Path) -> None:
    """The mouth must actually move, or the avatar would be a still photo."""
    photo = _photo(tmp_path / "me.png")
    result = build_portrait_pack(photo, tmp_path / "pack")

    with Image.open(result.sheet_path) as sheet:
        rgb = sheet.convert("RGB")
        row = PET_STATES.index("talking")
        counts = [_dark_pixels(rgb, row, i) for i in range(TALKING_FRAMES)]

    assert counts == sorted(counts), f"mouth should widen monotonically: {counts}"
    assert counts[-1] > counts[0], f"widest frame must exceed closed: {counts}"


def test_idle_row_has_the_blink_accent(tmp_path: Path) -> None:
    photo = _photo(tmp_path / "me.png")
    result = build_portrait_pack(photo, tmp_path / "pack")

    data = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    idle = data["animations"]["idle"]
    assert idle["accent_frames"] == 1
    assert idle["accent_every"] >= 2, "a blink on every loop is not an accent"

    with Image.open(result.sheet_path) as sheet:
        rgb = sheet.convert("RGB")
        row = PET_STATES.index("idle")
        frames = idle["frames"]
        first = _dark_pixels(rgb, row, 0)
        accent = _dark_pixels(rgb, row, frames - 1)

    assert accent > first, "the accent frame should be the blink"


def test_frame_counts_are_within_the_repo_limit(tmp_path: Path) -> None:
    photo = _photo(tmp_path / "me.png")
    result = build_portrait_pack(photo, tmp_path / "pack")
    data = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    for state, spec in data["animations"].items():
        assert 1 <= spec["frames"] <= MAX_FRAMES_PER_STATE, state


def test_portrait_crop_override_is_honoured(tmp_path: Path) -> None:
    """A user-supplied crop must change the output, not be ignored."""
    photo = _photo(tmp_path / "me.png", (600, 600))
    wide = build_portrait_pack(photo, tmp_path / "a", spec=PortraitSpec(crop=(0.0, 0.0, 1.0, 0.5)))
    tight = build_portrait_pack(
        photo, tmp_path / "b", spec=PortraitSpec(crop=(0.25, 0.25, 0.75, 0.75))
    )
    assert wide.sheet_path.read_bytes() != tight.sheet_path.read_bytes()


def test_spec_rejects_impossible_values() -> None:
    with pytest.raises(ValueError):
        PortraitSpec(crop=(0.5, 0.0, 0.25, 1.0)).validate()
    with pytest.raises(ValueError):
        PortraitSpec(mouth=(1.5, 0.5)).validate()
    with pytest.raises(ValueError):
        PortraitSpec(mouth_size=(0.0, 0.1)).validate()
    with pytest.raises(ValueError):
        PortraitSpec(mouth_open_gain=0.5).validate()


def test_missing_photo_and_tiny_photo_fail_clearly(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        build_portrait_pack(tmp_path / "nope.png", tmp_path / "pack")

    tiny = tmp_path / "tiny.png"
    Image.new("RGB", (20, 20), (10, 10, 10)).save(tiny)
    with pytest.raises(ValueError, match="too small"):
        build_portrait_pack(tiny, tmp_path / "pack2")


def test_builder_is_deterministic(tmp_path: Path) -> None:
    """Same photo, same spec, same bytes — so the sheet can be cached/hashed."""
    photo = _photo(tmp_path / "me.png")
    first = build_portrait_pack(photo, tmp_path / "one")
    second = build_portrait_pack(photo, tmp_path / "two")
    assert first.sheet_path.read_bytes() == second.sheet_path.read_bytes()


def test_sheet_never_contains_the_overlay_colour_key(tmp_path: Path) -> None:
    """Magenta is the overlay's transparency key; a sprite must not use it."""
    photo = _photo(tmp_path / "me.png")
    result = build_portrait_pack(photo, tmp_path / "pack")
    with Image.open(result.sheet_path) as sheet:
        rgb = sheet.convert("RGB")
        assert (255, 0, 255) not in set(rgb.get_flattened_data())
