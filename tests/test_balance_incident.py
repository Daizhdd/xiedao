"""Offline replay of the October 3 MiMo -> DeepSeek balance failure."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from ai import client, fallback, prefs, prompts
from ai.pipeline import PipelineWorker
from ai.usage_budget import UsageBudget
from db import DB
import task_execution as execution


MIMO = dict(name='mimo', provider='openai_compat',
            base_url='https://api.xiaomimimo.com/v1', api_key='fake',
            model='mimo-v2.6-flash')
BACKUP = dict(name='小鲸鱼小说', provider='deepseek',
              base_url='https://api.deepseek.com/v1', api_key='fake',
              model='deepseek-chat')


def response(text, used=20):
    return {'choices': [{'message': {'content': text}}],
            'usage': {'completion_tokens': used}}


class BalanceIncidentTests(unittest.TestCase):
    def setUp(self):
        fallback.reset()
        self.logs = patch.object(client, '_log_api')
        self.logs.start()

    def tearDown(self):
        patch.stopall()
        fallback.reset()

    def test_http_balance_error_is_structured_for_all_client_entry_points(self):
        for entry in ('chat_once', 'chat_stream', 'chat_once_tools'):
            with self.subTest(entry=entry):
                error = HTTPError(BACKUP['base_url'], 402, 'Payment Required', {},
                                  io.BytesIO(json.dumps({'error': {
                                      'message': 'Insufficient Balance'}}).encode()))
                with patch.object(client, '_resolve'), patch(
                        'ai.client.urllib.request.urlopen', side_effect=error):
                    args = (BACKUP, [], []) if entry == 'chat_once_tools' else (BACKUP, [])
                    with self.assertRaises(client.ModelHTTPError) as caught:
                        getattr(client, entry)(*args)
                exc = caught.exception
                self.assertEqual(exc.status_code, 402)
                self.assertFalse(exc.retryable)
                self.assertIn('账户余额不足', str(exc))
                self.assertIn('小鲸鱼小说', str(exc))
                self.assertIn('api.deepseek.com', str(exc))
                self.assertNotIn('fake', str(exc))

    def test_automatic_planning_disables_thinking_and_respects_explicit_override(self):
        worker = PipelineWorker(MIMO, 'unused.db', 1, 1)
        sent = []
        def post(cfg, body, _timeout):
            sent.append(body)
            return response('规划草案')
        with patch.object(client, '_post', side_effect=post):
            worker._call_chain(MIMO, prompts.SYSTEM_ASSIST, '规划六卷', 0.8, 2)
            worker._prefs = dict(prefs.DEFAULT_PREFS, thinking='always_on')
            worker._call_chain(MIMO, prompts.SYSTEM_ASSIST, '规划六卷', 0.8, 2)
        self.assertEqual(sent[0]['thinking'], {'type': 'disabled'})
        self.assertNotIn('thinking', sent[1])
        self.assertEqual(sent[0]['max_completion_tokens'], 8192)

    def test_rebuild_failure_preserves_original_book_and_both_causes(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = DB(str(Path(tmp) / 'synthetic.db'))
            try:
                pid = db.create_project('合成作品', plan_volumes=1)
                cid = db.create_chapter(pid, 1, 1, '旧正文', content='必须保留的旧正文')
                db.add_ai_config(**dict(name=BACKUP['name'], provider=BACKUP['provider'],
                                       base_url=BACKUP['base_url'], api_key='fake',
                                       model=BACKUP['model'], is_default=0))
                worker = PipelineWorker(MIMO, db.path, pid, 1, rebuild=True, whole_book=True)
                execution.start_task(db, worker, '合成重构')
                attempted, failed = [], []
                worker.failed.connect(failed.append)
                def post(cfg, body, _timeout):
                    attempted.append(cfg['name'])
                    if cfg['name'] == 'mimo':
                        return response('', 8192)
                    raise client.ModelHTTPError(cfg, 402, 'Insufficient Balance')
                with patch.object(client, '_post', side_effect=post), patch('ai.pipeline.traceback.print_exc'):
                    worker.run()
                self.assertEqual(attempted, ['mimo', '小鲸鱼小说'])
                self.assertEqual(db.get_chapter(cid)['content'], '必须保留的旧正文')
                self.assertEqual(len(db.get_chapters(pid)), 1)
                self.assertIn('默认模型「mimo」', failed[0])
                self.assertIn('未返回正文', failed[0])
                self.assertIn('备用模型「小鲸鱼小说」', failed[0])
                self.assertIn('账户余额不足', failed[0])
                self.assertEqual(worker._budget.snapshot()['unknown_calls'], 0)
            finally:
                db.close()

    def test_rejection_releases_tokens_for_next_backup_within_task_cap(self):
        third = dict(BACKUP, name='可用备用', model='available')
        worker = PipelineWorker(MIMO, 'unused.db', 1, 1)
        worker._budget = UsageBudget(output_cap=16384)
        worker._fallback_configs = [MIMO, BACKUP, third]
        attempted = []
        def post(cfg, body, _timeout):
            attempted.append(cfg['name'])
            if cfg['name'] == 'mimo':
                return response('', 8192)
            if cfg['name'] == '小鲸鱼小说':
                raise client.ModelHTTPError(cfg, 402, 'Insufficient Balance')
            return response('成功生成大纲', 100)
        with patch.object(client, '_post', side_effect=post):
            self.assertEqual(worker._call(prompts.SYSTEM_ASSIST, '大纲'), '成功生成大纲')
        self.assertEqual(attempted, ['mimo', '小鲸鱼小说', '可用备用'])
        snap = worker._budget.snapshot()
        self.assertEqual(snap['calls'], 3)
        self.assertEqual(snap['unknown_calls'], 0)
        self.assertEqual(snap['reserved_completion'], 8292)
        self.assertEqual(snap['completion_tokens'], 8292)

    def test_permanent_errors_do_not_retry_but_server_errors_do(self):
        worker = PipelineWorker(BACKUP, 'unused.db', 1, 1)
        for status, count in ((400, 1), (401, 1), (402, 1), (403, 1), (500, 3)):
            with self.subTest(status=status), patch.object(client, '_post', side_effect=
                    client.ModelHTTPError(BACKUP, status, 'offline')) as post:
                with self.assertRaises(client.ModelHTTPError):
                    worker._call_chain(BACKUP, prompts.SYSTEM_ASSIST, '草案', 0.8, 2)
                self.assertEqual(post.call_count, count)

    def test_permanent_default_rejection_uses_backup_on_first_failure(self):
        attempts = []
        def run(cfg):
            attempts.append(cfg['name'])
            if cfg['name'] == 'mimo':
                raise client.ModelHTTPError(cfg, 402, 'Insufficient Balance')
            return '备用成功'
        result = fallback.guard('审稿', MIMO, run, configs=[MIMO, BACKUP])
        self.assertEqual(result, '备用成功')
        self.assertEqual(attempts, ['mimo', '小鲸鱼小说'])


if __name__ == '__main__':
    unittest.main()
