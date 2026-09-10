"""`validate`: every rule, and the report it builds.

The top layer of `analysis`, and the largest: it reads `walk` for its
traversals and `references` for the rules that say which command parameter
names which table. Nothing here is imported by either of them, which is
what keeps the layering acyclic -- and `misc_tools` is the only module in
the tools layer that imports anything from here.

One module until it reached fifteen hundred lines, at which point a reader
after one rule read every other rule on the way. The seven modules below
are ordered, and `report` is the only one that knows them all:

    findings <- {maps, pages, command_rules, assets, database} <- report

with three edges inside the middle five, each because two rules share a
piece of machinery rather than a subject:

* `command_rules` reads `maps` for `_MapSizes`, since a Transfer Player is
  checked against the size of the map it names;
* `database` reads `assets` for `_AssetChecker`, since a tileset's nine
  sheets are files on disk;
* `command_rules` reads `assets` for the same checker, since the two asset
  rules that need a command list ride the walk `check_commands` already
  makes rather than making a second one.

There were two until that last one landed, and this sentence said two for a
round afterwards. `tests/test_schema.py` holds the table itself, which is
why the code was right while the prose was not.

Re-exported from here so `from .lint import X` and `from ..analysis import
X` both keep working: the split is about how this code is read and changed,
not about making every caller learn which of seven files a name lives in.

One warning for anyone writing a test against it. `MAX_FINDINGS` is bound
in each module that reads it, so patching it here changes nothing that
runs; `ToolTest.lower_the_finding_ceiling` finds every binding by scanning
and proves that it found them.
"""

from __future__ import annotations

from . import (  # noqa: F401
    assets,
    command_rules,
    database,
    findings,
    maps,
    pages,
    report,
)
from .findings import (  # noqa: F401
    DEFAULT_FINDING_LIMIT,
    ERROR,
    Finding,
    INFO,
    MAX_FINDINGS,
    SEVERITIES,
    WARNING,
    _Findings,
    _Tables,
    _missing,
)
from .maps import (  # noqa: F401
    _MapSizes,
    _check_encounters,
    _check_map_dimensions,
    _encounter_troop_id,
    _parent_cycles,
    check_map_tree,
    check_maps,
)
from .pages import (  # noqa: F401
    CONTROL_RELEASING_CODES,
    OPAQUE_ESCAPE_CODES,
    _check_autorun,
    _check_shadowing,
    _is_unconditional,
    check_event_pages,
)
from .command_rules import (  # noqa: F401
    _check_indent,
    _check_jump,
    _check_references,
    _check_transfer,
    check_commands,
    check_list_terminator,
)
from .assets import (  # noqa: F401
    _AssetChecker,
    _check_command_assets,
    _check_database_assets,
    _check_map_assets,
    check_assets,
    command_assets,
    map_assets,
    page_graphic,
)
from .database import (  # noqa: F401
    NESTED_DB_REFERENCES,
    _check_start_position,
    _check_system,
    check_database,
    check_tilesets,
)
from .report import (  # noqa: F401
    VALIDATE_SCOPES,
    _report,
    _resolve_map_ids,
    validate,
)
