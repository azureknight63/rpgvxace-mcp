"""Tool registration and argument handling shared by every tool module."""

from __future__ import annotations

import contextlib
from typing import Any, Callable, Iterator

from ..convert import TextCodec
from ..project import Project

# How much of a caller's own value is quoted back in an error. Without this, a
# rejected multi-megabyte argument becomes a multi-megabyte error message.
MAX_ECHO_CHARS = 120


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

            with ctx.staged(filename) as write_landed:
                ...mutate the cached value...
                write = ctx.project.save_file(filename, dry_run=dry_run, ...)
                if not dry_run:
                    write_landed.done()

        Reading is unaffected -- dropping the cache costs one re-parse.
        """
        keeper = StagedWrite()
        try:
            yield keeper
        finally:
            if not keeper.committed:
                for filename in filenames:
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
    """The handle :meth:`Context.staged` yields; call ``done()`` when written."""

    __slots__ = ("committed",)

    def __init__(self) -> None:
        self.committed = False

    def done(self) -> None:
        self.committed = True


REGISTRY: dict[str, Tool] = {}


def tool(name: str, title: str, description: str, schema: dict[str, Any],
         writes: bool = False):
    """Register a tool handler under ``name``.

    ``writes`` drives the readOnly and destructive hints in
    :meth:`Tool.definition`, and it marks the handlers that change a cached
    file. Every one of those must wrap its mutation in
    ``with ctx.staged(filename)`` and call ``done()`` only once the write has
    actually landed -- otherwise a dry run or a refused write stays in the
    cache for the next write of that file to put on disk. See
    :meth:`Context.staged`.
    """
    """Register a tool handler."""

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
    if args.get(key) is None:
        return default
    return as_int(args[key], key, minimum, maximum)


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
