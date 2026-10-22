# `lore_bible`

A guide for building out a game's lore. The server cannot interview anyone, so
the tool does not interview: the model in the conversation asks the author, and
the tool supplies what it needs to ask well and keeps the answers.

It is **guide-only**. It never edits a data file of the game. The one thing it
writes is `.rpgvxace-mcp/lore/` in the project's work folder, a cache like the
renders and the backups. It is for anyone using the server; nothing in it is
about any particular game.

## The flow

1. **scan** the project for what it already says, and where that is thin.
2. **tiers**: four scope tiers (light, moderate, rich, deep) with comparison
   games, what each asks of the author, and a suggestion derived from the scan.
3. Ask the author which tier and which **mode** they want, and **choose**:
   * `qa`: you ask in the conversation, a few questions at a time;
   * `artifact`: an HTML worksheet in the project that the author fills in in a
     browser and pastes back;
   * `custom`: the author describes their own approach in words; you follow it
     with the same question bank and the same storage.
4. Run the mode: **next** serves the unanswered questions, **save** keeps
   answers and named entries, **export** writes the bible.
5. **review** the bible against the game's real text, and **export** the
   finished bible.

`status` shows where things stand and what to do next.

| op | writes | arguments | returns |
| --- | --- | --- | --- |
| `scan` | the baseline, into the bible file | `refresh` | the baseline and a ranked `gaps` list |
| `tiers` | nothing | none | the four tiers, `suggested_tier` and why |
| `choose` | the bible file | `tier`, `mode`, `approach` (custom only) | confirmation and what happens next |
| `next` | nothing | `count` (1 to 10, default 3), `group` | the next unanswered questions |
| `save` | the bible file | `answers`, `entries`, `delete_entries` | what changed, and progress |
| `status` | nothing | none | tier, mode, progress per group, counts, next step |
| `review` | nothing | `scopes` | findings, each with a `where` and a question to ask |
| `export` | `bible.html`, `bible.md` | `formats`, `worksheet` | the paths written |

## The scan

The baseline is a bounded, deterministic summary, not a dump:

* **Volume of text by kind**: dialogue (Show Text), choices, scrolling text,
  comments (counted apart, as developer notes and not prose), map display
  names, database descriptions, database notes, and the System table's title
  and terms; each as a count and a number of words.
* **The database**: how many entries can carry a description and how many have
  none, per table. An entry with no name is an editor placeholder and is not
  counted.
* **Names**: capitalised words that occur in the middle of a sentence more
  often than they occur in lower case, with how often they occur, the first
  few places, and whether any database description or note mentions them
  (`explained`). A name that recurs and is never explained is the most useful
  thing the scan finds.
* **Spelling variants**: names one letter apart (both at least five letters
  long), names that differ only in case or diacritics, and names that differ
  only in spacing ("Silver Gate" and "Silvergate").
* **Speakers**: a Show Text box carries a face graphic name and index, used as
  the speaker; with no face, the event's name if it is not the default `EVnnn`.
  Per speaker, the number of boxes, the average words a box, and a few sample
  lines.
* **Where the text sits**: boxes in map events on the action button, on touch,
  and on autorun or parallel pages. Talking to someone is optional by nature;
  this is an approximation, and no main-path analysis is attempted.
* **The maps**: how many, how many still have the default name, how many have
  no display name (VX Ace), how many are blank (reusing `map_critique`'s
  shape analysis rather than measuring again), how deep the tree is, how many
  events there are and how many maps have none.
* **Gaps**: at most ten observations in plain language, ranked, such as "the
  name X appears 14 times in the game text and no database description or note
  mentions it". A project with no player-visible text at all gets a clean
  baseline of zeros and the one gap that says so, not an error.

Bounded the way the other walks are: one `WalkBudget` is shared by every event,
page and command; stored bytes are charged before they are decoded; each string
is analysed only up to 4,096 characters; the tables that grow with a project
have ceilings. When anything is cut the answer says so, in the usual vocabulary:
`walk_stopped`, `proper_nouns_truncated`, `speakers_truncated`,
`vocabulary_truncated`, `files_not_read`.

`scan` and `review` read dialogue and so work on VX Ace and VX, as `text` does.
An XP project is refused with the same reason `text` gives (XP's Show Text keeps
its first line in the 101 itself). The other ops work on any project.

## The tiers

| tier | comparison games | what it asks (estimates) |
| --- | --- | --- |
| light | Dragon Quest (1986), Ib, Yume Nikki | 5 to 15 named characters, 5 to 10 places, a bible of 500 to 1,500 words |
| moderate | Chrono Trigger, EarthBound, Undertale | 20 to 40 characters, 10 to 20 places, 3,000 to 8,000 words |
| rich | Final Fantasy VII, Suikoden II, Xenoblade Chronicles | 50 to 120 characters, 20 to 40 places, 10,000 to 30,000 words |
| deep | Dark Souls, Disco Elysium, Dragon Age: Origins | 100 to 300 characters, 30 to 80 places, 40,000 to 150,000 words |

Every count in that table is a planning estimate derived from the comparison
games, not a sourced figure; the tool labels them so. Each comparison carries a
one-line reason and a `basis`: `sourced` (a fact read in an encyclopaedia
article), `single_source` (one weak source, and worded as "reported" or "one
source"), or `synthesis` (craft advice, worded as advice). The suggested tier
comes from the words of dialogue and scrolling text the game has (under 2,000
words suggests light, under 15,000 moderate, under 60,000 rich), which is a
rule of thumb and says so.

## The questions

Sixty questions in eight groups (premise, world, factions, cast, conflict, tone,
delivery, scope), each with a stable id such as `premise.seed`, the tier it
belongs to, why it matters and what a good answer looks like. A tier asks its
own questions and every lower tier's, so the light tier on its own is a short,
coherent interview (17 questions) that covers premise, world, cast, conflict,
tone and delivery. After a scan, some questions carry a `from_your_game` line
that quotes the project: its actors, its map names, a recurring name nothing
explains, the share of items with no description.

## Storage, and why the file is not trusted

The bible is one JSON file, `.rpgvxace-mcp/lore/bible.json`. `.rpgvxace-mcp/`
sits inside the project, and a person or a model may have edited it, so every
read goes through a validator that rebuilds the document field by field:
wrong types, over-long strings, control characters, unknown keys, numbers that
are not finite and a baseline nested too deeply are refused with a message that
names the field. The file's size is checked before it is opened. A link or
junction as the work folder, the lore folder or the file is refused, and writes
are atomic, so a failed write leaves the old bible.

Answers to question ids this server does not have (an older or newer bible) are
kept and counted in `status` as `unknown_answers`, not refused.

## Review

The review compares the bible's names with the game's own words (dialogue,
descriptions, notes, map names, event names). A name counts as found when every
word of it, and every adjacent pair, is in the game's text; case, accents and
spacing are ignored. Findings:

* `not_in_game`: the bible has it and the game never says it;
* `character_not_an_event`, `place_not_a_map`: the game says the name but no
  event, actor or enemy (or map) is called that;
* `spelling_variant`: the bible and the game spell a name differently;
* `game_name_not_in_bible`: a name the game says three or more times that the
  bible never mentions;
* `map_not_in_bible`: a named map the bible never mentions.

Each finding has a `where` and an `ask`, the sentence to put to the author. A
finding is a prompt, not a verdict: lore the game never says may be good
background, and the review says when the vocabulary it compared against was cut.

## Export

`export` writes `bible.html` and `bible.md` into the lore folder. The HTML is a
single self-contained file with inline CSS and script, no external fonts,
scripts or images, and a light and a dark scheme. It has a table of contents,
the answered questions as prose, and, with `worksheet: true`, a labelled text
area for every unanswered question of the chosen tier. Its **Copy my answers**
button puts `{"answers": {...}}` on the clipboard (or in a box to copy by hand);
the author pastes it into the conversation and the model passes it to `save`.

The bible's text came from a file that a person or model wrote, so the page
treats it as hostile: every string is HTML-escaped and only ever placed as the
text of an element; the data block the script reads is JSON with `<`, `>` and
`&` written as Unicode escapes; the script writes to the page with `textContent`
only. The markdown escapes HTML and the characters that would turn text into a
heading, link or emphasis, and quotes every answer.

`export` writes cache files, so it works under `--read-only`, as `map_render`
does; `choose` and `save` are refused there, and `scan` answers without saving
(and says so).

## Limits

| limit | value |
| --- | --- |
| bible file | 1,000,000 bytes |
| one answer | 4,000 characters |
| entries in the bible / in one `save` | 500 / 100 |
| entry name / text | 80 / 2,000 characters |
| `delete_entries` | 100 names |
| `approach` | 2,000 characters |
| questions per `next` | 10 |
| findings per `review` | 60 (`findings_total` says how many there were) |
| saved baseline | 65,536 bytes: 40 names, 20 spelling pairs, 15 speakers with 3 sample lines of 120 characters, 10 gaps |

## Where the guidance comes from

The questions, tiers and pitfalls are curated offline from a craft survey and a
scope comparison, so the server needs no network. Where the survey could not
corroborate a claim, the tool does not state it: it is either worded as craft
advice or left out. Claims that rest on a single source or on synthesis are
marked in the data (`basis`) and worded to match.
