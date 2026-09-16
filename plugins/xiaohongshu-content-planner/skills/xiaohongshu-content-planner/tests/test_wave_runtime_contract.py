"""Contract with the sibling Wave executable; never call image generation APIs."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from test_image_request_guard import SOURCE_A, guard, request


WAVE = Path(__file__).resolve().parents[4] / "wave-image"


@unittest.skipUnless(sys.platform == "win32" and WAVE.is_dir(),
                     "Windows x64 marketplace with sibling Wave plugin required")
class WaveRuntimeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config = json.loads((WAVE / ".mcp.json").read_text(encoding="utf-8"))
        spec = next(iter(config["mcpServers"].values()))
        command = spec["command"].replace("${CODEBUDDY_PLUGIN_ROOT}", str(WAVE))
        allowed = {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "USERPROFILE", "LOCALAPPDATA"}
        environment = {k: v for k, v in os.environ.items() if k.upper() in allowed}
        environment["PATH"] = str(Path(os.environ["SYSTEMROOT"]) / "System32")
        environment["WAVEEEE_API_KEY"] = ""
        environment["WAVEEEE_OUTPUT_DIR"] = spec["env"]["WAVEEEE_OUTPUT_DIR"]
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "server_info", "arguments": {}}},
        ]
        completed = subprocess.run(
            [command, *spec.get("args", [])],
            input="".join(json.dumps(m) + "\n" for m in messages),
            capture_output=True, encoding="utf-8", env=environment,
            timeout=30, check=True,
        )
        responses = {item["id"]: item["result"] for line in completed.stdout.splitlines()
                     if (item := json.loads(line)).get("id") is not None}
        cls.info = responses[3]["structuredContent"]
        cls.schemas = {tool["name"]: tool["inputSchema"] for tool in responses[2]["tools"]}

    def test_real_runtime_reports_expected_version_without_key(self):
        self.assertEqual(self.info["version"], "0.4.2")
        self.assertFalse(self.info["api_key_configured"])
        self.assertEqual(self.info["default_response_format"], "auto")

    def test_every_advertised_response_format_passes_guard_unchanged(self):
        for tool in sorted(guard.TOOLS):
            schema = self.schemas[tool]
            properties = schema["properties"]
            task_properties = (properties["tasks"]["items"]["properties"]
                               if "batch" in tool else properties)
            formats = task_properties["response_format"]["enum"]
            self.assertEqual(set(formats), {"auto", "url", "b64_json"})
            self.assertEqual(task_properties["response_format"]["default"], "auto")
            for response_format in [None, *formats]:
                with self.subTest(tool=tool, response_format=response_format):
                    document = request(tool)
                    task = (document["arguments"]["tasks"][0] if "batch" in tool
                            else document["arguments"])
                    if tool.startswith("edit"):
                        task["image_paths"] = [str(SOURCE_A.resolve())]
                    if response_format is not None:
                        task["response_format"] = response_format
                    original = copy.deepcopy(document)
                    result = guard.preflight(document)
                    self.assertTrue(result["ok"])
                    self.assertEqual(document, original)
                    self.assertEqual(result["mcp_call"]["arguments"], document["arguments"])
                    self.assertFalse(set(document["arguments"]) - set(properties))
                    if "batch" in tool:
                        self.assertFalse(set(task) - set(task_properties))


if __name__ == "__main__":
    unittest.main()
