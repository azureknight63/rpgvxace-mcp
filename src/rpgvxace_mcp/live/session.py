"""One live session: a scratch copy of the project, its game, and the wire.

The rules this module exists to keep:

- The project is never run and never written. `start` copies it to a new
  folder under the system temp directory -- without save files, without
  this server's own `.rpgvxace-mcp` folder -- and injects the bridge into
  the copy's script list. The copy carries Game.exe, so it runs on its own.
- One session at a time, held here rather than per caller, because there
  is one desktop and one game window.
- Every wait is bounded. A game that never comes up, stops answering,
  exits or shows an error dialog produces a reply that says which, within
  `BOOT_SECONDS` or `REPLY_SECONDS` (a step waits longer, in proportion to
  the frames it asked for). Nothing here blocks on the game.
- Every session ends with its process ended and its copy deleted: `stop`
  does it, `atexit` does it if the server exits normally, and a Windows
  job object does it if the server is killed.
"""

from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from .. import marshal_rb
from ..render import png
from . import bridge, frame, wire
from .limits import (  # noqa: F401 - re-exported: tests set these here
    BOOT_SECONDS,
    LiveError,
    MAX_SCRATCH_BYTES,
    MAX_SCRATCH_FILES,
    MAX_SCREENSHOT_BYTES,
    REPLY_SECONDS,
)
from .winapi import (
    GAME_WINDOW_CLASS,
    KillOnClose,
    capture_window,
    dialog_texts,
    keep_awake,
    pid_alive,
    restore_without_focus,
    windows_of,
)


# Every scratch folder this server makes starts with this, under the system
# temp directory, and holds a marker naming the server process that owns it.
SCRATCH_PREFIX = "rpgvxace-live-"
MARKER_FILE = "rpgvxace-live-session.json"


# `BOOT_SECONDS` and `REPLY_SECONDS` are in `limits`, read back above, so the
# tool can quote them without loading this module. They are still names here,
# which is what a test shortens.
# Folders a scratch copy leaves out, at any depth.
_NOT_COPIED = frozenset({".rpgvxace-mcp", ".git", ".hg", ".svn"})

QUIT_SECONDS = 3.0
POLL_SECONDS = 0.01


# How often a wait reminds the game it is active. RGSS3 stops the moment
# its window loses focus, and the user may click away mid-wait.
AWAKE_SECONDS = 0.25


# A save slot, which a test run must not see and must not overwrite.
_SAVE_FILE = re.compile(r"^save\d*\.r[xv]data2?$", re.IGNORECASE)


def launch_command(game_dir: Path) -> "list[str]":
    """The command that starts the copied game.

    A module-level function so the unit tests can substitute a stand-in that
    speaks the same protocol (`tests/fixtures/fake_game.py`).
    """
    exe = game_dir / "Game.exe"
    if not exe.is_file():
        raise LiveError(
            "the project has no Game.exe, so there is nothing to run. RPG "
            "Maker VX Ace puts one in every project it creates; copy it in "
            "from a new project.")
    if sys.platform != "win32":
        raise LiveError("Game.exe runs on Windows only")
    return [str(exe)]


def scratch_root() -> Path:
    return Path(tempfile.gettempdir())


_JOB: "KillOnClose | None" = None
_CURRENT: "Session | None" = None


def current() -> "Session | None":
    return _CURRENT


def _job() -> KillOnClose:
    global _JOB
    if _JOB is None:
        _JOB = KillOnClose()
    return _JOB


def _make_writable(function, name, _error) -> None:
    """`rmtree`'s answer to a file it may not delete: clear the flag, retry.

    Windows refuses to delete a read-only file, and `copy2` copies the flag
    along. A game kept in git has a `.git` folder of read-only objects; with
    `ignore_errors` alone the copy of one could never be removed.
    """
    try:
        os.chmod(name, stat.S_IWRITE | stat.S_IREAD)
        function(name)
    except OSError:
        pass


def _remove_tree(path: Path) -> bool:
    """Delete a scratch folder, retrying while Windows releases the files."""
    for _ in range(25):
        shutil.rmtree(path, onerror=_make_writable)
        if not path.exists():
            return True
        time.sleep(0.2)
    return False


def sweep_orphans() -> "list[str]":
    """Delete scratch folders left by a server that is no longer running.

    Only folders carrying this server's marker, and only when the server
    process the marker names is gone -- another server's live session is
    left alone.
    """
    removed = []
    for folder in scratch_root().glob(SCRATCH_PREFIX + "*"):
        marker = folder / MARKER_FILE
        try:
            owner = json.loads(marker.read_text(encoding="utf-8"))["server_pid"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if (not isinstance(owner, int) or isinstance(owner, bool)
                or not 0 < owner < 2 ** 32):
            continue  # a marker this server did not write
        if owner != os.getpid() and not pid_alive(owner):
            if _remove_tree(folder):
                removed.append(str(folder))
    return removed


def _copy_project(source: Path, target: Path) -> "tuple[int, int]":
    """Copy a project for a test run; return (files, bytes) copied.

    Skipped: save files, this server's own work folder, version-control
    folders (history, not game data, and often the largest thing in the
    project), and links -- a
    junction inside a project could otherwise pull in a folder from
    anywhere on the disk. Bounded by `MAX_SCRATCH_BYTES` and
    `MAX_SCRATCH_FILES`, counted before anything is written.
    """
    plan: "list[tuple[Path, Path]]" = []
    total = 0
    stack = [source]
    while stack:
        folder = stack.pop()
        with os.scandir(folder) as entries:
            for entry in entries:
                if entry.is_symlink() or entry.name in _NOT_COPIED:
                    continue
                path = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    if _is_junction(path):
                        continue
                    stack.append(path)
                elif entry.is_file(follow_symlinks=False):
                    if _SAVE_FILE.match(entry.name):
                        continue
                    total += entry.stat(follow_symlinks=False).st_size
                    plan.append((path, target / path.relative_to(source)))
                if len(plan) > MAX_SCRATCH_FILES or total > MAX_SCRATCH_BYTES:
                    raise LiveError(
                        "the project is larger than a live session copies "
                        "(%d files or %d bytes at most)"
                        % (MAX_SCRATCH_FILES, MAX_SCRATCH_BYTES))
    for origin, destination in plan:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origin, destination)
    return len(plan), total


def _is_junction(path: Path) -> bool:
    is_junction = getattr(os.path, "isjunction", None)
    if is_junction is not None:
        return bool(is_junction(path))
    try:
        return bool(os.lstat(path).st_file_attributes & 0x400)
    except (AttributeError, OSError):
        return False


def _scripts_path(game_dir: Path) -> Path:
    """Where Game.ini says the script pack is, or the default."""
    ini = game_dir / "Game.ini"
    try:
        for line in ini.read_text(encoding="utf-8", errors="replace").splitlines():
            key, _, value = line.partition("=")
            if key.strip().lower() == "scripts" and value.strip():
                chosen = game_dir / value.strip().replace("\\", "/")
                # Game.ini is the project's own file: an absolute value
                # (`/` discards the left side) or a `..` one would send the
                # injected script pack to a file outside the scratch copy.
                try:
                    chosen.resolve().relative_to(game_dir.resolve())
                except (ValueError, OSError):
                    break
                return chosen
    except OSError:
        pass
    return game_dir / "Data" / "Scripts.rvdata2"


class Session:
    """A running scratch copy of a project, and the wire to its bridge."""

    def __init__(self, project_root: Path, folder: Path, options: dict):
        self.project_root = project_root
        self.folder = folder
        self.game_dir = folder / "game"
        self.bridge_dir = self.game_dir / wire.BRIDGE_DIR
        self.options = options
        self.token = uuid.uuid4().hex
        self.process: "subprocess.Popen | None" = None
        self.seq = 0
        self.started = time.time()
        self.adopted = False
        self.copied: "tuple[int, int]" = (0, 0)
        self.shots = 0

    # -- setting up ---------------------------------------------------------
    def prepare(self) -> None:
        """Copy the project, inject the bridge, and write the session config."""
        (self.folder / MARKER_FILE).write_text(json.dumps(
            {"server_pid": os.getpid(), "project": str(self.project_root)}),
            encoding="utf-8")
        self.copied = _copy_project(self.project_root, self.game_dir)
        scripts_file = _scripts_path(self.game_dir)
        if not scripts_file.is_file():
            raise LiveError(
                "the copy has no %s, so the bridge has nowhere to go. An "
                "encrypted game (Game.rgss3a) cannot be driven this way."
                % scripts_file.name)
        scripts = marshal_rb.load(scripts_file.read_bytes())
        bridge.inject(scripts)
        scripts_file.write_bytes(marshal_rb.dump(scripts))
        self.bridge_dir.mkdir(parents=True, exist_ok=True)
        wire.write_atomic(self.bridge_dir / wire.CONFIG_FILE, wire.to_ruby({
            "token": self.token,
            "paused": bool(self.options.get("paused")),
            "skip_title": bool(self.options.get("skip_title")),
            "allow_eval": bool(self.options.get("allow_eval")),
        }))

    def launch(self, command: "list[str]") -> dict:
        """Start the game and wait, bounded, for the bridge to say it is up."""
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        self.process = subprocess.Popen(
            command, cwd=str(self.game_dir), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=flags)
        self.adopted = _job().adopt(self.process.pid)
        ready = self.bridge_dir / wire.READY_FILE
        deadline = time.monotonic() + BOOT_SECONDS
        nudged = 0.0
        while time.monotonic() < deadline:
            nudged = self._nudge(nudged)
            if self.process.poll() is not None:
                raise LiveError("the game exited while starting, with code "
                                "%s%s" % (self.process.returncode,
                                          self.crash_sentence()))
            if ready.exists():
                try:
                    hello = wire.read_message(ready)
                except (OSError, ValueError):
                    hello = None
                if isinstance(hello, dict) and hello.get("token") == self.token:
                    return hello
            if self.dialogs():
                # Not a timeout: RGSS showed a message box before the
                # bridge could start -- an error in a script slot above
                # it, most often -- and is waiting for a click.
                raise LiveError(
                    "the game stopped while starting%s. It has been ended."
                    % self.dialog_sentence())
            time.sleep(POLL_SECONDS * 5)
        raise LiveError(
            "the game did not come up within %g seconds%s. It has been "
            "ended." % (BOOT_SECONDS, self.dialog_sentence()))

    # -- what the game is doing ---------------------------------------------
    @property
    def pid(self) -> "int | None":
        return self.process.pid if self.process else None

    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def exit_code(self) -> "int | None":
        return None if self.process is None else self.process.poll()

    def crash(self) -> "dict | None":
        """The Ruby exception the bridge caught, if it caught one."""
        path = self.bridge_dir / wire.CRASH_FILE
        if not path.exists():
            return None
        try:
            found = wire.read_message(path)
        except (OSError, ValueError):
            return None
        return found if isinstance(found, dict) else None

    def crash_sentence(self) -> str:
        found = self.crash()
        if not found:
            return ""
        return ". The game raised %s: %s" % (found.get("class"),
                                             found.get("message"))

    def dialogs(self) -> "list[dict[str, str]]":
        return dialog_texts(self.pid) if self.alive() else []

    def dialog_sentence(self) -> str:
        shown = self.dialogs()
        if not shown:
            return ""
        return (". It is showing a dialog -- %s"
                % "; ".join("%s: %s" % (d["title"], d["text"]) for d in shown))

    # -- the wire -------------------------------------------------------------
    def request(self, op: str, args: "dict | None" = None,
                seconds: "float | None" = None) -> dict:
        """Send one request and wait, bounded, for its reply."""
        self.require_alive()
        self.seq += 1
        seq = self.seq
        request_path = self.bridge_dir / wire.REQUEST_FILE
        response_path = self.bridge_dir / wire.RESPONSE_FILE
        wire.write_atomic(request_path, wire.to_ruby(
            {"seq": seq, "op": op, "args": args or {}}))
        limit = REPLY_SECONDS if seconds is None else seconds
        deadline = time.monotonic() + limit
        nudged = 0.0
        while time.monotonic() < deadline:
            nudged = self._nudge(nudged)
            if response_path.exists():
                reply = self._take(response_path)
                if isinstance(reply, dict) and reply.get("seq") == seq:
                    return self._answer(op, reply)
            if not self.alive():
                self.require_alive()
            time.sleep(POLL_SECONDS)
        try:
            request_path.unlink()
            picked_up = False
        except OSError:
            picked_up = True
        raise LiveError(
            "the game did not answer %s within %g seconds%s. It is still "
            "running (pid %s), so it is busy somewhere the bridge cannot "
            "see -- a loop that never reaches Graphics.update, a scene "
            "transition, a long load%s. Ask for status, or stop it."
            % (op, limit,
               "" if picked_up else ", and never picked the request up",
               self.pid, self.dialog_sentence()))

    def _nudge(self, last: float) -> float:
        """Remind the game it is active, at most every `AWAKE_SECONDS`."""
        now = time.monotonic()
        if now - last < AWAKE_SECONDS or self.pid is None:
            return last
        keep_awake(self.pid)
        return now

    def _take(self, path: Path) -> Any:
        try:
            reply = wire.read_message(path)
        except (OSError, ValueError):
            return None
        try:
            path.unlink()
        except OSError:
            pass
        return reply

    def _answer(self, op: str, reply: dict) -> dict:
        if not reply.get("ok"):
            raise LiveError("the game refused %s: %s" % (op, reply.get("error")))
        result = reply.get("result")
        envelope = {key: reply.get(key)
                    for key in ("frame", "paused", "scene", "crashed")
                    if key in reply}
        if isinstance(result, dict):
            return {**envelope, **result}
        return {**envelope, "result": result}

    def require_alive(self) -> None:
        if self.alive():
            return
        raise LiveError(
            "the game exited, with code %s%s. Its copy is kept until stop, "
            "so status can still be read." % (self.exit_code(),
                                              self.crash_sentence()))

    # -- the window -----------------------------------------------------------
    def screenshot(self) -> "tuple[bytes, dict]":
        """The game window as PNG bytes, and what was captured."""
        self.require_alive()
        windows = [w for w in windows_of(self.pid)
                   if w["class"] == GAME_WINDOW_CLASS] or windows_of(self.pid)
        if not windows:
            raise LiveError("the game has no visible window to capture")
        if restore_without_focus(windows[0]["hwnd"]):
            # A minimised window has no client area to print. Restored
            # without taking focus, then given a moment to draw a frame.
            keep_awake(self.pid)
            time.sleep(0.2)
        width, height, bgra, method = capture_window(windows[0]["hwnd"])
        rgb = frame.bgra_to_rgb(bgra)
        size, scaled = (width, height), False
        data = png.encode(width, height, rgb, 3)
        while len(data) > MAX_SCREENSHOT_BYTES and width > 1 and height > 1:
            width, height, rgb = frame.halve(width, height, rgb)
            data = png.encode(width, height, rgb, 3)
            scaled = True
        self.shots += 1
        path = self.folder / "screenshots" / ("shot%03d.png" % self.shots)
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(data)
        info = {"width": width, "height": height, "captured_with": method,
                "window_size": list(size), "path": str(path),
                "png_bytes": len(data)}
        if scaled:
            info["scaled"] = True
        return data, info

    # -- ending ---------------------------------------------------------------
    def end(self) -> dict:
        """Stop the game and delete the copy. Safe to call twice."""
        if self.alive():
            # Asked to quit, and given time to, only if it heard the
            # question: a game that did not answer `quit` is wedged, and
            # waiting for it to exit as well spent both timeouts back to
            # back before the kill that was always coming.
            try:
                self.request("quit", seconds=QUIT_SECONDS)
                self.process.wait(timeout=QUIT_SECONDS)
            except (LiveError, subprocess.TimeoutExpired):
                pass
        if self.alive():
            self.process.kill()
            try:
                self.process.wait(timeout=QUIT_SECONDS)
            except subprocess.TimeoutExpired:
                pass
        crash = self.crash()
        removed = _remove_tree(self.folder)
        out = {"exit_code": self.exit_code(), "scratch_removed": removed,
               "scratch_dir": str(self.game_dir)}
        if crash:
            out["crash"] = crash
        return out

    def describe(self) -> dict:
        out = {"running": self.alive(), "pid": self.pid,
               "exit_code": self.exit_code(),
               "scratch_dir": str(self.game_dir),
               "project": str(self.project_root),
               "seconds": round(time.time() - self.started, 1),
               "ends_with_server": self.adopted,
               "started_with": dict(self.options)}
        crash = self.crash()
        if crash:
            out["crash"] = crash
        shown = self.dialogs()
        if shown:
            out["dialogs"] = shown
        return out


def start(project_root: Path, options: dict,
          launcher: "Callable[[Path], list[str]] | None" = None) -> dict:
    """Begin the one session, or refuse because one is running."""
    global _CURRENT
    if _CURRENT is not None:
        if _CURRENT.alive():
            raise LiveError(
                "a live session is already running (pid %s, %s). Call stop "
                "first: there is one game window, so there is one session."
                % (_CURRENT.pid, _CURRENT.game_dir))
        stop()
    swept = sweep_orphans()
    folder = Path(tempfile.mkdtemp(prefix=SCRATCH_PREFIX, dir=scratch_root()))
    candidate = Session(Path(project_root), folder, options)
    try:
        candidate.prepare()
        hello = candidate.launch((launcher or launch_command)(candidate.game_dir))
    except BaseException:
        candidate.end()
        raise
    _CURRENT = candidate
    out = candidate.describe()
    out.update(frame=hello.get("frame"), scene=hello.get("scene"),
               files_copied=candidate.copied[0],
               bytes_copied=candidate.copied[1])
    if swept:
        out["orphans_removed"] = swept
    return out


def stop() -> dict:
    """End the session, if there is one."""
    global _CURRENT
    if _CURRENT is None:
        return {"stopped": False}
    ending, _CURRENT = _CURRENT, None
    return {"stopped": True, **ending.end()}


atexit.register(stop)
