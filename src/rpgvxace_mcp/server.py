"""The MCP server: JSON-RPC 2.0 over stdio, with tools and reference resources.

The transport is implemented directly rather than through the MCP SDK so the
server has no third-party dependencies: a user only needs Python, which matters
because the alternative for the VX-family engines is installing Ruby as well.

stdout carries protocol traffic and nothing else. Diagnostics go to stderr.
"""

from __future__ import annotations

import base64
import json
import sys
from typing import Any, TextIO

from . import __version__
from .convert import ConversionError
from .docs import RESOURCES, server_instructions
from .project import Project, ProjectError, ReadOnlyError, StaleFileError
from .tools import (  # noqa: F401
    command_docs_tools,
    db_tools,
    event_tools,
    live_tools,
    map_common,
    map_critique_tools,
    map_edit_tools,
    map_paint_tools,
    map_read_tools,
    map_render_tools,
    map_tile_tools,
    misc_tools,
    raw_tools,
    search_tools,
    text_tools,
)
# Imported for the side effect: each module's @tool decorators register its
# tools in REGISTRY, which all_tools() then reads. Removing these as unused
# imports empties the tool list.
# `echo` quotes a caller's own string back in an error message, bounded by the
# tools layer's MAX_ECHO_CHARS. Imported rather than reimplemented: this file
# had its own copy under the same name and the two had drifted -- only the copy
# here sliced a string before repr'ing it, which is the half that matters, and
# the copy every tool calls is the one handed raw arguments.
from .tools.registry import (
    Context,
    ImageReply,
    REGISTRY,
    ToolError,
    all_tools,
    echo,
)


SERVER_NAME = "rpgvxace-mcp"
PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = {PROTOCOL_VERSION, "2025-03-26", "2024-11-05"}

# A request longer than this is not a request anyone meant to send, and
# parsing it costs more than refusing it.
MAX_MESSAGE_CHARS = 32 * 1024 * 1024

# JSON-RPC error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# One client message, and one batch of them. Both are read from a pipe this
# server does not control, so both are bounded before anything is allocated.
MAX_BATCH_MESSAGES = 100

# A response this server will not emit. A tool asked for more than its caller
# can use is a tool error the model can recover from by narrowing the request;
# an oversized protocol frame is not recoverable, so it is refused here.
MAX_RESPONSE_CHARS = 4 * 1024 * 1024

# What one tool result may contribute. Strictly smaller than the frame budget,
# because the result is JSON-escaped into the frame and grows doing so: with
# both set to the same number, a result that just fit came back as "this
# server is broken" instead of the recoverable "narrow your request".
MAX_TOOL_RESULT_CHARS = MAX_RESPONSE_CHARS // 2

# Methods a client may call before the initialize handshake. Everything else
# waits, so protocol version negotiation cannot be skipped.
PRE_INITIALIZE_METHODS = ("initialize", "ping")


class Server:
    def __init__(self, project: Project, verbose: bool = False):
        self.context = Context(project)
        self.verbose = verbose
        self.initialized = False

    # -- dispatch -------------------------------------------------------------
    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params", {})
        if params is None:
            params = {}

        if "result" in message or "error" in message:
            # This is a response, not a request -- a client that echoed our own
            # traffic back at us. Answering it with an error carrying its id
            # would land in the client's pending-request table as if it were
            # the reply to something it had asked.
            return None
        if method is None:
            return _error(request_id, INVALID_REQUEST, "message has no method")
        if not isinstance(method, str):
            # Otherwise the handshake gate below reports the wrong problem.
            return _error(request_id, INVALID_REQUEST,
                          "method must be a string, not %s"
                          % type(method).__name__)

        # Notifications carry no id and take no response.
        if request_id is None:
            return None

        if not isinstance(params, dict):
            # JSON-RPC allows positional params; MCP does not use them, and
            # without this check they reach params.get() as a list.
            return _error(request_id, INVALID_PARAMS,
                          "params must be an object, not %s"
                          % type(params).__name__)
        if not self.initialized and method not in PRE_INITIALIZE_METHODS:
            return _error(
                request_id, INVALID_REQUEST,
                "call initialize before %s: the protocol version has not been "
                "negotiated yet" % echo(method))

        try:
            if method == "initialize":
                result = self._initialize(params)
                self.initialized = True
                return _result(request_id, result)
            if method == "ping":
                return _result(request_id, {})
            if method == "tools/list":
                return _result(request_id, {"tools": [t.definition() for t in all_tools()]})
            if method == "tools/call":
                return _result(request_id, self._call_tool(params))
            if method == "resources/list":
                # Everything but the builder: that is how this server produces
                # a resource's body, and the rest is protocol. Listing the
                # protocol keys here instead meant a field added in docs.py
                # silently failed to reach the client, and a resource missing
                # one raised KeyError.
                return _result(request_id, {"resources": [
                    {k: v for k, v in r.items() if k != "builder"}
                    for r in RESOURCES
                ]})
            if method == "resources/read":
                return _result(request_id, self._read_resource(params))
            if method == "resources/templates/list":
                return _result(request_id, {"resourceTemplates": []})
            return _error(request_id, METHOD_NOT_FOUND,
                          "unknown method %s" % echo(method))
        except ToolError as exc:
            # A ToolError reaching here came from dispatch itself -- an unknown
            # tool or resource, or a malformed params object -- not from a tool
            # body. Those are caught in _call_tool and returned inside the
            # result instead, so the model can read them and correct itself.
            return _error(request_id, INVALID_PARAMS, str(exc))
        except Exception as exc:  # noqa: BLE001 - the loop must survive any tool
            import traceback  # only a fault pays for it: ~10 ms at start-up

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
        tool = REGISTRY.get(name) if isinstance(name, str) else None
        if tool is None:
            raise ToolError(
                "unknown tool %s; available tools are %s"
                % (echo(name), ", ".join(sorted(REGISTRY)))
            )
        arguments = params.get("arguments", {})
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            # `or {}` here would have coerced an empty list past this check.
            raise ToolError("arguments must be an object, not %s"
                            % type(arguments).__name__)

        # name is looked up above; an unhashable one would have raised there,
        # so the isinstance test in that lookup is what keeps this an
        # INVALID_PARAMS rather than an internal error.

        try:
            # run() checks the arguments against the tool's declared schema
            # first. Inside the try, because a wrong argument is the model's to
            # fix: it comes back as a tool error it can read, not as "this
            # server is broken".
            payload = tool.run(self.context, arguments)
        except (ToolError, ProjectError, ConversionError, ValueError,
                OSError, LookupError, TypeError, AttributeError,
                StopIteration, RecursionError, MemoryError) as exc:
            # Tool-level failures are reported inside the result so the model
            # can read the message and correct itself, per the MCP tool error
            # model. The list is wide on purpose: a disk that is full, a
            # wrong-typed value reaching an index, or a shape a hand-crafted
            # project file put where a tool did not expect it are all things
            # the caller can act on, and returning them as protocol errors
            # instead told the model this server was broken. A genuine bug in
            # a handler lands here too, which is the right trade -- the model
            # sees a failure it can report rather than a dead tool.
            return _tool_error(exc, tool.name)
        image = None
        if isinstance(payload, ImageReply):
            # A picture rides in its own content block, after the text, so
            # `content[0]` is the JSON reply for every tool alike.
            payload, image = payload.body, base64.b64encode(
                payload.png).decode("ascii")
        try:
            text = _dump_json(payload)
        except (ValueError, RecursionError) as exc:
            # Marshal object links make a loaded value a graph, so a raw
            # include_raw payload can be cyclic. That is the tool's problem to
            # report, not evidence the server is broken.
            return _tool_error(ToolError(
                "produced a result that could not be serialised (%s). Ask for "
                "less of it -- most tools take a limit, and the raw forms of "
                "the map and event tools are the ones that can be cyclic."
                % exc), tool.name)
        if len(text) + len(image or "") > MAX_TOOL_RESULT_CHARS:
            # Returned, not raised: raising would reach handle()'s ToolError
            # branch and come back as a protocol error, when this is exactly
            # the kind of failure the model can recover from by narrowing its
            # request.
            return _tool_error(ToolError(
                "produced %d characters, above the %d this server returns. "
                "Narrow the request -- most tools take a limit, and the map and "
                "event tools take an id -- then call it again."
                % (len(text), MAX_TOOL_RESULT_CHARS)), tool.name)
        content = [{"type": "text", "text": text}]
        if image is not None:
            content.append({"type": "image", "data": image,
                            "mimeType": "image/png"})
        return {"content": content, "isError": False}

    def _read_resource(self, params: dict[str, Any]) -> dict[str, Any]:
        uri = params.get("uri")
        for resource in RESOURCES:
            if resource["uri"] == uri:
                return {"contents": [{
                    "uri": uri,
                    "mimeType": resource["mimeType"],
                    "text": resource["builder"](self.context.project),
                }]}
        raise ToolError("unknown resource %s; available: %s"
                        % (echo(uri), ", ".join(r["uri"] for r in RESOURCES)))

    def _log(self, text: str) -> None:
        if self.verbose:
            print(text, file=sys.stderr, flush=True)

    # -- loop -----------------------------------------------------------------
    def serve(self, stdin: TextIO, stdout: TextIO) -> None:
        """Read one message per line until the client closes the pipe.

        Note the caller contract: ``stdout`` must be able to encode any text
        the project contains, because responses are written with
        ``ensure_ascii=False``. :mod:`rpgvxace_mcp.__main__` wraps both streams
        as UTF-8 for this reason.
        """
        while True:
            line, oversized = _read_line(stdin)
            if line is None:
                return
            if oversized:
                _write(stdout, _error(
                    None, INVALID_REQUEST,
                    "a message longer than %d characters arrived and was "
                    "discarded unread" % MAX_MESSAGE_CHARS))
                continue
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                _write(stdout, _error(None, PARSE_ERROR,
                                      "invalid JSON: %s" % exc))
                continue
            except RecursionError:
                # Deeply nested JSON. This is the one parse outside a request's
                # error handling, so without this the session would end here.
                _write(stdout, _error(
                    None, PARSE_ERROR,
                    "message nests too deeply to parse"))
                continue
            except ValueError:
                # After JSONDecodeError, which is a subclass: an integer
                # literal of more than 4,300 digits raises a plain ValueError
                # on CPython 3.11, and one such line ended the session.
                _write(stdout, _error(
                    None, PARSE_ERROR,
                    "a number in the message is too long to parse"))
                continue
            # A batch is a list of messages; respond with a list of the answers.
            if isinstance(message, list):
                if len(message) > MAX_BATCH_MESSAGES:
                    # Each entry can ask for a full tool listing, so an
                    # unbounded batch is an unbounded response.
                    _write(stdout, _error(
                        None, INVALID_REQUEST,
                        "a batch of %d messages arrived; this server answers at "
                        "most %d at a time"
                        % (len(message), MAX_BATCH_MESSAGES)))
                    continue
                if not message:
                    _write(stdout, _error(None, INVALID_REQUEST,
                                          "an empty batch has nothing to answer"))
                    continue
                # A non-object member is answered rather than dropped: a client
                # that sent nothing but malformed entries would otherwise wait
                # for a reply that never comes.
                responses = _answer_batch(self, message)
                if responses:
                    _write(stdout, responses)
                continue
            if not isinstance(message, dict):
                _write(stdout, _error(None, INVALID_REQUEST, "message must be an object"))
                continue
            response = self.handle(message)
            if response is not None:
                _write(stdout, response)


def _answer_batch(server: "Server", message: list[Any]) -> list[dict[str, Any]]:
    """Answer every member of a batch, within one frame's worth of room.

    A batch reply is a single frame, so an oversized one used to be replaced by
    one error carrying ``id: null`` -- which left every request in the batch
    unanswered, the exact failure the substitution exists to prevent. Instead
    the budget is spent as the batch is answered, and a member that does not
    fit is answered with its own error under its own id.
    """
    responses: list[dict[str, Any]] = []
    # Two characters for the enclosing brackets, and one comma per response
    # after the first, counted as each is appended. The members' own sizes
    # alone let a batch that summed to exactly the cap pass here and then trip
    # `_write`'s check -- where the payload is a list, so `isinstance(payload,
    # dict)` is false and the substitute carries `id: null`, leaving every
    # member of the batch unanswered. That is the regression this accounting
    # exists to prevent, so it has to count the frame it is measuring.
    spent = 2
    for entry in message:
        if not isinstance(entry, dict):
            answer = _error(None, INVALID_REQUEST,
                            "a batch member must be an object, not %s"
                            % type(entry).__name__)
        else:
            answer = server.handle(entry)
        if answer is None:
            continue        # a notification takes no response
        # Measured with the same function that will write it. The second
        # pass in `_write` is kept deliberately: this loop has to know each
        # answer's size *before* it decides whether the batch still fits, and
        # the substitute error below changes that size -- so the frame the
        # caller finally writes is not always the one measured here. One
        # extra pass over at most a four-megabyte frame, against a rule that
        # must be applied per member.
        size = len(_frame_text(answer))
        if spent + size > MAX_RESPONSE_CHARS:
            answer = _error(
                answer.get("id"), INTERNAL_ERROR,
                "the answers to this batch do not fit in one response. Send "
                "these requests separately, or ask for less in each.")
            size = len(_frame_text(answer))
        # The comma this response needs if it is not the first.
        spent += size + (1 if responses else 0)
        responses.append(answer)
    return responses


def _tool_error(exc: Exception, tool_name: str) -> dict[str, Any]:
    hint = ""
    if not isinstance(exc, (ToolError, ProjectError, ConversionError)):
        # An error type the tool did not raise deliberately. Naming it beats
        # showing the model a bare message with no idea what went wrong.
        hint = ("\n\nThis came back as %s, which usually means an argument was "
                "not the shape the tool expected, or the project file holds "
                "something unusual. Check the arguments, then run validate."
                % type(exc).__name__)
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


def _read_line(stdin: TextIO) -> tuple[str | None, bool]:
    """One line, never holding more than the message limit in memory.

    Iterating the stream directly would materialise a whole line before any cap
    could be consulted, so a client that never sends a newline could exhaust
    memory before being refused. Returns (None, False) at end of input, and
    (partial, True) for a line too long to accept -- the rest of which is read
    and dropped so the next line still parses.
    """
    chunk = stdin.readline(MAX_MESSAGE_CHARS + 1)
    if not chunk:
        return None, False
    if len(chunk) <= MAX_MESSAGE_CHARS or chunk.endswith("\n"):
        return chunk, False
    while True:
        more = stdin.readline(MAX_MESSAGE_CHARS + 1)
        if not more or more.endswith("\n"):
            return chunk, True


def _dump_json(payload: Any) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str)


def _result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _frame_text(payload: Any) -> str:
    """One payload as the exact characters that would go on the wire.

    Here rather than at each `json.dumps` call, because `_answer_batch`
    measured each member with a call of its own that passed neither
    `ensure_ascii=False` nor `default=str` -- so the two spellings of "the
    frame" disagreed, and changing `_write`'s options would have made the
    batch accounting undercount and collapse the whole batch into one
    `id: null` error. That is precisely the regression `_answer_batch`'s own
    docstring says it exists to prevent.
    """
    return json.dumps(payload, ensure_ascii=False, default=str)


def _write(stdout: TextIO, payload: Any) -> None:
    """Write one frame, or an error frame, but never half of one.

    Serialising first means a failure -- a cycle from Marshal object links, a
    recursion limit on a nested id -- becomes a well-formed error instead of a
    truncated frame that desynchronises every response after it. The failing
    payload's own id is carried into the substitute, so a client waiting on that
    request still gets an answer for it rather than none at all.
    """
    try:
        frame = _frame_text(payload)
        if len(frame) > MAX_RESPONSE_CHARS:
            raise ValueError("response is %d characters, above the %d limit"
                             % (len(frame), MAX_RESPONSE_CHARS))
    except (TypeError, ValueError, RecursionError) as exc:
        request_id = payload.get("id") if isinstance(payload, dict) else None
        frame = ""
        try:
            frame = json.dumps(_error(request_id, INTERNAL_ERROR,
                                      "could not serialise the response: %s" % exc))
        except (TypeError, ValueError, RecursionError):
            frame = ""
        if not frame or len(frame) > MAX_RESPONSE_CHARS:
            # The id itself can be the oversized part, so the substitute is
            # measured too rather than assumed small.
            frame = json.dumps(_error(None, INTERNAL_ERROR,
                                      "could not serialise the response"))
    try:
        stdout.write(frame + "\n")
        stdout.flush()
    except OSError:
        # The client closed the pipe. There is nowhere left to report this, and
        # serve()'s next read ends the loop.
        pass
