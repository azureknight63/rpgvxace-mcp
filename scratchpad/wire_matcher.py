"""Give `_Hits` the matcher, so one place decides when a walk is done.

Threading the matcher separately to four walks *and* into `reply` was two
routes to the same value, and only one of them could learn about the deadline.
"""
import pathlib
import re

P = pathlib.Path(r"C:\Users\azure\Desktop\dev\rpgmvx-mcp"
                 r"\src\rpgvxace_mcp\tools\search_tools.py")
t = P.read_text(encoding="utf-8")
landed = []


def sub(label, old, new, count=1):
    global t
    if t.count(old) < count:
        raise SystemExit("NOT FOUND (%s), %d of %d:\n%s"
                         % (label, t.count(old), count, old[:200]))
    t = t.replace(old, new, count)
    landed.append(label)


# --- the collector owns the matcher -----------------------------------------
sub("_Hits takes the matcher",
    '''    __slots__ = ("rows", "limit", "searched", "unsearchable", "unreadable",
                 "walk")

    def __init__(self, limit: int):
        self.rows: list[dict[str, Any]] = []
        self.limit = limit''',
    '''    __slots__ = ("rows", "limit", "matcher", "searched", "unsearchable",
                 "unreadable", "walk")

    def __init__(self, limit: int, matcher: "_Matcher | None"):
        self.rows: list[dict[str, Any]] = []
        self.limit = limit
        # Held here rather than passed to each walk as well. Threading it both
        # ways meant `done` could not see the matcher's deadline, and only the
        # reply knew the text test had given up.
        self.matcher = matcher''')

sub("full keeps its meaning and done gains the deadline",
    '''    @property
    def full(self) -> bool:
        return len(self.rows) >= self.limit
''',
    '''    @property
    def full(self) -> bool:
        """The caller's limit is reached. Distinct from `done` on purpose.

        `may_have_more` means "there are hits past this page", which is what a
        full page implies and a spent deadline does not -- that one gets its
        own note, because "I stopped early" and "here is a full page" are
        different things to tell a caller.
        """
        return len(self.rows) >= self.limit

    @property
    def done(self) -> bool:
        """Nothing further this search does can change the answer.

        Either the page is full, or the text test has spent its deadline and
        now answers False without looking -- in which case walking the rest of
        the project only costs the loading and decoding.
        """
        return self.full or (self.matcher is not None
                             and self.matcher.exhausted)
''')

sub("reply reads its own matcher",
    '''    def reply(self, matcher: "_Matcher | None") -> dict[str, Any]:''',
    '''    def reply(self) -> dict[str, Any]:''')
sub("reply's clipped branch",
    '''        if matcher is not None and matcher.clipped:''',
    '''        matcher = self.matcher
        if matcher is not None and matcher.clipped:''')
sub("reply reports the deadline",
    '''        stopped = self.walk.note("search")
        if stopped:
            out["command_walk_stopped"] = stopped
            # So an empty result cannot read as "there are none".
            out["may_have_more"] = True
        return out''',
    '''        stopped = self.walk.note("search")
        if stopped:
            out["command_walk_stopped"] = stopped
            # So an empty result cannot read as "there are none".
            out["may_have_more"] = True
        spent = matcher.note() if matcher is not None else None
        if spent:
            out["text_scan_stopped"] = spent
            out["may_have_more"] = True
        return out''')

# --- the walks read it off the collector ------------------------------------
sub("search builds the collector around the matcher",
    "    found = _Hits(limit)", "    found = _Hits(limit, matcher)")
sub("search's reply call", "    return found.reply(matcher)",
    "    return found.reply()")
sub("scope loop stops on either bound",
    "            if scope in scopes and not found.full:\n                walk(ctx, found, matcher)",
    "            if scope in scopes and not found.done:\n                walk(ctx, found)")
sub("events walk call", "        _search_events(ctx, found, matcher, code_filter)",
    "        _search_events(ctx, found, code_filter)")

sub("events walk signature",
    '''def _search_events(ctx: Context, found: _Hits, matcher: "_Matcher | None",
                   code_filter: "int | None") -> None:
    """Every event command list on every map, plus common events and troops."""
    found.searching("events")''',
    '''def _search_events(ctx: Context, found: _Hits,
                   code_filter: "int | None") -> None:
    """Every event command list on every map, plus common events and troops."""
    found.searching("events")
    matcher = found.matcher''')

for name, scope in (("_search_database", "database"),
                    ("_search_maps", "maps"),
                    ("_search_scripts", "scripts")):
    old = ("def %s(ctx: Context, found: _Hits,\n"
           "                 %smatcher: \"_Matcher | None\") -> None:"
           % (name, " " * (len(name) - len("_search_maps"))))
    # The continuation indent differs per name; match on the signature loosely.
    pattern = re.compile(
        r"def %s\(ctx: Context, found: _Hits,\s*\n\s*matcher: \"_Matcher \| None\"\) -> None:"
        % name)
    match = pattern.search(t)
    if not match:
        raise SystemExit("signature not found for %s" % name)
    t = t[:match.start()] + ("def %s(ctx: Context, found: _Hits) -> None:" % name) + t[match.end():]
    landed.append("%s signature" % name)

# Each of the three now needs `matcher` bound from the collector, on the line
# after its docstring. Done by name so an added walk cannot be missed silently.
sub("database walk binds the matcher",
    '''    """Every string field of every entry of every id-indexed table."""
    for key, entry in ctx.project.data_files.items():''',
    '''    """Every string field of every entry of every id-indexed table."""
    matcher = found.matcher
    for key, entry in ctx.project.data_files.items():''')
sub("maps walk binds the matcher",
    '''    """The map names in the tree. The maps themselves are the events scope."""
    try:''',
    '''    """The map names in the tree. The maps themselves are the events scope."""
    matcher = found.matcher
    try:''')
sub("scripts walk binds the matcher",
    '''    """Every line of every script slot that inflates."""
    try:''',
    '''    """Every line of every script slot that inflates."""
    matcher = found.matcher
    try:''')

# --- every in-walk stop check honours both bounds ---------------------------
before = t.count("if found.full:")
t = t.replace("if found.full:", "if found.done:")
landed.append("%d in-walk stop checks now read `done`" % before)

t.encode("utf-8")
tmp = P.with_suffix(".py.tmp")
tmp.write_text(t, encoding="utf-8", newline="")
tmp.replace(P)
print("landed %d edits:" % len(landed))
for line in landed:
    print("   " + line)
