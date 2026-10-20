"""Where a player can walk, from a tileset's flag words: the engine's rules.

Pure functions over tile ids and flags -- no pictures, no projects -- so the
rules can be tested against the engine's own source rather than against a
render. This is RGSS3's ``Game_Map``::

    def check_passage(x, y, bit)
      all_tiles(x, y).each do |tile_id|
        flag = tileset.flags[tile_id]
        next if flag & 0x10 != 0            # [star]: no effect on passage
        return true  if flag & bit == 0     # passable
        return false if flag & bit == bit   # impassable
      end
      return false                          # nothing decided
    end

``all_tiles`` is the tile ids of the events whose graphic is a tile, then the
three tile layers topmost first (z = 2, 1, 0). A move in direction ``d``
(2 down, 4 left, 6 right, 8 up) asks for ``bit = 1 << (d / 2 - 1)``: down
0x1, left 0x2, right 0x4, up 0x8. The walk is made once per bit, so the tile
that decides one direction need not decide another. The flags are looked up
by the tile id itself, whatever sheet it is on: an autotile's entry is at its
own id (``2048 + kind * 48 + shape``), and the engine has no special case for
the water of A1 -- whether water can be walked is whatever the editor wrote
in the flag word, and the boat and ship rules read other bits (0x200 and
0x400) that the player's never looks at.

Two things the engine does not do, and this module does so a hostile file
cannot crash a render: a tile id with no entry in the flags table is nil in
the engine (a crash); here it is treated as passable -- an entry of 0 -- and
the result says it happened, so the caller can report it.

The terrain tag is ``flags[tile_id] >> 12`` of the topmost tile (z = 2, 1, 0)
whose tag is above zero; unlike passability it does not skip star tiles.
"""

from __future__ import annotations

from typing import NamedTuple, Sequence

from .tiles import STAR_FLAG


# The four bits of a passage word, as the engine numbers them.
DOWN, LEFT, RIGHT, UP = 0x1, 0x2, 0x4, 0x8
DIRECTION_BITS = (DOWN, LEFT, RIGHT, UP)
EVERY_WAY = DOWN | LEFT | RIGHT | UP

# The priority_type that makes an event solid to the player.
SAME_AS_CHARACTERS = 1


class Passage(NamedTuple):
    """Which directions are blocked at one cell.

    ``blocked`` has a bit set for each direction in which the cell cannot be
    left or entered through that edge (the engine checks the same bit going
    out of one cell as coming into the next, reversed). ``unknown`` is True
    when a tile id the walk consulted has no entry in the flags table.
    """

    blocked: int
    unknown: bool


def flag_of(flags: Sequence[int], tile_id: int) -> int | None:
    """``flags[tile_id]`` as RGSS reads it, or None where there is no entry.

    A ``Table`` keeps its 16-bit words unsigned and RGSS reads them signed,
    which matters for exactly one thing: bit 15 makes the word negative, so
    ``flag >> 12`` is below zero and `terrain_tag` moves on.
    """
    if 0 <= tile_id < len(flags):
        word = flags[tile_id]
        return word - 0x10000 if word >= 0x8000 else word
    return None


def passage(stack: Sequence[int], flags: Sequence[int]) -> Passage:
    """Which edges of a cell block the player, for ``stack`` topmost first."""
    blocked = 0
    unknown = False
    for bit in DIRECTION_BITS:
        for tile_id in stack:
            flag = flag_of(flags, tile_id)
            if flag is None:
                # Nil in the engine; passable here, and said so.
                unknown = True
                break
            if flag & STAR_FLAG:
                continue
            if flag & bit:
                blocked |= bit
            break
        else:
            blocked |= bit      # no tile decided: the engine's final `false`
    return Passage(blocked, unknown)


def terrain_tag(stack: Sequence[int], flags: Sequence[int]) -> tuple[int, bool]:
    """``(tag, unknown)`` for a cell: the topmost tile with a tag above zero."""
    unknown = False
    for tile_id in stack:
        flag = flag_of(flags, tile_id)
        if flag is None:
            unknown = True
            continue
        tag = flag >> 12
        if tag > 0:
            return tag, unknown
    return 0, unknown


def event_blocks(through, priority_type) -> bool:
    """Whether an event page is solid: not through, and same as characters.

    ``through`` follows Ruby truthiness (only nil and false are false);
    ``priority_type`` must be the integer 1, not ``true`` or a string.
    """
    if through is not None and through is not False:
        return False
    return (isinstance(priority_type, int) and not isinstance(priority_type, bool)
            and priority_type == SAME_AS_CHARACTERS)
