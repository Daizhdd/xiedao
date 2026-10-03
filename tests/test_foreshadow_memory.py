# -*- coding: utf-8 -*-
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ai import foreshadow_memory as fm
from ai import tools as atools
from ai.context import context_pack_for
from book_backup import capture_project, restore_project
from db import DB


class ForeshadowEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DB(str(Path(self.tmp.name) / 'f.db'))
        self.pid = self.db.create_project('伏笔书')
        self.one = self.db.create_chapter(self.pid, 1, 1, '初见',
                                          content='林野把铜钥匙藏进靴中。')
        self.two = self.db.create_chapter(self.pid, 1, 2, '转折',
                                          content='林野发现门上有钥匙孔。',
                                          chapter_card='铜钥匙与门的秘密')
        self.five = self.db.create_chapter(self.pid, 1, 5, '回收',
                                           content='林野用铜钥匙打开了门。')
        self.fid = self.db.add_foreshadow(self.pid, '铜钥匙能打开密室门',
                                          planted_ch='第1章', plan_ch='第4章')

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_legacy_unverified_entry_not_injected_as_fact(self):
        self.assertEqual(fm.visible_before(self.db, self.pid, self.two), [])
        self.assertNotIn('铜钥匙能打开密室门',
                         context_pack_for(self.db, self.db.get_chapter(self.two)))

    def test_evidence_time_and_overdue_reminder(self):
        fm.record_plant(self.db, self.fid, self.one, '林野把铜钥匙藏进靴中。')
        self.assertEqual(fm.visible_before(self.db, self.pid, self.one), [])
        self.assertEqual(len(fm.visible_before(self.db, self.pid, self.two)), 1)
        self.assertIn('铜钥匙能打开密室门',
                      context_pack_for(self.db, self.db.get_chapter(self.two)))
        self.assertEqual(fm.reminders(self.db, self.pid, self.five)[0]['state'], '逾期')
        self.db.update_foreshadow(self.fid, plan_ch='第5章', plan_from=5, plan_to=5)
        self.assertEqual(fm.reminders(self.db, self.pid, self.five)[0]['state'], '到期')

    def test_early_reveal_is_advisory_and_plan_changes_recompute(self):
        fm.record_plant(self.db, self.fid, self.one, '林野把铜钥匙藏进靴中。')
        self.db.update_chapter_meta(self.two,
                                    content='林野想到：铜钥匙能打开密室门。')
        self.assertEqual(fm.reminders(self.db, self.pid, self.two)[0]['state'],
                         '疑似提前揭底')
        self.db.update_foreshadow(self.fid, plan_ch='第2-3章')
        row = self.db.get_foreshadow(self.fid)
        self.assertEqual((row['plan_from'], row['plan_to']), (2, 3))
        self.assertEqual(fm.reminders(self.db, self.pid, self.two)[0]['state'], '到期')

    def test_resolution_is_candidate_until_author_confirms(self):
        fm.record_plant(self.db, self.fid, self.one, '林野把铜钥匙藏进靴中。')
        fm.propose_resolution(self.db, self.fid, self.five, '林野用铜钥匙打开了门。')
        self.assertEqual(self.db.get_foreshadow(self.fid)['status'], '疑似回收')
        self.assertEqual(len(fm.visible_before(self.db, self.pid, self.five)), 1)
        fm.confirm_resolution(self.db, self.fid)
        later = self.db.create_chapter(self.pid, 1, 6, '之后', content='门后有人。')
        self.assertEqual(fm.visible_before(self.db, self.pid, later), [])

    def test_rejected_same_version_cannot_be_proposed_again(self):
        fm.propose_resolution(self.db, self.fid, self.five, '林野用铜钥匙打开了门。')
        fm.reject_resolution(self.db, self.fid)
        with self.assertRaisesRegex(ValueError, '已驳回'):
            fm.propose_resolution(self.db, self.fid, self.five, '林野用铜钥匙打开了门。')
        self.db.save_chapter(self.five, '林野最终用铜钥匙打开了门。')
        fm.propose_resolution(self.db, self.fid, self.five, '林野最终用铜钥匙打开了门。')
        self.assertEqual(self.db.get_foreshadow(self.fid)['status'], '疑似回收')

    def test_edited_source_is_marked_unverified(self):
        fm.record_plant(self.db, self.fid, self.one, '林野把铜钥匙藏进靴中。')
        self.db.save_chapter(self.one, '林野丢掉了铜钥匙。')
        self.assertFalse(fm.source_valid(self.db, self.db.get_foreshadow(self.fid), 'planted'))
        self.assertEqual(fm.visible_before(self.db, self.pid, self.two), [])

    def test_tool_only_proposes_evidence_backed_resolution(self):
        result = atools._exec_resolve_foreshadow(
            self.db, self.pid, {'foreshadow_id': self.fid,
                                'quote': '林野用铜钥匙打开了门。'}, chapter_id=self.five)
        self.assertTrue(result.startswith('疑似回收'))
        self.assertNotEqual(self.db.get_foreshadow(self.fid)['status'], '已回收')
        other = self.db.create_project('别人的书')
        self.assertIn('不属于本书', atools._exec_resolve_foreshadow(
            self.db, other, {'foreshadow_id': self.fid}, chapter_id=self.five))

    def test_direct_status_edit_cannot_bypass_resolution_evidence(self):
        with self.assertRaisesRegex(ValueError, '有效回收引文'):
            self.db.update_foreshadow(self.fid, status='已回收')
        self.assertEqual(self.db.get_foreshadow(self.fid)['status'], '待回收')

    def test_backup_keeps_evidence_and_old_v3_restores(self):
        fm.record_plant(self.db, self.fid, self.one, '林野把铜钥匙藏进靴中。')
        payload = capture_project(self.db, self.pid)
        self.db.delete_project(self.pid)
        restore_project(self.db, payload)
        self.assertEqual(capture_project(self.db, self.pid)['tables'], payload['tables'])
        old = dict(payload)
        old['tables'] = dict(payload['tables'])
        old['format_version'] = 3
        old['tables'].pop('review_reports')
        old['tables'].pop('style_profiles')
        old['tables'].pop('style_suggestions')
        old['tables'].pop('task_usage')
        old['tables'].pop('setting_candidates')
        for f in old['tables']['foreshadows']:
            for key in ('planted_chapter_id', 'planted_hash', 'planted_quote', 'planted_pos',
                        'resolve_chapter_id', 'resolve_hash', 'resolve_quote', 'resolve_pos',
                        'rejected_hash', 'entity_id', 'plan_from', 'plan_to'):
                f.pop(key)
        restore_project(self.db, old)
        self.assertEqual(self.db.get_foreshadow(self.fid)['content'], '铜钥匙能打开密室门')


if __name__ == '__main__':
    unittest.main()
