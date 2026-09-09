"""`analysis`: the walks, the reference rules, and the linter.

One module until it reached two thousand lines over three separable
subjects, at which point the dependency boundary and the criticality
boundary turned out to coincide: everything the four map tool modules import
lives in `walk`, everything the two delete paths import lives in
`references`, and `misc_tools` is the only importer of anything in `lint`.
That last claim is a test rather than a sentence -- `test_schema.py` reads
every tool module's imports and requires that only `misc_tools` takes a
name from `lint` -- because it is the reason the boundary is where it is,
and a second importer would make it false while leaving it written down.

`lint` is itself a package of seven modules now, for the same reason at the
next scale down; its own `__init__` gives that order.

Re-exported from here so `from ..analysis import X` keeps working: the split
is about how this code is read and changed, not about making every caller
learn which of three files a name lives in. `tests/test_schema.py` holds
both layerings -- a tool module is a leaf, these three are ordered
`walk <- references <- lint`, and every module in either package is named in
the table that constrains it.
"""

from .walk import (  # noqa: F401
    Location,
    MAX_WALK_STEPS,
    WalkBudget,
    _db_at,
    _event_at,
    _int,
    _map_at,
    _map_file_name,
    _safe_entries,
    _seq,
    _text,
    iter_command_lists,
    iter_maps,
)
from . import lint, references, walk  # noqa: F401
from .references import (  # noqa: F401
    DEFAULT_REFERENCE_LIMIT,
    MAP_TARGET,
    REFERENCE_PREVIEW,
    REFERENCE_RULES,
    ReferencePreview,
    TABLE_NOUNS,
    _as_preview,
    _referenced_ids,
    _requires_met,
    find_references,
    find_references_for,
    reference_preview,
    reference_previews,
)
# Every name `lint` re-exports, and the same set: this list and the one
# in `lint/__init__` were written by hand and had drifted eleven names
# apart, all of them private -- so `analysis._check_system` resolved
# and `analysis._check_autorun` did not, and the public-name test
# could not see the difference. `test_schema.py` holds the two lists
# equal now, which is why this is still written out rather than
# derived: a reader can see what is here, and a test makes it right.
from .lint import (  # noqa: F401
    CONTROL_RELEASING_CODES,
    DEFAULT_FINDING_LIMIT,
    ERROR,
    Finding,
    INFO,
    MAX_FINDINGS,
    NESTED_DB_REFERENCES,
    OPAQUE_ESCAPE_CODES,
    SEVERITIES,
    VALIDATE_SCOPES,
    WARNING,
    _AssetChecker,
    _Findings,
    _MapSizes,
    _Tables,
    _check_autorun,
    _check_command_assets,
    _check_database_assets,
    _check_encounters,
    _check_indent,
    _check_jump,
    _check_map_assets,
    _check_map_dimensions,
    _check_references,
    _check_shadowing,
    _check_start_position,
    _check_system,
    _check_transfer,
    _encounter_troop_id,
    _is_unconditional,
    _missing,
    _parent_cycles,
    _report,
    _resolve_map_ids,
    check_assets,
    check_commands,
    check_database,
    check_event_pages,
    check_list_terminator,
    check_map_tree,
    check_maps,
    check_tilesets,
    validate,
)
