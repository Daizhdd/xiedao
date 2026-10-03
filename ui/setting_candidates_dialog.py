# -*- coding: utf-8 -*-
"""Author review for setting suggestions extracted from chapter text."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPlainTextEdit, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout)

from ai import setting_candidates as settings
from db import CATEGORIES


class SettingCandidatesDialog(QDialog):
    def __init__(self, db, chapter_id, parent=None):
        super().__init__(parent)
        self.db = db
        chapter = db.get_chapter(chapter_id)
        if chapter is None:
            raise ValueError('章节不存在')
        self.pid = chapter['project_id']
        self.setWindowTitle('设定候选 · 作者确认')
        self.resize(960, 590)
        root = QVBoxLayout(self)
        root.addWidget(QLabel('模型发现的设定须有本章原文证据。确认后才会用于后续写作。'))
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ['来源', '分类', '词条', '候选定义', '原文证据', '状态'])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setColumnWidth(0, 90)
        self.table.setColumnWidth(1, 75)
        self.table.setColumnWidth(2, 105)
        self.table.setColumnWidth(3, 265)
        self.table.setColumnWidth(4, 260)
        root.addWidget(self.table, 1)
        self.hint = QLabel('')
        self.hint.setWordWrap(True)
        root.addWidget(self.hint)
        actions = QHBoxLayout()
        self.btn_source = QPushButton('查看原文')
        self.btn_edit = QPushButton('编辑候选')
        self.btn_reject = QPushButton('驳回')
        self.btn_confirm = QPushButton('确认入设定库')
        self.btn_confirm.setObjectName('primaryAction')
        for button in (self.btn_source, self.btn_edit, self.btn_reject,
                       self.btn_confirm):
            actions.addWidget(button)
        root.addLayout(actions)
        self.btn_source.clicked.connect(self._source)
        self.btn_edit.clicked.connect(self._edit)
        self.btn_reject.clicked.connect(self._reject)
        self.btn_confirm.clicked.connect(self._confirm)
        self.table.cellDoubleClicked.connect(lambda _r, _c: self._source())
        self._refresh()

    def _refresh(self):
        rows = settings.list_candidates(self.db, self.pid)
        self.table.setRowCount(len(rows))
        pending = 0
        for index, candidate in enumerate(rows):
            source = self.db.get_chapter(candidate['source_chapter_id'])
            if candidate['status'] == 'candidate':
                state = '待确认' if settings.valid_source(self.db, candidate) else '证据过期'
                pending += 1
            elif candidate['status'] == 'stale':
                state = '证据过期'
            else:
                state = '已确认' if candidate['status'] == 'accepted' else '已驳回'
            values = (f"第{source['chapter_no']}章" if source else '来源已删除',
                      candidate['category'], candidate['term'],
                      candidate['definition'], candidate['quote'], state)
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.UserRole, candidate['id'])
                self.table.setItem(index, col, item)
        self.hint.setText(f'待处理 {pending} 条；证据过期的候选需从当前正文重新提取。')

    def _selected(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, '设定候选', '请先选中一条候选。')
            return None
        item = self.table.item(row, 0)
        candidate = settings.get_candidate(self.db, item.data(Qt.UserRole)) if item else None
        return candidate if candidate and candidate['project_id'] == self.pid else None

    def _confirm(self):
        candidate = self._selected()
        if candidate is None:
            return
        try:
            settings.confirm_candidate(self.db, candidate['id'])
        except ValueError as exc:
            QMessageBox.warning(self, '不能确认', str(exc))
        self._refresh()

    def _reject(self):
        candidate = self._selected()
        if candidate is None:
            return
        try:
            settings.reject_candidate(self.db, candidate['id'])
        except ValueError as exc:
            QMessageBox.warning(self, '不能驳回', str(exc))
        self._refresh()

    def _edit(self):
        candidate = self._selected()
        if candidate is None or candidate['status'] != 'candidate':
            return
        dlg = QDialog(self)
        dlg.setWindowTitle('编辑设定候选')
        dlg.resize(560, 330)
        layout = QFormLayout(dlg)
        category = QComboBox()
        category.addItems(CATEGORIES)
        category.setCurrentText(candidate['category'])
        term = QLineEdit(candidate['term'])
        definition = QPlainTextEdit(candidate['definition'])
        definition.setMaximumHeight(110)
        quote = QLineEdit(candidate['quote'])
        for label, field in (('分类', category), ('词条', term),
                             ('候选定义', definition), ('原文证据', quote)):
            layout.addRow(label, field)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addRow(buttons)
        if dlg.exec():
            try:
                settings.update_candidate(
                    self.db, candidate['id'], category.currentText(), term.text(),
                    definition.toPlainText(), quote.text())
            except ValueError as exc:
                QMessageBox.warning(self, '不能保存', str(exc))
            self._refresh()

    def _source(self):
        candidate = self._selected()
        if candidate is None:
            return
        if not settings.valid_source(self.db, candidate):
            QMessageBox.warning(self, '原文已变化', '这条证据对应的正文版本已失效。')
            return
        chapter = self.db.get_chapter(candidate['source_chapter_id'])
        body = chapter['content'] or ''
        dlg = QDialog(self)
        dlg.setWindowTitle(f"设定来源 · 第{chapter['chapter_no']}章")
        dlg.resize(790, 610)
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel(f"{candidate['term']} · 选中的文字是模型引用的原文"))
        view = QPlainTextEdit(body)
        view.setReadOnly(True)
        layout.addWidget(view)
        start = len(body[:candidate['quote_start']].encode('utf-16-le')) // 2
        end = len(body[:candidate['quote_end']].encode('utf-16-le')) // 2
        cursor = view.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        view.setTextCursor(cursor)
        view.ensureCursorVisible()
        dlg.exec()
