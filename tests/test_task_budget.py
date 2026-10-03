# -*- coding: utf-8 -*-
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ai import client, prompts
from ai.usage_budget import UsageBudget, BudgetPaused
from db import DB
import task_ledger as ledger


CFG = {'name': 'fake', 'provider': 'openai_compat',
       'base_url': 'https://example.invalid/v1', 'api_key': 'fake',
       'model': 'fake'}


class UsageBudgetTests(unittest.TestCase):
    def test_known_usage_does_not_double_count_reasoning(self):
        budget = UsageBudget(max_calls=2, output_cap=1000)
        replies = []
        def fake_post(_cfg, body, _timeout):
            replies.append(body['max_tokens'])
            return {'choices': [{'message': {'content': '正文'}}],
                    'usage': {'prompt_tokens': 40, 'completion_tokens': 120,
                              'completion_tokens_details': {'reasoning_tokens': 20}}}
        with patch.object(client, '_post', side_effect=fake_post):
            self.assertEqual(client.chat_once(CFG, [{'role': 'user', 'content': '测试'}],
                                              max_tokens=500, budget=budget), '正文')
        snap = budget.snapshot()
        self.assertEqual(replies, [500])
        self.assertEqual((snap['calls'], snap['prompt_tokens'],
                          snap['completion_tokens'], snap['reasoning_tokens']),
                         (1, 40, 120, 20))
        self.assertEqual(snap['reserved_completion'], 120)

    def test_unknown_usage_keeps_reservation_and_cap_blocks_next_request(self):
        budget = UsageBudget(max_calls=3, output_cap=500)
        calls = []
        with patch.object(client, '_post', side_effect=lambda *_:
                          (calls.append(1) or {'choices': [{'message': {'content': '正文'}}]})):
            client.chat_once(CFG, [{'role': 'user', 'content': '测试'}],
                             max_tokens=500, budget=budget)
            with self.assertRaises(BudgetPaused):
                client.chat_once(CFG, [{'role': 'user', 'content': '再来'}],
                                 max_tokens=500, budget=budget)
        self.assertEqual(len(calls), 1)
        self.assertEqual(budget.snapshot()['unknown_calls'], 1)
        self.assertEqual(budget.snapshot()['reserved_completion'], 500)

    def test_call_cap_and_tool_calls_share_same_budget(self):
        budget = UsageBudget(max_calls=1)
        with patch.object(client, '_post', return_value={
                'choices': [{'message': {'content': '完成', 'tool_calls': []}}],
                'usage': {'completion_tokens': 15}}):
            client.chat_once_tools(CFG, [{'role': 'user', 'content': '登记'}], [],
                                   budget=budget)
            with self.assertRaises(BudgetPaused):
                client.chat_once(CFG, [{'role': 'user', 'content': '继续'}], budget=budget)
        self.assertEqual(budget.snapshot()['calls'], 1)

    def test_task_phase_attempts_and_usage_survive_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / 'task.db')
            db = DB(path)
            ledger.initialize(db)
            pid = db.create_project('任务书')
            run = ledger.create_run(db, pid, '写第一卷', 'write_volume', {'vol': 1})
            ledger.record_phase(db, run, 10, '章摘要', 'running', input_hash='h1')
            ledger.record_phase(db, run, 10, '章摘要', 'failed', error_kind='TimeoutError')
            ledger.record_phase(db, run, 10, '章摘要', 'running', input_hash='h1')
            ledger.record_phase(db, run, 10, '章摘要', 'completed', output_ref='digest')
            usage = UsageBudget(max_calls=2)
            asked = usage.reserve(1000)
            usage.finish(None, asked)
            ledger.save_usage(db, run, pid, usage.snapshot())
            ledger.update_run(db, run, 'partial')
            db.close()
            db = DB(path)
            rows = db.conn.execute('SELECT * FROM task_steps WHERE run_id=?', (run,)).fetchall()
            self.assertEqual(rows[0]['attempts'], 2)
            self.assertEqual(rows[0]['status'], 'completed')
            self.assertEqual(rows[0]['output_ref'], 'digest')
            self.assertEqual(ledger.get_usage(db, run)['unknown_calls'], 1)
            self.assertEqual([r['id'] for r in ledger.list_recoverable(db, pid)], [run])
            db.close()

    def test_only_retry_summary_never_calls_writer(self):
        from ui.task_recovery_dialog import RetryWorker
        from ai.pipeline import PipelineWorker
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / 'retry.db')
            db = DB(path)
            pid = db.create_project('补摘要书')
            cid = db.create_chapter(pid, 1, 1, '第一章', content='已经采纳的正文')
            worker = RetryWorker(path, cid, CFG, 'summary')
            done = []
            worker.done.connect(done.append)
            def fake(system, *_args, **_kw):
                if system == prompts.SYSTEM_SUMMARY:
                    return '新摘要'
                if system == prompts.SYSTEM_ROLLUP:
                    return '新卷摘要'
                raise AssertionError('不得重新生成正文')
            with patch.object(PipelineWorker, '_call', side_effect=fake):
                worker.run()
            self.assertEqual(db.get_chapter(cid)['content'], '已经采纳的正文')
            self.assertEqual(db.get_chapter(cid)['summary'], '新摘要')
            self.assertTrue(done)
            db.close()

    def test_pipeline_pauses_after_call_limit_and_keeps_saved_body(self):
        from ai.pipeline import PipelineWorker
        with tempfile.TemporaryDirectory() as tmp:
            db = DB(str(Path(tmp) / 'budget.db'))
            ledger.initialize(db)
            pid = db.create_project('预算书')
            db.add_outline(pid, '章纲', '第1章 开始', '写开场', volume=1)
            db.set_setting('task_budget.max_calls', 1)
            worker = PipelineWorker(CFG, db.path, pid, 1, use_tools=False,
                                    review=False, distill_style=False, min_chars=1)
            mode, args = ledger.worker_spec(worker)
            run = ledger.create_run(db, pid, '写一章', mode, args)
            worker.task_run_id = run
            finished, requests = [], []
            worker.finished_ok.connect(finished.append)
            def fake_post(_cfg, body, _timeout):
                requests.append(body)
                return {'choices': [{'message': {'content': '预算内正文'}}],
                        'usage': {'prompt_tokens': 50, 'completion_tokens': 30}}
            with patch.object(client, '_post', side_effect=fake_post):
                worker.run()
            chapter = db.get_chapters(pid)[0]
            self.assertEqual(chapter['content'], '预算内正文')
            self.assertEqual(chapter['summary'], '')
            self.assertEqual(chapter['memory_pending'], 1)
            self.assertEqual(len(requests), 1)
            self.assertTrue(finished[0].startswith('预算已用尽'))
            self.assertEqual(ledger.get_usage(db, run)['completion_tokens'], 30)
            db.close()


if __name__ == '__main__':
    unittest.main()
