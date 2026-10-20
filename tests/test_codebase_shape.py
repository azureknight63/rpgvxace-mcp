"""Rules about the shape of this codebase, read from its own source.

Not about RPG Maker. Every class here opens a `.py` file and asks
something structural of it: which module may import which, how many
blank lines sit above a definition, whether a mixin's stated contract
matches what it reads, whether a crafted object spells out a field list
the constructors already hold. None of them builds a project or calls a
tool.

Out of `test_schema.py`, which is about engine profiles and what the
server can observe of a file. Six hundred lines of one subject under the
heading of another, and the seam was contiguous. No guard in
`guard_table.py` names any class here, so nothing needed re-anchoring --
which is why this was the split to do first.
"""

import ast
import functools
import re
import sys
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE / "fixtures"))
sys.path.insert(0, str(HERE))  # tool_case: bare-name import must work from the repo root

from rpgvxace_mcp.rubytypes import RubyHash  # noqa: E402


# The four directories every class here reads, derived once.
# `Path(__file__).resolve().parent` was written out eleven more times
# below, six of them with the same three-segment tail, in a file whose
# subject is this codebase not repeating itself.
ROOT = HERE.parent
SRC = ROOT / "src"
PACKAGE = SRC / "rpgvxace_mcp"
TOOLS = PACKAGE / "tools"


@functools.lru_cache(maxsize=None)
def _parse(source):
    """`ast.parse`, once per distinct source text.

    Nearly every class here walks the same ~100 source files, and each walk
    parsed them again. Keyed on the text and not on the path, so a test that
    hands in a synthetic source gets its own tree and an edited file is never
    served stale. Callers only read the trees; none rewrites one.
    """
    return ast.parse(source)


def python_files(*folders):
    """Every `.py` file under these folders, sorted within each.

    One enumerator: there were two, and they disagreed -- one used
    `rglob` for all three folders, the other `rglob` for `src` and
    `glob` for `tests` and `scripts`, so `tests/fixtures` was read by
    one rule and not by the other with nothing saying which was meant.
    Deep for all of them, which is the stricter of the two and the one
    whose comment gives a reason.
    """
    out = []
    for folder in folders:
        out += sorted(folder.rglob("*.py"))
    return out


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
        return TOOLS

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

        crossings = []
        shared = self.shared_modules()
        self.assertIn("registry", shared, "the scan lost the shared layer")
        for path in self.tool_modules():
            tree = _parse(path.read_text(encoding="utf-8"))
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
        # Deep in all three: `tests/fixtures/build_project.py` sits
        # one directory down and was unread while this said "everywhere".
        return python_files(PACKAGE, HERE, ROOT / "scripts")

    def blanks_above(self, path):
        """`blanks_in` over one file."""
        return self.blanks_in(path.read_text(encoding="utf-8"))

    def blanks_in(self, source):
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
        lines = source.split("\n")
        tree = _parse(source)
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

    def test_the_blank_line_scan_can_tell_the_counts_apart(self):
        """The instrument, fed sources it must judge. Without this a scan
        that always answered `[]` kept the convention test below green."""
        def counts(source):
            return [blank for _, blank in self.blanks_in(source)]

        body = "import os\n"
        for blanks, expected in ((0, 0), (1, 1), (2, 2), (3, 3)):
            with self.subTest(blanks=blanks):
                self.assertEqual(
                    counts(body + "\n" * blanks + "def f():\n    pass\n"),
                    [expected])
        # A comment block belongs to the definition under it, so the count
        # is taken above the comment; a decorator likewise.
        self.assertEqual(
            counts(body + "\n\n# why\n# more\ndef f():\n    pass\n"), [2])
        self.assertEqual(
            counts(body + "\n@decorator\ndef f():\n    pass\n"), [1])
        self.assertEqual(counts(body + "\n\nclass C:\n    pass\n"), [2])

    def test_the_blank_line_scan_sees_many_definitions(self):
        seen = sum(len(self.blanks_above(path)) for path in self.modules())
        self.assertGreater(seen, 500, "the scan found almost nothing to judge")

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
        "reachability": {"walk"},
        # Reads `reachability`'s edge builder and start-map reader rather
        # than keeping a second copy of the transfer parsing.
        "dead_ends": {"walk", "reachability"},
        "map_shape": {"walk"},
        # The lore scan: walks the text (budgeted by `walk`) and reads the
        # per-map shapes rather than measuring blankness a second time.
        "lore_baseline": {"walk", "map_shape"},
        "tile_variety": {"walk"},
        "stat_outliers": {"walk"},
        # Fences a project's maps with `stat_outliers`' rule, and walks
        # nothing: it reads the replies the other scopes built.
        "summary": {"stat_outliers"},
        "switch_usage": {"walk"},
        # The wider reference scan behind `references` and `refactor`: it
        # reuses `references`' command rules and `switch_usage`' operand
        # tables rather than keeping a second copy of either.
        "ref_sites": {"walk", "references", "switch_usage"},
        "lint": {"walk", "references"},
    }
    LINT = {
        "findings": set(),
        "maps": {"findings"},
        # `assets` too, since `check_event_pages` does the map and
        # page asset checks in the walk it already makes -- it has
        # the map open, the events sorted, the event name decoded
        # and each page in hand, which is every value
        # `_check_map_assets` opened a second pass over the map set
        # to get.
        "pages": {"findings", "assets"},
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

    # The picture-drawing half, in the order a map render needs it: PNG in
    # and out, then pixel operations on a decoded image, then the tile
    # geometry and autotile tables that cut sheets with them, then the scene
    # that composes a map from all three. `scene` knowing nothing of projects
    # is the point of the split, and it is checked below with the rest.
    RENDER = {
        "png": set(),
        "canvas": {"png"},
        "tiles": {"png", "canvas"},
        # XP's tile model is a second set of tables over the same canvas.
        "xp_tiles": {"png", "canvas", "tiles"},
        # The engine's passage rules (pure, over ids and flags), then the
        # marks drawn from what they say, then the scene that places both.
        "passability": {"tiles"},
        "overlays": {"png", "canvas", "tiles", "passability"},
        # One engine's tile model as a value, which the scene is given.
        "layouts": {"png", "tiles", "xp_tiles", "overlays"},
        "scene": {"png", "canvas", "tiles", "passability", "overlays",
                  "layouts"},
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
        # The journal of previous file contents: reads `paths` for the
        # error type, the name rules and the link check, and is held by
        # `Project` and `backups` (which names it in its contract).
        "undo_journal": {"paths"},
        # Reads `paths` for its error type and containment check, and
        # nothing else: it is a format reader, not a project.
        "rgss_archive": {"paths"},
        "project": {"paths", "backups", "rgss_archive", "undo_journal"},
    }

    # The rest of the package root, listed rather than passed over. These
    # do have an order -- `rubytypes` is below `convert`, `schema` and
    # `marshal_rb`, which are below `commands`, and `docs` and `server` are
    # above `project` -- but stating it here would give `ROOT_MODULES` two
    # bottom layers and `test_the_bottom_layer_imports_no_sibling` asserts
    # there is one. So they are named as out of scope, which is a fact a
    # reader can check, rather than skipped, which is a hole.
    #
    # `autotile` is with them: it reads `schema` for the layer-3 word and is
    # read by `render` and the tools, which is an order, but not one with
    # `paths` at the bottom. `live` too. It sits above `marshal_rb`, `rubytypes` and `render`'s
    # `png` and below `tools`, but its own modules have three independent
    # bottoms (`wire`, `frame`, `winapi`), so it cannot take a table here
    # without inventing an order between them. Its one edge into `render`
    # is held by `test_live_reaches_render_only_through_its_bottom_layer`.
    ROOT_UNORDERED = {
        "analysis", "autotile", "commands", "convert", "docs", "live",
        "lore_export", "lore_guidance", "lore_store", "marshal_rb", "render",
        "rubytypes", "schema", "server", "tools", "__main__",
    }

    def tables(self):
        """Each layering table with the directory it describes."""
        package = PACKAGE
        return (("rpgvxace_mcp", self.ROOT_MODULES, package),
                ("analysis", self.ANALYSIS, package / "analysis"),
                ("render", self.RENDER, package / "render"),
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
        out = set()
        for path in paths:
            level = 1 if path.parent == where else 2
            tree = _parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom) or node.level != level:
                    continue
                if node.module in table:
                    out.add(node.module)
                for alias in node.names:
                    if alias.name in table:
                        out.add(alias.name)
        return out

    def test_the_import_scan_finds_edges_to_judge(self):
        """The instrument, observed. `reached - allowed` is empty for a scan
        that finds nothing, so the two tests below stayed green when
        `sibling_imports` was changed to the wrong level or to `set()`."""
        for label, table, where in self.tables():
            modules = self.found(where)
            edges = sum(
                len(self.sibling_imports(table, where, modules[name])
                    - {name})
                for name in table)
            with self.subTest(package=label):
                self.assertGreater(
                    edges, 2, "%s: the scan found almost no imports between "
                    "its modules, so nothing below was judged" % label)

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
                # Both directions here too, against the exclusion list
                # rather than against nothing: the previous version skipped
                # the reverse check for this table alone, which is the hole
                # this class's own docstring calls "worse" -- a module
                # added to the package root would have been unconstrained
                # and read like a file that passes.
                found = set(self.found(where))
                missing = sorted(set(table) - found)
                self.assertEqual(missing, [], "named and not there: "
                                              + ", ".join(missing))
                unplaced = sorted(found - set(table) - self.ROOT_UNORDERED)
                self.assertEqual(
                    unplaced, [],
                    "these are in the package root and neither in the "
                    "layering table nor named as unordered: "
                    + ", ".join(unplaced))
                continue
            with self.subTest(package=label):
                self.assertEqual(sorted(self.found(where)), sorted(table))

    def test_the_package_re_exports_what_the_tools_import(self):
        """`from ..analysis import X` keeps working, for every X in use.

        The split is about how this code is read, not about making nine
        tool modules learn which of three files a name lives in -- so the
        re-export is part of the claim and is checked the same way.
        """
        from rpgvxace_mcp import analysis
        wanted = set()
        for path in python_files(TOOLS):
            tree = _parse(path.read_text(encoding="utf-8"))
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
        from rpgvxace_mcp import analysis
        of_the_linter = set(vars(analysis.lint))
        # A sanity check on the definition above, not on the code: if the
        # linter's own namespace came back empty this test would pass by
        # finding no importers of anything.
        self.assertIn("validate", of_the_linter)
        importers = {}
        for path in python_files(TOOLS):
            tree = _parse(path.read_text(encoding="utf-8"))
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
        found = set()
        paths = python_files(PACKAGE / "analysis")
        self.assertGreater(len(paths), 5, "read almost nothing")
        for path in paths:
            if path.name == "__init__.py":
                continue
            for node in _parse(path.read_text(encoding="utf-8")).body:
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
        modules = {"walk", "references", "reachability", "dead_ends",
                   "map_shape", "tile_variety", "stat_outliers",
                   "summary", "switch_usage", "ref_sites", "lint",
                   "lore_baseline"}
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

    # `live` and `render` are both in ROOT_UNORDERED, so nothing above
    # constrains what one takes from the other. `live` draws its screenshot
    # PNG with render's encoder, and that one edge is stated here instead:
    # it reaches `render`'s bottom layer and nothing higher, and `render`
    # reaches nothing outside itself at all.
    LIVE_MAY_TAKE_FROM_RENDER = {"png"}

    def relative_imports(self, path):
        """(level, dotted module, imported names) for each `from .x import`."""
        tree = _parse(path.read_text(encoding="utf-8"))
        return [(node.level, node.module or "",
                 [alias.name for alias in node.names])
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.level]

    def test_live_reaches_render_only_through_its_bottom_layer(self):
        for path in sorted((PACKAGE / "live").glob("*.py")):
            for level, module, names in self.relative_imports(path):
                if level != 2 or module.split(".")[0] != "render":
                    continue
                reached = ({module.split(".")[1]} if "." in module
                           else set(names))
                with self.subTest(file=path.name, module=module):
                    self.assertEqual(
                        sorted(reached - self.LIVE_MAY_TAKE_FROM_RENDER), [],
                        "live/%s reaches above render's PNG layer, so the "
                        "screenshot depends on the map compositor"
                        % path.name)

    # `render` reads the layer model from `schema` and the autotile facts
    # from `autotile`, which it shares with `map_paint`. Neither reaches
    # `live`, and the second test below holds that, so no cycle can close.
    RENDER_MAY_READ = {"autotile", "schema"}

    def test_render_reaches_nothing_outside_itself(self):
        for path in sorted((PACKAGE / "render").glob("*.py")):
            outward = [module for level, module, _ in
                       self.relative_imports(path) if level > 1
                       and module.split(".")[0] not in self.RENDER_MAY_READ]
            with self.subTest(file=path.name):
                self.assertEqual(outward, [],
                                 "render/%s imports from outside render, so "
                                 "live, which reads render's PNG layer, "
                                 "could close a cycle through it"
                                 % path.name)

    def test_what_render_may_read_never_reaches_live(self):
        for name in sorted(self.RENDER_MAY_READ):
            path = PACKAGE / ("%s.py" % name)
            reached = [module for _, module, _ in self.relative_imports(path)
                       if module.split(".")[0] in ("live", "render", "tools")]
            with self.subTest(module=name):
                self.assertEqual(reached, [],
                                 "%s is below render, so importing live, "
                                 "render or tools from it closes a cycle"
                                 % name)

    def test_one_module_encodes_a_png(self):
        """Two encoders are two places a chunk or a CRC can be got wrong.

        The mark of an encoder is writing an IHDR chunk; a decoder reads
        one by comparing, which is a bytes literal too, so only a call that
        builds a chunk counts.
        """
        writers = []
        for path in sorted(PACKAGE.rglob("*.py")):
            tree = _parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and node.args
                        and isinstance(node.args[0], ast.Constant)
                        and node.args[0].value == b"IHDR"):
                    writers.append(path.relative_to(PACKAGE).as_posix())
                    break
        self.assertEqual(writers, ["render/png.py"])


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
        return (ROOT / where).read_text(encoding="utf-8")

    def declared(self, where, name, tuple_name):
        for node in _parse(self.source(where)).body:
            if isinstance(node, ast.ClassDef) and node.name == name:
                for inner in node.body:
                    if (isinstance(inner, ast.Assign)
                            and getattr(inner.targets[0], "id", "")
                            == tuple_name):
                        return [e.value for e in inner.value.elts]
        raise AssertionError("%s has no %s" % (name, tuple_name))

    def reached(self, where, name):
        """Every `self.<name>` the mixin reads that it does not define."""
        cls = next(node for node in _parse(self.source(where)).body
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
        import tempfile
        # `HERE / "fixtures"` is on `sys.path` from the module preamble, so
        # this used to insert it again through a second spelling of
        # `Path(__file__).resolve().parent` -- in the file whose subject is
        # this codebase not repeating itself.
        from build_project import build
        from rpgvxace_mcp.backups import BackupStore
        from rpgvxace_mcp.project import Project
        tmp = Path(tempfile.mkdtemp(prefix="rpgvxace-mixin-"))
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
        found = python_files(SRC, HERE, ROOT / "scripts")
        self.assertGreater(len(found), 30, "scanned almost nothing")
        return found

    def blocks(self, path):
        """`blocks_in` over one file."""
        return self.blocks_in(path.read_text(encoding="utf-8"))

    def blocks_in(self, text):
        """Every `from X import (a, b, c)` written one name per line."""
        lines = text.split("\n")
        for node in _parse(text).body:
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

    def test_the_block_scan_finds_blocks_and_their_order(self):
        """The instrument, fed sources it must judge, and the tree it must
        find blocks in: a scan whose pattern stopped matching read as every
        block being sorted."""
        unsorted = "from x import (\n    b,\n    a,\n)\n"
        self.assertEqual(list(self.blocks_in(unsorted)), [(1, ["b", "a"])])
        self.assertEqual(list(self.blocks_in(
            "from x import (\n    a,\n    b,\n)\n")), [(1, ["a", "b"])])
        self.assertEqual(list(self.blocks_in(
            "from x import (\n    a as z,\n    b,\n)\n")), [])
        found = sum(len(list(self.blocks(path))) for path in self.modules())
        self.assertGreater(found, 20, "the scan found almost no blocks")

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

    def imports_inside(self, tree):
        """Every plain `import x` that is not at the top of the file."""
        top = {id(node) for node in tree.body}
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and id(node) not in top:
                found += [(alias.asname or alias.name, node.lineno)
                          for alias in node.names]
        return found

    def test_no_module_is_imported_both_at_the_top_and_inside(self):
        """The same defect as the test below, for a plain `import`.

        `import ast` inside a method reads as "only this method needs it".
        Thirty-one of them across five suites read as nothing at all, and
        one round removed twelve of them from one file by hand -- so this
        is the rule rather than a thirteenth fix.

        Only a module the file *also* imports at the top, which is the
        property the test below states for `from X import`: two statements
        naming one module put the same names in two places to look. A
        lazily imported module that appears nowhere else -- `winreg` on the
        Windows path, `base64` inside the measurement -- names one place
        and is left alone, with no exception list to keep in step.
        """
        out = []
        for path in self.modules():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            top = {alias.asname or alias.name
                   for node in tree.body if isinstance(node, ast.Import)
                   for alias in node.names}
            for name, line in self.imports_inside(tree):
                if name in top:
                    out.append("%s:%d imports %s, which the file already "
                               "imports" % (path.name, line, name))
        self.assertEqual(
            out, [],
            "these import a module the file's own import block already "
            "names: " + "; ".join(out))

    def test_a_module_is_imported_from_once(self):
        """`from ..commands import (...)` and then `from ..commands import x`.

        Two statements naming one module put the same names in two places to
        look, and that is how the second one goes unread: `map_edit_tools`
        had `reference_preview` on its own four lines above the block that
        already imported two other names from `..analysis`.
        """
        out = []
        for path in self.modules():
            seen = {}
            tree = _parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if not isinstance(node, ast.ImportFrom):
                    continue
                where = "." * node.level + (node.module or "")
                if where in seen:
                    out.append("%s:%d and :%d both import from %s"
                               % (path.name, seen[where], node.lineno, where))
                seen[where] = node.lineno
        self.assertEqual(out, [], "; ".join(out))


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
        found = sorted(HERE.glob("test_*.py"))
        self.assertGreater(len(found), 5, "found almost no suites")
        return found

    def test_the_main_block_is_the_last_statement(self):
        wrong = []
        for path in self.suites():
            body = _parse(path.read_text(encoding="utf-8")).body
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


class TestNoSuiteRewritesAFieldList(unittest.TestCase):
    """A crafted object does not spell out a class's field list again.

    `schema` holds `new_event_command`, `new_page`, `new_condition`,
    `new_event` and the rest; `src` uses them and
    `tests/fixtures/build_project.py` uses them at twenty call sites. The
    guard suites built the same objects by hand, and `tool_case`'s crafted
    page wrote out nine of the thirteen `RPG::Event::Page` fields -- with
    `move_route` among the four it omitted, which is the field whose
    absence from `_page_view` was the round-9 round-trip bug. A fourth copy
    of a field list, in the suite that tests field lists.

    What this refuses is that: a site setting five or more fields of one
    class is writing the list again and should call the constructor. It
    does *not* refuse building an object by hand, and that distinction is
    the whole point -- these suites craft deliberately *incomplete*
    objects, which is their subject. `new_page()` fills all thirteen
    fields, so substituting it into a test about a damaged page would
    quietly repair the damage. A rule that counted constructor calls would
    have demanded exactly that, which is why this counts fields.
    """

    # A site may set up to four fields of one class before it is rewriting
    # the list. Four because the largest legitimate case is an event's
    # id/name/x/y, where the fifth field would be `pages`, and a test that
    # sets pages as well is building a whole event.
    MOST_FIELDS_BY_HAND = 4

    def suites(self):
        found = python_files(HERE)
        self.assertGreater(len(found), 5, "found almost no suites")
        return found

    def constructed(self):
        """The classes `schema` has a constructor for, read from `schema`.

        Derived, not listed: `RPG::Map::Encounter`, `RPG::Tileset` and
        `RPG::Troop` have no constructor, so a suite building one by hand
        is doing the only thing available and this rule must not claim
        otherwise.

        Read from the tree rather than from `ast.unparse` text: unparse
        renormalises string quotes, so a regex looking for a double-quoted
        class name matched nothing -- which the count below caught.
        """
        source = (PACKAGE / "schema.py").read_text(encoding="utf-8")
        found = set()
        for node in _parse(source).body:
            if not (isinstance(node, ast.FunctionDef)
                    and node.name.startswith("new_")):
                continue
            for inner in ast.walk(node):
                if (isinstance(inner, ast.Call)
                        and getattr(inner.func, "id", "") == "RubyObject"
                        and inner.args
                        and isinstance(inner.args[0], ast.Constant)
                        and isinstance(inner.args[0].value, str)):
                    found.add(inner.args[0].value)
        self.assertGreater(len(found), 5, "read almost no constructors")
        return found

    def target_name(self, node):
        """The dotted name a construction is assigned to, or None.

        `self.page = RubyObject(...)` is as much a hand-built page as
        `page = RubyObject(...)`, and the regex this replaced matched a
        bare word only, so it read straight past the attribute form.
        """
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            inner = self.target_name(node.value)
            return None if inner is None else "%s.%s" % (inner, node.attr)
        return None

    def built_class(self, node):
        """The Ruby class a `RubyObject("RPG::...")` call names, or None."""
        if not (isinstance(node, ast.Call)
                and getattr(node.func, "id", "") == "RubyObject"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and node.args[0].value.startswith("RPG::")):
            return None
        return node.args[0].value

    def declared_fields(self, node):
        """Fields the construction itself names, from an ivars dict.

        `RubyObject("RPG::X", {"a": 1, "b": 2})` writes two fields of the
        list without a single `.set`, and the regex this replaced required
        a one-argument call, so every such site read as zero fields.
        """
        dicts = [argument for argument in node.args[1:]
                 if isinstance(argument, ast.Dict)]
        dicts += [word.value for word in node.keywords
                  if isinstance(word.value, ast.Dict)]
        return {key.value for mapping in dicts for key in mapping.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)}

    HOLDS_A_SCOPE = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                     ast.ClassDef)

    def in_scope(self, statements):
        """Every node under these statements, not entering a nested scope.

        `ast.walk` cannot be pruned, so the first version of this
        descended into methods from the module as well and reported each
        site three times -- once per enclosing scope -- which put two
        methods' `page` locals back in one bucket, the defect this class
        was rewritten to remove.
        """
        out = []
        # Order does not matter -- `hand_built_in` sorts by line number --
        # so this pops from the end. `pop(0)` made it a queue drained from
        # the front, which is linear each time and was not the point.
        stack = [node for node in statements
                 if not isinstance(node, self.HOLDS_A_SCOPE)]
        while stack:
            node = stack.pop()
            out.append(node)
            stack.extend(child for child in ast.iter_child_nodes(node)
                         if not isinstance(child, self.HOLDS_A_SCOPE))
        return out

    def scopes(self, tree):
        """Every scope, and the statements in it that are not in another.

        A method boundary is a boundary: the line scan this replaced ran
        forward until an unrelated assignment, and an indented `def` was
        not one -- so a second method reusing the name `page` had its
        fields unioned into the first method's count, and a site that set
        a field after any other assignment had them dropped. Wrong in
        both directions at once, which is why this asks the tree.
        """
        for node in ast.walk(tree):
            if isinstance(node, self.HOLDS_A_SCOPE):
                yield self.in_scope(node.body)

    def hand_built(self, path):
        """Each `x = RubyObject("RPG::...")` and how many fields it is set.

        A field belongs to the nearest preceding construction of the same
        name *in the same scope*, which is how these are written
        throughout: build, then set, then use. Read from the tree, because
        the four regexes this replaced held a weaker property than the
        paragraph above claimed -- see `scopes`, `target_name` and
        `declared_fields` for what each of them missed.
        """
        return self.hand_built_in(path.read_text(encoding="utf-8"))

    def hand_built_in(self, source):
        """The same, on source text, so the scan itself can be tested."""
        tree = _parse(source)
        out = []
        for statements in self.scopes(tree):
            sites = {}
            for node in sorted(statements, key=lambda n: (
                    getattr(n, "lineno", 0), getattr(n, "col_offset", 0))):
                if isinstance(node, ast.Assign) and len(node.targets) == 1:
                    cls = self.built_class(node.value)
                    name = self.target_name(node.targets[0])
                    if cls is not None and name is not None:
                        site = [node.lineno, cls,
                                self.declared_fields(node.value)]
                        # A second construction of the same name starts a
                        # new site; the fields set after it are its own.
                        sites[name] = site
                        out.append(site)
                        continue
                    if name is not None:
                        sites.pop(name, None)
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "set"
                        and node.args
                        and isinstance(node.args[0], ast.Constant)):
                    owner = self.target_name(node.func.value)
                    if owner in sites:
                        sites[owner][2].add(node.args[0].value)
        # The sites are built as lists so the fields can be added to
        # them, and handed back as tuples because a caller only reads them.
        return [tuple(site) for site in out]

    # The instrument's own tests, on source written here rather than on the
    # suites: the property this class asserts is only as good as the scan,
    # and the scan it replaced was wrong in both directions with every
    # assertion above still green.
    def test_the_scan_counts_the_fields_a_construction_declares(self):
        source = (
            'page = RubyObject("RPG::Event::Page", {"a": 1, "b": 2})\n'
            'page.set("c", 3)\n')
        self.assertEqual([(cls, sorted(fields))
                          for _, cls, fields in self.hand_built_in(source)],
                         [("RPG::Event::Page", ["a", "b", "c"])])

    def test_the_scan_reads_an_attribute_target(self):
        source = ('self.page = RubyObject("RPG::Event::Page")\n'
                  'self.page.set("a", 1)\n')
        self.assertEqual([sorted(fields)
                          for _, _, fields in self.hand_built_in(source)],
                         [["a"]])

    def test_the_scan_does_not_cross_a_method_boundary(self):
        """Two methods, one local name: two sites of one field, not one of two."""
        source = ('class T:\n'
                  '    def one(self):\n'
                  '        page = RubyObject("RPG::Event::Page")\n'
                  '        page.set("a", 1)\n'
                  '    def two(self, page):\n'
                  '        page.set("b", 2)\n')
        # `two` only writes through a parameter that merely shares the name:
        # one site with one field. In one merged scope the parameter would be
        # the object `one` built, and the answer would be one site of two.
        self.assertEqual([sorted(fields) for _, _, fields
                          in self.hand_built_in(source)],
                         [["a"]])

    def test_the_scan_keeps_counting_after_an_unrelated_assignment(self):
        source = ('page = RubyObject("RPG::Event::Page")\n'
                  'page.set("a", 1)\n'
                  'other = 3\n'
                  'page.set("b", 2)\n')
        self.assertEqual([sorted(fields)
                          for _, _, fields in self.hand_built_in(source)],
                         [["a", "b"]])

    def test_the_scan_flags_a_site_that_writes_the_list(self):
        """Five fields is the whole point, and nothing in the tree has one."""
        source = 'page = RubyObject("RPG::Event::Page")\n' + "".join(
            'page.set("f%d", 1)\n' % index for index in range(5))
        line, cls, fields = self.hand_built_in(source)[0]
        self.assertEqual(cls, "RPG::Event::Page")
        self.assertGreater(len(fields), self.MOST_FIELDS_BY_HAND)

    def test_no_site_sets_five_or_more_fields_of_one_class(self):
        covered = self.constructed()
        wide = []
        for path in self.suites():
            for line, cls, fields in self.hand_built(path):
                if cls not in covered:
                    continue
                if len(fields) > self.MOST_FIELDS_BY_HAND:
                    wide.append("%s:%d builds %s with %d fields by hand"
                                % (path.name, line, cls, len(fields)))
        self.assertEqual(
            wide, [],
            "`schema` has a constructor for these and the field list is "
            "written out again here: " + "; ".join(wide))

    def test_the_scan_sees_the_hand_built_objects(self):
        """So the assertion above cannot pass by reading nothing."""
        total = sum(len(self.hand_built(path)) for path in self.suites())
        self.assertGreater(total, 10,
                           "found %d hand-built objects, so this checked "
                           "almost nothing" % total)

    def test_the_constructors_it_names_exist(self):
        """So the argument above is about real functions."""
        from rpgvxace_mcp import schema
        for name in ("new_event_command", "new_page", "new_event",
                     "new_map_info", "new_move_command", "new_condition",
                     "new_graphic"):
            with self.subTest(constructor=name):
                self.assertTrue(callable(getattr(schema, name, None)), name)


class TestEverySuiteImportsFromTheRepoRoot(unittest.TestCase):
    """`python -m unittest tests.test_x`, from the root, with no PYTHONPATH.

    The README documents that invocation, and each suite makes it work by
    putting `src/`, `tests/fixtures/` and `tests/` on `sys.path` itself.
    `tests/test_prove_guards.py` cannot see a suite that forgets the last
    of the three: it runs each file under pytest, whose default import mode
    puts the file's own directory on the path, so the bare `tool_case`
    import resolves there and fails only under the documented unittest
    form. `test_stat_outliers.py` arrived that way.

    Each module in its own interpreter: in one process the first suite to
    run fixes the path for every suite after it, which is how this passes
    while checking nothing.
    """

    @staticmethod
    def suites():
        return sorted(HERE.glob("test_*.py"))

    @staticmethod
    def import_alone(path):
        import os
        import subprocess

        env = {key: value for key, value in os.environ.items()
               if key != "PYTHONPATH"}
        done = subprocess.run(
            [sys.executable, "-c",
             "import importlib, sys; importlib.import_module(sys.argv[1])",
             "tests." + path.stem],
            cwd=str(ROOT), capture_output=True, text=True, env=env,
            timeout=120)
        if done.returncode == 0:
            return None
        lines = done.stderr.strip().splitlines()
        return "%s: %s" % (path.name, lines[-1] if lines else "no detail")

    def test_the_scan_finds_the_suites(self):
        """So the assertion below cannot pass by importing nothing."""
        names = {path.name for path in self.suites()}
        self.assertIn("test_tools.py", names)
        self.assertIn(Path(__file__).name, names)

    def test_every_suite_imports_from_the_repo_root(self):
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=4) as pool:
            failed = [line for line in pool.map(self.import_alone,
                                                self.suites()) if line]
        self.assertEqual(failed, [],
                         "these suites do not import as `tests.<name>` from "
                         "the repository root: " + "; ".join(failed))


# Last in the file, deliberately. Module statements run in order, so a
# `unittest.main()` above a class definition exits before that class
# exists and reports success having skipped it. `TestTheMainBlockIsLast`
# is the scan that holds this for every suite.
if __name__ == "__main__":
    unittest.main()
