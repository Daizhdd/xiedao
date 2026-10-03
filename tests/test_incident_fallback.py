"""Regression tests for the September 27 empty-body chapter failure."""
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai import client, fallback, prompts  # noqa: E402
from ai.pipeline import PipelineWorker, Stopped  # noqa: E402


DEFAULT = {"name": "mimo", "provider": "openai_compat",
           "base_url": "https://example.invalid/v1", "api_key": "test",
           "model": "mimo-test"}
BACKUP = dict(DEFAULT, name="backup", model="backup-test")


class IncidentFallbackTests(unittest.TestCase):
    def setUp(self):
        fallback.reset()

    def tearDown(self):
        fallback.reset()

    def test_empty_body_at_token_cap_has_specific_error(self):
        payload = {"choices": [{"message": {"content": ""},
                                "finish_reason": "length"}],
                   "usage": {"completion_tokens": 8192}}
        with patch.object(client, "_post", return_value=payload), \
                patch.object(client, "_log_api"):
            with self.assertRaises(client.OutputBudgetExhausted):
                client.chat_once(DEFAULT, [], max_tokens=8192)

    def test_first_failed_writing_step_uses_backup_model(self):
        fallback.set_configs([DEFAULT, BACKUP])
        worker = PipelineWorker(DEFAULT, "unused.db", 1, 1)
        attempted = []

        def fake_chat(cfg, _messages, **_kwargs):
            attempted.append(cfg["name"])
            if cfg["name"] == "mimo":
                raise client.OutputBudgetExhausted("8192 token 耗尽，无正文")
            return "备用模型生成的正文"

        with patch.object(client, "chat_once", side_effect=fake_chat), \
                patch.object(client, "_log_api"):
            result = worker._call(prompts.SYSTEM_WRITER, "第十章")
        self.assertEqual(result, "备用模型生成的正文")
        self.assertEqual(attempted, ["mimo", "backup"])

    def test_long_timeout_switches_without_repeating_default_model(self):
        fallback.set_configs([DEFAULT, BACKUP])
        worker = PipelineWorker(DEFAULT, "unused.db", 1, 1)
        attempted = []

        def fake_chat(cfg, _messages, **_kwargs):
            attempted.append(cfg["name"])
            if cfg["name"] == "mimo":
                raise TimeoutError("180 秒总时限")
            return "备用模型生成的正文"

        with patch.object(client, "chat_once", side_effect=fake_chat), \
                patch.object(client, "_log_api"):
            result = worker._call(prompts.SYSTEM_WRITER, "第十章")
        self.assertEqual(result, "备用模型生成的正文")
        self.assertEqual(attempted, ["mimo", "backup"])

    def test_direct_mimo_writer_gets_larger_body_budget_without_thinking(self):
        mimo = dict(DEFAULT, base_url="https://api.xiaomimimo.com/v1",
                    model="mimo-v2.6-flash")
        worker = PipelineWorker(mimo, "unused.db", 1, 1)
        calls = []

        def fake_chat(_cfg, _messages, **kwargs):
            calls.append(kwargs)
            return "正文"

        with patch.object(client, "chat_once", side_effect=fake_chat):
            self.assertEqual(worker._call_chain(
                mimo, prompts.SYSTEM_WRITER, "第十章", 0.8, 0), "正文")
        self.assertEqual(calls[0]["max_tokens"], 16384)
        self.assertEqual(calls[0]["thinking"], "disabled")

    def test_stop_interrupts_wait_even_if_network_worker_is_stuck(self):
        worker = PipelineWorker(DEFAULT, "unused.db", 1, 1)
        entered = threading.Event()
        release = threading.Event()
        outcome = []

        def stuck_chat(*_args, **_kwargs):
            entered.set()
            release.wait(2)
            return "late"

        def call():
            try:
                worker._call_chain(DEFAULT, "system", "user", 0.8, 0)
            except Stopped:
                outcome.append("stopped")

        with patch.object(client, "chat_once", side_effect=stuck_chat):
            thread = threading.Thread(target=call)
            thread.start()
            try:
                self.assertTrue(entered.wait(1))
                start = time.monotonic()
                worker.stop()
                thread.join(0.5)
                self.assertLess(time.monotonic() - start, 0.5)
                self.assertEqual(outcome, ["stopped"])
            finally:
                release.set()
                thread.join(1)


if __name__ == "__main__":
    unittest.main()
