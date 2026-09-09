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
    VX_PROFILE,
    SchemaWalk,
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
        # The three halves map_tools was split into. Named individually
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
        root = Path(__file__).resolve().parent.parent
        return (sorted((root / "src" / "rpgvxace_mcp").rglob("*.py"))
                + [root / "scripts" / "prove_guards.py"])

    def test_every_top_level_definition_has_two_blank_lines_above_it(self):
        thin = []
        for path in self.modules():
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(r"[^\n]\n\n(?=(?:def |class |@)\S)",
                                     text):
                line = text.count("\n", 0, match.end()) + 1
                thin.append("%s:%d" % (path.name, line))
        self.assertEqual(thin, [],
                         "these top-level definitions have one blank line "
                         "above them and the rest of the project uses two: "
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
    """`walk <- references <- lint`, checked rather than asserted in prose.

    The module was split because the dependency boundary and the criticality
    boundary coincided: everything the four map tool modules import lives in
    `walk`, everything the two delete paths import lives in `references`,
    and `misc_tools` is the only importer of anything in `lint`. That order
    is the whole reason the split is worth having, so it is worth a test --
    a cycle would make the three files one file again with extra steps.
    """

    # Which siblings each module may import. Lower may not reach higher.
    ALLOWED = {
        "walk": set(),
        "references": {"walk"},
        "lint": {"walk", "references"},
    }

    def package(self):
        return (Path(__file__).resolve().parent.parent / "src"
                / "rpgvxace_mcp" / "analysis")

    def sibling_imports(self, name):
        """Which sibling modules one file imports, from its source."""
        import ast
        tree = ast.parse((self.package() / (name + ".py")).read_text(
            encoding="utf-8"))
        out = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 1:
                if node.module in self.ALLOWED:
                    out.add(node.module)
                for alias in node.names:
                    if alias.name in self.ALLOWED:
                        out.add(alias.name)
        return out

    def test_no_module_imports_a_layer_above_itself(self):
        for name, allowed in self.ALLOWED.items():
            with self.subTest(module=name):
                reached = self.sibling_imports(name)
                self.assertEqual(
                    sorted(reached - allowed), [],
                    "%s imports a layer above it, so the split has a cycle"
                    % name)

    def test_the_bottom_layer_imports_no_sibling(self):
        """Stated separately: `walk` is what makes the order an order."""
        self.assertEqual(self.sibling_imports("walk"), set())

    def test_every_named_module_exists(self):
        """So the table above cannot outlive a rename."""
        for name in self.ALLOWED:
            self.assertTrue((self.package() / (name + ".py")).is_file(),
                            "%s is named in the layering table and is not "
                            "there" % name)

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


# Last in the file, deliberately. It used to sit mid-module,
# and module statements run in order -- so `python tests/test_schema.py`
# called `unittest.main()` (which exits) before the classes below
# it existed, and reported success having skipped them. pytest
# collects either way, which is why nobody noticed.
if __name__ == "__main__":
    unittest.main()
