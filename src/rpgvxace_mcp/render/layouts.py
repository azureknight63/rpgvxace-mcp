"""What a scene needs to know about an engine's tile graphics, as one value.

`scene.render_map` composes a window of a map. Everything in it that depends on
which engine made the map is here: which graphic a tile id is cut from, how it
is cut, which tiles are drawn over characters, how a character frame is found
on its sheet, and which overlays the engine's rules are known well enough to
draw. The default, `ACE_LAYOUT`, is what the scene did before there were
layouts and calls the same functions, so a VX Ace picture does not change.

VX uses `ACE_LAYOUT` when asked to (its sheet sizes and block geometry were
measured and match; its id numbering is remembered, see
`docs/engine_parity.md`); XP has its own, built by `xp_layout`.
"""

from __future__ import annotations

from typing import Callable, NamedTuple

from . import overlays as marks
from .png import Image
from .tiles import STAR_FLAG, compose_tile, sheet_needed
from .xp_tiles import xp_compose_tile, xp_sheet_needed, xp_sprite_rect


class Layout(NamedTuple):
    """One engine's tile model, as the functions the scene calls."""

    key: str
    # tile id -> the sheet it is cut from (a key of the scene's ``sheets``).
    sheet_needed: Callable[[int], "str | None"]
    # (tile id, sheets) -> one 32 x 32 tile, or None.
    compose: Callable[[int, "dict[str, Image | None]"], "Image | None"]
    # The tile's entry in the flags table the scene was given -> whether the
    # tile is drawn over characters.
    above: Callable[[int], bool]
    # (sheet, name, index, direction, pattern) -> (sx, sy, w, h, lifted).
    sprite_rect: "Callable[..., tuple[int, int, int, int, bool]] | None"
    # Whether event graphics that are not tiles are drawn; if not, the reason
    # the scene gives for the marker it draws instead.
    draws_characters: bool
    characters_note: str
    # The overlays this engine's rules are known well enough to draw.
    overlays: tuple


def _ace_above(flag: int) -> bool:
    return flag & STAR_FLAG != 0


# `sprite_rect` is filled in by `scene`, which owns the `$`/`!` rules of Ace's
# character sheets; None means "the scene's own".
ACE_LAYOUT = Layout(
    key="ace",
    sheet_needed=sheet_needed,
    compose=compose_tile,
    above=_ace_above,
    sprite_rect=None,
    draws_characters=True,
    characters_note="",
    overlays=marks.OVERLAY_NAMES,
)


# VX with Ace's numbering and tables (REMEMBERED, see the module docstring),
# and without the overlays that need a flag table: VX's is not modelled here.
VX_LAYOUT = ACE_LAYOUT._replace(key="vx", overlays=(marks.EVENT_IDS,))


def _xp_above(priority: int) -> bool:
    return priority != 0


def xp_layout(autotiles: bool, characters: bool) -> Layout:
    """XP's layout; ``autotiles`` and ``characters`` are the remembered parts.

    Without them the layout draws what `docs/engine_parity.md` records as read
    from XP's own scripts and data -- tileset tiles, above or below characters
    by their priority -- and leaves the rest undrawn and counted.
    """
    return Layout(
        key="xp",
        sheet_needed=xp_sheet_needed,
        compose=lambda tile_id, sheets: xp_compose_tile(tile_id, sheets,
                                                        autotiles),
        above=_xp_above,
        sprite_rect=xp_sprite_rect,
        draws_characters=characters,
        characters_note="XP character sheets are drawn only with "
                        "unverified_formats",
        # passable? and terrain_tag are not established for XP, and the one
        # flag word `passability` reads does not exist there.
        overlays=(marks.EVENT_IDS,),
    )
