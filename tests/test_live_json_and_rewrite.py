"""Live audit regressions: JSON options, advisory evidence, identical rewrites."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from ai import client, evidence_review, review, rewrite_candidates, story_memory
from ai.pipeline import PipelineWorker
from db import DB


MIMO = dict(name='mimo', provider='openai_compat', base_url='https://api.xiaomimimo.com/v1',
            api_key='fake', model='mimo-v2.6-flash')


class LiveJsonAndRewriteTests(unittest.TestCase):
    def test_repeated_fact_quote_is_repaired_once_with_specific_feedback(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = DB(str(Path(tmp) / 'synthetic.db'))
            try:
                pid = db.create_project('证据返修')
                cid = db.create_chapter(pid, 1, 1, '第一章', content='咔嚓。陈桥走进钟室。咔嚓。')
                def payload(quote):
                    return json.dumps({'facts': [dict(entity='陈桥', attribute='位置',
                        value='钟室', quote=quote, certainty='asserted')]}, ensure_ascii=False)
                with patch('builtins.print'), patch.object(story_memory, '_now', return_value='2026-10-03'), \
                        patch.object(client, '_log_api'):
                    call = unittest.mock.Mock(side_effect=[payload('咔嚓。'), payload('陈桥走进钟室。')])
                    self.assertEqual(story_memory.extract_candidates(db, cid, call), 1)
                self.assertEqual(call.call_count, 2)
                self.assertIn('出现 2 次', call.call_args.args[1])
                rows = story_memory.list_facts(db, pid, cid)
                self.assertEqual(rows[0]['quote'], '陈桥走进钟室。')
                self.assertEqual(rows[0]['status'], 'candidate')
            finally:
                db.close()

    def test_documented_json_mode_is_added_only_to_known_direct_mimo(self):
        sent = []
        with patch.object(client, '_post', side_effect=lambda cfg, body, timeout:
                (sent.append(body) or {'choices': [{'message': {'content': '{"ok": true}'}}]})):
            client.chat_once(MIMO, [], **client.structured_output_options(MIMO))
            other = dict(MIMO, base_url='https://example.invalid/v1')
            client.chat_once(other, [], **client.structured_output_options(other))
        self.assertEqual(sent[0]['response_format'], {'type': 'json_object'})
        self.assertNotIn('response_format', sent[1])
        self.assertEqual(client.structured_output_options(dict(MIMO, model='mimo-unknown')), {})

    def test_review_requests_json_mode_without_claiming_invalid_evidence_was_checked(self):
        with patch.object(client, 'chat_once', return_value='not json') as chat:
            logs = []
            self.assertEqual(review.review_chapter(MIMO, '上下文', '正文', sources={}, log=logs.append), (None, []))
        self.assertEqual(chat.call_args.kwargs['response_format'], {'type': 'json_object'})
        self.assertIn('审稿输出不是有效 JSON', logs[-1])

    def test_advisory_causality_may_omit_source_but_unproven_conflict_may_not(self):
        payload = dict(score=86, categories={key: 86 for key in evidence_review.CATEGORIES},
            issues=[dict(kind='人物一致性', severity='建议', body_quote='陈桥拿起钟锤。',
                         source_ref='', source_quote='', explanation='建议动作写得更清楚',
                         suggestion='增加动作细节')])
        self.assertEqual(evidence_review.parse_report(json.dumps(payload), '陈桥拿起钟锤。', {})['score'], 86)
        for severity in ('一般', '严重'):
            payload['issues'][0]['severity'] = severity
            with self.assertRaisesRegex(ValueError, '客观冲突'):
                evidence_review.parse_report(json.dumps(payload), '陈桥拿起钟锤。', {})

    def test_identical_rewrite_is_retried_once_with_feedback_then_saved_as_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = DB(str(Path(tmp) / 'synthetic.db'))
            try:
                pid = db.create_project('候选验证')
                cid = db.create_chapter(pid, 1, 1, '第一章', content='旧稿内容')
                worker = PipelineWorker(MIMO, db.path, pid, 1, force_rewrite=True,
                    candidate_mode=True, review=False, gen_card=False, min_chars=1,
                    rewrite_instruction='明确调查线索')
                with patch.object(worker, '_call', side_effect=['旧稿内容', '调查线索变清楚的新稿']) as call:
                    outcome = worker._do_chapter(db, None, 1, '第一章', cid)
                self.assertEqual(outcome[0], '候选稿')
                self.assertEqual(call.call_count, 2)
                self.assertIn('上一轮验收反馈', call.call_args.args[1])
                self.assertEqual(db.get_chapter(cid)['content'], '旧稿内容')
                self.assertEqual(len(rewrite_candidates.list_candidates(db, cid)), 1)
            finally:
                db.close()

    def test_two_identical_rewrites_leave_manuscript_and_candidate_table_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = DB(str(Path(tmp) / 'synthetic.db'))
            try:
                pid = db.create_project('候选验证')
                cid = db.create_chapter(pid, 1, 1, '第一章', content='旧稿内容')
                worker = PipelineWorker(MIMO, db.path, pid, 1, force_rewrite=True,
                    candidate_mode=True, review=False, gen_card=False, min_chars=1)
                with patch.object(worker, '_call', return_value='旧稿内容') as call:
                    with self.assertRaisesRegex(RuntimeError, '重复返回旧稿'):
                        worker._do_chapter(db, None, 1, '第一章', cid)
                self.assertEqual(call.call_count, 2)
                self.assertEqual(db.get_chapter(cid)['content'], '旧稿内容')
                self.assertEqual(rewrite_candidates.list_candidates(db, cid), [])
            finally:
                db.close()


if __name__ == '__main__':
    unittest.main()
