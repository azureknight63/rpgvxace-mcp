"""Every guard in this codebase: the edit that removes it, and its tests.

The `Guard` record and every instance of it: append-only, one entry per
fix, which is why it reached two thousand lines. `prove_guards.py` next
door is the engine that reads them, and the two are read for entirely
different reasons -- a verdict you do not believe sends you to the engine,
and you had to scroll past all of this to reach it.

`Guard` itself is here rather than there because it is the shape of the
data, not part of the machine: it holds the fields and refuses a
mismatched anchor pair at import time, and the engine imports it from
here along with the list.

The aliases are here rather than there because they are part of the data:
`LINT` and `BOUNDED` mean nothing to the engine, which takes the strings
they hold as opaque.

`tests/test_prove_guards.py` checks every entry: the named tests must exist
and be collectable, no two guards may watch one line, no two aliases may
name one thing, and a reverted form must differ from the present one.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
# Every source file a guard watches. `_SRC` is written once because the
# prefix was repeated thirty times, and a typo in one of thirty is a
# STALE verdict about a file that is fine.
_SRC = "src/rpgvxace_mcp/"


LINT_ASSETS = _SRC + "analysis/lint/assets.py"
LINT_COMMANDS = _SRC + "analysis/lint/command_rules.py"
LINT_DATABASE = _SRC + "analysis/lint/database.py"
LINT_FINDINGS = _SRC + "analysis/lint/findings.py"
LINT_MAPS = _SRC + "analysis/lint/maps.py"
LINT_PAGES = _SRC + "analysis/lint/pages.py"
LINT_REPORT = _SRC + "analysis/lint/report.py"


REFERENCES = _SRC + "analysis/references.py"
WALK = _SRC + "analysis/walk.py"


BACKUPS = _SRC + "backups.py"
COMMANDS = _SRC + "commands.py"
CONVERT = _SRC + "convert.py"
DOCS = _SRC + "docs.py"
CODEC = _SRC + "marshal_rb.py"
PROJECT = _SRC + "project.py"
RUBYTYPES = _SRC + "rubytypes.py"
SCHEMA = _SRC + "schema.py"
SERVER = _SRC + "server.py"


COMMAND_DOCS = _SRC + "tools/command_docs_tools.py"
DB_TOOLS = _SRC + "tools/db_tools.py"
EVENT_TOOLS = _SRC + "tools/event_tools.py"
MAP_COMMON = _SRC + "tools/map_common.py"
MAP_EDIT = _SRC + "tools/map_edit_tools.py"
MAP_READ = _SRC + "tools/map_read_tools.py"
MAP_TILE = _SRC + "tools/map_tile_tools.py"
MISC_TOOLS = _SRC + "tools/misc_tools.py"
SAFETY = _SRC + "tools/pattern_safety.py"
RAW_TOOLS = _SRC + "tools/raw_tools.py"
REGISTRY = _SRC + "tools/registry.py"
SEARCH_TOOLS = _SRC + "tools/search_tools.py"


class Guard:
    """One guard, the edit that removes it, and the test that must then fail."""

    def __init__(self, name: str, file: str, present: "str | list[str]",
                 reverted: "str | list[str]", tests: list[str], why: str):
        self.name = name
        self.file = file
        # A property can be held in two places at once, and then no single
        # reversion proves it: `event_get`'s negative page is refused by the
        # `minimum` its schema declares, which `check_args` enforces, and
        # again by the `opt_int` call that reads it. Removing either left the
        # other, so the guard read VACUOUS -- a true answer about the code
        # and a false one about the guard. Anchors given as a list are
        # reverted together, which is the only way to ask "is this held at
        # all" of a property that is held twice.
        self.present = [present] if isinstance(present, str) else list(present)
        self.reverted = ([reverted] if isinstance(reverted, str)
                         else list(reverted))
        if len(self.present) != len(self.reverted):
            raise ValueError(
                "guard %r names %d anchors and %d replacements; reverting "
                "the first and skipping the second would report a verdict "
                "about an edit nobody wrote"
                % (name, len(self.present), len(self.reverted)))
        self.tests = tests
        self.why = why

    @property
    def anchors(self) -> list[tuple[str, str]]:
        """Each (present, reverted) pair, in the order they are applied."""
        return list(zip(self.present, self.reverted))


# Every test class a guard names, grouped by the file it lives in and
# alphabetical within the group. This block was ordered by line *width*,
# so the thirty aliases for one suite were scattered across all 132 lines
# and two carried gratuitous parentheses to satisfy the sort. The per-file
# census is now visible by eye, which is what makes a future `tests/` split
# a contiguous edit rather than a 132-line scan.


def _at(module: str, cls: str) -> str:
    """One test class, as `prove_guards` names it."""
    return "tests/%s.py::%s::" % (module, cls)


# test_guards_arguments
DSLCHOICE = _at("test_guards_arguments", "TestAChoiceBranchIsNeverDropped")
CRAFTEDCOMMON = _at(
    "test_guards_arguments", "TestACraftedCommonEventIsReportedNotCrashed")
TYPED = _at("test_guards_arguments", "TestADeclaredTypeIsChecked")
ONCE = _at("test_guards_arguments", "TestAMapEditResolvesEachArgumentOnce")
BEFOREWORK = _at("test_guards_arguments", "TestARefusalComesBeforeTheWork")
ADDPAGE = _at("test_guards_arguments", "TestAddPageRefusesInsteadOfCrashing")
EMPTYC = _at(
    "test_guards_arguments", "TestAnEmptyCommandListIsRefusedEverywhere")
SHAPE = _at("test_guards_arguments", "TestArgumentShapeIsEnforced")
COERCE = _at("test_guards_arguments", "TestArgumentsAreNotCoerced")
COMMON = _at("test_guards_arguments", "TestCommonEventOpsAreGatedToo")
DBID = _at("test_guards_arguments", "TestDatabaseIdsAreChecked")
DBARG = _at("test_guards_arguments", "TestDbGetArgumentsAreHonoured")
PAGEARG = _at("test_guards_arguments", "TestEventGetChecksThePageItWasGiven")
LENGTHS = _at(
    "test_guards_arguments", "TestEveryDeclaredLengthLimitIsEnforced")
DSLIDS = _at(
    "test_guards_arguments", "TestEveryIdInTheDslRefusesACoercibleValue")
GATES = _at("test_guards_arguments", "TestEveryOpSelectingToolHasATable")
EVERY = _at("test_guards_arguments", "TestGuardsRunOnEveryPath")
AREAID = _at("test_guards_arguments", "TestHashTablesThatCarryTheirOwnId")
OPARG = _at("test_guards_arguments", "TestInapplicableArgumentsAreRefused")
TILESNONE = _at("test_guards_arguments", "TestMapGetHonoursTilesWithoutATable")
CANON = _at("test_guards_arguments", "TestMapIdsAreCanonical")
EMPTYT = _at("test_guards_arguments", "TestMapTilesNamesAnEmptyTable")
NOOP = _at("test_guards_arguments", "TestNoOpSlipsPastTheGate")
GATED = _at("test_guards_arguments", "TestOpArgumentsAreGatedEverywhere")
NILFILL = _at("test_guards_arguments", "TestRawDataCanFillANilField")
RAWD = _at("test_guards_arguments", "TestRawDataRefusesBreakage")
RAWSCR = _at(
    "test_guards_arguments", "TestRawDataWillNotHandBackCompressedScripts")
READDROP = _at("test_guards_arguments", "TestReadToolsDropNoArgumentsEither")
INERT = _at("test_guards_arguments", "TestSearchRefusesAnInertRegexFlag")
DSLINT = _at("test_guards_arguments", "TestTheDslRefusesACoercibleId")
SPELLINGS = _at("test_guards_arguments", "TestTwoSpellingsOfOneArgumentAgree")


# test_guards_fields
NESTED = _at("test_guards_fields", "TestANestedPatchCannotInventAField")
FIELD = _at("test_guards_fields", "TestAPatchCannotInventAField")
WHOLE = _at("test_guards_fields", "TestAWholesaleObjectCannotInventFields")
HIDDEN = _at("test_guards_fields", "TestAnInventedFieldCannotHideInAContainer")
ATFIELD = _at("test_guards_fields", "TestAtPrefixedFieldsAreRefused")
CALLABLE = _at("test_guards_fields", "TestEveryConversionBranchIsCallable")
NAMES = _at("test_guards_fields", "TestEveryNameFromJsonIsChecked")


# test_guards_findings
RIGHTCMD = _at("test_guards_findings", "TestAFindingNamesTheRightCommand")
PAGES = _at("test_guards_findings", "TestDamagedPagesAreRefusedNotCrashedOn")
SCOPEALLOW = _at(
    "test_guards_findings", "TestEveryScopeSpendsTheSameAllowance")
NARROW = _at("test_guards_findings", "TestNarrowingValidateActuallyNarrowsIt")
CEILING = _at("test_guards_findings", "TestNoWalkCollectsPastTheCeiling")
CEILWALK = _at("test_guards_findings", "TestTheCeilingStopsTheWalkingToo")
MAPSCOPE = _at("test_guards_findings", "TestTheMapScopeStopsCollecting")
VHOST = _at("test_guards_findings", "TestValidateSurvivesHostileData")


# test_guards_patterns
SUBJECTS = _at("test_guards_patterns", "TestEveryNamedCharacterIsMeasured")
LOOKAROUND = _at("test_guards_patterns", "TestLookaroundIsRefusedByName")
FAST = _at("test_guards_patterns", "TestSearchPatternsStayFast")
REDOS = _at("test_guards_patterns", "TestSearchRefusesTheHangingPattern")
ALPHABET = _at("test_guards_patterns", "TestTheAlphabetFailsClosed")
FAILCLOSED = _at("test_guards_patterns", "TestTheMeasurementFailsClosed")
FAILCLOSED2 = _at(
    "test_guards_patterns", "TestTheMeasurementFailsClosedOnEveryAbnormalExit")
CHILD = _at("test_guards_patterns", "TestTheMeasurementReachesItsChild")
CLOSED = _at("test_guards_patterns", "TestTheShapeGateFailsClosed")
SHAPECLOSED = _at(
    "test_guards_patterns", "TestTheShapeGateFailsClosedThroughout")
REMEMBERS = _at("test_guards_patterns", "TestWhatTheMeasurementRemembers")


# test_guards_replies
COUNTS = _at("test_guards_replies", "TestACountIsNamedForWhatItCounted")
CRAFTED = _at("test_guards_replies", "TestACraftedNameIsBoundedInAReplyToo")
FILTERED = _at("test_guards_replies", "TestAFilteredListSaysItWasFiltered")
LISTEDNAME = _at(
    "test_guards_replies", "TestAListedNameIsCappedLikeEveryOtherName")
SCOPECLAIM = _at("test_guards_replies", "TestAScopeIsClaimedOnlyOnceRead")
SLOTID = _at("test_guards_replies", "TestAScriptSlotIdIsCalledAnId")
EVERYPATH = _at(
    "test_guards_replies", "TestATruncatedSummarySaysSoOnEveryPath")
LAYOUT = _at(
    "test_guards_replies", "TestCommandReferenceSaysWhoseLayoutsTheseAre")
TALLY = _at("test_guards_replies", "TestFindingCountsAreTrustworthy")
MAPREFS = _at("test_guards_replies", "TestMapReferenceCountsDoNotUnderstate")
REFCNT = _at("test_guards_replies", "TestReferenceCountsDoNotUnderstate")
RTP = _at("test_guards_replies", "TestRtpOverrideIsHonest")
SCRIPTAT = _at("test_guards_replies", "TestScriptHitsSayWhere")
READ = _at("test_guards_replies", "TestSearchDoesNotClaimToHaveRead")
SEARCHED = _at("test_guards_replies", "TestSearchReportsWhatItSearched")
CLIP = _at("test_guards_replies", "TestSearchSaysWhatItDidNotRead")
TILETALLY = _at("test_guards_replies", "TestTileTallySaysWhatItLeftOut")
UNREAD = _at(
    "test_guards_replies", "TestUnreadableStructuresAreNotReportedAsEmpty")
VSTART = _at("test_guards_replies", "TestValidateChecksTheStartPosition")
VMISS = _at(
    "test_guards_replies", "TestValidateDistinguishesMissingFromBroken")
VPAGE = _at("test_guards_replies", "TestValidatePageNumbersMatchTheTools")
VRTP = _at("test_guards_replies", "TestValidateRespectsTheRtp")
VSCOPE = _at("test_guards_replies", "TestValidateScoping")
VTBL = _at("test_guards_replies", "TestValidateTellsUnreadableFromEmpty")


# test_guards_response
RAWPATH = _at("test_guards_response", "TestARawPathCannotBeUnboundedlyLong")
BYTES = _at("test_guards_response", "TestARenderedCommandLineIsBoundedInBytes")
TILEBUF = _at("test_guards_response", "TestATileTallyUsesTheBuffer")
SUMMARY = _at("test_guards_response", "TestMapGetBoundsItsEventSummary")
BOUNDED = _at("test_guards_response", "TestRefusalsAreBounded")
BOMB = _at("test_guards_response", "TestScriptInflateLimit")
CACHEBOUND = _at("test_guards_response", "TestTheFileCacheIsBounded")


# test_guards_walk_budget
SLOTS = _at(
    "test_guards_walk_budget", "TestASkippedPageSlotIsStillCountedForReading")
EMPTYPAGE = _at(
    "test_guards_walk_budget", "TestAnEmptyPageStillCostsSomething")
NOPAGES = _at(
    "test_guards_walk_budget", "TestAnEventWithNoPagesStillCostsARow")
BOUND = _at("test_guards_walk_budget", "TestCollectionIsBounded")
WRITEPATH = _at(
    "test_guards_walk_budget", "TestEveryWalkOnAWritePathIsBounded")
SPENDS = _at("test_guards_walk_budget", "TestEveryWalkSpendsItsAllowance")
GROW = _at("test_guards_walk_budget", "TestGrowingAMapIsBoundedToo")
BATCH = _at("test_guards_walk_budget", "TestOneBatchGetsOneWalkAllowance")
BUDGETONE = _at("test_guards_walk_budget", "TestOneBudgetForAWholeResponse")
LISTCAP = _at("test_guards_walk_budget", "TestOneCommandListHasACeiling")
BUDGET = _at(
    "test_guards_walk_budget", "TestReadingManyEntriesSharesOneBudget")
SEARCHWORK = _at("test_guards_walk_budget", "TestSearchBoundsItsTotalWork")
AMPLIFY = _at(
    "test_guards_walk_budget", "TestSharedChildrenCannotAmplifyAWalk")
ONETRUTH = _at("test_guards_walk_budget", "TestTheMapListingHasOneTruth")


# test_guards_writes
DELPREV = _at("test_guards_writes", "TestADeletePreviewMatchesTheDelete")
DRYRUN = _at("test_guards_writes", "TestADryRunDoesNotReadAsDone")
FOREIGN = _at("test_guards_writes", "TestAForeignBackupDirectoryIsSkipped")
MAPEV = _at("test_guards_writes", "TestAMapPatchCannotMoveAnEvent")
ASSETSTALE = _at("test_guards_writes", "TestAnAssetAddedMidSessionIsSeen")
PRUNE = _at("test_guards_writes", "TestBackupPruning")
DELSTALE = _at("test_guards_writes", "TestDeletingRespectsStaleness")
LOG = _at("test_guards_writes", "TestDeletionIsLogged")
DROPPED = _at("test_guards_writes", "TestDiscardedTileCountIsRight")
PREVIEW = _at("test_guards_writes", "TestEveryPreviewReadsAsAPreview")
HOSTILE = _at("test_guards_writes", "TestHostileDataFolder")
CREATE = _at("test_guards_writes", "TestMapCreateIsStaged")
MAPW = _at("test_guards_writes", "TestMapWritesStayConsistent")
STAGE = _at("test_guards_writes", "TestNothingIsStaged")
PATH = _at("test_guards_writes", "TestPathContainment")
PATHRO = _at("test_guards_writes", "TestPathContainmentReadOnly")
RESTORE = _at("test_guards_writes", "TestRestoreValidatesBeforeActing")
FORM = _at("test_guards_writes", "TestScriptNameStringForm")
SNAPDRY = _at("test_guards_writes", "TestSnapshotPreviewMatchesTheSnapshot")
PERFILE = _at("test_guards_writes", "TestStagingIsPerFile")
STALE = _at("test_guards_writes", "TestStalenessDetection")

# test_marshal
T = _at("test_marshal", "TestHostileInput")
L = _at("test_marshal", "TestLosslessEdgeCases")


# test_protocol
HARD = _at("test_protocol", "TestTransportHardening")


# test_schema
PROF = _at("test_schema", "TestEngineProfiles")
SAFE = _at("test_schema", "TestResponseTextSafety")
OBS = _at("test_schema", "TestSchemaObservation")
PROV = _at("test_schema", "TestSchemaReportProvenance")
DOCSAGREE = _at("test_schema", "TestTheReferenceAgreesWithTheTables")
UNMOD = _at("test_schema", "TestUnmodelledEngine")


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
        "table extent validation", RUBYTYPES,
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
        "table declared dimension", RUBYTYPES,
        """        if not 1 <= dim <= 3:
            raise ValueError("Table declares %d dimensions, expected 1, 2 or 3" % dim)
""",
        "",
        [T + "test_table_with_impossible_dimension_degrades"],
        "RGSS subscripts a Table by its declared dimension, so a nonsense one "
        "makes the cells unreachable in the engine",
    ),
    Guard(
        "table payload exact length", RUBYTYPES,
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
        [L + "test_a_symbol_in_a_name_position_is_reported"],
        "a non-ASCII class or ivar name loads but cannot be written back, and "
        "nothing says so -- the file quietly changes shape. The test that "
        "used to be named here checked the value came back whole, which it "
        "does either way, so this guard read VACUOUS while the warning it "
        "watches was the only thing standing",
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
        """        if id(value) in seen:
            return
        seen.add(id(value))""",
        """        pass""",
        [OBS + "test_link_shared_graph_does_not_explode",
         OBS + "test_shared_object_is_tallied_once"],
        "Marshal links make the loaded value a graph, so a shared subtree is "
        "walked once per path -- exponential work from a few hundred bytes",
    ),
    Guard(
        "schema walk covers wrapper types", SCHEMA,
        # The five per-wrapper branches this used to name became one loop
        # over `ruby_children`, which is the function that answers which
        # kinds hold other values -- so that is what the guard watches.
        """    children = ruby_children(value)""",
        """    children = value.ivars.values() if isinstance(value, RubyObject) else []""",
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
        "response text has no lone surrogates", RUBYTYPES,
        """    return "".join(char if char.isprintable() or char in KEPT_CONTROLS
                   else "?" for char in text)""",
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
        "backup name is one path component", BACKUPS,
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
        "containment is checked before the read-only gate", BACKUPS,
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
        "read-only blocks snapshots and pruning", BACKUPS,
        '        self._assert_writable("pruning backups")',
        "",
        [PATHRO + "test_snapshot_and_prune_are_refused"],
        "a server started read-only still creates and deletes directories on "
        "the operator's disk",
    ),
    Guard(
        "pruning spares the snapshot being restored", BACKUPS,
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
        """            if current == cached.digest:""",
        """            if True:""",
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
        "restore validates every name before it copies", BACKUPS,
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
        "tileset sheets go through the asset checker", LINT_DATABASE,
        "        checker.check(sheet_name, \"tilesets\",",
        "        _ = sheet_name and checker and (\"tilesets\",) and (",
        [VRTP + "test_a_tileset_sheet_miss_is_downgraded_with_an_unresolved_rtp"],
        "one rule keeps its own asset comparison, so a project whose RTP cannot "
        "be located gets up to nine warnings per tileset for the default sheets "
        "that live in the RTP -- and they outrank the downgraded ones",
    ),
    Guard(
        "an unreadable table is not an empty table", LINT_FINDINGS,
        "        self._load(name)\n"
        "        if name in self._failures:\n"
        "            return None",
        "        self._load(name)\n"
        "        if False:",
        [VTBL + "test_an_unreadable_table_is_one_finding_not_a_dangling_reference_each"],
        "a table caught half-written by the editor reads as empty, so every id "
        "that refers to it is reported as missing",
    ),
    Guard(
        "page numbers are the ones the tools take", LINT_PAGES,
        "        % (last_unconditional, event_id, name, last_unconditional - 1),",
        "        % (last_unconditional + 1, event_id, name, last_unconditional),",
        [VPAGE + "test_a_page_finding_names_the_page_the_tools_take"],
        "the message counts pages from one while every tool argument counts from "
        "zero, so a model acting on \"page 2\" edits the third page",
    ),
    Guard(
        "missing means absent, not unparseable", LINT_COMMANDS,
        '    if map_id not in on_disk:\n        return [_missing(',
        '    if map_id not in map_sizes:\n        return [_missing(',
        [VMISS + "test_a_transfer_to_an_unparseable_map_is_not_called_missing"],
        "a map that exists but will not parse is reported as not existing, which "
        "sends an agent recreating a file that is already there",
    ),
    Guard(
        "a tile table is checked for being one", LINT_MAPS,
        "    if not isinstance(data, Table):",
        "    if False:",
        [VHOST + "test_a_tile_table_that_is_not_a_table"],
        "reading .xsize off whatever @data holds raises out of the whole validate "
        "run, so one odd ivar denies the tool for that project",
    ),
    Guard(
        "map ids in MapInfos are addressable ones", LINT_MAPS,
        "        if _int(map_id) is None or not 1 <= map_id <= MAX_MAP_ID:",
        "        if _int(map_id) is None:",
        [VHOST + "test_a_map_id_no_filename_can_express"],
        "an id no Map%03d name can express is reported as a missing file instead "
        "of as the bad id it is",
    ),
    Guard(
        "a non-numeric parent_id stops the walk", LINT_MAPS,
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
        "repeated map ids are collapsed", LINT_REPORT,
        "    wanted = sorted({i for i in (_int(m) for m in map_ids) if i is not None})",
        "    wanted = [i for i in (_int(m) for m in map_ids) if i is not None]",
        [VSCOPE + "test_repeated_map_ids_are_counted_once"],
        "a caller repeating an id multiplies every per-map rule by however many "
        "times they repeated it, and the reported map count with it",
    ),
    Guard(
        "the findings limit cannot be negative", LINT_REPORT,
        "    limit = max(1, _int(limit) or DEFAULT_FINDING_LIMIT)",
        "    limit = _int(limit) or DEFAULT_FINDING_LIMIT",
        [VSCOPE + "test_a_negative_limit_does_not_drop_findings"],
        "a negative limit slices the findings list from the wrong end and drops "
        "the ones the caller most needed to see",
    ),
    Guard(
        "the start position is bounds-checked", LINT_DATABASE,
        "            findings.extend(_check_start_position("
        "project, system, start_map))",
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
        "a script hit says where it is", WALK,
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
        '    if path and _bare_name(path[0]) in STRUCTURAL_MAP_FIELDS:',
        '    if path and path[0] in STRUCTURAL_MAP_FIELDS:',
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
        "findings are counted before they are dropped", LINT_REPORT,
        "    tally = Counter(f.severity for f in findings)",
        "    tally = Counter(f.severity for f in findings[:limit])",
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
        "findings stop being collected at the ceiling", LINT_COMMANDS,
        # Anchored with its own comment, because the bare line now appears in
        # every walk that appends inside a loop -- which is the point of the
        # round that added them, and made this anchor ambiguous.
        '''            if findings.full:
                # The ceiling is a collection ceiling, so it is checked here
                # rather than at the end: one page of a million Jump to Label
                # commands with no matching Label is a million findings.
                break''',
        '''            if False:
                break''',
        # The *walking*, asked of the budget. `_Findings.add` caps what
        # is kept, so a test counting findings passes with this line
        # reverted -- which is how this guard came back VACUOUS once
        # `validate` began collecting into one collector.
        [CEILWALK + "test_the_command_walk_stops_visiting_at_the_ceiling"],
        "the ceiling bounds only what is returned, so a page of broken "
        "jumps reads on and charges the walk allowance for a finding per "
        "command -- tens of thousands of them -- in order to report ten",
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
        'borrowed command layouts are labelled', COMMAND_DOCS,
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
        'a reference count says when it is a floor', REFERENCES,
        '''    return ReferencePreview(found[:REFERENCE_PREVIEW],
                            len(found) > REFERENCE_PREVIEW, note)''',
        '''    return ReferencePreview(found[:REFERENCE_PREVIEW], False, note)''',
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
        '''        return write_reply(ctx.project.snapshot(label, dry_run=dry_run),
                           dry_run)''',
        '        if dry_run:\n            return {"would_copy": sorted(\n                p.name for p in ctx.project.data_dir.glob(\n                    "*" + ctx.project.extension)),\n                "label": label, "dry_run": True}\n        return write_reply(ctx.project.snapshot(label), dry_run)',
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
        [T + "test_rejects_a_deeply_nested_symbol"],
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
        [FAST + "test_every_costly_pattern_is_refused",
         FAST + "test_the_tool_refuses_them_too"],
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
        '    refuse_inert(\n        args, ("event_ids", "name_contains", "page"),',
        '    _unused = (\n        args, ("event_ids", "name_contains", "page"),',
        [READDROP + "test_a_common_event_read_refuses_page_arguments"],
        'event_get common_event_id=N name_contains=X returns the event whether or not it matches, and the reply reads exactly like a positive match',
    ),
    Guard(
        'a code lookup refuses a search', COMMAND_DOCS,
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
        # Two anchors, because the property is held twice: `check_args`
        # enforces the `minimum` the schema declares, and `opt_int` checks it
        # again where it is read. Reverting either left the other and the
        # guard read VACUOUS -- a true answer about the code and a false one
        # about the guard.
        ['''                          "two tools differ on that.", minimum=0),''',
         '    only_page = opt_int(args, "page", minimum=0)'],
        ['''                          "two tools differ on that."),''',
         '    only_page = opt_int(args, "page")'],
        [PAGEARG + "test_a_negative_page_is_refused"],
        'a negative page is accepted and filters nothing out, while event_edit refuses the same value and raw_data gives it the opposite meaning',
    ),
    Guard(
        'the delete preview matches the delete', MAP_EDIT,
        '            "deleted": filename if has_file else None}',
        '            "deleted": filename}',
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
        'def named(names: "Any", limit: int = MAX_NAMES_LISTED,',
        'def named(names: "Any", limit: int = 10 ** 9,',
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
        'shared children cannot amplify a walk', WALK,
        '                    if budget is not None and not budget.spend(len(commands)):\n                        return',
        '                    if False:\n                        return',
        # Only the validate path. `event_get` walks its own loop with its
        # own budget and `search` stops on its deadline, so both of those
        # tests pass with this line reverted -- and each has its own guard
        # ('a walk stops on the deadline, not only on the limit', and the
        # per-event and per-page-slot charges below). Naming all three here
        # meant no single reversion could fail them all, which reads as
        # VACUOUS and is really three properties in one guard.
        [AMPLIFY + "test_validate_stops_and_says_so"],
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
        [FAST + "test_every_costly_pattern_is_refused"],
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
        'a delete asks what points at it under a budget', REFERENCES,
        # One walk now: `find_references` delegates to the batch form with
        # a single id, so the two bodies that each had to take the
        # allowance are one body that does.
        '''    for location, commands in iter_command_lists(project, None, budget):''',
        '''    for location, commands in iter_command_lists(project):''',
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
        '    shown = commands[:command_budget.take(len(commands))]',
        '    shown = commands',
        [WRITEPATH + "test_reading_a_common_event_is_bounded",
         WRITEPATH + "test_a_bounded_common_event_still_reports_its_real_length"],
        'a common event whose @list is 250,000 links to one command is a few kilobytes on disk and renders a thirty-megabyte reply',
    ),
    Guard(
        'the asset collector refuses past its ceiling', LINT_ASSETS,
        '''        """
        if self.full:
            return''',
        '''        """
        if False:
            return''',
        [CEILING + "test_the_asset_checker_itself_stops_collecting",
         CEILING + "test_the_asset_walk_stops_collecting"],
        'four walks share one collector and none of them bounded it -- one crafted map produced 1600 findings against a ceiling of 4',
    ),
    Guard(
        'the event page walk stops at the ceiling', LINT_PAGES,
        '''            # Two findings per event at most, and the event count comes out of
            # the file: a crafted map that shares one event object across
            # forty thousand slots produced forty thousand findings here.
            if findings.full:
                return findings.items''',
        '''            if False:
                return findings''',
        [CEILWALK + "test_the_page_walk_stops_visiting_at_the_ceiling"],
        'one event object shared across forty thousand slots keeps the walk charging for a finding per slot, long after the ceiling has stopped keeping them',
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
        # Two anchors: the round that made every examined page slot count
        # gave this ceiling a second test, inside the pages loop. Either one
        # alone bounds the total, so reverting one left the other and the
        # guard read VACUOUS -- the property is doubly held, and asking "is
        # it held at all" means removing both.
        ["""        if rows >= MAX_EVENT_SUMMARY_ROWS:
            return out, True""",
         """            if rows >= MAX_EVENT_SUMMARY_ROWS:"""],
        ["""        if False:
            return out, True""",
         """            if False:"""],
        [SUMMARY + "test_the_summary_stops_at_its_ceiling",
         SUMMARY + "test_a_stopped_summary_says_so"],
        'events times pages, both out of the file: the shared-object map is fifty events of fifty pages all one object, so 7 KB produced 2500 rows and nothing said why',
    ),
    Guard(
        'a visit is charged for even when it carries nothing', WALK,
        '        self.remaining -= max(1, steps)',
        '        self.remaining -= steps',
        [EMPTYPAGE + "test_the_budget_charges_for_a_list_of_no_commands",
         EMPTYPAGE + "test_search_stops_and_says_so",
         EMPTYPAGE + "test_validate_events_stops_and_says_so"],
        'every charge in the project was spend(len(commands)), which is zero for a page holding an empty list, and spend only trips below zero -- so an 18.4 KB map describing four million (event, page) pairs was walked in full, search taking 5.7s and reporting no stop signal at all',
    ),
    Guard(
        'the walk charges per event and per page slot', WALK,
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
        'the page rule walk takes an allowance', LINT_PAGES,
        '''            if budget is not None and not budget.spend(1):
                return findings.items
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
        'the asset walk takes the allowance its sibling had', LINT_ASSETS,
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
        '''        # The event's own row, before the isinstance test rather than after
        # it. Charging only for a slot that reads as an RPG::Event meant a
        # hash of a million integers was walked free, which is the same
        # defect one level up as the pages loop below had.
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
        'one batch gets one reference walk', DB_TOOLS,
        # The anchor moved from the shared allowance to the shared *walk*.
        # Sharing one allowance capped the damage of asking per op; asking
        # once removes it, so a reverted `self.walk` now proves nothing --
        # `reference_previews` builds its own budget for the one pass it
        # makes. The bulk call is what the property rests on now.
        '''    request.references = reference_previews(
        ctx.project, table,
        {entry_id for entry_id in (opt_int(raw_op, "id")
                                   for raw_op in ops
                                   if raw_op.get("op") == "delete")
         if entry_id is not None},
        "a delete's reference scan", request.walk)''',
        '''    request.references = {}''',
        [BATCH + "test_a_batch_shares_one_allowance"],
        'a db_edit carrying two hundred deletes of unreferenced entries made two hundred whole-project traversals of the same unchanged command lists, because the walk returns early only when it finds hits -- so the ordinary case, deleting an entry nothing points at, is the expensive one',
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
        '    command = [sys.executable, "-I", "-S", "-c", _CHILD]',
        '    command = [sys.executable, "-S", "-c", _CHILD]',
        [CHILD + "test_the_child_cannot_import_from_the_working_directory"],
        "python -c puts the process's CWD first on sys.path, so a re.py in an untrusted project directory is executed by the first regex search of the session -- and the CWD is routinely the project folder",
    ),
    Guard(
        'a declared length limit applies to every mode', SEARCH_TOOLS,
        # Two anchors: the round that taught `_check_declared` to enforce a
        # declared `maxLength` made this property doubly held, so reverting
        # the hand check alone leaves the gate and reads VACUOUS. Removing
        # both is the only way to ask whether it is held at all -- and the
        # hand check still earns its place, because it is what makes the
        # refusal say the same thing in both modes.
        ['''    if len(query) > MAX_PATTERN_CHARS:
        raise ToolError(
            "the query is %d characters, above the %d this server accepts. "''',
         '''                      maxLength=MAX_PATTERN_CHARS),'''],
        ['''    if False:
        raise ToolError(
            "the query is %d characters, above the %d this server accepts. "''',
         '''                      ),'''],
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
        'narrowing a validate narrows what it reads', LINT_COMMANDS,
        '    map_sizes = _MapSizes(project)',
        """    map_sizes = {map_id: (_int(m.get("width")), _int(m.get("height")))
                 for map_id, m in iter_maps(project)}""",
        [NARROW + "test_a_narrowed_run_does_not_parse_every_map",
         NARROW + "test_a_transfer_to_an_undersized_map_is_still_caught"],
        'the map-size lookup was built from every map on disk, so validate map_ids=[5] cost 74% of a whole-project run and the argument that exists to make a run cheap bought nothing -- measured on the real 39-map project, now 23%',
    ),
    Guard(
        'the map scope collects into one bounded list', LINT_FINDINGS,
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
        'a foreign backup directory is stepped over', BACKUPS,
        '            if stale.name in spared or _is_reparse_point(stale):',
        '            if stale.name in spared or stale.is_symlink():',
        [FOREIGN + "test_the_real_snapshots_are_still_pruned",
         FOREIGN + "test_pruning_does_not_fail_because_of_it"],
        "is_symlink() is False for an NTFS directory junction, so the filter that meant to step over a directory this server did not create stepped into one and _contained then failed the prune for the whole project. Not the data loss a review predicted -- rmtree refuses a junction on 3.11 and _contained raises before it -- but one foreign directory stopping the work, with a message about containment rather than about a directory that was never ours",
    ),
    Guard(
        'the gate holds the type it declares', REGISTRY,
        '''        if not isinstance(value, (int, float)):
            raise ToolError(
                "%s must be an integer, got %s. %s"
                % (key, type(value).__name__, echo(value)))''',
        '''        if False:
            raise ToolError(
                "%s must be an integer, got %s. %s"
                % (key, type(value).__name__, echo(value)))''',
        [TYPED + "test_a_string_for_an_integer_is_refused_by_the_gate"],
        "check_args' docstring says it holds a call to the schema its tool declares, and it checked unknown keys, maxItems and integer bounds but never `type` -- so a string sent for a declared integer fell through to whichever accessor reached it first, reported from a different layer in different words",
    ),
    Guard(
        'a refusal that always follows a read comes before it', RAW_TOOLS,
        '''    if table == "scripts":
        # Before the load, not after it.''',
        '''    if False:
        # Before the load, not after it.''',
        [BEFOREWORK + "test_the_scripts_table_is_refused_without_being_read"],
        'raw_data unmarshalled the whole file and then refused the scripts table unconditionally, so the read could never be used and left a cache entry for a table nothing there can act on',
    ),
    Guard(
        'a script slot id is called an id', PROJECT,
        '                "id": slot_id,',
        '                "magic": slot_id,',
        [SLOTID + "test_the_reply_names_it_id"],
        'schema documents the file as "Array of [id, name, zlib code]" and docs renders that to the model, while the code called the first element a magic number -- and scripts op="get" hands that row to the caller verbatim, so the name in the code is the one a model reads',
    ),
    # ---------------- round 8: bytes, and every branch ----------------
    Guard(
        'a rendered command line is capped in bytes', COMMANDS,
        """    if isinstance(value, RubyObject):
        # The Ruby class is the useful answer for a parameter that is an
        # object. `decoded_text` would say `<RubyObject>`, which names this
        # reader's wrapper rather than anything that is in the file.
        return "<%s>" % decoded_text(codec, value.cls)
    return decoded_text(codec, value)""",
        """    if isinstance(value, RubyObject):
        return "<%s>" % value.cls
    return str(value)""",
        [BYTES + "test_one_long_string_parameter_is_capped",
         BYTES + "test_a_long_string_in_a_specced_field_is_capped"],
        'every bound in this project counted nodes and none counted bytes: one command holding a megabyte string was a single step of the walk budget, and the rendered line was the one text with no cap at all',
    ),
    Guard(
        'a command line renders a bounded number of parts', COMMANDS,
        """        return "%s: %s" % (name, rendered(as_list(p(0)),
                                          lambda c: _text(c, codec),
                                          separator=" / "))""",
        """        return "%s: %s" % (name, " / ".join(
            _text(c, codec) for c in as_list(p(0))))""",
        [BYTES + "test_a_long_choice_list_is_capped"],
        'Show Choices renders every choice and the list length comes out of the file, not from MAX_CHOICES which bounds authoring: an Array of a million links to one string is three megabytes on disk',
    ),
    Guard(
        'a capped list is sliced before it is built', RUBYTYPES,
        '    shown = [render(value) for value in items[:limit]]',
        '    shown = [render(value) for value in items]',
        [BYTES + "test_only_the_entries_that_survive_the_cap_are_rendered"],
        'the point of this function is where the work happens: named(render(v) for v in values) has rendered every entry by the time named sees the first, and for values out of a file that rendering is the cost being bounded',
    ),
    Guard(
        'the troop branch of the walk charges too', WALK,
        """    for entry_id, troop in _safe_entries(project, "troops"):
        if budget is not None and not budget.spend(1):
            return
        if not isinstance(troop, RubyObject):""",
        """    for entry_id, troop in _safe_entries(project, "troops"):
        if not isinstance(troop, RubyObject):""",
        [SPENDS + "test_a_troops_array_of_non_troops_is_charged_for"],
        'round 7 charged per event and per page slot in the map branch and left the other two charging len(commands), which is zero for a slot that is not a page',
    ),
    Guard(
        'a troop page slot is charged for', WALK,
        """        for page_index, page in enumerate(_seq(troop.get("pages"))):
            if budget is not None and not budget.spend(1):
                return
            if isinstance(page, RubyObject):""",
        """        for page_index, page in enumerate(_seq(troop.get("pages"))):
            if isinstance(page, RubyObject):""",
        [SPENDS + "test_a_troop_pages_list_of_non_pages_is_charged_for"],
        'the troop branch is the one that costs a product: a Troops array of a million links to one troop whose @pages is a million integers is four megabytes on disk and 10^12 iterations, with spend never reached',
    ),
    Guard(
        'a common event slot is charged for', WALK,
        """    for entry_id, common in _safe_entries(project, "common_events"):
        if budget is not None and not budget.spend(1):
            return
        if isinstance(common, RubyObject):""",
        """    for entry_id, common in _safe_entries(project, "common_events"):
        if isinstance(common, RubyObject):""",
        [SPENDS + "test_a_common_events_array_of_non_events_is_charged_for"],
        'linear in file size rather than a product, so cheaper -- but a row that is not an RPG::CommonEvent still cost nothing',
    ),
    Guard(
        'the summary counts a page slot it skips', MAP_READ,
        """            rows += 1
            if not isinstance(page, RubyObject):
                continue""",
        """            if not isinstance(page, RubyObject):
                continue
            rows += 1""",
        [SLOTS + "test_the_map_summary_counts_a_slot_it_skips"],
        'round 7 made every appended row count and not every examined slot, so the ceiling was reached only through the outer per-event charge and the inner loop ran to completion once per event',
    ),
    Guard(
        'the summary counts an event slot it skips', MAP_READ,
        """        rows += 1
        if not isinstance(event, RubyObject):
            continue""",
        """        if not isinstance(event, RubyObject):
            continue
        rows += 1""",
        [SLOTS + "test_the_map_summary_counts_an_event_slot_it_skips"],
        'the same defect one level up: a hash of a million integers where events belong was walked free',
    ),
    Guard(
        "event_get charges for a page slot before both skips", EVENT_TOOLS,
        """            if not command_budget.spend(1):
                break
            if only_page is not None and index != only_page:
                continue""",
        """            if only_page is not None and index != only_page:
                continue""",
        [SLOTS + "test_the_event_page_list_charges_for_a_slot_it_skips",
         SLOTS + "test_naming_one_page_does_not_make_the_rest_free"],
        'naming `page` made it worse rather than better: the continue for every other index sat above the only charge on the path',
    ),
    Guard(
        'a summary cut short on its last event says so', MAP_READ,
        """                cut_short = True
                break""",
        """                break""",
        [EVERYPATH + "test_a_single_event_over_the_ceiling_is_reported_as_cut_short",
         EVERYPATH + "test_the_reply_carries_the_note"],
        'the pages loop breaks on the ceiling, then the row is appended and the outer loop ends normally at return out, False -- so one event with more pages than the ceiling came back short with no events_truncated at all',
    ),
    Guard(
        'a summary row says how many pages there are', MAP_READ,
        '            "page_count": len(pages),',
        '',
        [EVERYPATH + "test_a_row_says_how_many_pages_the_event_really_has"],
        'a short page list that does not say it is short is read as complete; event_get carries this field for exactly that reason',
    ),
    Guard(
        'a listed name is capped like every other name', DB_TOOLS,
        """        name = decoded(ctx, entry.get("name")) if isinstance(
            entry, RubyObject) else """"",
        """        name = ""
        if isinstance(entry, RubyObject):
            raw_name = entry.get("name")
            if isinstance(raw_name, RubyString):
                name = ctx.codec.decode(raw_name.data)""",
        [LISTEDNAME + "test_a_long_entry_name_is_capped_in_the_row",
         LISTEDNAME + "test_an_escape_in_a_name_is_sanitised"],
        'the fifth bare copy of this decode, on the one field of the row that is always present, seven lines from the _brief_field that goes through decoded and says why: a listing repeats one crafted name into every row',
    ),
    Guard(
        'a path cannot be unboundedly long', RAW_TOOLS,
        '    if len(path) > MAX_PATH_SEGMENTS:',
        '    if False:',
        [RAWPATH + "test_a_path_with_too_many_segments_is_refused"],
        'a hash may legally contain itself -- _read_hash_pairs registers it before reading its pairs -- and each hash segment costs three linear scans, so "events/" + "1/" * 4,000,000 fits inside MAX_MESSAGE_CHARS and asks for 10^11 comparisons',
    ),
    Guard(
        'every scope gets the same allowance', LINT_REPORT,
        """    walk_budget = WalkBudget()
    if "maps" in scopes:""",
        """    if "maps" in scopes:""",
        [SCOPEALLOW + "test_the_maps_scope_says_it_stopped"],
        'this was built between two of the four scopes, so the map tree, the map properties, the encounters, the tilesets and the database cross-references walked with no allowance at all -- every one of them a row-times-child product out of a file',
    ),
    Guard(
        'a tileset sheet slot is charged for', LINT_DATABASE,
        """            if budget is not None and not budget.spend(1):
                break
            sheet = (TILESET_SHEET_NAMES[index]""",
        """            sheet = (TILESET_SHEET_NAMES[index]""",
        [SCOPEALLOW + "test_the_maps_scope_is_given_an_allowance"],
        '_AssetChecker.check returns before it counts anything when the name is empty, so checker.full never became true and the row-times-slot product had no bound at all',
    ),
    Guard(
        'a parent chain cannot collect past the ceiling', LINT_MAPS,
        """            if not findings.room:
                break
            if budget is not None and not budget.spend(1):
                break
            if looped:""",
        """            if looped:""",
        [SCOPEALLOW + "test_a_parent_chain_stops_walking_as_well_as_collecting"],
        'the ceiling was on the outer `for start in infos`, so one MapInfos that is a single cycle through every entry reached this loop once and appended a finding per entry',
    ),
    Guard(
        'the map tree collects into one bounded list', LINT_MAPS,
        '    _parent_cycles(infos, findings, budget)\n    return findings.items',
        '    return findings.items + _parent_cycles(infos, None, budget)',
        [SCOPEALLOW + "test_a_parent_chain_cannot_collect_past_the_ceiling"],
        'each pass bounded a list of its own and the three were then concatenated, so a ceiling of MAX_FINDINGS produced up to three times that -- the fourth time a ceiling here was written against the wrong list',
    ),
    Guard(
        'every id in the compiler refuses a coercible value', COMMANDS,
        """            operand_type, operand = 1, _needs_int(cond["from_variable"],
                                                  "from_variable")""",
        """            operand_type, operand = 1, int(cond["from_variable"])""",
        [DSLIDS + "test_a_condition_operand_variable_is_not_coerced",
         DSLIDS + "test_every_id_in_the_compiler_goes_through_the_checker"],
        'in the same return statement as a _needs_int call: {"variable": 3, "from_variable": 2.9} compared against variable 2 and answered "rewrote page 0 of event 1"',
    ),
    Guard(
        'a transfer destination refuses a coercible value', COMMANDS,
        """        _needs_int(t["map_id"], "transfer.map_id"),""",
        """        int(t["map_id"]),""",
        [DSLIDS + "test_a_transfer_destination_map_is_not_coerced",
         DSLIDS + "test_every_id_in_the_compiler_goes_through_the_checker"],
        'a Transfer Player destination is what the command is, and int(2.9) is 2, so a wrong-typed coordinate moved the player one tile from where the caller said and the reply reported success',
    ),
    Guard(
        'a crafted common event is reported, not crashed', EVENT_TOOLS,
        '        raise ToolError("there is no common event %d" % common_id)\n    if not isinstance(common, RubyObject):',
        '        raise ToolError("there is no common event %d" % common_id)\n    if False:',
        [CRAFTEDCOMMON + "test_a_string_in_a_common_event_slot_is_reported",
         CRAFTEDCOMMON + "test_an_array_in_a_common_event_slot_is_reported"],
        'the read path called .get("list") straight away, so a file holding an Array in that slot raised AttributeError -- reported with the hint "an argument was not the shape the tool expected", a diagnosis about the caller for a fact about the file',
    ),
    Guard(
        'a tile id the layer cannot hold is refused', MAP_COMMON,
        '    if not 0 <= tile_id <= ceiling:',
        '    if False:',
        [MAPW + "test_a_tile_value_the_layer_cannot_hold_is_refused",
         MAPW + "test_a_region_id_beyond_the_layer_is_refused"],
        'Table.set masks to 16 bits, so an out-of-range id was silently stored as a different tile and then reported back as the value the caller asked for -- and layer 3 holds region ids the editor numbers 0 to 63, so 2816 is a plausible value from a tileset that reads as a region',
    ),
    Guard(
        'an unreadable alphabet is a refusal', SAFETY,
        '    if depth > MAX_ALPHABET_DEPTH:',
        '    if False:',
        [ALPHABET + "test_a_pattern_too_deep_to_read_is_refused"],
        'past the cap this returned an empty alphabet, and an empty alphabet is not "no characters" but "could not tell" -- so every subject became a run of a, and a nested pattern measured at 0.0001s against a subject it cannot fail on while taking 3.9s per 2,000-digit string in the server, uninterruptibly',
    ),
    Guard(
        'a finding names the command that is wrong', LINT_COMMANDS,
        '        "remove the jump." % target, location.at(index))]',
        '        "remove the jump." % target, location.at(0))]',
        [RIGHTCMD + "test_a_missing_label_names_index_three"],
        'each rule takes the command list location and copies it where it reports, so an index passed wrongly sends a reader to the wrong command -- and the only test of it used a one-command page, which any index satisfies',
    ),
    Guard(
        'one listing of the map files, in both its forms', PROJECT,
        ['    def _forget_map_ids(self) -> None:',
         '        self._map_ids_set = frozenset(self._map_ids)'],
        ['    def _forget_map_ids_unused(self) -> None:',
         '        pass  # the set is never rebuilt'],
        [ONETRUTH + "test_a_restore_leaves_them_agreeing"],
        'the list and the set are two forms of one fact, held twice over: `_forget_map_ids` drops both, and `map_ids_on_disk` rebuilds the set whenever it rebuilds the list. Reverting either alone proves nothing, which is what a single-anchor version of this guard reported as VACUOUS -- so both are reverted, and then a membership test answers from before the last change to the folder',
    ),
]
