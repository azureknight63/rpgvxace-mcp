"""Path rules: what a caller may name, and where a name may lead.

The bottom of this module's layering, and the reason the three project
errors live here rather than with `Project`: `safe_component` raises one,
and an error defined above the code that raises it is a cycle.

Everything this server opens is named either by its own registry or by a
caller, and this server feeds the model text out of the project itself --
map names, dialogue, script source. So a name that reaches the filesystem
is treated as something an attacker may have influenced, and it is judged
here rather than resolved somewhere later.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Any


WORK_DIR = ".rpgvxace-mcp"


BACKUP_DIR = "backups"


CHANGE_LOG = "changes.jsonl"


BACKUP_STAMP_FORMAT = "%Y%m%d-%H%M%S"


TMP_SUFFIX = ".rpgvxace-mcp.tmp"


DEFAULT_BACKUP_KEEP = 10


# A single path component that cannot lead anywhere but where it is joined.
# Everything this server opens is named either by its own registry or by a
# caller, and a caller's string reaches the filesystem, so it is checked here.
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")


# Names Windows resolves to devices rather than files, whatever the extension.
# safe_component is meant to be the one place a caller's name is judged, so it
# refuses these rather than leaving each call site to prove it cannot reach one.
_RESERVED_NAMES = frozenset(
    ["con", "prn", "aux", "nul"]
    + ["com%d" % n for n in range(1, 10)]
    + ["lpt%d" % n for n in range(1, 10)]
)


# A snapshot directory this server made: the stamp, and an optional label.
_BACKUP_STAMP = re.compile(r"^\d{8}-\d{6}(-[A-Za-z0-9._-]+)?$")


def _is_reparse_point(path: Path) -> bool:
    """Whether this is a symlink, a junction, or any other reparse point.

    `Path.is_symlink()` is False for an NTFS directory junction -- which is
    why `os.path.isjunction` had to be added in 3.12 -- so the prune filter
    that meant to step over a directory it did not create stepped straight
    into one.

    What that actually did is worth recording, because it is not what a review
    of this code predicted. The prediction was that `shutil.rmtree` would
    recurse through the junction and delete the target's contents. Measured on
    3.11: rmtree refuses ("Cannot call rmtree on a symbolic link") and the
    target survives -- and it never gets that far anyway, because
    `_contained` resolves the junction to its target, finds it outside the
    backup root, and raises. So there was no data loss. What there was is one
    foreign directory failing the prune for the whole project, with a message
    about containment rather than about a directory that was never ours.

    Three checks because no single one covers every version: `is_symlink` for
    a real symlink, `os.path.isjunction` where it exists, and the reparse
    attribute for 3.11 on Windows.
    """
    if path.is_symlink():
        return True
    isjunction = getattr(os.path, "isjunction", None)
    if isjunction is not None and isjunction(path):
        return True
    try:
        attributes = os.lstat(path).st_file_attributes
    except (AttributeError, OSError):
        # Not Windows, or the entry went away between listing and asking.
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


class ProjectError(RuntimeError):
    """Raised for problems with the project itself, not with a request."""


class ReadOnlyError(ProjectError):
    """Raised when something would write while --read-only is in force."""


class StaleFileError(ProjectError):
    """Raised when a file changed on disk after this server read it."""


def safe_component(name: Any, what: str) -> str:
    """One path component, with no way out of the directory it is joined to.

    Tool arguments reach the filesystem, and this server feeds the model text
    out of the project itself -- map names, dialogue, script source -- so a path
    argument has to be treated as something an attacker may have influenced. A
    name with a separator, a drive letter, or ``..`` in it is refused here
    rather than resolved somewhere later.
    """
    if not isinstance(name, str) or not _SAFE_COMPONENT.match(name):
        raise ProjectError(
            "%r is not a usable %s. It has to be a single name made of letters, "
            "digits, dots, dashes, plus signs or underscores -- no directory "
            "separators, drive letters or \"..\"." % (name, what)
        )
    if name.split(".")[0].lower() in _RESERVED_NAMES or name.endswith("."):
        raise ProjectError(
            "%r is not a usable %s: Windows resolves that name to a device "
            "rather than a file." % (name, what)
        )
    return name


def _contained(path: Path, inside: Path, what: str) -> Path:
    """Assert a built path really is under the directory it should be.

    Belt to :func:`safe_component`'s braces: a symlink or junction inside the
    tree could otherwise carry a validated name somewhere else.
    """
    resolved = path.resolve()
    if not resolved.is_relative_to(inside.resolve()):
        raise ProjectError(
            "%s resolves to %s, which is outside %s" % (what, resolved, inside)
        )
    return resolved
