# -*- coding: utf-8 -*-
"""右侧 AI 面板：模型切换 + 生成/润色/摘要 + 输出"""
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QComboBox, QPushButton, QTextEdit, QGroupBox)
from PySide6.QtCore import Signal


class AIPanel(QWidget):
    config_requested = Signal()
    generate_requested = Signal()
    polish_requested = Signal()
    summary_requested = Signal()
    preview_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)

        g1 = QGroupBox("模型")
        g1l = QHBoxLayout(g1)
        self.cfg_combo = QComboBox()
        g1l.addWidget(self.cfg_combo, 1)
        btn_cfg = QPushButton("配置")
        btn_cfg.clicked.connect(lambda: self.config_requested.emit())
        g1l.addWidget(btn_cfg)
        lay.addWidget(g1)

        g2 = QGroupBox("AI 操作")
        g2l = QVBoxLayout(g2)
        self.btn_gen = QPushButton("生成本章（Ctrl+G）")
        self.btn_gen.clicked.connect(lambda: self.generate_requested.emit())
        g2l.addWidget(self.btn_gen)
        self.btn_polish = QPushButton("润色当前文本")
        self.btn_polish.clicked.connect(lambda: self.polish_requested.emit())
        g2l.addWidget(self.btn_polish)
        self.btn_summary = QPushButton("生成章节摘要")
        self.btn_summary.clicked.connect(lambda: self.summary_requested.emit())
        g2l.addWidget(self.btn_summary)
        self.btn_preview = QPushButton("预览上下文包")
        self.btn_preview.clicked.connect(lambda: self.preview_requested.emit())
        g2l.addWidget(self.btn_preview)
        lay.addWidget(g2)
        self._ops_group = g2

        g3 = QGroupBox("输出")
        g3l = QVBoxLayout(g3)
        self.out = QTextEdit()
        self.out.setObjectName("aiOut")
        self.out.setReadOnly(True)
        self.out.setPlaceholderText("AI 生成/润色/摘要结果会显示在这里，可手动复制回编辑器。")
        g3l.addWidget(self.out)
        lay.addWidget(g3, 1)

        # 面板整体始终可用（配置按钮随时可点）；仅 AI 操作组随选书启用
        self._ops_group.setEnabled(False)

    def set_enabled(self, on):
        self._ops_group.setEnabled(on)

    def load_configs(self, configs):
        self.cfg_combo.clear()
        self._cfg_map = {}
        for c in configs:
            self.cfg_combo.addItem(f"{c['name']}（{c['provider']}）")
            self._cfg_map[self.cfg_combo.count() - 1] = c["id"]
        if configs:
            self.cfg_combo.setCurrentIndex(0)

    def current_config_id(self):
        idx = self.cfg_combo.currentIndex()
        return self._cfg_map.get(idx) if hasattr(self, "_cfg_map") else None

    def append_out(self, text):
        self.out.append(text)

    def set_out(self, text):
        self.out.setPlainText(text)
