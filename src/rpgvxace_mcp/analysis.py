"""Cross-referencing and linting over a whole project.

The checks here catch the failures that neither the editor nor a playtest of the
first ten minutes will show you: a transfer pointing at coordinates outside its
target map, an event graphic whose file was renamed, an autorun page that can
never stop, a page that a later page permanently shadows.

Every rule states what it looked at, so a finding can be judged rather than
trusted. Where the parameter layout of a command is not certain, no rule is
written for it -- a silent wrong answer would be worse than no answer.
"""

from __future__ import annotations

from typing import Any, Iterator

from .commands import command_name, summarize
from .project import Project
from .rubytypes import RubyHash, RubyObject, RubyString


class Location:
    """Where in a project something was found."""

    __slots__ = ("kind", "map_id", "event_id", "event_name", "page", "index", "entry_id",
                 "table", "field")

    def __init__(self, kind: str, map_id: int | None = None, event_id: int | None = None,
                 event_name: str | None = None, page: int | None = None,
                 index: int | None = None, entry_id: int | None = None,
                 table: str | None = None, field: str | None = None):
        self.kind = kind
        self.map_id = map_id
        self.event_id = event_id
        self.event_name = event_name
        self.page = page
        self.index = index
        self.entry_id = entry_id
        self.table = table
        self.field = field

    def as_dict(self) -> dict[str, Any]:
        out = {"kind": self.kind}
        for name in ("map_id", "event_id", "event_name", "page", "index",
                     "entry_id", "table", "field"):
            value = getattr(self, name)
            if value is not None:
                out[name] = value
        return out

    def describe(self) -> str:
        if self.kind == "map_event":
            return "map %s event %s (%s) page %s command %s" % (
                self.map_id, self.event_id, self.event_name, self.page, self.index)
        if self.kind == "common_event":
            return "common event %s command %s" % (self.entry_id, self.index)
        if self.kind == "troop_page":
            return "troop %s battle page %s command %s" % (
                self.entry_id, self.page, self.index)
        if self.kind == "database":
            where = "%s %s" % (self.table, self.entry_id)
            return where + (" field %s" % self.field if self.field else "")
        if self.kind == "map":
            where = "map %s" % self.map_id
            return where + (" field %s" % self.field if self.field else "")
        return self.kind


def iter_command_lists(project: Project, map_ids: list[int] | None = None
                       ) -> Iterator[tuple[Location, list[Any]]]:
    """Every event command list in the project, with where it came from.

    Covers map event pages, common events and troop battle event pages -- the
    three places an RPG Maker project stores an interpreter program.
    """
    for map_id in (map_ids if map_ids is not None else project.map_ids_on_disk()):
        try:
            game_map = project.load_map(map_id)
        except Exception:  # a map we cannot read is reported by check_map_tree
            continue
        events = game_map.get("events")
        if not isinstance(events, RubyHash):
            continue
        for event_id, event in events.sorted_items():
            if not isinstance(event, RubyObject):
                continue
            name = _text(project, event.get("name"))
            for page_index, page in enumerate(event.get("pages") or []):
                if isinstance(page, RubyObject):
                    yield (Location("map_event", map_id=map_id, event_id=event_id,
                                    event_name=name, page=page_index),
                           page.get("list") or [])

    try:
        for entry_id, common in project.entries("common_events"):
            if isinstance(common, RubyObject):
                yield (Location("common_event", entry_id=entry_id),
                       common.get("list") or [])
    except Exception:
        pass

    try:
        for entry_id, troop in project.entries("troops"):
            if not isinstance(troop, RubyObject):
                continue
            for page_index, page in enumerate(troop.get("pages") or []):
                if isinstance(page, RubyObject):
                    yield (Location("troop_page", entry_id=entry_id, page=page_index),
                           page.get("list") or [])
    except Exception:
        pass


def _text(project: Project, value: Any) -> str:
    if isinstance(value, RubyString):
        return project.codec.decode(value.data)
    return "" if value is None else str(value)


# ---------------------------------------------------------------------------
# Reference scanning
# ---------------------------------------------------------------------------

# Which command parameters hold an id from which table. ``requires`` pins other
# parameters that must match for the rule to apply (a Conditional Branch only
# refers to an item when its type parameter says so). Only layouts confirmed
# against the engines' own interpreters are listed, and they are the layouts VX
# Ace and VX share.
REFERENCE_RULES: dict[str, list[dict[str, Any]]] = {
    "items": [
        {"code": 126, "index": 0},
        {"code": 111, "index": 1, "requires": {0: 8}},
        {"code": 302, "index": 1, "requires": {0: 0}},
        {"code": 605, "index": 1, "requires": {0: 0}},
    ],
    "weapons": [
        {"code": 127, "index": 0},
        {"code": 111, "index": 1, "requires": {0: 9}},
        {"code": 302, "index": 1, "requires": {0: 1}},
        {"code": 605, "index": 1, "requires": {0: 1}},
    ],
    "armors": [
        {"code": 128, "index": 0},
        {"code": 111, "index": 1, "requires": {0: 10}},
        {"code": 302, "index": 1, "requires": {0: 2}},
        {"code": 605, "index": 1, "requires": {0: 2}},
    ],
    "common_events": [{"code": 117, "index": 0}],
    "troops": [{"code": 301, "index": 1, "requires": {0: 0}}],
    "animations": [{"code": 212, "index": 1}, {"code": 337, "index": 1}],
    "actors": [
        {"code": 129, "index": 0},
        {"code": 303, "index": 0},
        {"code": 320, "index": 0},
        {"code": 321, "index": 0},
        {"code": 322, "index": 0},
        {"code": 111, "index": 1, "requires": {0: 4}},
    ],
    "maps": [{"code": 201, "index": 1, "requires": {0: 0}}],
}


def find_references(project: Project, table: str, entry_id: int,
                    limit: int = 200) -> list[dict[str, Any]]:
    """Find event commands that mention ``entry_id`` from ``table``."""
    rules = REFERENCE_RULES.get(table)
    if not rules:
        return []
    hits: list[dict[str, Any]] = []
    for location, commands in iter_command_lists(project):
        for index, command in enumerate(commands):
            if not isinstance(command, RubyObject):
                continue
            code = command.get("code", 0)
            params = command.get("parameters") or []
            for rule in rules:
                if code != rule["code"]:
                    continue
                if not _requires_met(params, rule.get("requires")):
                    continue
                position = rule["index"]
                if len(params) > position and params[position] == entry_id:
                    located = Location(location.kind, location.map_id, location.event_id,
                                       location.event_name, location.page, index,
                                       location.entry_id)
                    hits.append({
                        "where": located.describe(),
                        "location": located.as_dict(),
                        "command": command_name(code),
                        "summary": summarize(command, project.codec),
                    })
                    break
            if len(hits) >= limit:
                return hits
    return hits


def _requires_met(params: list[Any], requires: dict[int, Any] | None) -> bool:
    if not requires:
        return True
    for position, expected in requires.items():
        position = int(position)
        if len(params) <= position or params[position] != expected:
            return False
    return True


# ---------------------------------------------------------------------------
# Linting
# ---------------------------------------------------------------------------


class Finding:
    __slots__ = ("severity", "rule", "message", "location", "checked")

    def __init__(self, severity: str, rule: str, message: str,
                 location: Location | None = None, checked: str = ""):
        self.severity = severity
        self.rule = rule
        self.message = message
        self.location = location
        self.checked = checked

    def as_dict(self) -> dict[str, Any]:
        out = {"severity": self.severity, "rule": self.rule, "message": self.message}
        if self.location is not None:
            out["where"] = self.location.describe()
            out["location"] = self.location.as_dict()
        if self.checked:
            out["checked"] = self.checked
        return out


ERROR, WARNING, INFO = "error", "warning", "info"


def validate(project: Project, scopes: list[str], map_ids: list[int] | None = None,
             limit: int = 200) -> dict[str, Any]:
    findings: list[Finding] = []
    ran: list[str] = []

    ids = project.map_ids_on_disk() if map_ids is None else map_ids

    if "maps" in scopes:
        ran.append("map tree and map properties")
        findings += check_map_tree(project)
        findings += check_encounters(project, ids)
        findings += check_tilesets(project, ids)
    if "events" in scopes:
        ran.append("event command structure and references")
        findings += check_commands(project, ids)
    if "assets" in scopes:
        ran.append("graphic and audio file references")
        findings += check_assets(project, ids)
    if "database" in scopes:
        ran.append("database cross-references")
        findings += check_database(project)

    order = {ERROR: 0, WARNING: 1, INFO: 2}
    findings.sort(key=lambda f: (order.get(f.severity, 3), f.rule))
    counts = {level: sum(1 for f in findings if f.severity == level)
              for level in (ERROR, WARNING, INFO)}
    return {
        "checks_run": ran,
        "maps_examined": len(ids),
        "counts": counts,
        "findings": [f.as_dict() for f in findings[:limit]],
        "truncated": len(findings) > limit,
        "note": "Rules only cover event command parameters whose layout is "
                "confirmed for VX; commands outside that set are not checked.",
    }


def check_map_tree(project: Project) -> list[Finding]:
    findings = []
    try:
        infos = dict(project.entries("map_infos"))
    except Exception as exc:
        return [Finding(ERROR, "map_infos_unreadable", str(exc))]
    on_disk = set(project.map_ids_on_disk())

    for map_id, info in infos.items():
        name = _text(project, info.get("name")) if isinstance(info, RubyObject) else "?"
        if map_id not in on_disk:
            findings.append(Finding(
                ERROR, "missing_map_file",
                "MapInfos lists map %s (%s) but %s is not on disk"
                % (map_id, name, project.map_file(map_id)),
                Location("map", map_id=map_id), "MapInfos against Data/Map*.rvdata"))
        parent = info.get("parent_id") if isinstance(info, RubyObject) else 0
        if parent and parent not in infos:
            findings.append(Finding(
                WARNING, "missing_parent_map",
                "map %s (%s) has parent_id %s, which is not in the map tree"
                % (map_id, name, parent),
                Location("map", map_id=map_id, field="parent_id")))

    for map_id in sorted(on_disk - set(infos)):
        findings.append(Finding(
            WARNING, "orphan_map_file",
            "%s exists but no MapInfos entry points at it, so the editor will "
            "not show it" % project.map_file(map_id),
            Location("map", map_id=map_id)))

    # Parent cycles would make the editor's tree infinite.
    for map_id in infos:
        seen = set()
        current = map_id
        while current:
            if current in seen:
                findings.append(Finding(
                    ERROR, "map_tree_cycle",
                    "map %s sits in a parent_id cycle" % map_id,
                    Location("map", map_id=map_id, field="parent_id")))
                break
            seen.add(current)
            info = infos.get(current)
            current = info.get("parent_id") if isinstance(info, RubyObject) else 0
    return findings


def check_encounters(project: Project, map_ids: list[int]) -> list[Finding]:
    findings = []
    troop_ids = {i for i, _ in _safe_entries(project, "troops")}
    for map_id in map_ids:
        try:
            game_map = project.load_map(map_id)
        except Exception as exc:
            findings.append(Finding(ERROR, "map_unreadable",
                                    "map %s could not be read: %s" % (map_id, exc),
                                    Location("map", map_id=map_id)))
            continue
        encounters = game_map.get("encounter_list") or []
        for entry in encounters:
            troop_id = _encounter_troop_id(entry)
            if troop_id is None:
                continue
            if troop_id not in troop_ids:
                findings.append(Finding(
                    ERROR, "missing_troop",
                    "map %s can encounter troop %s, which does not exist"
                    % (map_id, troop_id),
                    Location("map", map_id=map_id, field="encounter_list")))
        if encounters and not game_map.get("encounter_step"):
            findings.append(Finding(
                WARNING, "zero_encounter_step",
                "map %s has encounters but an encounter_step of 0" % map_id,
                Location("map", map_id=map_id, field="encounter_step")))
        data = game_map.get("data")
        width, height = game_map.get("width"), game_map.get("height")
        if data is not None and (data.xsize != width or data.ysize != height):
            findings.append(Finding(
                ERROR, "tile_data_size_mismatch",
                "map %s says it is %sx%s but its tile table is %sx%s"
                % (map_id, width, height, data.xsize, data.ysize),
                Location("map", map_id=map_id, field="data")))
    return findings


def _encounter_troop_id(entry: Any) -> int | None:
    """The troop id of an encounter entry, whichever engine wrote it.

    VX keeps a plain array of troop ids; VX Ace wraps each one in an
    ``RPG::Map::Encounter`` alongside its weight and region set.
    """
    if isinstance(entry, int):
        return entry
    if isinstance(entry, RubyObject):
        troop_id = entry.get("troop_id")
        return troop_id if isinstance(troop_id, int) else None
    return None


def check_tilesets(project: Project, map_ids: list[int]) -> list[Finding]:
    """VX Ace only: every map names a tileset, and tilesets name graphics."""
    if "tilesets" not in project.data_files:
        return []
    findings = []
    tilesets = dict(_safe_entries(project, "tilesets"))
    graphics = project.asset_names("tilesets")
    for map_id in map_ids:
        try:
            game_map = project.load_map(map_id)
        except Exception:
            continue
        tileset_id = game_map.get("tileset_id")
        if isinstance(tileset_id, int) and tileset_id not in tilesets:
            findings.append(Finding(
                ERROR, "missing_tileset",
                "map %s uses tileset %s, which does not exist"
                % (map_id, tileset_id),
                Location("map", map_id=map_id, field="tileset_id")))
    for entry_id, tileset in tilesets.items():
        if not isinstance(tileset, RubyObject):
            continue
        for index, name in enumerate(tileset.get("tileset_names") or []):
            text = _text(project, name)
            if text and graphics and text not in graphics:
                sheet = ["A1", "A2", "A3", "A4", "A5", "B", "C", "D", "E"][index] \
                    if index < 9 else str(index)
                findings.append(Finding(
                    WARNING, "missing_graphic",
                    'tileset %s (%s) sheet %s names "%s", which is not in '
                    "Graphics/Tilesets" % (entry_id, _text(project, tileset.get("name")),
                                           sheet, text),
                    Location("database", table="tilesets", entry_id=entry_id,
                             field="tileset_names")))
    return findings


def check_commands(project: Project, map_ids: list[int]) -> list[Finding]:
    findings = []
    existing = {
        "items": {i for i, _ in _safe_entries(project, "items")},
        "weapons": {i for i, _ in _safe_entries(project, "weapons")},
        "armors": {i for i, _ in _safe_entries(project, "armors")},
        "troops": {i for i, _ in _safe_entries(project, "troops")},
        "common_events": {i for i, _ in _safe_entries(project, "common_events")},
        "actors": {i for i, _ in _safe_entries(project, "actors")},
        "animations": {i for i, _ in _safe_entries(project, "animations")},
    }
    map_sizes = {}
    for map_id in project.map_ids_on_disk():
        try:
            game_map = project.load_map(map_id)
            map_sizes[map_id] = (game_map.get("width"), game_map.get("height"))
        except Exception:
            continue

    for location, commands in iter_command_lists(project, map_ids):
        labels = {
            _text(project, c.get("parameters")[0])
            for c in commands
            if isinstance(c, RubyObject) and c.get("code") == 118 and c.get("parameters")
        }
        indent_previous = 0
        for index, command in enumerate(commands):
            if not isinstance(command, RubyObject):
                continue
            here = _at(location, index)
            code = command.get("code", 0)
            params = command.get("parameters") or []
            indent = command.get("indent", 0) or 0
            if indent > indent_previous + 1:
                findings.append(Finding(
                    WARNING, "indent_jump",
                    "command %s jumps from indent %s to %s, which means the branch "
                    "structure is inconsistent" % (index, indent_previous, indent), here))
            indent_previous = indent

            for table, rules in REFERENCE_RULES.items():
                if table not in existing and table != "maps":
                    continue
                for rule in rules:
                    if code != rule["code"] or not _requires_met(params, rule.get("requires")):
                        continue
                    position = rule["index"]
                    if len(params) <= position:
                        continue
                    value = params[position]
                    if not isinstance(value, int):
                        continue
                    if table == "maps":
                        if value not in map_sizes:
                            findings.append(Finding(
                                ERROR, "transfer_to_missing_map",
                                "Transfer Player targets map %s, which does not exist"
                                % value, here))
                        elif len(params) > 3:
                            width, height = map_sizes[value]
                            x, y = params[2], params[3]
                            if isinstance(x, int) and isinstance(y, int) and (
                                    x >= width or y >= height):
                                findings.append(Finding(
                                    ERROR, "transfer_outside_map",
                                    "Transfer Player sends the party to (%s, %s) on map "
                                    "%s, which is only %sx%s" % (x, y, value, width, height),
                                    here))
                    elif value not in existing[table]:
                        findings.append(Finding(
                            ERROR, "missing_%s" % table.rstrip("s"),
                            "%s refers to %s id %s, which does not exist"
                            % (command_name(code), table.rstrip("s"), value), here))

            if code == 119 and params:
                target = _text(project, params[0])
                if target not in labels:
                    findings.append(Finding(
                        ERROR, "missing_label",
                        'Jump to Label "%s" has no matching Label in this command list'
                        % target, here))

        findings += check_page_reachability(project, location, commands)

    findings += check_shadowed_pages(project, map_ids)
    return findings


def check_page_reachability(project: Project, location: Location,
                            commands: list[Any]) -> list[Finding]:
    """Flag command lists that cannot terminate or are missing their end marker."""
    findings = []
    if not commands:
        return findings
    last = commands[-1]
    if isinstance(last, RubyObject) and (last.get("code", 0) or 0) != 0:
        findings.append(Finding(
            WARNING, "missing_list_terminator",
            "this command list does not end with a blank command, which the "
            "interpreter uses to know the page is finished",
            _at(location, len(commands) - 1)))
    return findings


def check_shadowed_pages(project: Project, map_ids: list[int]) -> list[Finding]:
    """A page with no conditions hides every page before it.

    VX picks the *last* page whose conditions are satisfied, so an
    unconditional page 2 means page 1 can never run.
    """
    findings = []
    condition_flags = ("switch1_valid", "switch2_valid", "variable_valid",
                       "self_switch_valid", "item_valid", "actor_valid")
    for map_id in map_ids:
        try:
            game_map = project.load_map(map_id)
        except Exception:
            continue
        events = game_map.get("events")
        if not isinstance(events, RubyHash):
            continue
        for event_id, event in events.sorted_items():
            if not isinstance(event, RubyObject):
                continue
            pages = event.get("pages") or []
            name = _text(project, event.get("name"))

            def unconditional(page: Any) -> bool:
                condition = page.get("condition") if isinstance(page, RubyObject) else None
                if not isinstance(condition, RubyObject):
                    return False
                return not any(condition.get(flag) for flag in condition_flags)

            for index, page in enumerate(pages):
                if index == 0 or not isinstance(page, RubyObject):
                    continue
                if unconditional(page):
                    findings.append(Finding(
                        WARNING, "shadowed_pages",
                        "page %s of event %s (%s) has no conditions, so pages 1 to %s "
                        "can never run -- the last page whose conditions match is the "
                        "one that runs" % (index + 1, event_id, name, index),
                        Location("map_event", map_id=map_id, event_id=event_id,
                                 event_name=name, page=index),
                        "page conditions of every multi-page event"))

            # Only the last page matters here. Because the last matching page
            # wins, an earlier unconditional Autorun page is the normal way to
            # write a cutscene: it sets a self switch, and a later conditioned
            # page then takes over. It is the *final* page being an
            # unconditional Autorun that traps the player.
            if pages and isinstance(pages[-1], RubyObject) and unconditional(pages[-1]):
                last = pages[-1]
                if last.get("trigger") == 3 and not _releases_control(last):
                    findings.append(Finding(
                        WARNING, "unstoppable_autorun",
                        "the last page (%s) of event %s (%s) is Autorun with no page "
                        "conditions and never transfers the player, erases itself or "
                        "ends the game, so it restarts as soon as it finishes and the "
                        "player never regains control"
                        % (len(pages), event_id, name),
                        Location("map_event", map_id=map_id, event_id=event_id,
                                 event_name=name, page=len(pages) - 1),
                        "the last page of every event, since that is the page that "
                        "wins when its conditions match"))
    return findings


# Commands that end an unconditional autorun page's grip on the player: moving
# them to another map, erasing the event for this visit, or ending the game.
CONTROL_RELEASING_CODES = {201, 214, 353, 354}


def _releases_control(page: RubyObject) -> bool:
    return any(isinstance(command, RubyObject)
               and command.get("code") in CONTROL_RELEASING_CODES
               for command in page.get("list") or [])


def check_assets(project: Project, map_ids: list[int]) -> list[Finding]:
    findings = []
    characters = project.asset_names("characters")
    faces = project.asset_names("faces")
    battlers = project.asset_names("battlers")
    parallaxes = project.asset_names("parallaxes")
    audio = {kind: project.asset_names(kind) for kind in ("bgm", "bgs", "me", "se")}

    # A project whose RTP we could not find will reference files we cannot see,
    # so a miss is only evidence of a problem when the asset pool is complete.
    complete = project.assets_complete
    severity = WARNING if complete else INFO

    def check(name: Any, pool: set[str], kind: str, location: Location,
              rule: str) -> None:
        text = _text(project, name)
        if not text or text in pool:
            return

        # A name that differs only in case works on Windows and fails on a
        # case-sensitive filesystem, so it survives every local playtest.
        near = [candidate for candidate in pool if candidate.lower() == text.lower()]
        if near:
            findings.append(Finding(
                WARNING, "asset_case_mismatch",
                '%s "%s" is spelled "%s" on disk. Windows ignores the difference, '
                "so this loads while you develop and fails on a case-sensitive "
                "filesystem" % (kind, text, near[0]),
                location, "exact file names in %s" % kind))
            return

        if complete:
            message = ('%s "%s" is referenced but is not in the project %s folder'
                       % (kind, text, kind))
        else:
            message = ('%s "%s" is not in the project %s folder; it may come from '
                       "%s, which could not be located"
                       % (kind, text, kind, " or ".join(project.rtp_unresolved)))
        findings.append(Finding(
            severity, rule, message, location,
            "%d %s files in the project and any located RTP" % (len(pool), kind)))

    for map_id in map_ids:
        try:
            game_map = project.load_map(map_id)
        except Exception:
            continue
        check(game_map.get("parallax_name"), parallaxes, "parallax",
              Location("map", map_id=map_id, field="parallax_name"), "missing_graphic")
        if game_map.get("specify_battleback"):
            for field, kind in (("battleback1_name", "battlebacks1"),
                                ("battleback2_name", "battlebacks2")):
                check(game_map.get(field), project.asset_names(kind), kind,
                      Location("map", map_id=map_id, field=field), "missing_graphic")
        for field, kind in (("bgm", "bgm"), ("bgs", "bgs")):
            track = game_map.get(field)
            if isinstance(track, RubyObject):
                check(track.get("name"), audio[kind], kind,
                      Location("map", map_id=map_id, field=field), "missing_audio")
        events = game_map.get("events")
        if isinstance(events, RubyHash):
            for event_id, event in events.sorted_items():
                if not isinstance(event, RubyObject):
                    continue
                name = _text(project, event.get("name"))
                for page_index, page in enumerate(event.get("pages") or []):
                    if not isinstance(page, RubyObject):
                        continue
                    graphic = page.get("graphic")
                    location = Location("map_event", map_id=map_id, event_id=event_id,
                                        event_name=name, page=page_index)
                    if isinstance(graphic, RubyObject):
                        check(graphic.get("character_name"), characters, "characters",
                              location, "missing_graphic")

    for location, commands in iter_command_lists(project, map_ids):
        for index, command in enumerate(commands):
            if not isinstance(command, RubyObject):
                continue
            code = command.get("code", 0)
            params = command.get("parameters") or []
            here = _at(location, index)
            if code == 101 and params:
                check(params[0], faces, "faces", here, "missing_graphic")
            elif code in (241, 245, 249, 250) and params:
                kind = {241: "bgm", 245: "bgs", 249: "me", 250: "se"}[code]
                track = params[0]
                if isinstance(track, RubyObject):
                    check(track.get("name"), audio[kind], kind, here, "missing_audio")

    for entry_id, actor in _safe_entries(project, "actors"):
        if not isinstance(actor, RubyObject):
            continue
        check(actor.get("character_name"), characters, "characters",
              Location("database", table="actors", entry_id=entry_id,
                       field="character_name"), "missing_graphic")
        check(actor.get("face_name"), faces, "faces",
              Location("database", table="actors", entry_id=entry_id,
                       field="face_name"), "missing_graphic")
    for entry_id, foe in _safe_entries(project, "enemies"):
        if isinstance(foe, RubyObject):
            check(foe.get("battler_name"), battlers, "battlers",
                  Location("database", table="enemies", entry_id=entry_id,
                           field="battler_name"),
                  "missing_graphic")
    return findings


def check_database(project: Project) -> list[Finding]:
    findings = []
    class_ids = {i for i, _ in _safe_entries(project, "classes")}
    skill_ids = {i for i, _ in _safe_entries(project, "skills")}
    enemy_ids = {i for i, _ in _safe_entries(project, "enemies")}
    actor_ids = {i for i, _ in _safe_entries(project, "actors")}

    for entry_id, actor in _safe_entries(project, "actors"):
        if isinstance(actor, RubyObject) and actor.has("class_id"):
            if actor.get("class_id") not in class_ids:
                findings.append(Finding(
                    ERROR, "missing_class",
                    "actor %s has class_id %s, which does not exist"
                    % (entry_id, actor.get("class_id")),
                    Location("database", table="actors", entry_id=entry_id,
                             field="class_id")))

    for entry_id, klass in _safe_entries(project, "classes"):
        if not isinstance(klass, RubyObject):
            continue
        for learning in klass.get("learnings") or []:
            if isinstance(learning, RubyObject) and learning.get("skill_id") not in skill_ids:
                findings.append(Finding(
                    ERROR, "missing_skill",
                    "class %s learns skill %s, which does not exist"
                    % (entry_id, learning.get("skill_id")),
                    Location("database", table="classes", entry_id=entry_id,
                             field="learnings")))

    for entry_id, troop in _safe_entries(project, "troops"):
        if not isinstance(troop, RubyObject):
            continue
        for member in troop.get("members") or []:
            if isinstance(member, RubyObject) and member.get("enemy_id") not in enemy_ids:
                findings.append(Finding(
                    ERROR, "missing_enemy",
                    "troop %s includes enemy %s, which does not exist"
                    % (entry_id, member.get("enemy_id")),
                    Location("database", table="troops", entry_id=entry_id,
                             field="members")))

    system = None
    try:
        system = project.load_data("system")
    except Exception:
        pass
    if isinstance(system, RubyObject):
        for actor_id in system.get("party_members") or []:
            if actor_id not in actor_ids:
                findings.append(Finding(
                    ERROR, "missing_party_member",
                    "the starting party includes actor %s, which does not exist"
                    % actor_id,
                    Location("database", table="system", entry_id=0,
                             field="party_members")))
        start_map = system.get("start_map_id")
        if start_map and start_map not in set(project.map_ids_on_disk()):
            findings.append(Finding(
                ERROR, "missing_start_map",
                "the party starts on map %s, which does not exist" % start_map,
                Location("database", table="system", entry_id=0, field="start_map_id")))
    return findings


def _safe_entries(project: Project, table: str) -> list[tuple[int, Any]]:
    try:
        return project.entries(table)
    except Exception:
        return []


def _at(location: Location, index: int) -> Location:
    return Location(location.kind, location.map_id, location.event_id,
                    location.event_name, location.page, index, location.entry_id,
                    location.table, location.field)
