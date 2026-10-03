# -*- coding: utf-8 -*-
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ai import evidence_review as er, pipeline as pl, prompts, story_memory as sm
from book_backup import capture_project, restore_project
from db import DB


class EvidenceReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = DB(str(Path(self.temp.name) / 't.db'))
        self.pid = self.db.create_project('审稿证据书')
        a = self.db.create_chapter(self.pid, 1, 1, '前情', content='林野左手受伤。')
        self.cid = self.db.create_chapter(self.pid, 1, 2, '正文',
                                          content='林野举起左手持剑。', status='AI草稿')
        sm.extract_candidates(self.db, a, lambda *_:
                              '{"facts":[{"entity":"林野","attribute":"身体状态",'
                              '"value":"左手受伤","quote":"林野左手受伤。",'
                              '"certainty":"asserted"}]}')
        self.fact = sm.list_facts(self.db, self.pid, a)[0]
        sm.confirm_fact(self.db, self.fact['id'])

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def verdict(self, ref=None, source_quote='林野左手受伤。', kind='人物一致性', score=62):
        return json.dumps({'score': score,
                           'categories': {k: score for k in er.CATEGORIES},
                           'issues': [{'kind': kind, 'severity': '严重',
                                      'body_quote': '林野举起左手持剑。',
                                      'source_ref': ref if ref is not None else f"fact:{self.fact['id']}",
                                      'source_quote': source_quote,
                                      'explanation': '左手受伤与动作冲突',
                                      'suggestion': '改为右手持剑'}]}, ensure_ascii=False)

    def test_valid_source_and_quote_are_located(self):
        rid = er.review_saved_chapter(self.db, self.cid, {},
                                      model_call=lambda *_: self.verdict())
        row = er.get_report(self.db, rid)
        self.assertEqual(row['status'], 'reviewed')
        issue = json.loads(row['issues_json'])[0]
        self.assertEqual(issue['body_start'], 0)
        self.assertEqual(issue['source_ref'], f"fact:{self.fact['id']}")
        self.assertEqual(self.db.get_chapter(self.cid)['status'], 'AI草稿·待核')

    def test_fabricated_or_missing_evidence_is_unreviewed(self):
        for raw in (self.verdict('fact:999'), self.verdict(source_quote='不存在的原文'),
                    self.verdict(ref=''), 'not json'):
            rid = er.create_report(self.db, self.cid, raw)
            self.assertEqual(er.get_report(self.db, rid)['status'], 'unreviewed')
            self.assertEqual(self.db.get_chapter(self.cid)['status'], 'AI草稿·未审')

    def test_subjective_suggestion_may_omit_external_source(self):
        raw = self.verdict(ref='', source_quote='', kind='节奏')
        rid = er.create_report(self.db, self.cid, raw)
        self.assertEqual(er.get_report(self.db, rid)['status'], 'reviewed')

    def test_high_score_severe_conflict_needs_author_resolution(self):
        rid = er.create_report(self.db, self.cid, self.verdict(score=90))
        self.assertEqual(self.db.get_chapter(self.cid)['status'], 'AI草稿·待核')
        er.set_issue_state(self.db, rid, 0, 'proposed')
        self.assertEqual(self.db.get_chapter(self.cid)['status'], 'AI草稿·待核')
        er.set_issue_state(self.db, rid, 0, 'not_issue')
        self.assertEqual(self.db.get_chapter(self.cid)['status'], 'AI草稿')

    def test_legacy_score_only_report_is_unreviewed(self):
        rid = er.create_report(self.db, self.cid, '{"score":95,"issues":[]}')
        self.assertEqual(er.get_report(self.db, rid)['status'], 'unreviewed')
        self.assertEqual(self.db.get_chapter(self.cid)['status'], 'AI草稿·未审')

    def test_report_cannot_be_attached_to_a_different_body_version(self):
        old_hash = sm.body_hash(self.db.get_chapter(self.cid)['content'])
        self.db.save_chapter(self.cid, '林野改用右手。')
        with self.assertRaisesRegex(ValueError, '正文版本已变化'):
            er.create_report(self.db, self.cid, self.verdict(score=90),
                             expected_hash=old_hash)
        self.assertIsNone(er.latest_report(self.db, self.cid))

    def test_author_disposition_keeps_original_score_threshold(self):
        rid = er.create_report(self.db, self.cid, self.verdict(score=90), min_score=95)
        er.set_issue_state(self.db, rid, 0, 'ignored')
        self.assertEqual(self.db.get_chapter(self.cid)['status'], 'AI草稿·低分')

    def test_pipeline_prefers_resolved_conflict_over_higher_score(self):
        first = '林野举起左手持剑。'
        revised = '林野改用右手持剑。'
        drafts = [first, revised]
        worker = pl.PipelineWorker({}, self.db.path, self.pid, 1,
                                   force_rewrite=True, gen_card=False,
                                   use_tools=False, review=True,
                                   distill_style=False, min_chars=1)

        def fake_call(system, _user, **_kwargs):
            if system == prompts.SYSTEM_WRITER:
                return drafts.pop(0)
            if system == prompts.SYSTEM_SUMMARY:
                return '林野换手持剑。'
            if system == prompts.SYSTEM_ROLLUP:
                return '林野左手受伤后换右手。'
            raise AssertionError('意外的模型调用')

        def fake_review(_cfg, messages, **_kwargs):
            body = messages[1]['content'].rsplit('【正文】\n', 1)[1].split(
                '\n\n请审稿', 1)[0]
            if body == first:
                return self.verdict(score=90)
            self.assertEqual(body, revised)
            return json.dumps({'score': 85, 'categories': {k: 85 for k in er.CATEGORIES},
                               'issues': []}, ensure_ascii=False)

        worker._call = fake_call
        with patch.object(pl.aiclient, 'chat_once', side_effect=fake_review):
            worker._do_chapter(self.db, None, 2, '正文', self.cid)
        chapter = self.db.get_chapter(self.cid)
        self.assertEqual(chapter['content'], revised)
        self.assertEqual(chapter['status'], 'AI草稿')
        self.assertEqual(er.latest_report(self.db, self.cid)['score'], 85)

    def test_issue_actions_are_scoped_to_current_body(self):
        rid = er.create_report(self.db, self.cid, self.verdict())
        er.set_issue_state(self.db, rid, 0, 'not_issue')
        self.assertEqual(json.loads(er.get_report(self.db, rid)['issues_json'])[0]['state'],
                         'not_issue')
        self.db.save_chapter(self.cid, '林野改用右手。')
        self.assertFalse(er.report_current(self.db, er.get_report(self.db, rid)))
        with self.assertRaises(ValueError):
            er.set_issue_state(self.db, rid, 0, 'ignored')

    def test_backup_restores_reports_and_old_v3_remains_accepted(self):
        er.create_report(self.db, self.cid, self.verdict())
        snapshot = capture_project(self.db, self.pid)
        self.db.delete_project(self.pid)
        restore_project(self.db, snapshot)
        self.assertEqual(capture_project(self.db, self.pid)['tables'], snapshot['tables'])
        old = dict(snapshot)
        old['tables'] = dict(snapshot['tables'])
        old['tables'].pop('review_reports')
        old['tables'].pop('style_profiles')
        old['tables'].pop('style_suggestions')
        old['tables'].pop('task_usage')
        old['tables'].pop('setting_candidates')
        old['format_version'] = 3
        restore_project(self.db, old)
        self.assertIsNone(er.latest_report(self.db, self.cid))

    def test_issue_revision_creates_candidate_without_changing_manuscript(self):
        from unittest.mock import patch
        from ui.evidence_review_dialog import IssueRevisionWorker
        from ai import rewrite_candidates as rw
        report_id = er.create_report(self.db, self.cid, self.verdict())
        issue = json.loads(er.get_report(self.db, report_id)['issues_json'])[0]
        original = self.db.get_chapter(self.cid)['content']
        worker = IssueRevisionWorker(self.db.path, self.cid, {}, issue)
        ids, errors = [], []
        worker.done.connect(ids.append)
        worker.failed.connect(errors.append)
        with patch('ai.client.simple_chat', return_value='林野改用右手持剑。'):
            worker.run()
        self.assertEqual(errors, [])
        self.assertEqual(len(ids), 1)
        self.assertEqual(self.db.get_chapter(self.cid)['content'], original)
        self.assertEqual(rw.get_candidate(self.db, ids[0])['mode'], 'review_issue')


if __name__ == '__main__':
    unittest.main()
