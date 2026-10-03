"""Book-scoped task lifecycle shared by desktop views; contains no widgets."""
from PySide6.QtCore import QObject, Signal, Slot

import task_execution as execution
import task_ledger as ledger


def outcome(worker, message):
    if message.startswith(('已停止', '全书重构已停止')):
        return 'stopped', '任务已停止'
    if message.startswith('预算已用尽'):
        return 'paused_budget', '预算已用尽'
    if getattr(worker, '_stats', {}).get('failed', 0):
        return 'partial', '部分完成'
    return 'completed', '任务完成'


class TaskSession(QObject):
    progress = Signal(str)
    tick = Signal(int, int)
    chapter_done = Signal(int, str)
    checkpoint_req = Signal(str, str)
    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, coordinator, worker, run_id):
        super().__init__(coordinator)
        self.coordinator, self.worker, self.run_id = coordinator, worker, run_id
        self.project_id = worker.pid
        self.status, self.label = 'running', '运行中'
        self._result = None
        self._settled = False
        self._started = False
        worker.progress.connect(self.progress)
        worker.tick.connect(self.tick)
        worker.checkpoint_req.connect(self.checkpoint_req)
        worker.chapter_done.connect(self._chapter)
        worker.finished_ok.connect(self._succeeded)
        worker.failed.connect(self._failed)
        # Final usage and book fingerprints are saved in the worker's finally.
        worker.finished.connect(self._settle)

    def start(self):
        if self._started or self._settled:
            raise execution.TaskRecoveryError('这项任务已经开始或已经结束。')
        self._started = True
        self.worker.start()

    def stop(self):
        if not self._settled:
            if self._started:
                self.worker.stop()
            else:
                self._result = ('ok', '已停止：任务尚未开始。')
                self._settle()

    def gate_continue(self):
        if not self._settled:
            self.worker.gate_continue()

    def gate_skip(self):
        if not self._settled:
            self.worker.gate_skip()

    @Slot(int, str)
    def _chapter(self, cid, name):
        if self._settled:
            return
        chapter = self.coordinator.db.get_chapter(cid)
        if chapter is None or chapter['project_id'] != self.project_id:
            self._result = ('error', '任务返回的章节不属于当前作品。')
            self.stop()
            return
        ledger.record_step(self.coordinator.db, self.run_id, f'chapter:{cid}', 'completed', name)
        self.chapter_done.emit(cid, name)

    @Slot(str)
    def _succeeded(self, message):
        if self._result is None or self._result[0] != 'error':
            self._result = ('ok', message)

    @Slot(str)
    def _failed(self, message):
        self._result = ('error', message)

    @Slot()
    def _settle(self):
        if self._settled:
            return
        self._settled = True
        kind, message = self._result or ('error', '任务线程已结束，但没有返回结果。')
        db = self.coordinator.db
        if kind == 'error':
            self.status, self.label = 'failed', '任务出错'
            ledger.update_run(db, self.run_id, self.status, message)
        else:
            self.status, self.label = outcome(self.worker, message)
            ledger.update_run(db, self.run_id, self.status)
            attempt = ledger.get_usage(db, self.run_id)
            if attempt is not None:
                total = ledger.cumulative_usage(db, self.run_id)
                message += (f"\n本次模型调用 {attempt['calls']} 次，任务累计 {total['calls']} 次；"
                            f"累计已知输出 {total['completion_tokens']} token，"
                            f"用量未知 {total['unknown_calls']} 次。")
        if self.coordinator.active is self:
            self.coordinator.active = None
        if kind == 'error':
            self.failed.emit(message)
        else:
            self.finished_ok.emit(message)


class TaskCoordinator(QObject):
    def __init__(self, db):
        super().__init__()
        self.db = db
        self.worker = None
        self.active = None

    @property
    def busy(self):
        return self.active is not None or bool(self.worker and self.worker.isRunning())

    def prepare(self, title, factory, project_id, parent_run_id=0):
        if self.busy:
            raise execution.TaskRecoveryError('已有一个写作任务在运行，请先完成或停止任务。')
        worker = factory()
        if worker.pid != project_id:
            raise execution.TaskRecoveryError('书籍已切换，请重新发起这项任务。')
        run_id = execution.start_task(self.db, worker, title, parent_run_id)
        session = TaskSession(self, worker, run_id)
        self.worker, self.active = worker, session
        if parent_run_id:
            ledger.mark_resumed(self.db, parent_run_id)
        return session


def get_coordinator(db):
    """All windows that use this main-thread DB share the same execution slot."""
    coordinator = getattr(db, '_task_coordinator', None)
    if coordinator is None:
        coordinator = db._task_coordinator = TaskCoordinator(db)
    return coordinator
