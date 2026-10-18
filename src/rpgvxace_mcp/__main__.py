"""Command line entry point: ``python -m rpgvxace_mcp --project <path>``."""

from __future__ import annotations

import argparse
import io
import sys

from . import __version__
from .project import DEFAULT_BACKUP_KEEP, Project, ProjectError
from .server import SERVER_NAME, Server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=SERVER_NAME,
        description="MCP server for RPG Maker VX Ace and VX projects. Speaks "
                    "JSON-RPC over stdio; run it from an MCP client rather than "
                    "by hand.",
    )
    parser.add_argument(
        "project", nargs="?",
        help="Path to the project folder: the one holding Game.rvproj2 for VX "
             "Ace, or Game.rvproj for VX.")
    parser.add_argument(
        "--project", dest="project_flag",
        help="Same as the positional argument, for clients that prefer flags.")
    parser.add_argument(
        "--encoding", default="auto",
        help="Code page for text in the data files. Default auto-detects "
             "(utf-8, cp932 or cp1252). VX Ace projects are normally utf-8; use "
             "cp932 for a Japanese VX project.")
    parser.add_argument(
        "--read-only", action="store_true",
        help="Refuse every write. Useful for exploring a project you do not want "
             "touched.")
    parser.add_argument(
        "--backup-keep", type=int, default=DEFAULT_BACKUP_KEEP, metavar="N",
        help="How many backup snapshots to keep (default %d; 0 or less disables "
             "pruning)." % DEFAULT_BACKUP_KEEP)
    parser.add_argument(
        "--allow-other-engines", action="store_true",
        help="Open a Marshal project whose engine this server does not model, "
             "described with the closest model. VX Ace, VX and XP are modelled "
             "and need no flag; for anything else, edits may produce files "
             "the editor rejects.")
    parser.add_argument(
        "--rtp", action="append", metavar="PATH", default=[],
        help="Folder of an RTP (Run Time Package) this project uses. Normally "
             "found automatically from Game.ini and the registry; pass this when "
             "the RTP lives somewhere unusual, or on a non-Windows host.")
    parser.add_argument(
        "--render-dir", metavar="PATH", default=None,
        help="Where map_render writes its PNGs. Defaults to "
             ".rpgvxace-mcp/renders inside the project. A render is a cache "
             "file, not a project edit, so it is written even with "
             "--read-only; point this elsewhere to keep the project untouched.")
    parser.add_argument(
        "--allow-live-eval", action="store_true",
        help="Let the live tool's eval op run Ruby inside the game. Off by "
             "default: eval is arbitrary code in a process on your desktop. "
             "Even with this set, a session must also be started with "
             "allow_eval: true.")
    parser.add_argument(
        "--verbose", action="store_true",
        help="Log tracebacks to stderr.")
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = args.project_flag or args.project
    if not path:
        print("error: pass the project folder, e.g. "
              "python -m rpgvxace_mcp \"C:/Games/MyProject\"", file=sys.stderr)
        return 2

    try:
        project = Project(
            path,
            encoding=args.encoding,
            read_only=args.read_only,
            backup_keep=args.backup_keep,
            allow_other_engines=args.allow_other_engines,
            rtp_paths=args.rtp,
            render_dir=args.render_dir,
            allow_live_eval=args.allow_live_eval,
        )
    except (ProjectError, ValueError, OSError) as exc:
        # ValueError covers an unknown --encoding (ConversionError), a bad
        # Marshal file (MarshalError) and a map id no filename can express;
        # OSError an unreadable project. All used to escape as a traceback.
        print("error: %s" % exc, file=sys.stderr)
        return 1

    print("rpgvxace-mcp %s serving %s (%s, encoding %s%s)"
          % (__version__, project.root, project.profile.label, project.codec.name,
             ", read-only" if project.read_only else ""),
          file=sys.stderr, flush=True)

    # The protocol is UTF-8 regardless of the console's code page, and a stray
    # decode error on stdin would otherwise kill the session.
    stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace",
                             newline="\n")
    # errors="replace" is a backstop, not the fix: names are sanitised where
    # they enter a response (rubytypes.printable_text). Without it, a single
    # unencodable character would raise inside the write and end the session,
    # because that write happens outside any request's error handling.
    stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", newline="\n")
    server = Server(project, verbose=args.verbose)
    try:
        server.serve(stdin, stdout)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
