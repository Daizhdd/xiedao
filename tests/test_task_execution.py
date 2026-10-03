"""Recovery regressions: isolated books, fake models, and real task persistence."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication, QMessageBox

from ai import client, prompts, prefs, style_profile, fallback
from ai.usage_budget import BudgetPaused
from db import DB
import task_execution as execution
import task_ledger as ledger
from ui.chat_window import ChatWindow
from ui.pipeline_dialog import PipelineDialog


class TaskExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / 'synthetic.db')
        self.db = DB(self.path)
        ledger.initialize(self.db)
        self.pid = self.db.create_project('恢复演练', style_sheet='使用短句')
        self.config_id = self.db.add_ai_config('原模型', 'openai_compat',
            'https://example.invalid/v1', 'synthetic-secret', 'original', 1)
        self.other_id = self.db.add_ai_config('备用模型', 'openai_compat',
            'https://backup.invalid/v1', 'backup-secret', 'backup')
        self.cfg = dict(self.db.get_default_config())
        self.log_patch = patch.object(client, '_log_api')
        self.log_patch.start()
        fallback.reset()

    def tearDown(self):
        fallback.reset()
        self.log_patch.stop()
        self.db.close()
        self.tmp.cleanup()

    def make_task(self, **options):
        worker = execution.create_worker(self.cfg, self.path, self.pid, **options)
        run = execution.start_task(self.db, worker, '测试任务')
        ledger.update_run(self.db, run, 'interrupted')
        return run, worker

    def save_usage(self, run, **values):
        usage = dict.fromkeys(execution.USAGE_FIELDS, 0)
        usage.update(values)
        ledger.save_usage(self.db, run, self.pid, usage)

    def test_restart_preserves_all_options_preferences_style_and_model(self):
        self.db.set_setting(prefs.KEY_BUDGET, '32768')
        self.db.set_setting(prefs.KEY_THINKING, 'always_off')
        self.db.set_setting('task_budget.max_calls', '20')
        self.db.set_setting('task_budget.output_tokens', '50000')
        self.db.set_setting('task_budget.retries', '1')
        run, original = self.make_task(vol=2, auto_outline=False, skip_existing=False,
            gen_card=False, min_chars=23, use_tools=False, review=False, min_score=88,
            distill_style=False, force_rewrite=True, rewrite_instruction=' 保留伏笔 ',
            only_chapter_no=8, candidate_mode=True)
        stored = ledger.get_run(self.db, run)['args_json']
        self.assertNotIn('synthetic-secret', stored)
        self.assertNotIn('backup-secret', stored)
        self.assertNotIn('api_key', stored)
        self.db.update_ai_config(self.config_id, api_key='rotated-secret', is_default=0)
        self.db.update_ai_config(self.other_id, is_default=1)
        self.db.set_setting(prefs.KEY_BUDGET, '8192')
        self.db.set_setting(prefs.KEY_THINKING, 'always_on')
        self.db.set_setting('task_budget.max_calls', '100')
        self.db.update_project(self.pid, style_sheet='改用长句')
        self.db.close()
        self.db = DB(self.path)
        worker, warnings = execution.resume_worker(self.db, run, self.pid)
        for name in execution.OPTION_NAMES:
            self.assertEqual(getattr(worker, name), getattr(original, name), name)
        worker._configure_run(self.db)
        self.assertEqual(worker.cfg['model'], 'original')
        self.assertEqual(worker.cfg['api_key'], 'rotated-secret')
        self.assertEqual(worker._prefs, {'budget': 32768, 'thinking': 'always_off'})
        self.assertEqual(worker._budget.max_calls, 20)
        self.assertEqual(worker._budget.output_cap, 50000)
        self.assertEqual(worker._budget.retry_limit, 1)
        self.assertIn('使用短句', worker._style_snapshot)
        self.assertNotIn('改用长句', worker._style_snapshot)
        self.assertTrue(any('文风' in message for message in warnings))

    def test_changed_or_deleted_original_model_is_actionable(self):
        run, _ = self.make_task()
        self.db.update_ai_config(self.config_id, model='changed')
        with self.assertRaisesRegex(execution.TaskRecoveryError, '模型配置'):
            execution.resume_worker(self.db, run, self.pid)
        self.db.delete_ai_config(self.config_id)
        with self.assertRaisesRegex(execution.TaskRecoveryError, '模型配置'):
            execution.resume_worker(self.db, run, self.pid)

    def test_deleted_fallback_cannot_silently_change_execution_chain(self):
        run, _ = self.make_task()
        self.db.delete_ai_config(self.other_id)
        with self.assertRaises(execution.TaskRecoveryError):
            execution.resume_worker(self.db, run, self.pid)

    def test_manuscript_change_blocks_stale_recovery(self):
        cid = self.db.create_chapter(self.pid, 1, 1, '开场', content='原稿')
        run, _ = self.make_task(force_rewrite=True, rewrite_instruction='加快节奏')
        self.db.save_chapter(cid, '作者已经改稿')
        with self.assertRaisesRegex(execution.TaskRecoveryError, '作品内容'):
            execution.resume_worker(self.db, run, self.pid)

    def test_wrong_book_and_already_resumed_run_are_rejected(self):
        run, _ = self.make_task()
        other = self.db.create_project('另一部作品')
        with self.assertRaisesRegex(execution.TaskRecoveryError, '另一本书'):
            execution.resume_worker(self.db, run, other)
        ledger.mark_resumed(self.db, run)
        with self.assertRaisesRegex(execution.TaskRecoveryError, '接管'):
            execution.resume_worker(self.db, run, self.pid)

    def test_corrupt_or_future_snapshot_does_not_fall_back_to_defaults(self):
        run, _ = self.make_task()
        args = json.loads(ledger.get_run(self.db, run)['args_json'])
        args.pop('review')
        ledger.save_spec(self.db, run, 'write_volume', args)
        with self.assertRaisesRegex(execution.TaskRecoveryError, '不完整'):
            execution.resume_worker(self.db, run, self.pid)
        args['snapshot_version'] = 99
        ledger.save_spec(self.db, run, 'write_volume', args)
        with self.assertRaisesRegex(execution.TaskRecoveryError, '版本'):
            execution.resume_worker(self.db, run, self.pid)

    def test_exhausted_budget_requires_explicit_increase(self):
        self.db.set_setting('task_budget.max_calls', '1')
        run, _ = self.make_task()
        self.save_usage(run, calls=1, completion_tokens=40, reserved_completion=40)
        self.db.set_setting('task_budget.max_calls', '3')
        with self.assertRaises(execution.TaskBudgetExhausted):
            execution.resume_worker(self.db, run, self.pid)
        worker, _ = execution.resume_worker(self.db, run, self.pid,
            budget_override=execution.budget_settings(self.db))
        worker._configure_run(self.db)
        self.assertEqual(worker._budget.calls, 1)
        worker._budget.reserve(256)
        worker._budget.reserve(256)
        with self.assertRaises(BudgetPaused):
            worker._budget.reserve(256)

    def test_unknown_output_reservation_survives_recovery(self):
        self.db.set_setting('task_budget.output_tokens', '500')
        run, _ = self.make_task()
        self.save_usage(run, calls=1, unknown_calls=1, reserved_completion=500)
        with self.assertRaises(execution.TaskBudgetExhausted):
            execution.resume_worker(self.db, run, self.pid)
        worker, _ = execution.resume_worker(self.db, run, self.pid,
            budget_override={'max_calls': 0, 'output_cap': 1000})
        worker._configure_run(self.db)
        self.assertEqual(worker._budget.reserve(700), 500)
        self.assertEqual(worker._budget.snapshot()['unknown_calls'], 2)
        self.assertEqual(worker._budget.snapshot()['reserved_completion'], 1000)

    def test_legacy_tasks_warn_and_old_rewrite_without_instruction_refuses(self):
        run = ledger.create_run(self.db, self.pid, '旧写作', 'write_until', {'until_no': 3})
        ledger.update_run(self.db, run, 'interrupted')
        worker, warnings = execution.resume_worker(self.db, run, self.pid)
        self.assertEqual(worker.until_no, 3)
        self.assertTrue(any('旧任务' in message for message in warnings))
        bad = ledger.create_run(self.db, self.pid, '旧重写', 'rewrite', {'vol': 1})
        ledger.update_run(self.db, bad, 'interrupted')
        with self.assertRaisesRegex(execution.TaskRecoveryError, '没有存档'):
            execution.resume_worker(self.db, bad, self.pid)

    def test_old_schema_migrates_without_losing_existing_task(self):
        run, _ = self.make_task()
        self.db.conn.execute('ALTER TABLE task_runs DROP COLUMN book_revision')
        self.db.conn.commit()
        ledger.ensure_schema(self.db)
        worker, _ = execution.resume_worker(self.db, run, self.pid)
        self.assertEqual(worker.resume_run_id, run)

    def test_two_interruptions_only_generate_unfinished_chapters_and_sum_usage_once(self):
        for no in range(1, 5):
            oid = self.db.add_outline(self.pid, '章纲', f'第{no}章', '推进剧情', volume=1)
            self.db.create_chapter(self.pid, 1, no, f'第{no}章', content=f'旧稿{no}',
                                   chapter_card='推进剧情', outline_id=oid)
        self.db.set_setting('task_budget.max_calls', '20')
        worker = execution.create_worker(self.cfg, self.path, self.pid,
            force_rewrite=True, rewrite_instruction='原要求', gen_card=False,
            min_chars=1, review=False, use_tools=False, distill_style=False)
        writers, requests = [], []
        def fake_post(cfg, body, timeout):
            system = body['messages'][0]['content']
            requests.append(system)
            if system == prompts.SYSTEM_WRITER:
                self.assertIn('原要求', body['messages'][1]['content'])
                writers.append(len(writers) + 1)
                result = f'新正文{writers[-1]}'
            elif system == prompts.SYSTEM_SUMMARY:
                result = '章摘要'
            elif system == prompts.SYSTEM_ROLLUP:
                result = '卷摘要'
            else:
                self.fail(f'不该调用的阶段：{system[:20]}')
            return {'choices': [{'message': {'content': result}}],
                    'usage': {'prompt_tokens': 7, 'completion_tokens': 10}}
        def attempt(w, stop_no=0, parent=0):
            run = execution.start_task(self.db, w, '续写测试', parent)
            def chapter_done(cid, name):
                ledger.record_step(self.db, run, f'chapter:{cid}', 'completed', name)
                if self.db.get_chapter(cid)['chapter_no'] == stop_no:
                    w.stop()
            w.chapter_done.connect(chapter_done)
            w.finished_ok.connect(lambda message: ledger.update_run(
                self.db, run, 'stopped' if message.startswith('已停止') else 'completed'))
            failures = []
            w.failed.connect(failures.append)
            w.run()
            self.assertEqual(failures, [])
            return run
        with patch.object(client, '_post', side_effect=fake_post):
            first = attempt(worker, 2)
            self.assertEqual(len(writers), 2)
            self.db.set_setting('task_budget.max_calls', '100')
            second_worker, _ = execution.resume_worker(self.db, first, self.pid)
            second = attempt(second_worker, 3, first)
            third_worker, _ = execution.resume_worker(self.db, second, self.pid)
            third = attempt(third_worker, parent=second)
        self.assertEqual(writers, [1, 2, 3, 4])
        self.assertEqual([ledger.get_usage(self.db, run)['calls'] for run in
                          (first, second, third)], [6, 3, 3])
        self.assertEqual(ledger.cumulative_usage(self.db, third)['calls'], 12)
        self.assertEqual(ledger.cumulative_usage(self.db, third)['completion_tokens'], 120)
        self.assertEqual(third_worker._budget.max_calls, 20)
        self.assertNotIn(prompts.SYSTEM_REVIEWER, requests)
        self.assertEqual([c['content'] for c in self.db.get_chapters(self.pid)],
                         ['新正文1', '新正文2', '新正文3', '新正文4'])

    def test_classic_workspace_uses_the_same_complete_snapshot(self):
        dialog = PipelineDialog(self.db, self.pid, 1, self.cfg)
        dialog.ck_review.setChecked(False)
        dialog.sp_min.setValue(237)
        worker = dialog._make_worker(False)
        args = json.loads(ledger.get_run(self.db, dialog.run_id)['args_json'])
        self.assertFalse(args['review'])
        self.assertEqual(args['min_chars'], 237)
        self.assertEqual(args['snapshot_version'], execution.SNAPSHOT_VERSION)
        self.assertEqual(worker.task_run_id, dialog.run_id)
        dialog.close()

    def test_budget_pause_after_body_resumes_memory_without_another_writer_call(self):
        oid = self.db.add_outline(self.pid, '章纲', '第1章 开场', '推进剧情', volume=1)
        cid = self.db.create_chapter(self.pid, 1, 1, '第1章 开场',
                                    chapter_card='推进剧情', outline_id=oid)
        self.db.set_setting('task_budget.max_calls', '1')
        worker = execution.create_worker(self.cfg, self.path, self.pid,
            gen_card=False, min_chars=1, review=False, use_tools=False, distill_style=False)
        first = execution.start_task(self.db, worker, '先写正文')
        messages, systems = [], []
        worker.finished_ok.connect(messages.append)
        def fake_post(cfg, body, timeout):
            system = body['messages'][0]['content']
            systems.append(system)
            result = {prompts.SYSTEM_WRITER: '已保存正文',
                      prompts.SYSTEM_SUMMARY: '恢复后的摘要',
                      prompts.SYSTEM_ROLLUP: '恢复后的卷摘要'}[system]
            return {'choices': [{'message': {'content': result}}],
                    'usage': {'prompt_tokens': 7, 'completion_tokens': 10}}
        with patch.object(client, '_post', side_effect=fake_post):
            worker.run()
            self.assertTrue(messages[-1].startswith('预算已用尽'))
            self.assertEqual(self.db.get_chapter(cid)['content'], '已保存正文')
            self.assertTrue(self.db.get_chapter(cid)['memory_pending'])
            ledger.update_run(self.db, first, 'paused_budget')
            resumed, _ = execution.resume_worker(self.db, first, self.pid,
                budget_override={'max_calls': 3, 'output_cap': 0})
            child = execution.start_task(self.db, resumed, '补齐记忆', first)
            completed = []
            resumed.finished_ok.connect(completed.append)
            resumed.run()
        self.assertTrue(completed and not completed[-1].startswith('预算已用尽'))
        self.assertEqual(systems.count(prompts.SYSTEM_WRITER), 1)
        self.assertEqual(self.db.get_chapter(cid)['summary'], '恢复后的摘要')
        self.assertFalse(self.db.get_chapter(cid)['memory_pending'])
        self.assertEqual(ledger.cumulative_usage(self.db, child)['calls'], 3)

    def test_budget_increase_ui_requires_confirmation_and_cancel_keeps_parent(self):
        self.db.set_setting('task_budget.max_calls', '1')
        run, _ = self.make_task()
        self.save_usage(run, calls=1, reserved_completion=50)
        self.db.set_setting('task_budget.max_calls', '3')
        window = ChatWindow(self.db)
        window._set_current_book(self.pid)
        with patch('ui.chat_window.QMessageBox.question', return_value=QMessageBox.No):
            window._resume_interrupted(dict(ledger.get_run(self.db, run)))
        self.assertIsNone(window.worker)
        self.assertEqual(ledger.get_run(self.db, run)['status'], 'interrupted')
        started = []
        with patch('ui.chat_window.QMessageBox.question', return_value=QMessageBox.Yes):
            window._start_pipeline = lambda title, factory, **kw: started.append(factory())
            window._resume_interrupted(dict(ledger.get_run(self.db, run)))
        self.assertEqual(started[0]._runtime_snapshot['budget']['max_calls'], 3)
        window.close()

    def test_ui_discloses_original_model_when_default_has_changed(self):
        run, _ = self.make_task()
        self.db.update_ai_config(self.other_id, is_default=1)
        window = ChatWindow(self.db)
        window._set_current_book(self.pid)
        workers = []
        window._start_pipeline = lambda title, factory, **kw: workers.append(factory())
        window._resume_interrupted(dict(ledger.get_run(self.db, run)))
        self.assertEqual(workers[0].cfg['model'], 'original')
        self.assertIn('模型沿用「原模型」', window.history()[-1][1])
        window.close()


if __name__ == '__main__':
    unittest.main()
