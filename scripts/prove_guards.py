"""Prove that each hardening guard in the codec still checks something.

A passing test suite is not evidence that a guard works: a guard that has
stopped matching reads exactly like a guard that passes. So for every guard
added to the Marshal codec, this script

  1. copies the source tree to a temporary directory,
  2. reverts that one guard to the code that preceded it,
  3. runs the test that is supposed to catch the regression,
  4. and requires that test to FAIL.

Three things can go wrong, and they read alike from the outside:

* VACUOUS -- the test still passes with the guard reverted, so it is checking
  something else.
* STALE -- the guard's anchor is no longer in the file, so nothing was
  reverted. This happens whenever the code a guard watches is edited, which is
  normal; leaving the guard behind is not.
* AMBIGUOUS -- the anchor matches more than one place, so the revert hits
  whichever comes first, which may not be the one the test walks. A guard like
  this can report PROVEN for the wrong reason.

Proving them all takes minutes, which is long enough to be tempting to skip
after an edit. ``--check`` validates every anchor against its file in well
under a second and catches STALE and AMBIGUOUS immediately, so run that after
touching anything a guard watches.

    python scripts/prove_guards.py            # prove them all
    python scripts/prove_guards.py --check    # anchors only, seconds
    python scripts/prove_guards.py --list     # just list what is proven
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CODEC = "src/rpgvxace_mcp/marshal_rb.py"
TYPES = "src/rpgvxace_mcp/rubytypes.py"
SCHEMA = "src/rpgvxace_mcp/schema.py"
DOCS = "src/rpgvxace_mcp/docs.py"
MAP_COMMON = "src/rpgvxace_mcp/tools/map_common.py"
MAP_READ = "src/rpgvxace_mcp/tools/map_read_tools.py"
MAP_EDIT = "src/rpgvxace_mcp/tools/map_edit_tools.py"
MAP_TILE = "src/rpgvxace_mcp/tools/map_tile_tools.py"
MISC_TOOLS = "src/rpgvxace_mcp/tools/misc_tools.py"
PROJECT = "src/rpgvxace_mcp/project.py"
SERVER = "src/rpgvxace_mcp/server.py"
ANALYSIS = "src/rpgvxace_mcp/analysis.py"
REGISTRY = "src/rpgvxace_mcp/tools/registry.py"
DB_TOOLS = "src/rpgvxace_mcp/tools/db_tools.py"
EVENT_TOOLS = "src/rpgvxace_mcp/tools/event_tools.py"
CONVERT = "src/rpgvxace_mcp/convert.py"
SAFETY = "src/rpgvxace_mcp/tools/pattern_safety.py"
SEARCH_TOOLS = "src/rpgvxace_mcp/tools/search_tools.py"
RAW_TOOLS = "src/rpgvxace_mcp/tools/raw_tools.py"
COMMANDS = "src/rpgvxace_mcp/commands.py"
RUBYTYPES = "src/rpgvxace_mcp/rubytypes.py"


class Guard:
    """One guard, the edit that removes it, and the test that must then fail."""

    def __init__(self, name: str, file: str, present: str, reverted: str,
                 tests: list[str], why: str):
        self.name = name
        self.file = file
        self.present = present
        self.reverted = reverted
        self.tests = tests
        self.why = why


T = "tests/test_marshal.py::TestHostileInput::"
L = "tests/test_marshal.py::TestLosslessEdgeCases::"
PROF = "tests/test_schema.py::TestEngineProfiles::"
OBS = "tests/test_schema.py::TestSchemaObservation::"
SAFE = "tests/test_schema.py::TestResponseTextSafety::"
UNMOD = "tests/test_schema.py::TestUnmodelledEngine::"
PROV = "tests/test_schema.py::TestSchemaReportProvenance::"
PATH = "tests/test_tools.py::TestPathContainment::"
PATHRO = "tests/test_tools.py::TestPathContainmentReadOnly::"
PRUNE = "tests/test_tools.py::TestBackupPruning::"
BOMB = "tests/test_tools.py::TestScriptInflateLimit::"
STALE = "tests/test_tools.py::TestStalenessDetection::"
LOG = "tests/test_tools.py::TestDeletionIsLogged::"
FORM = "tests/test_tools.py::TestScriptNameStringForm::"
HOSTILE = "tests/test_tools.py::TestHostileDataFolder::"
RESTORE = "tests/test_tools.py::TestRestoreValidatesBeforeActing::"
RTP = "tests/test_tools.py::TestRtpOverrideIsHonest::"
HARD = "tests/test_protocol.py::TestTransportHardening::"
VRTP = "tests/test_tools.py::TestValidateRespectsTheRtp::"
VTBL = "tests/test_tools.py::TestValidateTellsUnreadableFromEmpty::"
VPAGE = "tests/test_tools.py::TestValidatePageNumbersMatchTheTools::"
VMISS = "tests/test_tools.py::TestValidateDistinguishesMissingFromBroken::"
VHOST = "tests/test_tools.py::TestValidateSurvivesHostileData::"
VSCOPE = "tests/test_tools.py::TestValidateScoping::"
VSTART = "tests/test_tools.py::TestValidateChecksTheStartPosition::"
STAGE = "tests/test_tools.py::TestNothingIsStaged::"
DBID = "tests/test_tools.py::TestDatabaseIdsAreChecked::"
COERCE = "tests/test_tools.py::TestArgumentsAreNotCoerced::"
MAPW = "tests/test_tools.py::TestMapWritesStayConsistent::"
RAWD = "tests/test_tools.py::TestRawDataRefusesBreakage::"
SEARCHED = "tests/test_tools.py::TestSearchReportsWhatItSearched::"
READ = "tests/test_tools.py::TestSearchDoesNotClaimToHaveRead::"
CREATE = "tests/test_tools.py::TestMapCreateIsStaged::"
SCRIPTAT = "tests/test_tools.py::TestScriptHitsSayWhere::"
SHAPE = "tests/test_tools.py::TestArgumentShapeIsEnforced::"
ATFIELD = "tests/test_tools.py::TestAtPrefixedFieldsAreRefused::"
EVERY = "tests/test_tools.py::TestGuardsRunOnEveryPath::"
TALLY = "tests/test_tools.py::TestFindingCountsAreTrustworthy::"
REDOS = "tests/test_tools.py::TestSearchRefusesTheHangingPattern::"
DELSTALE = "tests/test_tools.py::TestDeletingRespectsStaleness::"
CANON = "tests/test_tools.py::TestMapIdsAreCanonical::"
FIELD = "tests/test_tools.py::TestAPatchCannotInventAField::"
PERFILE = "tests/test_tools.py::TestStagingIsPerFile::"
BOUND = "tests/test_tools.py::TestCollectionIsBounded::"
OPARG = "tests/test_tools.py::TestInapplicableArgumentsAreRefused::"
NESTED = "tests/test_tools.py::TestANestedPatchCannotInventAField::"
ADDPAGE = "tests/test_tools.py::TestAddPageRefusesInsteadOfCrashing::"
FAST = "tests/test_tools.py::TestSearchPatternsStayFast::"
CLIP = "tests/test_tools.py::TestSearchSaysWhatItDidNotRead::"
MAPEV = "tests/test_tools.py::TestAMapPatchCannotMoveAnEvent::"
LAYOUT = "tests/test_tools.py::TestCommandReferenceSaysWhoseLayoutsTheseAre::"
RAWSCR = "tests/test_tools.py::TestRawDataWillNotHandBackCompressedScripts::"
EMPTYC = "tests/test_tools.py::TestAnEmptyCommandListIsRefusedEverywhere::"
PAGES = "tests/test_tools.py::TestDamagedPagesAreRefusedNotCrashedOn::"
REFCNT = "tests/test_tools.py::TestReferenceCountsDoNotUnderstate::"
DBARG = "tests/test_tools.py::TestDbGetArgumentsAreHonoured::"
EMPTYT = "tests/test_tools.py::TestMapTilesNamesAnEmptyTable::"
GROW = "tests/test_tools.py::TestGrowingAMapIsBoundedToo::"
DROPPED = "tests/test_tools.py::TestDiscardedTileCountIsRight::"
LISTCAP = "tests/test_tools.py::TestOneCommandListHasACeiling::"
SNAPDRY = "tests/test_tools.py::TestSnapshotPreviewMatchesTheSnapshot::"
BUDGET = "tests/test_tools.py::TestReadingManyEntriesSharesOneBudget::"
AREAID = "tests/test_tools.py::TestHashTablesThatCarryTheirOwnId::"
GATES = "tests/test_tools.py::TestEveryOpSelectingToolHasATable::"
GATED = "tests/test_tools.py::TestOpArgumentsAreGatedEverywhere::"
COMMON = "tests/test_tools.py::TestCommonEventOpsAreGatedToo::"
SYMDEPTH = "tests/test_marshal.py::TestHostileInput::"
BUDGETONE = "tests/test_tools.py::TestOneBudgetForAWholeResponse::"
TILETALLY = "tests/test_tools.py::TestTileTallySaysWhatItLeftOut::"
MAPREFS = "tests/test_tools.py::TestMapReferenceCountsDoNotUnderstate::"
LOOKAROUND = "tests/test_tools.py::TestLookaroundIsRefusedByName::"
MEASURED = "tests/test_tools.py::TestSearchPatternsStayFast::"
CLOSED = "tests/test_tools.py::TestTheShapeGateFailsClosed::"
WHOLE = "tests/test_tools.py::TestAWholesaleObjectCannotInventFields::"
NOOP = "tests/test_tools.py::TestNoOpSlipsPastTheGate::"
READDROP = "tests/test_tools.py::TestReadToolsDropNoArgumentsEither::"
FILTERED = "tests/test_tools.py::TestAFilteredListSaysItWasFiltered::"
UNREAD = "tests/test_tools.py::TestUnreadableStructuresAreNotReportedAsEmpty::"
TILESNONE = "tests/test_tools.py::TestMapGetHonoursTilesWithoutATable::"
PAGEARG = "tests/test_tools.py::TestEventGetChecksThePageItWasGiven::"
DELPREV = "tests/test_tools.py::TestADeletePreviewMatchesTheDelete::"
NILFILL = "tests/test_tools.py::TestRawDataCanFillANilField::"
BOUNDED = "tests/test_tools.py::TestRefusalsAreBounded::"
INERT = "tests/test_tools.py::TestSearchRefusesAnInertRegexFlag::"
AMPLIFY = "tests/test_tools.py::TestSharedChildrenCannotAmplifyAWalk::"
CACHEBOUND = ("tests/test_tools.py::TestTheFileCacheIsBounded::")
FOREIGN = ("tests/test_tools.py::TestAForeignBackupDirectoryIsSkipped::")
ONCE = ("tests/test_tools.py::TestAMapEditResolvesEachArgumentOnce::")
CRAFTED = ("tests/test_tools.py::TestACraftedNameIsBoundedInAReplyToo::")
ASSETSTALE = ("tests/test_tools.py::TestAnAssetAddedMidSessionIsSeen::")
SPELLINGS = ("tests/test_tools.py::TestTwoSpellingsOfOneArgumentAgree::")
MAPSCOPE = ("tests/test_tools.py::TestTheMapScopeStopsCollecting::")
DOCSAGREE = ("tests/test_schema.py::TestTheReferenceAgreesWithTheTables::")
PREVIEW = ("tests/test_tools.py::TestEveryPreviewReadsAsAPreview::")
NARROW = ("tests/test_tools.py::TestNarrowingValidateActuallyNarrowsIt::")
TILEBUF = ("tests/test_tools.py::TestATileTallyUsesTheBuffer::")
SPENDS = ("tests/test_tools.py::TestEveryWalkSpendsItsAllowance::")
NOPAGES = ("tests/test_tools.py::TestAnEventWithNoPagesStillCostsARow::")
EMPTYPAGE = ("tests/test_tools.py::TestAnEmptyPageStillCostsSomething::")
BATCH = ("tests/test_tools.py::TestOneBatchGetsOneWalkAllowance::")
FAILCLOSED2 = ("tests/test_tools.py::TestTheMeasurementFailsClosedOnEveryAbnormalExit::")
LENGTHS = ("tests/test_tools.py::TestEveryDeclaredLengthLimitIsEnforced::")
DSLINT = ("tests/test_tools.py::TestTheDslRefusesACoercibleId::")
DSLCHOICE = ("tests/test_tools.py::TestAChoiceBranchIsNeverDropped::")
CALLABLE = ("tests/test_tools.py::TestEveryConversionBranchIsCallable::")
COUNTS = ("tests/test_tools.py::TestACountIsNamedForWhatItCounted::")
DRYRUN = ("tests/test_tools.py::TestADryRunDoesNotReadAsDone::")
SCOPECLAIM = ("tests/test_tools.py::TestAScopeIsClaimedOnlyOnceRead::")
SUMMARY = ("tests/test_tools.py::TestMapGetBoundsItsEventSummary::")
CHILD = ("tests/test_tools.py::TestTheMeasurementReachesItsChild::")
REMEMBERS = ("tests/test_tools.py::TestWhatTheMeasurementRemembers::")
SHAPECLOSED = ("tests/test_tools.py::TestTheShapeGateFailsClosedThroughout::")
NAMES = ("tests/test_tools.py::TestEveryNameFromJsonIsChecked::")
HIDDEN = ("tests/test_tools.py::TestAnInventedFieldCannotHideInAContainer::")
WRITEPATH = ("tests/test_tools.py::TestEveryWalkOnAWritePathIsBounded::")
CEILING = ("tests/test_tools.py::TestNoWalkCollectsPastTheCeiling::")
SUBJECTS = ("tests/test_tools.py::TestEveryNamedCharacterIsMeasured::")
SEARCHWORK = ("tests/test_tools.py::TestSearchBoundsItsTotalWork::")
FAILCLOSED = "tests/test_tools.py::TestTheMeasurementFailsClosed::"

GUARDS = [
    Guard(
        "link index lower bound", CODEC,
        "        if not 0 <= index < len(table):",
        "        if index >= len(table):",
        [T + "test_rejects_negative_object_link",
         T + "test_rejects_negative_symbol_link"],
        "a negative index silently resolves to the wrong object, which we then "
        "write back over the user's file",
    ),
    Guard(
        "link index bounds at all", CODEC,
        """        if not 0 <= index < len(table):
            raise MarshalError(
                "%s %d out of range (%d known) at byte %d"
                % (kind, index, len(table), self.pos)
            )
        return table[index]""",
        "        return table[index]",
        [T + "test_rejects_out_of_range_object_link"],
        "an out-of-range link raises IndexError, which is not a MarshalError "
        "and escapes the caller's error handling",
    ),
    Guard(
        "non-negative element counts", CODEC,
        """        value = self._long()
        if value < 0:
            raise MarshalError(
                "negative %s count %d at byte %d" % (what, value, self.pos)
            )
        return value""",
        "        return self._long()",
        [T + "test_rejects_negative_array_count",
         T + "test_rejects_negative_hash_count",
         T + "test_rejects_negative_ivar_count"],
        "range() of a negative count is empty, so a corrupt container decodes "
        "as silently empty",
    ),
    Guard(
        "recursion depth cap", CODEC,
        """        if depth > MAX_DEPTH:
            raise MarshalError(
                "value nested more than %d deep at byte %d; refusing to recurse "
                "further" % (MAX_DEPTH, self.pos)
            )
""",
        "",
        [T + "test_rejects_excessive_nesting"],
        "deep nesting raises RecursionError from an arbitrary point in the "
        "parse instead of a clean MarshalError",
    ),
    Guard(
        "bignum sign byte", CODEC,
        """        if sign not in (BIGNUM_POSITIVE, BIGNUM_NEGATIVE):
            raise MarshalError(
                "bignum sign byte %#04x at byte %d is neither '+' nor '-'"
                % (sign, self.pos - 1)
            )
""",
        "",
        [T + "test_rejects_bad_bignum_sign"],
        "a corrupt sign byte is read as positive and silently normalised to "
        "'+' on the way back out",
    ),
    Guard(
        "table extent validation", TYPES,
        """def _check_extents(xsize: int, ysize: int, zsize: int) -> None:
    \"\"\"Refuse extents no real table has, before allocating for them.\"\"\"""",
        """def _check_extents(xsize: int, ysize: int, zsize: int) -> None:
    \"\"\"Refuse extents no real table has, before allocating for them.\"\"\"
    return""",
        [T + "test_table_refuses_an_out_of_range_axis",
         T + "test_table_refuses_an_absurd_cell_count",
         T + "test_table_with_absurd_extents_degrades_to_opaque_bytes"],
        "a header of xsize=0xFFFFFFFF with ysize=0 passes a product-only check, "
        "and the map tools then iterate four billion times",
    ),
    Guard(
        "table declared dimension", TYPES,
        """        if not 1 <= dim <= 3:
            raise ValueError("Table declares %d dimensions, expected 1, 2 or 3" % dim)
""",
        "",
        [T + "test_table_with_impossible_dimension_degrades"],
        "RGSS subscripts a Table by its declared dimension, so a nonsense one "
        "makes the cells unreachable in the engine",
    ),
    Guard(
        "table payload exact length", TYPES,
        "        if len(payload) != expected:",
        "        if len(payload) < expected:",
        [T + "test_table_with_trailing_payload_bytes_degrades"],
        "trailing payload bytes are accepted on read and then dropped on "
        "write, breaking the byte-identical round trip",
    ),
    Guard(
        "ivar records on values that cannot hold them", CODEC,
        """        # An Array, Hash, Float or decoded Table has nowhere to keep them, so
        # wrap rather than drop: the writer re-emits the record around the
        # value, and the round trip stays byte-exact.
        return RubyIvars(inner, ivars)""",
        """        self.warnings.append("dropped instance variables")
        return inner""",
        [L + "test_ivar_wrapped_array_keeps_its_record",
         L + "test_ivar_wrapped_hash_keeps_its_record"],
        "the instance-variable record is dropped, so the file does not come "
        "back the way it went in",
    ),
    Guard(
        "ivar-wrapped symbol in a name position", CODEC,
        """            if ivars:
                self.warnings.append(
                    "symbol :%s carries an encoding record in a name position; "
                    "that record cannot be reproduced when writing this file "
                    "back" % symbol
                )
""",
        "",
        [L + "test_ivar_wrapped_symbol_round_trips"],
        "a non-ASCII class or ivar name loads but cannot be written back, and "
        "nothing says so -- the file quietly changes shape",
    ),
    Guard(
        "C-level data records", CODEC,
        """        wrapper = RubyData(str(self._read_symbol_ref(depth)), None)
        self._register(wrapper)
        wrapper.value = self._read_value(depth + 1)
        return wrapper""",
        """        raise MarshalError("cannot read a 'data' record")""",
        [L + "test_c_level_data_record_round_trips"],
        "a file using type 'd' is refused entirely rather than carried through",
    ),
    Guard(
        "interned bytes are not link-registered", CODEC,
        "    return isinstance(obj, bytes) and len(obj) <= 1",
        "    return False",
        [L + "test_separate_empty_strings_are_not_aliased"],
        "two separate empty strings are written as a link, and Ruby loads them "
        "as one shared mutable String",
    ),
    Guard(
        "integral floats match Ruby's form", CODEC,
        """    if text.endswith(".0"):
        return text[:-2].encode("ascii")
""",
        "",
        [L + "test_integral_float_is_written_the_way_ruby_writes_it"],
        "Python writes 1.0 where Ruby writes 1, so a written float does not "
        "match what the engine itself would produce",
    ),
    Guard(
        "minor version is reproduced", CODEC,
        "        self.out.extend(bytes([MAJOR_VERSION, self.minor]))",
        "        self.out.extend(bytes([MAJOR_VERSION, MINOR_VERSION]))",
        [L + "test_older_minor_version_can_be_reproduced"],
        "a file written with an older minor version comes back with a changed "
        "version byte",
    ),
    Guard(
        "unknown class-reference kind", CODEC,
        """        tag = CLASS_REF_TAGS.get(obj.kind)
        if tag is None:
            raise MarshalError(
                "unknown class reference kind %r; expected one of %s"
                % (obj.kind, ", ".join(sorted(CLASS_REF_TAGS)))
            )""",
        "        tag = CLASS_REF_TAGS.get(obj.kind, T_MODULE_OLD)",
        [L + "test_unknown_class_ref_kind_is_refused"],
        "a mistyped kind is silently written as an old-style module reference",
    ),
    Guard(
        "non-UTF-8 names survive", CODEC,
        '        return self._string_bytes().decode("utf-8", "surrogateescape")',
        '        return self._string_bytes().decode("utf-8")',
        [L + "test_non_utf8_class_name_round_trips"],
        "a non-UTF-8 class path raises UnicodeDecodeError out of the parse and "
        "cannot round trip",
    ),
    Guard(
        "out-of-range Fixnum is reported", CODEC,
        """        if not FIXNUM_MIN <= value <= FIXNUM_MAX:
            # Ruby only writes a Fixnum inside that range, so this came from
            # somewhere else and we would re-dump it as a Bignum. Say so rather
            # than quietly changing the file's shape.
            self.warnings.append(
                "Fixnum %d at byte %d is outside the range Ruby writes as a "
                "Fixnum; it will be written back as a Bignum" % (value, self.pos)
            )
""",
        "",
        [L + "test_out_of_range_fixnum_is_reported"],
        "the value comes back out as a different Marshal type with nothing "
        "said about it",
    ),

    # -- C2: engine profiles, schema observation, response safety -------------
    Guard(
        "schema walk identity memo", SCHEMA,
        """    elif isinstance(value, list):
        if id(value) in seen:
            return
        seen.add(id(value))
        for item in value:""",
        """    elif isinstance(value, list):
        for item in value:""",
        [OBS + "test_link_shared_graph_does_not_explode",
         OBS + "test_shared_object_is_tallied_once"],
        "Marshal links make the loaded value a graph, so a shared subtree is "
        "walked once per path -- exponential work from a few hundred bytes",
    ),
    Guard(
        "schema walk covers wrapper types", SCHEMA,
        """    elif isinstance(value, RubyStruct):
        _tally(fields, value.cls, value.members)
        for item in value.members.values():
            _observe(item, fields, depth + 1, seen, budget)
    elif isinstance(value, (RubyUserMarshal, RubyData)):
        # These name a class but keep their state in one nested value.
        fields.setdefault(value.cls, {})
        _observe(value.value, fields, depth + 1, seen, budget)
    elif isinstance(value, (RubyUClass, RubyExtended)):
        _observe(value.inner, fields, depth + 1, seen, budget)
    elif isinstance(value, RubyIvars):
        _observe(value.inner, fields, depth + 1, seen, budget)
        for item in value.ivars.values():
            _observe(item, fields, depth + 1, seen, budget)
    elif isinstance(value, (RubyUserDef, RubySymbol)) and value.ivars:
        for item in value.ivars.values():
            _observe(item, fields, depth + 1, seen, budget)
""",
        "",
        [OBS + "test_wrapper_types_are_walked",
         OBS + "test_struct_members_are_tallied_under_the_struct"],
        "everything beneath a wrapper is missing from a report the tools "
        "present as observed authority",
    ),
    Guard(
        "class variables stay distinct", SCHEMA,
        "        bare = _bare_name(name)",
        '        bare = name.lstrip("@")',
        [OBS + "test_class_variables_do_not_collapse_onto_instance_variables"],
        "a script-added @@x collapses onto @x, so a reported field would be "
        "patched onto a different instance variable",
    ),
    Guard(
        "map id must be a real integer", SCHEMA,
        """    if isinstance(map_id, bool) or not isinstance(map_id, int):
        raise ValueError("map id must be a whole number, got %r" % (map_id,))
    if not 1 <= map_id <= MAX_MAP_ID:
        raise ValueError(
            "map id %d is outside 1..%d" % (map_id, MAX_MAP_ID)
        )""",
        """    if map_id < 1:
        raise ValueError("map ids start at 1, got %d" % map_id)""",
        [PROF + "test_map_filename_requires_a_real_id"],
        "a float map id truncates silently and addresses a different map",
    ),
    Guard(
        "audio kind is not substituted", SCHEMA,
        """        cls = self.audio_classes.get(kind)
        if cls is None:
            raise ValueError(
                "unknown audio kind %r; this engine has %s"
                % (kind, ", ".join(sorted(self.audio_classes)))
            )""",
        '        cls = self.audio_classes.get(kind, self.audio_classes["bgm"])',
        [PROF + "test_unknown_audio_kind_is_refused"],
        "a typo writes an RPG::BGM into an ME or SE slot on VX Ace",
    ),
    Guard(
        "response text has no lone surrogates", TYPES,
        """    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return text.encode("utf-8", "replace").decode("utf-8")
    return text""",
        "    return text",
        [SAFE + "test_class_names_from_a_file_survive_json_encoding",
         SAFE + "test_printable_text_replaces_lone_surrogates"],
        "one corrupt byte in one class name reaches json.dumps, then fails to "
        "encode on the transport and ends the session",
    ),
    Guard(
        "unmodelled engine cannot create a map", MAP_EDIT,
        "    if ctx.project.engine != ctx.project.profile.key:",
        "    if False:",
        [UNMOD + "test_creating_a_map_is_refused"],
        "a map is written with the wrong engine's field set, producing a file "
        "that engine's editor would reject",
    ),
    Guard(
        "documents name the model they used", DOCS,
        """    if project.engine == project.profile.key:
        return []""",
        """    if True:
        return []""",
        [UNMOD + "test_documents_say_which_model_is_being_used"],
        "a document titles itself one engine while describing another's file "
        "layout, with nothing to say so",
    ),
    Guard(
        "command reference follows the modelled engine", DOCS,
        "    engine = project.profile.key",
        "    engine = project.engine",
        [UNMOD + "test_command_reference_uses_the_modelled_engine_table"],
        "an unmodelled engine falls through to VX Ace's command table under a "
        "heading that says VX",
    ),
    Guard(
        "advisory notes travel with the data", MISC_TOOLS,
        '        "tile_id_ranges_note": TILE_ID_RANGES_NOTE,',
        "",
        [PROV + "test_advisory_notes_travel_with_the_data"],
        "runtime-derived tile constants are shipped to the client as bare "
        "facts, with their caveat left behind in a source comment",
    ),
    Guard(
        "schema report names this engine", MISC_TOOLS,
        '                "from the RGSS reference for %s where one exists, which covers "',
        '                "from the RGSS reference for a VX-family engine, which covers "',
        [PROV + "test_the_report_names_this_project_s_engine"],
        "an Ace project is told its field descriptions come from the VX "
        "reference",
    ),
    Guard(
        "backup name is one path component", PROJECT,
        """        safe_component(stamp, "backup name")
        root = self.backup_root()
        directory = root / stamp
        _contained(directory, root, "backup %r" % stamp)
        return directory""",
        """        return self.backup_root() / stamp""",
        [PATH + "test_restore_refuses_an_absolute_backup_name",
         PATH + "test_restore_refuses_parent_traversal"],
        "an absolute backup name replaces the join outright, so a restore reads "
        "any directory on the host and copies its files into Data/ where the "
        "read tools hand them to the model",
    ),
    Guard(
        "containment is checked before the read-only gate", PROJECT,
        """        source_dir = self._backup_dir(stamp)
        if not dry_run:
            self._assert_writable("restoring %s" % stamp)""",
        """        if not dry_run:
            self._assert_writable("restoring %s" % stamp)
        source_dir = self._backup_dir(stamp)""",
        [PATHRO + "test_a_bad_name_is_refused_as_a_bad_name"],
        "the containment check sits behind the read-only gate, so it stops "
        "being exercised in the mode a careful operator chooses, and the "
        "caller is told the wrong reason for the refusal",
    ),
    Guard(
        "data file names are components too", PROJECT,
        """        safe_component(filename, "data file name")
        path = self.data_dir / filename
        _contained(path, self.data_dir, "data file %r" % filename)
        return path""",
        """        return self.data_dir / filename""",
        [PATH + "test_path_for_refuses_a_name_that_is_not_one_component"],
        "every read and write in the server joins a caller's name to Data/ "
        "without checking it stays there",
    ),
    Guard(
        "read-only blocks snapshots and pruning", PROJECT,
        '        self._assert_writable("pruning backups")',
        "",
        [PATHRO + "test_snapshot_and_prune_are_refused"],
        "a server started read-only still creates and deletes directories on "
        "the operator's disk",
    ),
    Guard(
        "pruning spares the snapshot being restored", PROJECT,
        '        pre = self.snapshot("pre-restore", keep={stamp})',
        '        pre = self.snapshot("pre-restore")',
        [PRUNE + "test_restore_survives_a_full_backup_root"],
        "the safety snapshot taken during a restore prunes the very backup the "
        "restore is about to read from",
    ),
    Guard(
        "script inflate is bounded", PROJECT,
        """    stream = zlib.decompressobj()
    out = stream.decompress(data, max(budget, 1))
    if stream.unconsumed_tail:
        raise ProjectError(
            "this file's script bodies inflate to more than %d bytes in total, "
            "which is not something anyone wrote" % MAX_SCRIPT_BYTES
        )
    if not stream.eof:
        raise ProjectError("the compressed script body is truncated")
    return out""",
        "    return zlib.decompress(data)",
        [BOMB + "test_a_compression_bomb_is_reported_not_inflated"],
        "zlib expands about a thousand to one, so a crafted Scripts file names "
        "any amount of memory it likes",
    ),
    Guard(
        "staleness compares content, not just timestamps", PROJECT,
        """            if current is not None:
                if current == cached.digest:""",
        """            if current is not None:
                if True:""",
        [STALE + "test_a_same_size_rewrite_is_still_detected"],
        "a coarse filesystem timestamp lets an editor save be silently "
        "overwritten when the byte count happens to match",
    ),
    Guard(
        "deleting a file is logged", PROJECT,
        '        self._log_change(tool, filename, "deleted")',
        "",
        [LOG + "test_deleting_a_map_reaches_the_change_log"],
        "the most destructive operation in the server is the one that leaves no "
        "trace in the change log",
    ),
    Guard(
        "written script names carry the engine string form", PROJECT,
        "            row[1] = self.codec.make_string(name)",
        "            row[1] = RubyString(self.codec.encode(name))",
        [FORM + "test_a_written_script_name_keeps_this_engine_string_form"],
        "an Ace script name is written without its Ruby 1.9 encoding record, "
        "which is not a string the editor would produce",
    ),
    Guard(
        "map ids are ones a filename can express", PROJECT,
        '            if digits.isascii() and digits.isdigit() \\',
        '            if True \\',
        [HOSTILE + "test_an_unaddressable_map_file_does_not_stop_the_server"],
        "a Map0 or Map10000 in the Data folder reaches map_filename, which "
        "raises for an id it cannot express, and the server will not start",
    ),
    Guard(
        "a truncated script body is reported", PROJECT,
        """    if not stream.eof:
        raise ProjectError("the compressed script body is truncated")""",
        "",
        [HOSTILE + "test_a_truncated_script_body_is_an_error_not_a_short_script"],
        "a truncated stream returns the part that inflated, so reading and "
        "writing a script back silently drops its tail",
    ),
    Guard(
        "restore validates every name before it copies", PROJECT,
        '            safe_component(name, "file name")',
        '            pass  # check removed',
        [RESTORE + "test_a_bad_name_in_the_snapshot_stops_before_anything_is_copied"],
        "a name is rejected inside the copy loop, after the safety snapshot "
        "and after some files have already been written",
    ),
    Guard(
        "an explicit --rtp does not claim completeness", PROJECT,
        "        if self.rtp_override:",
        "        if False:",
        [RTP + "test_an_explicit_rtp_does_not_claim_every_rtp_was_located"],
        "the report tells the model every declared RTP was located when only "
        "one folder was given for all of them",
    ),
    Guard(
        "the handshake gates every other method", SERVER,
        """        if not self.initialized and method not in PRE_INITIALIZE_METHODS:""",
        """        if False:""",
        [HARD + "test_tools_are_refused_before_initialize"],
        "a client can skip protocol version negotiation and call tools "
        "against a version neither side agreed on",
    ),
    Guard(
        "params must be an object", SERVER,
        """        if not isinstance(params, dict):""",
        """        if False:""",
        [HARD + "test_positional_params_are_invalid_params_not_internal_error"],
        "JSON-RPC positional params reach params.get() and a malformed request "
        "is reported as an internal server error",
    ),
    Guard(
        "a forwarded response is not answered", SERVER,
        """        if "result" in message or "error" in message:""",
        """        if False:""",
        [HARD + "test_a_forwarded_response_is_not_answered"],
        "our error reply carries the client's own request id back to it and "
        "matches against something it is still waiting for",
    ),
    Guard(
        "the message cap applies before the line is held", SERVER,
        """    chunk = stdin.readline(MAX_MESSAGE_CHARS + 1)""",
        """    chunk = stdin.readline()""",
        [HARD + "test_an_oversized_line_is_discarded_without_being_parsed"],
        "a client that never sends a newline exhausts memory before the cap "
        "is ever consulted",
    ),
    Guard(
        "batches are bounded", SERVER,
        """                if len(message) > MAX_BATCH_MESSAGES:""",
        """                if False:""",
        [HARD + "test_an_oversized_batch_is_refused"],
        "one accepted message asks for a million tool listings in a single "
        "response frame",
    ),
    Guard(
        "an unanswerable batch is still answered", SERVER,
        """                if not message:""",
        """                if False:""",
        [HARD + "test_an_empty_batch_is_answered"],
        "an empty or wholly malformed batch produces no output and the client "
        "waits for a reply that never comes",
    ),
    Guard(
        "echoed values are truncated", REGISTRY,
        'MAX_ECHO_CHARS = 120',
        'MAX_ECHO_CHARS = 32 * 1024 * 1024',
        [HARD + "test_a_rejected_method_is_not_echoed_in_full",
         HARD + "test_a_rejected_non_string_is_not_echoed_in_full"],
        "a rejected 32 MB method name is quoted back in full",
    ),
    Guard(
        "tileset sheets go through the asset checker", ANALYSIS,
        "        checker.check(sheet_name, \"tilesets\",",
        "        _ = sheet_name and checker and (\"tilesets\",) and (",
        [VRTP + "test_a_tileset_sheet_miss_is_downgraded_with_an_unresolved_rtp"],
        "one rule keeps its own asset comparison, so a project whose RTP cannot "
        "be located gets up to nine warnings per tileset for the default sheets "
        "that live in the RTP -- and they outrank the downgraded ones",
    ),
    Guard(
        "an unreadable table is not an empty table", ANALYSIS,
        "        self._load(name)\n"
        "        if name in self._failures:\n"
        "            return None\n"
        "        return {i for i, _ in self._rows[name]}",
        "        self._load(name)\n"
        "        return {i for i, _ in self._rows.get(name, [])}",
        [VTBL + "test_an_unreadable_table_is_one_finding_not_a_dangling_reference_each"],
        "a table caught half-written by the editor reads as empty, so every id "
        "that refers to it is reported as missing",
    ),
    Guard(
        "page numbers are the ones the tools take", ANALYSIS,
        "        % (last_unconditional, event_id, name, last_unconditional - 1),",
        "        % (last_unconditional + 1, event_id, name, last_unconditional),",
        [VPAGE + "test_a_page_finding_names_the_page_the_tools_take"],
        "the message counts pages from one while every tool argument counts from "
        "zero, so a model acting on \"page 2\" edits the third page",
    ),
    Guard(
        "missing means absent, not unparseable", ANALYSIS,
        '    if map_id not in on_disk:\n        return [_missing(',
        '    if map_id not in map_sizes:\n        return [_missing(',
        [VMISS + "test_a_transfer_to_an_unparseable_map_is_not_called_missing"],
        "a map that exists but will not parse is reported as not existing, which "
        "sends an agent recreating a file that is already there",
    ),
    Guard(
        "a tile table is checked for being one", ANALYSIS,
        "    if not isinstance(data, Table):",
        "    if False:",
        [VHOST + "test_a_tile_table_that_is_not_a_table"],
        "reading .xsize off whatever @data holds raises out of the whole validate "
        "run, so one odd ivar denies the tool for that project",
    ),
    Guard(
        "map ids in MapInfos are addressable ones", ANALYSIS,
        "        if _int(map_id) is None or not 1 <= map_id <= MAX_MAP_ID:",
        "        if _int(map_id) is None:",
        [VHOST + "test_a_map_id_no_filename_can_express"],
        "an id no Map%03d name can express is reported as a missing file instead "
        "of as the bad id it is",
    ),
    Guard(
        "a non-numeric parent_id stops the walk", ANALYSIS,
        "            current = _int(current)\n"
        "            if current is None or current == 0:",
        "            if current == 0:",
        [VHOST + "test_a_parent_id_that_is_not_a_number"],
        "a parent_id holding an Array is unhashable, and the set membership test "
        "raises TypeError out of the whole run",
    ),
    Guard(
        # The cap moved to `convert` with the decode itself, so it now covers
        # replies as well as findings -- four copies in the tools layer had
        # neither the cap nor the sanitising.
        "quoted project text is length-capped", CONVERT,
        'MAX_TEXT_CHARS = 200',
        'MAX_TEXT_CHARS = 100_000_000',
        [VHOST + "test_an_event_name_of_absurd_length",
         VHOST + "test_a_long_event_name_is_capped_in_the_message"],
        "one crafted event name is repeated in full into every finding about it, "
        "so a small file produces a response too large to return",
    ),
    Guard(
        "repeated map ids are collapsed", ANALYSIS,
        "        wanted = sorted({i for i in (_int(m) for m in map_ids) if i is not None})",
        "        wanted = [i for i in (_int(m) for m in map_ids) if i is not None]",
        [VSCOPE + "test_repeated_map_ids_are_counted_once"],
        "a caller repeating an id multiplies every per-map rule by however many "
        "times they repeated it, and the reported map count with it",
    ),
    Guard(
        "the findings limit cannot be negative", ANALYSIS,
        "    limit = max(1, _int(limit) or DEFAULT_LIMIT)",
        "    limit = _int(limit) or DEFAULT_LIMIT",
        [VSCOPE + "test_a_negative_limit_does_not_drop_findings"],
        "a negative limit slices the findings list from the wrong end and drops "
        "the ones the caller most needed to see",
    ),
    Guard(
        "the start position is bounds-checked", ANALYSIS,
        "            findings += _check_start_position(project, system, start_map)",
        "            pass  # rule removed",
        [VSTART + "test_a_start_position_off_the_start_map_is_reported"],
        "the game boots with the party standing off the edge of the start "
        "map and nothing says so, though every other transfer is checked",
    ),
    Guard(
        "a change that did not land is not kept", REGISTRY,
        '                    self.project.forget_file(filename)',
        '                    pass  # rollback removed',
        [STAGE + "test_a_dry_run_leaves_nothing_behind",
         STAGE + "test_a_failed_op_batch_leaves_nothing_behind",
         STAGE + "test_a_resize_dry_run_does_not_discard_cached_tiles"],
        "a dry run, a refused write and a half-applied op batch all stay in "
        "the cache, and the next unrelated write of that file puts them on "
        "disk -- an edit to someone's game that no tool call asked for",
    ),
    Guard(
        "database entry ids are bounded", DB_TOOLS,
        '    else:\n        _check_id(request, entry_id)',
        '    else:\n        pass  # check removed',
        [DBID + "test_a_negative_create_id_is_refused",
         DBID + "test_a_huge_create_id_is_refused",
         DBID + "test_slot_zero_is_refused"],
        "set_entry indexes the array directly, so a negative id overwrites "
        "the last entry and a huge one pads the table with hundreds of "
        "thousands of empty slots -- both reported as a successful create",
    ),
    Guard(
        "a created entry carries its own id", DB_TOOLS,
        '    if isinstance(entry, RubyObject) and _carries_id(request):',
        '    if False:',
        [DBID + "test_a_created_entry_carries_its_own_id"],
        "the one field every RGSS data class needs is the one field a blank "
        "create leaves out, and the engine reads it",
    ),
    Guard(
        "the map tree is only edited by map_edit", DB_TOOLS,
        '    if table == "map_infos":',
        "    if False:",
        [DBID + "test_map_infos_is_not_editable_here"],
        "the tree can be edited without touching the map files, so an entry "
        "goes and its Map### file stays, or the reverse",
    ),
    Guard(
        "an unscanned table says it was unscanned", DB_TOOLS,
        '    scanned = table in REFERENCE_RULES',
        '    scanned = True',
        [DBID + "test_deleting_an_unscanned_table_says_it_was_not_scanned"],
        "a reference count of zero reads as \"nothing points at this\" when "
        "in fact nothing looked: only some tables have rules to scan for",
    ),
    Guard(
        "integer arguments are not coerced", REGISTRY,
        "    if isinstance(value, bool):",
        "    if False:",
        [COERCE + "test_a_fractional_id_is_refused",
         COERCE + "test_a_boolean_id_is_refused"],
        "int(True) is 1 and int(2.9) is 2, so a wrong-typed id addresses a "
        "different entry than the caller named and the tool reports success "
        "against the one it coerced to",
    ),
    Guard(
        "a falsy choice is not the default", REGISTRY,
        "    value = args.get(key)\n"
        "    if value is None:\n"
        "        value = default",
        "    value = args.get(key) or default",
        [COERCE + "test_an_explicit_falsy_choice_is_not_replaced_by_the_default"],
        "an explicit empty choice takes the default instead of being "
        "reported, and the message then says the argument was required "
        "when it had been given",
    ),
    Guard(
        "a map patch cannot change its size", MAP_EDIT,
        '    refuse_fields(',
        '    _skip = (',
        [MAPW + "test_a_patch_cannot_change_the_map_size",
         MAPW + "test_width_is_refused_on_update"],
        "setting @width alone leaves the map claiming a size its tile table "
        "does not have, which is the shape the editor refuses to open",
    ),
    Guard(
        # Now `_MapEdit.require_tree_entry`, which is both the lookup and the
        # check -- the reason that value is the one thing the request object
        # cannot simply re-derive.
        "both halves of a map edit are checked first", MAP_EDIT,
        '        if self.name is None:\n'
        '            return\n'
        '        self.info = self.ctx.project.get_entry("map_infos", self.map_id)',
        '        if self.name is not None:\n'
        '            return\n'
        '        self.info = self.ctx.project.get_entry("map_infos", self.map_id)',
        [MAPW + "test_a_rename_of_a_map_with_no_tree_entry_writes_nothing"],
        "the map file is written before the rename half is validated, so a "
        "caller is told the call failed with half of it already on disk",
    ),
    Guard(
        "a discarded tile is reported", MAP_EDIT,
        '    dropped = _cells_outside(data, width, height)',
        '    dropped = 0',
        [MAPW + "test_a_shrink_reports_the_tiles_it_discards"],
        "shrinking a map silently throws away every tile outside the new "
        "bounds",
    ),
    Guard(
        "tile values fit their layer", MAP_TILE,
        '    _check_tile(ctx, tile_id, rect.z, "tile_id")',
        '    pass  # check removed',
        [MAPW + "test_a_tile_value_the_layer_cannot_hold_is_refused",
         MAPW + "test_a_region_id_beyond_the_layer_is_refused"],
        "Table.set masks to sixteen bits, so an out-of-range id is stored as "
        "some other tile entirely and reported back as the one asked for",
    ),
    Guard(
        "raw_data refuses a shape swap", RAW_TOOLS,
        "    _check_same_kind(existing, new_value, \"/\".join(path))",
        "    pass  # check removed",
        [RAWD + "test_a_structure_cannot_be_replaced_by_a_scalar"],
        "an Integer takes the place of an event hash or a tile table, and "
        "the file stops being one the editor or the game can open",
    ),
    Guard(
        "search reports the scopes it searched", SEARCH_TOOLS,
        '            "scopes_searched": self.searched,',
        '            "scopes_searched": list(SEARCH_SCOPES),',
        [SEARCHED + "test_a_command_code_search_does_not_claim_to_have_searched_text"],
        "a command_code search skips the scopes holding no commands, and "
        "reporting them as searched tells the model they came back empty",
    ),
    Guard(
        "an unknown scope is refused", REGISTRY,
        "    unknown = [s for s in asked if s not in allowed]",
        "    unknown = []",
        [SEARCHED + "test_an_unknown_scope_is_refused_not_ignored"],
        "a misspelled scope is dropped and everything runs instead, so the "
        "caller believes it narrowed the search and gets the whole project",
    ),
    Guard(
        "a scope is searched only once it loads", SEARCH_TOOLS,
        '    try:\n        rows = ctx.project.entries("map_infos")\n    except (ProjectError, ValueError) as exc:\n        found.unsearchable["map_infos"] = str(exc)\n        return',
        '    found.searching("maps")\n    try:\n        rows = ctx.project.entries("map_infos")\n    except (ProjectError, ValueError) as exc:\n        found.unsearchable["map_infos"] = str(exc)\n        return',
        [READ + "test_a_scope_whose_only_file_fails_is_not_called_searched"],
        "a file that would not load comes back as \"searched, nothing found\", "
        "which is the same lie as reporting a scope that was skipped",
    ),
    Guard(
        "creating a map is staged like every other write", MAP_EDIT,
        "    with ctx.staged(filename, infos_file) as write_landed:",
        "    if True:  # staging removed",
        [CREATE + "test_a_refused_create_leaves_no_tree_entry"],
        "a refused save leaves a tree entry pointing at a map file that was never "
        "written, for the next MapInfos write to put on disk",
    ),
    Guard(
        "a script hit says where it is", ANALYSIS,
        '        if self.kind == "script":',
        "        if False:",
        [SCRIPTAT + "test_a_script_hit_names_its_slot_and_line"],
        "every script hit's location renders as the bare word \"script\", with "
        "no slot and no line number",
    ),
    Guard(
        "a dry run does not commit", REGISTRY,
        "        if not dry_run:",
        "        if True:",
        [STAGE + "test_a_dry_run_leaves_nothing_behind",
         STAGE + "test_a_resize_dry_run_does_not_discard_cached_tiles"],
        "StagedWrite.save keeps the staged mutation even when nothing was "
        "written, so a preview is left in the cache for the next real write "
        "of that file to put on disk",
    ),
    Guard(
        "the declared schema is enforced", REGISTRY,
        '        check_args(self.schema, args)',
        '        pass  # gate removed',
        [SHAPE + "test_a_misspelled_dry_run_does_not_write",
         SHAPE + "test_a_declared_bound_is_enforced",
         SHAPE + "test_a_declared_maxitems_is_enforced"],
        'a misspelled argument -- "dryrun" for "dry_run" -- is dropped '
        'rather than applied, so a tool performs a real write while the '
        'caller believes it asked for a preview',
    ),
    Guard(
        "a refusal list sees the @ spelling", REGISTRY,
        '        if bare_name(key) in fields:',
        '        if key in fields:',
        [ATFIELD + "test_a_map_patch_cannot_reach_a_structural_field",
         ATFIELD + "test_a_page_patch_cannot_reach_the_command_list"],
        'a Ruby ivar can be written "@width" as well as "width", and these '
        'patches accept either, so every refusal list is bypassed by '
        'spelling the field the other way',
    ),
    Guard(
        "raw_data sees the @ spelling too", RAW_TOOLS,
        '    if path and _bare_name(path[0]) in STRUCTURAL_MAP_PATHS:',
        '    if path and path[0] in STRUCTURAL_MAP_PATHS:',
        [ATFIELD + "test_raw_data_cannot_reach_a_structural_field"],
        'the same bypass on the escape hatch: path "@width" reaches the '
        'field this refuses',
    ),
    Guard(
        "creating a map checks its patch", MAP_EDIT,
        """    # The same refusal the update path makes. Creating used to return before
    # that check ran, so a create could write a map whose @width disagreed
    # with its tile table -- the shape the editor will not open.
    _refuse_structural(patch)""",
        "    pass  # check removed",
        [EVERY + "test_creating_a_map_refuses_a_structural_patch"],
        "the create path returned before the update path's check ran, so a "
        'create could write a map whose declared size disagreed with its '
        'tile table',
    ),
    Guard(
        "an update cannot move an entry id", DB_TOOLS,
        '    entry = request.require(entry_id)\n    request.refuse_id_patch(patch, entry_id)',
        '    entry = request.require(entry_id)\n    pass  # id patch not refused on update',
        [EVERY + "test_updating_an_entry_cannot_move_its_id"],
        "the refusal existed only on the create path, though the tool's own "
        'docstring said a patch could not move an id',
    ),
    Guard(
        "findings are counted before they are dropped", ANALYSIS,
        "    counts = {level: sum(1 for f in findings if f.severity == level)",
        "    counts = {level: sum(1 for f in findings[:limit] if f.severity == level)",
        [TALLY + "test_counts_survive_truncation"],
        'truncating by arrival order before sorting let a flood of warnings '
        "from one check push out a later check's errors, and the counts "
        'then reported zero errors while errors existed',
    ),
    Guard(
        "a command_code search needs somewhere to look", SEARCH_TOOLS,
        '        if "events" not in scopes:',
        '        if False:',
        [REDOS + "test_a_command_code_search_needs_the_events_scope"],
        'the search runs against no scope at all and reports zero hits, '
        'which reads as "this project has no such command"',
    ),
    Guard(
        "deleting checks staleness like every write", PROJECT,
        '            self._assert_not_stale(filename, path, cached)',
        '            pass  # check removed',
        [DELSTALE + "test_a_map_file_that_changed_is_not_deleted"],
        'the most destructive operation here is the one exempt from "refuse '
        'to act on a file that changed underneath us"',
    ),
    Guard(
        "map ids come from canonical filenames", PROJECT,
        '                    and digits == "%03d" % int(digits):',
        '                    and True:',
        [CANON + "test_an_id_always_names_a_file_that_exists"],
        'Map1, Map01 and Map001 all parse as id 1, so one cached map is '
        'walked once per file and an id names a file that is not there',
    ),
    Guard(
        "a patch cannot invent a field", REGISTRY,
        '    unknown = sorted({bare_name(key) for key in patch} - known)',
        '    unknown = []',
        [FIELD + "test_a_misspelled_field_is_refused_and_the_real_one_listed",
         FIELD + "test_a_blank_create_is_judged_against_a_sibling",
         FIELD + "test_a_map_patch_is_judged_too"],
        "a plausible typo adds an instance variable and is reported as a "
        "successful change, and the editor then rejects the object for "
        "carrying a field it does not expect",
    ),
    Guard(
        "staging commits one file at a time", REGISTRY,
        '                if filename not in keeper.written:',
        '                if keeper.written and False:',
        [PERFILE + "test_a_refused_second_save_discards_only_its_own_file"],
        "the first save to land commits every staged file, so a two-file "
        "write whose second save is refused keeps that file's mutation in "
        "the cache while the caller is told nothing happened",
    ),
    Guard(
        "an op's own keys are checked", REGISTRY,
        '        if item.get("properties"):',
        '        if False:',
        [SHAPE + "test_a_declared_bound_inside_an_op_is_enforced"],
        "an op's declared bounds are not applied to it -- an event_id of a "
        "billion reaches the handler. An unknown key inside an op is caught "
        "twice over now, by this and by check_op_args; a key that is real and "
        "out of range is caught only here",
    ),
    Guard(
        "an empty command list is refused", EVENT_TOOLS,
        '    entries = opt_list(raw_op, "commands")\n    if not entries:',
        '    entries = opt_list(raw_op, "commands")\n    if False:',
        # Every op that takes `commands`, from both rounds that registered a
        # guard on this line. Two guards on one anchor is invisible to
        # `check_anchors`, which counts occurrences in the file rather than
        # guards pointing at them -- so the test table in
        # tests/test_prove_guards.py checks for it instead.
        [SHAPE + "test_an_empty_command_list_is_refused",
         EMPTYC + "test_set_page_refuses_it",
         EMPTYC + "test_add_page_refuses_it",
         EMPTYC + "test_the_page_still_holds_what_it_did"],
        'commands: [] empties the page it was meant to rewrite and reports "replaced the command list" -- a deletion wearing the name of an edit',
    ),
    Guard(
        "findings stop being collected at the ceiling", ANALYSIS,
        # Anchored with its own comment, because the bare line now appears in
        # every walk that appends inside a loop -- which is the point of the
        # round that added them, and made this anchor ambiguous.
        '''            if len(findings) >= MAX_FINDINGS:
                # The ceiling is a collection ceiling, so it is checked here
                # rather than at the end: one page of a million Jump to Label
                # commands with no matching Label is a million findings.
                break''',
        '''            if False:
                break''',
        [BOUND + "test_a_flood_of_findings_stops_at_the_ceiling"],
        "the ceiling bounds only what is returned, so a page of broken "
        "jumps builds a finding per command -- tens of thousands of them "
        "-- in order to report ten",
    ),
    Guard(
        'an inapplicable argument is refused', REGISTRY,
        '    inert = sorted(key for key in args if key not in allowed)',
        '    inert = []',
        # Every test that turns on this one line, from both rounds that
        # registered a guard against it: that the gate refuses a dropped
        # argument, and that every op-selecting tool is wired into the gate.
        # A guard is identified by the line it reverts, so two guards on one
        # line is one revert reported twice.
        [OPARG + "test_event_update_will_not_quietly_drop_commands",
         OPARG + "test_db_delete_will_not_quietly_drop_a_patch",
         OPARG + "test_map_delete_will_not_quietly_drop_anything",
         OPARG + "test_map_update_will_not_quietly_drop_a_reparent",
         GATES + "test_every_op_selecting_tool_is_in_the_table_list",
         GATED + "test_map_tiles_refuses_the_other_ops_arguments"],
        'an argument the op does not read is dropped and the reply reports the same success as if it had been applied -- and the gate was declared for every op-selecting tool while four of them were not wired into it',
    ),
    Guard(
        'a nested patch cannot invent a field', CONVERT,
        '                _refuse_invented_fields(existing, new_value, field_path)',
        '                pass  # check removed',
        [NESTED + "test_a_nested_typo_is_refused",
         NESTED + "test_the_field_is_not_left_behind"],
        'a typo one level down adds an instance variable the editor will not open the project with, and reports it as the change asked for',
    ),
    Guard(
        'add_page refuses rather than crashing', EVENT_TOOLS,
        '    changes = request.patch_page(\n        page, raw_op, "the new page %d of event %d" % (len(pages), event_id))',
        '    changes = []  # patch not validated',
        [ADDPAGE + "test_a_misspelled_field_is_refused"],
        'add_page with a patch raises UnboundLocalError, which server.py does not catch, so a typo is reported as a broken server',
    ),
    Guard(
        'the backtracking budget is enforced', SAFETY,
        '    if worst <= MAX_BACKTRACK_PATHS:\n        return',
        '    if True:\n        return',
        [FAST + "test_every_costly_pattern_is_refused",
         FAST + "test_the_tool_refuses_them_too"],
        'one search with a pattern like "a?a?a?...b" wedges this single-threaded server until it is killed',
    ),
    Guard(
        'an unbounded repeat of a variable body is refused', SAFETY,
        '    if unbounded and inner_freedom > 1:',
        '    if False:',
        [FAST + "test_every_costly_pattern_is_refused"],
        '(a+)+ and (a|ab)* are charged a linear weight, which is not what they cost -- their time grows with the length of the text searched',
    ),
    Guard(
        'a clipped regex search says so', SEARCH_TOOLS,
        '        if not self.whole and len(text) > MAX_SUBJECT_CHARS:\n            self.clipped += 1',
        '        if False:\n            self.clipped += 1',
        [CLIP + "test_a_clipped_regex_search_says_so"],
        'a regex search reports "no hits" for text it never looked at, in the same shape as a search that read everything',
    ),
    Guard(
        'a map patch cannot reach into events', SCHEMA,
        'STRUCTURAL_MAP_FIELDS = ("width", "height", "data", "events")',
        'STRUCTURAL_MAP_FIELDS = ("width", "height", "data")',
        [MAPEV + "test_patching_events_is_refused",
         MAPEV + "test_the_event_did_not_move"],
        'a patch merges into the event objects and moves an event off the map, past the bounds check event_edit exists to apply',
    ),
    Guard(
        'borrowed command layouts are labelled', EVENT_TOOLS,
        '        entry["layouts_are_this_engines"] = modelled',
        '        entry["engine_is_modelled"] = modelled',
        [LAYOUT + "test_one_code_says_it_too"],
        "an unmodelled engine is handed VX Ace's parameter layouts as its own confirmed ones, and raw commands get written in the wrong order",
    ),
    Guard(
        'raw_data will not read the scripts table', RAW_TOOLS,
        '    if table == "scripts":',
        '    if False:',
        [RAWSCR + "test_reading_the_scripts_table_is_refused",
         RAWSCR + "test_writing_it_is_still_refused"],
        'every script body comes back as base64 of compressed Ruby, which no caller can use and which crowds out the rest of the response',
    ),
    Guard(
        'damaged pages are refused', EVENT_TOOLS,
        '        if not isinstance(pages, list):',
        '        if False:',
        [PAGES + "test_a_non_list_pages_is_refused"],
        'add_page reaches .append and delete_page reaches len() on whatever the file holds, so a damaged map crashes the call instead of being reported',
    ),
    Guard(
        'a reference count says when it is a floor', ANALYSIS,
        '''    return ReferencePreview(found[:REFERENCE_PREVIEW],
                            len(found) > REFERENCE_PREVIEW,
                            budget.note(what))''',
        '''    return ReferencePreview(found[:REFERENCE_PREVIEW],
                            False,
                            budget.note(what))''',
        # Both callers' tests. This was two guards on the same line written
        # twice in two files, which is why the line was worth merging: the
        # "ask for one past the limit" half had been added to db_edit and not
        # to map_edit, so the two guards were guarding code that had drifted.
        [REFCNT + "test_a_count_past_the_preview_says_at_least",
         MAPREFS + "test_a_count_past_the_preview_says_at_least"],
        'a thing referenced three hundred times is reported as referenced twenty-five times, so a caller that fixes those twenty-five believes it is free -- and this used to be two copies of the line, only one of which asked for the extra hit that makes the floor detectable',
    ),
    Guard(
        'ids are refused where they do nothing', DB_TOOLS,
        '        given = [name for name in ("ids", "id") if args.get(name) is not None]',
        '        given = []',
        [DBARG + "test_ids_on_a_single_object_table_are_refused"],
        'db_get on a single-object table returns the whole object as though the ids had been honoured',
    ),
    Guard(
        'a non-string field name is refused', DB_TOOLS,
        '    fields = list(SUMMARY_FIELDS.get(table, [])) + str_list(\n        args, "fields", MAX_EXTRA_FIELDS)',
        '    fields = list(SUMMARY_FIELDS.get(table, [])) + [\n        f for f in args.get("fields") or [] if isinstance(f, str)]',
        [DBARG + "test_a_non_string_field_name_is_refused"],
        'a malformed field name is dropped and the rows come back without it, with no error',
    ),
    Guard(
        'an empty tile table is named', MAP_TILE,
        '    if data.xsize < 1 or data.ysize < 1:',
        '    if False:',
        [EMPTYT + "test_an_empty_tile_table_says_so"],
        'a map with no cells is reported as a bad default for x, which is about an argument the caller never sent',
    ),
    Guard(
        'growing a map is bounded too', MAP_EDIT,
        '    _check_budget(width * height * data.zsize, "write",\n                  windowed=False, remedy=_RESIZE_REMEDY)',
        '    pass  # target size unchecked',
        [GROW + "test_a_grow_past_the_ceiling_is_refused"],
        'the whole-table ceiling applies to shrinking a map and not to growing one, so one call can build a multi-megabyte tile table',
    ),
    Guard(
        'one command list has a ceiling', EVENT_TOOLS,
        '    if len(commands) > MAX_COMMANDS_PER_LIST:',
        '    if False:',
        [LISTCAP + "test_a_list_past_the_ceiling_is_refused"],
        'one request writes a page carrying far more commands than the editor will open, with no cap and no warning',
    ),
    Guard(
        'appending is bounded as well', EVENT_TOOLS,
        '    _check_list_length(combined)\n    return combined',
        '    return combined',
        [LISTCAP + "test_appending_cannot_walk_past_it_a_little_at_a_time"],
        'the ceiling sees only the part being added, so a page can be walked past it a little at a time',
    ),
    Guard(
        'the snapshot preview asks the snapshot', MISC_TOOLS,
        '        return ctx.project.snapshot(label, dry_run=dry_run)',
        '        if dry_run:\n            return {"would_snapshot": sorted(\n                p.name for p in ctx.project.data_dir.glob(\n                    "*" + ctx.project.extension)),\n                "label": label, "dry_run": True}\n        return ctx.project.snapshot(label)',
        [SNAPDRY + "test_the_preview_leaves_out_what_the_snapshot_skips"],
        'the preview lists the symlinked data files the real snapshot skips, so it names files no backup would hold',
    ),
    Guard(
        'one conversion budget per response', DB_TOOLS,
        '                        "entry": to_json(entry, ctx.codec, mode, walk)})',
        '                        "entry": to_json(entry, ctx.codec, mode)})',
        [BUDGET + "test_the_budget_bounds_the_response_not_each_entry"],
        'each entry gets its own two-million-node allowance, so the budget bounds one entry and not the response',
    ),
    Guard(
        "a hash table's own id is set", DB_TOOLS,
        '    if request.kind == ARRAY:\n        return True',
        '    return request.kind == ARRAY\n    if False:',
        [AREAID + "test_a_created_area_carries_its_own_id",
         AREAID + "test_a_clone_gets_its_own_id_not_the_originals",
         AREAID + "test_a_patch_cannot_move_an_areas_id"],
        'a VX area is written with no @id, or with the @id of the entry it was cloned from, and a patch can move an id off its own slot',
    ),
    Guard(
        'map_tiles gates its op arguments', MAP_TILE,
        '    check_op_args(op, args, TILE_OP_ARGS, "map_tiles")',
        '    pass  # gate removed',
        [GATED + "test_map_tiles_refuses_the_other_ops_arguments"],
        'a fill carrying rows reports "filled ..." and never looks at them',
    ),
    Guard(
        'raw_data gates its op arguments', RAW_TOOLS,
        '    check_op_args(op, args, RAW_OP_ARGS, "raw_data")',
        '    pass  # gate removed',
        [GATED + "test_raw_data_refuses_a_value_it_would_not_write"],
        'a caller that meant to write and forgot op="set" is handed the value already there, under a "value" key that reads like confirmation',
    ),
    Guard(
        'scripts and backups gate theirs', MISC_TOOLS,
        '    check_op_args(op, args, SCRIPT_OP_ARGS, "scripts")',
        '    pass  # gate removed',
        [GATED + "test_scripts_refuses_code_on_a_read"],
        'scripts op="get" accepts a `code` and hands back the body already there, so a write is reported as a read of what it meant to replace',
    ),
    Guard(
        'the common event path is gated too', EVENT_TOOLS,
        '        check_op_args(op, raw_op, COMMON_EVENT_OP_ARGS, "ops[%d]" % index)',
        '        pass  # gate removed',
        [COMMON + "test_commands_are_not_dropped_on_an_update",
         COMMON + "test_an_event_id_means_nothing_here"],
        'a common event\'s "update" takes a commands list, drops it, and reports the rename as the whole change',
    ),
    Guard(
        'a symbol cannot recurse without bound', CODEC,
        '        if depth > MAX_DEPTH:\n            raise MarshalError(\n                "a symbol is nested more than %d records deep at byte %d; "',
        '        if False:\n            raise MarshalError(\n                "a symbol is nested more than %d records deep at byte %d; "',
        [SYMDEPTH + "test_rejects_a_deeply_nested_symbol"],
        '1.2KB of encoding records in a name position raises RecursionError, which is not a MarshalError and escapes to a startup traceback',
    ),
    Guard(
        'a string ivar cannot reset the budget', CONVERT,
        '                "__ivars__": {k: _to_json(v, codec, tables, walk)\n                              for k, v in value.ivars.items()}}',
        '                "__ivars__": {k: to_json(v, codec)\n                              for k, v in value.ivars.items()}}',
        [BUDGETONE + "test_a_string_ivar_is_charged_to_the_callers_budget",
         BUDGETONE + "test_a_string_ivar_cannot_reset_the_depth_limit"],
        'MAX_JSON_NODES and MAX_JSON_DEPTH both restart once per string that carries an ivar record, so a few kilobytes can ask for an unbounded walk',
    ),
    Guard(
        'the tile tally says what it left out', MAP_READ,
        '            "tile_ids_not_shown": max(0, len(counter) - TOP_TILE_IDS),',
        '            "tile_ids_not_shown": 0,',
        [TILETALLY + "test_it_reports_the_ids_it_did_not_list"],
        'a caller asking whether one tile is used reads a twelve-row list, does not find it, and concludes it is unused',
    ),
    Guard(
        'a lookaround is refused by name', SAFETY,
        '            raise PatternTooCostly(\n                "that pattern uses a lookahead, lookbehind or conditional "',
        '            atoms.append(_Atom(MAX_BACKTRACK_PATHS + 1, _ANYTHING))\n            _unused = (\n                "that pattern uses a lookahead, lookbehind or conditional "',
        [LOOKAROUND + "test_a_lookaround_names_itself"],
        "a lookaround is refused with the budget's message, which describes a repeated group or a chain -- neither of which the pattern has, so the caller rephrases forever instead of dropping the lookaround",
    ),
    Guard(
        'the measurement bounds the work', SAFETY,
        '    measure_cost(pattern)',
        '    pass  # measurement removed',
        [MEASURED + "test_every_costly_pattern_is_refused",
         MEASURED + "test_the_tool_refuses_them_too"],
        'ten patterns that the shape budget scores as cheap run for tens of seconds or never finish, because a score of 4096 covers patterns from instant to fatal and cannot tell them apart',
    ),
    Guard(
        'the shape gate fails closed', SAFETY,
        '    except _RE_ERROR:',
        '    except Exception:',
        [CLOSED + "test_an_unexpected_parser_failure_is_refused"],
        "anything the standard library's parser raises that this module did not anticipate silently disables the cheap gate, and the caller is never told it ran on an unchecked pattern",
    ),
    Guard(
        'a replacement cannot invent fields', CONVERT,
        '        refuse_invented_in_replacement(existing, converted, field_path)',
        '        pass  # replacement fields unchecked',
        [WHOLE + "test_a_typo_in_a_replacement_object_is_refused",
         WHOLE + "test_a_typo_inside_an_array_element_is_refused"],
        'a typo inside a value carrying __class__ is written into the file and reported as the change the caller asked for, by every tool that takes a patch -- and the editor then refuses to open the project',
    ),
    Guard(
        'raw_data checks a replacement too', RAW_TOOLS,
        '    refuse_invented_in_replacement(existing, new_value, "/".join(path))',
        '    pass  # replacement fields unchecked',
        [WHOLE + "test_raw_data_refuses_one_too"],
        "the escape hatch's own description says it refuses a field the object does not have, and it checked only the name in the path",
    ),
    Guard(
        'an op with no table is refused', REGISTRY,
        '        raise ToolError(\n            "op \\"%s\\" is accepted by %s but has no declared argument list, "',
        '        return\n        _unused = (\n            "op \\"%s\\" is accepted by %s but has no declared argument list, "',
        [NOOP + "test_an_op_with_no_table_entry_is_refused_not_ignored"],
        'an op the tool accepts but the table omits has its arguments dropped and reports success, which is the failure the gate exists to prevent, reached through the gate itself',
    ),
    Guard(
        'a common event read refuses page arguments', EVENT_TOOLS,
        '        refuse_inert(\n            args, ("event_ids", "name_contains", "page"),',
        '        _unused = (\n            args, ("event_ids", "name_contains", "page"),',
        [READDROP + "test_a_common_event_read_refuses_page_arguments"],
        'event_get common_event_id=N name_contains=X returns the event whether or not it matches, and the reply reads exactly like a positive match',
    ),
    Guard(
        'a code lookup refuses a search', EVENT_TOOLS,
        '        refuse_inert(args, ("search",),',
        '        _unused = (args, ("search",),',
        [READDROP + "test_a_code_lookup_refuses_a_search_it_would_not_run"],
        'command_reference code=101 search=X answers for 101 alone, and the caller cannot tell that from a search that matched nothing',
    ),
    Guard(
        'a filtered list says what it counted', MAP_READ,
        '    orphans = sorted(on_disk - {i for i, _ in',
        '    orphans = [] if parent_filter is not None else sorted(on_disk - {i for i, _ in',
        [FILTERED + "test_a_filter_does_not_claim_there_are_no_orphans"],
        'a filtered map_list reports "no orphan map files" from a check that never ran, which is the zero that reads as "nothing looked"',
    ),
    Guard(
        'unreadable events are not reported as empty', SCHEMA,
        'def unreadable_events_note(events: "Any") -> "str | None":',
        'def unreadable_events_note(events: "Any") -> "str | None":\n    return None',
        [UNREAD + "test_event_get_says_it_could_not_read_them",
         UNREAD + "test_map_get_says_so_too",
         UNREAD + "test_map_list_says_so_too"],
        'a map whose @events cannot be read comes back with an empty event list on all three read tools, so a model concludes there are none',
    ),
    Guard(
        'map_get answers a tiles request it cannot fill', MAP_READ,
        '    tiles = one_of(args, "tiles", list(TILE_DETAIL), TILE_DETAIL[0])',
        '    tiles = "none"',
        [TILESNONE + "test_asking_for_counts_says_there_is_no_table",
         TILESNONE + "test_an_invalid_tiles_value_is_still_refused"],
        'on a map whose tile data is not a Table the argument is dropped: no counts, no explanation, and an invalid value not even refused',
    ),
    Guard(
        'event_get checks the page it was given', EVENT_TOOLS,
        '    only_page = opt_int(args, "page", minimum=0)',
        '    only_page = opt_int(args, "page")',
        [PAGEARG + "test_a_negative_page_is_refused"],
        'a negative page is accepted and filters nothing out, while event_edit refuses the same value and raw_data gives it the opposite meaning',
    ),
    Guard(
        'the delete preview matches the delete', MAP_EDIT,
        '            "would_delete": filename if has_file else None,',
        '            "would_delete": filename,',
        [DELPREV + "test_the_preview_says_there_is_no_file_to_delete"],
        "the preview promises a file deletion the real call will not perform, which this module's own comments call worse than no preview",
    ),
    Guard(
        'raw_data can fill in a nil', RAW_TOOLS,
        '    if existing is None:',
        '    if False:',
        [NILFILL + "test_an_object_can_replace_a_nil"],
        'filling in a nil field is refused with "set a field inside it instead", and there is no field inside nil',
    ),
    Guard(
        'a refusal cannot grow with the file', RUBYTYPES,
        'def named(names: "Any", limit: int = MAX_NAMES_LISTED) -> str:',
        'def named(names: "Any", limit: int = 10 ** 9) -> str:',
        [BOUNDED + "test_an_unknown_field_refusal_is_capped"],
        'an entry carrying five thousand instance variables produces a fifty-nine-kilobyte error message, from names that came out of the file',
    ),
    Guard(
        'an inert regex flag is refused', SEARCH_TOOLS,
        '        if opt_bool(args, "regex"):',
        '        if False:',
        [INERT + "test_regex_alongside_a_command_code_is_refused"],
        'regex says how to read query, and a command_code search reads neither, so the flag is silently dropped',
    ),
    Guard(
        'shared children cannot amplify a walk', ANALYSIS,
        '                    if budget is not None and not budget.spend(len(commands)):\n                        return',
        '                    if False:\n                        return',
        [AMPLIFY + "test_event_get_stops_and_says_so",
         AMPLIFY + "test_search_stops_and_says_so",
         AMPLIFY + "test_validate_stops_and_says_so"],
        'a 7 KB map whose objects share children renders as five million commands: event_get takes 36 seconds, search 20 and validate 13, on a server the user cannot interrupt',
    ),
    Guard(
        'the measurement fails closed', SAFETY,
        '            verdict = (\n                "this server\'s safety check on that pattern exited abnormally "',
        '            verdict = None\n            _unused = (\n                "this server\'s safety check on that pattern exited abnormally "',
        [FAILCLOSED + "test_a_child_killed_by_a_signal_is_refused",
         FAILCLOSED + "test_a_child_that_exits_nonzero_silently_is_refused"],
        'a child that dies for any reason but a bad pattern is recorded as a measured pass and cached, so an unmeasured pattern then runs in-process',
    ),
    Guard(
        'a nullable atom does not break a run', SAFETY,
        '        if not atom.nullable and span and not seen.intersects(atom.charset):',
        '        if span and not seen.intersects(atom.charset):',
        [MEASURED + "test_every_costly_pattern_is_refused"],
        '"=?~?" repeated twenty-four times scores 2 -- every adjacent pair is literal-disjoint -- and runs over thirty seconds against a line of "=", which is what a stock VX Ace script separator is',
    ),
    Guard(
        'every named character reaches the measurement', SAFETY,
        '    every = "".join(char * run for char in ordered)',
        '    every = "".join(char * run for char in ordered[:0])',
        [SUBJECTS + "test_a_hidden_character_still_reaches_a_subject",
         SUBJECTS + "test_every_character_of_a_wide_alphabet_reaches_one"],
        'a pattern naming nine or more characters pushes its dangerous one out of every synthetic subject, so the child returns in milliseconds and the pass is cached',
    ),
    Guard(
        'one search cannot outrun its deadline', SEARCH_TOOLS,
        '        if self._clock() >= self._deadline:',
        '        if False:',
        [SEARCHWORK + "test_the_matcher_stops_when_its_time_is_spent",
         SEARCHWORK + "test_a_search_that_stopped_early_says_so"],
        'a pattern measured at 0.2s against one 2000-character subject is then applied to every string in the project -- a hundred thousand of them -- and nothing stops it, while `limit` cannot help because a pattern picked to be slow matches nothing',
    ),
    Guard(
        'a search that gave up says so', SEARCH_TOOLS,
        '''        spent = matcher.note() if matcher is not None else None
        if spent:''',
        '''        spent = None
        if spent:''',
        [SEARCHWORK + "test_a_search_that_stopped_early_says_so"],
        'a search that stopped on its deadline returns the same shape as one that read the whole project, so "no hits" reads as "there are none"',
    ),
    Guard(
        'a walk stops on the deadline, not only on the limit', SEARCH_TOOLS,
        '''        return self.full or (self.matcher is not None
                             and self.matcher.exhausted)''',
        '''        return self.full''',
        [SEARCHWORK + "test_a_search_that_gave_up_does_not_claim_the_later_scopes"],
        'the text test answers False for the rest of the project while every scope still loads and decodes all of it, so the deadline bounds the matching and nothing bounds the walk',
    ),
    Guard(
        'a delete asks what points at it under a budget', ANALYSIS,
        '    for location, commands in iter_command_lists(project, None, budget):',
        '    for location, commands in iter_command_lists(project):',
        [WRITEPATH + "test_deleting_an_entry_bounds_its_reference_scan",
         WRITEPATH + "test_deleting_a_map_bounds_its_reference_scan"],
        'find_references runs on both delete paths and returns early only when it finds hits, so "nothing points at it" is the answer that walks every command in the project -- five million visits from a 7 KB map, and the caller then deletes',
    ),
    Guard(
        'a delete whose scan stopped does not read as checked', DB_TOOLS,
        '             "references_checked": scanned and not incomplete,',
        '             "references_checked": scanned,',
        [WRITEPATH + "test_a_bounded_scan_does_not_claim_the_entry_is_free"],
        'zero references plus references_checked:true means "safe to delete", and a scan that stopped early produces that pair for neither reason',
    ),
    Guard(
        'one common event cannot render without a budget', EVENT_TOOLS,
        '        shown = commands[:command_budget.take(len(commands))]',
        '        shown = commands',
        [WRITEPATH + "test_reading_a_common_event_is_bounded",
         WRITEPATH + "test_a_bounded_common_event_still_reports_its_real_length"],
        'a common event whose @list is 250,000 links to one command is a few kilobytes on disk and renders a thirty-megabyte reply',
    ),
    Guard(
        'the asset collector refuses past its ceiling', ANALYSIS,
        '''    def check(self, name: Any, kind: str, location: Location,
              rule: str = "missing_graphic", context: str = "") -> None:
        if self.full:
            return''',
        '''    def check(self, name: Any, kind: str, location: Location,
              rule: str = "missing_graphic", context: str = "") -> None:
        if False:
            return''',
        [CEILING + "test_the_asset_checker_itself_stops_collecting",
         CEILING + "test_the_asset_walk_stops_collecting"],
        'four walks share one collector and none of them bounded it -- one crafted map produced 1600 findings against a ceiling of 4',
    ),
    Guard(
        'the event page walk stops at the ceiling', ANALYSIS,
        '''            # Two findings per event at most, and the event count comes out of
            # the file: a crafted map that shares one event object across
            # forty thousand slots produced forty thousand findings here.
            if len(findings) >= MAX_FINDINGS:
                return findings''',
        '''            if False:
                return findings''',
        [CEILING + "test_the_page_walk_stops_collecting"],
        'one event object shared across forty thousand slots produces a finding per slot',
    ),
    Guard(
        'every ivars dict has its names checked', CONVERT,
        '    return {check_ivar_name(name): from_json(item, codec)',
        '    return {name: from_json(item, codec)',
        [NAMES + "test_a_strings_ivars_are_checked",
         NAMES + "test_a_byte_strings_ivars_are_checked",
         NAMES + "test_an_ivar_wrappers_names_are_checked"],
        'the two string forms and the ivar wrapper wrote whatever field name they were given straight into the Marshal stream, which check_ivar_name exists to stop',
    ),
    Guard(
        'a struct names its class and its members legally', CONVERT,
        '        return RubyStruct(check_class_name(value["__struct__"]),',
        '        return RubyStruct(value["__struct__"],',
        [NAMES + "test_a_struct_name_is_checked"],
        'a struct name from caller JSON reached the file unchecked',
    ),
    Guard(
        'a wrapped class name is checked', CONVERT,
        '        return RubyUClass(check_class_name(value["__uclass__"]),',
        '        return RubyUClass(value["__uclass__"],',
        [NAMES + "test_a_uclass_name_is_checked"],
        'the class of a subclassed builtin reached the file unchecked',
    ),
    Guard(
        'every extended module name is checked', CONVERT,
        '            [check_class_name(name) for name in value["__extended__"]],',
        '            list(value["__extended__"]),',
        [NAMES + "test_an_extended_module_name_is_checked"],
        'the module list on an extended object reached the file unchecked',
    ),
    Guard(
        'an invented field cannot hide in a hash', CONVERT,
        '''    if isinstance(replacement, RubyHash) and isinstance(existing, RubyHash):''',
        '''    if False:''',
        [HIDDEN + "test_a_typo_inside_a_hash_value_is_refused",
         HIDDEN + "test_a_typo_inside_a_hash_inside_a_list_is_refused"],
        'the walk recursed into objects and lists and returned silently for a hash, so a typo inside one was never compared against the value it replaces -- the rule the docstring states did not apply to one of the three containers the format nests objects in',
    ),
    Guard(
        'an invented field cannot hide in a nested list', CONVERT,
        '''            if isinstance(item, (list, RubyHash)):''',
        '''            if False:''',
        [HIDDEN + "test_a_typo_inside_a_nested_list_is_refused"],
        'the list branch recursed only into elements that were themselves objects, so a list of lists went through unexamined',
    ),
    Guard(
        'the payload does not ride on a command line', SAFETY,
        """                              timeout=PREFLIGHT_SECONDS, input=payload,""",
        """                              timeout=PREFLIGHT_SECONDS,""",
        [CHILD + "test_the_subjects_travel_on_stdin",
         CHILD + "test_a_wide_kana_class_is_measured_not_refused"],
        'base64 of ten 2000-character subjects is 27000 characters for ASCII and 83000 for Japanese, against a 32767 Windows limit, so every Japanese pattern with a moderate character class was refused as unsafe on a question the check never got to ask',
    ),
    Guard(
        'a timing refusal is not remembered', SAFETY,
        """    if verdict is not None:
        return
    while len(_PASSED) >= MAX_MEASURED_CACHED:""",
        """    while len(_PASSED) >= MAX_MEASURED_CACHED:""",
        [REMEMBERS + "test_a_refusal_is_not_remembered"],
        'the only refusals this can produce are a timeout and a failure to spawn, both of which a loaded machine causes once and not again -- remembered, one such moment refuses a legitimate pattern for the life of the server',
    ),
    Guard(
        'the measured cache has a ceiling', SAFETY,
        """    while len(_PASSED) >= MAX_MEASURED_CACHED:
        del _PASSED[next(iter(_PASSED))]""",
        """    if False:
        del _PASSED[next(iter(_PASSED))]""",
        [REMEMBERS + "test_the_cache_has_a_ceiling"],
        'a caller can supply endlessly many distinct patterns, and every one that passed was kept for the life of the process',
    ),
    Guard(
        'the shape gate fails closed over its tree walk too', SAFETY,
        """        atoms = _flatten(parsed)
        worst, span = _worst_run(atoms)
    except PatternTooCostly:
        raise
    except Exception as exc:
        raise PatternTooCostly(_unanalysable(exc))""",
        """        atoms = _flatten(parsed)
        worst, span = _worst_run(atoms)
    except PatternTooCostly:
        raise""",
        [SHAPECLOSED + "test_a_failure_flattening_the_tree_is_a_refusal",
         SHAPECLOSED + "test_a_recursion_error_is_a_refusal"],
        'the guard covered the parse and stopped one line before the two recursive walks over a caller-shaped tree, so a RecursionError reached the caller as an internal error rather than as this gate declining to run',
    ),
    Guard(
        'a filtered count is not called a total', EVENT_TOOLS,
        """        "matching_events": len(out),""",
        """        "total": len(out),""",
        [COUNTS + "test_a_filtered_event_read_names_its_count",
         COUNTS + "test_the_count_follows_the_filter"],
        '"total" on a reply filtered by event_ids, name_contains or page reads as the number of events on the map, so a filtered read looks like the whole of a much emptier map -- the rename db_list and map_list already carry',
    ),
    Guard(
        'the events scope is claimed only once it reads one', SEARCH_TOOLS,
        """        if not read_something:
            read_something = True
            found.searching("events")""",
        """        if False:
            read_something = True
            found.searching("events")""",
        # Both directions. Reverting the guard stops the scope being claimed
        # at all, which satisfies the negative test on its own -- so the test
        # that a readable project *does* claim it is the one that decides.
        [SCOPECLAIM + "test_an_unreadable_project_does_not_claim_the_events_scope",
         SCOPECLAIM + "test_a_readable_project_still_claims_it"],
        'iter_maps and _safe_entries both swallow a file that will not load, so a project whose maps, common events and troops are all unreadable yielded nothing and search still reported the scope as read -- the exact thing `searching` exists to prevent',
    ),
    Guard(
        'a scope that read nothing says so', SEARCH_TOOLS,
        """    if not read_something:
        found.nothing_read("events", (""",
        """    if False:
        found.nothing_read("events", (""",
        [SCOPECLAIM + "test_it_says_that_it_read_nothing_rather_than_going_quiet"],
        'not claiming the scope is only half of it: the reply then just omits it, and a caller has to notice an absence rather than read a sentence',
    ),
    Guard(
        'the event summary has a ceiling', MAP_READ,
        """        if rows >= MAX_EVENT_SUMMARY_ROWS:
            return out, True""",
        """        if False:
            return out, True""",
        [SUMMARY + "test_the_summary_stops_at_its_ceiling",
         SUMMARY + "test_a_stopped_summary_says_so"],
        'events times pages, both out of the file: the shared-object map is fifty events of fifty pages all one object, so 7 KB produced 2500 rows and nothing said why',
    ),
    Guard(
        'a visit is charged for even when it carries nothing', ANALYSIS,
        '        self.remaining -= max(1, steps)',
        '        self.remaining -= steps',
        [EMPTYPAGE + "test_the_budget_charges_for_a_list_of_no_commands",
         EMPTYPAGE + "test_search_stops_and_says_so",
         EMPTYPAGE + "test_validate_events_stops_and_says_so"],
        'every charge in the project was spend(len(commands)), which is zero for a page holding an empty list, and spend only trips below zero -- so an 18.4 KB map describing four million (event, page) pairs was walked in full, search taking 5.7s and reporting no stop signal at all',
    ),
    Guard(
        'the walk charges per event and per page slot', ANALYSIS,
        '''            if budget is not None and not budget.spend(1):
                return
            if not isinstance(event, RubyObject):
                continue
            name = _text(project, event.get("name"))
            for page_index, page in enumerate(_seq(event.get("pages"))):
                if budget is not None and not budget.spend(1):
                    return''',
        '''            if not isinstance(event, RubyObject):
                continue
            name = _text(project, event.get("name"))
            for page_index, page in enumerate(_seq(event.get("pages"))):
                if False:
                    return''',
        # The budget's own state, not elapsed time. With `spend`'s one-step
        # floor in place a map of real pages is already bounded, so a
        # wall-clock test could not tell these extra charges apart from the
        # floor -- and what they close is a hash of values that are not
        # events, and a @pages list of values that are not pages, both of
        # which `continue` past before anything charges.
        [SPENDS + "test_an_events_hash_of_non_events_is_charged_for",
         SPENDS + "test_a_pages_list_of_non_pages_is_charged_for"],
        'the amplification unit is (events x pages), so charging only for command payload left the product free -- and a page slot holding something that is not an RPG::Event::Page was free as well',
    ),
    Guard(
        'the page rule walk takes an allowance', ANALYSIS,
        '''            if budget is not None and not budget.spend(1):
                return findings
            if not isinstance(event, RubyObject):
                continue
            pages = _seq(event.get("pages"))
            if budget is not None and not budget.spend(len(pages)):
                return findings''',
        '''            if not isinstance(event, RubyObject):
                continue
            pages = _seq(event.get("pages"))''',
        [SPENDS + "test_the_page_rule_walk_spends_the_allowance"],
        'check_event_pages collects only for an unconditional page, so one condition flag set on a crafted page made a finding ceiling powerless and it walked every page of every event for nothing',
    ),
    Guard(
        'the asset walk takes the allowance its sibling had', ANALYSIS,
        """            if budget is not None and not budget.spend(1):
                return
            if not isinstance(event, RubyObject):
                continue
            name = _text(project, event.get("name"))
            for page_index, page in enumerate(_seq(event.get("pages"))):
                if checker.full:
                    return
                if budget is not None and not budget.spend(1):
                    return""",
        """            if not isinstance(event, RubyObject):
                continue
            name = _text(project, event.get("name"))
            for page_index, page in enumerate(_seq(event.get("pages"))):
                if checker.full:
                    return""",
        [SPENDS + "test_the_asset_walk_spends_the_allowance"],
        '_AssetChecker.check returns before it counts anything when a graphic name is empty, so the collector ceiling never fills and the walk over events x pages had no bound at all',
    ),
    Guard(
        'the event summary charges for the event row too', MAP_READ,
        '''        # The event's own row. Charged here rather than only in the pages loop
        # below, which is what let an event with no pages be free.
        rows += 1''',
        '''        pass''',
        # An event with *no* pages, which is the case the counter missed:
        # the crafted map with 2000 real pages per event is bounded by the
        # page counter either way.
        [NOPAGES + "test_the_summary_stops_at_the_ceiling",
         NOPAGES + "test_and_says_that_it_stopped"],
        'the counter sat inside the pages loop while the event row was appended outside it, so a hash of a million links to an event with no pages built a million dicts -- and MAX_TOOL_RESULT_CHARS is measured after the reply is serialised, so it bounds the response and not the work',
    ),
    Guard(
        'one batch gets one walk allowance', DB_TOOLS,
        '        self.walk = WalkBudget()',
        '        self.walk = None',
        [BATCH + "test_a_batch_shares_one_allowance"],
        'a db_edit carrying two hundred deletes of unreferenced entries got two hundred separate 200,000-step allowances, because the walk returns early only when it finds hits',
    ),
    Guard(
        'the measurement fails closed on every abnormal exit', SAFETY,
        '''        if done.returncode == COMPILE_FAILED_EXIT:''',
        '''        if done.returncode != 0 and "error" in (done.stderr or "").lower():''',
        [FAILCLOSED2 + "test_every_abnormal_exit_is_refused",
         FAILCLOSED2 + "test_an_abnormal_exit_is_never_cached_as_a_pass"],
        '"error" in stderr stood in for "was this a compile error?", so MemoryError, RecursionError, ImportError and a non-interpreter sys.executable all skipped the refusal and were cached as measured passes -- four of six abnormal exits, measured',
    ),
    Guard(
        'the preflight child cannot import from the working directory', SAFETY,
        '    command = [sys.executable, "-I", "-c", _CHILD]',
        '    command = [sys.executable, "-c", _CHILD]',
        [CHILD + "test_the_child_cannot_import_from_the_working_directory"],
        "python -c puts the process's CWD first on sys.path, so a re.py in an untrusted project directory is executed by the first regex search of the session -- and the CWD is routinely the project folder",
    ),
    Guard(
        'a declared length limit applies to every mode', SEARCH_TOOLS,
        '''    if len(query) > MAX_PATTERN_CHARS:
        raise ToolError(
            "the query is %d characters, above the %d this server accepts. "''',
        '''    if False:
        raise ToolError(
            "the query is %d characters, above the %d this server accepts. "''',
        [LENGTHS + "test_a_long_substring_query_is_refused",
         LENGTHS + "test_both_modes_refuse_at_the_same_length"],
        'the substring branch returned before the check, so a megabyte-long query was accepted while the same string as a regex was refused -- and the ceiling test passed it because it checks that a limit is declared, not that it is applied',
    ),
    Guard(
        'a variable id is checked rather than coerced', COMMANDS,
        '    variable_id = _needs_int(entry["variable"], "variable")',
        '    variable_id = int(entry["variable"])',
        [DSLINT + "test_a_float_variable_id_is_refused",
         DSLINT + "test_a_boolean_variable_id_is_refused"],
        'int(2.9) is 2, so {"variable": 2.9} wrote game variable 2 and the tool reported success against the id the caller named -- the rule registry.as_int states, applied in this file to switch and indent but not to the ids that select which variable a command writes',
    ),
    Guard(
        'a choice branch is never compiled away', COMMANDS,
        '    if len(branches) > len(choices):',
        '    if False:',
        [DSLCHOICE + "test_more_branches_than_choices_is_refused"],
        'three branches against two choices compiled two and discarded the third, then reported the page as rewritten -- a dropped argument reporting the same success as an applied one',
    ),
    Guard(
        'every conversion branch is callable', CONVERT,
        '        return _string_to_json(RubyString(bytes(value)), codec, tables, walk)',
        '        return _string_to_json(RubyString(bytes(value)), codec)',
        [CALLABLE + "test_a_bare_bytes_value_converts",
         CALLABLE + "test_it_agrees_with_the_ruby_string_branch"],
        'the branch passed two of four required arguments, so it raised TypeError if reached -- reported to the model as "an argument was not the shape the tool expected", a diagnosis about the caller for a defect in the converter',
    ),
    Guard(
        'a preview is phrased as a preview by every tool that writes', REGISTRY,
        """    if not dry_run:
        return body""",
        """    if True:
        return body""",
        # The seven-tool tests, plus the two db_edit-specific ones that used
        # to guard `_edit_reply` before its rule moved here. A guard is
        # identified by the line it reverts, so folding them in beats a
        # second guard on the same line.
        [PREVIEW + "test_every_preview_says_so_at_the_top_level",
         PREVIEW + "test_no_preview_uses_a_past_tense_key",
         PREVIEW + "test_no_row_inside_a_preview_uses_one_either",
         DRYRUN + "test_a_preview_does_not_claim_to_have_applied_anything",
         DRYRUN + "test_a_previewed_change_is_named_in_the_conditional"],
        'five of the seven writing tools answered a dry run in the past tense with the one field saying otherwise nested a level down -- and map_edit disagreed with itself, its create and delete paths saying would_delete while its update and resize paths said changes',
    ),
    Guard(
        'narrowing a validate narrows what it reads', ANALYSIS,
        '    map_sizes = _MapSizes(project)',
        """    map_sizes = {map_id: (_int(m.get("width")), _int(m.get("height")))
                 for map_id, m in iter_maps(project)}""",
        [NARROW + "test_a_narrowed_run_does_not_parse_every_map",
         NARROW + "test_a_transfer_to_an_undersized_map_is_still_caught"],
        'the map-size lookup was built from every map on disk, so validate map_ids=[5] cost 74% of a whole-project run and the argument that exists to make a run cheap bought nothing -- measured on the real 39-map project, now 23%',
    ),
    Guard(
        'the map scope collects into one bounded list', ANALYSIS,
        # `full` rather than one of its three readers -- the loop condition,
        # `add` and `extend` all consult it, so reverting any one of them
        # leaves the other two holding the line. Defence in depth is worth
        # having and does mean this is the only decisive anchor.
        """    @property
    def full(self) -> bool:
        return len(self.items) >= MAX_FINDINGS""",
        """    @property
    def full(self) -> bool:
        return False""",
        [MAPSCOPE + "test_the_map_scope_stops_at_the_ceiling"],
        "_check_encounters bounded a list of its own that check_maps then discarded per map, while check_maps had no guard at all -- two maps against a ceiling of four collected eight, and the map count comes out of the project. Third time the ceiling was written against the wrong list, after _AssetChecker's four callers and check_tilesets",
    ),
    Guard(
        'the command reference is generated from its own tables', DOCS,
        '        *_shared_values(engine),',
        """        "* Variable operations: 0 set, 1 add, 2 subtract, 3 multiply, 4 divide,",
        "  5 modulo. Operand types: 0 constant, 1 variable, 2 random range.",""",
        [DOCSAGREE + "test_the_ace_document_does_not_claim_the_unknown_operands"],
        'six live tables were hand-typed into the document as prose and one had already drifted into contradicting the module it describes: it stated the Control Variables operand types as a closed set while commands.py records that VX Ace reorganised everything above 2 into a layout this server does not model -- in the one section of an engine-keyed document that was not engine-aware',
    ),
    Guard(
        'validate can be told to look at the disk again', MISC_TOOLS,
        """    if opt_bool(args, "refresh"):""",
        """    if False:""",
        [ASSETSTALE + "test_an_added_graphic_stops_being_reported"],
        "the asset listings are remembered for the life of the process and nothing invalidated them, so a developer who followed this tool's advice and added the missing graphic was told it was still missing on every later run until the server restarted -- demonstrated on a copy of the real 39-map project",
    ),
    Guard(
        'two spellings of one id agree about what is legal', DB_TOOLS,
        """                        "back under \\"missing_ids\\".", minimum=0),""",
        """                        "back under \\"missing_ids\\".", minimum=0,
                   maximum=MAX_ENTRY_ID),""",
        [SPELLINGS + "test_both_spellings_answer_a_high_id_the_same_way",
         SPELLINGS + "test_the_schema_declares_the_same_bound_on_both"],
        'db_get ids=[999999999] was accepted while db_get id=999999999 was refused -- one request, two spellings, two answers about what is a legal entry id',
    ),
    Guard(
        'a map edit resolves each argument once', MAP_EDIT,
        '    _refuse_structural(request.patch)',
        '    _refuse_structural(opt_dict(args, "patch"))',
        [ONCE + "test_a_patch_is_read_once"],
        'name and patch existed twice in one call chain: map_edit refused one copy and _apply_map_edit wrote another, equal only because opt_str and opt_dict are pure and nothing mutates args between them -- a property of the readers, in the one place where the value checked has to be the value written',
    ),
    Guard(
        'a name from a file is capped wherever it is printed', REGISTRY,
        '    return decoded_text(ctx.codec, value)',
        '    return ctx.codec.decode(value.data) if value is not None else ""',
        [CRAFTED + "test_map_list_caps_a_crafted_name",
         CRAFTED + "test_map_list_sanitises_control_characters"],
        'four copies of this decode in the tools layer had neither the cap nor the sanitising that analysis._text has, and that docstring says why: a value out of a file this server did not write would otherwise be repeated whole into every message about it. Measured -- a 1,000,006-character map name carrying an ANSI escape went into map_list intact while the same name in a finding came back capped',
    ),
    Guard(
        'the decoded-file cache has a total ceiling', PROJECT,
        '        self._evict_over_budget()',
        '        pass',
        [CACHEBOUND + "test_the_cache_stays_within_its_ceiling"],
        'MAX_DATA_FILE_BYTES bounds one file at 128 MB and nothing bounded the sum, while Marshal decodes at about 32 times its on-disk size for the shape a database table has -- measured, 1.61 MB of file becoming 52 MB held, with no eviction path but the named invalidations',
    ),
    Guard(
        'a staged file is never evicted', REGISTRY,
        '        self.project.pin(*filenames)',
        '        pass',
        [CACHEBOUND + "test_a_staged_file_is_never_evicted"],
        'every editing tool mutates the value in the cache and then asks for it to be written, so evicting that entry in between writes the file as it was on disk and reports the edit as applied',
    ),
    Guard(
        'a foreign backup directory is stepped over', PROJECT,
        '            if stale.name in spared or _is_reparse_point(stale):',
        '            if stale.name in spared or stale.is_symlink():',
        [FOREIGN + "test_the_real_snapshots_are_still_pruned",
         FOREIGN + "test_pruning_does_not_fail_because_of_it"],
        "is_symlink() is False for an NTFS directory junction, so the filter that meant to step over a directory this server did not create stepped into one and _contained then failed the prune for the whole project. Not the data loss a review predicted -- rmtree refuses a junction on 3.11 and _contained raises before it -- but one foreign directory stopping the work, with a message about containment rather than about a directory that was never ours",
    ),
]


TEST_TIMEOUT = 300


def run_tests(tree: Path, tests: list[str]) -> subprocess.CompletedProcess | None:
    """Run one guard's tests in the scratch tree, or None if they hung.

    A hang counts as a failure, not as an error to report. Some of what these
    guards protect is a client being left waiting -- an unanswered batch, a
    message never framed -- and with such a guard reverted the test does not
    fail, it simply never finishes. Treating the timeout as anything but a
    failure would report those guards as unprovable.
    """
    try:
        return subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--no-header", *tests],
            cwd=tree, capture_output=True, text=True,
            env={**dict(__import__("os").environ), "PYTHONPATH": str(tree / "src")},
            timeout=TEST_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return None


def prove(guard: Guard, run=None) -> tuple[bool, str]:
    """Revert one guard in a scratch copy and require its tests to fail.

    Failing with the guard reverted is only half of it: the tests have to pass
    with the guard in place, or their failure says nothing about the guard.
    This checked only the first half, so a test broken for an unrelated reason
    made its guard report PROVEN -- which is precisely the false assurance
    this whole script exists to prevent, in the script itself. Found when
    `TestMapReferenceCountsDoNotUnderstate` broke on a constant that had moved
    and its guard went on reporting PROVEN.

    The baseline runs only when the reverted run failed, so the cost lands on
    the guards that would otherwise be believed. ``run`` is injected by the
    tests, which drive every verdict without copying a tree.
    """
    run = run or run_tests
    with tempfile.TemporaryDirectory(prefix="prove-guard-") as tmp:
        tree = Path(tmp) / "tree"
        shutil.copytree(ROOT, tree, ignore=shutil.ignore_patterns(
            "__pycache__", ".git", ".pytest_cache", "build", "dist", "*.egg-info"))

        target = tree / guard.file
        source = target.read_text(encoding="utf-8")
        if guard.present not in source:
            # Distinct from VACUOUS, and a different thing to fix: the guard is
            # still describing code that has since moved or been reworded, so it
            # has silently stopped watching anything at all.
            return False, ("STALE: this guard's source text is no longer in %s, "
                           "so nothing was reverted. Re-anchor it to the code "
                           "as it now reads." % guard.file)
        target.write_text(source.replace(guard.present, guard.reverted, 1),
                          encoding="utf-8")

        result = run(tree, guard.tests)
        if result is not None and result.returncode == 0:
            # No baseline needed: tests that pass without the guard are
            # checking something else whether or not they also pass with it.
            return False, ("VACUOUS: the tests still pass with the guard "
                           "reverted, so they are checking something else")

        # The tests failed or hung. Put the guard back and make sure that was
        # the guard's absence talking.
        target.write_text(source, encoding="utf-8")
        baseline = run(tree, guard.tests)
        if baseline is None:
            return False, ("BROKEN: these tests hang with the guard in place "
                           "too, so their behaviour without it proves nothing. "
                           "Fix the tests, then prove the guard.")
        if baseline.returncode != 0:
            return False, ("BROKEN: these tests fail with the guard in place "
                           "too, so their failure without it proves nothing "
                           "-- fix the tests first. Baseline said: %s"
                           % (_first_failure(baseline) or "no detail"))

        if result is None:
            return True, ("hung for %ds with the guard reverted, which is the "
                          "failure it prevents" % TEST_TIMEOUT)
        return True, (_first_failure(result) or "failed as expected")


def _first_failure(result) -> str:
    """The most useful line of a failed test run, for the verdict to quote."""
    lines = [line for line in result.stdout.splitlines()
             if line.startswith("E ") or " - " in line]
    return lines[0].strip() if lines else ""


def check_anchors(guards: list[Guard]) -> int:
    """Report any guard whose anchor no longer matches its file exactly once.

    Editing the code a guard watches is normal; leaving the guard behind is
    not. A stale anchor reverts nothing and so proves nothing, and an anchor
    that matches twice reverts whichever copy comes first -- which may not be
    the one the test walks. Both read exactly like a guard that passes, and a
    full run takes twenty minutes to say so, which is long enough that it is
    tempting not to. This says it in seconds, so there is no excuse.
    """
    problems = []
    for guard in guards:
        source = (ROOT / guard.file).read_text(encoding="utf-8")
        found = source.count(guard.present)
        if found == 0:
            problems.append((guard.name, "STALE: no longer in %s" % guard.file))
        elif found > 1:
            problems.append((guard.name,
                             "AMBIGUOUS: matches %d places in %s, so the "
                             "revert may hit the wrong one"
                             % (found, guard.file)))
    for name, detail in problems:
        print("PROBLEM  %-46s %s" % (name, detail))
    if problems:
        print()
        print("%d of %d anchors need attention. Re-anchor them to the code "
              "as it now reads, then prove them." % (len(problems), len(guards)))
        return 1
    print("All %d anchors match their file exactly once." % len(guards))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true",
                        help="List the guards without proving them.")
    parser.add_argument("--only", metavar="TEXT", action="append",
                        help="Only guards whose name contains TEXT. Repeat it "
                             "to prove several: a round's guards are proven "
                             "together, which is how they are written.")
    parser.add_argument("--check", action="store_true",
                        help="Only check that every anchor still matches its "
                             "file exactly once. Runs no tests, so it is "
                             "seconds rather than minutes.")
    args = parser.parse_args()

    wanted = [text.lower() for text in args.only or ()]
    guards = [g for g in GUARDS
              if not wanted or any(text in g.name.lower() for text in wanted)]
    if wanted and not guards:
        # Silently proving nothing reads exactly like proving everything.
        print("no guard's name contains %s" % " or ".join(args.only))
        return 1
    if args.check:
        return check_anchors(guards)
    if args.list:
        for guard in guards:
            print("%-46s %s" % (guard.name, ", ".join(guard.tests)))
        return 0

    print("Proving %d guards. Each one is reverted in a scratch copy of the "
          "tree,\nand its tests must then fail.\n" % len(guards))
    failures = []
    for index, guard in enumerate(guards, start=1):
        proven, detail = prove(guard)
        mark = "PROVEN " if proven else "PROBLEM"
        print("%s  %2d/%d  %-46s" % (mark, index, len(guards), guard.name))
        print("         without it: %s" % guard.why)
        if not proven:
            print("         %s" % detail)
            failures.append((guard.name, detail))
    print()
    if failures:
        print("%d guard(s) could not be proven:" % len(failures))
        for name, detail in failures:
            print("  %-46s %s" % (name, detail.split(":")[0]))
        return 1
    print("All %d guards proven non-vacuous." % len(guards))
    return 0


if __name__ == "__main__":
    sys.exit(main())
