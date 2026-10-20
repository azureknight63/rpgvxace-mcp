"""What `lore_bible` knows about building a game's lore: data, and lookups.

Curated offline from two pieces of desk research (a craft survey and a scope
comparison of well-known games), so the server needs no network at run time and
this file is the only place the advice lives. It holds no logic beyond lookups.

How sure each statement is, because a shipped claim should say:

* `basis: "sourced"` -- a fact the research read in a page it could open
  (almost all of it an encyclopaedia article about the game).
* `basis: "single_source"` -- one source, and a weak or second-hand one. These
  are worded with "reported" or "per one source" and are never the only reason
  to do something.
* `basis: "synthesis"` -- craft advice that is common practice rather than a
  citation: it is the judgement of the survey, worded as advice, not as fact.
* Every count a tier asks of the author is a labelled estimate, derived from
  the comparison games and not a sourced figure.

Left out on purpose: claims the research could not corroborate (a late
rewrite of one famous game's structure, a development anecdote about another's
villain, a handful of play times, an author's relayed interview). They are not
rephrased here either; `tests/test_lore_bible.py` scans every string below for
the phrases that would reintroduce them.

Summarised in this project's own words; the comparisons are one line each.
"""

from __future__ import annotations

from .commands import MESSAGE_LINES_PER_BOX


# Where each statement's confidence comes from. See the module docstring.
BASES = ("sourced", "single_source", "synthesis", "estimate")


TIER_ORDER = ("light", "moderate", "rich", "deep")


GROUPS = (
    ("premise", "Premise and theme"),
    ("world", "The world"),
    ("factions", "Factions"),
    ("cast", "The cast"),
    ("conflict", "The central conflict or secret"),
    ("tone", "Tone and voice"),
    ("delivery", "How the player meets the lore"),
    ("scope", "Scope and finishing"),
)


ESTIMATES_NOTE = (
    "Every count and length in the tiers is a planning estimate derived from "
    "the comparison games, not a sourced figure and not a rule. Team sizes and "
    "lengths vary by source, so none is quoted as a target."
)


SMALL_TEAM_NOTE = (
    "A note on small teams, which is an observation and not a rule: several "
    "small RPG Maker games feel richer than their size (To the Moon on RPG "
    "Maker XP, Lisa: The Painful on VX Ace, Yume Nikki, Ib). What they seem to "
    "share is a few strong ideas, one voice, and lore carried by atmosphere or "
    "a handful of characters rather than by an encyclopaedic bible."
)


# (id, tier, text, why it matters, what a good answer looks like). The group is
# the id's first part, so an id cannot name one group and sit in another.
_QUESTION_ROWS = (
    ("premise.seed", "light",
     "In one or two sentences: who is the protagonist, what do they want, "
     "and what stands in their way?",
     "Everything else grows from this seed, so it is worth getting down first.",
     "Names a person, a concrete goal and an obstacle, not just a genre."),
    ("premise.theme", "light",
     "Underneath the plot, what is the game asking? Put it as a question, for "
     "example whether you can forgive someone who saved you by doing harm.",
     "A theme stated as a question gives every scene something to test itself "
     "against.",
     "A real two-sided question, not a moral slogan."),
    ("premise.last_feeling", "light",
     "How should the player feel at the last scene, and why?",
     "The feeling you want at the end decides how the rest is structured.",
     "A specific emotion with a reason, such as relief that comes at a cost."),
    ("premise.belief", "moderate",
     "What does the protagonist believe at the start that the story will "
     "challenge?",
     "A belief that gets tested is the simplest engine for a character arc.",
     "A belief that feels reasonable to hold, not a flaw label."),
    ("premise.personal_cost", "moderate",
     "Besides the world ending, what does the protagonist personally lose if "
     "they fail?",
     "Personal stakes carry the emotion; a big stake with no face on it moves "
     "few players.",
     "Names a person, a place or a relationship."),
    ("premise.protagonist_voice", "moderate",
     "Does the protagonist speak, or is the protagonist a silent stand-in for "
     "the player?",
     "It shapes how dialogue is written and how closely the player identifies "
     "with the hero.",
     "A choice, with the reason for it."),
    ("premise.midpoint_failure", "rich",
     "Is there a moment where the plot defeats the heroes, such as a lost "
     "battle or a ruined place?",
     "A midpoint failure can reshape the whole second half; Final Fantasy VI "
     "turns its heroes' failure into an open, character-driven recovery.",
     "A concrete setback with lasting consequences."),
    ("premise.structure", "rich",
     "Which overall shape fits: three acts, a repeating circle of want and "
     "change, or a four-part build with a twist? Where would each dungeon or "
     "chapter be its own small arc?",
     "A long game needs an overall shape and a repeatable per-chapter shape; "
     "forcing one template onto all of it tends to mislead.",
     "A main frame plus a small shape you can repeat per chapter."),
    ("premise.halfway_ending", "deep",
     "If the story stopped at the halfway point, would that be an acceptable "
     "ending? What would be lost?",
     "If the scope outgrows your time, a stopping point that still works is "
     "your insurance.",
     "A clear answer that names what the story loses."),

    ("world.traveller", "light",
     "Describe the world in three sentences, as a traveller seeing it for the "
     "first time would.",
     "This is the player's first impression and the layer everything else "
     "sits on.",
     "Sensory and specific, with one odd detail."),
    ("world.difference", "light",
     "What is the one thing that makes this world different from the genre "
     "default: one rule, one relic or one crisis?",
     "One idea explored fully is usually worth more than many left shallow.",
     "A single hook with consequences."),
    ("world.places", "light",
     "List your main places (three to eight) with a line each, and what the "
     "player does in each.",
     "It ties the lore to places that actually exist in the game.",
     "Each place has a function and a mood."),
    ("world.recent_past", "moderate",
     "What happened around a century ago that still shapes today?",
     "A recent past gives the world layers without needing an enormous "
     "history.",
     "One event with winners, losers and a lingering grievance."),
    ("world.power_limits", "moderate",
     "How do magic or technology work in practice, and what do they cost or "
     "limit?",
     "Limits are where the stories are; a power with no cost creates no "
     "choices.",
     "A cost that forces a decision."),
    ("world.daily_life", "moderate",
     "What do ordinary people eat, fear, worship and trade?",
     "Small concrete details are what make a town feel lived in.",
     "Three specifics, local to this world."),
    ("world.rulers", "rich",
     "Who rules, how do they keep power, and what is the economy's main "
     "resource?",
     "It gives villains and factions believable motives.",
     "A concrete mechanism such as a tax, a monopoly or fear."),
    ("world.origin_belief", "rich",
     "What do people believe about how the world began, and how much of it is "
     "true?",
     "A gap between myth and fact is a ready-made source of mystery.",
     "A belief that differs from the truth in a specific way."),
    ("world.geography", "rich",
     "How does the land shape movement, trade and conflict?",
     "It explains the shape of the overworld and why places matter.",
     "Choke points and borders tied to places in your game."),
    ("world.timeline", "deep",
     "Give a timeline of the key events using relative dates, marking each as "
     "known to the player, hidden or disputed.",
     "A timeline is the backbone of the canon, and the visibility flag keeps "
     "secrets straight.",
     "A dated list with a known, hidden or disputed flag."),
    ("world.naming", "deep",
     "What naming rules exist? How can you tell where a person comes from by "
     "their name?",
     "A few sound rules group names by culture and reduce how much a glossary "
     "has to explain.",
     "A few sound or ending rules with example names."),
    ("world.big_question", "deep",
     "What is the world's biggest unanswered question, and who inside the "
     "world is also trying to answer it?",
     "A mystery works better when someone in the story is chasing it.",
     "A question with at least two rival theories."),

    ("factions.sides", "light",
     "Who is for and who is against the hero? Name the main groups.",
     "Factions give the conflict a shape the player can see.",
     "Two or three groups, each with something it wants."),
    ("factions.wants", "moderate",
     "For each faction: what does it want, what will it refuse to do, and "
     "what does it hide?",
     "Believable groups have limits and secrets.",
     "A want, a line it will not cross, and a secret."),
    ("factions.reasonable_dissent", "moderate",
     "Where could a reasonable person disagree with the hero's side?",
     "It avoids cardboard enemies.",
     "A grievance a player could sympathise with."),
    ("factions.relations", "rich",
     "How do the factions treat each other: alliances, trade, grudges?",
     "Relationships between groups produce shifting alliances over a long "
     "game.",
     "A short table of who stands where."),
    ("factions.temptation", "rich",
     "Which faction would a player be tempted to join, and why?",
     "A choice only matters if the other option is attractive.",
     "A clear appeal that comes with a cost."),
    ("factions.false_beliefs", "deep",
     "What does each faction wrongly believe about another?",
     "Misunderstanding drives plot, and it needs tracking.",
     "Specific false beliefs, flagged so you remember who knows what."),

    ("cast.party", "light",
     "Describe each party member in one sentence: who they are, what they "
     "want and how they sound.",
     "Voice comes first; scenes get easier once you know how each person "
     "talks.",
     "A want plus one distinct speech habit."),
    ("cast.reasons_to_stay", "light",
     "Why does each party member stay with the group?",
     "A reason that is not simply that the plot needs them keeps a party "
     "believable.",
     "A personal reason."),
    ("cast.antagonist", "light",
     "Who is the main antagonist, and what is their relationship to the hero?",
     "Villains tend to land harder when they are personally tied to the "
     "cast.",
     "A link beyond opposing the hero."),
    ("cast.theme_echo", "moderate",
     "How does each party member embody a different answer to the theme "
     "question?",
     "Ensemble arcs work best when each one echoes the theme from a different "
     "angle.",
     "A short matrix of character against stance."),
    ("cast.personal_quests", "moderate",
     "What is each member's personal quest, and where in the game does it "
     "start, turn and resolve?",
     "Personal quests spread the emotional payoffs through the game.",
     "A start, a midpoint and a resolution for each."),
    ("cast.heavy_beats", "moderate",
     "Who dies, leaves or betrays the party, and has it been earned?",
     "A heavy beat lands only as hard as the investment placed before it.",
     "Planted at least once earlier."),
    ("cast.npcs", "rich",
     "Name five to ten memorable NPCs and the one thing each knows.",
     "NPCs are where much of an RPG's lore is delivered.",
     "Each has a fact, a voice and a place."),
    ("cast.villain_wins", "rich",
     "Does the villain ever win on screen? How?",
     "Stakes need evidence; a villain who only threatens never feels "
     "dangerous. In Final Fantasy VI the villain's plan succeeds and breaks "
     "the world.",
     "A specific defeat with consequences."),
    ("cast.pairs", "deep",
     "What is the relationship between each pair in the party: friends, "
     "rivals, a shared secret?",
     "Banter and drama come from friction between specific pairs.",
     "A pair list with at least some tension."),

    ("conflict.secret", "light",
     "What is the central secret or conflict, and when does the player learn "
     "it?",
     "The timing of the reveal sets the pacing of the whole game.",
     "A named secret and a point in the game."),
    ("conflict.plants", "moderate",
     "What clues does the player see before the reveal, and where?",
     "A twist should reframe things the player has already seen; with no "
     "plants it is just a surprise.",
     "At least three clues, in different channels."),
    ("conflict.who_knows", "moderate",
     "Who knows the secret, and who is lying or mistaken?",
     "A who-knows-what list prevents contradictions when many characters "
     "touch the same secret.",
     "A list by character of what each believes."),
    ("conflict.reframe", "rich",
     "What does the reveal change about earlier scenes?",
     "Reframing is the strongest kind of twist.",
     "An earlier scene that now reads differently."),
    ("conflict.open_questions", "rich",
     "Which questions will stay unanswered on purpose, and why?",
     "Deliberate ambiguity is different from a loose end, and players can "
     "tell when the author is in control.",
     "A short list with a reason for each."),
    ("conflict.early_or_never", "deep",
     "What happens if a player finds the secret early, or never finds an "
     "optional piece of it?",
     "It tests whether your layers of delivery hold up.",
     "A fallback for each case."),

    ("tone.words", "light",
     "Pick three tone words and one game or film that feels similar.",
     "A quick anchor for every later writing decision.",
     "Words that include a contrast, such as funny and sad."),
    ("tone.earnest_and_jokes", "light",
     "Where is the game allowed to be earnest, and where may it joke?",
     "Humour can lower a player's guard so a sincere moment lands; knowing "
     "where each belongs avoids tonal whiplash.",
     "Named moments for each."),
    ("tone.voices", "moderate",
     "How does each major character speak: sentence length, slang, "
     "formality?",
     "Text boxes are small, so voice has to show in few words.",
     "Three speech markers per character."),
    ("tone.never", "moderate",
     "What will the game never do, in content or in tone?",
     "A short list of lines protects consistency.",
     "A short, explicit list."),
    ("tone.translation", "rich",
     "If the text may ever be translated, should it avoid puns and idioms?",
     "Wordplay and idioms often do not survive translation, and a translated "
     "line may not fit the text box.",
     "A decision on whether wordplay is allowed."),
    ("tone.motifs", "deep",
     "Which three to five images, phrases or songs recur, and what do they "
     "mean?",
     "Motifs unify a long game without exposition.",
     "Motifs mapped to the theme."),

    ("delivery.channels", "light",
     "How will the player learn about the world: NPC chat, scenes, item text, "
     "books or signs? Roughly in what mix?",
     "This choice sets the writing approach for the entire game.",
     "A mix with one main channel."),
    ("delivery.reading_budget", "light",
     "About how much reading do you expect in one area?",
     "A reading budget protects pacing.",
     "A number of text boxes, or a time estimate."),
    ("delivery.item_text", "moderate",
     "Which three lore facts will live in item or equipment descriptions?",
     "Item text is a cheap, optional layer of lore.",
     "Items tied to places or events."),
    ("delivery.environment", "moderate",
     "Which places carry clues in what the player sees: ruins, graves, "
     "leftover objects?",
     "Showing a cause beats summarising it.",
     "Map names, with what the player sees there."),
    ("delivery.optional_npcs", "rich",
     "What will optional NPCs reveal that the main path does not?",
     "It rewards curiosity and lets different people hold partial or biased "
     "views.",
     "Several NPCs with partial views."),
    ("delivery.books", "rich",
     "Will there be books or notes? Who wrote them?",
     "An in-world author gives a note a voice and a bias.",
     "An author, a date and an agenda."),
    ("delivery.codex", "deep",
     "Is there a glossary or codex in the game, and is it opt-in?",
     "A codex helps the players who want it and costs the others nothing, "
     "as long as it stays optional.",
     "Opt-in, with short entries unlocked by play."),

    ("scope.length", "light",
     "How long should a first playthrough be, and how many main characters, "
     "towns and dungeons?",
     "Scope decides what lore you can afford to deliver.",
     "Concrete numbers."),
    ("scope.keep_one_thread", "light",
     "If you could keep only one thread of the story, which would it be?",
     "It tells you what to protect when you have to cut.",
     "A clear answer."),
    ("scope.no_delivery", "moderate",
     "Which lore facts have no place in the game to be delivered yet?",
     "Lore the player can never meet might as well not exist.",
     "A list, each marked cut, move or create a spot."),
    ("scope.minimum_ending", "rich",
     "What is the smallest ending that still pays off if you run out of "
     "time?",
     "Compressing the second half is a known cost: Xenogears' back half was "
     "told mostly in narration when its team could not finish the planned "
     "game.",
     "A smaller finale that still pays off."),
    ("scope.canon_process", "deep",
     "How will you update the canon when the plan changes?",
     "Plans change during production, so keep one source of truth with a "
     "change log.",
     "One place that is the truth."),
)


QUESTIONS = tuple(
    {"id": row[0], "group": row[0].split(".")[0], "tier": row[1],
     "text": row[2], "why": row[3], "hint": row[4]}
    for row in _QUESTION_ROWS
)


_BY_ID = {question["id"]: question for question in QUESTIONS}


def question(question_id):
    """One question by id, or None. Not an error: a hand-edited file may name any."""
    if not isinstance(question_id, str):
        return None
    return _BY_ID.get(question_id)


def questions_for_tier(tier):
    """The questions a tier asks: its own and every lower tier's, in order."""
    reach = TIER_ORDER.index(tier)
    return [q for q in QUESTIONS if TIER_ORDER.index(q["tier"]) <= reach]


def _game(name, reason, basis="sourced"):
    return {"game": name, "reason": reason, "basis": basis}


def _asks(characters, places, factions, eras, words):
    """What a tier asks of the author. Always a labelled estimate."""
    return {"estimate": True, "named_characters": characters,
            "places": places, "factions": factions, "eras": eras,
            "bible_words": words}


TIERS = {
    "light": {
        "label": "Light",
        "summary": "A clear hook, a handful of places and people, and the "
                   "story told in plain NPC lines. Finishable by one person.",
        "comparison_games": [
            _game("Dragon Quest (1986)",
                  "One hero, five towns and five dungeons, with the story "
                  "carried by plain town dialogue."),
            _game("Ib",
                  "A short game with a silent hero and sparse dialogue that "
                  "explains no lore, yet reads as atmospheric."),
            _game("Yume Nikki",
                  "No dialogue and no plot; atmosphere and environment alone, "
                  "made in RPG Maker 2003, and it gained a cult following."),
        ],
        "asks": _asks((5, 15), (5, 10), (0, 2), (1, 1), (500, 1500)),
        "player_meets_lore": "Town NPC lines, plus one or two signs or "
                             "books.",
        "biggest_risk": "Feeling empty when there is no hook at all; one "
                        "strong premise fixes most of that.",
    },
    "moderate": {
        "label": "Moderate",
        "summary": "A small cast with distinct voices, a few eras or zones, "
                   "and one consistent tone holding it together.",
        "comparison_games": [
            _game("Chrono Trigger",
                  "Six playable characters plus one optional, seven eras you "
                  "can reach, told through NPC conversation and scripted "
                  "scenes."),
            _game("EarthBound",
                  "Four playable characters; one writer-designer's voice "
                  "carries a script that NPCs deliver in conversational "
                  "dialogue."),
            _game("Undertale",
                  "Made almost entirely by one person over about 32 months; "
                  "the monsters you meet are memorable through how they talk "
                  "and how your choices change what they say."),
        ],
        "asks": _asks((20, 40), (10, 20), (2, 4), (2, 4), (3000, 8000)),
        "player_meets_lore": "NPC dialogue, set-piece scenes, a few books or "
                             "notes, and optional side characters.",
        "biggest_risk": "Inconsistent tone between zones, and lore that "
                        "exists in the bible but never reaches the player.",
    },
    "rich": {
        "label": "Rich",
        "summary": "A large cast or a long plot with reveals across many "
                   "locations. Needs hard limits to stay finishable.",
        "comparison_games": [
            _game("Final Fantasy VII",
                  "Around nine playable characters counting optional ones, "
                  "told through cutscenes and dialogue by a team reported at "
                  "100 to 150 people."),
            _game("Suikoden II",
                  "Over 100 recruitable characters and over 40 usable in "
                  "combat, with a silent protagonist and a story mostly "
                  "written by one writer."),
            _game("Xenoblade Chronicles",
                  "Voiced cutscenes plus optional Heart-to-Heart "
                  "conversations, set on the bodies of two giant beings."),
        ],
        "asks": _asks((50, 120), (20, 40), (4, 8), (3, 5), (10000, 30000)),
        "player_meets_lore": "Cutscenes, dialogue, optional side scenes, and "
                             "a bestiary or item text.",
        "biggest_risk": "Unfinishable scope. The cautionary example is "
                        "Xenogears, whose second half became mostly "
                        "narration and cutscenes because the team could not "
                        "finish the planned game; games at this tier were "
                        "made by teams of 30 to 150 people, far more than a "
                        "solo author.",
    },
    "deep": {
        "label": "Deep",
        "summary": "Lore layered through item text, a codex or inner-voice "
                   "dialogue, where history can outweigh the main plot. For "
                   "a solo author, treat this as one slice taken deep.",
        "comparison_games": [
            _game("Dark Souls",
                  "Story told mostly through environment, item text and "
                  "sparse NPC dialogue; the lore is optional to find."),
            _game("Disco Elysium",
                  "One district of one city, with the lore reached by "
                  "talking (24 skills speak as inner voices), made by about "
                  "35 in-house and 20 outside people over roughly three "
                  "years. Its Final Cut edition is reported to exceed a "
                  "million words (one source).", "single_source"),
            _game("Dragon Age: Origins",
                  "Codex entries, dialogue trees and companion banter, by a "
                  "team of 180 or more over a roughly six-year timeline."),
        ],
        "asks": _asks((100, 300), (30, 80), (8, 15), (5, 8),
                      (40000, 150000)),
        "player_meets_lore": "Item descriptions, books, a codex, optional "
                             "dialogue and the environment.",
        "biggest_risk": "Never finishing. The deep-lore examples were made "
                        "by 55 to 180 or more people; a solo author should "
                        "pick one slice and keep the rest implied.",
    },
}


PITFALLS = (
    {"id": "front_loaded_exposition",
     "rule": "Do not front-load exposition.",
     "why": "Forced lore overwhelms players; optional lore does not, because "
            "those who want it dig and the rest lose nothing.",
     "basis": "synthesis"},
    {"id": "no_delivery_surface",
     "rule": "Do not write lore that no part of the game carries.",
     "why": "If no scene, item, NPC or place delivers a fact, the player "
            "never sees it. Either create a place for it or treat it as "
            "background.",
     "basis": "synthesis"},
    {"id": "deepen_before_adding",
     "rule": "Deepen an existing element before inventing a new one.",
     "why": "A few elements explored fully read as richer than many left "
            "shallow.",
     "basis": "synthesis"},
    {"id": "lore_needs_a_character",
     "rule": "Attach every fact to a character who wants, fears or is "
             "changed by it.",
     "why": "Lore that serves no character is background noise; an "
            "unexplained name that keeps recurring is the usual symptom.",
     "basis": "synthesis"},
    {"id": "plant_before_twist",
     "rule": "Plant clues before a twist.",
     "why": "A twist with no plants is only a surprise; a planted one "
            "reframes what the player has already seen.",
     "basis": "synthesis"},
    {"id": "who_knows_what",
     "rule": "Keep a who-knows-what list for every secret.",
     "why": "Secrets drive reveals and cause contradictions when untracked, "
            "especially as the plan changes during production.",
     "basis": "synthesis"},
    {"id": "mystery_vs_mistake",
     "rule": "Separate deliberate mystery from accidental contradiction.",
     "why": "Players only debate the gaps when they trust the author is in "
            "control. Mark each intended contradiction and its true "
            "answer.",
     "basis": "synthesis"},
    {"id": "lock_the_secret",
     "rule": "Decide the big secret before writing the early scenes.",
     "why": "A late change to a central fact forces rewrites of scenes "
            "already written.",
     "basis": "synthesis"},
    {"id": "text_box_budget",
     "rule": "Budget text to the message window.",
     "why": "A text box shows %d lines at once in VX Ace; how many "
            "characters fit on a line depends on the font and the face "
            "graphic, and control codes use some of that, so check in your "
            "own game." % MESSAGE_LINES_PER_BOX,
     "basis": "sourced"},
    {"id": "party_voices",
     "rule": "Give each party member a personal quest and a distinct voice.",
     "why": "A very large roster is a collecting structure in which most "
            "members get a line or two rather than an arc; give each party "
            "member an arc, or accept the collecting structure knowingly.",
     "basis": "synthesis"},
    {"id": "villain_acts",
     "rule": "Let the villain act and succeed at least once.",
     "why": "Stakes need evidence on screen; a villain who only threatens "
            "never feels dangerous.",
     "basis": "synthesis"},
    {"id": "scope_to_capacity",
     "rule": "Match scope to capacity, and define a smaller ending.",
     "why": "Compressing the back half of an over-ambitious game is a known "
            "failure; a smaller finale you can actually build is better.",
     "basis": "sourced"},
    {"id": "one_template",
     "rule": "Do not force a single story template onto the whole game.",
     "why": "Screenplay and monomyth templates assume one protagonist and a "
            "fixed length. A long game with a party and optional content "
            "does better with a frame for the whole plus a small repeatable "
            "shape per chapter.",
     "basis": "synthesis"},
    {"id": "translation",
     "rule": "If translation is possible, write for it.",
     "why": "Short sentences and few puns survive translation; translated "
            "text can also run longer than the original and overflow a "
            "box.",
     "basis": "synthesis"},
    {"id": "bible_is_not_the_game",
     "rule": "Remember the bible is not the game.",
     "why": "The bible is the base of the iceberg; the player sees only a "
            "small tip. Write what you know, and deliver what you choose.",
     "basis": "synthesis"},
)


_PITFALL_BY_ID = {item["id"]: item for item in PITFALLS}


def pitfall(pitfall_id):
    """One pitfall by id, or None."""
    return _PITFALL_BY_ID.get(pitfall_id)
