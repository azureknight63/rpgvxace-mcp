"""Tool registration and argument handling shared by every tool module."""

from __future__ import annotations

from typing import Any, Callable

from ..convert import TextCodec
from ..project import Project


class ToolError(ValueError):
    """A problem with the request. Reported to the model, not a server fault."""


class Context:
    """What a tool handler is given: the open project and its text codec."""

    __slots__ = ("project",)

    def __init__(self, project: Project):
        self.project = project

    @property
    def codec(self) -> TextCodec:
        return self.project.codec


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


REGISTRY: dict[str, Tool] = {}


def tool(name: str, title: str, description: str, schema: dict[str, Any],
         writes: bool = False):
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


def need_int(args: dict[str, Any], key: str) -> int:
    value = need(args, key)
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ToolError("%s must be a whole number, got %r" % (key, value))


def opt_int(args: dict[str, Any], key: str, default: int | None = None) -> int | None:
    if args.get(key) is None:
        return default
    try:
        return int(args[key])
    except (TypeError, ValueError):
        raise ToolError("%s must be a whole number, got %r" % (key, args[key]))


def need_str(args: dict[str, Any], key: str) -> str:
    value = need(args, key)
    if not isinstance(value, str):
        raise ToolError("%s must be a string, got %r" % (key, value))
    return value


def opt_str(args: dict[str, Any], key: str, default: str | None = None) -> str | None:
    value = args.get(key)
    if value is None:
        return default
    if not isinstance(value, str):
        raise ToolError("%s must be a string, got %r" % (key, value))
    return value


def opt_bool(args: dict[str, Any], key: str, default: bool = False) -> bool:
    value = args.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ToolError("%s must be true or false, got %r" % (key, value))
    return value


def opt_list(args: dict[str, Any], key: str) -> list[Any]:
    value = args.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ToolError("%s must be an array, got %r" % (key, value))
    return value


def opt_dict(args: dict[str, Any], key: str) -> dict[str, Any]:
    value = args.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ToolError("%s must be an object, got %r" % (key, value))
    return value


def one_of(args: dict[str, Any], key: str, allowed: list[str],
           default: str | None = None) -> str:
    value = args.get(key) or default
    if value is None:
        raise ToolError("%s is required; choose one of %s" % (key, ", ".join(allowed)))
    if value not in allowed:
        raise ToolError("%s must be one of %s, got %r" % (key, ", ".join(allowed), value))
    return value
