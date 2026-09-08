"""MCP protocol tests, driving the server as a real subprocess over stdio."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE / "fixtures"))

from build_project import build  # noqa: E402

PROTOCOL_VERSION = "2025-06-18"


class ServerProcess:
    """A running rpgvxace-mcp server, spoken to in JSON-RPC over pipes."""

    def __init__(self, project: Path, extra_args: list[str] | None = None):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(HERE.parent / "src")
        env["PYTHONIOENCODING"] = "utf-8"
        self.process = subprocess.Popen(
            [sys.executable, "-m", "rpgvxace_mcp", str(project)] + (extra_args or []),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, text=True, encoding="utf-8", bufsize=1,
        )
        self._next_id = 0

    def request(self, method: str, params: dict | None = None) -> dict:
        self._next_id += 1
        message = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            message["params"] = params
        return self._round_trip(message)

    def notify(self, method: str, params: dict | None = None) -> None:
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._send(json.dumps(message))

    def send_raw(self, text: str) -> dict:
        self._send(text)
        return json.loads(self._readline())

    def _round_trip(self, message: dict) -> dict:
        self._send(json.dumps(message))
        return json.loads(self._readline())

    def _send(self, text: str) -> None:
        self.process.stdin.write(text + "\n")
        self.process.stdin.flush()

    def _readline(self) -> str:
        line = self.process.stdout.readline()
        if not line:
            raise AssertionError("server closed stdout; stderr:\n%s"
                                 % self.process.stderr.read())
        return line

    def close(self) -> tuple[int, str]:
        self.process.stdin.close()
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover
            self.process.kill()
        stderr = self.process.stderr.read()
        self.process.stdout.close()
        self.process.stderr.close()
        return self.process.returncode, stderr


class ProtocolTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rpgvxace-proto-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.project = self.tmp / "project"
        build(self.project)
        self.server = ServerProcess(self.project)
        self.addCleanup(self._shutdown)
        self.initialize()

    def _shutdown(self):
        try:
            self.server.close()
        except (OSError, ValueError):
            pass

    def initialize(self) -> dict:
        response = self.server.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0"},
        })
        self.server.notify("notifications/initialized")
        return response

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        return self.server.request("tools/call",
                                   {"name": name, "arguments": arguments or {}})

    def tool_payload(self, name: str, arguments: dict | None = None) -> dict:
        response = self.call_tool(name, arguments)
        result = response["result"]
        self.assertFalse(result.get("isError"), result)
        return json.loads(result["content"][0]["text"])


class TestHandshake(ProtocolTest):
    def test_initialize_reports_capabilities_and_instructions(self):
        result = self.initialize()["result"]
        self.assertEqual(result["protocolVersion"], PROTOCOL_VERSION)
        self.assertIn("tools", result["capabilities"])
        self.assertIn("resources", result["capabilities"])
        self.assertEqual(result["serverInfo"]["name"], "rpgvxace-mcp")
        self.assertIn("LAST page", result["instructions"])
        self.assertIn(str(self.project), result["instructions"])

    def test_older_protocol_version_is_honoured(self):
        response = self.server.request("initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {}})
        self.assertEqual(response["result"]["protocolVersion"], "2024-11-05")

    def test_unknown_protocol_version_falls_back_to_ours(self):
        response = self.server.request("initialize", {
            "protocolVersion": "1999-01-01", "capabilities": {}})
        self.assertEqual(response["result"]["protocolVersion"], PROTOCOL_VERSION)

    def test_ping(self):
        self.assertEqual(self.server.request("ping")["result"], {})

    def test_notifications_get_no_response(self):
        # A notification must not produce output, so the next request's reply is
        # the very next line on stdout.
        self.server.notify("notifications/cancelled", {"requestId": 99})
        self.assertEqual(self.server.request("ping")["result"], {})


class TestToolsOverProtocol(ProtocolTest):
    def test_list_advertises_every_tool_with_a_schema(self):
        tools = self.server.request("tools/list")["result"]["tools"]
        names = {t["name"] for t in tools}
        self.assertEqual(len(tools), 16)
        self.assertIn("project_info", names)
        self.assertIn("event_edit", names)
        for tool in tools:
            self.assertEqual(tool["inputSchema"]["type"], "object")
            self.assertTrue(tool["description"])
            self.assertIn("readOnlyHint", tool["annotations"])
        read_only = {t["name"]: t["annotations"]["readOnlyHint"] for t in tools}
        self.assertTrue(read_only["db_get"])
        self.assertFalse(read_only["db_edit"])

    def test_call_returns_json_text_content(self):
        info = self.tool_payload("project_info")
        self.assertEqual(info["engine"], "vx_ace")
        self.assertEqual(info["entry_counts"]["actors"], 2)

    def test_round_trip_edit_through_the_protocol(self):
        self.tool_payload("db_edit", {"table": "items", "ops": [
            {"op": "update", "id": 1, "patch": {"name": "Tonic", "price": 30}}]})
        listing = self.tool_payload("db_list", {"table": "items"})
        self.assertEqual(listing["entries"][0]["name"], "Tonic")

    def test_authoring_an_event_through_the_protocol(self):
        created = self.tool_payload("event_edit", {"map_id": 1, "ops": [{
            "op": "create", "x": 10, "y": 10, "name": "Guard",
            "commands": [
                {"text": "Halt! Who goes there?"},
                {"choices": ["A friend", "Nobody"],
                 "branches": [[{"switch": 2, "value": "on"}], [{"text": "Hmph."}]]},
            ],
        }]})
        event_id = created["applied"][0]["event_id"]
        events = self.tool_payload("event_get", {"map_id": 1, "event_ids": [event_id]})
        script = events["events"][0]["pages"][0]["script"]
        self.assertIn("Show Choices: A friend / Nobody", "\n".join(script))

    def test_tool_error_is_reported_in_the_result(self):
        response = self.call_tool("db_get", {"table": "actors", "id": 999})
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(payload["missing_ids"], [999])

        response = self.call_tool("db_list", {"table": "nope"})
        result = response["result"]
        self.assertTrue(result["isError"])
        self.assertIn("unknown table", result["content"][0]["text"])

    def test_stale_file_error_tells_the_model_what_to_do(self):
        self.tool_payload("db_list", {"table": "items"})
        # Stand in for the RPG Maker editor saving the project.
        path = self.project / "Data" / "Items.rvdata2"
        path.write_bytes(path.read_bytes() + b"\x00")
        result = self.call_tool("db_edit", {"table": "items", "ops": [
            {"op": "update", "id": 1, "patch": {"price": 1}}]})["result"]
        self.assertTrue(result["isError"])
        text = result["content"][0]["text"]
        # The advice names the engine that is actually open.
        self.assertIn("the rpg maker vx ace editor rewrites", text.lower())
        self.assertIn("close the rpg maker editor", text.lower())
        self.assertIn("refresh", text)

    def test_unknown_tool_lists_the_available_ones(self):
        response = self.call_tool("make_game")
        self.assertEqual(response["error"]["code"], -32602)
        self.assertIn("project_info", response["error"]["message"])


class TestResources(ProtocolTest):
    def test_list(self):
        resources = self.server.request("resources/list")["result"]["resources"]
        uris = {r["uri"] for r in resources}
        self.assertEqual(uris, {
            "rpgvxace://reference/data-format",
            "rpgvxace://reference/event-commands",
            "rpgvxace://guide/workflow",
        })
        for resource in resources:
            self.assertEqual(resource["mimeType"], "text/markdown")
            self.assertTrue(resource["description"])

    def test_read_command_reference(self):
        result = self.server.request(
            "resources/read", {"uri": "rpgvxace://reference/event-commands"})["result"]
        text = result["contents"][0]["text"]
        self.assertIn("| 121 | Control Switches |", text)
        self.assertIn("Move route commands", text)
        # VX Ace additions belong in an Ace project's reference.
        self.assertIn("| 285 | Get Location Info |", text)

    def test_read_data_format(self):
        result = self.server.request(
            "resources/read", {"uri": "rpgvxace://reference/data-format"})["result"]
        text = result["contents"][0]["text"]
        self.assertIn("RPG Maker VX Ace data format", text)
        self.assertIn("RPG::Event::Page", text)
        self.assertIn("RPG::Tileset", text)
        self.assertIn("`Tilesets.rvdata2`", text)
        self.assertIn("region layer", text)
        self.assertIn("width x height x 4", text)


class TestResourcesForVX(unittest.TestCase):
    """The documents are generated for the engine that is actually open."""

    def setUp(self):
        tmp = Path(tempfile.mkdtemp(prefix="rpgvxace-vxdocs-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        build(tmp / "project", "vx")
        self.server = ServerProcess(tmp / "project")
        self.addCleanup(self.server.close)
        self.server.request("initialize", {"protocolVersion": PROTOCOL_VERSION,
                                           "capabilities": {}})
        self.server.notify("notifications/initialized")

    def read(self, uri: str) -> str:
        return self.server.request(
            "resources/read", {"uri": uri})["result"]["contents"][0]["text"]

    def test_data_format_describes_vx(self):
        text = self.read("rpgvxace://reference/data-format")
        self.assertIn("RPG Maker VX data format", text)
        self.assertIn("no `Tilesets` file", text)
        self.assertIn("Areas.rvdata", text)
        self.assertIn("width x height x 3", text)
        self.assertNotIn("RPG::Tileset\n", text)

    def test_command_reference_omits_ace_additions(self):
        text = self.read("rpgvxace://reference/event-commands")
        self.assertIn("| 136 | Change Encounter |", text)
        self.assertNotIn("| 285 |", text)
        self.assertNotIn("| 137 |", text)

    def test_read_unknown_resource(self):
        response = self.server.request("resources/read", {"uri": "rpgvxace://nope"})
        self.assertEqual(response["error"]["code"], -32602)


class TestTransport(ProtocolTest):
    def test_invalid_json_is_reported_and_the_session_survives(self):
        response = self.server.send_raw("{not json")
        self.assertEqual(response["error"]["code"], -32700)
        self.assertEqual(self.server.request("ping")["result"], {})

    def test_unknown_method(self):
        response = self.server.request("dance")
        self.assertEqual(response["error"]["code"], -32601)

    def test_batches_are_answered_as_a_batch(self):
        payload = json.dumps([
            {"jsonrpc": "2.0", "id": 101, "method": "ping"},
            {"jsonrpc": "2.0", "id": 102, "method": "ping"},
        ])
        responses = self.server.send_raw(payload)
        self.assertEqual([r["id"] for r in responses], [101, 102])

    def test_stdout_carries_only_protocol_messages(self):
        self.server.request("tools/list")
        code, stderr = self.server.close()
        self.assertEqual(code, 0)
        # The startup banner belongs on stderr, never on the protocol stream.
        self.assertIn("rpgvxace-mcp", stderr)

    def test_unicode_survives_the_pipe(self):
        self.tool_payload("db_edit", {"table": "items", "ops": [
            {"op": "update", "id": 1, "patch": {"name": "Café Crème"}}]})
        listing = self.tool_payload("db_list", {"table": "items"})
        self.assertEqual(listing["entries"][0]["name"], "Café Crème")


class TestReadOnlyServer(unittest.TestCase):
    def test_read_only_flag_refuses_writes(self):
        tmp = Path(tempfile.mkdtemp(prefix="rpgvxace-ro-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        build(tmp / "project")
        server = ServerProcess(tmp / "project", ["--read-only"])
        self.addCleanup(server.close)
        server.request("initialize", {"protocolVersion": PROTOCOL_VERSION,
                                      "capabilities": {}})
        server.notify("notifications/initialized")
        result = server.request("tools/call", {
            "name": "db_edit",
            "arguments": {"table": "items",
                          "ops": [{"op": "update", "id": 1, "patch": {"price": 1}}]},
        })["result"]
        self.assertTrue(result["isError"])
        self.assertIn("--read-only", result["content"][0]["text"])


class TestStartupFailures(unittest.TestCase):
    def run_server(self, *args: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(HERE.parent / "src")
        return subprocess.run(
            [sys.executable, "-m", "rpgvxace_mcp", *args],
            capture_output=True, text=True, env=env, timeout=60, input="",
        )

    def test_missing_project_argument(self):
        result = self.run_server()
        self.assertEqual(result.returncode, 2)
        self.assertIn("pass the project folder", result.stderr)

    def test_nonexistent_directory(self):
        result = self.run_server(str(HERE / "no-such-project"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("no such directory", result.stderr)


if __name__ == "__main__":
    unittest.main()
