# -*- coding: utf-8 -*-
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ai import style_profile as sp
from ai.context import context_pack_for
from ai.pipeline import PipelineWorker
from ai import prompts
from book_backup import capture_project, restore_project
from db import DB


class StyleProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DB(str(Path(self.tmp.name) / 'style.db'))
        self.pid = self.db.create_project('悬疑书', style_sheet='旧作者规则：避免排比')
        self.other = self.db.create_project('冒险书')

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_book_scoping_and_legacy_style_preserved(self):
        sp.save_profile(self.db, self.pid, {'point_of_view': '第一人称',
                                            'pacing': '慢节奏', 'banned': '网感梗'})
        result = sp.effective_style(self.db, self.pid)
        self.assertIn('第一人称', result)
        self.assertIn('慢节奏', result)
        self.assertIn('避免排比', result)
        self.assertNotIn('第一人称', sp.effective_style(self.db, self.other))
        self.assertEqual(self.db.get_project(self.pid)['style_sheet'], '旧作者规则：避免排比')

    def test_suggestion_only_effective_after_author_accepts(self):
        sid = sp.add_suggestion(self.db, self.pid, '多用动作推进', '审稿指出说明过多',
                                '他推开门，冲进雨里。')
        self.assertNotIn('多用动作推进', sp.effective_style(self.db, self.pid))
        self.assertEqual(sp.add_suggestion(self.db, self.pid, '多用动作推进',
                                            '重复', '重复示例'), sid)
        sp.decide_suggestion(self.db, self.pid, sid, True)
        self.assertIn('多用动作推进', sp.effective_style(self.db, self.pid))
        other = sp.add_suggestion(self.db, self.pid, '全书都用排比', '模型建议', '示例')
        sp.decide_suggestion(self.db, self.pid, other, False)
        self.assertNotIn('全书都用排比', sp.effective_style(self.db, self.pid))

    def test_revision_and_task_snapshot_avoid_mid_run_style_change(self):
        rev = sp.save_profile(self.db, self.pid, {'point_of_view': '第一人称'})
        self.assertEqual(rev, 1)
        self.assertEqual(sp.save_profile(self.db, self.pid, {'point_of_view': '第一人称'}), 1)
        cid = self.db.create_chapter(self.pid, 1, 1, '开篇', chapter_card='主角醒来')
        worker = PipelineWorker({}, self.db.path, self.pid, 1,
                                use_tools=False, review=False, distill_style=False,
                                min_chars=1)
        worker._reset_stats()
        worker._style_snapshot = sp.effective_style(self.db, self.pid)
        sp.save_profile(self.db, self.pid, {'point_of_view': '第三人称'})
        seen = []
        def fake(system, user, **_kw):
            if system == prompts.SYSTEM_WRITER:
                seen.append(user)
                return '正文'
            return '摘要'
        worker._call = fake
        worker._do_chapter(self.db, None, 1, '开篇', cid)
        self.assertEqual(len(seen), 1)
        self.assertIn('第一人称', seen[0])
        self.assertNotIn('第三人称', seen[0])
        self.assertIn('第三人称', context_pack_for(self.db, self.db.get_chapter(cid)))

    def test_backup_and_v4_restore(self):
        sp.save_profile(self.db, self.pid, {'genre': '悬疑', 'rules': '不得暴露凶手'})
        sp.add_suggestion(self.db, self.pid, '少用解释', '依据', '他转身离开。')
        snapshot = capture_project(self.db, self.pid)
        self.db.delete_project(self.pid)
        restore_project(self.db, snapshot)
        self.assertEqual(capture_project(self.db, self.pid)['tables'], snapshot['tables'])
        old = dict(snapshot)
        old['tables'] = dict(snapshot['tables'])
        old['tables'].pop('style_profiles')
        old['tables'].pop('style_suggestions')
        old['tables'].pop('task_usage')
        old['tables'].pop('setting_candidates')
        old['format_version'] = 4
        restore_project(self.db, old)
        self.assertIsNone(sp.get_profile(self.db, self.pid))
        self.assertEqual(self.db.get_project(self.pid)['style_sheet'], '旧作者规则：避免排比')


if __name__ == '__main__':
    unittest.main()
