"""Every guard in this codebase: the edit that removes it, and its tests.

The `Guard` record and every instance of it: append-only, one entry per
fix, so new entries go at the end of `GUARDS`, after the last banner.
`prove_guards.py` next door is the engine that reads them. The two are read
for different reasons -- a verdict you do not believe sends you to the
engine -- so the table does not sit in front of it.

`Guard` itself is here rather than there because it is the shape of the
data, not part of the machine: it holds the fields and refuses a
mismatched anchor pair at import time, and the engine imports it from
here along with the list.

The aliases are here rather than there because they are part of the data:
Aliases such as `LINT_ASSETS` and `BOUNDED` mean nothing to the engine,
which takes the strings they hold as opaque.

`tests/test_prove_guards.py` checks every entry: the named tests must exist
and be collectable, no two guards may share an anchor, no two aliases may
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
STAT_OUTLIERS = _SRC + "analysis/stat_outliers.py"
SWITCH_USAGE = _SRC + "analysis/switch_usage.py"
WALK = _SRC + "analysis/walk.py"


RENDER_PNG = _SRC + "render/png.py"
RENDER_SCENE = _SRC + "render/scene.py"
RENDER_TILES = _SRC + "render/tiles.py"


AUTOTILE = _SRC + "autotile.py"
BACKUPS = _SRC + "backups.py"
COMMANDS = _SRC + "commands.py"
CONVERT = _SRC + "convert.py"
DOCS = _SRC + "docs.py"
CODEC = _SRC + "marshal_rb.py"
PROJECT = _SRC + "project.py"
RUBYTYPES = _SRC + "rubytypes.py"
SCHEMA = _SRC + "schema.py"
SERVER = _SRC + "server.py"
MAIN = _SRC + "__main__.py"


LIVE_BRIDGE = _SRC + "live/bridge.py"
LIVE_SESSION = _SRC + "live/session.py"


COMMAND_DOCS = _SRC + "tools/command_docs_tools.py"
DB_TOOLS = _SRC + "tools/db_tools.py"
EVENT_TOOLS = _SRC + "tools/event_tools.py"
LIVE_TOOLS = _SRC + "tools/live_tools.py"
MAP_COMMON = _SRC + "tools/map_common.py"
MAP_EDIT = _SRC + "tools/map_edit_tools.py"
MAP_PAINT = _SRC + "tools/map_paint_tools.py"
MAP_READ = _SRC + "tools/map_read_tools.py"
MAP_RENDER = _SRC + "tools/map_render_tools.py"
MAP_TILE = _SRC + "tools/map_tile_tools.py"
MISC_TOOLS = _SRC + "tools/misc_tools.py"
SAFETY = _SRC + "tools/pattern_safety.py"
RAW_TOOLS = _SRC + "tools/raw_tools.py"
REGISTRY = _SRC + "tools/registry.py"
SEARCH_TOOLS = _SRC + "tools/search_tools.py"


class Guard:
    """One guard, the edit that removes it, and the test that must then fail."""

    def __init__(self, name: str, file: str, present: str | list[str],
                 reverted: str | list[str], tests: list[str], why: str):
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


# Every test class a guard names, grouped by the file it lives in. A group
# is headed `# test_<file>`, so the per-file census is visible by eye and a
# future split of `tests/` is a contiguous edit.


def _at(module: str, cls: str) -> str:
    """One test class, as `prove_guards` names it."""
    return "tests/%s.py::%s::" % (module, cls)


# test_event_dsl
DSLREAL = _at("test_event_dsl", "TestFormsMatchEditorData")
ROUTELINES = _at("test_event_dsl", "TestMoveRouteLines")
DSLREFUSE = _at("test_event_dsl", "TestRefusals")
DSLWRONG = _at("test_event_dsl", "TestACallersWrongInputIsARefusalNotACrash")
DSLSPECS = _at("test_event_dsl", "TestTheSpecsSayWhatTheFormsWrite")
DSLVX = _at("test_event_dsl", "TestVXLayouts")
XPCMD = _at("test_event_dsl", "TestXPCommands")


# test_guards_arguments
DSLCHOICE = _at("test_guards_arguments", "TestAChoiceBranchIsNeverDropped")
CONVNAME = _at("test_convert_input", "TestANameWithATrailingNewlineIsRefused")
HOSTILEFILE = _at("test_hostile_input", "TestAFileCannotMakeAnUnboundedListOfWarnings")
HOSTILEW = _at("test_hostile_input", "TestTheWriterRefusesWhatTheReaderWould")
HOSTILER = _at("test_hostile_input", "TestARegexpOptionIsAByte")
HOSTILEN = _at("test_hostile_input", "TestANameThatIsAHugeIntegerIsDescribed")
HOSTILEK = _at("test_hostile_input", "TestAReplacementHashWithAnArrayKey")
HOSTILESP = _at("test_hostile_input", "TestASpaceInANameIsKept")
HOSTILECOL = _at("test_hostile_input", "TestHashKeysThatCollideDoNotMakeALookupQuadratic")
HOSTILEWF = _at("test_hostile_input", "TestAWriteThatFailsIsAProjectError")
HOSTILEW6 = _at("test_hostile_input", "TestASchemaWalkDoesNotCopyForEveryLink")
HOSTILELOG = _at("test_hostile_input", "TestAHostileChangeLog")
HOSTILESNAP = _at("test_hostile_input", "TestTwoSnapshotsInOneSecondAreTwoSnapshots")
HOSTILEMSG = _at("test_hostile_input", "TestAMessageWithAHugeIntegerDoesNotEndTheSession")
HOSTILENAME = _at("test_hostile_input", "TestANameWithATrailingNewlineIsNotAComponent")
HOSTILEDOC = _at("test_hostile_input", "TestTheWorkflowGuideDoesNotDenyTheLiveTool")
HOSTILETALLY = _at("test_hostile_input", "TestABignumIdIsNotATallyKey")
HOSTILEQ = _at("test_hostile_input", "TestAnyCommonEventCallIsNotQuadratic")
HOSTILEBIG = _at("test_hostile_input", "TestAnEnormousIdInAScriptIsUnresolvedNotAnError")
HOSTILECE = _at("test_hostile_input", "TestAnEnormousCommonEventIdIsAnyNotAnError")
HOSTILESTAT = _at("test_hostile_input", "TestAStatisticOverHugeNumbersIsSkippedNotAnError")
HOSTILERTP = _at("test_hostile_input", "TestAnRtpNameFromGameIniIsShort")
HOSTILEBIGID = _at("test_hostile_input", "TestAnIdTooBigToBeAnIdIsNotAnId")
HOSTILEVAL = _at("test_hostile_input", "TestValidateSurvivesBignumIdsInAFile")
HOSTILEINI = _at("test_hostile_input", "TestGameIniCannotPointTheScriptPackOutsideTheCopy")
HOSTILEREPLY = _at("test_hostile_input", "TestAReplyFromTheGameCannotExpandWithoutLimit")
HOSTILEBOMB = _at("test_hostile_input", "TestAScriptSlotBombIsRefused")
HOSTILEMARK = _at("test_hostile_input", "TestTheSessionIgnoresAMarkerItCannotRead")
HOSTILESHAPE = _at("test_hostile_input", "TestATileWhoseShapeTheTableLacksIsNotDrawn")
HOSTILETRIG = _at("test_hostile_input", "TestAPageTriggerThatIsNotANumberDoesNotBreakTheRead")
HOSTILEGATE = _at("test_hostile_input", "TestTheRegexGateSeesWhatANegatedClassMatches")
HOSTILEKIND = _at("test_hostile_input", "TestAPaintKindIsAShortAsciiSpelling")
HOSTILEAPP = _at("test_hostile_input", "TestAppendingToADamagedCommandListIsARefusal")
HOSTILESCOPE = _at("test_hostile_input", "TestAScopeListIsNotRunOnceForEachRepeat")
HOSTILELIVE = _at("test_hostile_input", "TestTheLiveToolChecksWhatItForwards")
HOSTILEMAPEDIT = _at("test_hostile_input", "TestMapEditAgreesWithTheTree")
HOSTILETEXTS = _at("test_hostile_input", "TestOneCommandDecodesABoundedNumberOfTexts")
HOSTILEMAPLIST = _at("test_hostile_input", "TestMapListSurvivesAHostileTree")
HOSTILEMAPGET = _at("test_hostile_input", "TestMapGetSurvivesHostileEventFields")
HOSTILERENDER = _at("test_hostile_input", "TestMapRenderIsBoundedAndStaysInItsFolder")
HOSTILEBACKUPS = _at("test_hostile_input", "TestTheBackupsToolIsBoundedAndExplicit")
HOSTILEINFO = _at("test_hostile_input", "TestProjectInfoRefreshRereadsTheScripts")
HOSTILERAW = _at("test_hostile_input", "TestRawDataRefusesBeforeItLoadsAndKeepsItsShapeGuard")
VOCABTILES = _at("test_guards_replies", "TestOneVocabularyForAPartialAnswer")
HOSTILERE = _at("test_hostile_input", "TestTheRereviewOfTheScrubFixes")
# test_render_overlays
OVERSTACK = _at("test_render_overlays", "TestStackedLayers")
OVERTABLE = _at("test_render_overlays", "TestIdsTheTableDoesNotHold")
OVERTAGS = _at("test_render_overlays", "TestTerrainTags")
OVEREVENTS = _at("test_render_overlays", "TestEventsThatBlock")
OVERPASS = _at("test_render_overlays", "TestPassabilityIsDrawn")
OVERDIGITS = _at("test_render_overlays", "TestTerrainTagsAreDrawn")
OVERLABELS = _at("test_render_overlays", "TestEventIdsAreDrawn")
OVERTOOL = _at("test_render_overlays", "TestTheToolDrawsOverlays")
OVERARGS = _at("test_render_overlays", "TestOverlayArgumentsAreHeld")
HOSTILEZ = _at("test_hostile_input", "TestAFloatKeepsItsSign")
HOSTILEID = _at("test_hostile_input", "TestAnEntryIdBelowZeroIsRefused")
CONVBYTES = _at("test_convert_input", "TestAStringThatDoesNotComeBackTravelsAsBytes")
CONVSIZE = _at("test_convert_input", "TestASharedStringIsChargedBySize")
CONVSTRICT = _at("test_convert_input", "TestACallersTextIsEncodedStrictly")
CONVWHOLE = _at("test_convert_input", "TestANumberTheCallerSendsIsAWholeNumber")
CONVDEEP = _at("test_convert_input", "TestAValueNestedTooDeeplyIsRefused")
CONVMARK = _at("test_convert_input", "TestAMarkerHasTheShapeItPromises")
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
PROTOCOL = _at("test_guards_patterns",
               "TestTheChildRefusesAStdinItCannotParse")
CLOSED = _at("test_guards_patterns", "TestTheShapeGateFailsClosed")
SHAPECLOSED = _at(
    "test_guards_patterns", "TestTheShapeGateFailsClosedThroughout")
SPELLED = _at(
    "test_guards_patterns",
    "TestTheShapeScoreDoesNotDependOnHowARepeatIsSpelled")
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
MAPPASS = _at("test_guards_walk_budget",
              "TestOneMapPassPerScopeThatNeedsOne")
BUDGET = _at(
    "test_guards_walk_budget", "TestReadingManyEntriesSharesOneBudget")
SEARCHWORK = _at("test_guards_walk_budget", "TestSearchBoundsItsTotalWork")
AMPLIFY = _at(
    "test_guards_walk_budget", "TestSharedChildrenCannotAmplifyAWalk")
ONETRUTH = _at("test_guards_walk_budget", "TestTheMapListingHasOneTruth")
MERGEDPAGES = _at("test_guards_walk_budget",
                  "TestTheMergedPageWalkStillFindsTheMapAssets")


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


# test_map_paint
PAINTCORRIDOR = _at("test_map_paint", "TestACorridor")
PAINTDUNGEON = _at("test_map_paint", "TestADungeon")
PAINTROOM = _at("test_map_paint", "TestARoom")
PAINTFILL = _at("test_map_paint", "TestFillingARectangle")
PAINTVX = _at("test_map_paint", "TestOnVX")
PAINTXP = _at("test_map_paint", "TestOnXP")
PAINTREFUSE = _at("test_map_paint", "TestRefusals")
PAINTRESHAPE = _at("test_map_paint", "TestReshapingAnEdit")
PAINTSHADOW = _at("test_map_paint", "TestTheEditorsShadow")
PAINTFLOOR = _at("test_map_paint", "TestTheFloorShapeFromNeighbours")
PAINTWALL = _at("test_map_paint", "TestTheWallShapeFromNeighbours")


# test_map_render
RENDEROPT = _at("test_map_render", "TestOptions")
RENDERSIZE = _at("test_map_render", "TestWindowAndScale")
RENDERSHEET = _at("test_map_render", "TestARender")
RENDERVX = _at("test_map_render", "TestVX")
RENDERIMAGE = _at("test_map_render", "TestThePictureComesBackAsAnImage")


# test_live
LIVEDRIVE = _at("test_live", "TestTheGameIsDriven")
LIVEEVAL = _at("test_live", "TestEvalNeedsTheOperatorAndTheSession")
LIVEREADONLY = _at("test_live", "TestReadOnlyRefusesALaunch")
LIVESTART = _at("test_live", "TestASessionStartsAndStops")
LIVETEXT = _at("test_live", "TestTheBridgeText")
LIVEWAIT = _at("test_live", "TestTheServerNeverWaitsForever")


# test_marshal
T = _at("test_marshal", "TestHostileInput")
L = _at("test_marshal", "TestLosslessEdgeCases")


# test_protocol
HARD = _at("test_protocol", "TestTransportHardening")


# test_render_png
PNGHOSTILE = _at("test_render_png", "TestHostileInput")
PNGFILTERS = _at("test_render_png", "TestDecodingEveryFilter")


# test_render_scene
SCENECHARS = _at("test_render_scene", "TestCharacters")
SCENELAYERS = _at("test_render_scene", "TestTheTileLayers")
SCENEREGION = _at("test_render_scene", "TestShadowsAndRegions")


# test_render_tiles
TILEPLACE = _at("test_render_tiles", "TestWhereATileIsOnItsSheet")


# test_schema
PROF = _at("test_schema", "TestEngineProfiles")
EDBYTES = _at("test_schema", "TestNewObjectsAreTheEditorsBytes")
SAFE = _at("test_schema", "TestResponseTextSafety")
OBS = _at("test_schema", "TestSchemaObservation")
PROV = _at("test_schema", "TestSchemaReportProvenance")
DOCSAGREE = _at("test_schema", "TestTheReferenceAgreesWithTheTables")
LAYERMODEL = _at("test_schema", "TestTheTileLayerModel")
XPPROF = _at("test_schema", "TestTheXPProfile")
UNMOD = _at("test_schema", "TestUnmodelledEngine")


# test_stat_outliers
STATSXP = _at("test_stat_outliers", "TestStatOutliersOnXP")


# test_tools
XPREF = _at("test_tools", "TestCommandReferenceXP")
DATABASE = _at("test_tools", "TestDatabase")
DETECT = _at("test_tools", "TestEngineDetection")
EVENTSXP = _at("test_tools", "TestEventsXP")
MAPSXP = _at("test_tools", "TestMapsXP")
XPTOOL = _at("test_tools", "TestXPProject")


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
                self.warn(
                    "symbol :%s carries an encoding record in a name position; "
                    "that record cannot be reproduced when writing this file "
                    "back" % str(symbol)[:MAX_WARNED_NAME]
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
            self.warn(
                "Fixnum %d at byte %d is outside the range Ruby writes as a "
                "Fixnum; it will be written back as a Bignum" % (value, self.pos)
            )
""",
        "",
        [L + "test_out_of_range_fixnum_is_reported"],
        "the value comes back out as a different Marshal type with nothing "
        "said about it",
    ),

    # -- engine profiles, schema observation, response safety -------------
    Guard(
        "schema walk identity memo", SCHEMA,
        """        seen.add(id(value))""",
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
                   or unicodedata.category(char) == "Zs"
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
        '        "tile_id_ranges_note": ctx.project.profile.tile_id_ranges_note,',
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
        "        pass  # check removed",
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
        ['            safe_component(name, "file name")',
         '            self.path_for(name)'],
        ['            pass  # check removed',
         '            pass  # destination check removed'],
        [RESTORE + "test_a_bad_name_in_the_snapshot_stops_before_anything_is_copied"],
        "a name is rejected inside the copy loop, after the safety snapshot "
        "and after some files have already been written. Two anchors, because "
        "round 10d added the destination check beside the source one and the "
        "property became doubly held -- reverting either alone left the other, "
        "which is what the single-anchor version reported as VACUOUS",
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
        "        checker.check(sheet_name, \"tilesets\",\n"
        "                          _db_at(\"tilesets\", entry_id, \"tileset_names\"),\n"
        "                          context=lambda: \"%s %s (%s) sheet %s\"\n"
        "                          % (TABLE_NOUNS[\"tilesets\"], entry_id, name, sheet))",
        # The whole call, not its first line: a revert that kept the lines
        # after it would not be valid Python, and the guard would be proven by
        # a file that does not import.
        "        pass  # asset check removed",
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
        "        if False:\n"
        "            return None",
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
        '    _check_tile(ctx, tile_id, rect.z, "tile_id", rect.view)',
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
        "    _refuse_structural(patch)",
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
        '        if not self.whole and length > MAX_SUBJECT_CHARS:\n            self.clipped += 1',
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
        '    return request.kind == ARRAY\n    if False:\n        return True',
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
        '    refuse_invented_in_replacement(',
        '    _ignored = (  # replacement fields unchecked',
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
        "    refuse_inert(\n"
        "        args, (\"event_ids\", \"name_contains\", \"page\", \"offset\"),\n"
        "        \"a common event has no pages and is addressed by its own id, so \"\n"
        "        \"reading one\",\n"
        "        remedy=\"Drop them, or read a map's events with map_id instead.\")",
        "    pass  # inapplicable arguments not refused",
        [READDROP + "test_a_common_event_read_refuses_page_arguments"],
        'event_get common_event_id=N name_contains=X returns the event whether or not it matches, and the reply reads exactly like a positive match',
    ),
    Guard(
        'a code lookup refuses a search', COMMAND_DOCS,
        "        refuse_inert(args, (\"search\",),\n"
        "                     \"a lookup of one code answers for that code alone, so it\",\n"
        "                     remedy=\"Ask for one or the other.\")",
        "        pass  # search not refused",
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
        '            verdict = (\n                "this server\'s safety check on that pattern %s, so it will "',
        '            verdict = None\n            _unused = (\n                "this server\'s safety check on that pattern %s, so it will "',
        [FAILCLOSED + "test_a_child_killed_by_a_signal_is_refused",
         FAILCLOSED + "test_a_child_that_exits_nonzero_silently_is_refused"],
        'a child that dies for any reason but a bad pattern is recorded as a measured pass and cached, so an unmeasured pattern then runs in-process',
    ),
    Guard(
        'a nullable atom does not break a run', SAFETY,
        '        if not atom.nullable and span and not seen.intersects(atom.charset):',
        '        if span and not seen.intersects(atom.charset):',
        [FAST + "test_the_shape_gate_refuses_a_nullable_chain_alone"],
        '"=?~?" repeated twenty-four times scores 2 -- every adjacent pair is literal-disjoint -- and runs over thirty seconds against a line of "=", which is what a stock VX Ace script separator is',
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
        '''        out.update(partial("text_scan",
                           stopped=matcher.note() if matcher else None))''',
        '''        out.update(partial("text_scan", stopped=None))''',
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
        '''            if findings.full:
                return findings.items''',
        '''            if False:
                return findings.items''',
        [CEILWALK + "test_the_page_walk_stops_visiting_at_the_ceiling"],
        'one event object shared across forty thousand slots keeps the walk charging for a finding per slot, long after the ceiling has stopped keeping them',
    ),
    Guard(
        'the page walk does the map and page asset checks', LINT_PAGES,
        ["""        if checker is not None:
            map_assets(checker, map_id, game_map)""",
         """            if checker is not None:
                for index, page in enumerate(pages):
                    page_graphic(checker, map_id, event_id, name,
                                 index, page)"""],
        # `if False:`, not `if checker is None:`: the latter passes None
        # into `map_assets` and raises AttributeError on the events-only
        # run, so the verdict would read PROVEN for a crash rather than
        # for the finding going missing, which the prover cannot tell
        # apart.
        ["""        if False:
            map_assets(checker, map_id, game_map)""",
         """            if False:
                for index, page in enumerate(pages):
                    page_graphic(checker, map_id, event_id, name,
                                 index, page)"""],
        [MERGEDPAGES + "test_a_default_run_reports_both"],
        'the default run gets its map parallaxes, battlebacks, autoplay audio and page character graphics from this walk and nowhere else, and both sites could be turned off with the whole suite green',
    ),
    Guard(
        'the asset checks ride along instead of walking again', LINT_REPORT,
        """        findings.extend(check_assets(
            project, ids, walk_budget, shared_checker,
            commands_done=shared_checker is not None,
            pages_done=shared_checker is not None))""",
        """        findings.extend(check_assets(
            project, ids, walk_budget, shared_checker,
            commands_done=False,
            pages_done=False))""",
        [MAPPASS + "test_adding_the_assets_scope_costs_no_map_pass",
         MAPPASS
         + "test_a_default_run_is_the_maps_pass_the_events_passes_and_no_more"],
        'the default run would open every map twice more, for checks the walks it already makes have done -- five passes over the map set where three are needed, charged to the same allowance',
    ),
    Guard(
        'every ivars dict has its names checked', CONVERT,
        '    return {check_ivar_name(name): from_json(item, codec, depth + 1)',
        '    return {name: from_json(item, codec, depth + 1)',
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
        '            [check_class_name(name) for name in modules],',
        '            list(modules),',
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
        'a bounded repeat compounds its body', SAFETY,
        """    elif inner_freedom > 1:""",
        """    elif False:""",
        [SPELLED
         + "test_the_dangerous_spellings_are_refused_by_the_shape_gate_alone",
         SPELLED + "test_two_spellings_of_one_shape_get_one_verdict"],
        '`(?:a?){24}b` scored 2 and `"a?" * 24 + "b"` scored 16,777,216, which is one pattern written two ways: the bounded branch charged the count range once and multiplied the body in once instead of compounding it, so the cheap gate let through what it refused in the other spelling and only the measurement stood behind it',
    ),
    Guard(
        'a run is built for every character a repeat can match', SAFETY,
        ["""    if len(quantified) > MAX_MEASURED_SUBJECTS:""",
         """    chosen = sorted(quantified
                    | set(ordered[:MAX_SINGLE_CHARACTER_SUBJECTS])) or ["a"]""",
         '    every = "".join(char * run for char in ordered)'],
        # All three, together: the property is held three times over. With
        # only the refusal reverted, the four hundred and eighty
        # full-length subjects time the child out and the pattern is
        # refused anyway; with only the selection reverted, the refusal
        # catches it on the count; and the every-character subject reaches
        # each character too, which is why a guard on that line alone read
        # VACUOUS once the per-character runs existed. Any one of the three
        # says nothing about whether a dangerous character is measured
        # against a run of itself at all.
        ["""    if False:""",
         """    chosen = ordered[:MAX_SINGLE_CHARACTER_SUBJECTS] or ["a"]""",
         '    every = "".join(char * run for char in ordered[:0])'],
        [FAST + "test_every_costly_pattern_is_refused",
         SUBJECTS + "test_a_hidden_character_still_reaches_a_subject",
         SUBJECTS + "test_every_character_of_a_wide_alphabet_reaches_one"],
        'the run each character got was PREFLIGHT_SUBJECT_CHARS divided by the alphabet, so four hundred and eighty non-letter literals in front of [a-z]+[a-z]+! left the longest measured letter run at four characters: the gate passed it in 0.063s and the pattern then costs about fifteen seconds per 2,000-letter string, uninterruptibly, for every string in the project',
    ),
    Guard(
        'the child refuses a stdin it cannot parse', SAFETY,
        r'''    "lines = sys.stdin.read().split(chr(10))\n"
    "if len(lines) < 2 or not lines[0]:\n"
    "    sys.exit(%d)\n"''',
        r'''    "lines = sys.stdin.read().split()\n"
    "if False:\n"
    "    sys.exit(%d)\n"''',
        [PROTOCOL + "test_an_empty_pattern_is_refused_and_not_remembered"],
        '`split()` drops an empty field, so an empty pattern line shifted the subjects up: the child compiled the first subject, printed ok, exited 0, and an unmeasured pattern was cached as a measured pass for the life of the process',
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
    return decoded_text(codec, _display_safe(value))""",
        """    if isinstance(value, RubyObject):
        return "<%s>" % value.cls
    return str(value)""",
        [BYTES + "test_one_long_string_parameter_is_capped",
         BYTES + "test_a_long_string_in_a_specced_field_is_capped"],
        'every bound in this project counted nodes and none counted bytes: one command holding a megabyte string was a single step of the walk budget, and the rendered line was the one text with no cap at all',
    ),
    Guard(
        'a command line renders a bounded number of parts', COMMANDS,
        # Two anchors: `_BoundedList` already holds only a prefix of a long
        # Array, so the cap in `rendered` alone is a second line of defence and
        # reverting it by itself reads VACUOUS. The property is that a long
        # choice list costs a bounded amount, and it is held twice.
        ["""    return "%s: %s" % (name, rendered(as_list(p(0)),
                                      lambda c: _text(c, codec),
                                      separator=" / "))""",
         """        super().__init__(value[:MAX_PARAM_ITEMS])"""],
        ["""    return "%s: %s" % (name, " / ".join(
        _text(c, codec) for c in as_list(p(0))))""",
         """        super().__init__(value)"""],
        [BYTES + "test_a_long_choice_list_is_capped"],
        'Show Choices renders every choice and the list length comes out of the file, not from MAX_CHOICES which bounds authoring: an Array of a million links to one string is three megabytes on disk',
    ),
    Guard(
        'the cancel kind is an id like every other', COMMANDS,
        ["""    cancel = _needs_int(entry.get("cancel", 5 if cancel_branch is not None
                                  else 0), "cancel")""",
         """    if not 0 <= cancel <= MAX_CANCEL_KIND:"""],
        # Two anchors: the coercion refusal and the range refusal are
        # different properties over one argument, and each of the two tests
        # named below fails for only one of them.
        ["""    cancel = int(entry.get("cancel", 5 if cancel_branch is not None
                                  else 0))""",
         """    if False:"""],
        [DSLCHOICE + "test_a_coercible_cancel_is_refused",
         DSLCHOICE + "test_a_cancel_outside_the_engines_range_is_refused"],
        'the one caller-supplied number in the compilers still coerced: int(2.9) is 2 and int(True) is 1, so a wrong-typed cancel selected a different choice than the caller named and the reply reported the page rewritten -- and 99 wrote a value the interpreter has no branch for, against a comment that had stated the range all along',
    ),
    Guard(
        'a rendered destination reads its designation', COMMANDS,
        ["""    if p(0) == DESIGNATION_VARIABLE:
        return ("%s: map from variable %s at (variable %s, variable %s)"
                % (name, p(TRANSFER_MAP), p(TRANSFER_X), p(TRANSFER_Y)))""",
         """    if p(0) == DESIGNATION_VARIABLE:
        return "%s: troop from variable %s" % (name, p(1))"""],
        # Two anchors, one per renderer: each test below fails for only its
        # own command, and the property is that neither reads a number
        # without reading what the number is.
        ["""    if False:
        return ("%s: map from variable %s at (variable %s, variable %s)"
                % (name, p(TRANSFER_MAP), p(TRANSFER_X), p(TRANSFER_Y)))""",
         """    if False:
        return "%s: troop from variable %s" % (name, p(1))"""],
        [BYTES + "test_a_variable_designated_transfer_says_so",
         BYTES + "test_a_variable_designated_battle_says_so"],
        'designation 1 means the numbers that follow are variable ids, which `references.REFERENCE_RULES` already pins with requires {0: 0} so the lint does not check one against the map list -- while the renderer read them as a destination either way and answered "map 3 at (5, 6)" for a transfer that goes wherever three variables say',
    ),
    Guard(
        'an unmodelled condition renders a bounded line', COMMANDS,
        '''    return "condition type %s %s%s" % (
        brief(kind), brief(rest), " and %d more" % more if more > 0 else "")''',
        '''    return "condition type %s %s" % (kind, params[1:])''',
        [BYTES + "test_an_unknown_condition_kind_is_capped"],
        'the kind selects this branch and comes out of the file, and `%s` on a list reprs every element with nothing capping either: an Array of a million links to one string is three megabytes on disk and one walk step',
    ),
    Guard(
        'a slot that is not a command renders a bounded line', COMMANDS,
        """            lines.append("%3d| <not an RPG::EventCommand: %s>"
                         % (index, brief(command)))""",
        """            lines.append("%3d| <not an RPG::EventCommand: %r>"
                         % (index, command))""",
        [BYTES + "test_a_command_that_is_not_a_command_is_capped"],
        'this branch exists because `@list` is an Array out of the file whose elements need not be commands, and it printed the element whole -- `%r` on an Array of a million links, once per slot, up to MAX_WALK_STEPS slots',
    ),
    Guard(
        'a command name is built from a coerced code', COMMANDS,
        """    number = as_int(code)
    if number is None:""",
        """    number = code
    if False:""",
        [BYTES + "test_a_command_code_that_is_not_a_number_is_rendered_anyway"],
        'a `@code` holding a Ruby String raised "%d format: a real number is required" and an Array raised "unhashable type" at the lookup, so one crafted command made a whole map unreadable through event_get while validate reported nothing wrong with it',
    ),
    Guard(
        'a command is dispatched on a coerced code', COMMANDS,
        """    raw = command.get("code", CODE_BLANK)
    code = CODE_BLANK if raw is None else as_int(raw)
    if code is None:
        return command_name(raw, engine)""",
        """    raw = command.get("code", CODE_BLANK)
    code = raw or CODE_BLANK""",
        [BYTES + "test_a_command_code_that_is_not_a_number_is_rendered_anyway"],
        'dispatch is equality on an int and `dict.get` raises for an unhashable key rather than missing it, so an Array in the code slot raised TypeError before any renderer was reached',
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
        '''        name = decoded(ctx, entry.get("name")) if isinstance(
            entry, RubyObject) else ""''',
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
        [RAWPATH + "test_a_path_with_too_many_segments_is_refused",
         HOSTILERAW + 'test_a_too_deep_path_does_not_load_the_file'],
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
        """        _needs_int(_required(t, "map_id", "transfer"), "transfer.map_id"),""",
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
        'Table.set masks to 16 bits, so an out-of-range id was silently stored as a different tile and then reported back as the value the caller asked for -- and the editor numbers regions 0 to 63, so a region view value of 64 would be stored in bits RGSS3 reads as a region no editor can paint',
    ),
    Guard(
        'an unreadable alphabet is a refusal', SAFETY,
        ['''    if depth > MAX_ALPHABET_DEPTH:
        # Refused, not returned empty.''',
         '    if depth > MAX_ALPHABET_DEPTH:  # in `_quantified`'],
        # Both, because the refusal is written twice now: `_alphabet` and
        # `_quantified` each stop at the same depth and `_subjects` calls
        # both, so reverting either alone leaves the other refusing and says
        # nothing about whether the property is held at all.
        ['''    if False:
        # Refused, not returned empty.''',
         '    if False:  # in `_quantified`'],
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
    Guard(
        'a PNG that inflates past its header is a bomb', RENDER_PNG,
        '    if len(raw) > expected or decoder.unconsumed_tail:',
        '    if False:',
        [PNGHOSTILE + "test_a_decompression_bomb_is_stopped_at_the_declared_size"],
        'a 2 x 2 image whose deflate stream holds 50 MB of zeros is a few kilobytes on disk; inflating without a ceiling turns one sheet named by a project file into the whole of the server\'s memory',
    ),
    Guard(
        'every PNG chunk is held to its CRC', RENDER_PNG,
        '        if zlib.crc32(kind + payload) & 0xFFFFFFFF != crc:',
        '        if False:',
        [PNGHOSTILE + "test_a_bad_crc"],
        'a flipped byte in a header or palette draws a wrong picture that looks like a rendering bug rather than a damaged file',
    ),
    Guard(
        'a PNG declaring too many pixels is refused unread', RENDER_PNG,
        '    if width * height > MAX_PNG_PIXELS:',
        '    if False:',
        [PNGHOSTILE + "test_huge_dimensions_are_refused_before_allocating"],
        'the header is the only thing a 40-byte file needs to say 8000 x 8000, and the decoder allocates four bytes a pixel for whatever it says',
    ),
    Guard(
        'an interlaced PNG is refused', RENDER_PNG,
        '    if interlace != 0:',
        '    if False:',
        [PNGHOSTILE + "test_interlaced_is_refused"],
        'the rows of an Adam7 image are in seven passes, so reading them as one pass draws noise with no error anywhere',
    ),
    Guard(
        'the Paeth filter predicts from the three neighbours', RENDER_PNG,
        '                row[i] = (row[i] + predictor) & 255',
        '                row[i] = (row[i] + a) & 255',
        [PNGFILTERS + "test_each_filter_type_alone_restores_the_rows"],
        'Paeth is the filter encoders choose most for pixel art, and getting it wrong shifts colours by a few levels in a way no one looks at twice',
    ),
    Guard(
        'which ids come from which sheet', RENDER_TILES,
        '        return "A5", n % 8 * TILE, n // 8 % 16 * TILE',
        '        return "A5", n % 8 * TILE, n // 8 % 8 * TILE',
        [TILEPLACE + "test_plain_tiles_use_the_split_halves_of_b_to_e"],
        'A5 is eight tiles wide and sixteen tall; reading it as eight by eight draws the lower half of the sheet as the upper half and nothing about the picture says so',
    ),
    Guard(
        'A4 alternates floor rows and wall rows', AUTOTILE,
        '    by = int((ty - 10) * 2.5 + (0.5 if wall else 0))',
        '    by = int((ty - 10) * 2.5)',
        [TILEPLACE + "test_where_each_autotile_block_starts"],
        'A4 rows are three tiles tall for a roof top and two for the wall under it, so a wall row starts half a tile later than 2.5 a row says -- one row off puts every building front on the wrong tiles',
    ),
    Guard(
        'a flagged tile is drawn over characters, not under', RENDER_SCENE,
        '                if tile_id == 0 or (self.flag(tile_id) & STAR_FLAG != 0) != above:',
        '                if tile_id == 0 or False != above:',
        [SCENELAYERS + "test_a_flagged_tile_goes_over_a_character"],
        'the star flag is how a roof, a treetop or an archway hides what walks behind it; without it the map shows a character standing on top of the building',
    ),
    Guard(
        'the region id is the byte above the shadow bits', RENDER_SCENE,
        '            region = region_of(grid.at(x, y, META_LAYER))',
        '            region = grid.at(x, y, META_LAYER)',
        [SCENEREGION + "test_the_region_id_is_the_byte_above_the_shadow_bits"],
        'the fourth layer of a VX Ace map holds a region as 256 times its number and shadow as the low four bits; read whole, a map with no regions at all shows a region id for every shadowed tile',
    ),
    Guard(
        'a character lower on the map is drawn over one above it', RENDER_SCENE,
        '        for sprite in sorted(sprites, key=lambda s: (s.y, s.event_id)):',
        '        for sprite in sprites:',
        [SCENECHARS + "test_the_sprite_lower_on_the_map_is_drawn_over_the_one_above"],
        'events are drawn in the order the map hash lists them, which is by id; a tall character then lands under one standing behind it',
    ),
    Guard(
        'a picture over the output ceiling is refused', MAP_RENDER,
        '    if out_w * out_h > MAX_OUTPUT_PIXELS:',
        '    if False:',
        [RENDERSIZE + "test_a_picture_over_the_output_ceiling_is_refused"],
        'the encoder is a Python loop over every row, and tile_size multiplies a 1000 x 1000 map to sixty-four million pixels',
    ),
    Guard(
        'a window over the native ceiling is refused before drawing', MAP_RENDER,
        '    if ww * wh * TILE * TILE > MAX_NATIVE_PIXELS:',
        '    if False:',
        [RENDERSIZE + "test_a_window_over_the_native_ceiling_is_refused_before_drawing"],
        'the canvas is drawn at 32 pixels a tile whatever tile_size is, so a small tile_size alone does not make a big map cheap -- the work is allocated before the output ceiling is looked at',
    ),
    Guard(
        'VX is refused rather than drawn from the wrong layout', MAP_RENDER,
        '    if project.profile.tileset_layout != "ace":',
        '    if False:',
        [RENDERVX + "test_a_vx_project_is_refused_with_the_reason"],
        'VX keeps one global tileset in Graphics/System with another autotile layout, and drawing it with Ace\'s tables gives a confident picture of the wrong tiles',
    ),
    Guard(
        'the render folder keeps a bounded number of pictures', MAP_RENDER,
        '    for stale in kept[MAX_KEPT_RENDERS:]:',
        '    for stale in []:',
        [RENDEROPT + "test_the_render_folder_keeps_only_the_newest"],
        'every edit to a map is a new picture under a new name, so a session of editing and looking fills the folder without bound',
    ),
    Guard(
        'a decoded sheet is keyed by its file\'s modification time', MAP_RENDER,
        '    key = (str(path), info.st_mtime_ns, info.st_size)',
        '    key = (str(path), 0, 0)',
        [RENDERSHEET + "test_an_edited_sheet_is_read_again"],
        'the cache outlives a tool call, so a tileset the artist saved a minute ago would be drawn as it was before until the server restarts',
    ),
    Guard(
        "VX's shop layout is VX's", COMMANDS,
        '    302: ["goods_type", "goods_id", "purchase_only"],',
        '    302: ["goods_type", "goods_id", "price_type", "price"],',
        [DSLVX + "test_vx_shop_spec_is_what_vx_reads",
         DSLSPECS + "test_spec_lengths_match_compiled_lengths"],
        "command_reference told a VX caller that Shop Processing carried a price kind and a price, which are VX Ace's: VX's command_302 reads `shop_purchase_only = @params[2]`, so a 302 written to the served layout put a price kind where VX reads the purchase-only flag",
    ),
    Guard(
        "VX's Show Animation has no wait flag", COMMANDS,
        '    212: ["character_id", "animation_id"],',
        '    212: ["character_id", "animation_id", "wait"],',
        [DSLSPECS + "test_spec_lengths_match_compiled_lengths"],
        "the shared table gave both engines VX Ace's three-parameter Show Animation, so command_reference served VX a parameter its command_212 never reads",
    ),
    Guard(
        "a VX balloon or animation refuses a wait flag", COMMANDS,
        '''        if "wait" in obj:
            raise _engine_lacks("wait flag", key)''',
        '''        if False:
            raise _engine_lacks("wait flag", key)''',
        [DSLVX + "test_vx_animation_and_balloon_have_no_wait_flag"],
        "VX has no wait on Show Balloon Icon or Show Animation, so a wait the caller asked for would be dropped and the page reported as written -- a cutscene that no longer waits, with nothing saying so",
    ),
    Guard(
        "move steps 39 and 40 are transparency", COMMANDS,
        '    38: "Through OFF", 39: "Transparent ON", 40: "Transparent OFF",',
        '    38: "Through OFF", 39: "Always on Top ON", 40: "Always on Top OFF",',
        [ROUTELINES + "test_39_and_40_are_transparency_in_both_engines"],
        "the table carried XP's names for 39 and 40, while both VX and VX Ace's Game_Character set `@transparent` for them -- so event_get rendered a step that hides the character as one that changes its draw priority",
    ),
    Guard(
        "a route's step line is the route's own step", COMMANDS,
        '            steps.append(_cmd(step_code, indent, [move]))',
        '            steps.append(_cmd(step_code, indent, [RubyObject(move.cls, dict(move.ivars))]))',
        [ROUTELINES + "test_each_step_has_a_505_line_sharing_its_object"],
        "every one of the 1,037 step lines in the real projects is a Marshal link to the route's own command; a copy reads the same in the editor but writes a file that is not the one the editor would write",
    ),
    Guard(
        "a layout form refuses a field it does not know", COMMANDS,
        '''    if unknown:
        raise CommandError(
            "%s does not take %s; it takes %s"''',
        '''    if False:
        raise CommandError(
            "%s does not take %s; it takes %s"''',
        [DSLREFUSE + "test_an_unknown_field_inside_a_form_is_refused_not_ignored"],
        'a misspelt field was ignored and its default written: {"bgm": {"naem": "Town1"}} would compile to a BGM with no name -- the editor\'s "(None)", which stops the music -- and report success',
    ),
    Guard(
        "ME and SE put pitch before volume", SCHEMA,
        '    "RPG::ME": ("name", "pitch", "volume"),',
        '    "RPG::ME": ("name", "volume", "pitch"),',
        [DSLREAL + "test_me_has_the_ivar_order_the_editor_writes"],
        "Marshal keeps instance variables in the order it is given them, and the editor writes ME and SE with @pitch before @volume -- the opposite of BGM and BGS -- so one shared order wrote files that load but are not the editor's",
    ),
    Guard(
        'a region is the byte above the shadow bits', SCHEMA,
        '    return word >> REGION_SHIFT',
        '    return word',
        [LAYERMODEL + "test_the_region_is_the_byte_above_the_shadow_bits"],
        'RGSS3 reads a region as data[x, y, 3] >> 8; read whole, every shadowed cell of a map with no regions reports a region, and region 1 reads as 256',
    ),
    Guard(
        'the shadow is the low four bits of layer 3', SCHEMA,
        '    return word & MAX_SHADOW',
        '    return word',
        [LAYERMODEL + "test_the_region_is_the_byte_above_the_shadow_bits"],
        'a cell holding region 1 is the word 256; read whole as a shadow it is a mask no four-bit field can hold',
    ),
    Guard(
        'a raw layer-3 word may set only the region and shadow bits', MAP_COMMON,
        '            and tile_id & ~META_LAYER_BITS):',
        '            and False):',
        [MAPW + "test_a_raw_word_with_bits_no_field_holds_is_refused"],
        'nothing in RGSS3 reads bits 4-7 or a region above 63, so a value using them is a tile id sent to the wrong layer -- stored, it reads back as a region or a shadow nobody painted',
    ),
    Guard(
        'painting one part of layer 3 keeps the other', MAP_TILE,
        '            if merge is not None:',
        '            if False:',
        [MAPW + "test_painting_a_region_keeps_the_shadow_and_back"],
        'a region and the wall shadow under it share one word; written whole, painting an encounter region erases every shadow it covers, and a region value written raw is a shadow',
    ),
    Guard(
        'the map edge joins a floor', AUTOTILE,
        '        return not layer.inside(nx, ny) or layer.kind(nx, ny) == kind',
        '        return layer.inside(nx, ny) and layer.kind(nx, ny) == kind',
        [PAINTFLOOR + "test_the_map_edge_joins_a_floor"],
        'the editor draws a floor running off the map with no border at the edge; treating the edge as a different kind outlines every map in its own border',
    ),
    Guard(
        'an inner corner shows only between two joined sides', AUTOTILE,
        '        if not (set(beside) & absent) and not joined(dx, dy))',
        '        if not joined(dx, dy))',
        [PAINTFLOOR + "test_a_diagonal_beside_an_open_side_is_ignored"],
        'a diagonal beside an open side is covered by that side\'s border; counting it names a pattern none of the 47 shapes has, and the paint fails on an ordinary corner',
    ),
    Guard(
        'a wall joins sideways only a run of the same rows', AUTOTILE,
        '            if _run(layer, nx, ny, kind, runs) != _run(layer, x, y, kind,',
        '            if False and _run(layer, nx, ny, kind, runs) != _run(layer, x, y, kind,',
        [PAINTWALL + "test_a_wall_stepping_down_beside_another_shows_its_edge"],
        'a wall that steps up or down beside another is drawn as one flat face, hiding the corner the editor shows there',
    ),
    Guard(
        'an A4 face joins the A4 tile beside it', AUTOTILE,
        '        elif not (other is not None and sheet_of(kind) == "A4"',
        '        elif not (False and sheet_of(kind) == "A4"',
        [PAINTWALL + "test_an_a4_face_joins_the_a4_top_beside_it"],
        'every inner corner of a room draws a vertical seam where the face meets the ceiling column beside it, which the editor does not -- 275 of 278 faces beside their own wall top in the real game join it',
    ),
    Guard(
        'a wall opens up and down at the map edge', AUTOTILE,
        '            if dy:\n                bits |= WALL_BITS[name]\n            continue',
        '            if False:\n                bits |= WALL_BITS[name]\n            continue',
        [PAINTWALL + "test_the_map_edge_joins_sideways_and_opens_up_and_down"],
        'a wall face at the top or bottom of the map loses its border there, unlike every one the editor stores',
    ),
    Guard(
        'the neighbourhood of an edit follows a wall run beside it', AUTOTILE,
        '                out.update((column, row) for row in range(top, bottom + 1))',
        '                pass',
        [PAINTRESHAPE + "test_the_neighbourhood_follows_a_wall_run_beside_it"],
        'lengthening a wall changes whether the columns beside it join along their whole height, and only the cells next to the edit would be re-shaped',
    ),
    Guard(
        'the shadow is written into the word, keeping the region', AUTOTILE,
        '        value = with_shadow(word, WALL_SHADOW if high else 0)',
        '        value = WALL_SHADOW if high else 0',
        [PAINTSHADOW + "test_a_region_under_the_shadow_is_kept"],
        'region and shadow share layer 3; written whole, every shadow a wall casts erases the encounter region under it',
    ),
    Guard(
        'a wall casts no shadow on itself', AUTOTILE,
        '        high = (not is_high(ground.get(x, y))',
        '        high = (True',
        [PAINTSHADOW + "test_a_wall_is_not_shaded_by_the_wall_beside_it"],
        'every wall face but the first in a row is shaded by the one before it, a dark stripe the editor never draws (it shades no wall cell in the real game)',
    ),
    Guard(
        'a painted cell\'s neighbours are re-shaped', MAP_PAINT,
        '        around = neighbourhood(self.layer, self.painted)',
        '        around = sorted(self.painted)',
        [PAINTFILL + "test_the_rectangle_and_its_ring_take_their_shapes"],
        'the cells around a painted patch keep the shapes they had, so the patch sits in the ground as a square with no border joining it',
    ),
    Guard(
        're-shaping is bounded', MAP_PAINT,
        '        if len(around) > MAX_RESHAPE_CELLS:',
        '        if False:',
        [PAINTREFUSE + "test_a_re_shape_over_its_ceiling_is_refused_and_undone"],
        'a wall run beside an edit is re-shaped whole, so a small paint next to a map of tall walls walks every row of every column beside it',
    ),
    Guard(
        'a block past the end of its sheet is refused', MAP_PAINT,
        '    if not _is_number(index) or int(index) >= count:',
        '    if not _is_number(index):',
        [PAINTREFUSE + "test_a_block_past_the_end_of_its_sheet_names_the_range"],
        'A2:32 is read as the first A3 kind, so a caller who miscounted the sheet paints a roof where they asked for ground',
    ),
    Guard(
        'a rectangle is bounded by the cell ceiling', MAP_PAINT,
        '    _check_budget(w * h, verb)',
        '    pass',
        [PAINTREFUSE + "test_a_rectangle_over_the_cell_ceiling",
         PAINTDUNGEON + "test_a_dungeon_too_large_is_refused"],
        'fill and dungeon default to the whole map, and a 1000 x 1000 map is a million Python-level writes and re-shapes in one call',
    ),
    Guard(
        'a kind on a sheet the tileset does not name is refused', MAP_PAINT,
        '        if kind.sheet not in named:',
        '        if False:',
        [PAINTREFUSE + "test_a_sheet_the_tileset_does_not_name"],
        'the game draws a cell whose sheet the tileset names no file for as nothing at all, and the write reports success',
    ),
    Guard(
        'map_paint gates its op arguments', MAP_PAINT,
        '    check_op_args(op, args, PAINT_OP_ARGS, "map_paint")',
        '    pass  # gate removed',
        [PAINTREFUSE + "test_the_other_ops_arguments"],
        'a fill carrying a cell list paints the rectangle and never looks at the cells, and reports the paint',
    ),
    Guard(
        'map_paint refuses VX and XP', MAP_PAINT,
        '    _refuse_engine(ctx)\n',
        '    pass\n',
        [PAINTVX + "test_vx_is_refused_with_the_reason",
         PAINTXP + "test_xp_is_refused_with_the_reason"],
        'VX keeps one global tileset with another autotile layout and XP seven autotiles of its own; shapes computed with VX Ace\'s rules are a confident write of the wrong tiles',
    ),
    Guard(
        'a room needs rows above it for its wall', MAP_PAINT,
        '    if y < wall_height:',
        '    if False:',
        [PAINTREFUSE + "test_a_room_with_no_space_above_for_its_wall"],
        'the wall is cut off by the top of the map and the room is reported as built',
    ),
    Guard(
        'a wall face never covers floor', MAP_PAINT,
        '                if above < 0 or self.is_floor(cx, above):',
        '                if above < 0:',
        [PAINTCORRIDOR + "test_a_bend_reaches_both_ends"],
        'the cells above a vertical passage are themselves floor, and a face written over them walls the passage shut',
    ),
    Guard(
        'one seed builds one dungeon', MAP_PAINT,
        '        rng = random.Random(seed)',
        '        rng = random.Random()',
        [PAINTDUNGEON + "test_the_same_seed_builds_the_same_dungeon"],
        'the seed is reported and does not reproduce the map, so a dungeon cannot be rebuilt after the map is edited or lost',
    ),
    Guard(
        'every room of a dungeon is joined to the others', MAP_PAINT,
        '        for a, b in _joins(rooms, width):',
        '        for a, b in _joins(rooms, width)[:1]:',
        [PAINTDUNGEON + "test_every_floor_cell_can_be_reached"],
        'a room with no corridor to it is a room the player cannot reach, in a dungeon reported as generated',
    ),
    Guard(
        'a door in the top wall opens through the face and the ceiling', MAP_PAINT,
        '            builder.add_floor(x + door_at, y - wall_height - 1, 1,',
        '            builder.add_floor(x + door_at, y - 1, 1,',
        [PAINTROOM + "test_a_door_in_the_top_wall"],
        'the gap is cut through the bottom row of the wall only, so the door is a notch in the face that leads into rock',
    ),
    Guard(
        'a render comes back as an image', MAP_RENDER,
        '    return ImageReply(reply, encoded)',
        '    return reply',
        [RENDERIMAGE + "test_the_tool_returns_the_png_beside_its_path",
         RENDERIMAGE + "test_the_server_sends_an_image_block_after_the_text"],
        'a model that can look at a picture was handed a path on a disk it may not share, and had to read the file back to see what it had just drawn',
    ),
    Guard(
        'a render over the inline cap is a path, not an error', MAP_RENDER,
        '    inline = len(encoded) <= MAX_INLINE_IMAGE_BYTES',
        '    inline = True',
        [RENDERIMAGE + "test_a_png_over_the_inline_cap_comes_back_as_a_path_only"],
        'the server refuses a reply whose image passes its budget, so a large render that worked and was written would come back as an error telling the caller to narrow a request that had nothing wrong with it',
    ),
    Guard(
        'a wedged game is killed after one wait, not two', LIVE_SESSION,
        """                self.request("quit", seconds=QUIT_SECONDS)
                self.process.wait(timeout=QUIT_SECONDS)
            except (LiveError, subprocess.TimeoutExpired):
                pass""",
        """                self.request("quit", seconds=QUIT_SECONDS)
            except LiveError:
                pass
            try:
                self.process.wait(timeout=QUIT_SECONDS)
            except subprocess.TimeoutExpired:
                pass""",
        [LIVEWAIT + "test_stop_waits_once_for_a_wedged_game"],
        'a game that did not answer quit is wedged, and stop then spent the quit timeout and the exit timeout back to back before the kill that was always coming -- six seconds on the real engine to end a game already known to be dead',
    ),
    Guard(
        'a dialog while booting is a refusal, not a timeout', LIVE_SESSION,
        """                raise LiveError(
                    "the game stopped while starting%s. It has been ended."
                    % self.dialog_sentence())""",
        "                break",
        [LIVEWAIT + "test_a_dialog_while_booting_is_read_and_not_waited_out"],
        'RGSS shows an error raised while the scripts load in a modal box and then waits for a click forever; the refusal read the dialog but opened with "did not come up within 60 seconds" four seconds in, naming a timeout that had not happened',
    ),
    Guard(
        'eval is refused by the tool unless the session allowed it',
        LIVE_TOOLS,
        '    if not running.options.get("allow_eval"):',
        '    if False:',
        [LIVEDRIVE + "test_eval_is_refused_unless_the_session_allowed_it"],
        'eval runs arbitrary Ruby in a process on the user\'s desktop; without this the only gate is the bridge reading its config, and its refusal does not name the flag that would lift it',
    ),
    Guard(
        'allow_eval at start needs the server flag', LIVE_TOOLS,
        '    if allow_eval and not ctx.project.allow_live_eval:',
        '    if False:',
        [LIVEEVAL + "test_start_with_allow_eval_is_refused_without_the_server_flag"],
        "the model alone turns on arbitrary Ruby in a process on the user's desktop, by asking for allow_eval -- the per-session key was never a key the human held",
    ),
    Guard(
        'eval reads the server flag on every call', LIVE_TOOLS,
        '    if not ctx.project.allow_live_eval:\n        missing.append(_EVAL_NOT_ALLOWED)',
        '    if False:\n        missing.append(_EVAL_NOT_ALLOWED)',
        [LIVEEVAL + "test_eval_checks_the_server_flag_as_well"],
        "the start check alone makes the operator's flag a value copied once into a session; eval must answer to the setting as it is, not as it was when the game came up",
    ),
    Guard(
        'the command line hands --allow-live-eval to the project', MAIN,
        '            allow_live_eval=args.allow_live_eval,\n',
        '',
        [LIVEEVAL + "test_main_hands_the_flag_to_the_project"],
        'a flag that parses and is never passed on reads as set in --help and leaves eval refused for the operator who set it, with no error to say why',
    ),
    Guard(
        'how a session started is not reported as how it is', LIVE_SESSION,
        '"started_with": dict(self.options)}',
        '"options": dict(self.options)}',
        [LIVESTART + "test_how_it_started_is_not_mistaken_for_how_it_is"],
        'status carried options.paused true beside paused false after a resume, which reads as a contradiction about the one fact a deterministic test depends on',
    ),
    Guard(
        'read-only refuses a launch', LIVE_TOOLS,
        '    if ctx.project.read_only:',
        '    if False:',
        [LIVEREADONLY + "test_start_is_refused"],
        '--read-only is how a user says nothing should happen to a project; a live session does not write it, but starting a game on the desktop is not exploring either',
    ),
    Guard(
        'a reset or an exit is not reported as a crash', LIVE_BRIDGE,
        """  def run
    mcp_bridge_run
  rescue RGSSReset, SystemExit
    raise
  rescue Exception => e""",
        """  def run
    mcp_bridge_run
  rescue Exception => e""",
        [LIVETEXT + "test_it_never_rescues_a_reset_or_an_exit"],
        'F12 raises RGSSReset and the quit op raises SystemExit; caught by the crash handler, the first would freeze the game in the crash loop on a keypress and the second would leave stop to kill a game that was trying to exit',
    ),
    Guard(
        'every wait reminds the game it is active', LIVE_SESSION,
        """            nudged = self._nudge(nudged)
            if response_path.exists():""",
        """            if response_path.exists():""",
        [LIVEDRIVE + "test_every_request_tells_the_game_it_is_active"],
        'RGSS3 blocks inside Graphics.update whenever its window is inactive, so the moment the user clicked back to their editor -- or minimised the game -- every request timed out as an unresponsive game; found on the real engine, where WM_ACTIVATEAPP posted to the game window brings it back without taking focus',
    ),

    # -- T13a: created objects carry the editor's ivar order ------------------
    Guard(
        'an event command is written indent first', SCHEMA,
        '        "@indent": indent, "@code": code, "@parameters": list(parameters or []),',
        '        "@code": code, "@indent": indent, "@parameters": list(parameters or []),',
        [EDBYTES + "test_a_blank_command_is_the_editors_bytes",
         EDBYTES + "test_a_new_page_is_the_editors_bytes"],
        'all 13,900 commands in two editor-written projects store @indent before @code; Marshal keeps the order it is given, so every command the server wrote loaded but was not the bytes the editor writes',
    ),
    Guard(
        'a created audio object is put in the editor order', SCHEMA,
        '        return in_editor_order(RubyObject(cls, {',
        '        return (RubyObject(cls, {',
        [EDBYTES + "test_me_and_se_are_the_editors_bytes"],
        'the profile built every audio class as name, volume, pitch; the editor writes ME and SE with @pitch before @volume',
    ),
    Guard(
        'a created map is put in the editor order', SCHEMA,
        '        return in_editor_order(RubyObject("RPG::Map", ivars))',
        '        return RubyObject("RPG::Map", ivars)',
        [EDBYTES + "test_events_map_infos_and_maps_have_the_editors_order"],
        'map_edit create wrote its 24 fields in the order the template listed them, which is not the one order every editor-written map in two projects holds',
    ),
    Guard(
        'a raw object parameter is put in the editor order', COMMANDS,
        '    return in_editor_order(obj)',
        '    return obj',
        [EDBYTES + "test_a_raw_object_parameter_is_put_in_the_editors_order"],
        'a {"__class__": ...} parameter kept the key order the caller typed, so a raw SE typed volume-first was written as no editor writes one',
    ),
    Guard(
        'a blank database entry takes its sibling order', DB_TOOLS,
        '        entry.ivars = ordered_like(entry.ivars, request.sibling_order())',
        '        pass',
        [DATABASE + "test_a_blank_entry_takes_its_siblings_field_order"],
        'a blank create wrote the patch keys in the order typed and @id last, where every sibling in the table holds another order',
    ),

    # -- T13b: VX stores the RPG::BGM family ----------------------------------
    Guard(
        'VX audio is the BGM family', SCHEMA,
        """    audio_classes=dict(BGM_FAMILY_AUDIO),
    # RGSS2 runs Ruby 1.8: strings are bare bytes.""",
        """    audio_classes={"bgm": "RPG::AudioFile", "bgs": "RPG::AudioFile",
                   "me": "RPG::AudioFile", "se": "RPG::AudioFile"},
    # RGSS2 runs Ruby 1.8: strings are bare bytes.""",
        [PROF + "test_vx_audio_is_the_bgm_family"],
        'RGSS2 calls play on every stored audio object and only RPG::BGM, BGS, ME and SE have it; a map created with RPG::AudioFile music is a NoMethodError the moment the player enters it',
    ),
    Guard(
        'VX is offered the audio forms', COMMANDS,
        """        "the editor puts there (VX Ace writes nil)"),
}""",
        """        "the editor puts there (VX Ace writes nil)"),
    "bgm": "audio", "bgs": "audio", "me": "audio", "se": "audio",
}""",
        [DSLVX + "test_vx_audio_forms_write_the_bgm_family"],
        'the four audio forms stayed refused on VX over a class question the RGSS2 runtime settles',
    ),
    Guard(
        'a route se step is offered on VX', COMMANDS,
        '        return [_audio("se", value, enc, what)]',
        '        raise CommandError("RPG Maker VX: the se step is not offered")',
        [DSLVX + "test_vx_audio_forms_write_the_bgm_family"],
        "VX's Game_Character plays a route's SE with command.parameters[0].play, the same as VX Ace's; refusing the step left VX routes without sound for no reason",
    ),

    # -- T13c: RPG Maker XP is modelled ---------------------------------------
    Guard(
        'XP is a profile of its own', SCHEMA,
        '            for profile in (ACE_PROFILE, VX_PROFILE, XP_PROFILE)}',
        '            for profile in (ACE_PROFILE, VX_PROFILE)}',
        [DETECT + "test_xp_opens_without_a_flag_and_is_modelled_as_xp"],
        'an XP project opened only with --allow-other-engines and was described with the VX model: VX\'s page fields, VX\'s command names, a VX map when one was created',
    ),
    Guard(
        'a new map has only this engine\'s documented fields', SCHEMA,
        '                 if key[1:] in documented}',
        '                 if True}',
        [XPPROF + "test_a_new_map_is_xp_s"],
        'map_edit create wrote VX\'s parallax and scroll fields into an XP map, which XP\'s RPG::Map does not have',
    ),
    Guard(
        'a new XP page is XP\'s', SCHEMA,
        '    page_template=new_xp_page,',
        '    page_template=None,',
        [XPPROF + "test_a_new_page_is_xp_s"],
        'event_edit built XP pages with priority_type and an item and actor condition, where XP\'s page has always_on_top and a smaller condition and graphic',
    ),
    Guard(
        'XP commands are named from XP\'s table', COMMANDS,
        '    known = (XP_COMMAND_NAMES if engine == "xp" else COMMAND_NAMES).get(number)',
        '    known = COMMAND_NAMES.get(number)',
        [XPCMD + "test_xp_commands_render_with_xp_s_names",
         EVENTSXP + "test_get_renders_a_readable_script"],
        'XP\'s 202 to 209 mean other things than VX\'s: its fog tone read as Set Move Route and its Set Event Location as Set Vehicle Location',
    ),
    Guard(
        'XP takes only the forms checked against its Interpreter', COMMANDS,
        '    if engine == "xp" and key not in XP_FORMS:',
        '    if False:',
        [XPCMD + "test_the_other_forms_are_refused_and_say_why"],
        'a wait form wrote VX\'s 230, which XP\'s Interpreter does not dispatch, and the audio forms wrote RPG::BGM into an engine that stores RPG::AudioFile',
    ),
    Guard(
        'XP\'s Show Text holds its first line', COMMANDS,
        '''    if engine == "xp":
        return _compile_xp_text(entry, lines, enc, indent)''',
        '''    if False:
        return _compile_xp_text(entry, lines, enc, indent)''',
        [XPCMD + "test_text_is_written_xp_s_way"],
        'XP\'s command_101 shows parameters[0] as the first line; VX\'s layout puts a face name there, so every XP message would open with a blank line and an unused face',
    ),
    Guard(
        'XP\'s Control Variables stops at its operand', COMMANDS,
        '    if engine == "xp" and operand_type != OPERAND_RANDOM:',
        '    if False:',
        [XPCMD + "test_xp_s_control_variables_stops_where_its_operand_does"],
        'every constant or variable Control Variables in XP editor data has five parameters; a sixth zero loads and runs, but is not the file XP\'s editor writes',
    ),
    Guard(
        'XP\'s route lines are 509 after a 209', EVENT_TOOLS,
        '    route_code, step_code = route_codes(engine)',
        '    route_code, step_code = 205, 505',
        [EVENTSXP + "test_move_route_gains_its_editor_step_lines"],
        'XP\'s Set Move Route got no step lines, so the editor shows it empty -- and its 205, a fog tone, would have been read as a route',
    ),
    Guard(
        'XP\'s reference rules are its own', REFERENCES,
        '_ENGINE_INDEXES = {"xp": _RuleIndex(XP_REFERENCE_RULES)}',
        '_ENGINE_INDEXES = {}',
        [XPTOOL + "test_a_battle_names_its_troop_in_xp_s_place"],
        'XP\'s Battle Processing keeps the troop in parameters[0]; VX\'s rule read parameters[1] (the can-escape flag), so a missing troop went unreported and a delete was told nothing referred to it',
    ),
    Guard(
        'XP skips the VX asset rules on commands', LINT_COMMANDS,
        '            if checker is not None and check_assets:',
        '            if checker is not None:',
        [XPTOOL + "test_xp_s_show_text_is_not_read_as_a_face"],
        'XP\'s Show Text holds its message where VX\'s holds a face, so every message was reported as a missing face graphic',
    ),
    Guard(
        'XP\'s Erase Event releases an autorun', LINT_PAGES,
        '    releasing = (_XP_PAGE_RELEASES_CONTROL if project.engine == "xp"',
        '    releasing = (PAGE_RELEASES_CONTROL if project.engine == "xp"',
        [XPTOOL + "test_an_autorun_that_erases_itself_is_not_reported"],
        'XP\'s Erase Event is 116, not 214, so every XP cutscene that ended by erasing itself was reported as trapping the player',
    ),
    Guard(
        'switch usage reads XP\'s commands', SWITCH_USAGE,
        '        self.route_code = route_codes(project.engine)[0]',
        '        self.route_code = 205',
        [XPTOOL + "test_switch_usage_reads_xp_s_commands"],
        'a switch an XP route turns on was reported as read but never set, because the scan looked for routes at VX\'s 205',
    ),
    Guard(
        'XP\'s stats are compared by XP\'s fields', STAT_OUTLIERS,
        '                     "xp": _XP_FIELDS}',
        '                     }',
        [STATSXP + "test_xp_s_own_fields_are_read"],
        'XP fell back to VX\'s allowlist, which has no pdef, mdef, str, dex or int, so most of an XP table\'s stats were never compared',
    ),
    Guard(
        'XP tile ids are named by XP\'s ranges', MAP_READ,
        '                                                ranges or TILE_ID_RANGES)}',
        '                                                TILE_ID_RANGES)}',
        [MAPSXP + "test_get_reports_properties_events_and_layers"],
        'map_get named XP\'s tileset tile 384 as VX\'s sheet C and its autotile 48 as sheet B',
    ),
    Guard(
        'the workflow offers map_paint only where it paints', DOCS,
        '                  if project.profile.tileset_layout == "ace" else "")',
        '                  if project.profile.has_tileset_database else "")',
        [XPTOOL + "test_the_documents_describe_xp"],
        'XP has a Tilesets table too, so an XP project was told to build its maps with map_paint, which refuses XP',
    ),
    Guard(
        'command_reference says XP\'s layouts are its own', COMMAND_DOCS,
        '    modelled = engine == ctx.project.profile.key',
        '    modelled = ctx.project.command_layouts_modelled',
        [XPREF + "test_the_layouts_are_xp_s_own"],
        'every XP lookup carried a warning that the layouts were VX Ace\'s and unconfirmed, beside layouts read from XP\'s own Interpreter',
    ),
    Guard(
        "a command parameter is read through the display-safe wrapper", COMMANDS,
        "            self._safe[index] = _display_safe(self.params[index])",
        "            self._safe[index] = self.params[index]",
        [BYTES + "test_no_parameter_slot_of_any_command_can_stop_the_line"],
        "a command parameter is whatever the file put in its slot: an Array "
        "is unhashable as a table key and as long as its repr is wide, and an "
        "Integer past 4300 digits raises where it is turned into a string, so "
        "one crafted command made event_get, search and validate fail for the "
        "whole map. Every renderer reads its slots through this call, which is "
        "why the fix is here and not at the six sites that were found",
    ),
    Guard(
        "the bridge refuses eval the session did not allow", LIVE_BRIDGE,
        '    unless @config["allow_eval"]',
        "    unless true",
        [LIVETEXT + "test_eval_is_refused_in_ruby_unless_the_session_allowed_it"],
        "the request file is plain data in a folder the game polls, so a request "
        "written by hand never passes through the tool's two gates: without "
        "this check the game evaluates whatever Ruby the file holds, with the "
        "player's own privileges",
    ),
    Guard(
        "an ivar name with a trailing newline is refused", CONVERT,
        '_IVAR_NAME = re.compile(r"^@[A-Za-z_][A-Za-z0-9_]*\\Z")',
        '_IVAR_NAME = re.compile(r"^@[A-Za-z_][A-Za-z0-9_]*$")',
        [CONVNAME + "test_an_ivar_name"],
        "`$` also matches just before a final newline, so a field name with one "
        "passed the check and was written into the Marshal stream with it: a "
        "file the editor cannot open",
    ),
    Guard(
        "a class name with a trailing newline is refused", CONVERT,
        '_CLASS_PATH = re.compile(r"^[A-Z][A-Za-z0-9_]*(::[A-Z][A-Za-z0-9_]*)*\\Z")',
        '_CLASS_PATH = re.compile(r"^[A-Z][A-Za-z0-9_]*(::[A-Z][A-Za-z0-9_]*)*$")',
        [CONVNAME + "test_a_class_name"],
        "the same `$` slip for a class path: `RPG::Item` plus a newline was "
        "accepted and written as a class name Ruby cannot resolve",
    ),
    Guard(
        "a string that does not re-encode travels as bytes", CONVERT,
        "        if not value.data.isascii() and text.encode(codec.name) != value.data:",
        "        if False:",
        [CONVBYTES + "test_the_other_byte_pair_is_kept_exactly"],
        "cp932 decodes 0x8790 and 0x81E0 to one character and writes back only "
        "the second, so a string holding the first was silently rewritten by "
        "any patch that carried it",
    ),
    Guard(
        "a string is charged by its size", CONVERT,
        "    walk.spend(_size_charge(len(value.data)))",
        "    pass",
        [CONVSIZE + "test_many_references_to_one_big_string_are_refused"],
        "a Marshal link returns the same string for every reference and each "
        "reference was decoded into a copy: one 4 MB string linked 5,000 times "
        "built 20 GB of reply before anything refused it",
    ),
    Guard(
        "an opaque payload is charged by its size", CONVERT,
        "        walk.spend(_size_charge(len(value.payload)))",
        "        pass",
        [CONVSIZE + "test_many_references_to_one_big_payload_are_refused"],
        "the same amplification through a _dump payload, which is base64'd "
        "once per reference",
    ),
    Guard(
        "a caller's text is encoded strictly", CONVERT,
        '        return RubyString(codec.encode_strict(value["__string__"]),',
        '        return RubyString(codec.encode(value["__string__"]),',
        [CONVSTRICT + "test_a_lone_surrogate_cannot_write_a_raw_byte"],
        "the lenient encoder turns a lone surrogate into a raw byte, so a "
        "caller's text could write bytes into a string tagged as valid UTF-8",
    ),
    Guard(
        "a table cell is a whole number", CONVERT,
        '        number = _whole(cell, "cell %d" % index)',
        "        number = int(cell)",
        [CONVWHOLE + "test_a_fractional_cell_is_refused_not_truncated",
         CONVWHOLE + "test_a_bool_or_a_string_is_not_a_cell"],
        "`int(1.9)` is 1 and `int(True)` is 1, so a tile id the caller got "
        "wrong was written as a different tile and reported as a success",
    ),
    Guard(
        "a table size that is infinite is a conversion error", CONVERT,
        '    xsize = _whole(value.get("xsize", 0), "xsize")',
        '    xsize = int(value.get("xsize", 0))',
        [CONVWHOLE + "test_infinity_is_a_conversion_error"],
        "`int(float('inf'))` raises OverflowError, which is not in the server's "
        "caught list, so a wrong argument was reported as an internal error",
    ),
    Guard(
        "a value nested too deeply is refused", CONVERT,
        "    if depth > MAX_JSON_DEPTH:\n"
        "        raise ConversionError(\n"
        '            "this value is nested more than %d levels deep, which the server "',
        "    if False:\n"
        "        raise ConversionError(\n"
        '            "this value is nested more than %d levels deep, which the server "',
        [CONVDEEP + "test_deep_is_refused"],
        "the writer has no depth limit and the reader refuses past 192, so a "
        "value nested a few hundred levels was written and the file could then "
        "not be opened through the server",
    ),
    Guard(
        "a hash form holds pairs", CONVERT,
        "        if not isinstance(entries, list) or any(",
        "        if False and any(",
        [CONVMARK + "test_hash_entries_are_pairs"],
        "`for k, v in entries` unpacks a two-character string into a key and a "
        "value, so {'__hash__': ['ab']} wrote a pair nobody sent",
    ),
    Guard(
        "a symbol form holds a string", CONVERT,
        '        if not isinstance(value["__symbol__"], str):',
        "        if False:",
        [CONVMARK + "test_a_symbol_is_a_string"],
        "`RubySymbol(123)` becomes the symbol \"123\": a number the caller "
        "sent was written as a name",
    ),
    Guard(
        'a variable operation that is not text is refused', COMMANDS,
        """    op_code = _named(entry.get("op", entry.get("operation", "set")),
                     VARIABLE_OP_CODES, "the variable operation")""",
        """    op_code = VARIABLE_OP_CODES.get(entry.get("op", entry.get("operation", "set")))""",
        [DSLWRONG + 'test_each_is_a_command_error'],
        'an unhashable op was a bare TypeError from a dict lookup, reported as an argument of the wrong shape with no name',
    ),
    Guard(
        'a variable range runs upward', COMMANDS,
        """    if end < variable_id:""",
        """    if False:""",
        [DSLWRONG + 'test_each_is_a_command_error'],
        'a reversed range wrote a command the interpreter loops over zero times, and the reply said the variables were set',
    ),
    Guard(
        'a switch range runs upward', COMMANDS,
        """    if end < switch_id:""",
        """    if False:""",
        [DSLWRONG + 'test_each_is_a_command_error'],
        'the same reversed range for Control Switches',
    ),
    Guard(
        'a self switch is one of A to D', COMMANDS,
        """    if value not in SELF_SWITCH_LETTERS:""",
        """    if False:""",
        [DSLWRONG + 'test_each_is_a_command_error'],
        'any text was written as a self-switch letter the engine has no switch for, and the event waited on a switch that could never be set',
    ),
    Guard(
        "a switch state is the engine's 0 or 1", COMMANDS,
        """        if value not in (0, 1):""",
        """        if False:""",
        [DSLWRONG + 'test_each_is_a_command_error'],
        'an integer other than 0 or 1 was written through as the state, which the interpreter reads as neither on nor off',
    ),
    Guard(
        'a transfer needs its coordinates', COMMANDS,
        """        _needs_int(_required(t, "x", "transfer"), "transfer.x"),""",
        """        _needs_int(t["x"], "transfer.x"),""",
        [DSLWRONG + 'test_each_is_a_command_error'],
        'a missing x was a bare KeyError, reported as an argument of the wrong shape with no name',
    ),
    Guard(
        'a raw command code is bounded', COMMANDS,
        """    code = _bounded(raw["code"], "raw.code", 0, MAX_COMMAND_CODE)""",
        """    code = _needs_int(raw["code"], "raw.code")""",
        [DSLWRONG + 'test_each_is_a_command_error'],
        'a raw code of any size was written, and a negative one is not a command',
    ),
    Guard(
        'a raw parameter nests only so deep', COMMANDS,
        """    if depth > MAX_JSON_DEPTH:""",
        """    if False:""",
        [DSLWRONG + 'test_nesting_a_raw_parameter_deeply_is_refused'],
        'the writer has no depth limit and the reader refuses past 192, so a literal nested a few hundred levels was written and the file could then not be opened through the server',
    ),
    Guard(
        'a raw object names its class as text', COMMANDS,
        """    if not cls or not isinstance(cls, str):""",
        """    if not cls:""",
        [DSLWRONG + 'test_each_is_a_command_error'],
        'a list for __class__ went into the object as its class and crashed the writer',
    ),
    Guard(
        'a move route step is an object', COMMANDS,
        """    step = _needs_object(step, "a move route step")""",
        """    step = step""",
        [DSLWRONG + 'test_each_is_a_command_error'],
        'a string step was a bare AttributeError on .get',
    ),
    Guard(
        'a move route flag is true or false', COMMANDS,
        """        "@repeat": _needs_bool(value.get("repeat", False), "repeat"),""",
        """        "@repeat": bool(value.get("repeat", False)),""",
        [DSLWRONG + 'test_each_is_a_command_error'],
        'bool("false") is True, so a route the caller said does not repeat repeated forever',
    ),
    Guard(
        'an empty else is still written', COMMANDS,
        """    if entry.get("else") is not None:""",
        """    if entry.get("else"):""",
        [DSLWRONG + 'test_an_empty_else_is_still_written'],
        'an else with no commands is falsy, so asking for one was silently dropped',
    ),
    Guard(
        "a refusal quotes a caller's value briefly", COMMANDS,
        """        raise CommandError("%s must be a whole number, got %s"
                           % (what, brief(value)))""",
        """        raise CommandError("%s must be a whole number, got %r"
                           % (what, value))""",
        [DSLWRONG + 'test_a_message_quotes_a_huge_value_briefly'],
        'the message repeated a whole caller value, so one large wrong argument made a megabyte error',
    ),
    Guard(
        'load warnings are capped', CODEC,
        """        if len(self.warnings) < MAX_LOAD_WARNINGS:""",
        """        if True:""",
        [HOSTILEFILE + 'test_repeated_empty_ivar_records_are_capped'],
        'an empty I record is 3 bytes and about 118 characters of warning, and the list goes into a tool reply whole: a 128 MB file was 5 GB of reply',
    ),
    Guard(
        'the warnings left out are counted', CODEC,
        """    if reader.warnings_left_out:""",
        """    if False:""",
        [HOSTILEFILE + 'test_the_cap_says_how_many_were_left_out'],
        'a cap that does not say it fired reads as a file with fifty problems',
    ),
    Guard(
        'a symbol in a warning is clipped', CODEC,
        """                    "back" % str(symbol)[:MAX_WARNED_NAME]""",
        """                    "back" % symbol""",
        [HOSTILEFILE + 'test_a_long_symbol_is_clipped_in_its_warning'],
        "the warning repeated the file's whole symbol name every time it fired: a 1 MB name reused 100,000 times is 100 GB",
    ),
    Guard(
        "the writer stops at the reader's depth", CODEC,
        """        if self._depth > MAX_DEPTH:""",
        """        if False:""",
        [HOSTILEW + 'test_deep_is_a_marshal_error_not_a_recursion_error', HOSTILEW + 'test_just_past_the_limit_is_refused_not_written'],
        'the reader refuses past MAX_DEPTH, so a value written deeper was a file this server could not open',
    ),
    Guard(
        'a regexp option is one byte', CODEC,
        """        if not 0 <= obj.options <= 0xFF:""",
        """        if False:""",
        [HOSTILER + 'test_out_of_range_is_refused_not_cut'],
        '_byte masks with 0xFF, so an option of 256 was written as 0 with no error',
    ),
    Guard(
        'a name that is a huge integer is described', CONVERT,
        """            and value.bit_length() > MAX_TEXT_CHARS * 3:""",
        """            and False:""",
        [HOSTILEN + 'test_a_bignum_name_does_not_raise'],
        'str() of an Integer past about 4300 digits raises on CPython 3.11, and a Bignum is a legal name in a file: it ended the whole reply',
    ),
    Guard(
        'a replaced hash is looked up, not turned into a dict', CONVERT,
        """            old = existing.get(key, _ABSENT)""",
        """            old = dict(existing.items()).get(key, _ABSENT)""",
        [HOSTILEK + 'test_no_type_error'],
        'an Array is a legal hash key and is unhashable, so building a dict of the existing pairs raised TypeError',
    ),
    Guard(
        'a float keeps the sign of a zero', CODEC,
        """                obj == 0 and math.copysign(1.0, _parse_float(raw))
                != math.copysign(1.0, obj)):""",
        """                False):""",
        [HOSTILEZ + 'test_negative_zero_is_not_written_as_zero'],
        '0.0 == -0.0, so a float edited to the other zero was written with its old bytes and lost the sign',
    ),
    Guard(
        'an entry id below zero is refused', PROJECT,
        """            if entry_id < 0:""",
        """            if False:""",
        [HOSTILEID + 'test_set_entry'],
        'value[-1] = entry replaces the last entry and reports a create',
    ),
    Guard(
        'a space in a name is kept', RUBYTYPES,
        '''                   or unicodedata.category(char) == "Zs"''',
        """                   or False""",
        [HOSTILESP + 'test_unicode_spaces_survive'],
        'isprintable() is False for every space but U+0020, so the ideographic space in a Japanese name came back as a question mark',
    ),
    Guard(
        'a Bignum hash key is looked up by scan', RUBYTYPES,
        """        return -_HASH_MODULUS < key < _HASH_MODULUS""",
        """        return True""",
        [HOSTILECOL + 'test_colliding_bignum_keys'],
        'CPython hashes an int modulo 2**61-1, so n crafted Bignum keys share a bucket and the first lookup costs n squared',
    ),
    Guard(
        'a temp file that cannot be made is a project error', PROJECT,
        """            # A folder that refuses a new file: a message, not a traceback.
            raise ProjectError("could not write %s: %s" % (filename, exc)) from exc
        temp_path = Path(temp_name)""",
        """            raise
        temp_path = Path(temp_name)""",
        [HOSTILEWF + 'test_a_temp_file_that_cannot_be_made'],
        'mkstemp sat outside the try, so a folder that refuses a new file was a raw OSError after the backup had been taken',
    ),
    Guard(
        'a file that cannot be deleted is a project error', PROJECT,
        """            raise ProjectError("could not delete %s: %s" % (filename, exc)) from exc""",
        """            raise""",
        [HOSTILEWF + 'test_a_file_that_cannot_be_deleted'],
        'unlink on a file held open on Windows was a raw PermissionError, where every other write path reports a ProjectError',
    ),
    Guard(
        'the scripts table must be a list to set a slot', PROJECT,
        """        if not isinstance(raw, list) or not raw:
            raise ProjectError("this project's Scripts data holds no slots")""",
        """        if not raw:
            raise ProjectError("this project's Scripts data holds no slots")""",
        [HOSTILEWF + 'test_a_script_table_that_is_not_a_list'],
        'a non-empty dict passed the emptiness test and raised KeyError on the first index',
    ),
    Guard(
        'the schema walk asks its memo before it builds children', SCHEMA,
        """    if id(value) in seen:
        return
    children = ruby_children(value)""",
        """    children = ruby_children(value)
    if False:
        return""",
        [HOSTILEW6 + 'test_self_links_are_linear'],
        'ruby_children copies the container, so one Array linking to itself N times copied N elements per link: N squared work for a few megabytes of file',
    ),
    Guard(
        'a change-log line the parser cannot hold is skipped', BACKUPS,
        """            except (json.JSONDecodeError, RecursionError):""",
        """            except json.JSONDecodeError:""",
        [HOSTILELOG + 'test_a_deeply_nested_line_is_skipped'],
        'a line of 30,000 brackets is under the line bound and overflows the JSON parser, which crashed the tool the line bound was written to protect',
    ),
    Guard(
        'a change-log line must be an object', BACKUPS,
        """            if isinstance(record, dict):""",
        """            if True:""",
        [HOSTILELOG + 'test_a_line_that_is_not_an_object_is_skipped'],
        'recent_changes promises a list of dicts and handed back whatever json.loads produced',
    ),
    Guard(
        'a negative change-log limit is empty', BACKUPS,
        """        rows: "deque[str]" = deque(maxlen=max(limit, 0))""",
        """        rows: "deque[str]" = deque(maxlen=limit)""",
        [HOSTILELOG + 'test_a_negative_limit_is_an_empty_answer'],
        'deque(maxlen=-1) raises ValueError',
    ),
    Guard(
        'two snapshots in one second are two snapshots', BACKUPS,
        """        while self._backup_dir(stamp).exists():""",
        """        while False:""",
        [HOSTILESNAP + 'test_the_first_is_kept'],
        'the stamp has one-second resolution and a snapshot copies over an existing directory, so a second pre-restore safety snapshot replaced the first, which held the original data',
    ),
    Guard(
        'a number too long to parse is a parse error', SERVER,
        """            except ValueError:
                # After JSONDecodeError""",
        """            except ZeroDivisionError:
                # After JSONDecodeError""",
        [HOSTILEMSG + 'test_the_session_goes_on'],
        'an integer literal of more than 4,300 digits raises a plain ValueError on CPython 3.11, and one such line ended the whole session',
    ),
    Guard(
        'a file name has no trailing newline', _SRC + "paths.py",
        """_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*\\Z")""",
        """_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")""",
        [HOSTILENAME + 'test_refused'],
        '`$` also matches before a final newline, so Map001.rvdata2 plus a newline was accepted as a file name',
    ),
    Guard(
        'the workflow guide does not deny the live tool', DOCS,
        """It edits files. Playtesting goes through `live`, which runs a scratch copy of""",
        """It edits files; it does not talk to a running game. There is no live state, no""",
        [HOSTILEDOC + 'test_no_false_claim'],
        'the guide told the model there was no playtest control while the live tool and the server instructions describe exactly that',
    ),
    Guard(
        'a switch read id is bounded like a write id', SWITCH_USAGE,
        """        if 1 <= ident <= _MAX_ID:""",
        """        if ident >= 1:""",
        [HOSTILETALLY + 'test_read_ids_are_clamped_like_write_ids'],
        'a Bignum read id from a crafted file became a tally key and str() of it raised in the report',
    ),
    Guard(
        'an unknown common-event call ends the walk', _SRC + "analysis/dead_ends.py",
        """            if calls is None:""",
        """            if False:""",
        [HOSTILEQ + 'test_called_from_with_many_unknown_calls'],
        'each common event with a non-literal call pushed the whole id set again: N events were N squared frontier entries, and 30,000 of them took more than three minutes',
    ),
    Guard(
        'an enormous common-event id means any', _SRC + "analysis/dead_ends.py",
        """            if match.group(1) is None or len(match.group(1)) > 9:""",
        """            if match.group(1) is None:""",
        [HOSTILECE + 'test_a_script_naming_one'],
        'int() of a run of more than 4,300 digits raises ValueError and the whole critique was lost',
    ),
    Guard(
        'a script integer literal is bounded', _SRC + "analysis/reachability.py",
        '''_INT_ARG = r"-?\\d{1,12}(?:_\\d{1,12}){0,4}"''',
        '''_INT_ARG = r"-?\\d+(?:_\\d+)*"''',
        [HOSTILEBIG + 'test_a_transfer_literal'],
        'int() of a literal past 4,300 digits raised ValueError out of the reachability walk',
    ),
    Guard(
        'a statistic ignores a number beyond a game stat', _SRC + "analysis/stat_outliers.py",
        """    if abs(value) > _MAX_MAGNITUDE:""",
        """    if False:""",
        [HOSTILESTAT + 'test_a_bignum_is_not_a_number'],
        'a Bignum or a float near the limit overflowed the fences, and OverflowError is not an error the server reports: every scope of the critique was lost',
    ),
    Guard(
        'a rounded statistic must be finite', _SRC + "analysis/stat_outliers.py",
        """    if not math.isfinite(value):""",
        """    if False:""",
        [HOSTILESTAT + 'test_round_refuses_a_non_finite'],
        'int() of an infinity raises OverflowError and of NaN raises ValueError',
    ),
    Guard(
        'a level curve needs a depth', _SRC + "analysis/stat_outliers.py",
        """                and level < table.ysize and table.zsize > 0):""",
        """                and level < table.ysize):""",
        [HOSTILESTAT + 'test_a_table_with_no_depth_is_not_read'],
        'a Table may have zsize 0 and table.get(...) then raises IndexError out of the report',
    ),
    Guard(
        'an RTP name from Game.ini is clipped', PROJECT,
        """                if len(value) > _RTP_NAME_CLIP:""",
        """                if False:""",
        [HOSTILERTP + 'test_a_huge_name_is_clipped'],
        'the name is quoted into every finding that cannot be checked against it: a 1 MB RTP value times thousands of findings is gigabytes of message',
    ),
    Guard(
        'a step of no frames is refused', LIVE_BRIDGE,
        '''      raise ArgumentError, "frames must be at least 1" if frames < 1''',
        '''      raise ArgumentError, "unused" if false''',
        [LIVETEXT + 'test_a_step_of_no_frames_is_refused_not_left_pending'],
        'a step of zero or fewer frames never finishes, answers nothing, and poll returns early for every later request: the client waits forever',
    ),
    Guard(
        'a bridge request must be a hash', LIVE_BRIDGE,
        '''    raise TypeError, "a request is a Hash" unless request.is_a?(Hash)''',
        '''    raise TypeError, "unused" unless request.is_a?(Hash)''',
        [LIVETEXT + 'test_a_request_that_is_not_a_hash_is_answered_not_fatal'],
        'a stray non-Hash request raised again inside the rescue and sent the game into the crash loop',
    ),
    Guard(
        'the walk reads a Bignum as absent', _SRC + "analysis/walk.py",
        '''    if number is not None and not -_ID_BOUND <= number <= _ID_BOUND:''',
        '''    if False:''',
        [HOSTILEBIGID + 'test_the_walk_reads_it_as_absent',
         HOSTILEVAL + 'test_a_crafted_map'],
        'a Bignum id formatted into a finding raised past 4,300 digits and ended the whole validate run',
    ),
    Guard(
        'Game.ini cannot move the script pack out of the copy', LIVE_SESSION,
        '''                    chosen.resolve().relative_to(game_dir.resolve())''',
        '''                    chosen.resolve()''',
        [HOSTILEINI + 'test_the_path_stays_inside'],
        'an absolute or .. Scripts= value sent the bridge-injected pack to a file outside the scratch copy, possibly the real project',
    ),
    Guard(
        'a save file of any engine is not copied', LIVE_SESSION,
        '''_SAVE_FILE = re.compile(r"^save\\d*\\.r[xv]data2?$", re.IGNORECASE)''',
        '''_SAVE_FILE = re.compile(r"^save\\d*\\.rvdata2?$", re.IGNORECASE)''',
        [HOSTILEMARK + 'test_an_xp_save_is_a_save'],
        'XP saves (Save1.rxdata) were copied into the scratch run, which breaks the rule that a test run never sees saves',
    ),
    Guard(
        'an orphan marker must name a real owner', LIVE_SESSION,
        '''        if (not isinstance(owner, int) or isinstance(owner, bool)
                or not 0 < owner < 2 ** 32):''',
        '''        if False:''',
        [HOSTILEMARK + 'test_a_marker_with_a_non_numeric_owner'],
        'a marker owner that is not a pid raised out of sweep_orphans and stopped every later start',
    ),
    Guard(
        'a game reply has a node budget', _SRC + "live/wire.py",
        '''    if budget[0] < 0:''',
        '''    if False:''',
        [HOSTILEREPLY + 'test_a_shared_list_is_bounded'],
        'Marshal links share one object, so 32 levels of [x, x] named 2**32 nodes and hung the server',
    ),
    Guard(
        'a script slot inflates only so far', LIVE_BRIDGE,
        '''    if stream.unconsumed_tail:''',
        '''    if False:''',
        [HOSTILEBOMB + 'test_a_slot_that_inflates_past_the_limit_is_empty'],
        'zlib.decompress had no output cap, so a crafted slot inflated to a gigabyte while a session was being prepared',
    ),
    Guard(
        'a tile whose shape the table lacks is not drawn', _SRC + "render/tiles.py",
        '''    if shape >= len(rows):''',
        '''    if False:''',
        [HOSTILESHAPE + 'test_compose_returns_none_not_an_index_error'],
        'autotile_block names a shape 0-47 for every kind and a wall has 16, a waterfall 4: a damaged map id crashed the whole render with IndexError',
    ),
    Guard(
        'a trigger is named only when it is a number', _SRC + "tools/event_tools.py",
        '''    if isinstance(trigger, int) and not isinstance(trigger, bool):''',
        '''    if True:''',
        [HOSTILETRIG + 'test_a_list_trigger'],
        'dict.get hashes its key: an @trigger holding an Array raised TypeError and one crafted page failed the read of the whole map',
    ),
    Guard(
        'a page trigger is converted once through the shared budget', _SRC + "tools/event_tools.py",
        '''    trigger_code = to_json(trigger, ctx.codec, TABLE_MODES[0], walk)''',
        '''    trigger_code = to_json(trigger, ctx.codec, walk)''',
        [HOSTILETRIG + 'test_the_shared_budget_goes_where_to_json_expects_it'],
        'walk is the fourth parameter of to_json; passed third it was read as the table mode and the budget was never used',
    ),
    Guard(
        'a negated literal stands for a character it accepts', _SRC + "tools/pattern_safety.py",
        '''            found |= _matched_by(lambda c: c != chr(av))''',
        '''            found.add("a")''',
        [HOSTILEGATE + 'test_a_negated_literal'],
        '[^a]+[^a]+b was judged on runs of a (fail at once) and b (match at once): the ReDoS gate accepted it and 3,000 Japanese characters then took minutes',
    ),
    Guard(
        'a negated class stands for characters it accepts', _SRC + "tools/pattern_safety.py",
        '''                found |= _matched_by(lambda c: not _in_class_matches(av, c))''',
        '''                found.add("\\x00")''',
        [HOSTILEGATE + 'test_a_negated_class'],
        'NEGATE assumed NUL is outside every negated class, so [^\\x00a]+[^\\x00a]+b got no run its class accepts',
    ),
    Guard(
        'a range is measured inside as well as at its ends', _SRC + "tools/pattern_safety.py",
        '''                    if high - low > 1:''',
        '''                    if False:''',
        [HOSTILEGATE + 'test_a_range_is_not_only_its_endpoints'],
        '[a-z]+[a-z]+[az] has only the ends to be told apart from, and runs of an interior letter are as long as any other',
    ),
    Guard(
        'a kind spelling is short', MAP_PAINT,
        '''    if len(text) > _MAX_KIND_SPELLING:''',
        '''    if False:''',
        [HOSTILEKIND + 'test_a_long_spelling_is_refused_before_it_is_echoed'],
        'a 10 MB spelling was echoed whole into every refusal, past the reply-size ceilings',
    ),
    Guard(
        'a kind number is ASCII digits', MAP_PAINT,
        '''    return text.isascii() and text.isdigit()''',
        '''    return text.isdigit()''',
        [HOSTILEKIND + 'test_a_non_ascii_digit_is_a_refusal'],
        'str.isdigit() is True for the superscript two, which int() then rejects with a bare ValueError instead of the spelling help',
    ),
    Guard(
        'a damaged command list is refused, not appended to', _SRC + "tools/event_tools.py",
        '''    if existing is not None and not isinstance(existing, list):''',
        '''    if False:''',
        [HOSTILEAPP + 'test_a_list_that_is_a_string_is_not_split_into_characters'],
        'list("abc") is three characters: a damaged @list was written back in front of the new commands',
    ),
    Guard(
        'a repeated body is measured on an alternating subject', _SRC + "tools/pattern_safety.py",
        '''    if 2 <= len(quantified) <= _ALTERNATING_LIMIT:''',
        '''    if False:''',
        [HOSTILEGATE + 'test_a_repeated_body_of_two_characters',
         HOSTILEGATE + 'test_a_repeated_body_of_a_letter_and_a_digit'],
        '(?:ab)+(?:ab)+! took 4.7 s on 2,000 characters and was accepted: every subject was one character at a time or built from the whole alphabet, with the literal ! between a and b',
    ),
    Guard(
        'a scope named twice is run once', _SRC + "tools/registry.py",
        '''    wanted = list(dict.fromkeys(s for s in asked if s in allowed))''',
        '''    wanted = [s for s in asked if s in allowed]''',
        [HOSTILESCOPE + 'test_duplicates_collapse_in_first_seen_order'],
        'scopes=["summary"] * 1,000,000 ran the summary a million times and echoed the million names back',
    ),
    Guard(
        'unknown scope names are listed briefly', _SRC + "tools/registry.py",
        '''        shown = ", ".join(echo(u) for u in unknown[:MAX_NAMES_LISTED])''',
        '''        shown = ", ".join(echo(u) for u in unknown)''',
        [HOSTILESCOPE + 'test_a_flood_of_unknown_names_is_a_short_refusal'],
        'a call naming fifty thousand unknown scopes got an error the size of the request',
    ),
    Guard(
        'a state section is one the bridge advertises', _SRC + "tools/live_tools.py",
        '''        if section not in bridge.STATE_SECTIONS:''',
        '''        if False:''',
        [HOSTILELIVE + 'test_an_unknown_state_section_is_refused_here'],
        'sections went to the game unchecked and the bridge builds a method name from each',
    ),
    Guard(
        'an id key is ASCII digits', _SRC + "tools/live_tools.py",
        '''        if not (isinstance(name, str) and name.isascii() and name.isdigit()''',
        '''        if not (isinstance(name, str) and name.isdigit()''',
        [HOSTILELIVE + 'test_a_non_ascii_digit_key_is_a_refusal'],
        'isdigit() passes the superscript two, which int() rejects with a bare ValueError',
    ),
    Guard(
        'an id key is short', _SRC + "tools/live_tools.py",
        '''                and len(name) <= 9 and int(name) >= 1):''',
        '''                and int(name) >= 1):''',
        [HOSTILELIVE + 'test_an_enormous_digit_key_is_a_refusal'],
        'a 5,000-digit key trips the CPython int-string limit with a ValueError',
    ),
    Guard(
        'deleting a map that exists nowhere is refused', _SRC + "tools/map_edit_tools.py",
        '''    if not has_file and not (isinstance(infos, RubyHash)''',
        '''    if False and not (isinstance(infos, RubyHash)''',
        [HOSTILEMAPEDIT + 'test_deleting_a_map_that_does_not_exist_is_a_refusal',
         HOSTILEMAPEDIT + 'test_the_preview_refuses_it_too'],
        'map_id=999 re-saved and backed up MapInfos and reported success',
    ),
    Guard(
        'a new map order is read as a bounded integer', _SRC + "tools/map_edit_tools.py",
        '''    order = max([_tree_order(info.get("order")) for _, info in infos''',
        '''    order = max([info.get("order") or 0 for _, info in infos''',
        [HOSTILEMAPEDIT + 'test_a_tree_entry_with_a_text_order_does_not_stop_a_create',
         HOSTILERE + 'test_a_huge_order_in_the_tree_does_not_become_the_next_order'],
        'a String @order raised TypeError in max(); a Bignum was added to and written back',
    ),
    Guard(
        'one command decodes a bounded number of texts', _SRC + "tools/search_tools.py",
        '''            if left <= 0:''',
        '''            if False:''',
        [HOSTILETEXTS + 'test_nested_lists_share_one_budget'],
        '64 lists of 64 links to one big string was 4,096 decodes in one command',
    ),
    Guard(
        'map_list builds only the rows it returns', _SRC + "tools/map_read_tools.py",
        '''        if matching <= offset or matching > offset + limit:''',
        '''        if matching > offset + limit:''',
        [HOSTILEMAPLIST + 'test_rows_before_the_offset_are_not_built'],
        'rows before the offset were built, each with a map_file call and a map load, then thrown away',
    ),
    Guard(
        'a tree key that is not a map id is skipped', _SRC + "tools/map_read_tools.py",
        '''        if not isinstance(info, RubyObject) or not _is_map_id(map_id):''',
        '''        if not isinstance(info, RubyObject):''',
        [HOSTILEMAPLIST + 'test_a_key_that_is_not_a_map_id_does_not_fail_the_listing'],
        'one MapInfos key of 0 raised ValueError from map_file and failed the whole listing',
    ),
    Guard(
        'the map_list offset is bounded', _SRC + "tools/map_read_tools.py",
        ['''    offset = opt_int_default(args, "offset", 0, minimum=0, maximum=MAX_MAP_ID)''',
         '''% MAX_MAP_ID, minimum=0, maximum=MAX_MAP_ID),'''],
        ['''    offset = opt_int_default(args, "offset", 0, minimum=0)''',
         '''% MAX_MAP_ID, minimum=0),'''],
        [HOSTILEMAPLIST + 'test_the_offset_is_bounded'],
        'an offset of a trillion was taken and echoed back',
    ),
    Guard(
        'a page trigger is named only when it is a whole number', _SRC + "tools/map_read_tools.py",
        '''    if isinstance(trigger, int) and not isinstance(trigger, bool):''',
        '''    if True:''',
        [HOSTILEMAPGET + 'test_a_list_trigger_is_described_not_a_crash'],
        'a Ruby Array as @trigger is a Python list, unhashable in TRIGGER_NAMES.get: map_get died with TypeError',
    ),
    Guard(
        'a condition id is shown only when it is a sane number', _SRC + "tools/map_read_tools.py",
        '''            and -_SHOWN_BOUND <= value <= _SHOWN_BOUND):''',
        '''            ):''',
        [HOSTILEMAPGET + 'test_a_bignum_condition_id_is_described_not_a_crash'],
        'a 5,000-digit condition id cannot be formatted: map_get died with ValueError',
    ),
    Guard(
        'a table lists a bounded number of layers', _SRC + "tools/map_read_tools.py",
        ['''                       for z in range(min(data.zsize, _LAYERS_SHOWN))},''',
         '''    for z in range(min(data.zsize, _LAYERS_SHOWN)):'''],
        ['''                       for z in range(data.zsize)},''',
         '''    for z in range(data.zsize):'''],
        [HOSTILEMAPGET + 'test_a_huge_third_extent_does_not_list_every_layer'],
        'a 0 x 0 x 60,000 table passed the cell budget and listed 60,000 layers twice',
    ),
    Guard(
        'a render opens a bounded number of character sheets', _SRC + "tools/map_render_tools.py",
        '''            if len(loaded) >= _MAX_CHARACTER_SHEETS:''',
        '''            if False:''',
        [HOSTILERENDER + 'test_a_map_of_many_character_sheets_decodes_a_bounded_number'],
        '2,000 events each naming a different sheet decoded 2,000 PNGs of up to 8 megapixels',
    ),
    Guard(
        'the render folder is checked before it is made', _SRC + "tools/map_render_tools.py",
        '''        _contained(folder, ctx.project.root, "the render folder")''',
        '''        pass''',
        [HOSTILERENDER + 'test_the_folder_is_checked_before_it_is_made'],
        'a junction planted as the work folder carried the mkdir outside the project before the check refused',
    ),
    Guard(
        'a failed render write removes its temp file', _SRC + "tools/map_render_tools.py",
        '''            temporary.unlink()''',
        '''            unused = temporary''',
        [HOSTILERENDER + 'test_a_failed_move_leaves_no_temp_file'],
        'a failed os.replace left name.png.tmp, which the trim glob never matches',
    ),
    Guard(
        'the render trim names only its own pictures', _SRC + "tools/map_render_tools.py",
        ['''    kept = sorted((p for p in folder.glob("map-????-??????????.png")''',
         '''                   if p.is_file() and _OWN_PICTURE.fullmatch(p.name)),'''],
        ['''    kept = sorted((p for p in folder.glob("map-*.png")''',
         '''                   if p.is_file()),'''],
        [HOSTILERENDER + 'test_only_its_own_pictures_are_trimmed'],
        'map-*.png trimmed pictures a person kept in a shared --render-dir; the property is held twice (glob and name pattern), so both are reverted together',
    ),
    Guard(
        'an empty restore list is refused', _SRC + "tools/misc_tools.py",
        '''        if not files:''',
        '''        if False:''',
        [HOSTILEBACKUPS + 'test_an_empty_file_list_is_not_every_file'],
        'files: [] meant every file in the snapshot, overwriting the whole Data folder',
    ),
    Guard(
        'a snapshot name is bounded', _SRC + "tools/misc_tools.py",
        '''                       maxLength=MAX_BACKUP_NAME_CHARS),''',
        '''                       ),''',
        [HOSTILEBACKUPS + 'test_a_long_backup_name_is_refused_short'],
        'a 100,000-character backup name was a bare ValueError (path too long) and was echoed back',
    ),
    Guard(
        'a snapshot label is bounded', _SRC + "tools/misc_tools.py",
        '''                      maxLength=MAX_LABEL_CHARS),''',
        '''                      ),''',
        [HOSTILEBACKUPS + 'test_a_long_label_is_refused'],
        'a 100,000-character label became a directory name past the path limit: a raw OSError',
    ),
    Guard(
        'a restore file name is bounded', _SRC + "tools/misc_tools.py",
        '''            if len(name) > MAX_BACKUP_NAME_CHARS:''',
        '''            if False:''',
        [HOSTILEBACKUPS + 'test_a_long_file_name_is_refused_short'],
        'each files entry was echoed whole into its refusal; the count was capped and the length was not',
    ),
    Guard(
        'the scripts count honours refresh', _SRC + "tools/misc_tools.py",
        '''refresh=refresh))''',
        '''refresh=False))''',
        [HOSTILEINFO + 'test_refresh_reaches_the_script_file'],
        'project_info refresh=true re-read every file but the scripts, whose count could be stale',
    ),
    Guard(
        'a set without a path is refused before the load', _SRC + "tools/raw_tools.py",
        '''        if not path:''',
        '''        if False:''',
        [HOSTILERAW + 'test_a_set_without_a_path_does_not_load_the_file'],
        'op=set with no path unmarshalled the whole table before saying a path is needed',
    ),
    Guard(
        'a wrapped array is judged by what it holds', _SRC + "tools/raw_tools.py",
        '''    if isinstance(existing, RubyIvars):''',
        '''    if False:''',
        [HOSTILERAW + 'test_a_wrapped_array_cannot_become_a_number'],
        'an Array with instance variables is a RubyIvars wrapper: the same-kind guard saw no array and let a number replace it',
    ),
    Guard(
        'unlisted tile ids force a partial key', _SRC + "tools/map_read_tools.py",
        '''            if unlisted:''',
        '''            if False:''',
        [VOCABTILES + 'test_a_reply_that_omits_tile_ids_says_so_in_a_partial_key'],
        'map_get tiles=counts left ids out and said so only in a count: a caller reading the list saw a complete answer',
    ),
    Guard(
        'a command walks a bounded number of its parameters', _SRC + "tools/search_tools.py",
        ['''    for value in as_list(command.get("parameters"))[:MAX_TEXTS_PER_COMMAND]:''',
         '''        for part in (value[:MAX_TEXTS_PER_COMMAND]'''],
        ['''    for value in as_list(command.get("parameters")):''',
         '''        for part in (value'''],
        [HOSTILERE + 'test_a_command_iterates_a_bounded_number_of_parameters'],
        'the shared decode budget counted strings only: a Marshal link to one million-element list of integers was walked in full by every command holding it',
    ),
    Guard(
        'a render writes through a temp file of its own making', _SRC + "tools/map_render_tools.py",
        '''    descriptor, temporary_name = tempfile.mkstemp(''',
        '''    descriptor, temporary_name = os.open(folder / (name + ".tmp"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC), str(folder / (name + ".tmp")) ; _unused = dict(''',
        [HOSTILERE + 'test_a_predictable_temp_name_is_not_written_through'],
        '<picture>.tmp is a name an attacker can predict, and write_bytes follows a link planted at it',
    ),
    Guard(
        'a label at its cap still makes a restorable name', _SRC + "tools/misc_tools.py",
        '''MAX_LABEL_CHARS = 64''',
        '''MAX_LABEL_CHARS = 128''',
        [HOSTILERE + 'test_a_snapshot_with_the_longest_label_can_be_restored'],
        'a 100-character label made a snapshot name past the restore tool\'s own cap: recovery blocked by the safety net',
    ),
    Guard(
        'the store refuses an empty file list', _SRC + "backups.py",
        '''        if files is not None and not files:''',
        '''        if False:''',
        [HOSTILERE + 'test_an_empty_file_list_is_refused_by_the_store_too'],
        'restore_backup(files=[]) meant every file; only the tool layer refused it',
    ),
    Guard(
        'the render trim checks the hash, not just the wildcards', _SRC + "tools/map_render_tools.py",
        '''                   if p.is_file() and _OWN_PICTURE.fullmatch(p.name)),''',
        '''                   if p.is_file()),''',
        [HOSTILERENDER + 'test_only_its_own_pictures_are_trimmed'],
        'the glob wildcard ? matches any character: a map-0001-holidaypic.png kept in a shared --render-dir counted as ours and was trimmed',
    ),
    Guard(
        'passability skips a star tile', _SRC + "render/passability.py",
        '''            if flag & STAR_FLAG:''',
        '''            if False:''',
        [OVERSTACK + 'test_a_star_tile_defers_to_the_one_below'],
        'a star tile (0x10) has no effect on passage: counting it painted a wall over every cell with a tree top above its trunk',
    ),
    Guard(
        'passability decides each direction on its own', _SRC + "render/passability.py",
        '''            if flag & bit:''',
        '''            if flag & EVERY_WAY:''',
        [OVERSTACK + 'test_the_deciding_tile_is_chosen_per_bit'],
        'the engine walks the layers once per direction bit: one walk for all four would block edges the player can cross',
    ),
    Guard(
        'a stack no tile decides is impassable', _SRC + "render/passability.py",
        '''            blocked |= bit      # no tile decided: the engine's final `false`''',
        '''            pass''',
        [OVERSTACK + 'test_a_stack_of_nothing_but_stars_is_impassable'],
        'check_passage ends in `return false`: a cell of only star tiles is a wall, not open ground',
    ),
    Guard(
        'an id past the flags table is passable, not a crash', _SRC + "render/passability.py",
        '''    if 0 <= tile_id < len(flags):''',
        '''    if True:''',
        [OVERTABLE + 'test_hostile_ids_do_not_crash'],
        'a map holding id 65535 (or -1, which indexes from the end) against a short flags table must be reported, not raise',
    ),
    Guard(
        'a flag word is read as the engine reads it, signed', _SRC + "render/passability.py",
        '''        return word - 0x10000 if word >= 0x8000 else word''',
        '''        return word''',
        [OVERTAGS + 'test_a_table_stores_words_unsigned_but_the_engine_reads_them_signed'],
        'Table keeps words unsigned; bit 15 set is a negative word in RGSS, and read unsigned it showed terrain tags 8 to 15 that no cell has',
    ),
    Guard(
        'a terrain tag must be above zero to count', _SRC + "render/passability.py",
        '''        if tag > 0:''',
        '''        if tag:''',
        [OVERTAGS + 'test_a_negative_word_has_no_tag'],
        'a negative shifted word is not a tag; the engine moves on to the layer below',
    ),
    Guard(
        'a solid event is judged by Ruby truthiness', _SRC + "render/passability.py",
        '''    if through is not None and through is not False:''',
        '''    if through:''',
        [OVEREVENTS + 'test_ruby_truthiness_decides_through'],
        'only nil and false are false in Ruby: a damaged page holding 0 in `through` is a through page',
    ),
    Guard(
        'layer 3 is not read as a tile layer for passage', _SRC + "render/scene.py",
        '''        for z in range(min(TILE_LAYER_COUNT, grid.depth) - 1, -1, -1):''',
        '''        for z in range(grid.depth - 1, -1, -1):''',
        [OVERPASS + 'test_the_meta_layer_is_not_a_tile_layer'],
        'layer 3 packs shadow bits and a region id; looking its word up as a tile made regions into walls',
    ),
    Guard(
        'a tile-graphic event is asked before the layers', _SRC + "render/scene.py",
        '''        out = list(tile_events.get((x, y), ()))''',
        '''        out = []''',
        [OVERPASS + 'test_a_tile_graphic_event_joins_the_walk_ahead_of_the_layers'],
        'all_tiles is the events\' tile ids then the layers: a chest drawn from a tile changes what can be walked',
    ),
    Guard(
        'digits are skipped below the readable tile size', _SRC + "render/overlays.py",
        '''    if tile_size < MIN_DIGIT_TILE_SIZE:''',
        '''    if False:''',
        [OVERDIGITS + 'test_below_the_floor_no_digit_is_drawn_but_the_cell_is_counted'],
        'the canvas is resampled by nearest pixel: a one- or two-pixel digit stroke at 4 pixels a tile is noise, not a number',
    ),
    Guard(
        'a label too long for a cell is counted, not drawn', _SRC + "render/overlays.py",
        '''    return 0 <= number < 10 ** MAX_LABEL_DIGITS''',
        '''    return True''',
        [OVERLABELS + 'test_a_long_or_negative_id_is_skipped_not_drawn'],
        'an event id out of a damaged file ran to thousands of digits: str() of it and a plate wider than the picture',
    ),
    Guard(
        'overlay names are held to the list', _SRC + "tools/map_render_tools.py",
        '''    overlays = scopes_arg(args, "overlays", marks.OVERLAY_NAMES, default=())''',
        '''    overlays = list(args.get("overlays") or ())''',
        [OVERARGS + 'test_an_unknown_name_is_refused_and_the_choices_listed',
         OVERARGS + 'test_hostile_entries_are_refused_without_a_crash'],
        'a misspelled overlay was dropped and the caller told the picture had it; a repeated one ran twice',
    ),
    Guard(
        'overlays has a ceiling', _SRC + "tools/map_render_tools.py",
        '''                         maxItems=MAX_OVERLAYS),''',
        '''                         ),''',
        [OVERARGS + 'test_more_than_the_ceiling_is_refused'],
        'a list of a million names was read, checked and echoed before the dedupe',
    ),
    Guard(
        'events are read for overlays that mark them', _SRC + "tools/map_render_tools.py",
        '''    wants_events = bool({marks.PASSABILITY, marks.EVENT_IDS} & set(overlays))''',
        '''    wants_events = False''',
        [OVERTOOL + 'test_event_ids_are_drawn_even_when_events_is_none'],
        'events=none skipped the walk, so event_ids labelled nothing and passability never saw a solid event',
    ),
]

