"""The two exports of the lore bible: one self-contained HTML page, and markdown.

Pure text out of a bible and a list of questions; nothing here touches the
disk or the project. `lore_store.write_export` puts the result in the lore
folder.

The HTML page is meant to be opened from a folder, in a browser, by the
author, and passed back to the conversation as a pasted block of JSON. So it
is a single file with its CSS and its script inline, no external fonts,
scripts, styles or images, and nothing that reaches a network. The author's
text is in it, and that text came from a file a person or a model wrote, so it
is treated as hostile on the way in:

* every string is HTML-escaped (including quotes) before it is placed, and it
  is only ever placed as the text of an element, never in an attribute value
  or a script;
* the one block of data the script reads is JSON in a
  `<script type="application/json">` with `<`, `>` and `&` written as
  Unicode escapes, so no string can close the block or open a comment;
* the script builds the clipboard text from the text areas' values and
  `JSON.stringify`, and writes to the page only with `textContent`.

The markdown is the plain-text equivalent. It escapes HTML and the characters
that would turn the author's text into a heading, a link or emphasis, and
quotes every answer, so an answer cannot add structure to the document.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any

from .lore_guidance import GROUPS, TIERS
from .lore_store import ENTRY_KINDS, MAX_ANSWER_CHARS


_KIND_HEADINGS = {"place": "Places", "character": "Characters",
                  "faction": "Factions", "event": "Events", "term": "Terms",
                  "item": "Items", "other": "Other"}


_STYLE = """
:root { color-scheme: light dark; --bg: #fbfaf7; --fg: #1d1c1a; --muted: #5d5a54;
  --line: #d9d4c9; --card: #ffffff; --accent: #8a4b1f; --field: #ffffff; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #171614; --fg: #ece8df; --muted: #a9a398; --line: #3a3732;
    --card: #1f1d1a; --accent: #e0a36b; --field: #14130f; } }
body { background: var(--bg); color: var(--fg); margin: 0;
  font: 16px/1.55 Georgia, "Times New Roman", serif; }
header, main, footer { max-width: 46rem; margin: 0 auto; padding: 0 16px; }
header { padding-top: 2rem; }
h1 { font-size: 1.9rem; margin: 0 0 .25rem; }
h2 { border-bottom: 1px solid var(--line); padding-bottom: .25rem;
  margin-top: 2.25rem; }
h3 { font-size: 1.05rem; margin: 0 0 .25rem; }
nav ol { columns: 2; padding-left: 1.25rem; }
a { color: var(--accent); }
.meta, .why, .hint { color: var(--muted); font-size: .92rem; margin: .1rem 0; }
article { background: var(--card); border: 1px solid var(--line);
  border-radius: 8px; padding: .85rem 1rem; margin: .8rem 0; }
textarea { width: 100%; box-sizing: border-box; min-height: 6rem; margin-top: .4rem;
  padding: .5rem; border: 1px solid var(--line); border-radius: 6px;
  background: var(--field); color: var(--fg); font: inherit; }
label { font-size: .85rem; color: var(--muted); }
button { font: inherit; padding: .5rem 1rem; border-radius: 6px; cursor: pointer;
  border: 1px solid var(--accent); background: var(--accent); color: var(--bg); }
dt { font-weight: bold; margin-top: .6rem; }
dd { margin: .1rem 0 .4rem 1rem; }
footer { padding: 1.5rem 16px 3rem; }
@media (max-width: 40rem) { nav ol { columns: 1; } }
"""


_SCRIPT = """
(function () {
  var data = JSON.parse(document.getElementById('bible-data').textContent);
  var areas = document.querySelectorAll('textarea[data-qid]');
  var status = document.getElementById('status');
  var button = document.getElementById('copy');
  var fallback = document.getElementById('fallback');
  function collect() {
    var out = {};
    areas.forEach(function (area) {
      var value = area.value.trim();
      if (value) { out[area.getAttribute('data-qid')] = value; }
    });
    return { answers: out };
  }
  function update() {
    var fresh = Object.keys(collect().answers).length;
    status.textContent = fresh + ' new answer(s) to copy. '
      + Object.keys(data.answers).length + ' already saved.';
  }
  function offer(text) {
    fallback.value = text;
    fallback.hidden = false;
    fallback.select();
    status.textContent = 'Copy this box (Ctrl+C), then paste it into the conversation.';
  }
  areas.forEach(function (area) { area.addEventListener('input', update); });
  if (button) {
    button.addEventListener('click', function () {
      var text = JSON.stringify(collect(), null, 2);
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function () {
          status.textContent = 'Copied. Paste it into the conversation.';
        }, function () { offer(text); });
      } else { offer(text); }
    });
  }
  update();
})();
"""


def _e(text: str) -> str:
    return html.escape(text, quote=True)


def _paragraphs(text: str) -> str:
    """Prose as `<p>` per paragraph and `<br>` per line break, all escaped."""
    out = []
    for block in re.split(r"\n[ \t]*\n+", text.strip("\n")):
        if block.strip():
            out.append("<p>%s</p>" % "<br>".join(_e(line.strip())
                                                 for line in block.split("\n")))
    return "\n".join(out)


def _json_for_script(data: Any) -> str:
    """JSON that cannot end its own `<script>` element or open a comment."""
    text = json.dumps(data, ensure_ascii=True, indent=1, sort_keys=True)
    return (text.replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("&", "\\u0026"))


def _header(bible: dict[str, Any]) -> str:
    tier = bible.get("tier")
    label = TIERS[tier]["label"] if tier in TIERS else "not chosen yet"
    mode = bible.get("mode") or "not chosen yet"
    return "Tier: %s. Mode: %s." % (label, mode)


def _sections(bible: dict[str, Any], questions: "list[dict[str, str]]"
              ) -> "list[tuple[str, str, list[dict[str, str]]]]":
    """(group key, heading, questions) for each group that has any."""
    out = []
    for key, heading in GROUPS:
        here = [q for q in questions if q["group"] == key]
        if here:
            out.append((key, heading, here))
    return out


def _unlisted(bible: dict[str, Any], questions: "list[dict[str, str]]"
              ) -> "dict[str, str]":
    """Saved answers to questions this export is not asking."""
    asked = {q["id"] for q in questions}
    return {key: text for key, text in sorted(bible["answers"].items())
            if key not in asked}


def render_html(bible: dict[str, Any], questions: "list[dict[str, str]]",
                worksheet: bool) -> str:
    """The bible as one self-contained page. See the module docstring."""
    answers = bible["answers"]
    sections = _sections(bible, questions)
    unlisted = _unlisted(bible, questions)
    parts = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>Lore bible</title>",
        "<style>%s</style>" % _STYLE,
        "</head>",
        "<body>",
        "<header>",
        "<h1>Lore bible</h1>",
        '<p class="meta">%s</p>' % _e(_header(bible)),
        "</header>",
        "<main>",
        "<nav>",
        "<ol>",
    ]
    for key, heading, _ in sections:
        parts.append('<li><a href="#group-%s">%s</a></li>' % (key, _e(heading)))
    if unlisted:
        parts.append('<li><a href="#other-answers">Other answers</a></li>')
    parts += ['<li><a href="#entries">Entries</a></li>', "</ol>", "</nav>"]
    unanswered: "list[str]" = []
    for key, heading, here in sections:
        parts += ['<section id="group-%s">' % key, "<h2>%s</h2>" % _e(heading)]
        for question in here:
            identifier = question["id"].replace(".", "-")
            parts += ['<article id="q-%s">' % identifier,
                      "<h3>%s</h3>" % _e(question["text"])]
            if question["id"] in answers:
                parts.append('<div class="answer">%s</div>'
                             % _paragraphs(answers[question["id"]]))
            else:
                unanswered.append(question["id"])
                parts.append('<p class="why">%s</p>' % _e(question["why"]))
                if worksheet:
                    parts += [
                        '<label for="a-%s">Your answer</label>' % identifier,
                        '<textarea id="a-%s" data-qid="%s" rows="4" '
                        'maxlength="%d"></textarea>'
                        % (identifier, _e(question["id"]), MAX_ANSWER_CHARS),
                        '<p class="hint">A good answer: %s</p>'
                        % _e(question["hint"])]
                else:
                    parts.append('<p class="meta">Not answered yet.</p>')
            parts.append("</article>")
        parts.append("</section>")
    if unlisted:
        parts += ['<section id="other-answers">', "<h2>Other answers</h2>"]
        for key, text in unlisted.items():
            parts += ["<article>", "<h3>%s</h3>" % _e(key),
                      '<div class="answer">%s</div>' % _paragraphs(text),
                      "</article>"]
        parts.append("</section>")
    parts += ['<section id="entries">', "<h2>Entries</h2>"]
    if not bible["entries"]:
        parts.append('<p class="meta">No entries yet.</p>')
    for kind in ENTRY_KINDS:
        rows = [e for e in bible["entries"] if e["kind"] == kind]
        if rows:
            parts += ["<h3>%s</h3>" % _KIND_HEADINGS[kind], "<dl>"]
            for entry in rows:
                parts += ["<dt>%s</dt>" % _e(entry["name"]),
                          "<dd>%s</dd>" % (_paragraphs(entry["text"])
                                           or "<p></p>")]
            parts.append("</dl>")
    parts += ["</section>", "</main>", "<footer>"]
    if worksheet:
        parts += [
            '<button id="copy" type="button">Copy my answers</button>',
            '<p id="status" class="meta" role="status"></p>',
            '<textarea id="fallback" aria-label="Answers to copy" readonly '
            'hidden></textarea>',
            "<noscript><p class=\"meta\">The copy button needs scripts; "
            "otherwise type your answers into the conversation.</p></noscript>",
        ]
    parts.append("</footer>")
    parts.append('<script type="application/json" id="bible-data">%s</script>'
                 % _json_for_script({"answers": answers,
                                     "unanswered": unanswered}))
    parts.append("<script>%s</script>" % _SCRIPT)
    parts += ["</body>", "</html>", ""]
    return "\n".join(parts)


_MD_SPECIAL = re.compile(r"([\\`*_\[\]])")


def _md(text: str) -> str:
    """User text for a markdown document: no HTML, no emphasis, no links."""
    return _MD_SPECIAL.sub(r"\\\1", html.escape(text, quote=False))


def _quoted(text: str) -> str:
    lines = _md(text.strip("\n")).split("\n")
    return "\n".join("> " + line if line.strip() else ">" for line in lines)


def render_markdown(bible: dict[str, Any], questions: "list[dict[str, str]]",
                    worksheet: bool) -> str:
    """The same bible as plain markdown."""
    answers = bible["answers"]
    out = ["# Lore bible", "", _md(_header(bible)), ""]
    for key, heading, here in _sections(bible, questions):
        out += ["## " + heading, ""]
        for question in here:
            answer = answers.get(question["id"])
            if answer is None and not worksheet:
                continue
            out += ["### " + _md(question["text"]), ""]
            if answer is not None:
                out += [_quoted(answer), ""]
            else:
                out += ["_(not answered yet)_", ""]
    unlisted = _unlisted(bible, questions)
    if unlisted:
        out += ["## Other answers", ""]
        for key, text in unlisted.items():
            out += ["### " + _md(key), "", _quoted(text), ""]
    out += ["## Entries", ""]
    if not bible["entries"]:
        out += ["No entries yet.", ""]
    for kind in ENTRY_KINDS:
        rows = [e for e in bible["entries"] if e["kind"] == kind]
        if not rows:
            continue
        out += ["### " + _KIND_HEADINGS[kind], ""]
        for entry in rows:
            body = _md(entry["text"].strip()).replace("\n", "\n  ")
            out.append("- **%s**%s" % (_md(entry["name"]),
                                       (": " + body) if body else ""))
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"
