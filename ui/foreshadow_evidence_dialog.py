# -*- coding: utf-8 -*-
"""Review foreshadow sources, due dates, and proposed resolutions."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QInputDialog,
                               QLabel, QMessageBox, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout)

from ai import foreshadow_memory as memory


class ForeshadowEvidenceDialog(QDialog):
    def __init__(self, db, project_id, parent=None):
        super().__init__(parent)
        self.db, self.pid = db, project_id
        self.setWindowTitle('伏笔证据与回收')
        self.resize(970, 560)
        root = QVBoxLayout(self)
        root.addWidget(QLabel('模型提出的回收只算「疑似」；原文证据由作者核对后确认。'))
        self.chapter = QComboBox()
        for ch in db.get_chapters(project_id):
            self.chapter.addItem(f"第{ch['chapter_no']}章 {ch['title']}", ch['id'])
        root.addWidget(self.chapter)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(['伏笔', '状态', '来源', '计划', '提醒', '证据'])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self.table)
        bar = QHBoxLayout()
        self.btn_plant = QPushButton('绑定埋设证据')
        self.btn_confirm = QPushButton('确认回收')
        self.btn_reject = QPushButton('驳回疑似回收')
        self.btn_plan = QPushButton('调整计划区间')
        self.btn_source = QPushButton('查看原文证据')
        for btn in (self.btn_plant, self.btn_confirm, self.btn_reject,
                    self.btn_plan, self.btn_source):
            bar.addWidget(btn)
        root.addLayout(bar)
        self.chapter.currentIndexChanged.connect(self._refresh)
        self.btn_plant.clicked.connect(self._plant)
        self.btn_confirm.clicked.connect(self._confirm)
        self.btn_reject.clicked.connect(self._reject_resolution)
        self.btn_plan.clicked.connect(self._plan)
        self.btn_source.clicked.connect(self._source)
        self._refresh()

    def _refresh(self):
        rows = self.db.get_foreshadows(self.pid)
        reminders = {x['foreshadow']['id']: x for x in
                     memory.reminders(self.db, self.pid, self.chapter.currentData())} \
                    if self.chapter.currentData() else {}
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            planting = ('已核对' if memory.source_valid(self.db, row, 'planted') else
                        '旧记录/待核对')
            resolving = ('有回收证据' if memory.source_valid(self.db, row, 'resolve')
                         else '无有效回收证据')
            alert = reminders.get(row['id'], {}).get('state', '未到埋设章/来源待核对')
            values = (row['content'][:75], row['status'], planting,
                      row['plan_ch'] or '未计划', alert, resolving)
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, row['id'])
                self.table.setItem(i, col, item)

    def _selected(self):
        i = self.table.currentRow()
        if i < 0:
            QMessageBox.information(self, '伏笔证据', '请先选中一条伏笔。')
            return None
        item = self.table.item(i, 0)
        row = self.db.get_foreshadow(item.data(Qt.UserRole)) if item else None
        return row if row is not None and row['project_id'] == self.pid else None

    def _confirm(self):
        row = self._selected()
        if row is None:
            return
        try:
            memory.confirm_resolution(self.db, row['id'])
        except ValueError as exc:
            QMessageBox.warning(self, '无法确认', str(exc))
        self._refresh()

    def _reject_resolution(self):
        row = self._selected()
        if row is None:
            return
        try:
            memory.reject_resolution(self.db, row['id'])
        except ValueError as exc:
            QMessageBox.warning(self, '无法驳回', str(exc))
        self._refresh()

    def _plant(self):
        row = self._selected()
        if row is None:
            return
        chapters = self.db.get_chapters(self.pid)
        labels = [f"第{c['chapter_no']}章 {c['title']} (id={c['id']})" for c in chapters]
        if not labels:
            return
        label, ok = QInputDialog.getItem(self, '埋设章节', '选择真实埋设章节：', labels, 0, False)
        if not ok:
            return
        cid = chapters[labels.index(label)]['id']
        quote, ok = QInputDialog.getText(self, '原文证据', '粘贴正文中唯一的逐字引文：')
        if ok:
            try:
                memory.record_plant(self.db, row['id'], cid, quote.strip())
            except ValueError as exc:
                QMessageBox.warning(self, '证据无效', str(exc))
            self._refresh()

    def _plan(self):
        row = self._selected()
        if row is None:
            return
        lo, ok = QInputDialog.getInt(self, '计划回收', '最早回收章号（0 为未定）：',
                                     row['plan_from'], 0, 99999)
        if not ok:
            return
        hi, ok = QInputDialog.getInt(self, '计划回收', '最晚回收章号：',
                                     max(lo, row['plan_to']), lo, 99999)
        if ok:
            self.db.update_foreshadow(row['id'], plan_from=lo, plan_to=hi,
                                      plan_ch=f'第{lo}-{hi}章' if lo else '')
            self._refresh()

    def _source(self):
        row = self._selected()
        if row is None:
            return
        options = [name for name in ('planted', 'resolve')
                   if memory.source_valid(self.db, row, name)]
        if not options:
            QMessageBox.information(self, '伏笔证据', '这条伏笔没有当前有效的原文证据。')
            return
        choice, ok = QInputDialog.getItem(self, '查看证据', '选择来源：', options, 0, False)
        if not ok:
            return
        from ui.editor_dialog import ChapterEditorDialog
        cid = row[f'{choice}_chapter_id']
        dlg = ChapterEditorDialog(self.db, cid, self)
        body = self.db.get_chapter(cid)['content']
        pos = body.index(row[f'{choice}_quote'])
        start = len(body[:pos].encode('utf-16-le')) // 2
        end = len(body[:pos + len(row[f'{choice}_quote'])].encode('utf-16-le')) // 2
        cursor = dlg.editor.edit.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        dlg.editor.edit.setTextCursor(cursor)
        dlg.editor.edit.ensureCursorVisible()
        dlg.exec()
        self._refresh()
