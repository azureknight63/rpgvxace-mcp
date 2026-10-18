"""The project fixture every tool test runs against, and two crafted maps.

Its own module so both halves of the suite import *down* to it rather than
one importing the other -- the sibling-import problem the tools layer had
just finished fixing, and worse here: a guard names its test by file, so a
class that moves because another file imported it takes its guard with it.

`ToolTest` builds a fresh demo project per test method. The mixins build
the crafted files only the bound tests need: a map whose objects share
children, and a map of events with empty pages.
"""

import atexit
import os
import shutil
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE / "fixtures"))

from build_project import build  # noqa: E402

from rpgvxace_mcp import marshal_rb  # noqa: E402
from rpgvxace_mcp.schema import (  # noqa: E402
    new_event,
    new_event_command,
    new_page,
)
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
    live_tools,
    map_common,
    map_critique_tools,
    map_edit_tools,
    map_paint_tools,
    map_read_tools,
    map_render_tools,
    map_tile_tools,
    misc_tools,
    raw_tools,
    search_tools,
)


class _Workspace:
    """One built demo project per engine, handed to one test at a time.

    Building the project costs 0.15-0.3 s of mostly file creation on Windows,
    and every test paid it in `setUp` (211 times in `test_tools` alone). So
    each engine's project is built once, its bytes are remembered, and every
    checkout puts the directory back to exactly those bytes before handing it
    over: files that changed are rewritten, files that appeared (backups,
    temp files, a sibling `outside` directory) are removed, files that went
    missing come back. That is the same isolation as a fresh build -- the
    tree is byte-identical at the start of every test -- for the cost of
    reading ~60 small files.

    A workspace is leased to one test at a time. A second concurrent request
    (a test that builds two projects, or one that calls `setUp` again from
    inside a `subTest`) is the same owner re-leasing, or falls back to a
    private fresh build, so nothing is ever shared between two live tests.
    Any failure while restoring discards the workspace and rebuilds it.
    """

    _lock = threading.Lock()
    _by_engine: dict = {}
    _created: list = []

    def __init__(self, engine):
        self.engine = engine
        self.owner = None
        self.root = Path(tempfile.mkdtemp(prefix="rpgvxace-test-"))
        _Workspace._created.append(self.root)
        self._materialise()

    def _materialise(self):
        shutil.rmtree(self.root / "project", ignore_errors=True)
        build(self.root / "project", self.engine)
        self.files, self.dirs = {}, {self.root}
        for directory, subdirs, names in os.walk(self.root):
            self.dirs.update(Path(directory, d) for d in subdirs)
            for name in names:
                path = Path(directory, name)
                self.files[path] = path.read_bytes()

    def restore(self):
        """Make the tree byte-identical to the build, or rebuild it."""
        try:
            self._restore()
        except OSError:
            _force_rmtree(self.root / "project")
            for extra in list(self.root.iterdir()):
                _force_rmtree(extra)
            self._materialise()

    def _restore(self):
        seen = set()
        for directory, subdirs, names in os.walk(self.root, topdown=True):
            here = Path(directory)
            for entry in list(subdirs):
                path = here / entry
                if path in self.dirs and not path.is_symlink():
                    continue
                subdirs.remove(entry)
                _force_rmtree(path)
            for name in names:
                path = here / name
                wanted = self.files.get(path)
                if wanted is None or path.is_symlink():
                    _force_rmtree(path)
                    continue
                seen.add(path)
                if path.stat().st_size != len(wanted) or path.read_bytes() != wanted:
                    path.write_bytes(wanted)
        for path in self.dirs:
            path.mkdir(parents=True, exist_ok=True)
        for path, wanted in self.files.items():
            if path not in seen:
                path.write_bytes(wanted)

    @classmethod
    def lease(cls, engine, owner):
        """A workspace for `owner`: (workspace, shared?)."""
        with cls._lock:
            space = cls._by_engine.get(engine)
            if space is None:
                space = cls._by_engine[engine] = cls(engine)
                space.owner = owner
                return space, True
            if space.owner is None or space.owner is owner:
                space.owner = owner
                space.restore()
                return space, True
        return cls(engine), False

    def release(self, owner):
        with _Workspace._lock:
            if self.owner is owner:
                self.owner = None


def _force_rmtree(path):
    """Remove a file, link or directory, clearing read-only on the way."""
    path = Path(path)
    if path.is_symlink() or path.is_file():
        try:
            path.unlink()
        except PermissionError:
            os.chmod(path, stat.S_IWRITE)
            path.unlink()
        return

    def clear(function, target, _excinfo):
        os.chmod(target, stat.S_IWRITE)
        function(target)
    shutil.rmtree(path, onerror=clear)


@atexit.register
def _remove_workspaces():
    for root in _Workspace._created:
        shutil.rmtree(root, ignore_errors=True)


class ToolTest(unittest.TestCase):
    """Base class giving each test its own copy of the demo project.

    Subclasses set ``engine`` to run the same checks against the other engine
    in the family.

    The copy is a restored shared directory rather than a fresh build (see
    `_Workspace`); `self.tmp` is the same path in every test of one engine,
    and is byte-for-byte what a fresh build would be when `setUp` returns.
    """

    read_only = False
    engine = "vx_ace"

    def setUp(self):
        space, shared = _Workspace.lease(self.engine, self)
        self.tmp = space.root
        if shared:
            self.addCleanup(space.release, self)
        else:
            self.addCleanup(shutil.rmtree, space.root, ignore_errors=True)
        self.project = Project(self.tmp / "project", read_only=self.read_only)
        self.ctx = Context(self.project)

    @property
    def ace(self) -> bool:
        return self.engine == "vx_ace"

    @property
    def xp(self) -> bool:
        return self.engine == "xp"

    @property
    def extension(self) -> str:
        return self.project.profile.extension

    def call(self, tool_name, /, **arguments):
        # run(), not handler(): a real call goes through the argument check,
        # so the tests should too.
        return REGISTRY[tool_name].run(self.ctx, arguments)

    def reopen(self):
        """A fresh Project, to prove a change actually reached the disk."""
        # With the test's own `read_only`: a reopened project that was
        # writable in a read-only test left every guard checked through
        # `reopen()` or `craft_page_map` unexercised.
        return Context(Project(self.tmp / "project",
                               read_only=self.read_only))

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

    # What this needs from the test case it is mixed into, written down for
    # the same reason `BackupStore.NEEDS_FROM_PROJECT` is: a mixin that
    # reads a name nobody defines fails when the method runs, and this one
    # runs inside a timing assertion where the failure reads as a bound
    # that did not hold.
    #
    # The first version of this list was wrong in both directions -- it
    # declared `WHAT`, which is defined right below and so is not needed
    # from anywhere, and omitted `ctx`, which `craft_page_map` assigns.
    # That is what the reason `BackupStore`'s list works is the three tests
    # and not the tuple; this one now has them too.
    NEEDS_FROM_HOST = ("assertLess", "call", "ctx", "project", "reopen")

    # The subclass's own, naming the map in a failure message.
    WHAT = "an uncrafted map"

    def craft_page_map(self, commands, pages, events, max_bytes, what,
                       condition=None):
        """Rewrite map 1 as `events` copies of one event of `pages` pages.

        One page object, one command object, one event object, shared into
        every slot -- which is what makes the file small and the walk large.
        `commands` is the list every page carries; `condition` is the page's
        condition object, or None for a bare one.
        """
        # From `new_page`, which is the constructor the server itself
        # uses, rather than nine of the thirteen fields written out here.
        # This was the third copy of that field set after round 9 merged
        # the two in `src` -- and the four it left out included
        # `move_route`, which is the field whose absence from `_page_view`
        # was the round-9 round-trip bug.
        page = new_page()
        if condition is not None:
            page.set("condition", condition)
        page.set("list", commands)

        event = new_event(1, "E")
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
        command = new_event_command(108, 0,
                                    [self.project.codec.make_string("x")])
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
