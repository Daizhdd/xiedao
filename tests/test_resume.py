# -*- coding: utf-8 -*-
"""通用断点续跑：台账步骤读取、worker 步骤级跳过、续跑参数校验。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import task_ledger as ledger  # noqa: E402
from ai import fallback, prompts  # noqa: E402
from ai import pipeline as pl  # noqa: E402
from db import DB  # noqa: E402


FAKE_CFG = {"name": "fake", "provider": "openai_compat",
            "base_url": "https://example.invalid/v1", "api_key": "t",
            "model": "fake-model"}


def fake_chat_factory(writer_texts, scores):
    """返回 fake_chat(cfg, messages)：writer 依次出 writer_texts，
    reviewer 依次出 scores，摘要/汇总固定文案。"""
    state = {"w": 0, "s": 0}

    def fake_chat(_cfg, messages, **kw):
        sys = messages[0]["content"]
        if sys == prompts.SYSTEM_WRITER:
            i = state["w"]
            state["w"] += 1
            return writer_texts[i] if i < len(writer_texts) else "兜底正文" * 300
        if sys == prompts.SYSTEM_REVIEWER:
            i = state["s"]
            state["s"] += 1
            if i < len(scores):
                return '{"score": %d, "issues": []}' % scores[i]
            return '{"score": 90, "issues": []}'
        if sys == prompts.SYSTEM_SUMMARY:
            return "本章摘要：续跑测试。"
        if sys == prompts.SYSTEM_ROLLUP:
            return "本卷至今：续跑测试摘要。"
        raise AssertionError(f"未预期的调用：{sys[:20]}")

    return fake_chat


class ResumeLedgerTests(unittest.TestCase):
    def setUp(self):
        fallback.reset()
        self._tmp = tempfile.TemporaryDirectory()
        self.db = DB(str(Path(self._tmp.name) / "r.db"))
        ledger.initialize(self.db)

    def tearDown(self):
        fallback.reset()
        self.db.close()
        self._tmp.cleanup()

    def test_completed_chapter_keys_filters_and_scopes(self):
        run_a = ledger.create_run(self.db, 1, "任务A", "write_volume", {})
        run_b = ledger.create_run(self.db, 1, "任务B", "write_volume", {})
        ledger.record_step(self.db, run_a, "chapter:11", "completed", "第一章")
        ledger.record_step(self.db, run_a, "chapter:12", "failed", "第二章")
        ledger.record_step(self.db, run_b, "chapter:11", "completed", "第一章")
        ledger.record_step(self.db, run_a, "outline:9", "completed", "卷纲")
        self.assertEqual(ledger.completed_chapter_keys(self.db, run_a),
                         {"chapter:11"})
        self.assertEqual(ledger.completed_chapter_keys(self.db, run_b),
                         {"chapter:11"})
        self.assertEqual(ledger.completed_chapter_keys(self.db, 0), set())
        self.assertEqual(ledger.completed_chapter_keys(self.db, None), set())

    def test_worker_skips_chapters_completed_in_parent_run(self):
        """父任务已完成（含只留空壳的异常章）→ 续跑按台账跳过，只写缺的章。"""
        pid = self.db.create_project("续跑书", "概念", "玄幻", "男频", "爽")
        for i in (1, 2, 3):
            self.db.add_outline(pid, "章纲", f"第{i}章 测试{i}", f"要点{i}",
                                volume=1)
        o1, o2, o3 = [o for o in self.db.get_outlines(pid)
                      if o["level"] == "章纲"]
        ch1 = self.db.create_chapter(pid, 1, 1, "第1章 测试1", content="已写" * 400,
                                     outline_id=o1["id"])
        # ch2：上次任务留下空壳但步骤已标记完成（异常现场）
        ch2 = self.db.create_chapter(pid, 1, 2, "第2章 测试2", content="",
                                     outline_id=o2["id"])
        run = ledger.create_run(self.db, pid, "写第1卷", "write_volume",
                                {"vol": 1})
        ledger.record_step(self.db, run, f"chapter:{ch1}", "completed", "第1章")
        ledger.record_step(self.db, run, f"chapter:{ch2}", "completed", "第2章")
        ledger.update_run(self.db, run, "interrupted")

        w = pl.PipelineWorker(FAKE_CFG, self.db.path, pid, 1,
                              use_tools=False, review=True,
                              resume_run_id=run)
        logs = []
        w.progress.connect(logs.append)
        with patch.object(pl.aiclient, "chat_once",
                          side_effect=fake_chat_factory(["新正文" * 300], [])):
            w.run()
        chapters = {c["chapter_no"]: c for c in self.db.get_chapters(pid)}
        self.assertEqual(chapters[1]["content"], "已写" * 400)   # 有正文，未动
        self.assertEqual(chapters[2]["content"], "")             # 台账完成→跳过
        self.assertEqual(chapters[3]["content"], "新正文" * 300)  # 缺章补写
        self.assertTrue(any("断点续跑" in s for s in logs))
        self.assertTrue(any("上次任务已完成本章" in s for s in logs))

    def test_worker_without_resume_keeps_rewrite_semantics(self):
        """不带 resume_run_id 的重写不受台账影响（旧行为不变）。"""
        pid = self.db.create_project("重写书", "概念", "玄幻", "男频", "爽")
        self.db.add_outline(pid, "章纲", "第1章 测试1", "要点1", volume=1)
        o1 = [o for o in self.db.get_outlines(pid) if o["level"] == "章纲"][0]
        ch1 = self.db.create_chapter(pid, 1, 1, "第1章 测试1", content="旧稿" * 400,
                                     chapter_card="- 本章目标：测试",
                                     outline_id=o1["id"])
        run = ledger.create_run(self.db, pid, "重写", "rewrite", {})
        ledger.record_step(self.db, run, f"chapter:{ch1}", "completed", "第1章")
        w = pl.PipelineWorker(FAKE_CFG, self.db.path, pid, 1,
                              use_tools=False, review=True,
                              force_rewrite=True)
        with patch.object(pl.aiclient, "chat_once",
                          side_effect=fake_chat_factory(["重写新稿" * 300], [])):
            w.run()
        self.assertEqual(self.db.get_chapter(ch1)["content"], "重写新稿" * 300)

    def test_worker_spec_persists_rewrite_instruction(self):
        pid = self.db.create_project("规格书", "概念", "玄幻", "男频", "爽")
        w = pl.PipelineWorker(FAKE_CFG, self.db.path, pid, 2,
                              force_rewrite=True, rewrite_instruction="节奏快点")
        mode, args = ledger.worker_spec(w)
        self.assertEqual(mode, "rewrite")
        self.assertEqual(args["rewrite_instruction"], "节奏快点")
        w2 = pl.PipelineWorker(FAKE_CFG, self.db.path, pid, 2,
                               force_rewrite=True, whole_book=True,
                               rewrite_instruction=" 符合书名 ")
        mode2, args2 = ledger.worker_spec(w2)
        self.assertEqual(mode2, "rewrite_book")
        self.assertEqual(args2["rewrite_instruction"], "符合书名")

    def test_rewrite_resume_skips_done_and_keeps_instruction(self):
        """重写任务断点续跑：父任务已重写完的章按台账跳过，
        未完成章带原要求续写（要求前后一致）。"""
        pid = self.db.create_project("重写续跑书", "概念", "玄幻", "男频", "爽")
        self.db.add_outline(pid, "章纲", "第1章 起步", "要点1", volume=1)
        self.db.add_outline(pid, "章纲", "第2章 转折", "要点2", volume=1)
        outlines = [o for o in self.db.get_outlines(pid) if o["level"] == "章纲"]
        ch1 = self.db.create_chapter(pid, 1, 1, "第1章 起步", content="旧稿一" * 300,
                                     chapter_card="- 本章目标：起步",
                                     outline_id=outlines[0]["id"])
        ch2 = self.db.create_chapter(pid, 1, 2, "第2章 转折", content="旧稿二" * 300,
                                     chapter_card="- 本章目标：转折",
                                     outline_id=outlines[1]["id"])
        run = ledger.create_run(self.db, pid, "重写第1卷", "rewrite",
                                {"vol": 1, "force_rewrite": True,
                                 "only_chapter_no": 0,
                                 "rewrite_instruction": "节奏快点"})
        ledger.record_step(self.db, run, f"chapter:{ch1}", "completed", "第1章")
        ledger.update_run(self.db, run, "interrupted")

        seen_instructions = []
        base_fake = fake_chat_factory(["新稿一" * 300, "新稿二" * 300], [])

        def fake_chat(cfg, messages, **kw):
            if messages[0]["content"] == prompts.SYSTEM_WRITER:
                seen_instructions.append(
                    "【用户修改要求" in messages[1]["content"]
                    and "节奏快点" in messages[1]["content"])
            return base_fake(cfg, messages, **kw)

        w = pl.PipelineWorker(FAKE_CFG, self.db.path, pid, 1,
                              force_rewrite=True, rewrite_instruction="节奏快点",
                              use_tools=False, review=True, resume_run_id=run)
        logs = []
        w.progress.connect(logs.append)
        with patch.object(pl.aiclient, "chat_once", side_effect=fake_chat):
            w.run()
        chapters = {c["chapter_no"]: c for c in self.db.get_chapters(pid)}
        self.assertEqual(chapters[1]["content"], "旧稿一" * 300,
                         "台账已完成的重写章必须跳过")
        # 章 1 被跳过 → 章 2 是本次唯一的写作调用，取第一份文本
        self.assertEqual(chapters[2]["content"], "新稿一" * 300)
        self.assertEqual(seen_instructions, [True],
                         "续写必须带上存档的原始要求")
        self.assertTrue(any("断点续跑" in s for s in logs))

    def test_mark_resumed_hides_from_interrupted_list(self):
        run = ledger.create_run(self.db, 3, "任务", "rewrite", {})
        ledger.update_run(self.db, run, "interrupted")
        ledger.mark_resumed(self.db, run)
        self.assertEqual(ledger.get_run(self.db, run)["status"], "resumed")
        self.assertEqual(ledger.list_interrupted(self.db, 3), [])

    def test_mark_interrupted_on_reopen(self):
        run = ledger.create_run(self.db, 7, "遗留任务", "write_volume", {})
        # initialize 已把 running 标为 interrupted；再建一个 running 验证
        run2 = ledger.create_run(self.db, 7, "新任务", "write_volume", {})
        self.assertEqual(ledger.get_run(self.db, run2)["status"], "running")
        ledger.mark_interrupted(self.db)
        self.assertEqual(ledger.get_run(self.db, run2)["status"], "interrupted")
        rows = ledger.list_interrupted(self.db, 7)
        self.assertEqual({r["id"] for r in rows}, {run, run2})


if __name__ == "__main__":
    unittest.main()
