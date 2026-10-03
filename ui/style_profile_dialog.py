# -*- coding: utf-8 -*-
"""Per-book writing profile and author-controlled style suggestions."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QFormLayout, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QTableWidget,
                               QTableWidgetItem, QTextEdit, QVBoxLayout)

from ai import style_profile as style


class StyleProfileDialog(QDialog):
    def __init__(self, db, project_id, parent=None):
        super().__init__(parent)
        self.db, self.pid = db, project_id
        self.setWindowTitle('本书作品文风')
        self.resize(940, 760)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)
        title = QLabel('作品文风')
        title.setObjectName('dialogTitle')
        root.addWidget(title)
        self.revision = QLabel('')
        self.revision.setObjectName('dialogSubtitle')
        root.addWidget(self.revision)
        short = QGridLayout()
        short.setHorizontalSpacing(16)
        short.setVerticalSpacing(8)
        long_form = QFormLayout()
        self.fields = {}
        profile = style.get_profile(db, project_id)
        short_index = 0
        for key in style.FIELDS:
            widget = QTextEdit() if key in ('sample', 'rules', 'banned') else QLineEdit()
            value = profile[key] if profile else ''
            if isinstance(widget, QTextEdit):
                widget.setPlainText(value)
                widget.setFixedHeight(65 if key != 'sample' else 100)
            else:
                widget.setText(value)
            if isinstance(widget, QTextEdit):
                long_form.addRow(style.LABELS[key], widget)
            else:
                line, side = divmod(short_index, 2)
                short.addWidget(QLabel(style.LABELS[key]), line, side * 2)
                short.addWidget(widget, line, side * 2 + 1)
                short_index += 1
            self.fields[key] = widget
        short.setColumnStretch(1, 1)
        short.setColumnStretch(3, 1)
        root.addLayout(short)
        root.addLayout(long_form)
        save = QPushButton('保存作品档案')
        save.setObjectName('primaryAction')
        save.clicked.connect(self._save)
        root.addWidget(save)
        root.addWidget(QLabel('模型归纳的建议先留在这里；采纳后才进入下一次任务的写作上下文。'))
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(['建议', '依据', '示例', '状态'])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self.table, 1)
        bar = QHBoxLayout()
        accept = QPushButton('采纳建议')
        reject = QPushButton('驳回建议')
        accept.clicked.connect(lambda: self._decide(True))
        reject.clicked.connect(lambda: self._decide(False))
        bar.addWidget(accept)
        bar.addWidget(reject)
        root.addLayout(bar)
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFixedHeight(130)
        root.addWidget(self.preview)
        self._refresh()

    def _refresh(self):
        profile = style.get_profile(self.db, self.pid)
        self.revision.setText(
            f"作品档案版本：{profile['revision'] if profile else '未设置'}。"
            '写作任务开始时读取快照；运行中的任务不会切换风格。')
        self.preview.setPlainText(style.effective_style(self.db, self.pid))
        rows = style.suggestions(self.db, self.pid)
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for col, value in enumerate((row['text'], row['reason'],
                                         row['example'], row['status'])):
                item = QTableWidgetItem(value[:250])
                item.setData(Qt.UserRole, row['id'])
                self.table.setItem(i, col, item)

    def _save(self):
        values = {key: widget.toPlainText() if isinstance(widget, QTextEdit)
                  else widget.text() for key, widget in self.fields.items()}
        try:
            style.save_profile(self.db, self.pid, values)
        except ValueError as exc:
            QMessageBox.warning(self, '保存失败', str(exc))
            return
        self._refresh()

    def _decide(self, accept):
        i = self.table.currentRow()
        if i < 0:
            QMessageBox.information(self, '风格建议', '请先选择一条建议。')
            return
        item = self.table.item(i, 0)
        try:
            style.decide_suggestion(self.db, self.pid, item.data(Qt.UserRole), accept)
        except ValueError as exc:
            QMessageBox.warning(self, '无法处理', str(exc))
        self._refresh()
