"""Which of a real project's files can be held up as what the editor wrote.

The opt-in real-project tests treat the author's game as ground truth: the
shape numbers the editor stored, the field order it wrote, the bytes it
produced. That holds only while the editor is the only thing that has written
those files. The game is also edited through this server (and by older builds
of it, which wrote some fields in another order), and every such write is
logged in `.rpgvxace-mcp/changes.jsonl`.

So a file counts as editor-written when it has not been modified since the
server first logged a change to the project. A project the server never
touched has no log, and every file counts. This errs on the side of leaving
a file out: the editor saving a map after the first logged change takes it
out of the ground truth too, which only costs coverage, where counting a
server-written file would fail a test over data nobody wrote the editor's way.
"""

import json
from datetime import datetime
from pathlib import Path


LOG = Path(".rpgvxace-mcp") / "changes.jsonl"


def first_logged_change(root):
    """Seconds since the epoch of the earliest logged change, or None."""
    try:
        text = (Path(root) / LOG).read_text(encoding="utf-8")
    except OSError:
        return None
    earliest = None
    for line in text.splitlines():
        try:
            at = datetime.fromisoformat(json.loads(line)["at"]).timestamp()
        except (ValueError, KeyError, TypeError):
            continue
        if earliest is None or at < earliest:
            earliest = at
    return earliest


def editor_written(project, filename):
    """True unless `filename` (a name in Data) changed after the first logged change."""
    cutoff = first_logged_change(project.root)
    if cutoff is None:
        return True
    path = Path(project.root) / "Data" / filename
    try:
        return path.stat().st_mtime < cutoff
    except OSError:
        return False


def editor_written_map_ids(project):
    """The ids of the maps on disk that can stand as the editor's own output."""
    extension = project.profile.extension
    return [map_id for map_id in project.map_ids_on_disk()
            if editor_written(project, "Map%03d%s" % (map_id, extension))]
