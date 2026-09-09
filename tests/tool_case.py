"""The project fixture every tool test runs against, and two crafted maps.

Its own module so both halves of the suite import *down* to it rather than
one importing the other -- the sibling-import problem the tools layer had
just finished fixing, and worse here: a guard names its test by file, so a
class that moves because another file imported it takes its guard with it.

`ToolTest` builds a fresh demo project per test method. The mixins build
the crafted files only the bound tests need: a map whose objects share
children, and a map of events with empty pages.
"""

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
from rpgvxace_mcp.project import (  # noqa: E402
    Project,
)
from rpgvxace_mcp.rubytypes import (  # noqa: E402
    RubyHash,
    RubyObject,
)
from rpgvxace_mcp.tools.registry import (  # noqa: E402
    Context,
    REGISTRY,
)

# Imported for their side effect: each of these modules registers its tools
# with `REGISTRY` at import time, so a suite that never mentions them still
# needs them loaded -- and `REGISTRY` is empty without them, which reads as
# "the tool does not exist" rather than as a missing import.
#
# Here rather than in each suite, because that is what went wrong when this
# file was split out: pyflakes reports the block as unused, the automatic
# prune believed it, and every suite then failed with KeyError on a tool
# name. And the repair after that put this block inside the docstring,
# because the anchor it looked for matched the word "class" in prose --
# which is why `tests/test_prove_guards.py` now runs each suite file on its
# own rather than only as part of the whole run.
from rpgvxace_mcp.tools import (  # noqa: E402,F401
    command_docs_tools,
    db_tools,
    event_tools,
    map_common,
    map_edit_tools,
    map_read_tools,
    map_tile_tools,
    misc_tools,
    raw_tools,
    search_tools,
)


class ToolTest(unittest.TestCase):
    """Base class giving each test its own copy of the demo project.

    Subclasses set ``engine`` to run the same checks against the other engine
    in the family.
    """

    read_only = False
    engine = "vx_ace"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rpgvxace-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        build(self.tmp / "project", self.engine)
        self.project = Project(self.tmp / "project", read_only=self.read_only)
        self.ctx = Context(self.project)

    @property
    def ace(self) -> bool:
        return self.engine == "vx_ace"

    @property
    def extension(self) -> str:
        return ".rvdata2" if self.ace else ".rvdata"

    def call(self, tool_name, /, **arguments):
        # run(), not handler(): a real call goes through the argument check,
        # so the tests should too.
        return REGISTRY[tool_name].run(self.ctx, arguments)

    def reopen(self):
        """A fresh Project, to prove a change actually reached the disk."""
        return Context(Project(self.tmp / "project"))

    def data_bytes(self, stem):
        """Bytes of one data file, named without its extension."""
        name = stem if stem.endswith(self.extension) else stem + self.extension
        return (self.tmp / "project" / "Data" / name).read_bytes()

    def lower_the_finding_ceiling(self, ceiling):
        """Patch `MAX_FINDINGS` everywhere it is bound, and prove it.

        Four test classes each kept their own list of the modules to patch,
        and one of those lists was wrong already: patching only the
        `analysis` package left every real branch reading 5000, so the
        assertions passed against a ceiling nothing had lowered. `from X
        import MAX_FINDINGS` binds the value in the importing module, so
        there is one name to patch per importer -- and a hand-kept list of
        importers goes stale the moment the linter gains a module.

        So the modules are found rather than named, and the two assertions
        are the point: something was found, and afterwards nothing under
        `analysis` still reads the old number.
        """
        from rpgvxace_mcp import analysis

        prefix = analysis.__name__
        under = [(name, module) for name, module in sorted(sys.modules.items())
                 if name == prefix or name.startswith(prefix + ".")]
        found = [module for _, module in under
                 if getattr(module, "MAX_FINDINGS", None) is not None]
        self.assertGreaterEqual(
            len(found), 2, "no module bound MAX_FINDINGS, so this patched "
                           "nothing and every branch still reads 5000")
        for module in found:
            self.addCleanup(setattr, module, "MAX_FINDINGS",
                            module.MAX_FINDINGS)
            module.MAX_FINDINGS = ceiling
        self.assertEqual(
            [name for name, module in under
             if getattr(module, "MAX_FINDINGS", ceiling) != ceiling],
            [], "these still read the old ceiling")


class ValidateTest(ToolTest):
    """Helpers for the rules that must not cry wolf."""

    def findings(self, rule=None, **arguments):
        rows = self.call("validate", **arguments)["findings"]
        return [f for f in rows if rule is None or f["rule"] == rule]

    def corrupt(self, stem):
        """Leave a data file that exists but cannot be parsed."""
        name = stem if stem.endswith(self.extension) else stem + self.extension
        (self.tmp / "project" / "Data" / name).write_bytes(b"\x04\x08 not marshal")

    def declare_missing_rtp(self):
        """Point Game.ini at an RTP that is not installed anywhere."""
        (self.tmp / "project" / "Game.ini").write_text(
            "[Game]\nRTP1=NoSuchRtpAnywhere\nLibrary=System\\RGSS300.dll\n"
            "Scripts=Data\\Scripts%s\nTitle=Demo\n" % self.extension,
            encoding="utf-8")
        self.project = Project(self.tmp / "project")
        self.ctx = Context(self.project)
        self.assertFalse(self.project.assets_complete)

    def first_event(self, map_id=1):
        game_map = self.project.load_map(map_id)
        event_id, event = game_map.get("events").sorted_items()[0]
        return game_map, event_id, event


class CraftedMap:
    """One builder for the maps whose objects share children.

    `SharedObjectMap` and `EmptyPageMap` below were this builder written
    twice: the nine-field page loop, the event, the events hash, the write
    and the reopen were byte-identical, and `assert_bounded` differed only
    in the sentence it prints on failure. `SharedObjectMap`'s own docstring
    argued against exactly that -- "a second copy of the builder would be a
    second thing to keep in step with the RPG::Event::Page field list" --
    while the file held the second copy, and both copies had to be edited
    when a page field moved.

    A mixin rather than a base class: the suites that need one of these maps
    need only the map, and `ToolTest` is already their base.
    """

    # Far below the 13-36 seconds measured without a bound, and far above
    # what any of the bounded walks costs. One value, because the two copies
    # of it were the same number written twice.
    BUDGET_SECONDS = 10.0

    def craft_page_map(self, commands, pages, events, max_bytes, what,
                       condition=None):
        """Rewrite map 1 as `events` copies of one event of `pages` pages.

        One page object, one command object, one event object, shared into
        every slot -- which is what makes the file small and the walk large.
        `commands` is the list every page carries; `condition` is the page's
        condition object, or None for a bare one.
        """
        page = RubyObject("RPG::Event::Page")
        page.set("condition",
                 condition if condition is not None
                 else RubyObject("RPG::Event::Page::Condition"))
        # Empty, so the asset checker returns before it collects.
        page.set("graphic", RubyObject("RPG::Event::Page::Graphic"))
        for name, value in (("trigger", 0), ("priority_type", 0),
                            ("move_type", 0), ("move_speed", 3),
                            ("move_frequency", 3), ("walk_anime", True),
                            ("step_anime", False), ("direction_fix", False),
                            ("through", False)):
            page.set(name, value)
        page.set("list", commands)

        event = RubyObject("RPG::Event")
        event.set("id", 1)
        event.set("name", self.project.codec.make_string("E"))
        event.set("x", 0)
        event.set("y", 0)
        event.set("pages", [page] * pages)

        hash_of_events = RubyHash()
        for event_id in range(1, events + 1):
            hash_of_events.set(event_id, event)
        game_map = self.project.load_map(1)
        game_map.set("events", hash_of_events)

        path = self.project.path_for(self.project.map_file(1))
        path.write_bytes(marshal_rb.dump(game_map))
        # Small on disk is the whole point of the finding.
        self.assertLess(path.stat().st_size, max_bytes,
                        "%s is meant to be small and is %d bytes"
                        % (what, path.stat().st_size))
        self.ctx = self.reopen()

    def assert_bounded(self, tool_name, **arguments):
        """Call a tool and require it to finish. `WHAT` names the map."""
        import time
        start = time.perf_counter()
        out = self.call(tool_name, **arguments)
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, self.BUDGET_SECONDS,
                        "%s took %.1fs on %s"
                        % (tool_name, elapsed, self.WHAT))
        return out


class SharedObjectMap(CraftedMap):
    """A map whose objects share children, for the walk-bound tests."""

    # Small enough to build quickly, large enough that an unbounded walk is
    # unmistakable: 2000 x 50 x 50 is five million command visits.
    LIST_LEN, PAGES, EVENTS = 2000, 50, 50
    WHAT = "a 7 KB crafted map"

    def craft_shared_map(self):
        """Every command, page and event one shared object."""
        command = RubyObject("RPG::EventCommand")
        command.set("code", 108)
        command.set("indent", 0)
        command.set("parameters", [self.project.codec.make_string("x")])
        self.craft_page_map([command] * self.LIST_LEN, self.PAGES,
                            self.EVENTS, 64 * 1024, self.WHAT)


class EmptyPageMap(CraftedMap):
    """A map whose amplification is entirely in (events x pages).

    Every page holds an empty @list, so every charge of the form
    `spend(len(commands))` is free. One page condition flag is set so the page
    is not "unconditional", which is what keeps `_check_shadowing` walking and
    producing nothing -- a finding ceiling cannot bound a walk that finds
    nothing. The graphic is left empty for the same reason in the asset walk:
    `_AssetChecker.check` returns before it counts anything.
    """

    # 4,000,000 pairs from ~18 KB. Large enough that an unbounded walk is
    # unmistakable, small enough to build in a test.
    PAGES, EVENTS = 2000, 2000
    WHAT = "a map of empty pages"

    def craft_empty_page_map(self):
        condition = RubyObject("RPG::Event::Page::Condition")
        # Truthy, so the page is not unconditional and the shadowing rule
        # keeps looking without ever collecting.
        condition.set("switch1_valid", True)
        condition.set("switch1_id", 1)
        self.craft_page_map([], self.PAGES, self.EVENTS, 128 * 1024,
                            self.WHAT, condition=condition)
