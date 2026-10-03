"""Synthetic desktop checks for the bookroom navigation and editor layout."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from ai import setting_candidates
from db import DB
from ui.chat_window import ChatWindow, ActivityCard
from ui.editor_dialog import ChapterEditorDialog
from ui.project_tree import ProjectTree, KIND_PROJECT, KIND_CHAPTER


class WorkspaceLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = DB(str(Path(self.temp.name) / 'book.db'))
        self.pid = self.db.create_project('长夜行舟', genre='悬疑',
                                          plan_volumes=2, plan_chapters=10)
        self.other = self.db.create_project('另一部作品')
        self.cid = self.db.create_chapter(self.pid, 1, 1, '灯塔',
                                          content='灯塔每晚少亮一盏灯。', status='AI草稿·待核')
        self.candidate = setting_candidates.propose_candidate(
            self.db, self.cid, '地理', '灯塔', '灯光每晚减少一盏。',
            '灯塔每晚少亮一盏灯。')
        self.win = ChatWindow(self.db)
        self.win._set_current_book(self.pid)

    def tearDown(self):
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()
        self.db.close()
        self.temp.cleanup()

    def test_search_and_overview_counts_are_bound_to_current_book(self):
        self.assertEqual(self.win.overview_metrics['settings'].text(), '1')
        self.assertEqual(self.win.overview_metrics['review'].text(), '1')
        self.assertEqual(self.win.progress_value.text(), '1 / 20 章')
        self.win.book_search.setText('长夜')
        visible = [self.win.book_list.item(i).text()
                   for i in range(self.win.book_list.count())
                   if not self.win.book_list.item(i).isHidden()]
        self.assertEqual(len(visible), 1)
        self.assertIn('长夜', visible[0])
        self.assertEqual(self.win.current_pid, self.pid)
        roots = [self.win.tree.topLevelItem(i) for i in range(self.win.tree.topLevelItemCount())
                 if not self.win.tree.topLevelItem(i).isHidden()]
        self.assertEqual(len(roots), 1)
        self.assertGreater(roots[0].childCount(), 0)

    def test_narrow_window_hides_overview_and_wide_window_restores_it(self):
        self.win.show()
        self.win.resize(1024, 760)
        self.app.processEvents()
        self.assertTrue(self.win.inspector.isHidden())
        self.win.resize(1360, 860)
        self.app.processEvents()
        self.assertFalse(self.win.inspector.isHidden())
        self.win.btn_overview.click()
        self.assertTrue(self.win.inspector.isHidden())

    def test_latest_and_review_actions_open_the_bound_chapter(self):
        with patch.object(self.win, '_open_chapter_dialog') as opened:
            self.win.btn_latest.click()
            self.assertEqual(opened.call_args.args[0], self.cid)
            self.win.btn_review_queue.click()
            self.assertEqual(opened.call_args.args[0], self.cid)

    def test_chapter_information_and_tools_do_not_cover_the_manuscript(self):
        dlg = ChapterEditorDialog(self.db, self.cid)
        self.assertEqual(dlg.editor.tabs.tabText(2), '章节信息')
        self.assertEqual(dlg.editor.tabs.currentIndex(), 0)
        self.assertTrue(dlg.tools_panel.isHidden())
        dlg.btn_tools.click()
        self.assertFalse(dlg.tools_panel.isHidden())
        dlg.editor.ch_title.setText('新的灯塔')
        dlg.editor.edit.setPlainText('灯塔的最后一盏灯熄灭了。')
        dlg._save()
        row = self.db.get_chapter(self.cid)
        self.assertEqual(row['title'], '新的灯塔')
        self.assertEqual(row['content'], '灯塔的最后一盏灯熄灭了。')
        dlg.close()

    def test_activity_card_keeps_details_collapsed_and_tracks_progress(self):
        card = ActivityCard('写作任务')
        card.log('正在写正文')
        self.assertTrue(card.log_view.isHidden())
        card.update_count(2, 5)
        self.assertEqual(card.progress_bar.value(), 2)
        self.assertEqual(card.progress_bar.maximum(), 5)
        card.log('  ❌ 本章失败')
        self.assertFalse(card.log_view.isHidden())
        for index in range(15):
            card.add_chapter(index, f'第{index + 1}章')
        self.assertEqual(card.chapters_row.count(), 15)
        card.close()

    def test_resource_icons_do_not_overwrite_book_and_chapter_click_bindings(self):
        tree = ProjectTree(self.db)
        tree.load()
        tree.load_project_detail(self.pid)
        roots = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
        root = next(item for item in roots if item.data(0, Qt.UserRole) == (KIND_PROJECT, self.pid))
        self.assertFalse(root.icon(0).isNull())
        selected_books, selected_chapters = [], []
        tree.project_selected.connect(selected_books.append)
        tree.chapter_selected.connect(selected_chapters.append)
        tree._on_click(root, 0)
        stack = [root]
        chapter = None
        while stack:
            item = stack.pop()
            data = item.data(0, Qt.UserRole)
            if data and data[0] == KIND_CHAPTER and data[1] == self.cid:
                chapter = item
                break
            stack.extend(item.child(i) for i in range(item.childCount()))
        self.assertIsNotNone(chapter)
        self.assertFalse(chapter.icon(0).isNull())
        tree._on_click(chapter, 0)
        self.assertEqual(selected_books, [self.pid])
        self.assertEqual(selected_chapters, [self.cid])
        tree.close()


if __name__ == '__main__':
    unittest.main()
