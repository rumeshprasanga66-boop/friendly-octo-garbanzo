"""Personal avatar support: turning an authorised photo into a companion pack.

See ``docs/avatar.md`` for the limits of what this does and does not claim.
"""

from __future__ import annotations

from dodaios.avatar.portrait import (
    FRAME_SIZE,
    TALKING_FRAMES,
    PortraitResult,
    PortraitSpec,
    build_portrait_pack,
)

__all__ = [
    "FRAME_SIZE",
    "TALKING_FRAMES",
    "PortraitResult",
    "PortraitSpec",
    "build_portrait_pack",
]
