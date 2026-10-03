# -*- coding: utf-8 -*-
"""写作偏好（ai.prefs）：app_settings 存取、参数解析、流水线与审稿的贯通。"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai import client, prompts, prefs, review  # noqa: E402
from ai.pipeline import PipelineWorker  # noqa: E402
from db import DB  # noqa: E402


MIMO = {"name": "mimo", "provider": "openai_compat",
        "base_url": "https://api.xiaomimimo.com/v1", "api_key": "test",
        "model": "mimo-v2.6-flash"}


class PrefsStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = DB(str(Path(self._tmp.name) / "t.db"))

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def test_setting_roundtrip_and_missing_default(self):
        self.assertEqual(self.db.get_setting("nope"), "")
        self.assertEqual(self.db.get_setting("nope", "fallback"), "fallback")
        self.db.set_setting(prefs.KEY_BUDGET, "32768")
        self.db.set_setting(prefs.KEY_THINKING, "always_off")
        self.assertEqual(self.db.get_setting(prefs.KEY_BUDGET), "32768")
        self.assertEqual(self.db.get_setting(prefs.KEY_THINKING), "always_off")
        self.db.set_setting(prefs.KEY_BUDGET, "8192")   # 覆盖写
        self.assertEqual(self.db.get_setting(prefs.KEY_BUDGET), "8192")

    def test_load_defaults_when_unset(self):
        self.assertEqual(prefs.load(self.db.path), prefs.DEFAULT_PREFS)
        self.assertIsNone(prefs.load(self.db.path)["budget"])
        self.assertEqual(prefs.load(self.db.path)["thinking"], "auto")

    def test_load_missing_file_falls_back_to_defaults(self):
        self.assertEqual(prefs.load(""), prefs.DEFAULT_PREFS)
        self.assertEqual(prefs.load(r"Z:\不存在\novel.db"), prefs.DEFAULT_PREFS)

    def test_load_parses_and_sanitizes_values(self):
        self.db.set_setting(prefs.KEY_BUDGET, "24576")
        self.db.set_setting(prefs.KEY_THINKING, "always_off")
        loaded = prefs.load(self.db.path)
        self.assertEqual(loaded, {"budget": 24576, "thinking": "always_off"})

        for bad in ("abc", "999999", "512", "-1"):
            self.db.set_setting(prefs.KEY_BUDGET, bad)
            self.assertIsNone(prefs.load(self.db.path)["budget"], bad)
        self.db.set_setting(prefs.KEY_BUDGET, "65536")
        self.assertEqual(prefs.load(self.db.path)["budget"], 65536)

        self.db.set_setting(prefs.KEY_THINKING, "bogus")
        self.assertEqual(prefs.load(self.db.path)["thinking"], "auto")

    def test_resolution_helpers(self):
        self.assertEqual(prefs.writer_budget({"budget": None}), 16384)
        self.assertEqual(prefs.writer_budget({"budget": 8192}), 8192)
        self.assertEqual(prefs.writer_budget({}), 16384)

        auto = {"thinking": "auto"}
        self.assertEqual(prefs.thinking_for(auto, "writer"), "disabled")
        self.assertEqual(prefs.thinking_for(auto, "summary"), "disabled")
        self.assertEqual(prefs.thinking_for(auto, "rollup"), "disabled")
        self.assertEqual(prefs.thinking_for(auto, "reviewer"), "disabled")
        self.assertIsNone(prefs.thinking_for(auto, "other"))
        self.assertIsNone(prefs.thinking_for({"thinking": "always_on"}, "writer"))
        self.assertEqual(
            prefs.thinking_for({"thinking": "always_off"}, "other"), "disabled")
        # 异常兜底：空偏好等价自动
        self.assertIsNone(prefs.thinking_for(None, "other"))


class PrefsWiringTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = DB(str(Path(self._tmp.name) / "w.db"))

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def test_pipeline_uses_custom_budget_and_thinking_for_mimo_writer(self):
        self.db.set_setting(prefs.KEY_BUDGET, "32768")
        self.db.set_setting(prefs.KEY_THINKING, "always_off")
        worker = PipelineWorker(MIMO, self.db.path, 1, 1)
        worker._prefs = prefs.load(self.db.path)
        calls = []

        def fake_chat(_cfg, _messages, **kwargs):
            calls.append(kwargs)
            return "正文"

        with patch.object(client, "chat_once", side_effect=fake_chat):
            worker._call_chain(MIMO, prompts.SYSTEM_WRITER, "u", 0.8, 0)
        self.assertEqual(calls[0]["max_tokens"], 32768)
        self.assertEqual(calls[0]["thinking"], "disabled")

    def test_pipeline_non_mimo_still_uses_max_tokens(self):
        self.db.set_setting(prefs.KEY_BUDGET, "32768")
        worker = PipelineWorker(MIMO, self.db.path, 1, 1)
        worker._prefs = prefs.load(self.db.path)
        other = dict(MIMO, base_url="https://api.deepseek.com/v1",
                     model="deepseek-chat")
        calls = []

        def fake_chat(_cfg, _messages, **kwargs):
            calls.append(kwargs)
            return "正文"

        with patch.object(client, "chat_once", side_effect=fake_chat):
            worker._call_chain(other, prompts.SYSTEM_WRITER, "u", 0.8, 0)
        self.assertEqual(calls[0]["max_tokens"], 8192)
        self.assertEqual(calls[0]["thinking"], "disabled")   # 仅为兼容字段留位
        # 客户端层保证：非 MiMo 不携带 MiMo 私有参数
        self.assertNotIn("max_completion_tokens",
                         client._generation_options(other, 8192, "disabled"))

    def test_pipeline_thinking_always_on_keeps_model_default(self):
        self.db.set_setting(prefs.KEY_THINKING, "always_on")
        worker = PipelineWorker(MIMO, self.db.path, 1, 1)
        worker._prefs = prefs.load(self.db.path)
        calls = []

        def fake_chat(_cfg, _messages, **kwargs):
            calls.append(kwargs)
            return "正文"

        with patch.object(client, "chat_once", side_effect=fake_chat):
            worker._call_chain(MIMO, prompts.SYSTEM_WRITER, "u", 0.8, 0)
        self.assertIsNone(calls[0]["thinking"])
        self.assertEqual(calls[0]["max_tokens"], 16384)   # 默认预算

    def test_review_receives_thinking_from_prefs(self):
        self.db.set_setting(prefs.KEY_THINKING, "always_on")
        worker = PipelineWorker(MIMO, self.db.path, 1, 1)
        worker._prefs = prefs.load(self.db.path)
        seen = []

        def fake_review(_cfg, _prompt, _text, log=None, thinking="disabled"):
            seen.append(thinking)
            return 88, []

        with patch.object(review, "review_chapter", side_effect=fake_review):
            score, issues = worker._safe_review("user", "正文")
        self.assertEqual((score, issues), (88, []))
        self.assertEqual(seen, [None])

        # 默认（auto）：审稿关思考
        worker2 = PipelineWorker(MIMO, self.db.path, 1, 1)
        with patch.object(review, "review_chapter", side_effect=fake_review):
            worker2._safe_review("user", "正文")
        self.assertEqual(seen[-1], "disabled")

    def test_review_chapter_passes_thinking_to_client(self):
        captured = []

        def fake_chat(_cfg, messages, **kwargs):
            captured.append(kwargs)
            return '{"score": 90, "issues": []}'

        with patch.object(client, "chat_once", side_effect=fake_chat):
            review.review_chapter(MIMO, "p", "t")
            review.review_chapter(MIMO, "p", "t", thinking=None)
        self.assertEqual(captured[0]["thinking"], "disabled")
        self.assertIsNone(captured[1]["thinking"])


if __name__ == "__main__":
    unittest.main()
