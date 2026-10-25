import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location(
    "smoke", Path(__file__).resolve().parents[1] / "smoke_test.py")
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


class MCPClientTests(unittest.TestCase):
    def setUp(self):
        self.client = smoke.Client("http://localhost:45127")
        self.client.request = Mock()

    def test_prefers_structured_content(self):
        self.client.request.return_value = {
            "structuredContent": {"key": "api-v2-pack"},
            "content": [{"type": "text", "text": "human readable description"}]}
        self.assertEqual(self.client.tool("new_pack"), {"key": "api-v2-pack"})

    def test_accepts_json_text_fallback(self):
        self.client.request.return_value = {
            "content": [{"type": "text", "text": '{"key":"text-pack"}'}]}
        self.assertEqual(self.client.tool("new_pack"), {"key": "text-pack"})

    def test_tool_errors_are_not_success(self):
        for response in (
            {"isError": True, "content": [{"type": "text", "text": "pack_not_found"}]},
            {"content": [{"type": "text", "text": '{"Error":"schema missing"}'}]},
            {"content": []},
        ):
            with self.subTest(response=response), self.assertRaises(AssertionError):
                self.client.request.return_value = response
                self.client.tool("new_pack")

    def test_waits_for_job_and_unwraps_result(self):
        self.client.request.side_effect = [
            {"structuredContent": {"job": 7, "state": "running"}},
            {"structuredContent": {"job": 7, "state": "queued"}},
            {"structuredContent": {"job": 7, "state": "finished",
                                   "result": {"schema_loaded": True}}},
        ]
        self.assertEqual(self.client.job("set_game", {"game": "warhammer_3"}),
                         {"schema_loaded": True})
        calls = self.client.request.call_args_list
        self.assertEqual(calls[1].args[1]["name"], "wait_for_job")
        self.assertEqual(calls[1].args[1]["arguments"]["job"], 7)

    def test_rejects_failed_or_cancelled_jobs(self):
        for state in ("failed", "cancelled"):
            with self.subTest(state=state), self.assertRaises(AssertionError):
                self.client.request.return_value = {
                    "structuredContent": {"job": 7, "state": state}}
                self.client.job("set_game")

    def test_job_wait_is_bounded(self):
        self.client.request.return_value = {
            "structuredContent": {"job": 7, "state": "running"}}
        with patch.object(smoke.time, "monotonic", side_effect=[0, 181]):
            with self.assertRaisesRegex(AssertionError, "timed out"):
                self.client.job("set_game")
        self.assertEqual(self.client.request.call_count, 1)

    def test_decodes_streamable_http_sse(self):
        result = {"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}
        raw = ("event: message\r\ndata: " + json.dumps(result) + "\r\n\r\n").encode()
        self.assertEqual(smoke.decode_response(raw), result)


class LifecycleTests(unittest.TestCase):
    def test_checks_schema_and_preserves_existing_shared_packs(self):
        client = Mock()
        client.tool.side_effect = [
            {"game": "warhammer_3", "schema_loaded": True, "packs": [{"key": "existing"}]},
            {"tables": {"twad_key_deletes_tables": [1]}},
            {"key": "temporary"},
            {"packs": [{"key": "existing"}, {"key": "temporary"}]},
            {},
            {"packs": [{"key": "existing"}]},
        ]
        smoke.check_pack_lifecycle(client)
        client.job.assert_called_once_with(
            "set_game", {"game": "warhammer_3", "rebuild_dependencies": False})
        self.assertIn(unittest.mock.call("close_pack", {"pack": "temporary"}),
                      client.tool.call_args_list)
        self.assertNotIn(unittest.mock.call("close_all_packs"), client.tool.call_args_list)

    def test_missing_schema_fails_before_pack_creation(self):
        client = Mock()
        client.tool.return_value = {"game": "warhammer_3", "schema_loaded": False}
        with self.assertRaises(AssertionError):
            smoke.check_pack_lifecycle(client)
        client.tool.assert_called_once_with("session_status")


if __name__ == "__main__":
    unittest.main()
