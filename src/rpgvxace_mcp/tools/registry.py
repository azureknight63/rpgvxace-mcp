"""Tool registration and argument handling shared by every tool module."""

from __future__ import annotations

import contextlib
from typing import Any, Callable, Iterator

from ..convert import TextCodec
from ..rubytypes import _bare_name as bare_name
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
    """Quote a caller's value back at it, without quoting all of it."""
    shown = repr(value)
    if len(shown) > MAX_ECHO_CHARS:
        shown = shown[:MAX_ECHO_CHARS] + "... (%d characters)" % len(shown)
    return shown


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
        try:
            yield keeper
        finally:
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
        """
        check_args(self.schema, args)
        return self.handler(ctx, args)

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

    ``known`` is drawn from the project's own data -- the fields this entry
    already carries, or a sibling's -- rather than from a table of documented
    field names. Two reasons: the reference tables cover the map and event
    classes but no database class, so a documented allow-list would refuse
    every field on every database entry; and a project whose scripts add their
    own ivars is normal, and refusing those would break editing it.

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
               ", ".join(sorted(known))))


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
               ", ".join(sorted(known))))
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


def _check_declared(key: str, value: Any, spec: dict[str, Any]) -> None:
    """Apply one property's declared bounds to the value given for it."""
    if value is None:
        return
    if "maxItems" in spec and isinstance(value, list) \
            and len(value) > spec["maxItems"]:
        raise ToolError("%s takes at most %d entries, got %d"
                        % (key, spec["maxItems"], len(value)))
    if spec.get("type") == INT and isinstance(value, (int, float)) \
            and not isinstance(value, bool):
        as_int(value, key, spec.get("minimum"), spec.get("maximum"))
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
