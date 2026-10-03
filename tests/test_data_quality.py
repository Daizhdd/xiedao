# -*- coding: utf-8 -*-
"""数据质量工具：大纲标题合法性、重复章号检测与安全合并。"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai import parse as aparse  # noqa: E402
from ai.pipeline import PipelineWorker  # noqa: E402
from db import DB  # noqa: E402


class SaneTitleTests(unittest.TestCase):
    def test_chinese_titles_pass(self):
        for t in ("第10章 死局初现的真相", "第3卷 风起", "第1章",
                  "番外：第312次回档", "第10章 AI觉醒"):
            self.assertTrue(aparse.sane_outline_title(t), t)

    def test_junk_titles_rejected(self):
        for t in ("第10章 by ___", "第4章 ...", "第2章 TODO", "",
                  None, "   "):
            self.assertFalse(aparse.sane_outline_title(t), repr(t))

    def test_volume_titles_checked_against_juan_prefix(self):
        self.assertTrue(aparse.sane_outline_title("第1卷 源起"))
        self.assertFalse(aparse.sane_outline_title("第1卷 END?"))

    def test_pipeline_items_sane_rejects_junk_title(self):
        good = [(None, {"title": "第1章 开局", "content": "要点"})]
        junk = [(None, {"title": "第10章 by ___", "content": "要点"})]
        no_title_no_content = [(None, {"title": "随便行", "content": ""})]
        self.assertTrue(PipelineWorker._items_sane(good, "章纲"))
        self.assertFalse(PipelineWorker._items_sane(junk, "章纲"))
        self.assertFalse(PipelineWorker._items_sane(junk, "卷纲"))
        self.assertFalse(PipelineWorker._items_sane(no_title_no_content, "章纲"))


class DuplicateChapterTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = DB(str(Path(self._tmp.name) / "d.db"))
        self.pid = self.db.create_project("重号书", "概念", "玄幻", "男频", "爽")

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def _add(self, vol, no, title, content="", summary="", card="",
             outline_id=0):
        cid = self.db.create_chapter(self.pid, vol, no, title, content=content,
                                     summary=summary, chapter_card=card,
                                     outline_id=outline_id)
        if content:
            self.db.save_chapter(cid, content + "（改）", note="手动保存")
        return cid

    def test_find_duplicate_groups(self):
        self._add(4, 31, "裂痕初显A", content="A" * 10)
        self._add(4, 31, "裂痕初显B", content="B" * 10)
        self._add(4, 33, "推演A")
        self._add(4, 34, "不重复")
        dupes = self.db.find_duplicate_chapters(self.pid)
        self.assertEqual([(v, n) for v, n, _ in dupes], [(4, 31)])
        self.assertEqual(len(dupes[0][2]), 2)

    def test_merge_snapshots_everything_without_mixing_drafts(self):
        oid = self.db.add_outline(self.pid, "章纲", "第31章 裂痕初显", "要点")
        a = self._add(4, 31, "裂痕初显A", content="A稿", summary="",
                      card="", outline_id=oid)
        b = self._add(4, 31, "裂痕初显B", content="B稿v1", summary="B摘要",
                      card="B章节卡")
        self.db.save_chapter(b, "B稿v2", note="第二次保存")
        actions = self.db.merge_duplicate_chapters(a, [b])
        kept = self.db.get_chapter(a)
        self.assertIsNone(self.db.get_chapter(b))
        versions = self.db.get_versions(a)
        snaps = [v["snapshot"] for v in versions]
        # b 的两版正文 + 最新正文全部快照进 a 的版本史
        self.assertIn("B稿v1", snaps)
        self.assertIn("B稿v2", snaps)
        self.assertEqual(kept["summary"], "")
        self.assertEqual(kept["chapter_card"], "")
        self.assertEqual(kept["outline_id"], oid)
        self.assertEqual(kept["content"], "A稿（改）")   # 保留章正文不动
        self.assertTrue(any("版本" in x for x in actions))
        # 保留章自身的历史版本仍在
        self.assertTrue(any("A稿" in s for s in snaps))

    def test_merge_without_content_drop_still_cleans(self):
        a = self._add(1, 5, "第五章", content="有正文")
        b = self._add(1, 5, "第五章重复")
        actions = self.db.merge_duplicate_chapters(a, [b])
        self.assertIsNone(self.db.get_chapter(b))
        self.assertTrue(any("删除" in x for x in actions))
        self.assertEqual(len(self.db.find_duplicate_chapters(self.pid)), 0)

    def test_merge_missing_keep_raises(self):
        a = self._add(1, 5, "第五章")
        b = self._add(1, 5, "第五章重复")
        with self.assertRaises(RuntimeError):
            self.db.merge_duplicate_chapters(999, [b])
        self.assertIsNotNone(self.db.get_chapter(b))   # 未被误删


if __name__ == "__main__":
    unittest.main()
