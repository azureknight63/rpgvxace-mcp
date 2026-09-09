"""Tool registration and argument handling shared by every tool module."""

from __future__ import annotations

import contextlib
from typing import Any, Callable, Iterator

from ..convert import TextCodec, brief, decoded_text
from ..rubytypes import _bare_name as bare_name, named
from ..project import Project

# How much of a caller's own value is quoted back in an error. Without this, a
# rejected multi-megabyte argument becomes a multi-megabyte error message.
MAX_ECHO_CHARS = 120

# How many operations one editing call applies. A batch larger than
# this is not one a caller composed deliberately, and every op in it
# mutates a cached file before any of them is written.
MAX_OPS = 200


class ToolError(ValueError):
    """A problem with the request. Reported to the model, not a server fault."""


def echo(value: Any) -> str:
    """Quote a caller's value back at it, without quoting all of it.

    `convert.brief` with this layer's own cap. It was a fourth
    implementation of that job, and `brief`'s docstring claimed the merge
    that had not happened -- while this copy kept `repr` on a container,
    which is the half that bounds the result and not the work.
    """
    return brief(value, MAX_ECHO_CHARS)


def decoded(ctx: "Context", value: Any) -> str:
    """Text from a project file, ready to put in a reply: capped, sanitised.

    The tools layer's one spelling of this, over `convert.decoded_text`.
    Inline it and you get the version this replaced -- a bare
    `codec.decode(value.data)` with no cap, which put a 1,000,006-character
    map name carrying an ANSI escape into `map_list`'s reply whole.
    `TestOneDecodeForTheWholeToolsLayer` is what holds that.
    """
    return decoded_text(ctx.codec, value)


class Context:
    """What a tool handler is given: the open project and its text codec."""

    __slots__ = ("project",)

    def __init__(self, project: Project):
        self.project = project

    @property
    def codec(self) -> TextCodec:
        return self.project.codec

    @contextlib.contextmanager
    def staged(self, *filenames: str) -> Iterator["StagedWrite"]:
        """Apply changes to a cached file, keeping none of them unless they land.

        Every editing tool mutates the value in :class:`Project`'s cache and
        then asks it to be written. Nothing used to undo that mutation, so
        three things went wrong in the same way: a ``dry_run`` staged its
        "preview" in the cache, a refused write (read-only, or the editor
        holding the file) left the attempt behind, and an op batch that failed
        halfway kept the ops before the failure. In each case the change was
        invisible until some later, unrelated write of the same file marshalled
        the cache and put it on disk -- a silent edit to someone's game that no
        tool call had asked for.

        So: on the way out, unless the block says the write completed, the
        cached file is dropped and re-read from disk next time.

            with ctx.staged(filename) as staged:
                ...mutate the cached value...
                write = staged.save(filename, dry_run=dry_run, tool="...",
                                    summary="...")

        Reading is unaffected -- dropping the cache costs one re-parse.
        """
        keeper = StagedWrite(self.project, filenames)
        # Pinned for the length of the block. The mutation these tools make
        # lives in the project's cache until it is saved, and that cache
        # evicts when it goes over budget -- dropping one of these entries in
        # between would write the file as it was on disk and report the edit
        # as applied.
        self.project.pin(*filenames)
        try:
            yield keeper
        finally:
            self.project.unpin(*filenames)
            for filename in filenames:
                if filename not in keeper.written:
                    self.project.forget_file(filename)


class Tool:
    __slots__ = ("name", "title", "description", "schema", "handler", "writes")

    def __init__(self, name: str, title: str, description: str,
                 schema: dict[str, Any], handler: Callable[[Context, dict], Any],
                 writes: bool):
        self.name = name
        self.title = title
        self.description = description
        self.schema = schema
        self.handler = handler
        self.writes = writes

    def run(self, ctx: "Context", args: dict[str, Any]) -> Any:
        """Check the arguments against this tool's own schema, then run it.

        The check lives here rather than in the transport so that it travels
        with the tool: put in one caller, every other entry point skipped it,
        and a schema that is not enforced everywhere is not a contract.

        The reply says which op answered it, for the same reason and in the
        same place. Three of the tools that take an `op` echoed it and four
        did not, so a caller holding a reply could not always tell which
        branch produced it -- and `op` is what selects the branch, so it is
        the one argument a reply cannot be read without. Added here rather
        than in each handler: that is what "three of seven" comes from.
        """
        check_args(self.schema, args)
        reply = self.handler(ctx, args)
        op = args.get("op")
        if isinstance(reply, dict) and isinstance(op, str):
            # Rebuilt rather than assigned into, and *whether or not* the
            # handler already set `op`: five of `map_edit`'s reply sites set
            # it themselves and put it second or third, so the guarantee
            # this docstring makes did not hold for the tool with the most
            # branches. Placing it here means no handler can get it wrong.
            #
            # `dry_run` keeps its own value rather than being assumed True:
            # stripping the key unconditionally would drop an explicit
            # False, which is a silent drop in the one function whose job is
            # to normalise a reply's shape.
            lead = ({"dry_run": reply["dry_run"]} if "dry_run" in reply
                    else {})
            return {**lead, "op": op,
                    **{k: v for k, v in reply.items()
                       if k not in ("dry_run", "op")}}
        return reply

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": self.schema,
            "annotations": {
                "title": self.title,
                "readOnlyHint": not self.writes,
                "destructiveHint": self.writes,
                "idempotentHint": False,
                "openWorldHint": False,
            },
        }


class StagedWrite:
    """The handle :meth:`Context.staged` yields: write through it.

    :meth:`save` is the whole point, and is deliberately the only way to keep
    what a tool staged. Deciding "commit only when the write actually happened"
    used to be re-derived at all ten call sites, so a new tool could write and
    simply forget to commit, reintroducing the leak the staging exists to stop.
    Now the save and the commit are one call: writing correctly and forgetting
    to commit are no longer two different things a tool can do.
    """

    __slots__ = ("written", "_project", "_staged")

    def __init__(self, project: Project, staged: "tuple[str, ...]") -> None:
        self.written: set[str] = set()
        self._project = project
        self._staged = staged

    def save(self, filename: str, *, dry_run: bool = False, tool: str = "",
             summary: str = "") -> dict[str, Any]:
        """Write one file, and keep that file's staged changes if it landed.

        Per file, not per block. A single flag meant the first save to land
        committed every staged file, so a two-file write whose second save was
        refused kept the second file's mutation in the cache for a later write
        to persist -- while the caller was told the whole call had failed.
        """
        if filename not in self._staged:
            raise ToolError(
                "%s was written without being staged, so a failure part-way "
                "could not be undone. This is a bug in the tool, not in the "
                "request." % filename)
        write = self._project.save_file(filename, dry_run=dry_run, tool=tool,
                                       summary=summary)
        if not dry_run:
            self.written.add(filename)
        return write


REGISTRY: dict[str, Tool] = {}


def tool(name: str, title: str, description: str, schema: dict[str, Any],
         writes: bool = False):
    """Register a tool handler under ``name``.

    ``writes`` drives the readOnly and destructive hints in
    :meth:`Tool.definition`, and it marks the handlers that change a cached
    file. Every one of those must mutate inside ``with ctx.staged(filename)``
    and write through the handle it yields -- ``write_landed.save(filename,
    dry_run=dry_run, ...)`` -- which saves and commits in one call. A mutation
    that is never saved that way is dropped, so a dry run or a refused write
    cannot stay in the cache for the next write of that file to put on disk.
    See :meth:`Context.staged` and :class:`StagedWrite`.
    """

    def decorate(handler: Callable[[Context, dict], Any]):
        if name in REGISTRY:
            raise RuntimeError("duplicate tool name %r" % name)
        REGISTRY[name] = Tool(name, title, description, schema, handler, writes)
        return handler

    return decorate


def all_tools() -> list[Tool]:
    return [REGISTRY[name] for name in sorted(REGISTRY)]


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------


def schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


def prop(kind: str, description: str, **extra: Any) -> dict[str, Any]:
    out = {"type": kind, "description": description}
    out.update(extra)
    return out


INT = "integer"
STR = "string"
BOOL = "boolean"
ARR = "array"
OBJ = "object"

DRY_RUN = prop(BOOL, "Report what would change without writing anything to disk.")

# What a preview calls each key a completed write would name in the past
# tense.
#
# One table, because seven tools write and this fix used to live in exactly
# one of them. The diagnosis, from the tool that had it: a preview answered
# `applied: [{"op": "update", "id": 1, "changes": [...]}]`, every word of it
# past tense, with the one field saying otherwise -- `write.dry_run` --
# nested a level down, and a model reading the op rows concluded the change
# had landed and did not retry it.
#
# `map_edit` is why this belongs here rather than in each tool: its create and
# delete paths already said `dry_run` at the top level and used `would_write`
# and `would_delete`, while its update and resize paths said `changes`. One
# tool, two conventions, nothing for a caller to learn.
PREVIEW_NAMES = {
    "applied": "would_apply",
    # `writes` is a list of dicts and `would_write` was a list of names, in
    # two branches of one tool: `_create_map` built the second by hand while
    # every other path here built the first. Same key either side of the
    # rename now, so the preview and the act are the same sentence.
    "writes": "would_write",
    "cloned_from": "would_clone_from",
    # A snapshot's two lists. `Project.snapshot` names the facts and this
    # names them for a preview, so the project layer stays below the layer
    # that decides how a reply reads.
    "files": "would_copy",
    "pruned": "would_prune",
    "changes": "would_change",
    "changed": "would_change",
    "created": "would_create",
    "deleted": "would_delete",
    "removed": "would_remove",
    "restored": "would_restore",
    "renamed_to": "would_rename_to",
    "code_characters": "would_write_characters",
    "after": "would_become",
}


# How a reply says it is not the whole story. Three suffixes, each with one
# type, because there were twenty-odd spellings and two of them collided:
# `notes` was a list in one tool and a sentence in another, and `_truncated`
# was a boolean in two tools and a paragraph in a third -- so a caller
# reading either had to know which tool it was talking to.
#
#   <subject>_truncated  bool  the answer about <subject> is partial
#   <subject>_note       str   prose about <subject>, whatever the reason
#   <subject>_not_read   dict  what could not be read, and why, by name
#
# A dynamic key is the fourth thing this replaces: `"%s_note" % scope` is a
# key a caller cannot find in any schema, so `search` now answers one
# `scopes_not_read` mapping instead of one key per scope.
PARTIAL_SUFFIXES = ("_truncated", "_note", "_not_read")


def partial(subject: str, truncated: bool = False,
            notes: "tuple[str, ...] | list[str]" = ()) -> dict[str, Any]:
    """The keys that say an answer about ``subject`` is not complete.

    One function, so the suffixes and their types cannot drift again. The
    boolean and the prose are separate keys deliberately: a model reads the
    sentence and a caller's code reads the flag, and folding them into one
    key is what made `events_truncated` a paragraph where
    `references_truncated` was a boolean.
    """
    out: dict[str, Any] = {}
    if truncated:
        out["%s_truncated" % subject] = True
    said = [note for note in notes if note]
    if said:
        out["%s_note" % subject] = " ".join(said)
    return out


def preview_row(row: dict[str, Any]) -> dict[str, Any]:
    """One op row of a preview, renamed. Where a model reads what happened."""
    return {PREVIEW_NAMES.get(key, key): value for key, value in row.items()}


def write_reply(body: dict[str, Any], dry_run: bool) -> dict[str, Any]:
    """One writing tool's reply, phrased for what actually happened.

    A completed write keeps its own words -- `applied`, `changes` -- because
    they are true. A preview says `dry_run` first, before the rows it carries,
    and renames every key that would otherwise read as a completed change.
    The descriptions either side of the rename are identical, which is the
    point: a preview and the act have to be the same sentence, or the preview
    is not one.

    Idempotent, so a tool whose branch already phrased itself correctly can be
    passed through without changing.
    """
    if not dry_run:
        return body
    out: dict[str, Any] = {"dry_run": True}
    for key, value in body.items():
        if key == "dry_run":
            continue
        if isinstance(value, list):
            value = [preview_row(item) if isinstance(item, dict) else item
                     for item in value]
        out[PREVIEW_NAMES.get(key, key)] = value
    return out

REFRESH = prop(BOOL, "Re-read the file from disk instead of using the copy already "
                     "in memory. Use after the RPG Maker editor has saved.")


# ---------------------------------------------------------------------------
# Argument accessors
# ---------------------------------------------------------------------------


def refuse_fields(patch: dict[str, Any], fields: "tuple[str, ...]",
                  remedy: str) -> None:
    """Refuse a patch naming any of ``fields``.

    Keys are compared bare. Ruby ivars are written ``@width`` and this
    codebase's patches accept either spelling, so a refusal list checked
    against the raw key let ``{"@width": 999}`` straight through every one of
    the three guards that shared this shape.
    """
    for key in patch:
        if bare_name(key) in fields:
            raise ToolError("%s cannot be set with a patch. %s"
                            % (bare_name(key), remedy))


def refuse_unknown_fields(patch: dict[str, Any], known: "set[str]",
                          what: str) -> None:
    """Refuse a patch naming a field this kind of entry does not have.

    ``apply_patch`` only checks that a name is a *syntactically* valid Ruby
    ivar, so a plausible typo -- ``pirce`` for ``price`` -- used to invent a
    new instance variable and be reported back as a successful change. The
    editor rejects objects carrying instance variables it does not expect, and
    ``raw_data._assign`` already refuses this on exactly those grounds: the
    deliberately unvalidated escape hatch was stricter than the validating
    tools.

    ``known`` always includes the project's own data -- the fields this entry
    carries, or a sibling's -- and never consists of documented names alone.
    Two reasons: the reference tables cover the map and event classes but no
    database class, so a documented allow-list would refuse every field on
    every database entry; and a project whose scripts add their own ivars is
    normal, and refusing those would break editing it.

    It is not *only* the project's data, which an earlier version of this said:
    `known_fields` unions in the class's documented fields, so a map, an event
    page and a common event each accept a documented field the entry happens
    not to carry yet. `project_info`'s description states that rule; this one
    used to contradict it.

    An empty ``known`` means there was nothing to compare against, so nothing
    is refused: this check narrows what a patch may say, and never blocks a
    write it cannot judge.
    """
    if not known:
        return
    unknown = sorted({bare_name(key) for key in patch} - known)
    if unknown:
        raise ToolError(
            "%s has no field%s %s. It carries %s. Nothing here invents a "
            "field, because the editor rejects an object with instance "
            "variables it does not expect -- check the spelling."
            % (what, "s" if len(unknown) > 1 else "",
               ", ".join(echo(u) for u in unknown),
               named(sorted(known))))


def check_args(schema_def: dict[str, Any], args: dict[str, Any],
               where: str = "this tool") -> None:
    """Hold a call to the schema its tool declares.

    Every tool's schema says ``additionalProperties: False`` and most declare
    numeric bounds, but nothing enforced either: the transport hands arguments
    straight to the handler. So a misspelled ``dry_run`` -- ``dryrun``,
    ``dry-run`` -- was silently dropped and the tool performed a real write
    while the caller believed it had asked for a preview. That is the worst
    shape of failure available here, because the caller is told what it
    expected to hear.

    Checked once at dispatch rather than in each handler, so a new tool is
    covered by declaring its schema, which it has to do anyway.
    """
    known = schema_def.get("properties") or {}
    unknown = [key for key in args if key not in known]
    if unknown:
        raise ToolError(
            "unknown argument%s %s in %s. It takes %s -- check the "
            "spelling, because an argument that is not known is ignored "
            "rather than applied."
            % ("s" if len(unknown) > 1 else "",
               ", ".join(echo(u) for u in sorted(unknown)), where,
               named(sorted(known))))
    for key, value in args.items():
        _check_declared(key, value, known[key])
        item = (known[key].get("items") or {}) if isinstance(value, list) else {}
        if item.get("properties"):
            # An op inside `ops` is an argument too. Checking only the top
            # level meant a misspelled key inside one was dropped, and for
            # `set_commands` that emptied the page it was meant to rewrite.
            for index, entry in enumerate(value):
                if isinstance(entry, dict):
                    check_args(item, entry, "%s[%d]" % (key, index))


def known_fields(ctx: "Context", obj: Any, cls: str) -> "set[str]":
    """What a patch to this object may name: what it has, plus what it documents.

    Both halves are needed and neither alone will do. The fields an object
    carries are the ones certainly real, but a map or a page can legitimately
    be given a field the editor left off this particular instance -- and the
    reference tables cover exactly those classes. So the union, which
    `refuse_unknown_fields` then holds a patch to.

    Written once because it had been written three times -- for a map, for an
    event page, and inline for a common event -- and the inline copy was both
    the one that would drift and the one with no comment saying why the union
    is the right set.
    """
    documented = ctx.project.profile.class_fields.get(cls, {})
    return set(obj.fields()) | set(documented)


def scopes_arg(args: dict[str, Any], key: str,
                allowed: "tuple[str, ...] | list[str]") -> list[str]:
    """The scopes a caller asked for, refusing names that do nothing.

    Dropping an unrecognised name and running everything instead is the
    opposite of what the rest of these tools do with a bad argument: the
    caller believes it narrowed the search and is handed the whole project.
    """
    asked = opt_list(args, key)
    unknown = [s for s in asked if s not in allowed]
    if unknown:
        raise ToolError("unknown %s %s; choose from %s"
                        % (key, ", ".join(echo(u) for u in unknown),
                           ", ".join(allowed)))
    return [s for s in asked if s in allowed] or list(allowed)


def check_op_args(op: str, args: dict[str, Any],
                  uses: "dict[str, set[str]]", where: str = "this tool") -> None:
    """Refuse an argument the chosen op does not read.

    ``check_args`` refuses a name no op knows. This refuses a name that is
    real but inert here: ``event_edit`` op "update" reads name and patch and
    never looks at ``commands``, so a single op asking to rename an event and
    rewrite its command list renamed it, dropped the commands, and reported
    "updated event 1". The caller has no way to see the difference between
    that and the whole request landing, which makes a silent drop worse than
    a refusal -- it will not retry what it believes already happened.

    ``uses`` maps each op to every argument it reads, including the ones the
    tool reads for every op. An op absent from that table is refused as a bug
    in this server: `one_of` has already accepted it, so a missing entry
    means these two lists disagree -- and returning quietly would drop the
    caller's arguments and report success, which is the failure this function
    exists to prevent, reached through the function itself.

    ``TestEveryOpSelectingToolHasATable`` walks the registry, so a tool that
    grows an ``op`` without a table fails rather than silently ignoring
    arguments.
    """
    allowed = uses.get(op)
    if allowed is None:
        # Refused, not returned. An op reaches here only after `one_of` has
        # accepted it, so a missing entry means this server's own two lists
        # disagree -- and returning quietly would drop the caller's arguments
        # and report success, which is the failure this function exists to
        # prevent, reached through the function itself. Named as a server bug
        # rather than a caller's mistake, because that is what it is.
        raise ToolError(
            "op \"%s\" is accepted by %s but has no declared argument list, "
            "so its arguments could not be checked. That is a bug in this "
            "server rather than in the request; please report it. Refusing "
            "rather than running it, because the alternative is dropping "
            "arguments and calling that success." % (op, where))
    inert = sorted(key for key in args if key not in allowed)
    if not inert:
        return
    details = []
    for key in inert:
        takers = sorted(name for name, keys in uses.items() if key in keys)
        details.append("%s (op %s)" % (echo(key), ", ".join('"%s"' % t for t in takers))
                       if takers else "%s (no op here reads it)" % echo(key))
    raise ToolError(
        "op \"%s\" in %s does not read %s. It reads %s. Nothing is ignored "
        "silently here, because a dropped argument reports the same success "
        "as one that was applied -- split the request into one op per "
        "change, or drop the argument."
        % (op, where, "; ".join(details), ", ".join(sorted(allowed - {"op"}))))


def _check_declared(key: str, value: Any, spec: dict[str, Any]) -> None:
    """Apply one property's declared bounds to the value given for it."""
    if value is None:
        return
    if "maxItems" in spec and isinstance(value, list) \
            and len(value) > spec["maxItems"]:
        raise ToolError("%s takes at most %d entries, got %d"
                        % (key, spec["maxItems"], len(value)))
    # `maxLength` too, which this enforced for lists and not for strings --
    # so a declared string ceiling was documentation rather than a gate, and
    # the one property that declares one enforced it again by hand.
    if "maxLength" in spec and isinstance(value, str) \
            and len(value) > spec["maxLength"]:
        raise ToolError("%s takes at most %d characters, got %d"
                        % (key, spec["maxLength"], len(value)))
    declared = spec.get("type")
    if declared == INT:
        # Only the wrong *kind* is refused here. `as_int` below owns the two
        # numeric cases -- a bool, and a float that is not whole -- and says
        # why in words this could not improve on; delegating also means the
        # gate and every reader refuse them identically. An integral float is
        # accepted because JSON has no integer type of its own, so a client
        # may legitimately send 2.0 for 2.
        if not isinstance(value, (int, float)):
            raise ToolError(
                "%s must be an integer, got %s. %s"
                % (key, type(value).__name__, echo(value)))
        as_int(value, key, spec.get("minimum"), spec.get("maximum"))
    elif declared == STR and not isinstance(value, str):
        raise ToolError("%s must be a string, got %s. %s"
                        % (key, type(value).__name__, echo(value)))
    elif declared == BOOL and not isinstance(value, bool):
        raise ToolError("%s must be true or false, got %s. %s"
                        % (key, type(value).__name__, echo(value)))
    elif declared == ARR and not isinstance(value, list):
        raise ToolError("%s must be an array, got %s. %s"
                        % (key, type(value).__name__, echo(value)))
    elif declared == OBJ and not isinstance(value, dict):
        raise ToolError("%s must be an object, got %s. %s"
                        % (key, type(value).__name__, echo(value)))
    # Deliberately no enum check here. `one_of` and `_table_arg` already
    # enforce those, and they can say which tables *this project* has and why
    # one is absent -- a generic list of every engine's tables is a worse
    # answer than the one already given.


def need(args: dict[str, Any], key: str) -> Any:
    if key not in args or args[key] is None:
        raise ToolError("%s is required" % key)
    return args[key]


def as_int(value: Any, what: str, minimum: int | None = None,
           maximum: int | None = None) -> int:
    """One integer from a caller, refusing anything that would silently move.

    ``int()`` is not the right coercion for an argument that selects what to
    edit. ``int(True)`` is 1 and ``int(2.9)`` is 2, so a wrong-typed id used to
    address a different entry than the caller named -- and quietly, because the
    tool then reported success against the id it had coerced to.
    :func:`schema.map_filename` refuses a float for exactly this reason, and
    coercing here made that guard unreachable.

    An integral float is allowed: JSON has no integer type of its own, so a
    client may legitimately send ``2.0`` for 2.
    """
    if isinstance(value, bool):
        raise ToolError("%s must be a whole number, not true or false" % what)
    if isinstance(value, float):
        if not value.is_integer():
            raise ToolError(
                "%s must be a whole number, got %s -- it is not clear which "
                "one you meant" % (what, echo(value)))
        value = int(value)
    if not isinstance(value, int):
        raise ToolError("%s must be a whole number, got %s" % (what, echo(value)))
    if minimum is not None and value < minimum:
        raise ToolError("%s must be at least %d, got %d" % (what, minimum, value))
    if maximum is not None and value > maximum:
        raise ToolError("%s must be at most %d, got %d" % (what, maximum, value))
    return value


def need_int(args: dict[str, Any], key: str, minimum: int | None = None,
             maximum: int | None = None) -> int:
    return as_int(need(args, key), key, minimum, maximum)


def opt_int(args: dict[str, Any], key: str, default: int | None = None,
            minimum: int | None = None, maximum: int | None = None) -> int | None:
    """One optional integer, with the declared bounds applied to the default too.

    The default is not always a constant -- a resize falls back to the map's
    own recorded width -- and that comes out of a file this server did not
    necessarily write. Returning it unchecked let a crafted extent straight
    past the maximum the argument declares.
    """
    if args.get(key) is None:
        if default is None:
            return None
        return as_int(default, "the default for %s" % key, minimum, maximum)
    return as_int(args[key], key, minimum, maximum)


def ops_arg(args: dict[str, Any], key: str = "ops") -> list[dict[str, Any]]:
    """The op list an editing tool was given, checked for being usable.

    Both checks were written out in each editing tool, and the copies had
    already drifted on whether they truncated the value they quoted back.
    """
    ops = opt_list(args, key)
    if not ops:
        raise ToolError("%s must contain at least one operation" % key)
    for index, raw_op in enumerate(ops):
        if not isinstance(raw_op, dict):
            raise ToolError("%s[%d] must be an object, got %s"
                            % (key, index, echo(raw_op)))
    return ops


def str_list(args: dict[str, Any], key: str, limit: int) -> list[str]:
    """A list of strings, each read strictly, and bounded in length.

    Coercing with ``str()`` turned a caller's nested array into the text
    ``"['a']"`` and then looked for a file by that name, which reports the
    wrong problem.
    """
    values = opt_list(args, key)
    if len(values) > limit:
        raise ToolError("%s lists %d entries; this server takes at most %d at "
                        "a time" % (key, len(values), limit))
    for index, value in enumerate(values):
        if not isinstance(value, str):
            raise ToolError("%s[%d] must be a string, got %s"
                            % (key, index, echo(value)))
    return list(values)


def int_list(args: dict[str, Any], key: str, limit: int,
             minimum: int | None = None, maximum: int | None = None) -> list[int]:
    """A list of ids, each read strictly, and bounded in length.

    The bare ``[int(i) for i in ...]`` this replaces both coerced each element
    and accepted a list of any length -- and raised Python's own "invalid
    literal" text, which does not say which argument was wrong.
    """
    values = opt_list(args, key)
    if len(values) > limit:
        raise ToolError("%s lists %d entries; this server takes at most %d at "
                        "a time" % (key, len(values), limit))
    return [as_int(v, "%s[%d]" % (key, i), minimum, maximum)
            for i, v in enumerate(values)]


def need_str(args: dict[str, Any], key: str) -> str:
    value = need(args, key)
    if not isinstance(value, str):
        raise ToolError("%s must be a string, got %s" % (key, echo(value)))
    return value


def opt_str(args: dict[str, Any], key: str, default: str | None = None) -> str | None:
    value = args.get(key)
    if value is None:
        return default
    if not isinstance(value, str):
        raise ToolError("%s must be a string, got %s" % (key, echo(value)))
    return value


def opt_bool(args: dict[str, Any], key: str, default: bool = False) -> bool:
    value = args.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ToolError("%s must be true or false, got %s" % (key, echo(value)))
    return value


def opt_list(args: dict[str, Any], key: str) -> list[Any]:
    value = args.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ToolError("%s must be an array, got %s" % (key, echo(value)))
    return value


def opt_dict(args: dict[str, Any], key: str) -> dict[str, Any]:
    value = args.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ToolError("%s must be an object, got %s" % (key, echo(value)))
    return value


def one_of(args: dict[str, Any], key: str, allowed: list[str],
           default: str | None = None) -> str:
    # `or default` would swallow a present-but-falsy value: an explicit
    # op="" took the default instead of being reported, and the message then
    # said the argument was required when it had in fact been given.
    value = args.get(key)
    if value is None:
        value = default
    if value is None:
        raise ToolError("%s is required; choose one of %s" % (key, ", ".join(allowed)))
    if value not in allowed:
        raise ToolError("%s must be one of %s, got %s"
                        % (key, ", ".join(allowed), echo(value)))
    return value
