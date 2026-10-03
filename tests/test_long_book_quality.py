"""Synthetic story evidence, cache compatibility, isolation, and bounded cost."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai import bookqa, book_index, context, story_memory
from ai.pipeline import PipelineWorker
from db import DB
from book_backup import capture_project, restore_project


class LongBookQualityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DB(str(Path(self.tmp.name) / 'synthetic.db'))
        self.pid = self.db.create_project('长篇质量演练')
        self.other = self.db.create_project('另一部小说')

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def chapter(self, number, content='', **kwargs):
        return self.db.create_chapter(self.pid, 1, number, f'第{number}章', content=content, **kwargs)

    def test_middle_passage_is_recalled_and_cited_without_loading_the_whole_book(self):
        cid = self.chapter(37, '开头的日常。' * 900 + '月蚀铜钥只能打开北门。' + '结尾的日常。' * 900)
        self.db.create_chapter(self.other, 1, 37, '外部作品', content='月蚀铜钥打开南门。')
        with patch.object(self.db, 'get_chapters', side_effect=AssertionError('full book load')):
            evidence = bookqa.gather_evidence(self.db, self.pid, '月蚀铜钥能打开哪扇门？')
        self.assertEqual(evidence['search_backend'], 'indexed')
        self.assertEqual(evidence['chapters'][0]['no'], 37)
        block = bookqa._evidence_block(evidence)
        self.assertIn('月蚀铜钥只能打开北门', block)
        self.assertNotIn('南门', block)
        self.assertIn('正文命中片段', block)
        self.assertLessEqual(len(block), bookqa.QA_EVIDENCE_BUDGET)

    def test_index_tracks_edits_deletions_and_precise_restore(self):
        cid = self.chapter(1, '月蚀铜钥只能打开北门。')
        before = capture_project(self.db, self.pid)
        bookqa.gather_evidence(self.db, self.pid, '月蚀铜钥')
        self.db.save_chapter(cid, '星河银铃只能打开东门。')
        old = bookqa.gather_evidence(self.db, self.pid, '月蚀铜钥')
        self.assertEqual(old['chapters'], [])
        self.assertTrue(bookqa.gather_evidence(self.db, self.pid, '星河银铃')['chapters'])
        self.db.delete_chapter(cid)
        self.assertEqual(bookqa.gather_evidence(self.db, self.pid, '星河银铃')['chapters'], [])
        restore_project(self.db, before)
        restored = bookqa.gather_evidence(self.db, self.pid, '月蚀铜钥')
        self.assertEqual(restored['chapters'][0]['no'], 1)
        self.db.conn.execute("INSERT INTO chapter_search(chapter_search,rank) VALUES('integrity-check',1)")
        self.db.conn.commit()

    def test_fallback_search_still_finds_body_only_facts(self):
        self.chapter(7, '月蚀铜钥打开北门。')
        with patch.object(book_index, 'ensure_index', return_value=False):
            evidence = bookqa.gather_evidence(self.db, self.pid, '月蚀铜钥')
        self.assertEqual(evidence['search_backend'], 'fallback')
        self.assertEqual(evidence['chapters'][0]['no'], 7)

    def test_short_chinese_name_uses_fallback_when_index_has_no_match(self):
        self.chapter(2, '林野取走铜钥。')
        evidence = bookqa.gather_evidence(self.db, self.pid, '林野')
        self.assertEqual(evidence['chapters'][0]['no'], 2)

    def test_empty_search_answers_locally_without_a_model_call(self):
        evidence = bookqa.gather_evidence(self.db, self.pid, '不存在的银月王冠')
        with patch.object(bookqa.aiclient, 'simple_chat', side_effect=AssertionError('paid call')):
            self.assertIn('没有找到', bookqa.answer({}, evidence, '银月王冠在哪里？'))

    def test_source_snapshot_stays_pure_and_prompt_is_bounded(self):
        self.chapter(1, '月蚀铜钥' * 20000, summary='设定摘要' * 5000)
        evidence = bookqa.gather_evidence(self.db, self.pid, '第1章的月蚀铜钥')
        json.dumps(evidence, ensure_ascii=False)
        self.assertLessEqual(len(bookqa._evidence_block(evidence)), 6000)
        self.assertIn('正文命中片段', bookqa._evidence_block(evidence))
        self.assertNotIn('conn', evidence)

    def test_explicit_early_chapter_question_does_not_include_later_reveal(self):
        self.chapter(1, '月蚀铜钥暂时没有主人。')
        self.chapter(20, '月蚀铜钥后来归青禾所有。')
        evidence = bookqa.gather_evidence(self.db, self.pid, '第1章的月蚀铜钥是谁的？')
        self.assertNotIn('青禾', bookqa._evidence_block(evidence))
        self.assertTrue(all(chapter['no'] <= 1 for chapter in evidence['chapters']))

    def test_incremental_fingerprint_is_byte_identical_to_legacy_cache(self):
        for number in range(1, 5):
            self.chapter(number, f'正文「{number}」\n第二段', summary=f'摘要{number}')
        rows = self.db.get_chapters(self.pid)
        values = [[row[key] for key in ('id','volume','chapter_no','title','content','summary')]
                  for row in rows]
        legacy = hashlib.sha256(json.dumps(values, ensure_ascii=False).encode('utf-8')).hexdigest()
        self.assertEqual(context.memory_fingerprint(iter(rows)), legacy)

    def test_context_reads_metadata_and_nearby_tails_without_future_plot(self):
        self.chapter(1, '前文第一章正文', summary='旧摘要')
        target = self.chapter(2, chapter_card='推进主线')
        self.chapter(3, '未来主角死亡', summary='未来结局不能泄露')
        with patch.object(self.db, 'get_chapters', side_effect=AssertionError('full book load')):
            packed = context.context_pack_for(self.db, self.db.get_chapter(target))
            order = story_memory.chapter_order(self.db, self.pid)
        self.assertIn('前文第一章正文', packed)
        self.assertNotIn('未来', packed)
        self.assertEqual(order[target], 1)

    def test_legacy_prefix_cache_is_reused_without_another_summary_call(self):
        first = self.chapter(1, '旧正文', summary='旧摘要')
        self.db.update_chapter_meta(first, prefix_hash=context.memory_fingerprint(self.db.get_chapters(self.pid)),
                                    prefix_summary='已验证的旧卷记忆')
        worker = PipelineWorker({}, self.db.path, self.pid, 1, review=False,
                                use_tools=False, distill_style=False)
        with patch.object(worker, '_call', side_effect=AssertionError('unnecessary model call')):
            worker._finish_chapter(self.db, first, '第一章')
        target = self.chapter(2, chapter_card='继续剧情')
        with patch.object(self.db, 'get_chapters', side_effect=AssertionError('full book load')):
            packed = context.context_pack_for(self.db, self.db.get_chapter(target))
        self.assertIn('已验证的旧卷记忆', packed)

    def test_earlier_edit_invalidates_cached_memory_without_future_leak(self):
        first = self.chapter(1, '原正文', summary='原摘要')
        self.db.update_chapter_meta(first, prefix_hash=context.memory_fingerprint(self.db.get_chapters(self.pid)),
                                    prefix_summary='过期记忆')
        self.db.conn.execute('UPDATE chapters SET content=?,summary=? WHERE id=?',
                             ('已改正文', '修订摘要', first))
        self.db.conn.commit()
        target = self.chapter(2, chapter_card='继续')
        packed = context.context_pack_for(self.db, self.db.get_chapter(target))
        self.assertIn('修订摘要', packed)
        self.assertNotIn('过期记忆', packed)


if __name__ == '__main__':
    unittest.main()
