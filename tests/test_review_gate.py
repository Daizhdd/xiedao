# -*- coding: utf-8 -*-
"""审稿质量门槛：未审稿/低分/通过与审稿通过明确区分，未审的重写稿不直接采用。"""
import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai import fallback, prompts  # noqa: E402
from ai import pipeline as pl  # noqa: E402
from db import DB  # noqa: E402


FAKE_CFG = {"name": "fake", "provider": "openai_compat",
            "base_url": "https://example.invalid/v1", "api_key": "t",
            "model": "fake-model"}


class ReviewGateTests(unittest.TestCase):
    def setUp(self):
        fallback.reset()
        self._tmp = tempfile.TemporaryDirectory()
        self.db = DB(str(Path(self._tmp.name) / "g.db"))
        self.pid = self.db.create_project("门槛书", "概念", "玄幻", "男频", "爽")
        self.db.add_outline(self.pid, "章纲", "第1章 开局", "主角登场", volume=1)
        self.scores = []          # 每次审稿依次弹出的 (score, issues)
        self.writer_texts = []    # 每次 SYSTEM_WRITER 调用依次返回的文本
        self.review_broken = False
        self.legacy_review = False

    def tearDown(self):
        fallback.reset()
        self.db.close()
        self._tmp.cleanup()

    def _fake_chat(self, cfg, messages, **kw):
        sys = messages[0]["content"]
        if sys == prompts.SYSTEM_REVIEWER:
            if self.review_broken:
                raise RuntimeError("审稿链路故障")
            if not self.scores:
                raise AssertionError("审稿调用次数超出预期")
            score, issues = self.scores.pop(0)
            if self.legacy_review:
                return json.dumps({'score': score, 'issues': issues}, ensure_ascii=False)
            body = messages[1]['content'].rsplit('【正文】\n', 1)[1].split(
                '\n\n请审稿', 1)[0]
            return json.dumps({
                'score': score,
                'categories': {category: score for category in pl.evidence_review.CATEGORIES},
                'issues': [
                    {'kind': '节奏', 'severity': '一般', 'body_quote': body,
                     'source_ref': '', 'source_quote': '',
                     'explanation': issue, 'suggestion': '调整这一段的节奏'}
                    for issue in issues]}, ensure_ascii=False)
        if sys == prompts.SYSTEM_WRITER:
            if not self.writer_texts:
                raise AssertionError("写作调用次数超出预期")
            return self.writer_texts.pop(0)
        if sys == prompts.SYSTEM_SUMMARY:
            return "本章摘要：门槛测试。"
        if sys == prompts.SYSTEM_ROLLUP:
            return "本卷至今：门槛测试摘要。"
        raise AssertionError(f"未预期的调用：{sys[:20]}")

    def _run_chapter(self, review=True):
        w = pl.PipelineWorker(FAKE_CFG, self.db.path, self.pid, 1,
                              use_tools=False, review=review, min_score=75)
        logs = []
        w.progress.connect(logs.append)
        w.finished_ok.connect(logs.append)
        with patch.object(pl.aiclient, "chat_once", side_effect=self._fake_chat):
            w.run()
        ch = [c for c in self.db.get_chapters(self.pid)
              if c["chapter_no"] == 1][-1]
        return ch, logs

    def test_passing_review_keeps_plain_ai_draft_status(self):
        self.writer_texts = ["初稿正文" * 300]
        self.scores = [(90, [])]
        ch, _logs = self._run_chapter()
        self.assertEqual(ch["status"], "AI草稿")

    def test_failed_review_marks_unreviewed_not_passed(self):
        self.writer_texts = ["初稿正文" * 300]
        self.review_broken = True
        ch, logs = self._run_chapter()
        self.assertEqual(ch["status"], "AI草稿·未审")
        self.assertEqual(ch["content"], "初稿正文" * 300)  # 正文照常入库
        self.assertTrue(any("未完成审稿" in s for s in logs))

    def test_old_score_only_review_is_unreviewed(self):
        self.writer_texts = ["初稿正文" * 300]
        self.scores = [(95, [])]
        self.legacy_review = True
        ch, logs = self._run_chapter()
        self.assertEqual(ch['status'], 'AI草稿·未审')
        self.assertTrue(any('缺少可核查证据' in s for s in logs))

    def test_review_disabled_marks_unreviewed(self):
        self.writer_texts = ["初稿正文" * 300]
        ch, _logs = self._run_chapter(review=False)
        self.assertEqual(ch["status"], "AI草稿·未审")

    def test_low_score_rewrite_not_crossing_threshold(self):
        # 复审与初稿同分：按既有语义采用重写稿，但两次都低于门槛 → 低分
        self.writer_texts = ["初稿正文" * 300, "重写稿正文" * 300]
        self.scores = [(58, ["承接断裂"]), (58, ["承接断裂"])]
        ch, logs = self._run_chapter()
        self.assertEqual(ch["status"], "AI草稿·低分")
        self.assertEqual(ch["content"], "重写稿正文" * 300)
        self.assertTrue(any("低分" in s or "未过" in s for s in logs))

    def test_unreviewed_rewrite_is_not_adopted_sight_unseen(self):
        self.writer_texts = ["初稿正文" * 300, "重写稿正文" * 300]
        self.scores = [(58, ["承接断裂"])]        # 复审直接失败
        self.review_broken_after_first = True

        def _fake(cfg, messages, **kw):
            if messages[0]["content"] == prompts.SYSTEM_REVIEWER:
                if self.writer_texts:      # 初稿还有待写 → 这是首次审稿
                    pass
                else:                      # 重写稿的复审：链路故障
                    raise RuntimeError("复审故障")
            return self._fake_chat(cfg, messages, **kw)

        w = pl.PipelineWorker(FAKE_CFG, self.db.path, self.pid, 1,
                              use_tools=False, review=True, min_score=75)
        logs = []
        w.progress.connect(logs.append)
        with patch.object(pl.aiclient, "chat_once", side_effect=_fake):
            w.run()
        ch = [c for c in self.db.get_chapters(self.pid)
              if c["chapter_no"] == 1][-1]
        self.assertEqual(ch["content"], "初稿正文" * 300,
                         "复审失败时不得采用未审的重写稿")
        self.assertEqual(ch["status"], "AI草稿·低分")
        self.assertTrue(any("复审未完成" in s for s in logs))

    def test_better_rewrite_is_adopted_and_passes(self):
        self.writer_texts = ["初稿正文" * 300, "重写稿正文" * 300]
        self.scores = [(58, ["承接断裂"]), (88, [])]
        ch, logs = self._run_chapter()
        self.assertEqual(ch["content"], "重写稿正文" * 300)
        self.assertEqual(ch["status"], "AI草稿")
        self.assertTrue(any("采用重写稿" in s for s in logs))

    def test_rewrite_still_below_threshold_is_marked_low(self):
        self.writer_texts = ["初稿正文" * 300, "重写稿正文" * 300]
        self.scores = [(58, ["承接断裂"]), (70, [])]
        ch, _logs = self._run_chapter()
        self.assertEqual(ch["content"], "重写稿正文" * 300)   # 70 >= 58 采用
        self.assertEqual(ch["status"], "AI草稿·低分")          # 但仍未过门槛

    def test_summary_reports_review_breakdown(self):
        self.writer_texts = ["初稿正文" * 300]
        self.review_broken = True
        _ch, logs = self._run_chapter()
        self.assertTrue(any("未完成审稿 1" in s for s in logs))


if __name__ == "__main__":
    unittest.main()
