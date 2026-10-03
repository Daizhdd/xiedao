"""Offline task ledger tests against a disposable SQLite database."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db import DB  # noqa: E402
import task_ledger as ledger  # noqa: E402


class TaskLedgerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.NamedTemporaryFile(dir=ROOT, suffix=".db", delete=False)
        self.path = Path(tmp.name)
        tmp.close()
        self.db = DB(str(self.path))
        ledger.ensure_schema(self.db)
        self.pid = self.db.create_project("任务测试书")

    def tearDown(self):
        self.db.close()
        if self.path.exists():
            self.path.unlink()

    def test_interrupted_run_and_step_survive_reopen(self):
        run_id = ledger.create_run(self.db, self.pid, "写到第十章",
                                   "write_until", {"until_no": 10})
        ledger.record_step(self.db, run_id, "chapter:1", "completed")
        self.db.close()
        self.db = DB(str(self.path))
        ledger.mark_interrupted(self.db)
        run = ledger.get_run(self.db, run_id)
        self.assertEqual(run["status"], "interrupted")
        self.assertEqual(run["project_id"], self.pid)
        step = self.db.conn.execute(
            "SELECT status FROM task_steps WHERE run_id=? AND step_key='chapter:1'",
            (run_id,)).fetchone()
        self.assertEqual(step["status"], "completed")

    def test_repeated_step_update_is_idempotent(self):
        run_id = ledger.create_run(self.db, self.pid, "写第一卷",
                                   "write_volume", {"vol": 1})
        ledger.record_step(self.db, run_id, "chapter:1", "running")
        ledger.record_step(self.db, run_id, "chapter:1", "completed")
        rows = self.db.conn.execute(
            "SELECT status FROM task_steps WHERE run_id=?", (run_id,)).fetchall()
        self.assertEqual([r["status"] for r in rows], ["completed"])

    def test_plan_survives_reopen_and_detects_book_change(self):
        plan_id = ledger.create_plan(self.db, self.pid, "rebuild_book",
                                     {"volumes": 2}, "重排两卷")
        original = ledger.get_plan(self.db, plan_id)["book_revision"]
        self.db.close()
        self.db = DB(str(self.path))
        ledger.ensure_schema(self.db)
        self.assertEqual(len(ledger.list_pending_plans(self.db, self.pid)), 1)
        self.db.add_outline(self.pid, "卷纲", "第一卷", "新纲", volume=1)
        self.assertNotEqual(original, ledger.book_revision(self.db, self.pid))


if __name__ == "__main__":
    unittest.main()
