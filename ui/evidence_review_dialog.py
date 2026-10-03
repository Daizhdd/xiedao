# -*- coding: utf-8 -*-
"""Review with clickable manuscript evidence and candidate-only revisions."""
import json

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QMessageBox,
                               QPlainTextEdit, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout)

from ai import client as aiclient
from ai import evidence_review as er
from ai import rewrite_candidates as rewrites
from ai.context import context_pack_for
from db import DB
from ui.rewrite_dialog import SYSTEM_REWRITE, RewriteCandidateDialog


class ReviewWorker(QThread):
    done = Signal(int)
    failed = Signal(str)

    def __init__(self, db_path, chapter_id, cfg, parent=None):
        super().__init__(parent)
        self.db_path, self.chapter_id, self.cfg = db_path, chapter_id, cfg

    def run(self):
        db = DB(self.db_path)
        try:
            old = db.get_chapter(self.chapter_id)['content']
            self.done.emit(er.review_saved_chapter(db, self.chapter_id, self.cfg))
        except Exception as exc:
            if db.get_chapter(self.chapter_id)['content'] == old:
                er.create_report(db, self.chapter_id, '')
            self.failed.emit(f'审稿未完成：{exc}')
        finally:
            db.close()


class IssueRevisionWorker(QThread):
    done = Signal(int)
    failed = Signal(str)

    def __init__(self, db_path, chapter_id, cfg, issue, parent=None):
        super().__init__(parent)
        self.db_path, self.chapter_id, self.cfg, self.issue = db_path, chapter_id, cfg, issue

    def run(self):
        db = DB(self.db_path)
        try:
            ch = db.get_chapter(self.chapter_id)
            base = ch['content']
            user = (f"【前情设定】\n{context_pack_for(db, ch)}\n"
                    f"【当前正文】\n{base}\n"
                    f"【需处理的审稿问题】{self.issue['explanation']}\n"
                    f"【原文证据】{self.issue['body_quote']}\n"
                    f"【建议】{self.issue['suggestion']}\n"
                    '只输出修改后的完整正文。')
            text = aiclient.simple_chat(self.cfg, SYSTEM_REWRITE, user,
                                       temperature=0.7, max_tokens=8192,
                                       timeout=300, thinking='disabled')
            cid = rewrites.create_candidate(db, self.chapter_id, text,
                                             self.issue['suggestion'],
                                             mode='review_issue', base_content=base)
            self.done.emit(cid)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            db.close()


class EvidenceReviewDialog(QDialog):
    changed = Signal()

    def __init__(self, db, chapter_id, parent=None):
        super().__init__(parent)
        self.db, self.chapter_id = db, chapter_id
        self.worker = None
        self.setWindowTitle('有证据的审稿')
        self.resize(920, 580)
        root = QVBoxLayout(self)
        self.summary = QLabel('')
        self.summary.setWordWrap(True)
        root.addWidget(self.summary)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(['类别', '严重度', '正文位置', '问题', '依据来源', '状态'])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self.table)
        buttons = QHBoxLayout()
        self.btn_review = QPushButton('复审当前正文')
        self.btn_review.setObjectName('primaryAction')
        self.btn_source = QPushButton('定位正文/依据')
        self.btn_propose = QPushButton('按问题生成修订候选')
        self.btn_ignore = QPushButton('忽略此问题')
        self.btn_not_issue = QPushButton('这不是问题')
        for button in (self.btn_review, self.btn_source, self.btn_propose,
                       self.btn_ignore, self.btn_not_issue):
            buttons.addWidget(button)
        root.addLayout(buttons)
        self.btn_review.clicked.connect(self._review)
        self.btn_source.clicked.connect(self._source)
        self.btn_propose.clicked.connect(self._propose)
        self.btn_ignore.clicked.connect(lambda: self._mark('ignored'))
        self.btn_not_issue.clicked.connect(lambda: self._mark('not_issue'))
        self.table.cellDoubleClicked.connect(lambda _r, col: self._source(source=col == 4))
        self._refresh()

    def _refresh(self):
        self.report = er.latest_report(self.db, self.chapter_id)
        if self.report is None:
            self.summary.setText('尚无审稿报告。点击「复审当前正文」生成。')
            self.table.setRowCount(0)
            return
        current = er.report_current(self.db, self.report)
        categories = json.loads(self.report['categories_json'])
        parts = '、'.join(f'{k} {v}' for k, v in categories.items())
        self.summary.setText(
            f"报告 #{self.report['id']}："
            + ('正文已变化，报告过期。' if not current else
               (f"{self.report['score']} 分；"
                + ('有严重问题待处理；' if er.has_unresolved_severe(
                    json.loads(self.report['issues_json'])) else '')
                + parts) if self.report['status'] == 'reviewed' else
               f"未审：{self.report['error']}"))
        issues = json.loads(self.report['issues_json'])
        self.table.setRowCount(len(issues))
        for i, issue in enumerate(issues):
            for col, value in enumerate((issue['kind'], issue['severity'],
                                         issue['body_quote'][:35], issue['explanation'][:65],
                                         issue['source_ref'] or '主观建议', issue['state'])):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.UserRole, i)
                self.table.setItem(i, col, item)

    def _issue(self):
        if not self.report or not er.report_current(self.db, self.report):
            QMessageBox.warning(self, '报告过期', '正文已变化，请先复审。')
            return None
        i = self.table.currentRow()
        issues = json.loads(self.report['issues_json'])
        if not 0 <= i < len(issues):
            QMessageBox.information(self, '审稿问题', '请先选中一条问题。')
            return None
        return i, issues[i]

    def _cfg(self):
        cfg = self.db.get_default_config()
        if cfg is None:
            QMessageBox.information(self, '审稿', '请先配置默认模型。')
            return None
        return dict(cfg)

    def _review(self):
        cfg = self._cfg()
        if cfg is None:
            return
        self.btn_review.setEnabled(False)
        self.summary.setText('正在审稿…')
        self.worker = ReviewWorker(self.db.path, self.chapter_id, cfg, self)
        self.worker.done.connect(self._done)
        self.worker.failed.connect(self._failed)
        self.worker.start()

    def _done(self, _id):
        self.btn_review.setEnabled(True)
        self._refresh()
        self.changed.emit()

    def _failed(self, error):
        self.btn_review.setEnabled(True)
        self.btn_propose.setEnabled(True)
        self._refresh()
        self.summary.setText(error)
        self.changed.emit()

    def _mark(self, state):
        picked = self._issue()
        if picked is None:
            return
        er.set_issue_state(self.db, self.report['id'], picked[0], state)
        self._refresh()
        self.changed.emit()

    def _source(self, source=False):
        picked = self._issue()
        if picked is None:
            return
        issue = picked[1]
        if source and issue['source_ref'].startswith('outline:'):
            oid = int(issue['source_ref'].split(':')[1])
            outline = self.db.get_outline(oid)
            if outline is None:
                return
            dlg = QDialog(self)
            dlg.setWindowTitle(f"依据：{outline['title']}")
            layout = QVBoxLayout(dlg)
            box = QPlainTextEdit(outline['content'])
            box.setReadOnly(True)
            layout.addWidget(box)
            pos = outline['content'].find(issue['source_quote'])
            self._select_quote(box, outline['content'], pos, len(issue['source_quote']))
            dlg.resize(700, 440)
            dlg.exec()
            return
        if source and issue['source_ref'].startswith('fact:'):
            from ai import story_memory
            fid = int(issue['source_ref'].split(':')[1])
            fact = self.db.conn.execute('SELECT * FROM story_facts WHERE id=?', (fid,)).fetchone()
            if fact is None or not story_memory._valid_source(self.db, fact):
                QMessageBox.warning(self, '依据已失效', '来源正文已变化，请复审。')
                return
            cid, quote, position = fact['source_chapter_id'], fact['quote'], fact['quote_start']
        else:
            cid, quote, position = self.chapter_id, issue['body_quote'], issue['body_start']
        from ui.editor_dialog import ChapterEditorDialog
        dlg = ChapterEditorDialog(self.db, cid, self)
        body = self.db.get_chapter(cid)['content']
        self._select_quote(dlg.editor.edit, body, position, len(quote))
        dlg.exec()
        self._refresh()

    @staticmethod
    def _select_quote(box, body, pos, length):
        start = len(body[:pos].encode('utf-16-le')) // 2
        end = len(body[:pos + length].encode('utf-16-le')) // 2
        cursor = box.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        box.setTextCursor(cursor)
        box.ensureCursorVisible()

    def _propose(self):
        picked = self._issue()
        cfg = self._cfg()
        if picked is None or cfg is None:
            return
        self.btn_propose.setEnabled(False)
        self.summary.setText('正在生成修订候选；当前正文保持不变…')
        self.worker = IssueRevisionWorker(self.db.path, self.chapter_id, cfg,
                                           picked[1], self)
        self.worker.done.connect(lambda proposal: self._proposed(picked[0], proposal))
        self.worker.failed.connect(self._failed)
        self.worker.start()

    def _proposed(self, issue_index, proposal):
        self.btn_propose.setEnabled(True)
        try:
            er.set_issue_state(self.db, self.report['id'], issue_index, 'proposed')
        except ValueError:
            self.summary.setText('正文已变化，修订候选仍保留，请重新比较。')
            return
        self._refresh()
        dlg = RewriteCandidateDialog(self.db, self.chapter_id, self)
        dlg._refresh(proposal)
        dlg.adopted.connect(self.changed.emit)
        dlg.exec()
        self._refresh()

    def reject(self):
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(self, '正在处理', '请等待审稿或候选生成完成。')
            return
        super().reject()

    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(self, '正在处理', '请等待审稿或候选生成完成。')
            event.ignore()
        else:
            super().closeEvent(event)
