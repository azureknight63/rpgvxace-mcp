"""Engine-profile and schema-observation tests.

The hostile-input cases here are guards, not illustrations: each one decodes to
something plausible, or walks exponentially, or ends the session, if its guard
is removed. `scripts/prove_guards.py` reverts each and requires the matching
test to fail.
"""

import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE / "fixtures"))

from build_project import build  # noqa: E402

from rpgvxace_mcp import marshal_rb  # noqa: E402
from rpgvxace_mcp.convert import TextCodec, to_json  # noqa: E402
from rpgvxace_mcp.docs import (  # noqa: E402
    command_reference_document,
    data_format_reference,
)
from rpgvxace_mcp.project import Project  # noqa: E402
from rpgvxace_mcp.rubytypes import (  # noqa: E402
    RubyData,
    RubyExtended,
    RubyHash,
    RubyIvars,
    RubyObject,
    RubyStruct,
    RubyUClass,
    RubyUserMarshal,
    printable_text,
)
from rpgvxace_mcp.schema import (  # noqa: E402
    ACE_PROFILE,
    SchemaWalk,
    VX_PROFILE,
    map_filename,
    observe_class_fields,
)
from rpgvxace_mcp.tools import (  # noqa: E402,F401
    db_tools,
    event_tools,
    map_common,
    map_edit_tools,
    map_read_tools,
    map_tile_tools,
    misc_tools,
)
from rpgvxace_mcp.tools.registry import Context, REGISTRY, ToolError  # noqa: E402


class TestEngineProfiles(unittest.TestCase):
    def test_capabilities_match_the_engine(self):
        self.assertTrue(ACE_PROFILE.has_tileset_database)
        self.assertFalse(ACE_PROFILE.has_encounter_areas)
        self.assertTrue(ACE_PROFILE.has_region_layer)
        self.assertTrue(ACE_PROFILE.strings_carry_encoding)

        self.assertFalse(VX_PROFILE.has_tileset_database)
        self.assertTrue(VX_PROFILE.has_encounter_areas)
        self.assertFalse(VX_PROFILE.has_region_layer)
        self.assertFalse(VX_PROFILE.strings_carry_encoding)

    def test_new_map_carries_exactly_this_engine_s_fields(self):
        ace = ACE_PROFILE.new_map(20, 15)
        vx = VX_PROFILE.new_map(20, 15)
        for field in ("tileset_id", "display_name", "note", "specify_battleback",
                      "battleback1_name", "battleback2_name"):
            self.assertTrue(ace.has(field), "Ace map should have %s" % field)
            self.assertFalse(vx.has(field), "VX map should not have %s" % field)
        # The shared fields are on both.
        for field in ("width", "height", "data", "events", "encounter_step"):
            self.assertTrue(ace.has(field))
            self.assertTrue(vx.has(field))
        self.assertEqual(ace.get("data").zsize, 4)
        self.assertEqual(vx.get("data").zsize, 3)

    def test_layer_note_is_derived_from_the_layer_table(self):
        # The prose and the data cannot drift, because the prose is built from
        # the data.
        self.assertTrue(any("four layers" in note for note in ACE_PROFILE.notes))
        self.assertTrue(any("three layers" in note for note in VX_PROFILE.notes))

    def test_ace_stat_note_matches_the_real_field_layout(self):
        # An earlier note claimed actors and items carry a params array. They do
        # not: params is an array on weapons/armors/enemies and a Table on
        # classes. Asserting an engine detail we have not confirmed is the one
        # thing this project must not do.
        note = next(n for n in ACE_PROFILE.notes if "features" in n)
        self.assertIn("weapons, armors and enemies", note)
        self.assertIn("classes a params Table", note)

    def test_absence_hint_explains_the_engine_difference(self):
        self.assertIn("global tileset", VX_PROFILE.absence_hint("tilesets"))
        self.assertIn("region", ACE_PROFILE.absence_hint("areas"))

    def test_unknown_audio_kind_is_refused(self):
        # Falling back to BGM would write an RPG::BGM into an ME or SE slot.
        with self.assertRaises(ValueError) as caught:
            ACE_PROFILE.new_audio_file("bgmm")
        self.assertIn("unknown audio kind", str(caught.exception))

    def test_map_filename_requires_a_real_id(self):
        self.assertEqual(map_filename(5, ".rvdata2"), "Map005.rvdata2")
        # A float would truncate silently and address a different map.
        for bad in (2.9, True, "3", None):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    map_filename(bad, ".rvdata2")
        for bad in (0, -1, 100000):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    map_filename(bad, ".rvdata2")


class TestSchemaObservation(unittest.TestCase):
    def test_shared_object_is_tallied_once(self):
        # Marshal resolves a link to the same Python object, so a value reachable
        # by many paths would otherwise be counted once per path.
        shared = RubyObject("Shared", {"@a": 1})
        fields = observe_class_fields([shared, shared, [shared, shared]])
        self.assertEqual(fields["Shared"]["a"], 1)

    def test_link_shared_graph_does_not_explode(self):
        # 20 levels, each holding the same child twice -- the shape a Marshal
        # file of a few hundred bytes can describe with object links.
        #
        # This has to measure the WORK, not the tally: the tally is protected by
        # its own memo, so counting fields would pass even while the walk did
        # 2**21 visits. nodes_visited is the population that actually grows.
        node: object = RubyObject("Leaf", {"@x": 1})
        for _ in range(20):
            node = [node, node]
        walk = SchemaWalk().visit(node)
        self.assertEqual(walk.fields["Leaf"]["x"], 1)
        self.assertFalse(walk.truncated)
        self.assertLess(walk.nodes_visited, 100,
                        "the walk visited %d values for a 21-node graph"
                        % walk.nodes_visited)

    def test_a_walk_that_hits_its_budget_says_so(self):
        # An honest partial answer beats a partial answer presented as whole.
        wide = [RubyObject("Wide%d" % n, {"@i": n}) for n in range(200)]
        walk = SchemaWalk(budget=20)
        walk.visit(wide)
        self.assertTrue(walk.truncated)
        self.assertEqual(walk.nodes_visited, walk.budget)

    def test_class_variables_do_not_collapse_onto_instance_variables(self):
        fields = observe_class_fields(RubyObject("Foo", {"@x": 1, "@@x": 2}))
        self.assertEqual(sorted(fields["Foo"]), ["@x", "x"])

    def test_wrapper_types_are_walked(self):
        # Each of these used to be a traversal dead end, so anything beneath one
        # was missing from a report presented as observed authority.
        inside = RubyObject("Inside", {"@deep": 1})
        for wrapper in (
            RubyIvars([inside], {"E": True}),
            RubyStruct("AStruct", {"member": inside}),
            RubyUserMarshal("AMarshal", inside),
            RubyData("AData", inside),
            RubyUClass("ASubclass", [inside]),
            RubyExtended(["AModule"], inside),
        ):
            with self.subTest(wrapper=type(wrapper).__name__):
                fields = observe_class_fields(wrapper)
                self.assertIn("Inside", fields,
                              "%s was not walked" % type(wrapper).__name__)

    def test_struct_members_are_tallied_under_the_struct(self):
        fields = observe_class_fields(RubyStruct("Point", {"x": 1, "y": 2}))
        self.assertEqual(sorted(fields["Point"]), ["x", "y"])

    def test_hash_keys_and_values_are_both_walked(self):
        hash_obj = RubyHash([(RubyObject("Key", {"@k": 1}),
                              RubyObject("Value", {"@v": 2}))])
        fields = observe_class_fields(hash_obj)
        self.assertIn("Key", fields)
        self.assertIn("Value", fields)


class TestResponseTextSafety(unittest.TestCase):
    """A corrupt name must not be able to end the session."""

    def test_printable_text_replaces_lone_surrogates(self):
        self.assertEqual(printable_text("ok"), "ok")
        cleaned = printable_text("Caf\udce9")
        cleaned.encode("utf-8")  # must not raise

    def test_class_names_from_a_file_survive_json_encoding(self):
        # marshal decodes names with surrogateescape, so a corrupt byte in a
        # class name arrives as a lone surrogate. json.dumps emits it happily
        # and the UTF-8 transport then cannot encode it.
        obj = RubyObject("Bad\udc80Name", {"@a\udc81": 1})
        payload = to_json(obj, TextCodec("utf-8"))
        text = json.dumps(payload, ensure_ascii=False)
        text.encode("utf-8")  # must not raise
        self.assertNotIn("\udc80", text)


class TestUnmodelledEngine(unittest.TestCase):
    """XP opens with a flag, described with the closest model available."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rpgvxace-xp-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "Data").mkdir(parents=True)
        (self.tmp / "Game.rxproj").write_text("xp", encoding="ascii")
        (self.tmp / "Data" / "Actors.rxdata").write_bytes(marshal_rb.dump([None]))
        (self.tmp / "Data" / "MapInfos.rxdata").write_bytes(
            marshal_rb.dump(RubyHash()))
        self.project = Project(self.tmp, allow_other_engines=True)
        self.ctx = Context(self.project)

    def test_documents_say_which_model_is_being_used(self):
        for document in (data_format_reference(self.project),
                         command_reference_document(self.project)):
            self.assertIn("described with the RPG Maker VX model", document)
            self.assertIn("unconfirmed", document)

    def test_command_reference_uses_the_modelled_engine_table(self):
        # Keying off the detected engine fell through to Ace's table while the
        # heading said VX.
        document = command_reference_document(self.project)
        self.assertIn("| 136 | Change Encounter |", document)
        self.assertNotIn("| 285 |", document)

    def test_creating_a_map_is_refused(self):
        # Reading is fine; inventing a file whose field set we do not know is not.
        with self.assertRaises(ToolError) as caught:
            REGISTRY["map_edit"].handler(
                self.ctx, {"op": "create", "name": "Somewhere"})
        self.assertIn("field set for a new map is not known", str(caught.exception))


class TestSchemaReportProvenance(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rpgvxace-prov-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        build(self.tmp / "project")
        self.ctx = Context(Project(self.tmp / "project"))

    def test_advisory_notes_travel_with_the_data(self):
        report = REGISTRY["project_info"].handler(
            self.ctx, {"include_schema": True})["schema"]
        self.assertIn("Advisory", report["tile_id_ranges_note"])
        self.assertIn("Advisory", report["tileset_flag_bits_note"])

    def test_the_report_names_this_project_s_engine(self):
        report = REGISTRY["project_info"].handler(
            self.ctx, {"include_schema": True})["schema"]
        self.assertIn("RPG Maker VX Ace", report["note"])
        self.assertNotIn("RGSS2", report["note"])


class TestTheReferenceAgreesWithTheTables(unittest.TestCase):
    """Every shared-value list in the document comes from the live table.

    Hand-typing them is what let one of the six drift into contradicting the
    module it describes. Generated, they cannot.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="docs-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        build(self.tmp / "ace", "vx_ace")
        build(self.tmp / "vx", "vx")
        self.ace = Project(self.tmp / "ace")
        self.vx = Project(self.tmp / "vx")

    def document(self, project):
        return command_reference_document(project)

    def test_every_switch_operation_is_named(self):
        from rpgvxace_mcp.commands import SWITCH_OPERATIONS
        text = self.document(self.ace)
        for code, name in SWITCH_OPERATIONS.items():
            self.assertIn(str(code), text)
            self.assertIn(name, text, "%d -> %s missing" % (code, name))

    def test_every_variable_operation_is_named(self):
        from rpgvxace_mcp.commands import VARIABLE_OPERATIONS
        text = self.document(self.ace)
        for code in VARIABLE_OPERATIONS:
            self.assertIn(str(code), text)

    def test_every_comparison_is_named(self):
        from rpgvxace_mcp.commands import COMPARISONS
        text = self.document(self.ace)
        for name in COMPARISONS.values():
            self.assertIn(name, text, "comparison %s missing" % name)

    def test_the_ace_document_does_not_claim_the_unknown_operands(self):
        """The drift, stated as a test.

        `commands.py` says VX Ace reorganised operand types above 2 and that
        this server does not claim to know the layout. The document said
        "Operand types: 0 constant, 1 variable, 2 random range." with no
        qualification, which reads as a closed set.
        """
        text = self.document(self.ace)
        section = text[text.index("Shared parameter values"):]
        marker = section[:section.index("##", 3)] if "##" in section[3:] else section
        self.assertTrue(
            "not known" in marker or "read a working" in marker
            or "confirmed" in marker,
            "the VX Ace reference states the operand types without saying "
            "the higher ones are not modelled for this engine")

    def test_the_vx_document_names_the_operands_vx_really_has(self):
        from rpgvxace_mcp.commands import VX_VARIABLE_OPERANDS
        text = self.document(self.vx)
        for name in VX_VARIABLE_OPERANDS.values():
            self.assertIn(name, text,
                          "VX operand %r is in the table and not in the VX "
                          "document" % name)

    def test_the_two_documents_differ(self):
        """An engine-keyed document whose sections are identical is not one."""
        self.assertNotEqual(self.document(self.ace), self.document(self.vx))

    def test_a_condition_type_is_not_typed_out_twice(self):
        """The Conditional Branch types come from the COND_* constants."""
        from rpgvxace_mcp import commands
        names = {value: name for name, value in vars(commands).items()
                 if name.startswith("COND_")}
        self.assertTrue(names, "no COND_ constants to generate from")
        text = self.document(self.ace)
        for code in names:
            self.assertIn(str(code), text)


class TestToolModulesAreLeaves(unittest.TestCase):
    """No tool module takes vocabulary from a sibling tool module.

    Shared facts belong in `schema` (what the format is), `analysis` (how a
    project is walked) or `registry` (what a tool call looks like) -- all of
    which sit below `tools/` and are imported by every one of them. A fact
    kept in a leaf makes that leaf un-splittable and the graph unreadable.

    Or in a module here that registers no tool of its own: `map_common` is
    the one, and the test below permits it by asking whether a module
    registers a tool rather than by naming it -- so the enforced rule is
    one module wider than the three named above.
    """

    def folder(self):
        root = Path(__file__).resolve().parent.parent / "src"
        return root / "rpgvxace_mcp" / "tools"

    def registers_a_tool(self, path):
        """Whether a module under tools/ actually registers one.

        This is the line between a leaf and shared infrastructure, and it is
        computed rather than listed: `registry` and `pattern_safety` register
        nothing and are imported by the modules that do, which is the design.
        A hard-coded exception list would have to be maintained, and the next
        helper added would look like a violation.
        """
        return "@tool(" in path.read_text(encoding="utf-8")

    def tool_modules(self):
        """The leaves: modules under tools/ that register a tool."""
        return sorted(p for p in self.folder().glob("*.py")
                      if p.name != "__init__.py" and self.registers_a_tool(p))

    def shared_modules(self):
        return {p.stem for p in self.folder().glob("*.py")
                if p.name != "__init__.py" and not self.registers_a_tool(p)}

    def test_the_scan_finds_the_tool_modules(self):
        """So a broken scan cannot make the rule below vacuously true."""
        names = {p.stem for p in self.tool_modules()}
        self.assertIn("event_tools", names)
        # The three tool modules `map_tools` became, beside `map_common`,. Named individually
        # because the split is the thing this suite made possible.
        for half in ("map_read_tools", "map_edit_tools", "map_tile_tools"):
            self.assertIn(half, names)
        self.assertGreater(len(names), 5)

    def test_no_tool_module_imports_from_a_sibling(self):
        import ast

        crossings = []
        shared = self.shared_modules()
        self.assertIn("registry", shared, "the scan lost the shared layer")
        for path in self.tool_modules():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                # level 1 and a bare module name is a sibling inside tools/.
                if node.level != 1 or not node.module:
                    continue
                if node.module in shared:
                    continue
                names = ", ".join(alias.name for alias in node.names)
                crossings.append("%s imports %s from .%s"
                                 % (path.name, names, node.module))
        self.assertEqual(
            crossings, [],
            "a tool module is a leaf: move the shared name down to schema, "
            "analysis or registry.\n  " + "\n  ".join(crossings))

    def test_the_shared_names_are_reachable_where_they_moved(self):
        """So this suite cannot pass by the names simply disappearing."""
        from rpgvxace_mcp.schema import (STRUCTURAL_MAP_FIELDS,
                                         unreadable_events_note)
        self.assertIn("events", STRUCTURAL_MAP_FIELDS)
        self.assertIsNone(unreadable_events_note(RubyHash()))
        self.assertIsNotNone(unreadable_events_note(42))


class TestOneBlankLineConvention(unittest.TestCase):
    """Two blank lines between top-level definitions, everywhere.

    The five modules the map-tools split created used one where every
    pre-existing module used two, so a reader moving between them read the
    same structure two ways -- and a diff of a moved function showed a
    whitespace change on top of the move. PEP 8 settles which one; this
    settles that there is only one.

    Checked with a regular expression rather than a linter, because this
    project has no third-party dependencies and adding one for whitespace
    would be a worse trade than a six-line test.
    """

    def modules(self):
        # Globbed, not listed: `scripts/` was one named file, so
        # `guard_table.py` arrived with 2,000 lines nothing checked.
        #
        # And `tests/` as well, which this said "everywhere" without
        # reading. The three files the bounds-guard split produced had 37
        # classes with no blank line above them at all, because the
        # generator that wrote them joined the blocks with one newline --
        # so the rule was blind to the directory whose files this round
        # rewrote most.
        root = Path(__file__).resolve().parent.parent
        # `rglob` for all three: `tests/fixtures/build_project.py` sits
        # one directory down and was unread while this said "everywhere".
        return (sorted((root / "src" / "rpgvxace_mcp").rglob("*.py"))
                + sorted((root / "tests").rglob("*.py"))
                + sorted((root / "scripts").rglob("*.py")))

    def blanks_above(self, path):
        """How many blank lines sit above each top-level definition.

        Read with `ast` rather than a regular expression. The expression
        this replaced matched `[^\n]\n\n(?=def |class |@|CONSTANT)`, which
        can only ever see *exactly one* blank line before a bare
        definition -- so zero blank lines were invisible, and so was any
        definition introduced by a comment block, which is most of the
        constants in this project. It found 37 violations in `tests/` when
        the directory was added and still could not see eight in
        `pattern_safety.py`.

        A comment block immediately above a definition belongs to it, so
        the count is taken above the comment.
        """
        import ast
        lines = path.read_text(encoding="utf-8").split("\n")
        tree = ast.parse("\n".join(lines))
        out = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                top = min([d.lineno for d in node.decorator_list]
                          + [node.lineno])
            else:
                # Definitions only. A *group* of related constants runs
                # together on purpose -- the command codes, the tag bytes --
                # and it is the group that is separated by two blank lines,
                # not each member of it. Requiring two above every constant
                # flagged 363 sites, almost all of them correct style, which
                # is a rule nobody would keep. The constants are covered by
                # the second test below, which asks the narrower question
                # they actually got wrong.
                continue
            while top > 1 and lines[top - 2].lstrip().startswith("#"):
                top -= 1
            if top <= 1:
                continue
            blank = 0
            while top - 2 - blank >= 0 and not lines[top - 2 - blank].strip():
                blank += 1
            out.append((top, blank))
        return out

    def test_every_top_level_definition_has_two_blank_lines_above_it(self):
        # Constants as well as definitions: four constants placed after a
        # function had one blank line where the rest of the project uses
        # two, and the first version of this could not see them.
        wrong = []
        for path in self.modules():
            for line, blank in self.blanks_above(path):
                if blank != 2:
                    wrong.append("%s:%d (%d)" % (path.name, line, blank))
        self.assertEqual(
            wrong, [],
            "these top-level definitions do not have two blank lines above "
            "them, with what they do have in brackets: " + ", ".join(wrong))

    def test_a_constant_after_code_has_two_blank_lines_above_it(self):
        """The narrower question, for the shape constants got wrong.

        A *group* of related constants runs together on purpose, so the
        definition rule above covers functions and classes only. What went
        wrong with constants was four of them placed after a function with
        one blank line where the project uses two -- and one blank line is
        a shape a regular expression can ask about exactly.
        """
        thin = []
        for path in self.modules():
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(
                    r"[^\n]\n\n(?=[A-Z_]{2,}\s*[:=])", text):
                line = text.count("\n", 0, match.end()) + 1
                thin.append("%s:%d" % (path.name, line))
        self.assertEqual(thin, [],
                         "these constants have one blank line above them "
                         "and the rest of the project uses two: "
                         + ", ".join(thin))

    def test_no_module_has_three_blank_lines_in_a_row(self):
        fat = []
        for path in self.modules():
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(r"\n{4}", text):
                line = text.count("\n", 0, match.start()) + 1
                fat.append("%s:%d" % (path.name, line))
        self.assertEqual(fat, [],
                         "three or more blank lines in a row: "
                         + ", ".join(fat))


class TestTheAnalysisLayeringIsAcyclic(unittest.TestCase):
    """Two ordered sets of modules, checked rather than asserted in prose.

    `analysis` was one module split into `walk <- references <- lint`
    because the dependency boundary and the criticality boundary coincided:
    everything the four map tool modules import lives in `walk`, everything
    the two delete paths import lives in `references`, and `misc_tools` is
    the only importer of anything in `lint`. That order is the whole reason
    the split is worth having, so it is worth a test -- a cycle would make
    the files one file again with extra steps.

    `lint` then became seven modules of its own with the same argument, so
    it has the same table. One checker over both, because the first version
    of this class hard-coded three names and a directory.

    Both directions. The first version asked only whether every name in the
    table was a real module, so a module added to the package was invisible
    to the layering it had just joined -- which is how `lint/` could have
    grown an eighth file that imported `report`.
    """

    # Which siblings each module may import. Lower may not reach higher.
    ANALYSIS = {
        "walk": set(),
        "references": {"walk"},
        "lint": {"walk", "references"},
    }
    LINT = {
        "findings": set(),
        "maps": {"findings"},
        "pages": {"findings"},
        # `assets` as well, since `check_commands` does the command
        # asset checks in the loop it already runs -- the two rules
        # that need a command list read a strict subset of what
        # that loop holds, and a second traversal is charged what
        # the first was.
        "command_rules": {"findings", "maps", "assets"},
        "assets": {"findings"},
        "database": {"findings", "assets"},
        "report": {"findings", "maps", "pages", "command_rules", "assets",
                   "database"},
    }

    # And the package root, which three docstrings assert and nothing read:
    # `paths.py:3` calls itself "the bottom of this module's layering",
    # `backups.py` says it reads `paths`, and `project.py` reads both. Only
    # the modules named here are constrained -- `marshal_rb`, `convert`,
    # `rubytypes`, `schema` and `commands` sit beside them without an order
    # this test can state, so they are excluded rather than pretended about.
    ROOT_MODULES = {
        "paths": set(),
        "backups": {"paths"},
        "project": {"paths", "backups"},
    }

    def tables(self):
        """Each layering table with the directory it describes."""
        package = (Path(__file__).resolve().parent.parent / "src"
                   / "rpgvxace_mcp")
        return (("rpgvxace_mcp", self.ROOT_MODULES, package),
                ("analysis", self.ANALYSIS, package / "analysis"),
                ("lint", self.LINT, package / "analysis" / "lint"))

    def found(self, where):
        """Every module in a directory: a file, or a package with an init."""
        out = {}
        for path in sorted(where.iterdir()):
            if path.suffix == ".py" and path.name != "__init__.py":
                out[path.stem] = [path]
            elif path.is_dir() and (path / "__init__.py").is_file():
                out[path.name] = sorted(path.glob("*.py"))
        return out

    def sibling_imports(self, table, where, paths):
        """Which siblings in `table` these files reach, read from source.

        A module one directory down names a parent's sibling with one more
        dot, so the level that counts as "sibling" depends on where the file
        is. `lint/report.py` reaching `..walk` is `lint` reaching `walk`.
        """
        import ast
        out = set()
        for path in paths:
            level = 1 if path.parent == where else 2
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom) or node.level != level:
                    continue
                if node.module in table:
                    out.add(node.module)
                for alias in node.names:
                    if alias.name in table:
                        out.add(alias.name)
        return out

    def test_no_module_imports_a_layer_above_itself(self):
        for label, table, where in self.tables():
            modules = self.found(where)
            for name, allowed in table.items():
                with self.subTest(package=label, module=name):
                    reached = self.sibling_imports(
                        table, where, modules[name]) - {name}
                    self.assertEqual(
                        sorted(reached - allowed), [],
                        "%s/%s imports a layer above it, so the split has a "
                        "cycle" % (label, name))

    def test_the_bottom_layer_imports_no_sibling(self):
        """Stated separately: it is what makes each order an order."""
        for label, table, where in self.tables():
            bottom = [name for name, allowed in table.items() if not allowed]
            self.assertEqual(len(bottom), 1,
                             "%s has %d bottom layers" % (label, len(bottom)))
            with self.subTest(package=label, module=bottom[0]):
                self.assertEqual(
                    self.sibling_imports(table, where,
                                         self.found(where)[bottom[0]]),
                    set())

    def test_the_table_and_the_directory_name_the_same_modules(self):
        """Both directions, so neither can outlive the other.

        A name in the table and no module is a rename nobody finished. A
        module and no name in the table is worse: it is a file the layering
        does not constrain at all, and it reads exactly like a file that
        passes.
        """
        for label, table, where in self.tables():
            if table is self.ROOT_MODULES:
                # The root package holds modules with no stated order --
                # see `ROOT_MODULES`. What is checked there is the other
                # direction only: every name in the table is a real module.
                missing = sorted(set(table) - set(self.found(where)))
                self.assertEqual(missing, [], "named and not there: "
                                              + ", ".join(missing))
                continue
            with self.subTest(package=label):
                self.assertEqual(sorted(self.found(where)), sorted(table))

    def test_the_package_re_exports_what_the_tools_import(self):
        """`from ..analysis import X` keeps working, for every X in use.

        The split is about how this code is read, not about making nine
        tool modules learn which of three files a name lives in -- so the
        re-export is part of the claim and is checked the same way.
        """
        import ast
        from rpgvxace_mcp import analysis
        tools = (Path(__file__).resolve().parent.parent / "src"
                 / "rpgvxace_mcp" / "tools")
        wanted = set()
        for path in sorted(tools.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (isinstance(node, ast.ImportFrom)
                        and node.module == "analysis"):
                    wanted.update(alias.name for alias in node.names)
        missing = sorted(n for n in wanted if not hasattr(analysis, n))
        self.assertEqual(missing, [],
                         "the tools layer imports these from `analysis` and "
                         "the package does not re-export them")

    def test_only_one_tool_module_imports_the_linter(self):
        """The claim the whole boundary rests on, checked.

        `analysis` was split where it was because the dependency boundary
        and the criticality boundary coincided: `walk` is imported by four
        map tool modules, `references` by the two delete paths, and `lint`
        by `misc_tools` alone. The first two are load-bearing on every read
        and every write; the third runs when a caller asks for a report.

        That was a sentence in two docstrings and nothing else, which is
        the state the layering table was in before this class. A second
        importer would not break anything -- it would quietly make the
        linter load-bearing, and the argument for the boundary would be
        false while still being written down.
        """
        import ast
        from rpgvxace_mcp import analysis
        of_the_linter = set(vars(analysis.lint))
        # A sanity check on the definition above, not on the code: if the
        # linter's own namespace came back empty this test would pass by
        # finding no importers of anything.
        self.assertIn("validate", of_the_linter)
        tools = (Path(__file__).resolve().parent.parent / "src"
                 / "rpgvxace_mcp" / "tools")
        importers = {}
        for path in sorted(tools.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                if node.module not in ("analysis", "analysis.lint"):
                    continue
                took = sorted({alias.name for alias in node.names}
                              & of_the_linter)
                if took:
                    importers.setdefault(path.stem, set()).update(took)
        self.assertEqual(
            sorted(importers), ["misc_tools"],
            "the linter is imported by more than the report tool now, so "
            "the reason `analysis` is split where it is no longer holds: "
            + "; ".join("%s takes %s" % (name, ", ".join(sorted(took)))
                        for name, took in sorted(importers.items())))

    def defined_below(self):
        """Every name defined anywhere under `analysis/`, from the source.

        Read rather than introspected: `__module__` tells you where a
        function or a class came from and says nothing at all about a
        constant or an alias, so an introspecting version of this called
        `MAX_FINDINGS` and `_seq` dangling re-exports.
        """
        import ast
        root = (Path(__file__).resolve().parent.parent / "src"
                / "rpgvxace_mcp" / "analysis")
        found = set()
        paths = sorted(root.rglob("*.py"))
        self.assertGreater(len(paths), 5, "read almost nothing")
        for path in paths:
            if path.name == "__init__.py":
                continue
            for node in ast.parse(path.read_text(encoding="utf-8")).body:
                if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                    found.add(node.name)
                elif isinstance(node, ast.AnnAssign):
                    found.add(node.target.id)
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            found.add(target.id)
                        elif isinstance(target, ast.Tuple):
                            found.update(e.id for e in target.elts
                                         if isinstance(e, ast.Name))
        return found

    def test_every_public_name_is_re_exported(self):
        """The direction that was missing, and it found one.

        The list in `analysis/__init__` is hand-written, and only the
        tools-layer direction above was checked -- so a name no tool module
        happened to import yet could be public, documented and unreachable
        by the name the package advertises. `check_list_terminator` was.
        """
        from rpgvxace_mcp import analysis
        missing = sorted(name for name in self.defined_below()
                         if not name.startswith("_")
                         and not hasattr(analysis, name))
        self.assertEqual(
            missing, [],
            "these are public in a submodule and not reachable as "
            "`analysis.X`: " + ", ".join(missing))

    def test_every_re_exported_name_is_really_there(self):
        """No dangling re-export, including the private ones.

        The list carries private names deliberately -- the guard suites
        reach the linter's internals, which is the point of a guard test --
        so it cannot be derived from what is public. What it can be is
        checked: each name resolves to something one of the three
        submodules actually defines.
        """
        from rpgvxace_mcp import analysis
        # The submodule names themselves are re-exported on purpose, and
        # `annotations` is the future import.
        modules = {"walk", "references", "lint"}
        exported = {name for name in vars(analysis)
                    if not name.startswith("__")}
        dangling = sorted(exported - self.defined_below() - modules
                          - {"annotations"})
        self.assertEqual(dangling, [],
                         "re-exported and defined nowhere below: "
                         + ", ".join(dangling))

    def test_the_package_re_exports_all_of_the_linter(self):
        """Two hand-written lists of one set, and they had drifted.

        `analysis/__init__` and `lint/__init__` both name the linter's
        exports, and eleven names were in the second and not the first --
        every one of them private, so `test_every_public_name_is_re_exported`
        above could not see it. The consequence was arbitrary:
        `analysis._check_system` resolved and `analysis._check_autorun` did
        not, for no reason a reader could find.
        """
        from rpgvxace_mcp import analysis
        from rpgvxace_mcp.analysis import lint
        modules = {"assets", "command_rules", "database", "findings",
                   "maps", "pages", "report"}
        exported = {name for name in vars(lint)
                    if not name.startswith("__")} - modules - {"annotations"}
        self.assertGreater(len(exported), 30, "read almost nothing")
        missing = sorted(name for name in exported
                         if not hasattr(analysis, name))
        self.assertEqual(
            missing, [],
            "`lint` re-exports these and `analysis` does not, so which "
            "spelling works is arbitrary: " + ", ".join(missing))

    def test_the_linter_is_reachable_by_the_name_it_had(self):
        """`from ..analysis import validate` survived becoming a package.

        Seven modules behind one name, and `misc_tools` imports the name.
        Without this the re-export test above could pass on a package that
        exported nothing a caller could run.
        """
        from rpgvxace_mcp import analysis
        for name in ("validate", "VALIDATE_SCOPES", "MAX_FINDINGS",
                     "Finding", "check_assets", "check_database"):
            with self.subTest(name=name):
                self.assertTrue(hasattr(analysis.lint, name), name)
                self.assertTrue(hasattr(analysis, name), name)


class TestEveryMixinContractHolds(unittest.TestCase):
    """A mixin declares what it needs from its host, and both are checked.

    `BackupStore` is half of `Project`: the backup store was a quarter of a
    thousand-line class, read for its own reasons and scrolled past to
    reach the loading and saving it sat between. It is a mixin rather than
    a collaborator because `restore_backup` performs a sequence -- snapshot,
    staleness-check, copy, cache-evict, flag-clear, map-id-invalidate --
    over state whose invariants belong to `Project`, and an object handed
    all of that could still be called with a `Project` mid-mutation.

    `CraftedMap` in `tool_case` is the same shape one layer down, and it is
    why this is a table: three tests written once per mixin is how the
    second one came to declare a name it defines itself and omit one it
    assigns.

    What makes the trade safe is that the contract is checked in three
    directions, and the third is the one almost nobody writes: everything
    declared exists, everything reached is declared, and nothing declared
    is unreached. A mixin reading a name nobody defines fails when the
    method runs -- for `restore_backup` that is *after* a safety snapshot
    has been taken, and for `assert_bounded` it is inside a timing
    assertion, where the failure reads as a bound that did not hold.
    """

    # Each mixin: its source file, its class, the tuple it declares, and a
    # host object to check the names against.
    MIXINS = (
        ("src/rpgvxace_mcp/backups.py", "BackupStore", "NEEDS_FROM_PROJECT"),
        ("tests/tool_case.py", "CraftedMap", "NEEDS_FROM_HOST"),
    )

    def source(self, where):
        return (Path(__file__).resolve().parent.parent
                / where).read_text(encoding="utf-8")

    def declared(self, where, name, tuple_name):
        import ast
        for node in ast.parse(self.source(where)).body:
            if isinstance(node, ast.ClassDef) and node.name == name:
                for inner in node.body:
                    if (isinstance(inner, ast.Assign)
                            and getattr(inner.targets[0], "id", "")
                            == tuple_name):
                        return [e.value for e in inner.value.elts]
        raise AssertionError("%s has no %s" % (name, tuple_name))

    def reached(self, where, name):
        """Every `self.<name>` the mixin reads that it does not define."""
        import ast
        cls = next(node for node in ast.parse(self.source(where)).body
                   if isinstance(node, ast.ClassDef) and node.name == name)
        out = {node.attr for node in ast.walk(cls)
               if isinstance(node, ast.Attribute)
               and isinstance(node.value, ast.Name)
               and node.value.id == "self"}
        own = {node.name for node in cls.body
               if isinstance(node, ast.FunctionDef)}
        own |= {target.id for node in cls.body
                if isinstance(node, ast.Assign)
                for target in node.targets if isinstance(target, ast.Name)}
        own |= {node.target.id for node in cls.body
                if isinstance(node, ast.AnnAssign)}
        return out - own

    def test_each_mixin_reaches_something(self):
        """So the two tests below cannot pass by reading nothing."""
        for where, name, _ in self.MIXINS:
            with self.subTest(mixin=name):
                self.assertTrue(self.reached(where, name),
                                "%s reaches no host name at all" % name)

    def test_everything_reached_is_declared(self):
        """The direction that keeps a stated contract from going stale."""
        for where, name, tuple_name in self.MIXINS:
            with self.subTest(mixin=name):
                undeclared = sorted(self.reached(where, name)
                                    - set(self.declared(where, name,
                                                        tuple_name)))
                self.assertEqual(
                    undeclared, [],
                    "%s reaches these and does not declare them, so its "
                    "contract reads as checked and is not: %s"
                    % (name, ", ".join(undeclared)))

    def test_nothing_declared_is_unreached(self):
        """A name in the list the mixin does not read is the same staleness.

        This is what rejected `root` and `read_only` from `BackupStore` --
        reached only through `work_dir()` and `_assert_writable()`, so
        listing them described those methods' business as the class's -- and
        `WHAT` from `CraftedMap`, which the mixin defines itself.
        """
        for where, name, tuple_name in self.MIXINS:
            with self.subTest(mixin=name):
                idle = sorted(set(self.declared(where, name, tuple_name))
                              - self.reached(where, name))
                self.assertEqual(idle, [],
                                 "%s declares these and never reaches them: "
                                 "%s" % (name, ", ".join(idle)))

    def test_everything_the_backup_mixin_needs_is_on_a_real_project(self):
        """The one that needs an instance: `__init__` sets most of them.

        Only `BackupStore`, because `CraftedMap`'s host is a `TestCase` and
        building one to ask is what this test would be doing anyway.
        """
        import shutil
        import sys
        import tempfile
        from pathlib import Path as P
        sys.path.insert(0, str(P(__file__).resolve().parent / "fixtures"))
        from build_project import build
        from rpgvxace_mcp.backups import BackupStore
        from rpgvxace_mcp.project import Project
        tmp = P(tempfile.mkdtemp(prefix="rpgvxace-mixin-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        build(tmp / "project")
        project = Project(tmp / "project")
        missing = sorted(name for name in BackupStore.NEEDS_FROM_PROJECT
                         if not hasattr(project, name))
        self.assertEqual(missing, [],
                         "the mixin says it needs these and `Project` does "
                         "not have them: " + ", ".join(missing))


class TestImportBlocksAreInOrder(unittest.TestCase):
    """Names inside a multi-line import block are sorted, and named once.

    Found by reading, in ten of twelve tool modules at once: `opt_int_default`
    above `opt_int` in six of them, `decoded` below `tool` in four, and a
    run of recently-added names pushed to the head of the block in five.
    Every one of them is the same thing -- a name appended where the diff
    was smallest rather than where a reader would look for it -- and the
    cost is that a block of thirty names stops being scannable, so the
    thirty-first gets appended too. `misc_tools` was importing a name
    nothing had read since the module was split.

    Two rules, no third-party linter: this project has no dependencies and
    adding one to sort names would be a worse trade than a short test.

    Plain `sorted()`, which is what the blocks already in order use:
    `Location` < `MAX_WALK_STEPS` < `WalkBudget` < `_db_at` < `iter_maps`.
    """

    def modules(self):
        root = Path(__file__).resolve().parent.parent
        found = (sorted((root / "src").rglob("*.py"))
                 + sorted((root / "tests").glob("*.py"))
                 + sorted((root / "scripts").glob("*.py")))
        self.assertGreater(len(found), 30, "scanned almost nothing")
        return found

    def blocks(self, path):
        """Every `from X import (a, b, c)` written one name per line."""
        import ast
        import re
        text = path.read_text(encoding="utf-8")
        lines = text.split("\n")
        for node in ast.parse(text).body:
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.end_lineno <= node.lineno:
                continue
            names = []
            for line in lines[node.lineno:node.end_lineno - 1]:
                match = re.fullmatch(r"    ([A-Za-z_][A-Za-z0-9_]*),", line)
                if match is None:
                    names = None
                    break
                names.append(match.group(1))
            # A block with a comment or an `as` in it is left alone: the
            # comment is there to group, which is a reason to be unsorted.
            if names:
                yield node.lineno, names

    def test_every_multiline_import_block_is_sorted(self):
        out = []
        for path in self.modules():
            for line, names in self.blocks(path):
                if names != sorted(names):
                    wrong = next(a for a, b in zip(names, sorted(names))
                                 if a != b)
                    out.append("%s:%d (%s)" % (path.name, line, wrong))
        self.assertEqual(out, [], "these import blocks are not sorted: "
                                  + ", ".join(out))

    def test_a_module_is_imported_from_once(self):
        """`from ..commands import (...)` and then `from ..commands import x`.

        Two statements naming one module put the same names in two places to
        look, and that is how the second one goes unread: `map_edit_tools`
        had `reference_preview` on its own four lines above the block that
        already imported two other names from `..analysis`.
        """
        import ast
        out = []
        for path in self.modules():
            seen = {}
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if not isinstance(node, ast.ImportFrom):
                    continue
                where = "." * node.level + (node.module or "")
                if where in seen:
                    out.append("%s:%d and :%d both import from %s"
                               % (path.name, seen[where], node.lineno, where))
                seen[where] = node.lineno
        self.assertEqual(out, [], "; ".join(out))


class TestTheTablesKeyedOffOneListAgree(unittest.TestCase):
    """A second table of the same keys is a second thing to keep in step.

    Three of these, each found by reading and each silent when wrong:

    `_conditions` in `map_read_tools` hand-wrote the six page-condition
    flags as a run of `if`s. `PAGE_CONDITION_FLAGS` exists, its own comment
    says it is there so "the field docs, the constructor and the validate
    rule agree", and this was the fourth reader -- the one that agreed by
    coincidence. A seventh flag would have been documented, constructed and
    validated, and not described.

    `SUMMARY_FIELDS` in `db_tools` is keyed by table name beside
    `ALL_DATABASE_TABLES`, which is derived from the file registry. Its
    read is `SUMMARY_FIELDS.get(table, [])`, so a table the schema gains
    lists with no summary columns and says nothing.

    `pattern_safety` keys three tables by the same eight regex category
    names, and both readers fail open in the refuse-more direction -- so a
    ninth category in one table and not the others degrades quietly, on the
    path that decides whether a pattern is safe to run.
    """

    def test_every_page_condition_flag_has_a_sentence(self):
        from rpgvxace_mcp.schema import PAGE_CONDITION_FLAGS
        from rpgvxace_mcp.tools.map_read_tools import _CONDITION_SENTENCES
        self.assertEqual(sorted(_CONDITION_SENTENCES),
                         sorted(PAGE_CONDITION_FLAGS))

    def test_every_summary_table_is_a_table(self):
        from rpgvxace_mcp.schema import ALL_DATABASE_TABLES
        from rpgvxace_mcp.tools.db_tools import SUMMARY_FIELDS
        self.assertEqual(sorted(SUMMARY_FIELDS), sorted(ALL_DATABASE_TABLES),
                         "a table with no summary columns lists with none "
                         "and says nothing about it")

    def test_every_regex_category_is_in_every_table(self):
        from rpgvxace_mcp.tools import pattern_safety
        names = sorted(pattern_safety._CATEGORIES)
        self.assertEqual(len(names), 8, names)
        for pair in pattern_safety._CATEGORY_DISJOINT:
            with self.subTest(pair=sorted(pair)):
                self.assertEqual(sorted(set(pair) - set(names)), [],
                                 "this pair names a category no table has")

    def test_each_category_test_and_sample_agree(self):
        """The sample has to be a character its own test accepts.

        Two tables meant `CATEGORY_SPACE` could have had a sample that is
        not a space, and the shape budget would have measured the wrong
        alphabet with nothing failing.
        """
        from rpgvxace_mcp.tools import pattern_safety
        for name, (test, sample) in pattern_safety._CATEGORIES.items():
            with self.subTest(category=name):
                self.assertTrue(test(sample),
                                "%s samples %r, which it rejects"
                                % (name, sample))


class TestTheMainBlockIsLast(unittest.TestCase):
    """`if __name__ == "__main__"` is the last statement in every suite.

    Module statements run in order, so a `unittest.main()` call above a
    class definition exits before that class exists -- and running the file
    directly then reports success having skipped everything below it. pytest
    collects either way, which is why it goes unnoticed.

    Two files carry a comment saying the block is last "deliberately", and
    in one of them it was not: `test_guards_findings.py` had two classes
    after it, including one added the same round. A comment asserting the
    property it does not have is the shape this project treats as a bug, so
    this is the test rather than a third copy of the comment.
    """

    def suites(self):
        root = Path(__file__).resolve().parent
        found = sorted(path for path in root.glob("test_*.py"))
        self.assertGreater(len(found), 5, "found almost no suites")
        return found

    def test_the_main_block_is_the_last_statement(self):
        import ast
        wrong = []
        for path in self.suites():
            body = ast.parse(path.read_text(encoding="utf-8")).body
            where = [index for index, node in enumerate(body)
                     if isinstance(node, ast.If)
                     and "__main__" in ast.unparse(node.test)]
            if not where:
                continue
            after = [node for node in body[where[0] + 1:]
                     if isinstance(node, (ast.ClassDef, ast.FunctionDef))]
            if after:
                wrong.append("%s (%d definitions after it: %s)"
                             % (path.name, len(after),
                                ", ".join(n.name for n in after)))
        self.assertEqual(
            wrong, [],
            "running these files directly skips what is below the "
            "`unittest.main()` call: " + "; ".join(wrong))
# Last in the file, deliberately. It used to sit mid-module,
# and module statements run in order -- so `python tests/test_schema.py`
# called `unittest.main()` (which exits) before the classes below
# it existed, and reported success having skipped them. pytest
# collects either way, which is why nobody noticed.
if __name__ == "__main__":
    unittest.main()
