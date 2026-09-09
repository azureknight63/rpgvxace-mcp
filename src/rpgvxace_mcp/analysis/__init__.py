"""`analysis`: the walks, the reference rules, and the linter.

One module until it reached two thousand lines over three separable
subjects, at which point the dependency boundary and the criticality
boundary turned out to coincide: everything the four map tool modules import
lives in `walk`, everything the two delete paths import lives in
`references`, and `misc_tools` is the only importer of anything in `lint`.

Re-exported from here so `from ..analysis import X` keeps working: the split
is about how this code is read and changed, not about making every caller
learn which of three files a name lives in. `tests/test_schema.py` holds
the layering itself -- a tool module is a leaf, and these three are ordered
`walk <- references <- lint`.
"""

from .walk import (  # noqa: F401
    Location,
    MAX_WALK_STEPS,
    WalkBudget,
    _brief,
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
    TABLE_NOUNS,
    ReferencePreview,
    _as_preview,
    _referenced_ids,
    _requires_met,
    find_references,
    find_references_for,
    reference_preview,
    reference_previews,
)
from .lint import (  # noqa: F401
    DEFAULT_FINDING_LIMIT,
    ERROR,
    Finding,
    INFO,
    MAX_FINDINGS,
    SEVERITIES,
    VALIDATE_SCOPES,
    WARNING,
    _AssetChecker,
    _Findings,
    _MapSizes,
    NESTED_DB_REFERENCES,
    _check_database_assets,
    _check_encounters,
    _check_map_assets,
    _check_map_dimensions,
    _check_start_position,
    _check_system,
    _missing,
    _parent_cycles,
    _Tables,
    check_assets,
    check_commands,
    check_database,
    check_event_pages,
    check_map_tree,
    check_maps,
    check_tilesets,
    validate,
)
