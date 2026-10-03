# -*- coding: utf-8 -*-
"""离线覆盖模型客户端的空响应、时限和降级计数。"""
import json
import sqlite3
import threading
import time
import unittest
from unittest.mock import patch

from ai import client, fallback


CFG = {
    "name": "offline",
    "provider": "openai_compat",
    "base_url": "https://example.invalid/v1",
    "api_key": "offline-key",
    "model": "offline-model",
}


class _JSONResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


class _StreamResponse:
    def __init__(self, lines):
        self.lines = [line.encode("utf-8") for line in lines]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __iter__(self):
        return iter(self.lines)


class ClientReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.logs = patch.object(client, "_log_api")
        self.logs.start()
        self.resolver = patch.object(client, "_resolve", return_value="127.0.0.1")
        self.resolver.start()

    def tearDown(self):
        patch.stopall()

    def test_chat_once_accepts_text_and_logs_usage(self):
        payload = {
            "choices": [{"message": {"content": "  hello  "}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
        }
        with patch("ai.client.urllib.request.urlopen", return_value=_JSONResponse(payload)):
            self.assertEqual(client.chat_once(CFG, [{"role": "user", "content": "hi"}]), "hello")
        self.assertTrue(any("usage prompt=7, completion=3, total=10" in c.args[0]
                            for c in client._log_api.call_args_list))

    def test_direct_mimo_uses_documented_budget_and_thinking_fields(self):
        mimo = dict(CFG, base_url="https://api.xiaomimimo.com/v1",
                    model="mimo-v2.6-flash")
        sent = []
        payload = {"choices": [{"message": {"content": "正文"}}],
                   "usage": {"completion_tokens": 120,
                             "completion_tokens_details": {"reasoning_tokens": 20}}}

        def fake_post(_cfg, body, _timeout):
            sent.append(body)
            return payload

        with patch.object(client, "_post", side_effect=fake_post):
            self.assertEqual(client.chat_once(
                mimo, [], max_tokens=16384, thinking="disabled"), "正文")
            client.chat_once_tools(mimo, [], [], max_tokens=2048,
                                   thinking="disabled")
        self.assertEqual([body["max_completion_tokens"] for body in sent],
                         [16384, 2048])
        self.assertTrue(all(body["thinking"] == {"type": "disabled"}
                            for body in sent))
        self.assertTrue(all("max_tokens" not in body for body in sent))
        self.assertIn("reasoning=20", client._usage_summary(payload))
        self.assertIn("visible=100", client._usage_summary(payload))

        self.assertEqual(client._generation_options(CFG, 16384, "disabled"),
                         {"max_tokens": 16384})

    def test_chat_once_rejects_empty_or_whitespace_content(self):
        for content in (None, "", " \n "):
            with self.subTest(content=content):
                payload = {"choices": [{"message": {"content": content}}]}
                with patch("ai.client.urllib.request.urlopen", return_value=_JSONResponse(payload)):
                    with self.assertRaisesRegex(RuntimeError, "空正文"):
                        client.chat_once(CFG, [{"role": "user", "content": "hi"}])

    def test_tools_allow_empty_content_only_when_tool_calls_exist(self):
        tool_call = {"id": "call-1", "type": "function", "function": {
            "name": "lookup", "arguments": "{}"}}
        payload = {"choices": [{"message": {"content": None, "tool_calls": [tool_call]}}]}
        with patch("ai.client.urllib.request.urlopen", return_value=_JSONResponse(payload)):
            result = client.chat_once_tools(CFG, [], [])
        self.assertEqual(result, {"content": "", "tool_calls": [tool_call]})

        payload = {"choices": [{"message": {"content": "  ", "tool_calls": []}}]}
        with patch("ai.client.urllib.request.urlopen", return_value=_JSONResponse(payload)):
            with self.assertRaisesRegex(RuntimeError, "空正文"):
                client.chat_once_tools(CFG, [], [])

    def test_sqlite_row_configuration_is_supported(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE config (name, provider, base_url, api_key, model)")
        conn.execute("INSERT INTO config VALUES (?, ?, ?, ?, ?)", tuple(CFG.values()))
        row = conn.execute("SELECT * FROM config").fetchone()
        payload = {"choices": [{"message": {"content": "ok"}}]}
        try:
            with patch("ai.client.urllib.request.urlopen", return_value=_JSONResponse(payload)):
                self.assertEqual(client.chat_once(row, []), "ok")
        finally:
            conn.close()

    def test_streaming_logs_usage_and_rejects_empty_result(self):
        lines = [
            'data: {"choices":[{"delta":{"content":"hello"}}]}\n',
            'data: {"choices":[],"usage":{"prompt_tokens":2,"completion_tokens":1,"total_tokens":3}}\n',
            "data: [DONE]\n",
        ]
        with patch("ai.client.urllib.request.urlopen", return_value=_StreamResponse(lines)):
            self.assertEqual(client.chat_stream(CFG, []), "hello")
        self.assertTrue(any("usage prompt=2, completion=1, total=3" in c.args[0]
                            for c in client._log_api.call_args_list))

        with patch("ai.client.urllib.request.urlopen", return_value=_StreamResponse(["data: [DONE]\n"])):
            with self.assertRaisesRegex(RuntimeError, "空正文"):
                client.chat_stream(CFG, [])

    def test_total_deadline_and_cancellation_return_without_waiting(self):
        release = threading.Event()
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            client._run_with_deadline(lambda: release.wait(1), 0.05)
        self.assertLess(time.monotonic() - started, 0.5)
        release.set()

        cancelled = threading.Event()
        release.clear()
        timer = threading.Timer(0.03, cancelled.set)
        timer.start()
        try:
            with self.assertRaises(client.CallCancelled):
                client._run_with_deadline(lambda: release.wait(1), 1, cancelled)
        finally:
            release.set()
            timer.join()

        cancelled = threading.Event()
        release.clear()
        timer = threading.Timer(0.03, cancelled.set)
        timer.start()
        payload = {"choices": [{"message": {"content": "late"}}]}

        def slow_response(*_args, **_kwargs):
            release.wait(1)
            return _JSONResponse(payload)

        try:
            with patch("ai.client.urllib.request.urlopen", side_effect=slow_response):
                with self.assertRaises(client.CallCancelled):
                    client.chat_once(CFG, [], timeout=2, cancel_event=cancelled)
        finally:
            release.set()
            timer.join()

    def test_backup_success_does_not_clear_default_failure_streak(self):
        default = dict(CFG, name="default", model="model-a")
        backup = dict(CFG, name="backup", model="model-b")
        fallback.reset()
        fallback.set_configs([default, backup])
        attempts = {"model-a": 0, "model-b": 0}

        def run(cfg):
            attempts[cfg["model"]] += 1
            if cfg["model"] == "model-a":
                raise RuntimeError("default offline")
            return "backup result"

        def cancel(_cfg):
            raise client.CallCancelled()

        with patch.object(client, "_log_api"):
            with self.assertRaisesRegex(RuntimeError, "default offline"):
                fallback.guard("kind", default, run)
            self.assertEqual(fallback._streaks["kind"], 1)
            self.assertEqual(fallback.guard("kind", default, run), "backup result")
            self.assertEqual(fallback._streaks["kind"], 2)
            self.assertEqual(fallback.guard("kind", default, run), "backup result")
            self.assertEqual(fallback._streaks["kind"], 3)
            with self.assertRaises(client.CallCancelled):
                fallback.guard("kind", default, cancel)
            self.assertEqual(fallback._streaks["kind"], 3)
        self.assertEqual(attempts, {"model-a": 3, "model-b": 2})

        fallback.guard("kind", default, lambda _cfg: "default recovered")
        self.assertEqual(fallback._streaks["kind"], 0)
        fallback.reset()

    def test_switching_default_model_resets_its_failure_streak(self):
        first = dict(CFG, name="first", model="model-a")
        second = dict(CFG, name="second", model="model-b")
        fallback.reset()

        def fail(_cfg):
            raise RuntimeError("offline")

        with self.assertRaises(RuntimeError):
            fallback.guard("draft", first, fail)
        self.assertEqual(fallback._streaks["draft"], 1)
        self.assertEqual(fallback.guard("draft", second, lambda _cfg: "ok"), "ok")
        self.assertEqual(fallback._streaks["draft"], 0)
        fallback.reset()


if __name__ == "__main__":
    unittest.main()
