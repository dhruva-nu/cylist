"""Identity colours for projects and people, and a separate one for goals.

Colours are picked from a fixed palette rather than generated, so every avatar
and project mark sits in the same family as the rest of the interface. The
choice is deterministic — the same name always produces the same colour — so a
project does not change appearance when rows are reordered or re-seeded.

The values are the accent colours from the approved design; none of them is the
brand yellow or the semantic status colours, which are reserved.

Every one is dark enough for white initials to clear WCAG AA on it, which is
why the amber and the sage are a shade deeper here than in the mock: an avatar
is text, and 3.3:1 initials are a decoration of a name rather than a name.

A goal's colour is never written as text — it is a rail down a card's edge, a
dot beside a name, a fill on a progress track — so it does not owe the same
debt to white initials that :data:`PALETTE` does, and can afford to be a good
deal louder. :data:`GOAL_PALETTE` is that: the same eight-hue family, pushed to
a saturation the avatar set was never allowed.
"""

from __future__ import annotations

import hashlib

PALETTE: tuple[str, ...] = (
    "#1D7D46",  # green
    "#3B6FC2",  # blue
    "#A76900",  # amber
    "#7A6B9E",  # violet
    "#8E6A3D",  # clay
    "#278655",  # sage
    "#B5533F",  # rust
    "#4A7B8C",  # slate blue
)

GOAL_PALETTE: tuple[str, ...] = (
    "#1FAA5C",  # green
    "#2F6FEB",  # blue
    "#E8890B",  # amber
    "#8B5CF6",  # violet
    "#0D9488",  # teal
    "#DB2777",  # rose
    "#E5484D",  # coral
    "#0891B2",  # cyan
)


def colour_for(name: str, palette: tuple[str, ...] = PALETTE) -> str:
    """Return a stable palette colour for a name.

    Uses BLAKE2b rather than :func:`hash` because Python salts string hashing
    per process, which would give the same project a different colour on every
    restart.
    """
    digest = hashlib.blake2b(name.strip().casefold().encode(), digest_size=8).digest()
    return palette[int.from_bytes(digest) % len(palette)]
