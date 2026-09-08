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
MAP_TOOLS = "src/rpgvxace_mcp/tools/map_tools.py"
MISC_TOOLS = "src/rpgvxace_mcp/tools/misc_tools.py"
PROJECT = "src/rpgvxace_mcp/project.py"
SERVER = "src/rpgvxace_mcp/server.py"
ANALYSIS = "src/rpgvxace_mcp/analysis.py"
REGISTRY = "src/rpgvxace_mcp/tools/registry.py"
DB_TOOLS = "src/rpgvxace_mcp/tools/db_tools.py"
EVENT_TOOLS = "src/rpgvxace_mcp/tools/event_tools.py"


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
        [L + "test_ivar_wrapped_symbol_in_a_name_position_is_reported"],
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
        "unmodelled engine cannot create a map", MAP_TOOLS,
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
        '            if digits.isdigit() and 1 <= int(digits) <= MAX_MAP_ID \\',
        '            if digits.isdigit() and True \\',
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
        "echoed values are truncated", SERVER,
        'MAX_ECHO_CHARS = 120',
        'MAX_ECHO_CHARS = 100_000_000',
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
        "quoted project text is length-capped", ANALYSIS,
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
        '    else:\n        _check_id(table, entry_id, request.container, request.kind)',
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
        '    if isinstance(entry, RubyObject) and _carries_id(request.kind):',
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
        "a map patch cannot change its size", MAP_TOOLS,
        '    refuse_fields(',
        '    _skip = (',
        [MAPW + "test_a_patch_cannot_change_the_map_size",
         MAPW + "test_width_is_refused_on_update"],
        "setting @width alone leaves the map claiming a size its tile table "
        "does not have, which is the shape the editor refuses to open",
    ),
    Guard(
        "both halves of a map edit are checked first", MAP_TOOLS,
        '    if name is not None:\n'
        '        info = ctx.project.get_entry("map_infos", map_id)',
        '    if False:\n'
        '        info = ctx.project.get_entry("map_infos", map_id)',
        [MAPW + "test_a_rename_of_a_map_with_no_tree_entry_writes_nothing"],
        "the map file is written before the rename half is validated, so a "
        "caller is told the call failed with half of it already on disk",
    ),
    Guard(
        "a discarded tile is reported", MAP_TOOLS,
        "        dropped = _cells_outside(data, width, height)",
        "        dropped = 0",
        [MAPW + "test_a_shrink_reports_the_tiles_it_discards"],
        "shrinking a map silently throws away every tile outside the new "
        "bounds",
    ),
    Guard(
        "tile values fit their layer", MAP_TOOLS,
        "        _check_tile(ctx, tile_id, z, \"tile_id\")",
        "        pass  # check removed",
        [MAPW + "test_a_tile_value_the_layer_cannot_hold_is_refused",
         MAPW + "test_a_region_id_beyond_the_layer_is_refused"],
        "Table.set masks to sixteen bits, so an out-of-range id is stored as "
        "some other tile entirely and reported back as the one asked for",
    ),
    Guard(
        "raw_data refuses a shape swap", MISC_TOOLS,
        "    _check_same_kind(existing, new_value, \"/\".join(path))",
        "    pass  # check removed",
        [RAWD + "test_a_structure_cannot_be_replaced_by_a_scalar"],
        "an Integer takes the place of an event hash or a tile table, and "
        "the file stops being one the editor or the game can open",
    ),
    Guard(
        "search reports the scopes it searched", MISC_TOOLS,
        "\n    return _result(hits, limit, searched, unreadable, unsearchable)",
        "\n    return _result(hits, limit, list(scopes), unreadable, unsearchable)",
        [SEARCHED + "test_a_command_code_search_does_not_claim_to_have_searched_text"],
        "a command_code search skips the scopes holding no commands, and "
        "reporting them as searched tells the model they came back empty",
    ),
    Guard(
        "an unknown scope is refused", MISC_TOOLS,
        "    unknown = [s for s in asked if s not in allowed]",
        "    unknown = []",
        [SEARCHED + "test_an_unknown_scope_is_refused_not_ignored"],
        "a misspelled scope is dropped and everything runs instead, so the "
        "caller believes it narrowed the search and gets the whole project",
    ),
    Guard(
        "a scope is searched only once it loads", MISC_TOOLS,
        '    if code_filter is None and "maps" in scopes:\n'
        '        try:',
        '    if code_filter is None and "maps" in scopes:\n'
        '        searched.append("maps")\n'
        '        try:',
        [READ + "test_a_scope_whose_only_file_fails_is_not_called_searched"],
        "a file that would not load comes back as \"searched, nothing found\", "
        "which is the same lie as reporting a scope that was skipped",
    ),
    Guard(
        "creating a map is staged like every other write", MAP_TOOLS,
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
        "raw_data sees the @ spelling too", MISC_TOOLS,
        '    if path and _bare_name(path[0]) in STRUCTURAL_MAP_PATHS:',
        '    if path and path[0] in STRUCTURAL_MAP_PATHS:',
        [ATFIELD + "test_raw_data_cannot_reach_a_structural_field"],
        'the same bypass on the escape hatch: path "@width" reaches the '
        'field this refuses',
    ),
    Guard(
        "creating a map checks its patch", MAP_TOOLS,
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
        '    entry = request.require(entry_id, "update")\n    request.refuse_id_patch(patch, entry_id)',
        '    entry = request.require(entry_id, "update")\n    pass  # check removed',
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
        "the hanging pattern shape is refused", MISC_TOOLS,
        '    _refuse_repeated_group(query)',
        '    pass  # check removed',
        [REDOS + "test_a_nested_quantifier_is_refused"],
        'a repeated group that itself repeats backtracks exponentially and '
        'cannot be interrupted, so one search wedges this single-threaded '
        'server for good',
    ),
    Guard(
        "a command_code search needs somewhere to look", MISC_TOOLS,
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
        "a repeated group is refused", MISC_TOOLS,
        '            if after < len(pattern) and pattern[after] in _QUANTIFIERS:',
        '            if False:',
        [REDOS + "test_a_nested_quantifier_is_refused"],
        "the shapes that backtrack exponentially -- ((a+))+, (?:a+)+, "
        "(a|a)+ -- walk straight past a flat pattern test, and a match in "
        "progress cannot be interrupted",
    ),
    Guard(
        "an op's own keys are checked", REGISTRY,
        '        if item.get("properties"):',
        '        if False:',
        [SHAPE + "test_a_misspelled_key_inside_an_op_is_refused"],
        "a misspelled key inside an op is dropped, and for set_commands "
        "that leaves the page it was meant to rewrite holding one blank "
        "command",
    ),
    Guard(
        "an empty command list is refused", EVENT_TOOLS,
        '    entries = opt_list(raw_op, "commands")\n    if not entries:',
        '    entries = opt_list(raw_op, "commands")\n    if False:',
        [SHAPE + "test_an_empty_command_list_is_refused"],
        "set_commands with no commands leaves the page holding a lone "
        "blank command and reports it as a rewrite",
    ),
    Guard(
        "findings stop being collected at the ceiling", ANALYSIS,
        '            if len(findings) >= MAX_FINDINGS:',
        '            if False:',
        [BOUND + "test_a_flood_of_findings_stops_at_the_ceiling"],
        "the ceiling bounds only what is returned, so a page of broken "
        "jumps builds a finding per command -- tens of thousands of them "
        "-- in order to report ten",
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


def prove(guard: Guard) -> tuple[bool, str]:
    """Revert one guard in a scratch copy and require its tests to fail."""
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

        result = run_tests(tree, guard.tests)
        if result is None:
            return True, ("hung for %ds with the guard reverted, which is the "
                          "failure it prevents" % TEST_TIMEOUT)
        if result.returncode == 0:
            return False, ("VACUOUS: the tests still pass with the guard "
                           "reverted, so they are checking something else")
        first = [line for line in result.stdout.splitlines()
                 if line.startswith("E ") or " - " in line]
        return True, (first[0].strip() if first else "failed as expected")


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
    parser.add_argument("--only", metavar="TEXT",
                        help="Only guards whose name contains TEXT.")
    parser.add_argument("--check", action="store_true",
                        help="Only check that every anchor still matches its "
                             "file exactly once. Runs no tests, so it is "
                             "seconds rather than minutes.")
    args = parser.parse_args()

    guards = [g for g in GUARDS if not args.only or args.only.lower() in g.name.lower()]
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
