# -*- coding: utf-8 -*-
"""Version-safe proposed rewrites, partial adoption, locks, and backup."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai import rewrite_candidates as rw
from book_backup import capture_project, restore_project, validate_full_backup
from db import DB


class RewriteCandidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DB(str(Path(self.tmp.name) / 'book.db'))
        self.pid = self.db.create_project('候选稿测试')
        self.base = '第一段，旧开头。\n\n第二段，旧冲突。\n第三段，旧结尾。\n'
        self.cid = self.db.create_chapter(self.pid, 1, 1, '第一章',
                                          content=self.base, summary='旧摘要')

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_candidate_does_not_change_author_draft_or_memory(self):
        proposal = rw.create_candidate(self.db, self.cid, '完全不同的新稿。', '节奏加快', task_id=42)
        row = rw.get_candidate(self.db, proposal)
        self.assertEqual(row['base_hash'], rw.digest(self.base))
        self.assertEqual(row['task_id'], 42)
        self.assertEqual(row['status'], 'pending')
        self.assertEqual(self.db.get_chapter(self.cid)['content'], self.base)
        self.assertEqual(self.db.get_chapter(self.cid)['summary'], '旧摘要')
        self.assertEqual(self.db.get_versions(self.cid), [])

    def test_full_accept_saves_version_and_invalidates_summary(self):
        candidate = '新开头。\n\n新冲突。\n新结尾。\n'
        proposal = rw.create_candidate(self.db, self.cid, candidate, review_status='AI草稿·低分')
        self.assertEqual(rw.accept_full(self.db, proposal), candidate)
        ch = self.db.get_chapter(self.cid)
        self.assertEqual(ch['content'], candidate)
        self.assertEqual(ch['summary'], '')
        self.assertEqual(ch['memory_pending'], 1)
        self.assertEqual(ch['status'], 'AI草稿·低分')
        self.assertEqual(rw.get_candidate(self.db, proposal)['status'], 'accepted')
        self.assertIn(self.base, [v['snapshot'] for v in self.db.get_versions(self.cid)])

    def test_author_edit_makes_candidate_stale_without_deleting_it(self):
        proposal = rw.create_candidate(self.db, self.cid, '重写稿。')
        self.db.save_chapter(self.cid, '作者手动改过。')
        with self.assertRaises(rw.StaleCandidate):
            rw.accept_full(self.db, proposal)
        self.assertEqual(self.db.get_chapter(self.cid)['content'], '作者手动改过。')
        self.assertEqual(rw.get_candidate(self.db, proposal)['status'], 'pending')
        rw.abandon(self.db, proposal)
        self.assertEqual(rw.get_candidate(self.db, proposal)['status'], 'discarded')

    def test_accept_two_paragraphs_separately_without_losing_other_change(self):
        draft = '第一段，新开头。\n\n第二段，旧冲突。\n第三段，新结尾。\n'
        proposal = rw.create_candidate(self.db, self.cid, draft)
        hunks = rw.diff_hunks(self.base, draft)
        self.assertEqual(len(hunks), 2)
        rw.accept_hunks(self.db, proposal, [0])
        self.assertEqual(self.db.get_chapter(self.cid)['content'],
                         '第一段，新开头。\n\n第二段，旧冲突。\n第三段，旧结尾。\n')
        self.assertEqual(rw.get_candidate(self.db, proposal)['status'], 'pending')
        self.assertEqual(self.db.get_chapter(self.cid)['status'], 'AI草稿·未审')
        rw.accept_hunks(self.db, proposal, [1])
        self.assertEqual(self.db.get_chapter(self.cid)['content'], draft)
        self.assertEqual(rw.get_candidate(self.db, proposal)['status'], 'accepted')

    def test_adjacent_changed_paragraphs_are_separately_adoptable(self):
        before = '甲旧。\n乙旧。\n丙不动。\n'
        after = '甲新。\n乙新。\n丙不动。\n'
        self.db.save_chapter(self.cid, before)
        proposal = rw.create_candidate(self.db, self.cid, after)
        self.assertEqual(len(rw.diff_hunks(before, after)), 2)
        rw.accept_hunks(self.db, proposal, [1])
        self.assertEqual(self.db.get_chapter(self.cid)['content'],
                         '甲旧。\n乙新。\n丙不动。\n')

    def test_human_edit_between_partial_accepts_blocks_remaining(self):
        draft = '第一段，新开头。\n\n第二段，旧冲突。\n第三段，新结尾。\n'
        proposal = rw.create_candidate(self.db, self.cid, draft)
        rw.accept_hunks(self.db, proposal, [0])
        self.db.save_chapter(self.cid, '我自己又改了正文。')
        with self.assertRaises(rw.StaleCandidate):
            rw.accept_hunks(self.db, proposal, [1])
        self.assertEqual(rw.get_candidate(self.db, proposal)['status'], 'pending')

    def test_locked_paragraph_is_protected_and_can_be_unlocked(self):
        pos = self.base.index('第二段')
        lock = rw.lock_selection(self.db, self.cid, pos, pos + 2)
        self.assertEqual(len(rw.active_locks(self.db, self.cid)), 1)
        with self.assertRaisesRegex(ValueError, '锁定段落'):
            rw.create_candidate(self.db, self.cid, self.base.replace('旧冲突', '新冲突'))
        proposal = rw.create_candidate(self.db, self.cid,
                                       self.base.replace('旧开头', '新开头'))
        rw.accept_full(self.db, proposal)
        moved = rw.active_locks(self.db, self.cid)
        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0]['id'], lock)
        with self.assertRaisesRegex(ValueError, '锁定段落'):
            rw.create_candidate(self.db, self.cid,
                                self.db.get_chapter(self.cid)['content'].replace('旧冲突', '被改动'))
        rw.unlock(self.db, lock, self.cid)

    def test_lock_selection_ending_at_newline_does_not_lock_next_paragraph(self):
        body = '第一段。\n第二段。\n'
        self.db.save_chapter(self.cid, body)
        lock = rw.lock_selection(self.db, self.cid, 0, len('第一段。\n'))
        row = rw.active_locks(self.db, self.cid)[0]
        self.assertEqual(row['id'], lock)
        self.assertEqual(body[row['start_pos']:row['end_pos']], '第一段。\n')

    def test_chapter_delete_cleans_candidates_and_locks_for_backup(self):
        rw.create_candidate(self.db, self.cid, self.base.replace('旧开头', '新开头'))
        rw.lock_selection(self.db, self.cid, self.base.index('第二段'),
                          self.base.index('第二段') + 2)
        self.db.delete_chapter(self.cid)
        self.assertEqual(rw.list_candidates(self.db, self.cid), [])
        payload = capture_project(self.db, self.pid)
        self.assertEqual(payload['tables']['rewrite_candidates'], [])
        self.assertEqual(payload['tables']['rewrite_locks'], [])

    def test_identical_chapter_merge_keeps_pending_candidate(self):
        duplicate = self.db.create_chapter(self.pid, 1, 1, '同稿重复章', content=self.base)
        proposal = rw.create_candidate(self.db, duplicate,
                                        self.base.replace('旧开头', '新开头'))
        self.db.merge_duplicate_chapters(self.cid, [duplicate])
        self.assertEqual(rw.get_candidate(self.db, proposal)['chapter_id'], self.cid)
        self.assertEqual(len(rw.list_candidates(self.db, self.cid)), 1)

    def test_atomic_accept_refuses_changed_body_and_keeps_version_history(self):
        old_hash = rw.digest(self.base)
        self.db.save_chapter(self.cid, '作者改稿。')
        count = len(self.db.get_versions(self.cid))
        with self.assertRaises(ValueError):
            self.db.save_chapter(self.cid, '过时候选。', expected_hash=old_hash)
        self.assertEqual(self.db.get_chapter(self.cid)['content'], '作者改稿。')
        self.assertEqual(len(self.db.get_versions(self.cid)), count)

    def test_repeated_locked_paragraph_is_rejected_conservatively(self):
        repeated = '同样的段落。\n同样的段落。\n末段。\n'
        self.db.save_chapter(self.cid, repeated)
        rw.lock_selection(self.db, self.cid, 1, 2)
        with self.assertRaisesRegex(ValueError, '重复文本'):
            rw.create_candidate(self.db, self.cid, repeated.replace('末段', '终章'))
        begin = repeated.index('末段')
        draft = rw.compose_selection(repeated, begin, begin + 2, '终章')
        proposal = rw.create_candidate(self.db, self.cid, draft, mode='selection',
                                       scope=(begin, begin + 2))
        rw.accept_full(self.db, proposal)
        self.assertEqual(self.db.get_chapter(self.cid)['content'], draft)
        self.assertEqual(len(rw.active_locks(self.db, self.cid)), 1)

    def test_local_rewrite_preserves_surrounding_blank_lines_and_emoji(self):
        self.db.save_chapter(self.cid, '😀开场。\n\n对话：你好。\n结束。\n')
        text = self.db.get_chapter(self.cid)['content']
        lo = text.index('你好')
        hi = lo + len('你好')
        draft = rw.compose_selection(text, lo, hi, '晚上好')
        proposal = rw.create_candidate(self.db, self.cid, draft, mode='dialogue',
                                       scope=(lo, hi))
        self.assertEqual(self.db.get_chapter(self.cid)['content'], text)
        rw.accept_full(self.db, proposal)
        self.assertEqual(self.db.get_chapter(self.cid)['content'],
                         '😀开场。\n\n对话：晚上好。\n结束。\n')

    def test_selection_cannot_overlap_locked_paragraph(self):
        rw.lock_selection(self.db, self.cid, 1, 3)
        with self.assertRaisesRegex(ValueError, '锁定段落'):
            rw.selection_prompt(self.base, 0, 3, 'selection', '改',
                                rw.active_locks(self.db, self.cid))

    def test_backup_restore_old_format_and_cross_book_protection(self):
        lock = rw.lock_selection(self.db, self.cid, 0, 2)
        proposal = rw.create_candidate(self.db, self.cid, self.base.replace('旧结尾', '新结尾'))
        backup = capture_project(self.db, self.pid)
        self.assertEqual(backup['format_version'], 7)
        self.db.delete_project(self.pid)
        restore_project(self.db, backup)
        self.assertEqual(capture_project(self.db, self.pid)['tables'], backup['tables'])
        self.assertEqual(rw.get_candidate(self.db, proposal)['status'], 'pending')
        self.assertEqual(rw.active_locks(self.db, self.cid)[0]['id'], lock)
        bad = copy.deepcopy(backup)
        bad['tables']['rewrite_candidates'][0]['chapter_id'] = 99999
        with self.assertRaisesRegex(Exception, '其他书'):
            validate_full_backup(bad)
        v2 = copy.deepcopy(backup)
        v2['format_version'] = 2
        v2['tables'].pop('rewrite_candidates')
        v2['tables'].pop('rewrite_locks')
        v2['tables'].pop('review_reports')
        v2['tables'].pop('style_profiles')
        v2['tables'].pop('style_suggestions')
        v2['tables'].pop('task_usage')
        v2['tables'].pop('setting_candidates')
        validate_full_backup(v2)
        restore_project(self.db, v2)
        self.assertEqual(rw.list_candidates(self.db, self.cid), [])

    def test_pipeline_batch_rewrite_creates_reviewable_candidates_only(self):
        from unittest.mock import patch
        from ai.pipeline import PipelineWorker
        from ai import prompts
        other = self.db.create_chapter(self.pid, 1, 2, '第二章',
                                       content='另一章旧稿。', chapter_card='继续')
        worker = PipelineWorker({}, self.db.path, self.pid, 1, force_rewrite=True,
                                rewrite_instruction='节奏快些', candidate_mode=True,
                                use_tools=False, review=False, distill_style=False,
                                min_chars=1)
        def fake(system, *_args, **_kwargs):
            if system == prompts.SYSTEM_WRITER:
                return '新的候选正文。'
            raise AssertionError('候选稿不得触发摘要或登记')
        worker._call = fake
        with patch.object(worker, '_distill_style', side_effect=AssertionError('不得改风格')):
            worker.run()
        self.assertEqual(self.db.get_chapter(self.cid)['content'], self.base)
        self.assertEqual(self.db.get_chapter(other)['content'], '另一章旧稿。')
        self.assertEqual(self.db.get_chapter(self.cid)['summary'], '旧摘要')
        self.assertEqual(len(rw.list_candidates(self.db, self.cid)), 1)
        self.assertEqual(len(rw.list_candidates(self.db, other)), 1)
        self.assertEqual(self.db.get_versions(self.cid), [])

    def test_whole_book_candidate_mode_does_not_run_book_creation_stages(self):
        from unittest.mock import patch
        from ai.pipeline import PipelineWorker
        from ai import prompts
        self.db.create_chapter(self.pid, 1, 2, '空章', content='')
        self.db.create_chapter(self.pid, 2, 3, '另一卷', content='另一卷旧稿。')
        before = dict(self.db.get_project(self.pid))
        worker = PipelineWorker({}, self.db.path, self.pid, 1, force_rewrite=True,
                                whole_book=True, candidate_mode=True,
                                rewrite_instruction='文风平实', use_tools=False,
                                review=False, min_chars=1, distill_style=False)
        worker._call = lambda system, *_args, **_kwargs: (
            '新的候选正文。' if system == prompts.SYSTEM_WRITER
            else (_ for _ in ()).throw(AssertionError('候选流程意外调用规划')))
        with patch.object(worker, '_stage_settings', side_effect=AssertionError('设定不可改')):
            with patch.object(worker, '_stage_volumes', side_effect=AssertionError('卷纲不可改')):
                with patch.object(worker, '_stage_chapter_outlines',
                                  side_effect=AssertionError('章纲不可改')):
                    with patch.object(worker, '_stage_intro', side_effect=AssertionError('简介不可改')):
                        worker.run()
        self.assertEqual(dict(self.db.get_project(self.pid)), before)
        self.assertEqual(self.db.get_chapter(self.cid)['content'], self.base)
        self.assertEqual(len(rw.list_candidates(self.db, self.cid)), 1)
        empty = next(c for c in self.db.get_chapters(self.pid) if c['title'] == '空章')
        self.assertEqual(rw.list_candidates(self.db, empty['id']), [])
        self.assertEqual(empty['content'], '')

    def test_candidate_resume_keeps_mode_and_skips_completed_proposals(self):
        from ai.pipeline import PipelineWorker
        import task_ledger as ledger
        from ai import prompts
        ledger.initialize(self.db)
        worker = PipelineWorker({}, self.db.path, self.pid, 1, force_rewrite=True,
                                candidate_mode=True, rewrite_instruction='节奏快些',
                                use_tools=False, review=False, distill_style=False,
                                min_chars=1)
        mode, args = ledger.worker_spec(worker)
        self.assertEqual(args['candidate_mode'], True)
        run = ledger.create_run(self.db, self.pid, '重写', mode, args)
        worker.task_run_id = run
        worker._call = lambda system, *_a, **_kw: ('候选正文。' if system == prompts.SYSTEM_WRITER
                                                   else '')
        worker.run()
        self.assertEqual(len(rw.list_candidates(self.db, self.cid)), 1)
        ledger.update_run(self.db, run, 'interrupted')
        resumed = PipelineWorker({}, self.db.path, self.pid, 1,
                                 force_rewrite=True, candidate_mode=args['candidate_mode'],
                                 rewrite_instruction=args['rewrite_instruction'],
                                 resume_run_id=run, use_tools=False, review=False,
                                 distill_style=False, min_chars=1)
        resumed._call = lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError('已完成候选不得重复调用模型'))
        resumed.run()
        self.assertEqual(len(rw.list_candidates(self.db, self.cid)), 1)

    def test_utf16_cursor_offsets_are_safe_with_emoji(self):
        text = '😀甲\n乙'
        self.assertEqual(rw.utf16_to_py(text, 2), 1)
        self.assertEqual(rw.utf16_to_py(text, 4), 3)
        with self.assertRaises(ValueError):
            rw.utf16_to_py(text, 1)

    def test_manual_selection_worker_stores_only_a_candidate(self):
        from unittest.mock import patch
        from ui.rewrite_dialog import ManualRewriteWorker
        lo = self.base.index('旧冲突')
        hi = lo + len('旧冲突')
        cfg = {'name': 'fake', 'provider': 'openai_compat',
               'base_url': 'https://example.invalid/v1', 'api_key': 'fake',
               'model': 'fake'}
        worker = ManualRewriteWorker(self.db.path, self.cid, cfg, self.base,
                                     'selection', '更紧张', (lo, hi))
        ids, errors = [], []
        worker.done.connect(ids.append)
        worker.failed.connect(errors.append)
        with patch('ai.client.simple_chat', return_value='新冲突'):
            worker.run()
        self.assertEqual(errors, [])
        self.assertEqual(len(ids), 1)
        self.assertEqual(self.db.get_chapter(self.cid)['content'], self.base)
        self.assertEqual(rw.get_candidate(self.db, ids[0])['candidate_content'],
                         self.base.replace('旧冲突', '新冲突'))

    def test_post_adoption_failure_keeps_body_and_pending_memory(self):
        from unittest.mock import patch
        from ui.rewrite_dialog import PostAdoptionWorker
        from ai.pipeline import PipelineWorker
        candidate = self.base.replace('旧结尾', '新结尾')
        rw.accept_full(self.db, rw.create_candidate(self.db, self.cid, candidate))
        worker = PostAdoptionWorker(self.db.path, self.cid, {})
        errors = []
        worker.failed.connect(errors.append)
        with patch.object(PipelineWorker, '_call', side_effect=RuntimeError('模拟摘要故障')):
            worker.run()
        self.assertEqual(self.db.get_chapter(self.cid)['content'], candidate)
        self.assertEqual(self.db.get_chapter(self.cid)['memory_pending'], 1)
        self.assertTrue(errors)

    def test_post_adoption_success_updates_summary_without_rewriting(self):
        from unittest.mock import patch
        from ui.rewrite_dialog import PostAdoptionWorker
        from ai.pipeline import PipelineWorker
        from ai import prompts
        candidate = self.base.replace('旧结尾', '新结尾')
        rw.accept_full(self.db, rw.create_candidate(self.db, self.cid, candidate))
        worker = PostAdoptionWorker(self.db.path, self.cid, {})
        done = []
        worker.done.connect(done.append)
        def fake(system, *_args, **_kw):
            if system == prompts.SYSTEM_SUMMARY:
                return '采纳后摘要'
            if system == prompts.SYSTEM_ROLLUP:
                return '采纳后卷摘要'
            raise AssertionError('采纳后不得重新生成正文')
        with patch.object(PipelineWorker, '_call', side_effect=fake):
            worker.run()
        self.assertEqual(self.db.get_chapter(self.cid)['content'], candidate)
        self.assertEqual(self.db.get_chapter(self.cid)['summary'], '采纳后摘要')
        self.assertEqual(self.db.get_chapter(self.cid)['memory_pending'], 0)
        self.assertEqual(done, ['摘要与记忆处理完成。事实候选仍需作者确认。'])

    def test_multi_step_selection_keeps_repeated_lock_after_offset_shift(self):
        before = '锁住。\n锁住。\n改甲。\n改乙。\n'
        self.db.save_chapter(self.cid, before)
        rw.lock_selection(self.db, self.cid, 1, 2)
        lo = before.index('改甲')
        hi = len(before)
        after = '锁住。\n锁住。\n扩展得更长的甲。\n新的乙。\n'
        proposal = rw.create_candidate(self.db, self.cid, after, mode='selection',
                                       scope=(lo, hi))
        self.assertEqual(len(rw.diff_hunks(before, after)), 2)
        rw.accept_hunks(self.db, proposal, [0])
        self.assertEqual(len(rw.active_locks(self.db, self.cid)), 1)
        rw.accept_hunks(self.db, proposal, [1])
        self.assertEqual(self.db.get_chapter(self.cid)['content'], after)
        self.assertEqual(len(rw.active_locks(self.db, self.cid)), 1)


if __name__ == '__main__':
    unittest.main()
