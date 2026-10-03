"""Offline safety checks for full book backup, delete, and rebuild recovery."""
import copy
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from book_backup import (backup_project, capture_project, read_backup,
                         recover_pending_rebuilds, restore_project,
                         write_pending_rebuild)
from db import DB
import task_ledger


try:
    from ai.pipeline import PipelineWorker
except ModuleNotFoundError as exc:
    if exc.name != "PySide6":
        raise
    # This safety test only calls _run_rebuild synchronously, so a tiny Qt
    # signal/thread shim lets the bundled stdlib Python run it offline.
    qtcore = types.ModuleType("PySide6.QtCore")

    class _DummyThread:
        def __init__(self, *args, **kwargs):
            pass

    class _DummySignal:
        def __init__(self, *args, **kwargs):
            pass

        def emit(self, *args, **kwargs):
            pass

    qtcore.QThread = _DummyThread
    qtcore.Signal = lambda *args, **kwargs: _DummySignal()
    pyside = types.ModuleType("PySide6")
    pyside.QtCore = qtcore
    sys.modules["PySide6"] = pyside
    sys.modules["PySide6.QtCore"] = qtcore
    try:
        from ai.pipeline import PipelineWorker
    finally:
        sys.modules.pop("PySide6.QtCore", None)
        sys.modules.pop("PySide6", None)


class ProjectBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.db_path = os.path.join(self.temp.name, "novel.db")
        self.db = DB(self.db_path)
        task_ledger.initialize(self.db)
        self.pid = self.db.create_project(
            "备份测试书", "一句话概念", "玄幻", "男频", "卖点",
            style_sheet="短句，少解释", intro="简介", plan_volumes=2,
            plan_chapters=8)
        self.db.add_setting(self.pid, "人物", "林野", "主角")
        oid = self.db.add_outline(self.pid, "卷纲", "第一卷 入道", "主线", volume=1)
        self.db.add_outline(self.pid, "章纲", "第1章 重生", "目标", volume=1)
        cid = self.db.create_chapter(
            self.pid, 1, 1, "第1章 重生", content="人工定稿正文",
            chapter_card="人物醒来", summary="醒来并立誓", status="定稿",
            outline_id=oid)
        self.db.snapshot(cid, "旧版正文", "人工修订前")
        self.db.add_foreshadow(self.pid, "戒指有秘密", planted_ch="第1章")
        self.db.set_volume_summary(self.pid, 1, "主角醒来")
        self.db.add_chat_msg(self.pid, "user", "保留我的要求")
        self.run_id = task_ledger.create_run(
            self.db, self.pid, "全书重构", "rebuild_book", {"vol": 1})
        task_ledger.record_step(self.db, self.run_id, "outline", "completed", "已生成")
        task_ledger.create_plan(self.db, self.pid, "rebuild_book", {"vol": 1}, "确认重构")

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def _tables(self):
        return capture_project(self.db, self.pid)["tables"]

    def test_atomic_backup_restores_all_book_and_task_rows_exactly(self):
        before = copy.deepcopy(self._tables())
        path = backup_project(self.db, self.pid, reason="rebuild",
                              prefix="backup_rebuild")
        payload = read_backup(path)

        self.db.update_project(self.pid, title="被修改的书", intro="坏简介")
        chapter = self.db.get_chapters(self.pid)[0]
        self.db.update_chapter_meta(chapter["id"], status="AI草稿", summary="坏摘要")
        self.db.clear_chat_msgs(self.pid)
        self.db.set_volume_summary(self.pid, 1, "坏摘要")
        self.db.add_setting(self.pid, "术语", "新设定", "不应残留")

        restored = restore_project(self.db, payload)
        self.assertEqual(restored["projects"], 1)
        self.assertEqual(self._tables(), before)

    def test_failed_exact_restore_rolls_back_without_mixing_rows(self):
        before = copy.deepcopy(self._tables())
        payload = capture_project(self.db, self.pid)
        payload["tables"]["projects"][0]["column_missing_from_schema"] = "bad"
        with self.assertRaisesRegex(Exception, "缺少备份列"):
            restore_project(self.db, payload)
        self.assertEqual(self._tables(), before)

    def test_delete_writes_complete_backup_before_atomic_removal(self):
        task_ledger.update_run(self.db, self.run_id, "completed")
        before = copy.deepcopy(self._tables())
        path = self.db.delete_project(self.pid)
        self.assertTrue(os.path.isfile(path))
        self.assertIsNone(self.db.get_project(self.pid))
        payload = read_backup(path)
        restore_project(self.db, payload)
        self.assertEqual(self._tables(), before)

    def test_delete_refuses_a_book_with_a_running_task(self):
        before = copy.deepcopy(self._tables())
        with self.assertRaisesRegex(RuntimeError, "任务运行"):
            self.db.delete_project(self.pid)
        self.assertEqual(self._tables(), before)
        self.assertFalse(any(n.startswith("backup_delete_")
                             for n in os.listdir(self.temp.name)))

    def test_pending_marker_recovers_a_crashed_rebuild(self):
        before = copy.deepcopy(self._tables())
        backup_path = backup_project(self.db, self.pid, reason="rebuild",
                                     prefix="backup_rebuild")
        marker_path = write_pending_rebuild(self.db, self.pid, backup_path)
        chapter_id = self.db.get_chapters(self.pid)[0]["id"]
        self.db.update_chapter_meta(chapter_id, content="重构中写入的新稿")

        recovered = recover_pending_rebuilds(self.db)
        self.assertEqual(len(recovered), 1)
        self.assertFalse(os.path.exists(marker_path))
        self.assertEqual(self._tables(), before)

    def test_chapter_failure_during_rebuild_restores_the_original_book(self):
        before = copy.deepcopy(self._tables())
        worker = PipelineWorker({}, self.db_path, self.pid, 1,
                                rebuild=True, rebuild_volumes=1,
                                use_tools=False, review=False,
                                distill_style=False)
        worker._reset_stats()
        worker._call = lambda *args, **kwargs: "mock outline response"
        worker._parse_with_repair = lambda text, kind, **kwargs: (
            [("- 第1卷 新卷", {"title": "第1卷 新卷", "content": "新主线"})]
            if kind == "卷纲" else [])
        worker._gen_outline_draft_items = lambda *args, **kwargs: (
            [("- 第1章 新章", {"title": "第1章 新章", "content": "新章目标"})],
            1, 1)

        def fail_one_chapter(db, volume):
            db.create_chapter(self.pid, volume, 1, "第1章 半成品", content="半成品")
            worker._stats["total"] += 1
            worker._stats["failed"] += 1

        worker._write_volume = fail_one_chapter
        with self.assertRaisesRegex(RuntimeError, "章节写作失败"):
            worker._run_rebuild(self.db)
        self.assertEqual(self._tables(), before)
        self.assertFalse(any(n.startswith("rebuild_pending_")
                             for n in os.listdir(self.temp.name)))

    def test_write_until_fills_a_hole_even_when_a_later_chapter_exists(self):
        self.db.add_outline(self.pid, "章纲", "第2章 缺失", "补上", volume=1)
        self.db.add_outline(self.pid, "章纲", "第3章 已有", "继续", volume=1)
        self.db.create_chapter(self.pid, 1, 2, "第2章 空壳", content="")
        self.db.create_chapter(self.pid, 1, 3, "第3章 已有", content="已有正文")
        worker = PipelineWorker({}, self.db_path, self.pid, 1, until_no=3,
                                use_tools=False, review=False,
                                distill_style=False)
        worker._reset_stats()
        worker._ensure_outlines_range = lambda *args, **kwargs: None
        worker._call = lambda *args, **kwargs: '离线摘要'
        calls = []

        def write_volume(db, volume, up_to_no=0):
            calls.append((volume, up_to_no))
            shell = next(c for c in db.get_chapters(self.pid)
                         if c["chapter_no"] == 2)
            db.update_chapter_meta(shell["id"], content="补好的正文")
            worker._stats["total"] += 1
            worker._stats["written"] += 1

        worker._write_volume = write_volume
        worker._run_until(self.db)
        self.assertEqual(calls, [(1, 3)])
        self.assertEqual(
            next(c for c in self.db.get_chapters(self.pid)
                 if c["chapter_no"] == 2)["content"], "补好的正文")

    def test_partial_generated_outline_range_is_rejected_before_insert(self):
        worker = PipelineWorker({}, self.db_path, self.pid, 1,
                                use_tools=False, review=False)
        worker._call = lambda *args, **kwargs: "mock outline response"
        worker._parse_with_repair = lambda *args, **kwargs: [
            ("- 第2章 仅一条", {"title": "第2章 仅一条", "content": "内容"})]
        before = copy.deepcopy([dict(r) for r in self.db.get_outlines(self.pid)])
        with self.assertRaisesRegex(RuntimeError, "未完整覆盖"):
            worker._ensure_outlines_range(
                self.db, self.db.get_project(self.pid), 1, 2, 3)
        self.assertEqual([dict(r) for r in self.db.get_outlines(self.pid)],
                         [dict(r) for r in before])

    def test_ensure_chapter_reuses_an_existing_empty_shell(self):
        outline_id = self.db.add_outline(
            self.pid, "章纲", "第2章 空壳", "补写", volume=1)
        chapter_id = self.db.create_chapter(self.pid, 1, 2, "第2章 空壳", content="")
        worker = PipelineWorker({}, self.db_path, self.pid, 1,
                                use_tools=False, review=False)
        outline = self.db.get_outline(outline_id)
        found_id, chapter = worker._ensure_chapter(
            self.db, outline, 2, "空壳")
        self.assertEqual(found_id, chapter_id)
        self.assertEqual(chapter["outline_id"], outline_id)
        self.assertEqual(len([c for c in self.db.get_chapters(self.pid)
                              if c["chapter_no"] == 2]), 1)


if __name__ == "__main__":
    unittest.main()
