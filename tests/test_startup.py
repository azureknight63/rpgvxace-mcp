"""Importing the server stays cheap: heavy implementations load on first call.

A client starts this server for every session and waits for `initialize`
before anything else, so what the import drags in is paid by every user
whether or not they ever draw a map or start a game. The tool modules
register their schema at import and keep their implementation behind it;
these tests hold both halves -- that the heavy modules are not loaded, and
that nothing advertised changed because they are not.
"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
sys.path.insert(0, str(SRC))

# Modules whose cost is the reason for the split: the Windows side of a live
# session and the critique analyses. (The renderer and `autotile` are about
# 8 ms together and their constants are quoted in two tools' schemas, so they
# stay eager.)
# `ctypes` is in the list because only the live session's Windows calls use it.
HEAVY = (
    "ctypes",
    "rpgvxace_mcp.live.session",
    "rpgvxace_mcp.live.winapi",
    "rpgvxace_mcp.analysis.dead_ends",
    "rpgvxace_mcp.analysis.lore_baseline",
    "rpgvxace_mcp.analysis.map_shape",
    "rpgvxace_mcp.analysis.reachability",
    "rpgvxace_mcp.analysis.stat_outliers",
    "rpgvxace_mcp.analysis.summary",
    "rpgvxace_mcp.analysis.switch_usage",
    "rpgvxace_mcp.analysis.tile_variety",
)


PROBE = """
import json, sys
import rpgvxace_mcp.server as server
from rpgvxace_mcp.tools.registry import all_tools
loaded = sorted(name for name in %r if name in sys.modules)
tools = [t.definition() for t in all_tools()]
after = sorted(name for name in %r if name in sys.modules)
print(json.dumps({"imported": loaded, "listed": after,
                  "tools": json.dumps(tools, sort_keys=False)}))
""" % (HEAVY, HEAVY)


def run_probe() -> dict:
    env = dict(os.environ, PYTHONPATH=str(SRC), PYTHONIOENCODING="utf-8")
    done = subprocess.run([sys.executable, "-c", PROBE], env=env,
                          capture_output=True, text=True, encoding="utf-8",
                          timeout=120)
    if done.returncode != 0:
        raise AssertionError(done.stderr)
    return json.loads(done.stdout)


class TestImportingTheServerIsLight(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.probe = run_probe()

    def test_importing_the_server_loads_none_of_the_heavy_modules(self):
        self.assertEqual(self.probe["imported"], [])

    def test_listing_the_tools_loads_none_of_them_either(self):
        self.assertEqual(self.probe["listed"], [])


class TestLazyLoadingChangesNothingAdvertised(unittest.TestCase):
    def test_the_advertised_tools_are_identical_once_everything_is_loaded(self):
        """The list a fresh process serves is the list a fully loaded one does."""
        import importlib

        import rpgvxace_mcp.server  # noqa: F401 - registers every tool
        from rpgvxace_mcp.tools.registry import all_tools

        for name in HEAVY:
            importlib.import_module(name)
        loaded = json.dumps([t.definition() for t in all_tools()],
                            sort_keys=False)
        self.assertEqual(run_probe()["tools"], loaded)


if __name__ == "__main__":
    unittest.main()
