"""Real Qt task starts for saved rebuild requests, with synthetic books only."""
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication, QPushButton
from ai import client, intent, prompts
from db import DB
import task_execution as execution
import task_ledger as ledger
from ui.chat_window import ChatWindow
from ui.task_cards import ActivityCard


class RebuildStartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DB(str(Path(self.tmp.name) / 'synthetic.db'))
        self.pid = self.db.create_project('合成重构', plan_volumes=1, plan_chapters=1)
        self.cid = self.db.create_chapter(self.pid, 1, 1, '旧稿', content='旧正文')
        self.db.add_ai_config('测试模型', 'openai_compat', 'https://example.invalid/v1',
                              'fake-key', 'offline', 1)
        self.cfg = dict(self.db.get_default_config())
        self.worker = execution.create_worker(self.cfg, self.db.path, self.pid,
            whole_book=True, rebuild=True, rebuild_volumes=1,
            rewrite_instruction='按原要求突出苦练', min_chars=1, review=False,
            use_tools=False, gen_card=False, distill_style=False)
        self.parent = execution.start_task(self.db, self.worker, '全书重构')
        ledger.update_run(self.db, self.parent, 'failed', '注入前次故障')
        ledger.checkpoint_run(self.db, self.parent, self.pid)
        self.win = ChatWindow(self.db)
        self.win._set_current_book(self.pid)
        self.log = patch.object(client, '_log_api')
        self.log.start()
        self.release = threading.Event()

    def tearDown(self):
        self.release.set()
        worker = self.win.worker
        if worker and worker.isRunning():
            worker.stop()
            worker.wait(5000)
        self.app.processEvents()
        self.win.close()
        self.log.stop()
        self.db.close()
        self.tmp.cleanup()

    def wait_for(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertTrue(predicate(), 'Qt task did not reach expected state')

    def test_continuation_routes_locally_and_questions_do_not_start(self):
        ctx = intent.book_context(self.db, self.pid)
        with patch.object(client, 'chat_once_tools') as api:
            result = intent.decide(self.cfg, ctx, '继续完成全书重构')
        api.assert_not_called()
        self.assertEqual(result, {'type': 'tool', 'name': 'restart_rebuild',
                                  'args': {'run_id': self.parent}})
        self.assertEqual(intent.local_decision(ctx, '全书重构开始了吗？')['name'], 'progress_report')
        for text in ('不要继续全书重构', '继续完成全书重构？', '怎么继续全书重构',
                     '继续全书重构，但改成两卷', '聊聊全书重构'):
            self.assertIsNone(intent.local_decision(ctx, text))

    def test_restart_preserves_complete_options_model_and_original_instruction(self):
        worker = execution.restart_rebuild_worker(self.db, self.parent, self.pid)
        for option in execution.OPTION_NAMES:
            self.assertEqual(getattr(worker, option), getattr(self.worker, option))
        self.assertEqual(worker.resume_run_id, 0)
        self.assertEqual(worker.cfg['model'], 'offline')
        self.assertEqual(self.db.get_chapter(self.cid)['content'], '旧正文')

    def test_restart_rejects_other_book_changed_content_and_completed_task(self):
        with self.assertRaises(execution.TaskRecoveryError):
            execution.restart_rebuild_worker(self.db, self.parent, self.pid + 1)
        ledger.update_run(self.db, self.parent, 'completed')
        with self.assertRaises(execution.TaskRecoveryError):
            execution.restart_rebuild_worker(self.db, self.parent, self.pid)
        ledger.update_run(self.db, self.parent, 'failed')
        self.db.update_chapter_meta(self.cid, content='用户之后改过的正文')
        with self.assertRaisesRegex(execution.TaskRecoveryError, '作品内容'):
            execution.restart_rebuild_worker(self.db, self.parent, self.pid)

    def test_progress_uses_actual_failed_task_not_chat_promise(self):
        self.db.add_chat_msg(self.pid, 'assistant', '开始全书重构，我正在写')
        report = intent.progress_text(self.db, self.pid)
        self.assertIn('失败，当前未在写作', report)
        self.assertIn('现有章节数不代表重构进度', report)

    def test_recovery_button_starts_task_and_activity_card_survives_stream_refresh(self):
        entered = threading.Event()
        def post(cfg, body, timeout):
            entered.set()
            self.release.wait(3)
            return {'choices': [{'message': {'content': '- 第1卷 新卷：苦练'}}],
                    'usage': {'completion_tokens': 10}}
        with patch.object(client, '_post', side_effect=post):
            button = next(b for b in self.win.findChildren(QPushButton) if b.text() == '重新执行重构')
            button.click()
            self.wait_for(entered.is_set)
            card = self.win._active_task_card
            self.assertIsInstance(card, ActivityCard)
            self.assertFalse(card._done)
            self.assertEqual(ledger.get_run(self.db, self.win.worker.task_run_id)['status'], 'running')
            with patch.object(intent, 'decide', side_effect=AssertionError('No model for status')):
                self.win.composer.setPlainText('继续完成全书重构')
                self.win._send()
            self.assertEqual(self.db.conn.execute('SELECT count(*) FROM task_runs').fetchone()[0], 2)
            self.assertIn('正在执行', self.win.history()[-1][1])
            self.win._rebuild_stream()
            self.app.processEvents()
            self.assertTrue(any(self.win.stream.itemAt(i).widget() is card
                                for i in range(self.win.stream.count())))
            self.win.worker.stop()
            self.release.set()
            self.wait_for(lambda: card._done)

    def test_send_continuation_really_rebuilds_without_a_routing_api_call(self):
        replies = iter(['- 第1卷 苦练：从头推进', '- 第1章 开端：修炼',
                        '新正文完成', '新的章摘要', '新的卷摘要'])
        systems = []
        def post(cfg, body, timeout):
            systems.append(body['messages'][0]['content'])
            self.assertIn('按原要求突出苦练', body['messages'][1]['content']) if body['messages'][0]['content'] in (prompts.SYSTEM_ASSIST, prompts.SYSTEM_WRITER) else None
            return {'choices': [{'message': {'content': next(replies)}}],
                    'usage': {'completion_tokens': 10}}
        with patch.object(intent, 'decide', side_effect=AssertionError('Must not ask model to route')), \
                patch.object(client, '_post', side_effect=post):
            self.win.composer.setPlainText('继续完成全书重构')
            self.win._send()
            self.wait_for(lambda: self.win._active_task_card and self.win._active_task_card._done)
        run = ledger.get_run(self.db, self.win.worker.task_run_id)
        self.assertEqual(run['status'], 'completed', run['error'])
        self.assertEqual(self.db.get_chapters(self.pid)[0]['content'], '新正文完成')
        self.assertIn(prompts.SYSTEM_WRITER, systems)
        self.assertEqual(len(systems), 5)

    def test_factory_and_dispatch_errors_surface_as_failed_cards_not_started_promises(self):
        with patch.object(execution, 'create_worker', side_effect=RuntimeError('构造失败')):
            self.win._tool_rebuild(self.cfg, {'instruction': '重构'})
        messages = [text for role, text in self.win.history() if role == 'assistant']
        self.assertIn('任务未启动：构造失败', messages[-1])
        self.assertFalse(any('任务已启动' in text for text in messages))
        self.assertTrue(self.win.findChildren(ActivityCard))
        self.assertFalse(self.win._task_busy())
        with patch.object(self.win, '_dispatch', side_effect=RuntimeError('派发失败')):
            self.win._dispatch_guarded(self.win._intent_seq, self.pid, self.cfg, {})
        self.assertIn('任务未启动：派发失败', self.win.history()[-1][1])


if __name__ == '__main__':
    unittest.main()
