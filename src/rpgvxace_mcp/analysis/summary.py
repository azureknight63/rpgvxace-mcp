"""The findings of every `map_critique` scope, ranked, with no tables.

Each scope answers one question in full -- per map, per layer, per field --
and on a real project the six full answers came to 64 KB, most of it
numbers about maps nothing was wrong with. This reads those answers and
keeps only what is worth a look, in the order to look at it:

* **high** -- would block play if it is not intended: a dead end the graph
  is sure of, and a switch or variable gate that nothing in the project
  ever sets.
* **medium** -- the same shapes where something this reader cannot see
  (a variable or script transfer, a script that touches switches by a
  computed index) may account for it; and maps with content that nothing
  leads to.
* **low** -- unusual against this project's own data: a map far emptier or
  far plainer than its siblings, the most extreme entries of a database
  table, and unreachable maps that are blank (folder-like placeholders).

Within a severity the order is the order of that list. Every finding is a
prompt to look, never a verdict, and says what to look for under
``prompt_to_look``.

What counts as "unusual" for a map is not a number written here. Dead
space (``empty_ground_fraction``) and tile variety (ground-layer
``entropy_bits``) are fenced by `stat_outliers.tukey_fences` over this
project's own non-blank maps -- the method `stat_outliers` applies to a
table, with its rules about too few values and a zero spread -- so the same
map is unusual in one project and ordinary in another. Blank maps are left
out of both populations: they are placeholders, and fifty of them would
make every built map look full.

Two absolute floors sit under those relative fences, because on a real
game the fences alone flagged nothing: most maps there have no empty
ground, so the dead-space IQR was 0 and the fence was skipped, and the
ground entropy spread so wide that its lower fence was below zero, so not
even a map of one tile id was past it.

* **Uniform ground.** A non-blank map whose ground layer uses exactly one
  tile id is flagged ``uniform_ground`` whatever the fence says, and is not
  listed again under ``low_tile_variety``. A map with no tile at all is not
  uniform ground: its ground is 100% empty, which dead space says.
* **Dead space with no spread.** When the IQR over enough maps is zero,
  the maps with *some* empty ground are fenced among themselves if at least
  `_MIN_VALUES` of them have it and they spread; otherwise any map more
  than half empty is past the fence. ``map_fences`` states which rule ran,
  under ``fallback``, beside the reason Tukey's fence was skipped.

Pure: reads the scope replies it is handed and walks nothing, so it costs
no allowance and cannot stop.
"""

from __future__ import annotations

from typing import Any

from .stat_outliers import _MIN_VALUES, _round, tukey_fences


SEVERITIES = ("high", "medium", "low")

# Past this empty-ground fraction a map is unusual when the project gives
# too little spread to fence it: more than half of the ground is void.
_HALF_EMPTY = 0.5


# The rules `map_fences` states for the two floors, kept short: they ride
# in every default reply.
_DEAD_SPACE_FALLBACK_RULE = (
    "Tukey's over the maps with some empty ground if %d or more do and "
    "they spread, else more than %d%% empty"
    % (_MIN_VALUES, round(_HALF_EMPTY * 100)))
_UNIFORM_GROUND_RULE = (
    "whatever the fence says: ground of exactly one tile id (a map with no "
    "tile is dead space)")

# Stat outlier fields named in the summary, most extreme first.
_TOP_OUTLIER_FIELDS = 5

# Where each scope says it did not finish.
_STOPPED_KEYS = (("reachability", "reachability_stopped"),
                 ("dead_ends", "dead_ends_stopped"),
                 ("switch_usage", "switch_usage_stopped"),
                 ("dead_space", "dead_space_stopped"),
                 ("tile_variety", "tile_variety_stopped"))


SUMMARY_NOTE = (
    "Ranked -- high: would block play if unintended; medium: the same, but "
    "something static reading cannot see may explain it, or content "
    "nothing leads to; low: unusual for this project's maps or tables. "
    "Each finding is a prompt to look, not a verdict. Map fences: Tukey's "
    "over the non-blank maps, with floors under them (map_fences). Full "
    "tables: name dead_space, tile_variety or stat_outliers in scopes.")


def _finding(severity: str, scope: str, finding: str, prompt: str,
             **subject: Any) -> dict[str, Any]:
    return {"severity": severity, "scope": scope, "finding": finding,
            **subject, "prompt_to_look": prompt}


def _maps(ids: "list[int]") -> str:
    return ", ".join(str(i) for i in ids)


def _dead_end_findings(dead: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    regions = [([e["map_id"]], e) for e in dead.get("no_exit", [])]
    regions += [(r["maps"], r) for r in dead.get("closed_regions", [])]
    for maps, entry in regions:
        uncertain = bool(entry.get("uncertain"))
        where = ("map %d has no transfer out" % maps[0] if len(maps) == 1
                 else "maps %s only lead to each other" % _maps(maps))
        prompt = "%s once entered: an intended ending, or a missing exit?" % (
            where[0].upper() + where[1:])
        if uncertain:
            prompt += " (Something unseen may leave: %s.)" % ", ".join(
                entry.get("reasons", []))
        subject: dict[str, Any] = (
            {"map_id": maps[0]} if len(maps) == 1 else {"maps": maps})
        subject["entered_from"] = entry.get("entered_from", [])
        if uncertain:
            subject["uncertain"] = True
        out.append(_finding("medium" if uncertain else "high", "dead_ends",
                            "no_exit" if len(maps) == 1 else "closed_region",
                            prompt, **subject))
    return out


def _gate_findings(usage: dict[str, Any]) -> list[dict[str, Any]]:
    """Reads nothing sets, whose condition fails at the initial value."""
    out = []
    for kind, noun in (("switches", "switch"), ("variables", "variable")):
        block = (usage.get(kind) or {}).get("read_but_never_set") or {}
        for row in block.get("shown", []):
            reads = row.get("read_at", [])
            if reads and all(r.get("holds_while_unset") is True
                             for r in reads):
                continue    # always true while unset: a constant, not a gate
            label = "%s %d" % (noun, row["id"])
            if row.get("name"):
                label += " (%s)" % row["name"]
            subject: dict[str, Any] = {"id": row["id"]}
            if row.get("name"):
                subject["name"] = row["name"]
            subject["reads"] = row.get("reads")
            if reads:
                subject["first_read"] = reads[0].get("where")
            subject["confidence"] = row.get("confidence")
            out.append(_finding(
                "high" if row.get("confidence") == "high" else "medium",
                "switch_usage", "%s_never_set" % noun,
                "%s is read and nothing sets it, so what it gates never "
                "happens: is the command that sets it missing?"
                % (label[0].upper() + label[1:]), **subject))
    return out


def _unreachable_findings(reach: dict[str, Any]
                          ) -> "tuple[list[dict[str, Any]], dict[str, Any] | None]":
    """``(content maps finding, blank maps finding)``; either may be empty."""
    if reach.get("start_map") is None:
        return [], None     # no start to walk from; `reachability_note` says so
    detail = {d["map_id"]: d for d in reach.get("unreachable_detail", [])}
    blank = [m for m in reach.get("unreachable", [])
             if detail.get(m, {}).get("blank") is True]
    built = [m for m in reach.get("unreachable", []) if m not in blank]
    unseen = (reach.get("unresolved_transfers") or 0) \
        + (reach.get("unresolved_script_transfers") or 0)
    content = []
    if built:
        subject: dict[str, Any] = {"maps": built}
        if unseen:
            subject["uncertain"] = True
        content.append(_finding(
            "medium", "reachability", "unreachable",
            "No transfer from the start reaches map%s %s, which %s content: "
            "unused, or is a door missing?%s"
            % ("" if len(built) == 1 else "s", _maps(built),
               "has" if len(built) == 1 else "have",
               " (%d transfer%s could not be followed.)"
               % (unseen, "" if unseen == 1 else "s") if unseen else ""),
            **subject))
    placeholder = None
    if blank:
        placeholder = _finding(
            "low", "reachability", "unreachable_blank",
            "Blank (no tile, no event) and unreachable: folder maps, safe to "
            "ignore unless one was meant to be built.", maps=blank)
    return content, placeholder


def _map_fence(rows: "list[tuple[int, float]]", metric: str, side: str
               ) -> "tuple[dict[str, Any], list[tuple[int, float]]]":
    """The fence over one per-map metric, and the rows past it on `side`."""
    fences = tukey_fences([v for _, v in rows], population="maps")
    if isinstance(fences, str):
        return {"metric": metric, "skipped": fences}, []
    edge = "upper_fence" if side == "high" else "lower_fence"
    limit = fences[edge]
    past = [(m, v) for m, v in rows
            if (v > limit if side == "high" else v < limit)]
    return ({"metric": metric, "n": fences["n"],
             "q1": _round(fences["q1"]), "q3": _round(fences["q3"]),
             edge: _round(limit)}, past)


def _columns(rows: "list[tuple[int, float]]", metric: str
             ) -> dict[str, Any]:
    """``maps`` as ids, the way every finding lists them, and ``metric``
    beside it in the same order: a dict per map cost twice the bytes in a
    reply held to 8 KB."""
    return {"maps": [m for m, _ in rows],
            metric: [_round(v) for _, v in rows]}


def _dead_space_fallback(rows: "list[tuple[int, float]]"
                         ) -> "tuple[dict[str, Any], list[tuple[int, float]]]":
    """The dead-space fence when most maps have no empty ground at all.

    Then q1 = q3 = 0 and Tukey's fence is skipped, which on a real game
    left a map 93% void unflagged. The maps that have *some* empty ground
    are fenced among themselves instead; with too few of them for a
    quartile, or no spread among them either, any map more than half
    empty is past the fence.
    """
    some = [(m, v) for m, v in rows if v > 0]
    fence, past = _map_fence(some, "empty_ground_fraction", "high")
    if "skipped" not in fence:
        del fence["metric"]
        return {"rule": _DEAD_SPACE_FALLBACK_RULE, **fence}, past
    past = [(m, v) for m, v in rows if v > _HALF_EMPTY]
    return {"rule": _DEAD_SPACE_FALLBACK_RULE, "n": len(some),
            "upper_fence": _HALF_EMPTY}, past


def _uniform_ground(variety: dict[str, Any], blank: "set[int]"
                    ) -> "tuple[list[int], list[int]]":
    """Non-blank maps at the floor `_UNIFORM_GROUND_RULE` states.

    Exactly one distinct ground id. A map with none has 100% empty ground,
    which `unusual_dead_space` already says, so it is not listed here too.
    A map whose ground distinct-id count is not reported is not judged.
    ``(maps, those of them with nothing on an upper tile layer either)``.
    """
    out: list[int] = []
    bare: list[int] = []
    for row in variety.get("maps", []):
        if row["map_id"] in blank:
            continue
        layers = {layer.get("layer"): layer for layer in row.get("layers", [])}
        distinct = (layers.get(0) or {}).get("distinct_tiles")
        if distinct != 1:
            continue
        out.append(row["map_id"])
        if all(layer.get("non_empty_cells") == 0
               for z, layer in layers.items() if z != 0):
            bare.append(row["map_id"])
    return out, bare


def _map_findings(shapes: dict[str, Any], variety: dict[str, Any]
                  ) -> "tuple[list[dict[str, Any]], dict[str, Any]]":
    blank = {row["map_id"] for row in shapes.get("maps", [])
             if row.get("blank") is True}
    empty = [(row["map_id"], row["empty_ground_fraction"])
             for row in shapes.get("maps", [])
             if row["map_id"] not in blank
             and row.get("empty_ground_fraction") is not None]
    ground = []
    for row in variety.get("maps", []):
        layers = {layer.get("layer"): layer for layer in row.get("layers", [])}
        bits = (layers.get(0) or {}).get("entropy_bits")
        if row["map_id"] not in blank and bits is not None:
            ground.append((row["map_id"], bits))
    space_fence, spacious = _map_fence(empty, "empty_ground_fraction", "high")
    space_limit = space_fence.get("upper_fence")
    if "skipped" in space_fence and len(empty) >= _MIN_VALUES:
        # Enough maps, and no spread: the zero-IQR case the fallback is for.
        space_fence["fallback"], spacious = _dead_space_fallback(empty)
        space_limit = space_fence["fallback"]["upper_fence"]
    variety_fence, plain = _map_fence(ground, "ground_entropy_bits", "low")
    uniform, bare = _uniform_ground(variety, blank)
    # A map at the floor is listed there, not twice.
    plain = [(m, v) for m, v in plain if m not in uniform]
    out = []
    if spacious:
        out.append(_finding(
            "low", "dead_space", "unusual_dead_space",
            "Far more empty ground than this project's maps usually have: "
            "intended void, or unfinished?",
            **_columns(spacious, "empty_ground_fraction"),
            upper_fence=space_limit))
    if uniform:
        out.append(_finding(
            "low", "tile_variety", "uniform_ground",
            "One ground tile id: intended (a lake, a bare floor), or "
            "unfinished?", maps=uniform, upper_layers_empty=bare))
    if plain:
        out.append(_finding(
            "low", "tile_variety", "low_tile_variety",
            "Far fewer kinds of ground tile than this project's maps usually "
            "use: intended (a lake, a bare floor), or unfinished?",
            **_columns(plain, "ground_entropy_bits"),
            lower_fence=variety_fence["lower_fence"]))
    return out, {"dead_space": space_fence, "tile_variety": variety_fence,
                 "uniform_ground": {"rule": _UNIFORM_GROUND_RULE}}


def _outlier_finding(outliers: dict[str, Any]) -> "dict[str, Any] | None":
    """The most extreme entry of each field, the few furthest out."""
    ranked = []
    for field in outliers.get("fields", []):
        if not field.get("outliers"):
            continue
        worst = field["outliers"][0]
        high = worst["direction"] == "high"
        fence = field["upper_fence"] if high else field["lower_fence"]
        # In IQRs past the fence, so fields on different scales compare.
        # `iqr` is rounded for the reply and can round to 0 when tiny.
        excess = (abs(worst["value"] - fence) / field["iqr"]
                  if field["iqr"] else float("inf"))
        ranked.append((-excess, field["table"], field["field"], {
            "table": field["table"], "field": field["field"],
            "id": worst["id"], "name": worst["name"],
            "value": worst["value"], "fence": fence,
            "outliers_in_field": field["outliers_total"]}))
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[:3])
    return _finding(
        "low", "stat_outliers", "stat_outliers",
        "Each is the most extreme entry of its field against its own table: "
        "a final boss should top its table, a typo should not.",
        entries=[entry for *_, entry in ranked[:_TOP_OUTLIER_FIELDS]],
        fields_with_outliers=len(ranked))


def critique_summary(reachability: dict[str, Any], dead_ends: dict[str, Any],
                     switch_usage: dict[str, Any], shapes: dict[str, Any],
                     variety: dict[str, Any], outliers: dict[str, Any]
                     ) -> dict[str, Any]:
    """Every finding worth a look, ranked, and what the map fences were.

    ``findings`` is the whole ranked list with a 1-based ``priority`` each;
    capping it is the caller's, which owns the reply's vocabulary for
    saying so. ``incomplete`` maps each input that did not finish to the
    sentence it gave, so the caller can say the summary is partial.
    """
    content, placeholder = _unreachable_findings(reachability)
    maps, fences = _map_findings(shapes, variety)
    outlier = _outlier_finding(outliers)
    found = (_dead_end_findings(dead_ends) + _gate_findings(switch_usage)
             + content + maps + ([outlier] if outlier else [])
             + ([placeholder] if placeholder else []))
    # Stable: within a severity, the order above is the order of priority.
    found.sort(key=lambda row: SEVERITIES.index(row["severity"]))
    inputs = {"reachability": reachability, "dead_ends": dead_ends,
              "switch_usage": switch_usage, "dead_space": shapes,
              "tile_variety": variety}
    incomplete = {scope: inputs[scope][key] for scope, key in _STOPPED_KEYS
                  if inputs[scope].get(key)}
    return {
        "findings": [{"priority": rank, **row}
                     for rank, row in enumerate(found, start=1)],
        "map_fences": fences,
        "incomplete": incomplete,
        "note": SUMMARY_NOTE,
    }
