"""Regression checks use isolated databases and fake model calls only."""
import tempfile
import unittest
from pathlib import Path

from db import DB
import task_ledger as ledger
from ai import prompts
from ai.context import context_pack_for
from ai.pipeline import PipelineWorker


class RecoveryMemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DB(str(Path(self.tmp.name) / 'book.db'))
        ledger.initialize(self.db)
        self.pid = self.db.create_project('回归测试')
        self.calls = []

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def chapter(self, no, content='', summary=''):
        return self.db.create_chapter(self.pid, 1, no, f'章节{no}',
                                      content=content, summary=summary,
                                      chapter_card='推进情节')

    def worker(self, **kwargs):
        w = PipelineWorker({}, self.db.path, self.pid, 1, review=False,
                           use_tools=False, distill_style=False,
                           min_chars=1, **kwargs)
        w._reset_stats()
        def call(system, user, **kw):
            self.calls.append(system)
            if system == prompts.SYSTEM_WRITER:
                return '新正文'
            if system == prompts.SYSTEM_SUMMARY:
                return '新摘要'
            return '汇总：' + user
        w._call = call
        return w

    def test_second_resume_inherits_grandparent_completion(self):
        cid = self.chapter(1, '已完成重写')
        a = ledger.create_run(self.db, self.pid, 'A', 'rewrite', {})
        ledger.record_step(self.db, a, f'chapter:{cid}', 'completed')
        b = ledger.create_run(self.db, self.pid, 'B', 'rewrite', {}, a)
        w = self.worker(force_rewrite=True, resume_run_id=b)
        w._resume_steps = ledger.completed_chapter_keys(self.db, b)
        self.assertEqual(w._do_chapter(self.db, None, 1, '章节1', cid), '跳过')
        self.assertEqual(self.db.get_chapter(cid)['content'], '已完成重写')

    def test_resume_chain_stops_at_other_book_and_cycles(self):
        other = ledger.create_run(self.db, self.pid + 1, 'other', 'rewrite', {})
        ledger.record_step(self.db, other, 'chapter:999', 'completed')
        a = ledger.create_run(self.db, self.pid, 'A', 'rewrite', {}, other)
        self.assertEqual(ledger.completed_chapter_keys(self.db, a), set())
        self.db.conn.execute('UPDATE task_runs SET parent_run_id=id WHERE id=?', (a,))
        self.db.conn.commit()
        self.assertEqual(ledger.completed_chapter_keys(self.db, a), set())

    def test_summary_failure_recovers_without_rewriting(self):
        cid = self.chapter(1)
        w = self.worker()
        original = w._call
        def fail(system, *args, **kw):
            if system == prompts.SYSTEM_SUMMARY:
                raise RuntimeError('summary failed')
            return original(system, *args, **kw)
        w._call = fail
        with self.assertRaises(RuntimeError):
            w._do_chapter(self.db, None, 1, '章节1', cid)
        self.db.close()
        self.db = DB(str(Path(self.tmp.name) / 'book.db'))
        resumed = self.worker()
        resumed._do_chapter(self.db, None, 1, '章节1', cid)
        self.assertEqual(self.calls.count(prompts.SYSTEM_WRITER), 1)
        self.assertEqual(self.db.get_chapter(cid)['summary'], '新摘要')
        self.assertTrue(self.db.get_volume_summary(self.pid, 1))

    def test_rollup_failure_preserves_summary_on_retry(self):
        cid = self.chapter(1)
        w = self.worker()
        original = w._call
        def fail(system, *args, **kw):
            if system == prompts.SYSTEM_ROLLUP:
                raise RuntimeError('rollup failed')
            return original(system, *args, **kw)
        w._call = fail
        with self.assertRaises(RuntimeError):
            w._do_chapter(self.db, None, 1, '章节1', cid)
        self.worker()._do_chapter(self.db, None, 1, '章节1', cid)
        self.assertEqual(self.calls.count(prompts.SYSTEM_WRITER), 1)
        self.assertEqual(self.calls.count(prompts.SYSTEM_SUMMARY), 1)
        self.assertTrue(self.db.get_volume_summary(self.pid, 1))

    def test_past_chapter_never_uses_future_volume_summary(self):
        cid = self.chapter(1, '第一章正文', '第一章摘要')
        self.chapter(10, '未来剧情', '未来结局主角死亡')
        self.db.set_volume_summary(self.pid, 1, '未来结局主角死亡')
        self.assertNotIn('未来结局主角死亡', context_pack_for(self.db, self.db.get_chapter(cid)))

    def test_edit_and_restore_invalidate_derived_memory(self):
        cid = self.chapter(1, '旧正文', '旧摘要')
        self.db.set_volume_summary(self.pid, 1, '旧卷摘要')
        self.db.save_chapter(cid, '新正文')
        self.assertEqual(self.db.get_chapter(cid)['summary'], '')
        self.assertEqual(self.db.get_volume_summary(self.pid, 1), '')
        self.db.update_chapter_meta(cid, summary='新摘要')
        self.db.restore_version(cid, self.db.get_versions(cid)[0]['id'])
        self.assertEqual(self.db.get_chapter(cid)['summary'], '')

    def test_merge_different_drafts_does_not_copy_metadata(self):
        keep = self.chapter(1, '保留的剧情')
        drop = self.chapter(1, '另一版剧情', '另一版摘要')
        self.db.update_chapter_meta(keep, chapter_card='')
        oid = self.db.add_outline(self.pid, '章纲', '另一版章纲', '另一版目标')
        self.db.update_chapter_meta(drop, outline_id=oid, chapter_card='另一版卡')
        self.db.merge_duplicate_chapters(keep, [drop])
        row = self.db.get_chapter(keep)
        self.assertEqual(row['summary'], '')
        self.assertEqual(row['chapter_card'], '')
        self.assertEqual(row['outline_id'], 0)
        self.assertIn('另一版剧情', [v['snapshot'] for v in self.db.get_versions(keep)])

    def test_force_rewrite_resume_finishes_saved_draft(self):
        cid = self.chapter(1, '旧正文')
        run = ledger.create_run(self.db, self.pid, '重写', 'rewrite', {})
        w = self.worker(force_rewrite=True)
        w.task_run_id = run
        original = w._call
        def fail(system, *args, **kw):
            if system == prompts.SYSTEM_SUMMARY:
                raise RuntimeError('summary failed')
            return original(system, *args, **kw)
        w._call = fail
        with self.assertRaises(RuntimeError):
            w._do_chapter(self.db, None, 1, '章节1', cid)
        self.worker(force_rewrite=True, resume_run_id=run)._do_chapter(
            self.db, None, 1, '章节1', cid)
        self.assertEqual(self.calls.count(prompts.SYSTEM_WRITER), 1)
        self.assertEqual(self.db.get_chapter(cid)['memory_pending'], 0)
        self.assertEqual(self.db.get_chapter(cid)['summary'], '新摘要')

    def test_new_rewrite_does_not_resume_an_unrelated_draft(self):
        cid = self.chapter(1, '旧正文')
        self.db.update_chapter_meta(cid, memory_pending=1, memory_run_id=999)
        run = ledger.create_run(self.db, self.pid, '重写', 'rewrite', {})
        self.worker(force_rewrite=True, resume_run_id=run)._do_chapter(
            self.db, None, 1, '章节1', cid)
        self.assertEqual(self.calls.count(prompts.SYSTEM_WRITER), 1)

    def test_prefix_cache_excludes_future_and_invalidates_after_edit(self):
        c1 = self.chapter(1, '第一章旧正文', '过期情节')
        c2 = self.chapter(2, '第二章正文', '第二章摘要')
        c3 = self.chapter(3, '第三章正文', '未来结局')
        w = self.worker()
        w._finish_chapter(self.db, c3, '第三章')
        self.assertNotIn('未来结局', context_pack_for(self.db, self.db.get_chapter(c2)))
        stale = self.db.get_chapter(c2)
        self.db.update_chapter_meta(c2, chapter_card='修改卡片')
        self.assertNotIn('未来结局', context_pack_for(self.db, stale))
        self.db.save_chapter(c1, '第一章新版正文')
        self.assertNotIn('过期情节', context_pack_for(self.db, self.db.get_chapter(c3)))
        w._finish_chapter(self.db, c2, '第二章')
        self.assertNotIn('过期情节', self.db.get_chapter(c2)['prefix_summary'])
        self.assertEqual(self.db.get_volume_summary(self.pid, 1), '')
        w._finish_chapter(self.db, c3, '第三章')
        self.assertNotIn('过期情节', self.db.get_volume_summary(self.pid, 1))

    def test_identical_merge_can_fill_matching_metadata(self):
        keep = self.chapter(1, '相同正文')
        drop = self.chapter(1, '相同正文', '匹配摘要')
        self.db.merge_duplicate_chapters(keep, [drop])
        self.assertEqual(self.db.get_chapter(keep)['summary'], '匹配摘要')

    def test_cross_book_merge_rejected_before_any_change(self):
        keep = self.chapter(1, '保留正文')
        drop = self.db.create_chapter(self.pid + 1, 1, 1, '其他书', content='其他正文')
        with self.assertRaises(RuntimeError):
            self.db.merge_duplicate_chapters(keep, [drop])
        self.assertIsNotNone(self.db.get_chapter(drop))
        self.assertEqual(self.db.get_versions(keep), [])

    def test_empty_summary_stays_pending(self):
        cid = self.chapter(1)
        w = self.worker()
        original = w._call
        w._call = lambda system, *a, **kw: '' if system == prompts.SYSTEM_SUMMARY else original(system, *a, **kw)
        with self.assertRaises(RuntimeError):
            w._do_chapter(self.db, None, 1, '章节1', cid)
        self.assertEqual(self.db.get_chapter(cid)['memory_pending'], 1)

    def test_write_until_repairs_memory_when_all_bodies_exist(self):
        cid = self.chapter(1, '已保存正文')
        w = self.worker(until_no=1)
        w._run_until(self.db)
        self.assertNotIn(prompts.SYSTEM_WRITER, self.calls)
        self.assertEqual(self.db.get_chapter(cid)['summary'], '新摘要')
        self.assertTrue(self.db.get_volume_summary(self.pid, 1))

    def test_failed_chapter_stops_before_following_chapter(self):
        for no in (1, 2, 3):
            self.db.add_outline(self.pid, '章纲', f'第{no}章 章节{no}',
                                '推进情节', volume=1)
        w = self.worker(gen_card=False)
        original = w._call
        writer_calls = []

        def fail_second_writer(system, *args, **kwargs):
            if system == prompts.SYSTEM_WRITER:
                writer_calls.append(len(writer_calls) + 1)
                if len(writer_calls) == 2:
                    raise RuntimeError('第二章模型失败')
            return original(system, *args, **kwargs)

        w._call = fail_second_writer
        with self.assertRaisesRegex(RuntimeError, '已暂停后续章节'):
            w._write_volume(self.db, 1)
        chapters = self.db.get_chapters(self.pid)
        self.assertEqual(writer_calls, [1, 2])
        self.assertEqual([(c['chapter_no'], bool(c['content'])) for c in chapters],
                         [(1, True), (2, False)])
        self.assertEqual(w._stats['failed'], 1)

    def test_old_database_migrates_without_changing_manuscript(self):
        cid = self.chapter(1, '旧库正文', '旧库摘要')
        for column in ('memory_pending', 'memory_run_id', 'prefix_summary', 'prefix_hash'):
            self.db.conn.execute(f'ALTER TABLE chapters DROP COLUMN {column}')
        self.db.conn.commit()
        self.db.close()
        self.db = DB(str(Path(self.tmp.name) / 'book.db'))
        row = self.db.get_chapter(cid)
        self.assertEqual(row['content'], '旧库正文')
        self.assertEqual(row['summary'], '旧库摘要')
        self.assertEqual(row['memory_pending'], 0)
        self.assertEqual(row['prefix_hash'], '')

    def test_worker_records_completion_without_ui_signal_handler(self):
        cid = self.chapter(1)
        run = ledger.create_run(self.db, self.pid, '写作', 'write_volume', {})
        w = self.worker()
        w.task_run_id = run
        w._do_chapter(self.db, None, 1, '章节1', cid)
        self.assertEqual(ledger.completed_chapter_keys(self.db, run), {f'chapter:{cid}'})
        self.assertEqual(self.db.get_chapter(cid)['memory_pending'], 0)


if __name__ == '__main__':
    unittest.main()
