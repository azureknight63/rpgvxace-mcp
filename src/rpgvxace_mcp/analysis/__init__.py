"""`analysis`: the walks, the reference rules, the reachability graph, and the linter.

One module until it reached two thousand lines over three separable
subjects, at which point the dependency boundary and the criticality
boundary turned out to coincide: everything the four map tool modules import
lives in `walk`, everything the two delete paths import lives in
`references`, and `misc_tools` is the only importer of anything in `lint`.
That last claim is a test rather than a sentence --
`test_codebase_shape.py` reads every tool module's imports and requires
that only `misc_tools` takes a name from `lint` -- because it is the reason the boundary is where it is,
and a second importer would make it false while leaving it written down.

`reachability` is a fourth subject at the same bottom layer as `references`:
it walks (from `walk`) and nothing else in this package walks it, because it
answers a design-quality question -- which map can nobody reach -- rather
than `references`'s correctness question of what still points at a thing
about to be deleted. `map_critique_tools` is its one caller, the way
`misc_tools` is `lint`'s.

`map_shape` is a fifth, also importing only `walk`: per-map ground emptiness
and occupied bounding box, read from the tile table with no command walk.
`map_critique_tools` combines it with `reachability`; neither imports the
other.

`switch_usage` is another at that layer for the same reason: it walks (from
`walk`) and answers a design-quality question -- which game switch or
variable is read and never set -- that `references` does not ask. Its one
caller is `map_critique_tools`, as `reachability`'s is.

`tile_variety` (Shannon entropy of each tile layer's ids, with Ace's region
and shadow layer counted apart) and `stat_outliers`
(IQR fences over a database table) sit there too, importing only `walk`.
`dead_ends` is the one module a step up: it reads `reachability`'s edge
builder and start map rather than parsing transfers a second time. All
three are `map_critique_tools`'s alone.

`summary` sits above `stat_outliers` and nothing else: it walks nothing,
reads the replies the other scopes already built, and fences a project's
maps with `stat_outliers.tukey_fences` so a map and a database field are
judged by one rule. `map_critique_tools` is its one caller too.

`lint` is itself a package of seven modules now, for the same reason at the
next scale down; its own `__init__` gives that order.

Re-exported from here so `from ..analysis import X` keeps working: the split
is about how this code is read and changed, not about making every caller
learn which of nine modules a name lives in. `tests/test_codebase_shape.py`
holds both layerings -- a tool module is a leaf, `walk` sits under every
other module here, and every module in either package is named in the
table that constrains it.
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
from . import (  # noqa: F401
    lint,
    references,
    walk,
)
from .references import (  # noqa: F401
    DEFAULT_REFERENCE_LIMIT,
    MAP_TARGET,
    REFERENCE_PREVIEW,
    REFERENCE_RULES,
    ReferencePreview,
    TABLE_NOUNS,
    XP_REFERENCE_RULES,
    _as_preview,
    _referenced_ids,
    _requires_met,
    find_references,
    find_references_for,
    reference_preview,
    reference_previews,
    rule_index,
    scan_note,
    unreadable_sources,
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
    PAGE_RELEASES_CONTROL,
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
    command_assets,
    map_assets,
    page_graphic,
    validate,
)


# The critique analyses are one tool's alone (`map_critique`), and loading
# them is most of what the rest of this package costs, so they load on the
# first name taken from them rather than when the server starts. `SEVERITIES`
# is not here: `lint`'s binding is the one this package has always exported.
# `tests/test_startup.py` holds that importing the server loads none of them.
# The table is inside the function so that the package namespace gains no
# name `test_every_re_exported_name_is_really_there` would have to excuse.
def __getattr__(name: str):
    import importlib

    lazy = {
        "dead_ends": ("map_dead_ends",),
        "map_shape": ("map_shapes",),
        "ref_sites": ("ACE_EXTRA_COMMAND_RULES", "FIELD_RULES", "FieldRule",
                      "Hit", "MAX_MENTIONS_LISTED", "MAX_MENTION_TEXT",
                      "Mention", "NOT_SCANNED_EVERYWHERE",
                      "PAGE_CONDITIONS", "REFERENCE_KINDS",
                      "RENUMBER_COMPLETE", "RENUMBER_UNCOVERED",
                      "RESERVED_IDS", "SWITCH_KINDS", "SiteScan",
                      "TROOP_PAGE_CONDITIONS", "command_rule_tables",
                      "coverage_for", "declaration_matches_scanner",
                      "renumber_support", "rewrite_hits", "scan_sites",
                      "scanned_labels", "site_rule_index", "unique_hits"),
        "reachability": ("map_reachability",),
        "stat_outliers": ("stat_outlier_report", "tukey_fences"),
        "summary": ("SUMMARY_NOTE", "critique_summary"),
        "switch_usage": ("map_switch_usage",),
        "tile_variety": ("MAX_TILE_VARIETY_CELLS", "TILE_VARIETY_METHOD",
                         "layer_variety", "map_tile_variety",
                         "meta_layer_counts", "shannon_entropy"),
    }
    if name in lazy:
        return importlib.import_module("." + name, __name__)
    for module, names in lazy.items():
        if name in names:
            return getattr(importlib.import_module("." + module, __name__), name)
    raise AttributeError("module %r has no attribute %r" % (__name__, name))
