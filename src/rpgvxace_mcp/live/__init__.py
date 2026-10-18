"""Live control of a running game: a scratch copy, a bridge, a session.

`live` is the one part of this server that runs the game rather than reading
its files. It never runs the project itself: `session` copies the project to
a scratch folder, `bridge` injects a Ruby script into the copy's script list,
and the two halves talk over files in the copy (`wire`). `winapi` captures
a screenshot and `frame` readies it for `render.png`, the package's one PNG
encoder; `winapi` also holds the job object that ends the game when this
server ends.

See docs/live.md for the protocol, its limits, and what it cannot do.
"""
