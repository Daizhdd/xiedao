"""Actual Qt threads test lifecycle ordering without a view or network."""
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from ai.pipeline import PipelineWorker
from db import DB
import task_execution as execution
import task_ledger as ledger
from ui.task_coordinator import get_coordinator


class FinalizingWorker(PipelineWorker):
    def __init__(self, *args, message='完成', **kwargs):
        super().__init__(*args, **kwargs)
        self.release = threading.Event()
        self.message = message
        self.final_saved = False

    def run(self):
        if self.message == 'failure':
            self.failed.emit('注入故障')
        else:
            self.finished_ok.emit(self.message)
        self.release.wait(3)
        private = DB(self.db_path)
        usage = dict.fromkeys(execution.USAGE_FIELDS, 0)
        usage.update(calls=7, completion_tokens=35, reserved_completion=35)
        ledger.save_usage(private, self.task_run_id, self.pid, usage)
        ledger.checkpoint_run(private, self.task_run_id, self.pid)
        private.close()
        self.final_saved = True


class CoordinatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DB(str(Path(self.tmp.name) / 'synthetic.db'))
        ledger.initialize(self.db)
        self.pid = self.db.create_project('协调器验证')
        self.other = self.db.create_project('另一本书')
        self.coordinator = get_coordinator(self.db)
        self.workers = []

    def tearDown(self):
        for worker in self.workers:
            worker.release.set()
            worker.wait(5000)
        self.app.processEvents()
        self.db.close()
        self.tmp.cleanup()

    def worker(self, **kwargs):
        worker = FinalizingWorker({}, self.db.path, self.pid, 1, **kwargs)
        self.workers.append(worker)
        return worker

    def wait_for(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertTrue(predicate(), 'Qt event did not arrive')

    def test_completion_waits_for_final_persistence_and_does_not_need_a_view(self):
        worker = self.worker()
        session = self.coordinator.prepare('任务', lambda: worker, self.pid)
        messages = []
        session.finished_ok.connect(messages.append)
        session.start()
        self.wait_for(lambda: session._result is not None)
        self.assertTrue(self.coordinator.busy)
        self.assertEqual(messages, [])
        self.assertEqual(ledger.get_run(self.db, session.run_id)['status'], 'running')
        worker.release.set()
        self.wait_for(lambda: session._settled)
        self.assertTrue(worker.final_saved)
        self.assertFalse(self.coordinator.busy)
        self.assertEqual(ledger.get_run(self.db, session.run_id)['status'], 'completed')
        self.assertIn('任务累计 7 次', messages[0])

    def test_failure_remains_durable_after_worker_finally(self):
        worker = self.worker(message='failure')
        session = self.coordinator.prepare('失败任务', lambda: worker, self.pid)
        errors = []
        session.failed.connect(errors.append)
        session.start()
        worker.release.set()
        self.wait_for(lambda: session._settled)
        self.assertEqual(errors, ['注入故障'])
        self.assertEqual(ledger.get_run(self.db, session.run_id)['status'], 'failed')
        self.assertEqual(ledger.get_usage(self.db, session.run_id)['calls'], 7)

    def test_views_share_one_slot_and_factory_is_not_called_when_busy(self):
        worker = self.worker()
        session = self.coordinator.prepare('第一窗口任务', lambda: worker, self.pid)
        called = []
        second = get_coordinator(self.db)
        self.assertIs(second, self.coordinator)
        with self.assertRaises(execution.TaskRecoveryError):
            second.prepare('第二窗口任务', lambda: called.append(True), self.other)
        self.assertEqual(called, [])
        session.stop()
        self.assertFalse(self.coordinator.busy)
        self.assertEqual(ledger.get_run(self.db, session.run_id)['status'], 'stopped')

    def test_wrong_book_is_rejected_before_task_record_creation(self):
        worker = self.worker()
        with self.assertRaises(execution.TaskRecoveryError):
            self.coordinator.prepare('错书', lambda: worker, self.other)
        self.assertEqual(self.db.conn.execute('SELECT count(*) FROM task_runs').fetchone()[0], 0)

    def test_late_chapter_signal_cannot_change_next_task(self):
        first = self.coordinator.prepare('旧任务', lambda: self.worker(), self.pid)
        first.stop()
        second = self.coordinator.prepare('新任务', lambda: self.worker(), self.pid)
        cid = self.db.create_chapter(self.pid, 1, 1, '一')
        first._chapter(cid, '迟到章节')
        rows = self.db.conn.execute('SELECT * FROM task_steps WHERE run_id=?',
                                    (second.run_id,)).fetchall()
        self.assertEqual(rows, [])
        self.assertIs(self.coordinator.active, second)
        second.stop()

    def test_budget_and_stop_statuses_survive_finalization(self):
        for message, status in [('预算已用尽：暂停', 'paused_budget'),
                                ('已停止：保留正文', 'stopped'),
                                ('全书重构已停止；原书保留', 'stopped')]:
            with self.subTest(status=status, message=message):
                worker = self.worker(message=message)
                session = self.coordinator.prepare('状态验证', lambda: worker, self.pid)
                session.start()
                worker.release.set()
                self.wait_for(lambda: session._settled)
                self.assertEqual(ledger.get_run(self.db, session.run_id)['status'], status)


if __name__ == '__main__':
    unittest.main()
