# -*- coding: utf-8 -*-
"""Narrative evidence, time boundaries, and backup compatibility."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai import story_memory as sm
from ai.context import context_pack_for
from book_backup import capture_project, restore_project, validate_full_backup
from db import DB
import task_ledger as ledger


class StoryMemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DB(str(Path(self.tmp.name) / 'novel.db'))
        self.pid = self.db.create_project('书甲')
        self.one = self.db.create_chapter(self.pid, 1, 1, '开始',
                                          content='林野此刻在江城。林野偷听到密令。')
        self.two = self.db.create_chapter(self.pid, 1, 2, '发展',
                                          content='林野离开江城。', chapter_card='林野赶路')
        self.ten = self.db.create_chapter(self.pid, 1, 10, '终局',
                                          content='林野死于地牢。')

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def extract(self, cid, name, attr, value, quote, certainty='asserted'):
        payload = json.dumps({'facts': [{'entity': name, 'attribute': attr,
                                         'value': value, 'quote': quote,
                                         'certainty': certainty}]}, ensure_ascii=False)
        return sm.extract_candidates(self.db, cid, lambda *_: payload)

    def test_evidence_time_and_confirmed_only(self):
        self.extract(self.one, '林野', '位置', '江城', '林野此刻在江城。')
        fact = sm.list_facts(self.db, self.pid, self.one)[0]
        self.assertEqual(sm.state_before(self.db, self.pid, self.two), [])
        sm.confirm_fact(self.db, fact['id'])
        self.assertEqual(sm.state_before(self.db, self.pid, self.one), [])
        state = sm.state_before(self.db, self.pid, self.two)
        self.assertEqual([(r['attribute'], r['value']) for r in state], [('位置', '江城')])
        self.assertIn('江城', context_pack_for(self.db, self.db.get_chapter(self.two)))

    def test_future_fact_not_visible_before_source(self):
        self.extract(self.ten, '林野', '身体状态', '死亡', '林野死于地牢。')
        sm.confirm_fact(self.db, sm.list_facts(self.db, self.pid, self.ten)[0]['id'])
        self.assertNotIn('死亡', sm.memory_for_chapter(self.db, self.two)['text'])
        self.assertEqual(sm.state_before(self.db, self.pid, self.two), [])

    def test_changed_body_and_deleted_chapter_make_evidence_stale(self):
        self.extract(self.one, '林野', '位置', '江城', '林野此刻在江城。')
        fid = sm.list_facts(self.db, self.pid)[0]['id']
        sm.confirm_fact(self.db, fid)
        self.db.save_chapter(self.one, '林野此刻在山中。')
        self.assertEqual(sm.state_before(self.db, self.pid, self.two), [])
        self.db.restore_version(self.one, self.db.get_versions(self.one)[0]['id'])
        self.assertEqual(len(sm.state_before(self.db, self.pid, self.two)), 1)
        self.db.delete_chapter(self.one)
        self.assertEqual(sm.state_before(self.db, self.pid, self.two), [])

    def test_bad_quote_and_uncertain_claim_never_auto_confirm(self):
        with self.assertRaises(ValueError):
            self.extract(self.one, '林野', '位置', '不存在', '虚构的证据')
        self.assertEqual(sm.list_facts(self.db, self.pid), [])
        self.extract(self.one, '林野', '秘密知情', '密令', '林野偷听到密令。', 'uncertain')
        fact = sm.list_facts(self.db, self.pid)[0]
        self.assertEqual(fact['status'], 'candidate')
        self.assertEqual(fact['certainty'], 'uncertain')
        self.assertEqual(sm.state_before(self.db, self.pid, self.two), [])

    def test_repeated_quote_requires_unique_evidence(self):
        self.db.save_chapter(self.one, '林野在江城。林野在江城。')
        with self.assertRaisesRegex(ValueError, '证据重复'):
            self.extract(self.one, '林野', '位置', '江城', '林野在江城。')
        self.assertEqual(sm.list_facts(self.db, self.pid), [])

    def test_idempotent_extraction_preserves_rejected_candidate(self):
        calls = []
        payload = '{"facts":[{"entity":"林野","attribute":"位置",'
        payload += '"value":"江城","quote":"林野此刻在江城。","certainty":"asserted"}]}'
        def model(*_):
            calls.append(1)
            return payload
        self.assertEqual(sm.extract_candidates(self.db, self.one, model), 1)
        fid = sm.list_facts(self.db, self.pid)[0]['id']
        sm.reject_fact(self.db, fid)
        self.assertEqual(sm.extract_candidates(self.db, self.one, model), 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(sm.list_facts(self.db, self.pid)[0]['status'], 'rejected')

    def test_conflict_requires_explicit_replace_and_keeps_history(self):
        self.extract(self.one, '林野', '位置', '江城', '林野此刻在江城。')
        old = sm.list_facts(self.db, self.pid)[0]['id']
        sm.confirm_fact(self.db, old)
        self.extract(self.two, '林野', '位置', '山中', '林野离开江城。')
        new = sm.list_facts(self.db, self.pid, self.two)[0]['id']
        with self.assertRaises(sm.MemoryConflict):
            sm.confirm_fact(self.db, new)
        self.assertEqual(sm.list_facts(self.db, self.pid, self.two)[0]['status'], 'conflict')
        sm.confirm_fact(self.db, new, replace=True)
        self.assertEqual(sm.list_facts(self.db, self.pid, self.two)[0]['supersedes_id'], old)
        self.assertEqual(sm.state_before(self.db, self.pid, self.two)[0]['value'], '江城')
        self.assertEqual(sm.state_before(self.db, self.pid, self.ten)[0]['value'], '山中')

    def test_books_entities_aliases_and_rules_are_scoped(self):
        other = self.db.create_project('书乙')
        other_ch = self.db.create_chapter(other, 1, 1, '新章', content='林野从未去过江城。')
        eid = sm.add_entity(self.db, self.pid, '林野')
        sm.add_alias(self.db, self.pid, eid, '阿野')
        sm.add_author_rule(self.db, self.pid, '阿野', '身体状态', '左手旧伤')
        self.assertEqual(sm.state_before(self.db, other, other_ch), [])
        self.assertIn('左手旧伤', sm.memory_for_chapter(self.db, self.two)['text'])
        self.assertEqual(sm.add_entity(self.db, other, '林野') != eid, True)

    def test_author_can_map_a_candidate_alias_to_existing_entity(self):
        existing = sm.add_entity(self.db, self.pid, '林野')
        self.db.save_chapter(self.two, '阿野来到山门。')
        self.extract(self.two, '阿野', '位置', '山门', '阿野来到山门。')
        fid = sm.list_facts(self.db, self.pid, self.two)[0]['id']
        sm.confirm_fact(self.db, fid, entity_id=existing)
        alias = self.db.conn.execute('SELECT entity_id FROM story_aliases WHERE project_id=? AND alias=?',
                                     (self.pid, '阿野')).fetchone()
        self.assertEqual(alias['entity_id'], existing)
        self.assertEqual(sm.list_facts(self.db, self.pid, self.two)[0]['entity_id'], existing)

    def test_reorder_and_duplicate_numbers_use_stable_order(self):
        self.extract(self.one, '林野', '位置', '江城', '林野此刻在江城。')
        sm.confirm_fact(self.db, sm.list_facts(self.db, self.pid)[0]['id'])
        self.db.update_chapter_meta(self.one, chapter_no=2)
        self.db.update_chapter_meta(self.two, chapter_no=1)
        self.assertEqual(sm.state_before(self.db, self.pid, self.two), [])
        self.db.update_chapter_meta(self.two, chapter_no=2)
        # Same chapter number is ordered by stable id.
        self.assertEqual(len(sm.state_before(self.db, self.pid, self.two)), 1)

    def test_backup_restore_delete_and_v1_compatibility(self):
        self.extract(self.one, '林野', '位置', '江城', '林野此刻在江城。')
        fid = sm.list_facts(self.db, self.pid)[0]['id']
        sm.confirm_fact(self.db, fid)
        sm.add_alias(self.db, self.pid, sm.add_entity(self.db, self.pid, '林野'), '阿野')
        before = capture_project(self.db, self.pid)
        self.assertEqual(before['format_version'], 7)
        self.db.delete_project(self.pid)
        restore_project(self.db, before)
        self.assertEqual(capture_project(self.db, self.pid)['tables'], before['tables'])
        old = copy.deepcopy(before)
        old['format_version'] = 1
        for table in ('story_entities', 'story_aliases', 'story_facts', 'story_extractions'):
            old['tables'].pop(table)
        for table in ('rewrite_candidates', 'rewrite_locks'):
            old['tables'].pop(table)
        old['tables'].pop('review_reports')
        old['tables'].pop('style_profiles')
        old['tables'].pop('style_suggestions')
        old['tables'].pop('task_usage')
        old['tables'].pop('setting_candidates')
        for row in old['tables']['chapters']:
            for col in ('memory_pending', 'memory_run_id', 'prefix_summary', 'prefix_hash'):
                row.pop(col, None)
        validate_full_backup(old)
        restore_project(self.db, old)
        self.assertEqual(self.db.get_chapter(self.one)['content'], '林野此刻在江城。林野偷听到密令。')
        self.assertEqual(sm.list_facts(self.db, self.pid), [])

    def test_merge_identical_drafts_preserves_fact_source(self):
        dup = self.db.create_chapter(self.pid, 1, 1, '重复章',
                                     content=self.db.get_chapter(self.one)['content'])
        self.extract(dup, '林野', '位置', '江城', '林野此刻在江城。')
        fid = sm.list_facts(self.db, self.pid, dup)[0]['id']
        sm.confirm_fact(self.db, fid)
        self.db.merge_duplicate_chapters(self.one, [dup])
        self.assertEqual(sm.list_facts(self.db, self.pid)[0]['source_chapter_id'], self.one)
        self.assertEqual(sm.state_before(self.db, self.pid, self.two)[0]['value'], '江城')

    def test_pipeline_produces_candidates_but_never_confirms_them(self):
        from unittest.mock import patch
        from ai.pipeline import PipelineWorker
        from ai import prompts
        cid = self.db.create_chapter(self.pid, 1, 4, '新章', chapter_card='林野拿到铜钥匙')
        self.db.set_setting(sm.AUTO_EXTRACT_SETTING, 'on')
        worker = PipelineWorker({}, self.db.path, self.pid, 1,
                                use_tools=True, review=False, distill_style=False,
                                min_chars=1)
        worker._reset_stats()
        calls = []
        def fake_call(system, *_args, **_kw):
            calls.append(system)
            if system == prompts.SYSTEM_WRITER:
                return '林野握着铜钥匙。'
            if system == prompts.SYSTEM_SUMMARY:
                return '林野得到铜钥匙。'
            if system == prompts.SYSTEM_ROLLUP:
                return '林野得到铜钥匙。'
            if system == sm.SYSTEM_EXTRACT:
                return ('{"facts":[{"entity":"林野","attribute":"持有物",'
                        '"value":"铜钥匙","quote":"林野握着铜钥匙。",'
                        '"certainty":"asserted"}]}')
            raise AssertionError(system)
        worker._call = fake_call
        with patch('ai.tools.register_chapter', return_value=[]):
            worker._do_chapter(self.db, None, 4, '新章', cid)
        self.assertIn(sm.SYSTEM_EXTRACT, calls)
        rows = sm.list_facts(self.db, self.pid, cid)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['status'], 'candidate')
        self.assertEqual(sm.state_before(self.db, self.pid, cid), [])

    def test_automatic_extraction_off_by_default_saves_a_model_call(self):
        from unittest.mock import patch
        from ai.pipeline import PipelineWorker
        from ai import prompts
        cid = self.db.create_chapter(self.pid, 1, 4, '新章', chapter_card='写正文')
        worker = PipelineWorker({}, self.db.path, self.pid, 1, use_tools=True,
                                review=False, distill_style=False, min_chars=1)
        worker._reset_stats()
        calls = []
        def fake(system, *_args, **_kw):
            calls.append(system)
            return '本章正文。' if system == prompts.SYSTEM_WRITER else '摘要。'
        worker._call = fake
        with patch('ai.tools.register_chapter', return_value=[]):
            worker._do_chapter(self.db, None, 4, '新章', cid)
        self.assertNotIn(sm.SYSTEM_EXTRACT, calls)

    def test_confirmed_fact_invalidates_pending_plan_but_candidate_does_not(self):
        original = ledger.book_revision(self.db, self.pid)
        self.extract(self.one, '林野', '位置', '江城', '林野此刻在江城。')
        self.assertEqual(ledger.book_revision(self.db, self.pid), original)
        fid = sm.list_facts(self.db, self.pid)[0]['id']
        sm.confirm_fact(self.db, fid)
        self.assertNotEqual(ledger.book_revision(self.db, self.pid), original)


if __name__ == '__main__':
    unittest.main()
