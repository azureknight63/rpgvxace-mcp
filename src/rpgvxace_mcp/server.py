"""The MCP server: JSON-RPC 2.0 over stdio, with tools and reference resources.

The transport is implemented directly rather than through the MCP SDK so the
server has no third-party dependencies: a user only needs Python, which matters
because the alternative for the VX-family engines is installing Ruby as well.

stdout carries protocol traffic and nothing else. Diagnostics go to stderr.
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any, TextIO

from . import __version__
from .convert import ConversionError
from .docs import RESOURCES, server_instructions
from .project import Project, ProjectError, ReadOnlyError, StaleFileError
from .tools import db_tools, event_tools, map_tools, misc_tools  # noqa: F401
from .tools.registry import Context, ToolError, all_tools, REGISTRY

SERVER_NAME = "rpgvxace-mcp"
PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = {"2025-06-18", "2025-03-26", "2024-11-05"}

# JSON-RPC error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class Server:
    def __init__(self, project: Project, verbose: bool = False):
        self.context = Context(project)
        self.verbose = verbose
        self.initialized = False

    # -- dispatch -------------------------------------------------------------
    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}

        if method is None:
            return _error(request_id, INVALID_REQUEST, "message has no method")

        # Notifications carry no id and take no response.
        if request_id is None:
            if method == "notifications/initialized":
                self.initialized = True
            return None

        try:
            if method == "initialize":
                return _result(request_id, self._initialize(params))
            if method == "ping":
                return _result(request_id, {})
            if method == "tools/list":
                return _result(request_id, {"tools": [t.definition() for t in all_tools()]})
            if method == "tools/call":
                return _result(request_id, self._call_tool(params))
            if method == "resources/list":
                return _result(request_id, {"resources": [
                    {k: r[k] for k in ("uri", "name", "title", "description", "mimeType")}
                    for r in RESOURCES
                ]})
            if method == "resources/read":
                return _result(request_id, self._read_resource(params))
            if method in ("resources/templates/list",):
                return _result(request_id, {"resourceTemplates": []})
            return _error(request_id, METHOD_NOT_FOUND, "unknown method %r" % method)
        except ToolError as exc:
            return _error(request_id, INVALID_PARAMS, str(exc))
        except Exception as exc:  # noqa: BLE001 - the loop must survive any tool
            self._log(traceback.format_exc())
            return _error(request_id, INTERNAL_ERROR, "%s: %s" % (type(exc).__name__, exc))

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        requested = params.get("protocolVersion")
        version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else PROTOCOL_VERSION
        project = self.context.project
        return {
            "protocolVersion": version,
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"listChanged": False, "subscribe": False},
            },
            "serverInfo": {
                "name": SERVER_NAME,
                "title": "RPG Maker VX Ace",
                "version": __version__,
            },
            "instructions": server_instructions(project) + (
                "\n\nOpen project: %s (%s, text encoding %s%s)."
                % (project.root, project.profile.label, project.codec.name,
                   ", read-only" if project.read_only else "")
            ),
        }

    def _call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        tool = REGISTRY.get(name)
        if tool is None:
            raise ToolError(
                "unknown tool %r; available tools are %s"
                % (name, ", ".join(sorted(REGISTRY)))
            )
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise ToolError("arguments must be an object")

        try:
            payload = tool.handler(self.context, arguments)
        except (ToolError, ProjectError, ConversionError, ValueError) as exc:
            # Tool-level failures are reported inside the result so the model can
            # read the message and correct itself, per the MCP tool error model.
            return _tool_error(exc, tool.name)
        return {
            "content": [{"type": "text", "text": _dump_json(payload)}],
            "isError": False,
        }

    def _read_resource(self, params: dict[str, Any]) -> dict[str, Any]:
        uri = params.get("uri")
        for resource in RESOURCES:
            if resource["uri"] == uri:
                return {"contents": [{
                    "uri": uri,
                    "mimeType": resource["mimeType"],
                    "text": resource["builder"](self.context.project),
                }]}
        raise ToolError("unknown resource %r; available: %s"
                        % (uri, ", ".join(r["uri"] for r in RESOURCES)))

    def _log(self, text: str) -> None:
        if self.verbose:
            print(text, file=sys.stderr, flush=True)

    # -- loop -----------------------------------------------------------------
    def serve(self, stdin: TextIO, stdout: TextIO) -> None:
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                _write(stdout, _error(None, PARSE_ERROR, "invalid JSON: %s" % exc))
                continue
            # A batch is a list of messages; respond with a list of the answers.
            if isinstance(message, list):
                responses = [r for r in (self.handle(m) for m in message if isinstance(m, dict))
                             if r is not None]
                if responses:
                    _write(stdout, responses)
                continue
            if not isinstance(message, dict):
                _write(stdout, _error(None, INVALID_REQUEST, "message must be an object"))
                continue
            response = self.handle(message)
            if response is not None:
                _write(stdout, response)


def _tool_error(exc: Exception, tool_name: str) -> dict[str, Any]:
    hint = ""
    if isinstance(exc, StaleFileError):
        hint = ("\n\nNothing was written. Close the RPG Maker editor, then call "
                "the tool again with refresh: true.")
    elif isinstance(exc, ReadOnlyError):
        hint = "\n\nThe server was started with --read-only."
    return {
        "content": [{"type": "text",
                     "text": "%s failed: %s%s" % (tool_name, exc, hint)}],
        "isError": True,
    }


def _dump_json(payload: Any) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str)


def _result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _write(stdout: TextIO, payload: Any) -> None:
    stdout.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    stdout.flush()
