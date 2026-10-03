"""Book-scoped routing and plan guards. Runs without a model or user database."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from db import DB  # noqa: E402
from ui.chat_window import ChatWindow, PlanCard  # noqa: E402
from ui.editor import Editor  # noqa: E402


class BookBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        tmp = tempfile.NamedTemporaryFile(dir=ROOT, suffix=".db", delete=False)
        self.path = Path(tmp.name)
        tmp.close()
        self.db = DB(str(self.path))
        self.first = self.db.create_project("测试甲")
        self.second = self.db.create_project("测试乙")
        self.win = ChatWindow(self.db)
        self.win._set_current_book(self.first)

    def tearDown(self):
        self.win.close()
        self.db.close()
        if self.path.exists():
            self.path.unlink()

    def test_late_route_cannot_execute_on_new_book(self):
        sent = []
        self.win._dispatch = lambda cfg, result: sent.append(result)
        seq = self.win._intent_seq
        self.win._set_current_book(self.second)
        self.win._dispatch_guarded(seq, self.first, {},
                                   {"type": "tool", "name": "rebuild_book"})
        self.assertEqual(sent, [])

    def test_late_batch_cannot_show_results_on_new_book(self):
        shown = []
        self.win._batch_done = lambda *args: shown.append(args)
        seq = self.win._batch_seq
        self.win._set_current_book(self.second)
        self.win._batch_done_guarded(seq, "第一本书的草案", "卷纲", self.first)
        self.assertEqual(shown, [])

    def test_ai_draft_status_survives_editor_load(self):
        cid = self.db.create_chapter(self.first, 1, 1, "草案", "正文", status="AI草稿")
        editor = Editor()
        editor.show_chapter(self.db.get_chapter(cid))
        self.assertEqual(editor.chapter_fields()["status"], "AI草稿")

    def test_write_until_starts_when_middle_chapter_is_missing(self):
        self.db.create_chapter(self.first, 1, 1, "一", "正文")
        self.db.create_chapter(self.first, 1, 3, "三", "正文")
        started = []
        self.win._start_pipeline = lambda title, factory: started.append(title)
        self.win._tool_write_until({}, {"chapter_no": 3})
        self.assertEqual(started, ["补写到第3章"])

    def test_old_plan_cannot_execute_after_new_instruction(self):
        sent = []
        self.win._dispatch = lambda cfg, result: sent.append(result)
        self.win._show_plan({}, {"name": "rebuild_book", "args": {},
                                 "steps": "重排大纲并重写"})
        card = self.win.stream.itemAt(self.win.stream.count() - 2).widget()
        self.assertIsInstance(card, PlanCard)
        self.win._intent_seq += 1
        card._confirm()
        self.assertEqual(sent, [])

    def test_plan_reloads_but_refuses_changed_book(self):
        sent = []
        self.win._dispatch = lambda cfg, result: sent.append(result)
        self.win._show_plan({}, {"name": "rebuild_book", "args": {},
                                 "steps": "重排大纲并重写"})
        self.win._rebuild_stream()
        cards = [self.win.stream.itemAt(i).widget()
                 for i in range(self.win.stream.count())]
        card = next(w for w in cards if isinstance(w, PlanCard))
        self.db.add_outline(self.first, "卷纲", "第一卷", "新增情节", volume=1)
        card._confirm()
        self.assertEqual(sent, [])

    def test_running_pipeline_keeps_its_book_visible(self):
        class Running:
            pid = self.first

            def isRunning(self):
                return True

        self.win.worker = Running()
        self.win._set_current_book(self.second)
        self.assertEqual(self.win.current_pid, self.first)


if __name__ == "__main__":
    unittest.main()
