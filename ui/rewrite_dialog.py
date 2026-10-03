# -*- coding: utf-8 -*-
"""Author-facing comparison and adoption of persistent rewrite proposals."""
import difflib
import json

from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMessageBox, QPlainTextEdit,
                               QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout)

from ai import client as aiclient
from ai import prefs as aprefs
from ai import rewrite_candidates as rewrites
from ai import story_memory
from ai.context import context_pack_for
from db import DB


SYSTEM_REWRITE = ('你是小说修订编辑。严格遵守作者本次要求和已有设定。'
                  '只输出替换文本，不输出解释、标题、代码围栏或 Markdown。')


class ManualRewriteWorker(QThread):
    done = Signal(int)
    failed = Signal(str)

    def __init__(self, db_path, chapter_id, cfg, base, mode, instruction,
                 scope=None, parent=None):
        super().__init__(parent)
        self.db_path, self.chapter_id, self.cfg = db_path, chapter_id, cfg
        self.base, self.mode, self.instruction, self.scope = base, mode, instruction, scope

    def run(self):
        db = None
        try:
            db = DB(self.db_path)
            ch = db.get_chapter(self.chapter_id)
            if ch is None or ch['content'] != self.base:
                raise rewrites.StaleCandidate('生成期间正文已改变，候选稿未保存')
            locks = rewrites.active_locks(db, self.chapter_id)
            if self.scope:
                lo, hi = self.scope
                user = rewrites.selection_prompt(self.base, lo, hi, self.mode,
                                                  self.instruction, locks)
            else:
                kind = {'dialogue': '只改善对话', 'pace': '保留情节并调整节奏'}[self.mode]
                protected = '\n'.join(self.base[l['start_pos']:l['end_pos']] for l in locks)
                user = (f'【修改方式】{kind}\n【作者要求】{self.instruction}\n'
                        f'【前情与设定】\n{context_pack_for(db, ch)}\n'
                        f'【现有全文】\n{self.base}\n'
                        f'【必须逐字保留的锁定段落】\n{protected or "（无）"}\n'
                        '请输出完整候选正文。')
            budget = (aprefs.writer_budget(aprefs.load(self.db_path))
                      if aiclient.is_direct_mimo(self.cfg) else 8192)
            replacement = aiclient.simple_chat(
                self.cfg, SYSTEM_REWRITE, user, temperature=0.7,
                max_tokens=budget, timeout=300, thinking='disabled')
            if not (replacement or '').strip():
                raise ValueError('模型没有返回候选文本')
            candidate = (rewrites.compose_selection(self.base, *self.scope, replacement)
                         if self.scope else replacement)
            proposal_id = rewrites.create_candidate(
                db, self.chapter_id, candidate, self.instruction, mode=self.mode,
                scope=self.scope, base_content=self.base)
            self.done.emit(proposal_id)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            if db is not None:
                db.close()


class PostAdoptionWorker(QThread):
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, db_path, chapter_id, cfg, parent=None):
        super().__init__(parent)
        self.db_path, self.chapter_id, self.cfg = db_path, chapter_id, cfg

    def run(self):
        db = None
        try:
            from ai.pipeline import PipelineWorker
            db = DB(self.db_path)
            ch = db.get_chapter(self.chapter_id)
            worker = PipelineWorker(self.cfg, self.db_path, ch['project_id'],
                                    ch['volume'], use_tools=False, review=False,
                                    distill_style=False)
            worker._prefs = aprefs.load(self.db_path)
            worker._finish_chapter(db, self.chapter_id, ch['title'])
            if db.get_setting(story_memory.AUTO_EXTRACT_SETTING, 'off') == 'on':
                story_memory.extract_candidates(
                    db, self.chapter_id,
                    lambda system, user: aiclient.simple_chat(
                        self.cfg, system, user, temperature=0.2,
                        max_tokens=1700, timeout=180, thinking='disabled',
                        **aiclient.structured_output_options(self.cfg)))
            self.done.emit('摘要与记忆处理完成。事实候选仍需作者确认。')
        except Exception as exc:
            self.failed.emit(f'正文已采纳，后处理尚未完成：{exc}。可再次写作补摘要，或手动分析事实。')
        finally:
            if db is not None:
                db.close()


class RewriteCandidateDialog(QDialog):
    adopted = Signal()

    def __init__(self, db, chapter_id, parent=None):
        super().__init__(parent)
        self.db, self.chapter_id = db, chapter_id
        self.post_worker = None
        self.setWindowTitle('重写候选 · 比较与采纳')
        self.resize(1100, 720)
        root = QVBoxLayout(self)
        root.addWidget(QLabel('候选稿不会改动当前正文。可整章采纳，或逐段选择差异。'))
        self.candidates = QTableWidget(0, 5)
        self.candidates.setHorizontalHeaderLabels(['编号', '方式', '时间', '状态', '作者要求'])
        self.candidates.setSelectionBehavior(QTableWidget.SelectRows)
        self.candidates.setEditTriggers(QTableWidget.NoEditTriggers)
        self.candidates.setMaximumHeight(160)
        self.candidates.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self.candidates)
        previews = QHBoxLayout()
        before_group = QVBoxLayout()
        before_group.addWidget(QLabel('当前稿 · 候选生成时的基准版本'))
        self.before = QPlainTextEdit()
        self.before.setReadOnly(True)
        before_group.addWidget(self.before)
        after_group = QVBoxLayout()
        after_group.addWidget(QLabel('候选稿 · 采纳前不会修改正文'))
        self.after = QPlainTextEdit()
        self.after.setReadOnly(True)
        after_group.addWidget(self.after)
        previews.addLayout(before_group, 1)
        previews.addLayout(after_group, 1)
        root.addLayout(previews, 1)
        self.hunks = QListWidget()
        self.hunks.setSelectionMode(QListWidget.MultiSelection)
        self.hunks.setMaximumHeight(145)
        root.addWidget(self.hunks)
        self.info = QLabel('')
        self.info.setWordWrap(True)
        root.addWidget(self.info)
        buttons = QHBoxLayout()
        self.btn_full = QPushButton('采纳全部剩余差异')
        self.btn_full.setObjectName('primaryAction')
        self.btn_selected = QPushButton('只采纳选中段落')
        self.btn_discard = QPushButton('放弃候选')
        self.btn_discard.setObjectName('dangerAction')
        self.btn_unlock = QPushButton('解除本章锁定段落')
        for b in (self.btn_full, self.btn_selected, self.btn_discard, self.btn_unlock):
            buttons.addWidget(b)
        root.addLayout(buttons)
        self.candidates.itemSelectionChanged.connect(self._show_selected)
        self.btn_full.clicked.connect(self._accept_full)
        self.btn_selected.clicked.connect(self._accept_selected)
        self.btn_discard.clicked.connect(self._discard)
        self.btn_unlock.clicked.connect(self._unlock)
        self._refresh()

    def _refresh(self, selected_id=None):
        rows = rewrites.list_candidates(self.db, self.chapter_id)
        self.candidates.blockSignals(True)
        self.candidates.setRowCount(len(rows))
        chosen = 0
        for i, row in enumerate(rows):
            if row['id'] == selected_id:
                chosen = i
            ch = self.db.get_chapter(self.chapter_id)
            hunks = rewrites.diff_hunks(row['base_content'], row['candidate_content'])
            accepted = set(json.loads(row['accepted_hunks_json']))
            expected = rewrites.compose(row['base_content'], hunks, accepted)
            state = ('已过期，仍保留两稿' if row['status'] == 'pending' and ch['content'] != expected
                     else row['status'])
            for col, value in enumerate((row['id'], row['mode'], row['created_at'],
                                         state, row['instruction'][:50])):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.UserRole, row['id'])
                self.candidates.setItem(i, col, item)
        self.candidates.blockSignals(False)
        if rows:
            self.candidates.selectRow(chosen)
        else:
            self.before.clear()
            self.after.clear()
            self.hunks.clear()
            self.info.setText('本章暂无重写候选。')

    def _selected(self):
        row = self.candidates.currentRow()
        if row < 0:
            return None
        item = self.candidates.item(row, 0)
        return rewrites.get_candidate(self.db, item.data(Qt.UserRole)) if item else None

    def _show_selected(self):
        row = self._selected()
        if row is None:
            return
        self.before.setPlainText(row['base_content'])
        self.after.setPlainText(row['candidate_content'])
        hunks = rewrites.diff_hunks(row['base_content'], row['candidate_content'])
        accepted = set(json.loads(row['accepted_hunks_json']))
        self.hunks.clear()
        for h in hunks:
            diff = ''.join(difflib.unified_diff(h['before'].splitlines(keepends=True),
                                                h['replacement'].splitlines(keepends=True),
                                                lineterm=''))
            title = f"差异 {h['index'] + 1}" + ('（已采纳）' if h['index'] in accepted else '')
            item = QListWidgetItem(title + '：' + diff[:160].replace('\n', ' '))
            item.setData(Qt.UserRole, h['index'])
            if h['index'] in accepted:
                item.setFlags(item.flags() & ~Qt.ItemIsSelectable & ~Qt.ItemIsEnabled)
            self.hunks.addItem(item)
        self.info.setText(f"候选 #{row['id']}：已采纳 {len(accepted)}/{len(hunks)} 段。"
                          '正文若已由作者修改，采纳会被阻止，候选仍保留。')

    def _accept_full(self):
        if self.post_worker is not None and self.post_worker.isRunning():
            return
        row = self._selected()
        if row is None:
            return
        try:
            rewrites.accept_full(self.db, row['id'])
        except ValueError as exc:
            QMessageBox.warning(self, '无法采纳', str(exc))
            return
        self.adopted.emit()
        self._refresh(row['id'])
        self._after_adoption()

    def _accept_selected(self):
        if self.post_worker is not None and self.post_worker.isRunning():
            return
        row = self._selected()
        if row is None:
            return
        indexes = [item.data(Qt.UserRole) for item in self.hunks.selectedItems()]
        try:
            rewrites.accept_hunks(self.db, row['id'], indexes)
        except ValueError as exc:
            QMessageBox.warning(self, '无法采纳', str(exc))
            return
        self.adopted.emit()
        self._refresh(row['id'])
        self._after_adoption()

    def _after_adoption(self):
        cfg = self.db.get_default_config()
        if cfg is None:
            self.info.setText('正文已采纳。尚无默认模型，摘要将在下一次写作任务中补全。')
            return
        for button in (self.btn_full, self.btn_selected, self.btn_discard):
            button.setEnabled(False)
        self.info.setText('正文已采纳；后台正在补齐摘要与故事记忆…')
        self.post_worker = PostAdoptionWorker(self.db.path, self.chapter_id, dict(cfg), self)
        self.post_worker.done.connect(self._post_done)
        self.post_worker.failed.connect(self._post_failed)
        self.post_worker.finished.connect(self._post_ready)
        self.post_worker.start()

    def _post_done(self, message):
        self.info.setText(message)

    def _post_failed(self, message):
        self.info.setText(message)

    def _post_ready(self):
        for button in (self.btn_full, self.btn_selected, self.btn_discard):
            button.setEnabled(True)

    def _discard(self):
        row = self._selected()
        if row is None:
            return
        rewrites.abandon(self.db, row['id'])
        self._refresh(row['id'])

    def _unlock(self):
        locks = rewrites.active_locks(self.db, self.chapter_id)
        if not locks:
            QMessageBox.information(self, '锁定段落', '本章当前正文没有有效的锁定段落。')
            return
        from PySide6.QtWidgets import QInputDialog
        body = self.db.get_chapter(self.chapter_id)['content']
        labels = [f"{i+1}. {body[l['start_pos']:l['end_pos']][:45]}" for i, l in enumerate(locks)]
        selected, ok = QInputDialog.getItem(self, '解除锁定', '选择段落：', labels, 0, False)
        if ok:
            index = labels.index(selected)
            rewrites.unlock(self.db, locks[index]['id'], self.chapter_id)
            self.info.setText('已解除锁定。')

    def reject(self):
        if self.post_worker is not None and self.post_worker.isRunning():
            QMessageBox.information(self, '正在补齐摘要', '请等待后台处理结束后关闭。')
            return
        super().reject()

    def closeEvent(self, event):
        if self.post_worker is not None and self.post_worker.isRunning():
            QMessageBox.information(self, '正在补齐摘要', '请等待后台处理结束后关闭。')
            event.ignore()
        else:
            super().closeEvent(event)
