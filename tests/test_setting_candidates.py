"""Model settings stay provisional until the author checks source evidence."""
import copy
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from ai import setting_candidates as candidates, tools
from ai.context import context_pack_for
from book_backup import capture_project, restore_project
from db import DB


class SettingCandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = DB(str(Path(self.temp.name) / 'book.db'))
        self.pid = self.db.create_project('设定候选书')
        self.first = self.db.create_chapter(
            self.pid, 1, 1, '开篇', content='林野看见血纹亮起，左手随即失去知觉。')
        self.second = self.db.create_chapter(
            self.pid, 1, 2, '后续', chapter_card='再次使用血纹')
        self.quote = '血纹亮起，左手随即失去知觉。'
        self.definition = '血纹启动会让使用者左手暂时失去知觉。'

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def propose(self, term='血纹'):
        return candidates.propose_candidate(
            self.db, self.first, '力量体系', term, self.definition, self.quote)

    def test_unconfirmed_setting_is_not_a_writing_constraint(self):
        cid = self.propose()
        self.assertEqual(self.db.get_settings(self.pid), [])
        self.assertNotIn(self.definition, context_pack_for(
            self.db, self.db.get_chapter(self.second)))
        setting_id = candidates.confirm_candidate(self.db, cid)
        self.assertEqual(self.db.get_settings(self.pid)[0]['id'], setting_id)
        self.assertIn(self.definition, context_pack_for(
            self.db, self.db.get_chapter(self.second)))
        self.assertEqual(candidates.get_candidate(self.db, cid)['status'], 'accepted')

    def test_quote_must_be_unique_and_current_before_confirmation(self):
        with self.assertRaisesRegex(ValueError, '逐字证据'):
            candidates.propose_candidate(
                self.db, self.first, '力量体系', '血纹', self.definition, '不存在')
        cid = self.propose()
        self.db.save_chapter(self.first, '林野再也没有看见血纹。')
        self.assertFalse(candidates.valid_source(self.db, candidates.get_candidate(self.db, cid)))
        self.assertEqual(candidates.get_candidate(self.db, cid)['status'], 'stale')
        with self.assertRaisesRegex(ValueError, '正文已变化'):
            candidates.confirm_candidate(self.db, cid)
        self.assertEqual(self.db.get_settings(self.pid), [])

    def test_deleted_chapter_keeps_historic_candidate_without_blocking_backup(self):
        cid = self.propose()
        self.db.delete_chapter(self.first)
        self.assertEqual(candidates.get_candidate(self.db, cid)['status'], 'stale')
        capture_project(self.db, self.pid)

    def test_rejected_candidate_never_enters_setting_dict(self):
        cid = self.propose()
        candidates.reject_candidate(self.db, cid)
        self.assertEqual(candidates.get_candidate(self.db, cid)['status'], 'rejected')
        self.assertEqual(self.db.get_settings(self.pid), [])

    def test_registrar_tool_creates_candidate_with_required_quote(self):
        spec = next(item for item in tools.TOOLS_SPEC
                    if item['function']['name'] == 'add_setting')
        self.assertIn('quote', spec['function']['parameters']['required'])
        args = {'category': '力量体系', 'term': '血纹',
                'definition': self.definition, 'quote': self.quote}
        result = tools._exec_add_setting(self.db, self.pid, args, self.first)
        self.assertIn('候选已记录', result)
        self.assertEqual(self.db.get_settings(self.pid), [])
        self.assertEqual(len(candidates.list_candidates(self.db, self.pid)), 1)

    def test_backup_restores_candidates_and_accepts_v6_snapshot(self):
        cid = self.propose()
        snapshot = capture_project(self.db, self.pid)
        self.assertEqual(snapshot['format_version'], 7)
        self.assertEqual(len(snapshot['tables']['setting_candidates']), 1)
        candidates.reject_candidate(self.db, cid)
        restore_project(self.db, snapshot)
        self.assertEqual(candidates.get_candidate(self.db, cid)['status'], 'candidate')

        old = copy.deepcopy(snapshot)
        old['format_version'] = 6
        old['tables'].pop('setting_candidates')
        old.pop('setting_candidates')
        restore_project(self.db, old)
        self.assertEqual(candidates.list_candidates(self.db, self.pid), [])

    def test_author_can_confirm_from_review_dialog(self):
        from PySide6.QtWidgets import QApplication
        from ui.setting_candidates_dialog import SettingCandidatesDialog
        app = QApplication.instance() or QApplication([])
        self.propose()
        dlg = SettingCandidatesDialog(self.db, self.first)
        self.assertEqual(dlg.table.rowCount(), 1)
        dlg.table.selectRow(0)
        dlg._confirm()
        self.assertEqual(self.db.get_settings(self.pid)[0]['term'], '血纹')
        dlg.close()
        self.assertIsNotNone(app)


if __name__ == '__main__':
    unittest.main()
