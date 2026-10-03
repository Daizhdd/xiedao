# -*- coding: utf-8 -*-
"""章节编辑弹窗：对话界面里点开某章时使用，复用主编辑器组件。"""
from ui.icons import icon_button, line_icon
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
                               QMessageBox, QInputDialog, QFrame, QLabel)

from ui.editor import Editor, MODE_CHAPTER
from ui import dialogs
from ai import rewrite_candidates as rewrites


class ChapterEditorDialog(QDialog):
    def __init__(self, db, cid, parent=None):
        super().__init__(parent)
        self.db = db
        self.cid = cid
        self.setWindowTitle("章节编辑")
        self.resize(1120, 850)
        self.setMinimumSize(880, 660)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)
        self.editor = Editor()
        lay.addWidget(self.editor, 1)
        auxiliary = QHBoxLayout()
        self.btn_tools = icon_button('写作辅助', 'chevron_down')
        self.btn_tools.setCheckable(True)
        auxiliary.addWidget(self.btn_tools)
        helper = QLabel('故事记忆、设定候选、审稿与重写')
        helper.setObjectName('dialogSubtitle')
        auxiliary.addWidget(helper)
        auxiliary.addStretch()
        lay.addLayout(auxiliary)
        tools = QFrame()
        self.tools_panel = tools
        tools.setObjectName('editorTools')
        tool_layout = QVBoxLayout(tools)
        tool_layout.setContentsMargins(12, 10, 12, 10)
        tool_layout.setSpacing(7)
        guide = QLabel('写作辅助 · 请先保存正文，再生成候选或核对证据')
        guide.setObjectName('dialogSubtitle')
        tool_layout.addWidget(guide)
        inspect = QHBoxLayout()
        self.btn_memory = icon_button('本章故事记忆与证据', 'book')
        self.btn_setting_candidates = icon_button('设定候选', 'pin')
        self.btn_review = icon_button('本章审稿报告与证据', 'search')
        self.btn_retry = icon_button('仅重试本章未完成步骤', 'retry')
        for button in (self.btn_memory, self.btn_setting_candidates,
                       self.btn_review, self.btn_retry):
            inspect.addWidget(button)
        tool_layout.addLayout(inspect)
        actions = QHBoxLayout()
        self.btn_candidates = QPushButton('重写候选 · 比较/采纳')
        self.btn_candidates.setObjectName('primaryAction')
        self.btn_selection = QPushButton('仅改选中部分')
        self.btn_dialogue = QPushButton('仅调整对话')
        self.btn_pace = QPushButton('保留情节改节奏')
        self.btn_lock = QPushButton('锁定选中段落')
        for button in (self.btn_candidates, self.btn_selection, self.btn_dialogue,
                       self.btn_pace, self.btn_lock):
            actions.addWidget(button)
        tool_layout.addLayout(actions)
        lay.addWidget(tools)
        tools.hide()
        self.btn_tools.toggled.connect(tools.setVisible)
        self.btn_tools.toggled.connect(
            lambda checked: self.btn_tools.setIcon(line_icon('chevron_up' if checked else 'chevron_down', self.btn_tools)))
        self._rewrite_worker = None
        row = db.get_chapter(cid)
        if row:
            opts = [(o["id"], o["title"]) for o in db.get_outlines(row["project_id"])
                    if o["level"] == "章纲"]
            self.editor.show_chapter(row, bind_options=opts)
        self.editor.save_requested.connect(self._save)
        self.editor.version_requested.connect(self._versions)
        self.btn_memory.clicked.connect(self._memory)
        self.btn_setting_candidates.clicked.connect(self._setting_candidates)
        self.btn_review.clicked.connect(self._review)
        self.btn_retry.clicked.connect(self._retry_steps)
        self.btn_candidates.clicked.connect(self._candidates)
        self.btn_selection.clicked.connect(lambda: self._generate('selection'))
        self.btn_dialogue.clicked.connect(lambda: self._generate('dialogue'))
        self.btn_pace.clicked.connect(lambda: self._generate('pace'))
        self.btn_lock.clicked.connect(self._lock_selection)
        self.editor.btn_ai.hide()   # 对话窗口里 AI 生成走聊天指令，避免死按钮

    def _save(self):
        if self.editor.mode != MODE_CHAPTER or not self.cid:
            return
        self.db.save_chapter(self.cid, self.editor.current_text(), "手动保存")
        meta = self.editor.chapter_fields()
        if self.editor.card_text().strip():
            meta["chapter_card"] = self.editor.card_text()
        self.db.update_chapter_meta(self.cid, **meta)
        row = self.db.get_chapter(self.cid)
        self.editor.show_chapter(row)

    def _versions(self):
        dlg = dialogs.VersionDialog(self.db, self.cid, self)
        if dlg.exec():
            row = self.db.get_chapter(self.cid)
            self.editor.show_chapter(row)

    def _memory(self):
        if self.editor.current_text() != self.db.get_chapter(self.cid)['content']:
            QMessageBox.information(self, '故事记忆', '请先保存当前正文，再查看或分析故事记忆。')
            return
        from ui.story_memory_dialog import StoryMemoryDialog
        StoryMemoryDialog(self.db, self.cid, self).exec()

    def _setting_candidates(self):
        if self._saved_body() is None:
            return
        from ui.setting_candidates_dialog import SettingCandidatesDialog
        SettingCandidatesDialog(self.db, self.cid, self).exec()

    def _review(self):
        if self._saved_body() is None:
            return
        from ui.evidence_review_dialog import EvidenceReviewDialog
        dlg = EvidenceReviewDialog(self.db, self.cid, self)
        dlg.changed.connect(self._reload_chapter)
        dlg.exec()

    def _retry_steps(self):
        if self._saved_body() is None:
            return
        from ui.task_recovery_dialog import TaskRecoveryDialog
        TaskRecoveryDialog(self.db, self.cid, self).exec()
        self._reload_chapter()

    def _saved_body(self):
        body = self.db.get_chapter(self.cid)['content']
        if self.editor.current_text() != body:
            QMessageBox.information(self, '请先保存', '请先保存编辑器中的正文，再生成或采纳候选稿。')
            return None
        return body

    def _selection_offsets(self, body):
        cursor = self.editor.edit.textCursor()
        if not cursor.hasSelection():
            return None
        return (rewrites.utf16_to_py(body, cursor.selectionStart()),
                rewrites.utf16_to_py(body, cursor.selectionEnd()))

    def _lock_selection(self):
        body = self._saved_body()
        if body is None:
            return
        scope = self._selection_offsets(body)
        if scope is None:
            QMessageBox.information(self, '锁定段落', '请先在正文中选中要锁定的段落。')
            return
        try:
            rewrites.lock_selection(self.db, self.cid, *scope)
        except ValueError as exc:
            QMessageBox.warning(self, '无法锁定', str(exc))
        else:
            QMessageBox.information(self, '锁定段落', '已锁定整个段落。正文改变后旧锁会失效；可在候选窗口解除。')

    def _generate(self, mode):
        body = self._saved_body()
        if body is None:
            return
        scope = self._selection_offsets(body)
        if mode == 'selection' and scope is None:
            QMessageBox.information(self, '局部重写', '请先在正文中选中要修改的文字。')
            return
        if not body.strip():
            QMessageBox.information(self, '局部重写', '当前章节还没有正文。')
            return
        cfg = self.db.get_default_config()
        if cfg is None:
            QMessageBox.information(self, '局部重写', '请先配置默认模型。')
            return
        instruction, ok = QInputDialog.getText(self, '重写要求', '这次希望怎样修改？')
        if not ok:
            return
        try:
            if scope:
                rewrites.selection_prompt(body, *scope, mode, instruction,
                                          rewrites.active_locks(self.db, self.cid))
        except ValueError as exc:
            QMessageBox.warning(self, '不能重写选区', str(exc))
            return
        from ui.rewrite_dialog import ManualRewriteWorker
        self._rewrite_worker = ManualRewriteWorker(
            self.db.path, self.cid, dict(cfg), body, mode, instruction,
            scope=scope, parent=self)
        for b in (self.btn_selection, self.btn_dialogue, self.btn_pace):
            b.setEnabled(False)
        self._rewrite_worker.done.connect(self._rewrite_done)
        self._rewrite_worker.failed.connect(self._rewrite_failed)
        self._rewrite_worker.start()

    def _rewrite_done(self, proposal_id):
        for b in (self.btn_selection, self.btn_dialogue, self.btn_pace):
            b.setEnabled(True)
        self._candidates(proposal_id)

    def _rewrite_failed(self, error):
        for b in (self.btn_selection, self.btn_dialogue, self.btn_pace):
            b.setEnabled(True)
        QMessageBox.warning(self, '候选生成失败', error)

    def _candidates(self, proposal_id=None):
        if self._saved_body() is None:
            return
        from ui.rewrite_dialog import RewriteCandidateDialog
        dlg = RewriteCandidateDialog(self.db, self.cid, self)
        dlg.adopted.connect(self._reload_chapter)
        if isinstance(proposal_id, int):
            dlg._refresh(proposal_id)
        dlg.exec()

    def _reload_chapter(self):
        row = self.db.get_chapter(self.cid)
        opts = [(o['id'], o['title']) for o in self.db.get_outlines(row['project_id'])
                if o['level'] == '章纲']
        self.editor.show_chapter(row, bind_options=opts)

    def reject(self):
        if self._rewrite_worker is not None and self._rewrite_worker.isRunning():
            QMessageBox.information(self, '正在生成', '候选稿生成中，请等待完成后关闭。')
            return
        super().reject()

    def closeEvent(self, event):
        if self._rewrite_worker is not None and self._rewrite_worker.isRunning():
            QMessageBox.information(self, '正在生成', '候选稿生成中，请等待完成后关闭。')
            event.ignore()
        else:
            super().closeEvent(event)
