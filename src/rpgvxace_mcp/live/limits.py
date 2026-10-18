"""What the `live` tool needs to describe itself, without loading a session.

The tool's schema quotes these limits and its handler catches `LiveError`,
and both happen when the server starts. `session` is the Windows side --
`ctypes`, the screenshot path, the scratch copy -- and loading it for every
session of a server that mostly never starts a game was a large part of the
server's start-up. So the few names the surface needs live here, and
`session` imports them back: `session.BOOT_SECONDS` is still the name a test
shortens and the one `session` reads.
"""

from __future__ import annotations


class LiveError(ValueError):
    """Something about the session or the game, worded for the caller."""


# How long a game has to come up and write ready.bin, and how long one
# request has to be answered. Module-level so a test can shorten them.
BOOT_SECONDS = 60.0
REPLY_SECONDS = 10.0


# How large a project this will copy. A VX Ace game is tens of megabytes;
# one past this is not a game that needs testing this way.
MAX_SCRATCH_BYTES = 2 * 1024 * 1024 * 1024
MAX_SCRATCH_FILES = 100_000


# The largest PNG a screenshot returns. Above it the frame is halved until
# it fits, and the reply says so.
MAX_SCREENSHOT_BYTES = 1_000_000
