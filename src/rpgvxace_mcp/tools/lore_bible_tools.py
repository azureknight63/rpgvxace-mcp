"""lore_bible: a guide to building out a game's lore, and the bible it keeps.

The server cannot interview anyone. The model in the conversation does the
asking, so this tool supplies what it needs to ask well and keeps the answers:
it scans the project for what the game already says, offers scope tiers with
comparisons to well-known games, serves questions from a curated bank, stores
answers and named entries in a bible file, exports the bible as a
self-contained HTML worksheet or as markdown, and reviews the bible against the
game's own text for contradictions and gaps.

It is guide-only. It never edits a data file of the game, and the only thing it
writes is `.rpgvxace-mcp/lore/` in the work folder, which is a cache like the
renders and the backups. The bible file is read back through a validator and
never trusted (see `lore_store`); the scan and the review live in
`analysis.lore_baseline`, loaded on first use like the other heavy analyses;
the guidance is data in `lore_guidance`; this module is the thin leaf that
reads the arguments and shapes the replies.

Under `--read-only`, `choose` and `save` are refused and `scan` answers
without saving, as the other tools that keep a cache treat their writes; `export`
still writes its cache files, as `map_render` writes its pictures.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import lore_store
from ..lore_export import render_html, render_markdown
from ..lore_guidance import (
    ESTIMATES_NOTE,
    GROUPS,
    QUESTIONS,
    SMALL_TEAM_NOTE,
    TIERS,
    TIER_ORDER,
    pitfall,
    question,
    questions_for_tier,
)
from ..lore_store import (
    BibleError,
    ENTRY_KINDS,
    EXPORT_FORMATS,
    MAX_ANSWER_CHARS,
    MAX_APPROACH_CHARS,
    MAX_BASELINE_BYTES,
    MAX_BASELINE_GAPS,
    MAX_BASELINE_NOUNS,
    MAX_BASELINE_SAMPLES,
    MAX_BASELINE_SAMPLE_CHARS,
    MAX_BASELINE_SPEAKERS,
    MAX_BASELINE_VARIANTS,
    MAX_BIBLE_BYTES,
    MAX_BIBLE_ENTRIES,
    MAX_DELETE_NAMES,
    MAX_ENTRIES_PER_SAVE,
    MAX_ENTRY_NAME_CHARS,
    MAX_ENTRY_TEXT_CHARS,
    MAX_QUESTIONS_PER_CALL,
    MAX_REVIEW_FINDINGS,
    MODES,
    REVIEW_SCOPES,
    check_answer_id,
    check_entry,
    check_text,
    entry_key,
)
from .registry import (
    ARR,
    BOOL,
    Context,
    INT,
    OBJ,
    REFRESH,
    STR,
    ToolError,
    check_op_args,
    one_of,
    opt_bool,
    opt_dict,
    opt_int_default,
    opt_list,
    opt_str,
    partial,
    prop,
    schema,
    scopes_arg,
    str_list,
    tool,
)


# What each op reads. The tool selects with an `op`, so an argument that is
# real but not this op's is refused rather than dropped.
LORE_OP_ARGS = {
    "scan": {"op", "refresh"},
    "tiers": {"op"},
    "choose": {"op", "tier", "mode", "approach"},
    "next": {"op", "count", "group"},
    "save": {"op", "answers", "entries", "delete_entries"},
    "status": {"op"},
    "review": {"op", "scopes"},
    "export": {"op", "formats", "worksheet"},
}


_GROUP_KEYS = tuple(key for key, _ in GROUPS)


_MODE_HELP = {
    "qa": "You ask the author the questions in the conversation, a few at a "
          "time (op=next), and keep their answers (op=save).",
    "artifact": "An HTML worksheet in the project: the author fills it in in "
                "a browser and pastes back the JSON its Copy my answers "
                "button puts on the clipboard, and you pass that to op=save.",
    "custom": "The author describes their own approach in words (the "
              "approach argument); you follow it, using the same question "
              "bank (op=next) and the same storage (op=save).",
}


# The pitfall a kind of review finding is an instance of.
_PITFALL_FOR_FINDING = {
    "not_in_game": "no_delivery_surface",
    "character_not_an_event": "no_delivery_surface",
    "place_not_a_map": "no_delivery_surface",
    "game_name_not_in_bible": "lore_needs_a_character",
    "spelling_variant": "mystery_vs_mistake",
}


def _n(value: int) -> str:
    return "{:,}".format(value)


_DESCRIPTION = (
    "Helps an author build out the lore of their game. The server cannot "
    "interview anyone, so this tool does not interview: you ask the author, "
    "and the tool supplies what you need to ask well and keeps the answers "
    "in a bible file under .rpgvxace-mcp/lore/ (a work folder like renders "
    "and backups, not game data). It never edits the game's data files; the "
    "only thing it writes is that folder. Work in this order. (1) scan reads "
    "what the game already says -- dialogue, choices, scrolling text, map "
    "display names, database names, descriptions and notes, and the System "
    "terms -- and returns a baseline: text volume by kind (comments are "
    "counted apart, as developer notes and not prose), capitalised names "
    "that recur and whether any description or note explains them, spelling "
    "variants, who speaks (by face graphic, else by event name), how the maps "
    "are named and laid out, where the text sits (action button, touch, "
    "autorun or parallel), and ranked plain-language gaps. (2) tiers offers "
    "four scope tiers (light, moderate, rich, deep), each with two or three "
    "comparison games and a one-line reason, what it asks of the author as "
    "labelled estimate ranges, where the player meets the lore and the "
    "biggest risk, plus a suggested_tier derived from the scan. (3) Ask the "
    "author which tier and which mode: qa (you ask in the conversation), "
    "artifact (an HTML worksheet the author fills in and pastes back) or "
    "custom (the author describes their own approach in words), and keep the "
    "choice with choose. (4) Run the mode: next serves the next unanswered "
    "questions of the chosen tier (it and every lower tier), each with why it "
    "matters, what a good answer looks like and, once scanned, often a "
    "from_your_game line to quote; save keeps answers by question id and "
    "named entries (place, character, faction, event, term, item, other); "
    "export writes bible.html and bible.md into the lore folder (worksheet "
    "true adds blank fields for the unanswered questions; the page's Copy my "
    "answers button puts {\"answers\": {...}} JSON on the clipboard for the "
    "author to paste to you, and you pass those answers to save). (5) review "
    "compares the bible with the game's own text and returns findings, each "
    "with a where and a question to ask the author: names the bible has that "
    "the game never says, recurring game names the bible lacks, names spelled "
    "two ways, characters and places the game never shows, maps the bible "
    "never mentions. status shows progress and the next step. A finding is a "
    "prompt to ask, not a verdict. scan and review read dialogue, so they "
    "work on VX Ace and VX only (XP is refused, as text refuses it); every "
    "other op works on any project. Under --read-only, choose and save are "
    "refused, scan answers without saving, and export still writes its cache "
    "files. Limits: the bible file holds at most %s bytes; an answer %s "
    "characters; the bible %s entries (%s per save call), an entry name %s "
    "characters and its text %s; delete_entries %s names; an approach %s "
    "characters; next serves at most %s questions a call; review returns at "
    "most %s findings (findings_total says how many there were). The saved "
    "baseline is at most %s bytes: it lists the %s most frequent names, %s "
    "spelling pairs, %s speakers with up to %s sample lines each (clipped to "
    "%s characters) and %s gaps. The scan is budgeted; when it stops early "
    "walk_stopped says so, and proper_nouns_truncated, speakers_truncated, "
    "vocabulary_truncated and files_not_read say what was cut."
    % (_n(MAX_BIBLE_BYTES), _n(MAX_ANSWER_CHARS), _n(MAX_BIBLE_ENTRIES),
       _n(MAX_ENTRIES_PER_SAVE), _n(MAX_ENTRY_NAME_CHARS),
       _n(MAX_ENTRY_TEXT_CHARS), _n(MAX_DELETE_NAMES),
       _n(MAX_APPROACH_CHARS), _n(MAX_QUESTIONS_PER_CALL),
       _n(MAX_REVIEW_FINDINGS), _n(MAX_BASELINE_BYTES),
       _n(MAX_BASELINE_NOUNS), _n(MAX_BASELINE_VARIANTS),
       _n(MAX_BASELINE_SPEAKERS), _n(MAX_BASELINE_SAMPLES),
       _n(MAX_BASELINE_SAMPLE_CHARS), _n(MAX_BASELINE_GAPS)))


def _refuse_bible(exc: BibleError) -> ToolError:
    return ToolError(str(exc))


def _text_engine(ctx: Context, op: str) -> None:
    """scan and review read Show Text, which XP lays out differently."""
    if not ctx.project.command_layouts_modelled:
        raise ToolError(
            "lore_bible %s reads the game's dialogue, and works on VX Ace "
            "and VX projects. This is a %s project: its Show Text keeps the "
            "first line in the 101 itself and has no face, so the layout "
            "this tool reads is not its own. The other ops (tiers, choose, "
            "next, save, status, export) work on any project."
            % (op, ctx.project.profile.label))


def _progress(bible: dict[str, Any]) -> dict[str, Any]:
    """Answered and total, per group, for the chosen tier (the lightest if none)."""
    tier = bible["tier"] or "light"
    groups = {key: {"answered": 0, "total": 0} for key in _GROUP_KEYS}
    for item in questions_for_tier(tier):
        row = groups[item["group"]]
        row["total"] += 1
        if item["id"] in bible["answers"]:
            row["answered"] += 1
    return {"groups": groups,
            "answered": sum(row["answered"] for row in groups.values()),
            "total": sum(row["total"] for row in groups.values())}


def _gap_kinds(baseline: Any) -> "set[str]":
    gaps = baseline.get("gaps") if isinstance(baseline, dict) else None
    return {gap["kind"] for gap in gaps
            if isinstance(gap, dict) and isinstance(gap.get("kind"), str)} \
        if isinstance(gaps, list) else set()


def _next_step(bible: dict[str, Any], progress: dict[str, Any]) -> str:
    if bible["baseline"] is None:
        return ("Run op=scan to read what the game already says, so the "
                "questions can quote it and the tiers can be suggested from "
                "it.")
    if bible["tier"] is None:
        return ("Run op=tiers to show the scope tiers, ask the author which "
                "tier and which mode they want (qa, artifact or custom), "
                "then op=choose.")
    if progress["answered"] < progress["total"]:
        if bible["mode"] == "artifact":
            return ("Run op=export with worksheet=true, have the author fill "
                    "the page in and paste what its Copy my answers button "
                    "copies, then pass those answers to op=save. op=next "
                    "still serves the questions in conversation.")
        return ("Run op=next to get the next questions, ask the author, then "
                "op=save their answers.")
    return ("Every question of the chosen tier is answered. Run op=review to "
            "check the bible against the game, then op=export for the "
            "finished bible.")


def _pitfalls(bible: dict[str, Any], progress: dict[str, Any]
              ) -> "list[dict[str, str]]":
    """The few pitfalls this bible shows a symptom of, at most three."""
    ids: "list[str]" = []
    if "unexplained_name" in _gap_kinds(bible["baseline"]):
        ids.append("lore_needs_a_character")
    if bible["entries"] and "delivery.channels" not in bible["answers"]:
        ids.append("no_delivery_surface")
    if bible["tier"] in ("rich", "deep"):
        ids.append("scope_to_capacity")
    return [{"id": found["id"], "rule": found["rule"], "why": found["why"]}
            for found in (pitfall(item) for item in ids[:3]) if found]


# ---------------------------------------------------------------------------
# The ops
# ---------------------------------------------------------------------------


def _op_scan(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    refresh = opt_bool(args, "refresh")
    _text_engine(ctx, "scan")
    from ..analysis import lore_baseline  # on first call: see tests/test_startup.py

    if refresh:
        ctx.project.forget_cached()
    baseline = lore_baseline.baseline_of(lore_baseline.lore_scan(ctx.project))
    saved = False
    notes: "tuple[str, ...]" = ()
    if ctx.project.read_only:
        notes = ("this server is read-only, so the baseline was not saved "
                 "to the bible; the tiers and the questions cannot use it "
                 "until a scan can be saved",)
    else:
        bible = lore_store.load(ctx.project)
        bible["baseline"] = baseline
        lore_store.save(ctx.project, bible)
        saved = True
    shown = {key: value for key, value in baseline.items() if key != "gaps"}
    reply: dict[str, Any] = {
        "saved": saved, "baseline": shown, "gaps": list(baseline["gaps"]),
        "next_step": "Run op=tiers: it suggests a tier from this scan and "
                     "shows all four, with comparison games."}
    reply.update(partial("baseline", notes=notes))
    return reply


def _op_tiers(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    from ..analysis import lore_baseline

    bible = lore_store.load(ctx.project)
    baseline = bible["baseline"]
    unsaved = False
    if not baseline and ctx.project.read_only:
        # A read-only server can never save a scan, so "run op=scan first"
        # would be advice nobody could follow: scan now, keep nothing.
        try:
            _text_engine(ctx, "tiers")
            baseline = lore_baseline.baseline_of(
                lore_baseline.lore_scan(ctx.project))
            unsaved = True
        except ToolError:
            baseline = {}
    suggestion = lore_baseline.suggest_tier(baseline)
    if unsaved and suggestion["tier"] is not None:
        suggestion = dict(suggestion, why=suggestion["why"].rstrip(".") + (
            " (from a scan that was not saved: this server is read-only)."))
    tiers = []
    for key in TIER_ORDER:
        tier = TIERS[key]
        asks = {name: list(value) if isinstance(value, tuple) else value
                for name, value in tier["asks"].items()}
        tiers.append({
            "key": key, "label": tier["label"], "summary": tier["summary"],
            "comparison_games": [dict(game) for game in
                                 tier["comparison_games"]],
            "asks": asks,
            "interview_questions": len(questions_for_tier(key)),
            "player_meets_lore": tier["player_meets_lore"],
            "biggest_risk": tier["biggest_risk"]})
    return {
        "tiers": tiers, "modes": dict(_MODE_HELP),
        "suggested_tier": suggestion["tier"], "suggestion": suggestion["why"],
        "chosen_tier": bible["tier"],
        "estimates_note": ESTIMATES_NOTE, "small_team_note": SMALL_TEAM_NOTE,
        "next_step": "Ask the author which tier and which mode they want, "
                     "then op=choose with tier and mode (and approach for "
                     "custom)."}


def _op_choose(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    tier = one_of(args, "tier", list(TIER_ORDER))
    mode = one_of(args, "mode", list(MODES))
    approach = opt_str(args, "approach")
    if mode == "custom":
        if approach is None or not approach.strip():
            raise ToolError("approach is required for mode custom: the "
                            "author's own approach, in their words")
        try:
            approach = check_text(approach, "approach", MAX_APPROACH_CHARS,
                                  multiline=True)
        except BibleError as exc:
            raise _refuse_bible(exc) from exc
    elif approach is not None:
        raise ToolError("approach is only for mode custom, and the mode is "
                        "%s; leave approach out, or choose mode custom" % mode)
    bible = lore_store.load(ctx.project)
    bible["tier"], bible["mode"] = tier, mode
    bible["approach"] = approach.strip() if mode == "custom" else None
    lore_store.save(ctx.project, bible)
    if mode == "qa":
        happens = ("Call op=next for the first questions, ask the author in "
                   "the conversation, and keep their answers with op=save.")
    elif mode == "artifact":
        happens = ("Call op=export with worksheet=true: it writes "
                   "bible.html into .rpgvxace-mcp/lore/. The author opens it "
                   "in a browser, fills in the fields and presses Copy my "
                   "answers, then pastes the JSON to you and you pass its "
                   "answers to op=save. op=next still serves the questions "
                   "if they would rather answer in conversation.")
    else:
        happens = ("Follow the author's approach: %s. Use op=next for the "
                   "question bank when it helps and op=save to keep answers "
                   "and entries; op=review checks the bible against the "
                   "game." % _one_line(bible["approach"]))
    return {"tier": tier, "mode": mode, "approach": bible["approach"],
            "questions_in_interview": len(questions_for_tier(tier)),
            "what_happens_next": happens}


def _one_line(text: "str | None") -> str:
    """An approach for a sentence: one line, bounded."""
    return " ".join((text or "").split())[:400]


def _op_next(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    from ..analysis import lore_baseline

    count = opt_int_default(args, "count", 3, 1, MAX_QUESTIONS_PER_CALL)
    group = None
    if args.get("group") is not None:
        group = one_of(args, "group", list(_GROUP_KEYS))
    bible = lore_store.load(ctx.project)
    if bible["tier"] is None:
        raise ToolError("no tier is chosen yet, so there is no interview to "
                        "serve: op=tiers shows the tiers and op=choose picks "
                        "one")
    hooks = lore_baseline.question_hooks(bible["baseline"])
    pending = [item for item in questions_for_tier(bible["tier"])
               if item["id"] not in bible["answers"]
               and (group is None or item["group"] == group)]
    rows = []
    for item in pending[:count]:
        row = {"id": item["id"], "group": item["group"],
               "tier": item["tier"], "text": item["text"],
               "why": item["why"], "good_answer": item["hint"]}
        if item["id"] in hooks:
            row["from_your_game"] = hooks[item["id"]]
        rows.append(row)
    progress = _progress(bible)
    reply: dict[str, Any] = {
        "questions": rows, "remaining": len(pending) - len(rows),
        "answered": progress["answered"], "total": progress["total"]}
    if rows:
        reply["next_step"] = ("Ask the author these, then op=save their "
                              "answers keyed by id.")
    else:
        reply["next_step"] = (
            "No unanswered questions are left%s. Run op=review to check the "
            "bible against the game, then op=export." % (
                " in that group" if group else ""))
    return reply


def _answers_to_save(args: dict[str, Any]) -> "dict[str, str]":
    raw = opt_dict(args, "answers")
    if len(raw) > len(QUESTIONS):
        raise ToolError(
            "answers lists %d questions; a call takes at most %d, one for "
            "each question this server asks" % (len(raw), len(QUESTIONS)))
    out: "dict[str, str]" = {}
    for key, value in raw.items():
        try:
            check_answer_id(key, "answers")
        except BibleError as exc:
            raise _refuse_bible(exc) from exc
        if question(key) is None:
            raise ToolError(
                "answers names %s, which is not a question this server "
                "asks; op=next lists the questions and their ids" % key)
        try:
            out[key] = check_text(value, "answers[%s]" % key,
                                  MAX_ANSWER_CHARS, multiline=True)
        except BibleError as exc:
            raise _refuse_bible(exc) from exc
    return out


def _entries_to_save(args: dict[str, Any]) -> "list[dict[str, str]]":
    out: "list[dict[str, str]]" = []
    seen: "set[tuple[str, str]]" = set()
    for index, raw in enumerate(opt_list(args, "entries")):
        try:
            entry = check_entry(raw, "entries[%d]" % index)
        except BibleError as exc:
            raise _refuse_bible(exc) from exc
        key = entry_key(entry)
        if key in seen:
            raise ToolError("entries[%d] lists the %s %s twice in one call; "
                            "each kind and name is listed once"
                            % (index, entry["kind"], entry["name"]))
        seen.add(key)
        out.append(entry)
    return out


def _names_to_delete(args: dict[str, Any]) -> "list[str]":
    names = str_list(args, "delete_entries", MAX_DELETE_NAMES)
    for index, name in enumerate(names):
        if len(name) > MAX_ENTRY_NAME_CHARS:
            raise ToolError("delete_entries[%d] is %d characters; an entry "
                            "name is at most %d"
                            % (index, len(name), MAX_ENTRY_NAME_CHARS))
    return names


def _op_save(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    answers = _answers_to_save(args)
    entries = _entries_to_save(args)
    deletes = _names_to_delete(args)
    if not answers and not entries and not deletes:
        raise ToolError("save needs answers, entries or delete_entries; "
                        "none was given")
    bible = lore_store.load(ctx.project)
    saved, cleared = [], []
    for key, text in answers.items():
        if text.strip():
            bible["answers"][key] = text
            saved.append(key)
        elif bible["answers"].pop(key, None) is not None:
            cleared.append(key)
    removed, not_found = [], []
    for name in deletes:
        folded = name.casefold()
        kept = [e for e in bible["entries"] if e["name"].casefold() != folded]
        if len(kept) < len(bible["entries"]):
            bible["entries"] = kept
            removed.append(name)
        else:
            not_found.append(name)
    added, updated = [], []
    for entry in entries:
        for held in bible["entries"]:
            if entry_key(held) == entry_key(entry):
                held["text"] = entry["text"]
                updated.append(entry["name"])
                break
        else:
            bible["entries"].append(entry)
            added.append(entry["name"])
    if len(bible["entries"]) > MAX_BIBLE_ENTRIES:
        raise ToolError(
            "the bible would hold %d entries; it holds at most %d. Delete "
            "some with delete_entries, or fold related ones together."
            % (len(bible["entries"]), MAX_BIBLE_ENTRIES))
    lore_store.save(ctx.project, bible)
    progress = _progress(bible)
    return {
        "answers_saved": saved, "answers_cleared": cleared,
        "entries_added": added, "entries_updated": updated,
        "entries_removed": removed, "entries_not_found": not_found,
        "answered": progress["answered"], "total": progress["total"],
        "next_step": _next_step(bible, progress)}


def _op_status(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    bible = lore_store.load(ctx.project)
    progress = _progress(bible)
    kinds = {kind: sum(1 for e in bible["entries"] if e["kind"] == kind)
             for kind in ENTRY_KINDS}
    baseline = bible["baseline"]
    scanned_at = baseline.get("scanned_at") if isinstance(baseline, dict) \
        else None
    return {
        "tier": bible["tier"], "mode": bible["mode"],
        "approach": bible["approach"],
        "progress": progress["groups"], "answered": progress["answered"],
        "total": progress["total"],
        "unknown_answers": sum(1 for key in bible["answers"]
                               if question(key) is None),
        "entries": {kind: count for kind, count in kinds.items() if count},
        "entries_total": len(bible["entries"]),
        "baseline": {"scanned": baseline is not None,
                     "scanned_at": scanned_at if isinstance(scanned_at, str)
                     else None},
        "pitfalls": _pitfalls(bible, progress),
        "next_step": _next_step(bible, progress)}


def _op_review(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    scopes = scopes_arg(args, "scopes", REVIEW_SCOPES)
    _text_engine(ctx, "review")
    from ..analysis import lore_baseline

    bible = lore_store.load(ctx.project)
    corpus = lore_baseline.lore_scan(ctx.project)
    reply = lore_baseline.lore_review(corpus, bible, scopes,
                                      MAX_REVIEW_FINDINGS)
    found = []
    for finding in reply["findings"]:
        wanted = _PITFALL_FOR_FINDING.get(finding["kind"])
        if wanted and wanted not in found:
            found.append(wanted)
    reply["relevant_pitfalls"] = [
        {"id": item["id"], "rule": item["rule"], "why": item["why"]}
        for item in (pitfall(name) for name in found[:3]) if item]
    reply["next_step"] = (
        "Go through the findings with the author: each is a question, not a "
        "verdict. Fix the bible with op=save, or the game with the other "
        "tools, then run op=export for the finished bible.")
    return reply


def _formats(args: dict[str, Any]) -> "list[str]":
    if args.get("formats") is not None and not opt_list(args, "formats"):
        raise ToolError("formats is empty; name html, markdown or both, or "
                        "leave it out for both")
    return scopes_arg(args, "formats", tuple(EXPORT_FORMATS))


def _op_export(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    formats = _formats(args)
    worksheet = opt_bool(args, "worksheet")
    bible = lore_store.load(ctx.project)
    asked = questions_for_tier(bible["tier"] or "light")
    texts: "dict[str, str]" = {}
    if "html" in formats:
        texts[EXPORT_FORMATS["html"]] = render_html(bible, asked, worksheet)
    if "markdown" in formats:
        texts[EXPORT_FORMATS["markdown"]] = render_markdown(bible, asked,
                                                            worksheet)
    written = lore_store.write_exports(ctx.project, texts)
    paths = {fmt: str(written[EXPORT_FORMATS[fmt]]) for fmt in formats}
    reply: dict[str, Any] = {
        "paths": paths,
        "bytes": {fmt: Path(path).stat().st_size
                  for fmt, path in paths.items()},
        "worksheet": worksheet,
        "worksheet_fields": (
            sum(1 for item in asked if item["id"] not in bible["answers"])
            if worksheet and "html" in formats else 0),
        "next_step": (
            "The author opens bible.html in a browser, fills in the fields "
            "and presses Copy my answers, then pastes the JSON to you; pass "
            "its answers to op=save." if worksheet and "html" in formats
            else "These are the bible as it stands; export with "
                 "worksheet=true for blank fields to fill in.")}
    reply.update(partial("export", notes=(
        "Wrote cache files under .rpgvxace-mcp/lore; this is not a project "
        "edit, so it is done under --read-only too and no project data "
        "changed.",)))
    return reply


_OPS = {"scan": _op_scan, "tiers": _op_tiers, "choose": _op_choose,
        "next": _op_next, "save": _op_save, "status": _op_status,
        "review": _op_review, "export": _op_export}


@tool(
    "lore_bible",
    "Guide an author through building their game's lore",
    _DESCRIPTION,
    schema({
        "op": prop(STR, "scan, tiers, choose, next, save, status, review or "
                        "export.", enum=list(LORE_OP_ARGS)),
        "refresh": REFRESH,
        "tier": prop(STR, "For choose: the scope tier the author wants: "
                          + ", ".join(TIER_ORDER) + ".",
                     enum=list(TIER_ORDER)),
        "mode": prop(STR, "For choose: qa (you ask in the conversation), "
                          "artifact (an HTML worksheet) or custom (the "
                          "author's own approach).", enum=list(MODES)),
        "approach": prop(STR, "For choose with mode custom (required there, "
                              "refused otherwise): the author's approach in "
                              "their own words. At most %s characters."
                         % _n(MAX_APPROACH_CHARS),
                         maxLength=MAX_APPROACH_CHARS),
        "count": prop(INT, "For next: how many questions (default 3, at most "
                           "%d)." % MAX_QUESTIONS_PER_CALL,
                      minimum=1, maximum=MAX_QUESTIONS_PER_CALL),
        "group": prop(STR, "For next: only this group of questions: "
                           + ", ".join(_GROUP_KEYS) + ".",
                      enum=list(_GROUP_KEYS)),
        "answers": prop(OBJ, "For save: question id to the author's answer "
                             "text (op=next gives the ids). An empty answer "
                             "clears that answer. At most %d questions a "
                             "call, each answer at most %s characters."
                        % (len(QUESTIONS), _n(MAX_ANSWER_CHARS))),
        "entries": prop(ARR, "For save: named things the lore has. An entry "
                             "with the same kind and name (any case) "
                             "replaces that entry's text. At most %d per "
                             "call, %s in the bible; a name at most %d "
                             "characters, a text at most %s."
                        % (MAX_ENTRIES_PER_SAVE, _n(MAX_BIBLE_ENTRIES),
                           MAX_ENTRY_NAME_CHARS, _n(MAX_ENTRY_TEXT_CHARS)),
                        maxItems=MAX_ENTRIES_PER_SAVE,
                        items=schema({
                            "kind": prop(STR, "One of "
                                              + ", ".join(ENTRY_KINDS) + ".",
                                         enum=list(ENTRY_KINDS)),
                            "name": prop(STR, "The name, one line."),
                            "text": prop(STR, "What the lore says about it."),
                        }, ["kind", "name", "text"])),
        "delete_entries": prop(ARR, "For save: names of entries to remove, "
                                    "of any kind, any case; applied before "
                                    "entries is, so a name in both is "
                                    "replaced. At most %d names, each at "
                                    "most %d characters."
                               % (MAX_DELETE_NAMES, MAX_ENTRY_NAME_CHARS),
                               items={"type": STR},
                               maxItems=MAX_DELETE_NAMES),
        "scopes": prop(ARR, "For review: which comparisons (default all): "
                            + ", ".join(REVIEW_SCOPES),
                       items={"type": STR, "enum": list(REVIEW_SCOPES)},
                       maxItems=len(REVIEW_SCOPES)),
        "formats": prop(ARR, "For export: html, markdown or both (default "
                             "both).",
                        items={"type": STR, "enum": list(EXPORT_FORMATS)},
                        maxItems=len(EXPORT_FORMATS)),
        "worksheet": prop(BOOL, "For export: add blank, labelled fields for "
                                "the chosen tier's unanswered questions "
                                "(default false: the bible as it stands)."),
    }, ["op"]),
)
def lore_bible(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    """Run one op. See the module docstring and the tool description."""
    op = one_of(args, "op", list(LORE_OP_ARGS))
    check_op_args(op, args, LORE_OP_ARGS, "lore_bible")
    return _OPS[op](ctx, args)
