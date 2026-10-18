"""Which database entries sit far from the rest of their own table.

A field is compared against the same field across the entries of its own
table, and nothing else: no level curve, no expected-power model, no other
game. An outlier here is a prompt to look -- a final boss *should* have the
largest HP in the enemies table -- and never a defect, which is why the reply
says "outlier" and not "problem".

Method
------
* Quantiles by linear interpolation between order statistics (the
  "type 7" definition: the default of NumPy and R, and Excel's QUARTILE.INC).
  Position ``p * (n - 1)`` in the sorted values, interpolated between the two
  neighbours. Hand-rolled because it is eight lines and the project has no
  third-party dependencies; `statistics.quantiles` would also do.
* The criterion is Tukey's fences: below ``q1 - 1.5 * IQR`` or above
  ``q3 + 1.5 * IQR``. Quartiles are robust to the very outliers being hunted,
  and the fences need no assumption about the shape of the distribution,
  which a table of twenty weapons does not have.
* A modified z-score (``0.6745 * (x - median) / MAD``) is reported beside
  each outlier as a second opinion; it is not the criterion. It is ``None``
  when the median absolute deviation is zero.

What is not compared
--------------------
Only an explicit allowlist of balance-relevant numeric fields is read
(`_FIELDS_BY_ENGINE`). Ids, icon indexes, animation ids, scopes, occasions,
hit types, equipment-type codes, flags and anything else that is a code
rather than a quantity are not on it, because an "outlier" among enum codes
is meaningless. A field that is on the list is still skipped -- and reported
under ``skipped`` with the reason -- when:

* fewer than `_MIN_VALUES` entries have a numeric value for it, since a
  quartile of three numbers is not a distribution; or
* its interquartile range is zero, i.e. at least half of the table shares
  one value. Tukey's fences then collapse onto that value and *every* other
  value would be flagged, which is a statement about the field being mostly
  constant (a weapon's MAT is 0 on nearly every weapon) and not about the
  entries.

Entries with a blank name are unused editor slots and are left out of every
table (counted under ``unnamed_entries_ignored``): a database padded to a
hundred rows with forty real ones would otherwise have zeros for its median.

Values that are nil, non-numeric, or booleans (a Python ``bool`` is an
``int``; no RGSS stat is one) are ignored per entry rather than aborting the
table, and an array-valued field (``params``) that is missing, short or not
an array contributes no value for that entry.

The engines store stats differently, so there is one allowlist per
engine: VX Ace has a ``params`` array on weapons, armors and enemies and a
``params`` Table on classes (stat x level); VX has flat fields, and keeps
its per-level curves in a Table on actors; XP has flat fields of its own
(str, dex, agi, int, pdef, mdef, SP) and its curves in an actor Table too.
"""

from __future__ import annotations

import math
from typing import Any

from ..project import Project, ProjectError
from ..rubytypes import RubyObject, Table
from .walk import _text


# Fewer values than this and a field is skipped, not judged.
_MIN_VALUES = 8

# Tukey's multiplier. 1.5 flags "outside"; 3.0 would be "far out".
_FENCE_FACTOR = 1.5

# Outliers returned per field. The count that *exist* is always reported.
_MAX_OUTLIERS_PER_FIELD = 10

# A table longer than this is not read: RPG Maker's editor tops out at 9999
# entries, and an array padded past that is a crafted file, not a project.
_MAX_ROWS = 20_000

# Levels at which a per-level Table of stats is sampled.
_CURVE_LEVELS = (1, 99)


_ACE_PARAM_NAMES = ("mhp", "mmp", "atk", "def", "mat", "mdf", "agi", "luk")
_VX_PARAM_NAMES = ("mhp", "mmp", "atk", "def", "spi", "agi")


# A field is addressed one of three ways. Each is a tuple whose first item
# is the kind, so the allowlist below stays plain data:
#   ("attr", name)               entry.name
#   ("param", index)             entry.params[index]
#   ("curve", attr, stat, level) entry.attr  (a Table) at [stat, level]
def _attr(name: str) -> tuple:
    return (name, ("attr", name))


def _params(names: tuple[str, ...], prefix: str = "") -> list[tuple]:
    return [(prefix + name, ("param", index))
            for index, name in enumerate(names)]


def _curves(attr: str, names: tuple[str, ...]) -> list[tuple]:
    return [("%s@L%d" % (name, level), ("curve", attr, stat, level))
            for level in _CURVE_LEVELS for stat, name in enumerate(names)]


# table -> [(label, address)]. The label is what the reply calls the field.
_ACE_FIELDS: dict[str, list[tuple]] = {
    "actors": [_attr("initial_level"), _attr("max_level")],
    "classes": _curves("params", _ACE_PARAM_NAMES),
    "skills": [_attr(n) for n in ("mp_cost", "tp_cost", "success_rate",
                                  "repeats", "speed", "tp_gain")],
    "items": [_attr(n) for n in ("price", "success_rate", "repeats",
                                 "speed", "tp_gain")],
    "weapons": [_attr("price")] + _params(_ACE_PARAM_NAMES),
    "armors": [_attr("price")] + _params(_ACE_PARAM_NAMES),
    "enemies": _params(_ACE_PARAM_NAMES) + [_attr("exp"), _attr("gold")],
    "states": [_attr(n) for n in ("priority", "min_turns", "max_turns",
                                  "chance_by_damage", "steps_to_remove")],
}


_VX_FIELDS: dict[str, list[tuple]] = {
    "actors": ([_attr(n) for n in ("initial_level", "exp_basis",
                                   "exp_inflation")]
               + _curves("parameters", _VX_PARAM_NAMES)),
    # `position` is the front/middle/back row, a code; a VX class carries no
    # other plain number (its curves live on the actors).
    "classes": [],
    "skills": [_attr(n) for n in ("mp_cost", "base_damage", "variance",
                                  "hit", "atk_f", "spi_f")],
    "items": [_attr(n) for n in ("price", "recover_hp", "recover_mp",
                                 "recover_hp_rate", "recover_mp_rate",
                                 "parameter_points")],
    "weapons": [_attr(n) for n in ("price", "atk", "def", "spi", "agi")],
    "armors": [_attr(n) for n in ("price", "def", "eva", "atk", "spi",
                                  "agi")],
    "enemies": [_attr(n) for n in ("maxhp", "maxmp", "atk", "def", "spi",
                                   "agi", "hit", "eva", "exp", "gold")],
    "states": [_attr(n) for n in ("priority", "hold_turn",
                                  "auto_release_prob", "reduce_hit_ratio",
                                  "reduce_hp_by_walking")],
}


# XP's actor curves are `parameters[kind, level]`: max HP, max SP and the
# four stats, from the RGSS1 reference's RPG::Actor page.
_XP_PARAM_NAMES = ("maxhp", "maxsp", "str", "dex", "agi", "int")
_XP_STATS = ("str_plus", "dex_plus", "agi_plus", "int_plus")


# From the RGSS1 reference. A class carries only codes and rank Tables, like
# VX's; an XP state's rates are percentages, and quantities like any other.
_XP_FIELDS: dict[str, list[tuple]] = {
    "actors": ([_attr(n) for n in ("initial_level", "final_level",
                                   "exp_basis", "exp_inflation")]
               + _curves("parameters", _XP_PARAM_NAMES)),
    "classes": [],
    "skills": [_attr(n) for n in ("sp_cost", "power", "atk_f", "eva_f",
                                  "str_f", "dex_f", "agi_f", "int_f", "hit",
                                  "pdef_f", "mdef_f", "variance")],
    "items": [_attr(n) for n in ("price", "recover_hp", "recover_sp",
                                 "recover_hp_rate", "recover_sp_rate",
                                 "parameter_points", "hit", "variance")],
    "weapons": [_attr(n) for n in ("price", "atk", "pdef", "mdef",
                                   *_XP_STATS)],
    "armors": [_attr(n) for n in ("price", "pdef", "mdef", "eva",
                                  *_XP_STATS)],
    "enemies": [_attr(n) for n in ("maxhp", "maxsp", "str", "dex", "agi",
                                   "int", "atk", "pdef", "mdef", "eva",
                                   "exp", "gold", "treasure_prob")],
    "states": [_attr(n) for n in ("rating", "hold_turn", "auto_release_prob",
                                  "shock_release_prob", "hit_rate",
                                  "maxhp_rate", "maxsp_rate", "atk_rate",
                                  "pdef_rate", "mdef_rate", "eva")],
}


_FIELDS_BY_ENGINE = {"vx_ace": _ACE_FIELDS, "vx": _VX_FIELDS,
                     "xp": _XP_FIELDS}


def _quantile(ordered: list[float], p: float) -> float:
    """The p-_quantile of an ascending list, by linear interpolation.

    Position ``p * (n - 1)``, interpolated between its two neighbouring
    order statistics. `ordered` must be non-empty and sorted.
    """
    position = p * (len(ordered) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


_MAX_MAGNITUDE = 2 ** 53


def _number(value: Any) -> "float | int | None":
    """A finite number out of a project file, or None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    # A game stat is a small Fixnum. A Bignum, or a float near the limit of
    # the type, overflows the fences (OverflowError is not an error the
    # server reports) and past 4,300 digits cannot even be printed.
    if abs(value) > _MAX_MAGNITUDE:
        return None
    return value


def _read(entry: Any, address: tuple) -> "float | int | None":
    """One field of one entry, or None when it is absent or malformed."""
    if not isinstance(entry, RubyObject):
        return None
    kind = address[0]
    if kind == "attr":
        return _number(entry.get(address[1]))
    if kind == "param":
        params = entry.get("params")
        index = address[1]
        if isinstance(params, list) and 0 <= index < len(params):
            return _number(params[index])
        return None
    if kind == "curve":
        _, attr, stat, level = address
        table = entry.get(attr)
        if (isinstance(table, Table) and stat < table.xsize
                and level < table.ysize and table.zsize > 0):
            return _number(table.get(stat, level, 0))
        return None
    return None


def _round(value: float) -> "float | int | None":
    if isinstance(value, int):
        return value
    if not math.isfinite(value):
        return None
    rounded = round(value, 4)
    return int(rounded) if rounded == int(rounded) else rounded


def _modified_z(value: float, median: float, mad: float) -> "float | None":
    if mad == 0:
        return None
    return _round(0.6745 * (value - median) / mad)


def tukey_fences(values: "list[float] | list[int]",
                 population: str = "table's entries"
                 ) -> "dict[str, float] | str":
    """Quartiles and Tukey's fences of `values`, or why there are none.

    The one statement of the method this module documents, so a second
    population -- `critique_summary` fences a project's maps with it -- is
    judged by the same rule rather than a copy of it: fewer than
    `_MIN_VALUES` values or a zero interquartile range is a reason string,
    because a fence over either flags noise. ``population`` names what the
    values are in that reason.
    """
    if len(values) < _MIN_VALUES:
        return ("only %d value%s; fewer than %d is too few for a quartile "
                "to mean anything" % (len(values),
                                      "" if len(values) == 1 else "s",
                                      _MIN_VALUES))
    ordered = sorted(values)
    q1 = _quantile(ordered, 0.25)
    q3 = _quantile(ordered, 0.75)
    iqr = q3 - q1
    if iqr == 0:
        return ("interquartile range is zero (q1 = q3 = %s): at least half "
                "the %s share one value, so every other value would be "
                "flagged" % (_round(q1), population))
    return {"n": len(ordered), "median": _quantile(ordered, 0.5),
            "q1": q1, "q3": q3, "iqr": iqr,
            "lower_fence": q1 - _FENCE_FACTOR * iqr,
            "upper_fence": q3 + _FENCE_FACTOR * iqr}


def _summarize(values: list[tuple[int, str, float]],
              limit: int = _MAX_OUTLIERS_PER_FIELD) -> "dict | str":
    """Quartiles, fences and outliers of ``(id, name, value)`` rows.

    Returns a reason string instead when the field must be skipped.
    """
    fences = tukey_fences([v for _, _, v in values])
    if isinstance(fences, str):
        return fences
    ordered = sorted(v for _, _, v in values)
    median, q1, q3, iqr = (fences["median"], fences["q1"], fences["q3"],
                           fences["iqr"])
    mad = _quantile(sorted(abs(v - median) for v in ordered), 0.5)
    lower = fences["lower_fence"]
    upper = fences["upper_fence"]
    found = []
    for entry_id, name, value in values:
        if value < lower:
            found.append((lower - value, entry_id, name, value, "low"))
        elif value > upper:
            found.append((value - upper, entry_id, name, value, "high"))
    found.sort(key=lambda item: (-item[0], item[1]))
    out = {
        "n": len(values),
        "median": _round(median),
        "q1": _round(q1),
        "q3": _round(q3),
        "iqr": _round(iqr),
        "lower_fence": _round(lower),
        "upper_fence": _round(upper),
        "outliers_total": len(found),
        "outliers": [
            {"id": entry_id, "name": name, "value": _round(value),
             "direction": direction,
             "modified_z": _modified_z(value, median, mad)}
            for _, entry_id, name, value, direction in found[:limit]
        ],
    }
    if len(found) > limit:
        out["truncated"] = True
    return out


def stat_outlier_report(project: Project,
                  limit: int = _MAX_OUTLIERS_PER_FIELD) -> dict[str, Any]:
    """Every allowlisted field of every database table, against itself."""
    allowlist = _FIELDS_BY_ENGINE.get(project.engine, _VX_FIELDS)
    fields: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    ignored: dict[str, int] = {}
    for table, specs in allowlist.items():
        if not specs:
            skipped.append({
                "table": table,
                "reason": "this engine's %s carry no balance-relevant "
                          "numeric field of their own" % table})
            continue
        try:
            entries = project.entries(table)
        except (ProjectError, ValueError, OSError) as error:
            skipped.append({"table": table,
                            "reason": "table could not be read: %s" % error})
            continue
        if len(entries) > _MAX_ROWS:
            skipped.append({
                "table": table,
                "reason": "%d entries is past the %d this analysis reads"
                          % (len(entries), _MAX_ROWS)})
            continue
        # An entry with a blank name is an unused slot the editor leaves
        # behind. Its zeros would drag every quartile to 0 and make the
        # real entries the outliers, so it is left out and counted.
        names = {entry_id: _text(project, entry.get("name"))
                 for entry_id, entry in entries
                 if isinstance(entry, RubyObject)}
        blank = [entry_id for entry_id, name in names.items()
                 if not name.strip()]
        if blank:
            ignored[table] = len(blank)
        entries = [(entry_id, entry) for entry_id, entry in entries
                   if names.get(entry_id, "").strip()]
        for label, address in specs:
            rows = []
            for entry_id, entry in entries:
                value = _read(entry, address)
                if value is not None:
                    rows.append((entry_id, names.get(entry_id, ""), value))
            result = _summarize(rows, limit)
            if isinstance(result, str):
                skipped.append({"table": table, "field": label,
                                "reason": result})
            else:
                fields.append({"table": table, "field": label, **result})
    return {
        "method": {
            "criterion": "Tukey fences: outside [q1 - 1.5*IQR, q3 + 1.5*IQR]",
            "quantiles": "linear interpolation between order statistics "
                         "(type 7; NumPy/R default)",
            "modified_z": "0.6745*(x - median)/MAD, informational only; "
                          "null when MAD is 0",
            "min_values": _MIN_VALUES,
            "max_outliers_per_field": limit,
            "fields_considered": {
                table: [label for label, _ in specs]
                for table, specs in allowlist.items()},
            "class_curves_sampled_at_levels": list(_CURVE_LEVELS),
        },
        "fields": fields,
        "skipped": skipped,
        "unnamed_entries_ignored": ignored,
        "note": "An outlier is a prompt to look, not a defect: a boss "
                "should be the largest HP in its table, a legendary weapon "
                "the highest price. Each field is compared only with the "
                "same field in its own table.",
    }
