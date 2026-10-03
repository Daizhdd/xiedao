# -*- coding: utf-8 -*-
"""Review evidence before allowing a story fact into the writing context."""
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout)

from ai import client as aiclient
from ai import story_memory as memory
from db import DB


class AnalysisWorker(QThread):
    done = Signal(int)
    failed = Signal(str)

    def __init__(self, db_path, chapter_id, cfg, parent=None):
        super().__init__(parent)
        self.db_path, self.chapter_id, self.cfg = db_path, chapter_id, cfg

    def run(self):
        db = None
        try:
            db = DB(self.db_path)
            count = memory.extract_candidates(
                db, self.chapter_id,
                lambda system, user: aiclient.simple_chat(
                    self.cfg, system, user, temperature=0.2, max_tokens=1700,
                    timeout=180, thinking='disabled',
                    **aiclient.structured_output_options(self.cfg)))
            self.done.emit(count)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            if db is not None:
                db.close()


class StoryMemoryDialog(QDialog):
    def __init__(self, db, chapter_id, parent=None):
        super().__init__(parent)
        self.db, self.chapter_id = db, chapter_id
        ch = db.get_chapter(chapter_id)
        if ch is None:
            raise ValueError('章节不存在')
        self.pid = ch['project_id']
        self.worker = None
        self.setWindowTitle(f"故事记忆 · 第{ch['chapter_no']}章")
        self.resize(880, 600)
        root = QVBoxLayout(self)
        root.addWidget(QLabel('事实候选需有原文证据；确认后才会用于后续章节。'))
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(['人物', '类别', '状态', '判断', '原文证据', '来源'])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self.table, 1)
        self.hint = QLabel('')
        self.hint.setWordWrap(True)
        root.addWidget(self.hint)
        row = QHBoxLayout()
        self.btn_analyze = QPushButton('分析当前正文')
        self.btn_confirm = QPushButton('确认事实')
        self.btn_reject = QPushButton('驳回')
        self.btn_edit = QPushButton('编辑候选')
        self.btn_source = QPushButton('查看原文')
        self.btn_alias = QPushButton('添加别名')
        self.btn_rule = QPushButton('添加作者规则')
        for button in (self.btn_analyze, self.btn_confirm, self.btn_reject,
                       self.btn_edit, self.btn_source, self.btn_alias, self.btn_rule):
            row.addWidget(button)
        root.addLayout(row)
        self.used = QLabel('')
        self.used.setWordWrap(True)
        root.addWidget(self.used)
        self.source_list = QTableWidget(0, 3)
        self.source_list.setHorizontalHeaderLabels(['本章使用的事实', '采用原因', '来源（双击打开）'])
        self.source_list.setEditTriggers(QTableWidget.NoEditTriggers)
        self.source_list.setMaximumHeight(150)
        self.source_list.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self.source_list)
        self.btn_analyze.clicked.connect(self._analyze)
        self.btn_confirm.clicked.connect(self._confirm)
        self.btn_reject.clicked.connect(self._reject)
        self.btn_edit.clicked.connect(self._edit)
        self.btn_source.clicked.connect(self._source)
        self.btn_alias.clicked.connect(self._alias)
        self.btn_rule.clicked.connect(self._rule)
        self.source_list.cellDoubleClicked.connect(self._used_source)
        self._refresh()

    def _refresh(self):
        rows = memory.list_facts(self.db, self.pid, self.chapter_id)
        self.table.setRowCount(len(rows))
        for i, fact in enumerate(rows):
            state = (fact['status'] if memory._valid_source(self.db, fact) else '证据过期')
            values = (fact['entity_name'], fact['attribute'], fact['value'],
                      state + (' · 不确定' if fact['certainty'] == 'uncertain' else ''),
                      fact['quote'][:55], f"第{self.db.get_chapter(self.chapter_id)['chapter_no']}章")
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(0x0100, fact['id'])
                self.table.setItem(i, col, item)
        current = memory.memory_for_chapter(self.db, self.chapter_id)
        self.used.setText('本章写作可用的已确认记忆：\n'
                          + (current['text'] or '（暂无）')
                          + (f"\n另有 {len(current['omitted'])} 条因字数限制未注入。"
                             if current['omitted'] else ''))
        entries = current['used'] + [
            {'fact': fact, 'reason': '字数预算省略',
             'source': ('作者规则' if fact['kind'] == 'rule' else
                        f"第{self.db.get_chapter(fact['source_chapter_id'])['chapter_no']}章")}
            for fact in current['omitted']]
        self.source_list.setRowCount(len(entries))
        for i, entry in enumerate(entries):
            fact = entry['fact']
            for col, value in enumerate((f"{fact['entity_name']}｜{fact['attribute']}：{fact['value']}",
                                         entry['reason'], entry['source'])):
                item = QTableWidgetItem(value)
                item.setData(0x0100, fact['id'])
                self.source_list.setItem(i, col, item)

    def _selected(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, '故事记忆', '请先选中一条事实。')
            return None
        item = self.table.item(row, 0)
        fid = item.data(0x0100) if item else None
        return self.db.conn.execute('SELECT * FROM story_facts WHERE id=? AND project_id=?',
                                    (fid, self.pid)).fetchone()

    def _analyze(self):
        ch = self.db.get_chapter(self.chapter_id)
        if not (ch['content'] or '').strip():
            QMessageBox.information(self, '故事记忆', '请先保存本章正文。')
            return
        cfg = self.db.get_default_config()
        if cfg is None:
            QMessageBox.information(self, '故事记忆', '请先配置默认模型。')
            return
        self.btn_analyze.setEnabled(False)
        self.hint.setText('正在分析已保存的正文…')
        self.worker = AnalysisWorker(self.db.path, self.chapter_id, dict(cfg), self)
        self.worker.done.connect(self._analyzed)
        self.worker.failed.connect(self._failed)
        self.worker.start()

    def _analyzed(self, count):
        self.btn_analyze.setEnabled(True)
        self.hint.setText(f'新增 {count} 条候选；同一正文不会重复分析。')
        self._refresh()

    def _failed(self, error):
        self.btn_analyze.setEnabled(True)
        self.hint.setText(f'分析失败：{error}')

    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(self, '故事记忆', '正在分析，请等待完成后关闭。')
            event.ignore()
        else:
            super().closeEvent(event)

    def reject(self):
        # Esc and window controls may bypass closeEvent on a modal QDialog.
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(self, '故事记忆', '正在分析，请等待完成后关闭。')
            return
        super().reject()

    def _confirm(self):
        fact = self._selected()
        if fact is None:
            return
        entity_id = None
        known = self.db.conn.execute('SELECT id,name FROM story_entities WHERE project_id=? ORDER BY name',
                                     (self.pid,)).fetchall()
        resolved = self.db.conn.execute('SELECT 1 FROM story_entities WHERE project_id=? AND name=?',
                                        (self.pid, fact['entity_name'])).fetchone()
        if not resolved and known:
            aliases = self.db.conn.execute('SELECT 1 FROM story_aliases WHERE project_id=? AND alias=?',
                                           (self.pid, fact['entity_name'])).fetchone()
            if not aliases:
                from PySide6.QtWidgets import QInputDialog
                choices = [f"新人物：{fact['entity_name']}"] + [r['name'] for r in known]
                choice, ok = QInputDialog.getItem(self, '确认人物身份',
                                                   f"「{fact['entity_name']}」是哪位人物？",
                                                   choices, 0, False)
                if not ok:
                    return
                if choice != choices[0]:
                    entity_id = next(r['id'] for r in known if r['name'] == choice)
        try:
            memory.confirm_fact(self.db, fact['id'], entity_id=entity_id)
        except memory.MemoryConflict as exc:
            old_source = self.db.get_chapter(exc.previous['source_chapter_id'])
            old_label = (f"第{old_source['chapter_no']}章" if old_source else '作者规则')
            answer = QMessageBox.question(
                self, '状态冲突',
                f"现有已确认状态：{exc.previous['value']}（{old_label}）\n"
                f"新候选：{fact['value']}\n\n确认这条不同状态，并保留两份来源？")
            if answer == QMessageBox.StandardButton.Yes:
                memory.confirm_fact(self.db, fact['id'], replace=True,
                                    entity_id=entity_id)
        except ValueError as exc:
            QMessageBox.warning(self, '不能确认', str(exc))
        self._refresh()

    def _reject(self):
        fact = self._selected()
        if fact is not None:
            memory.reject_fact(self.db, fact['id'])
            self._refresh()

    def _edit(self):
        fact = self._selected()
        if fact is None or fact['status'] != 'candidate':
            return
        dlg = QDialog(self)
        dlg.setWindowTitle('编辑事实候选')
        layout = QFormLayout(dlg)
        name = QLineEdit(fact['entity_name'])
        attr = QComboBox()
        attr.addItems(memory.ATTRIBUTES)
        attr.setCurrentText(fact['attribute'])
        value = QLineEdit(fact['value'])
        quote = QLineEdit(fact['quote'])
        for label, field in (('人物', name), ('类别', attr), ('状态', value), ('原文证据', quote)):
            layout.addRow(label, field)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addRow(buttons)
        if dlg.exec():
            try:
                memory.update_candidate(self.db, fact['id'], name.text(),
                                        attr.currentText(), value.text(), quote.text())
            except ValueError as exc:
                QMessageBox.warning(self, '不能保存', str(exc))
            self._refresh()

    def _source(self):
        fact = self._selected()
        self._show_source(fact)

    def _used_source(self, row, _col):
        item = self.source_list.item(row, 0)
        if item is None:
            return
        fact = self.db.conn.execute('SELECT * FROM story_facts WHERE id=? AND project_id=?',
                                    (item.data(0x0100), self.pid)).fetchone()
        self._show_source(fact)

    def _show_source(self, fact):
        if fact is None or fact['kind'] != 'story':
            return
        if not memory._valid_source(self.db, fact):
            QMessageBox.warning(self, '原文已变化', '这条证据对应的正文版本已失效。')
            return
        from ui.editor_dialog import ChapterEditorDialog
        dlg = ChapterEditorDialog(self.db, fact['source_chapter_id'], self)
        body = self.db.get_chapter(fact['source_chapter_id'])['content']
        # QTextCursor counts UTF-16 code units; Python string offsets do not.
        start = len(body[:fact['quote_start']].encode('utf-16-le')) // 2
        end = len(body[:fact['quote_end']].encode('utf-16-le')) // 2
        cursor = dlg.editor.edit.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        dlg.editor.edit.setTextCursor(cursor)
        dlg.editor.edit.ensureCursorVisible()
        dlg.exec()
        self._refresh()

    def _alias(self):
        fact = self._selected()
        if fact is None or not fact['entity_id']:
            return
        from PySide6.QtWidgets import QInputDialog
        alias, ok = QInputDialog.getText(self, '人物别名', '别名：')
        if ok:
            try:
                memory.add_alias(self.db, self.pid, fact['entity_id'], alias)
            except ValueError as exc:
                QMessageBox.warning(self, '不能保存', str(exc))

    def _rule(self):
        dlg = QDialog(self)
        dlg.setWindowTitle('添加作者确认的长期规则')
        layout = QFormLayout(dlg)
        name = QLineEdit()
        attr = QComboBox()
        attr.addItems(memory.ATTRIBUTES)
        value = QLineEdit()
        for label, field in (('人物', name), ('类别', attr), ('长期规则', value)):
            layout.addRow(label, field)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addRow(buttons)
        if dlg.exec():
            try:
                memory.add_author_rule(self.db, self.pid, name.text(),
                                       attr.currentText(), value.text())
            except ValueError as exc:
                QMessageBox.warning(self, '不能保存', str(exc))
            self._refresh()
