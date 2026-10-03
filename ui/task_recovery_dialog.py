# -*- coding: utf-8 -*-
"""Retry a failed chapter stage without regenerating accepted manuscript."""
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel,
                               QMessageBox, QPushButton, QVBoxLayout)

from ai import client as aiclient
from ai import evidence_review, story_memory, tools as agent_tools
from ai import prefs as aprefs
from ai.pipeline import PipelineWorker
from ai.usage_budget import UsageBudget, BudgetPaused
from db import DB
import task_ledger as ledger


PHASES = (('summary', '补章摘要与卷摘要'),
          ('registry', '重试设定/伏笔登记'),
          ('facts', '重试故事事实提取'),
          ('review', '复审当前正文'))


class RetryWorker(QThread):
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, db_path, chapter_id, cfg, phase, parent=None):
        super().__init__(parent)
        self.db_path, self.chapter_id, self.cfg, self.phase = db_path, chapter_id, cfg, phase

    def run(self):
        db = DB(self.db_path)
        ledger.ensure_schema(db)
        ch = db.get_chapter(self.chapter_id)
        if ch is None:
            db.close()
            self.failed.emit('章节不存在')
            return
        run = ledger.create_run(db, ch['project_id'], f"单章重试·{self.phase}",
                                'repair_chapter', {'chapter_id': self.chapter_id,
                                                   'phase': self.phase})
        budget = UsageBudget(db.get_setting('task_budget.max_calls', '0'),
                             db.get_setting('task_budget.output_tokens', '0'),
                             db.get_setting('task_budget.retries', '2'))
        try:
            ledger.record_phase(db, run, self.chapter_id, self.phase, 'running',
                                input_hash=story_memory.body_hash(ch['content']))
            if self.phase == 'summary':
                worker = PipelineWorker(self.cfg, self.db_path, ch['project_id'], ch['volume'],
                                        use_tools=False, review=False, distill_style=False)
                worker.task_run_id = run
                worker._budget = budget
                worker._prefs = aprefs.load(self.db_path)
                worker._active_db = db
                worker._finish_chapter(db, self.chapter_id, ch['title'])
            elif self.phase == 'registry':
                agent_tools.register_chapter(self.cfg, db, ch['project_id'],
                                              ch['chapter_no'], ch['title'], ch['content'],
                                              budget=budget, chapter_id=self.chapter_id)
            elif self.phase == 'facts':
                story_memory.extract_candidates(db, self.chapter_id,
                    lambda system, user: aiclient.simple_chat(
                        self.cfg, system, user, temperature=0.2, max_tokens=1700,
                        thinking='disabled', budget=budget,
                        **aiclient.structured_output_options(self.cfg)))
            elif self.phase == 'review':
                report_id = evidence_review.review_saved_chapter(db, self.chapter_id, self.cfg,
                    model_call=lambda system, user: aiclient.simple_chat(
                        self.cfg, system, user, temperature=0.2, max_tokens=3000,
                        thinking='disabled', budget=budget,
                        **aiclient.structured_output_options(self.cfg)))
                if evidence_review.get_report(db, report_id)['status'] != 'reviewed':
                    raise ValueError('审稿依据未通过校验，报告已标为未审')
            else:
                raise ValueError('未知重试阶段')
            ledger.record_phase(db, run, self.chapter_id, self.phase, 'completed')
            ledger.update_run(db, run, 'completed')
            self.done.emit('阶段已完成；正文未被重新生成。')
        except Exception as exc:
            status = 'paused_budget' if isinstance(exc, BudgetPaused) else 'failed'
            ledger.record_phase(db, run, self.chapter_id, self.phase,
                                'paused' if isinstance(exc, BudgetPaused) else 'failed',
                                error_kind=type(exc).__name__)
            ledger.update_run(db, run, status, type(exc).__name__)
            self.failed.emit(f'{self.phase} 未完成：{exc}')
        finally:
            ledger.save_usage(db, run, ch['project_id'], budget.snapshot())
            db.close()


class TaskRecoveryDialog(QDialog):
    def __init__(self, db, chapter_id, parent=None):
        super().__init__(parent)
        self.db, self.chapter_id = db, chapter_id
        self.worker = None
        self.setWindowTitle('仅重试未完成步骤')
        self.resize(540, 250)
        root = QVBoxLayout(self)
        root.addWidget(QLabel('只重试选中的后处理步骤；当前正文不会重新生成。'))
        self.history = QLabel('')
        self.history.setWordWrap(True)
        root.addWidget(self.history)
        self.phase = QComboBox()
        for key, label in PHASES:
            self.phase.addItem(label, key)
        root.addWidget(self.phase)
        row = QHBoxLayout()
        self.start = QPushButton('开始重试')
        self.start.clicked.connect(self._start)
        row.addWidget(self.start)
        root.addLayout(row)
        self.info = QLabel('')
        self.info.setWordWrap(True)
        root.addWidget(self.info)
        self._refresh()

    def _refresh(self):
        db = self.db
        ledger.ensure_schema(db)
        rows = db.conn.execute(
            "SELECT s.step_key,s.status,s.error_kind FROM task_steps s JOIN task_runs r "
            "ON r.id=s.run_id WHERE r.project_id=? AND s.step_key LIKE ? "
            "AND s.status IN ('failed','paused','unreviewed') ORDER BY s.id DESC LIMIT 8",
            (db.get_chapter(self.chapter_id)['project_id'], f'phase:{self.chapter_id}:%')).fetchall()
        self.history.setText('待重试步骤：' + ('、'.join(
            f"{r['step_key'].split(':')[-1]}（{r['error_kind'] or r['status']}）" for r in rows)
            if rows else '暂无失败记录；可以手动补摘要或复审。'))
        if rows:
            name = rows[0]['step_key'].split(':')[-1]
            mapping = {'章摘要': 'summary', '卷摘要': 'summary',
                       '设定伏笔登记': 'registry', '故事事实提取': 'facts',
                       '审稿': 'review'}
            idx = self.phase.findData(mapping.get(name, 'summary'))
            if idx >= 0:
                self.phase.setCurrentIndex(idx)

    def _start(self):
        cfg = self.db.get_default_config()
        if cfg is None:
            QMessageBox.information(self, '重试步骤', '请先配置默认模型。')
            return
        self.start.setEnabled(False)
        self.info.setText('正在重试…')
        self.worker = RetryWorker(self.db.path, self.chapter_id, dict(cfg),
                                  self.phase.currentData(), self)
        self.worker.done.connect(self._done)
        self.worker.failed.connect(self._failed)
        self.worker.finished.connect(lambda: self.start.setEnabled(True))
        self.worker.start()

    def _done(self, message):
        self.info.setText(message)
        self._refresh()

    def _failed(self, message):
        self.info.setText(message)
        self._refresh()

    def reject(self):
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(self, '重试中', '请等待当前步骤结束后关闭。')
            return
        super().reject()

    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(self, '重试中', '请等待当前步骤结束后关闭。')
            event.ignore()
        else:
            super().closeEvent(event)
