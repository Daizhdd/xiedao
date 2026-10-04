# -*- coding: utf-8 -*-
"""中间编辑器：分板块结构化编辑
章节(卷/章号/标题/状态 + 正文 + 章节卡) / 词条(分类/词条名 + 定义)
大纲(层级/标题 + 内容) / 伏笔(类型/埋设/回收/状态 + 内容)
"""
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QPlainTextEdit, QPushButton, QComboBox,
                               QTabWidget, QLineEdit, QSpinBox, QFormLayout)
from PySide6.QtCore import Signal
from PySide6.QtGui import QKeySequence, QShortcut
from ui.shortcuts import shortcut_hint, save_sequence
from ui.theme import ui_font_family

MODE_EMPTY = 0
MODE_CHAPTER = 1
MODE_SETTING = 2
MODE_OUTLINE = 3
MODE_FORESHADOW = 4

CATS = ["力量体系", "地理", "历史", "术语", "人物"]
OL_LEVELS = ["卷纲", "章纲"]
FH_TYPES = ["悬念", "物件", "身份", "线索", "冲突"]
STATUSES = ["AI草稿", "AI草稿·待核", "AI草稿·低分", "AI草稿·未审", "草稿", "定稿"]
FH_STATUSES = ["待回收", "疑似回收", "已回收"]


class Editor(QWidget):
    save_requested = Signal()
    version_requested = Signal()
    generate_shortcut = Signal()
    ai_draft_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.mode = MODE_EMPTY
        self.chapter_id = None
        self.setting_id = None
        self.outline_id = None
        self.foreshadow_id = None

        lay = QVBoxLayout(self)
        # ---- 顶栏 ----
        top = QHBoxLayout()
        self.title_label = QLabel("未选择内容")
        self.title_label.setObjectName('editorTitle')
        self.meta_label = QLabel("")
        self.meta_label.setObjectName('metaLabel')
        top.addWidget(self.title_label)
        top.addWidget(self.meta_label)
        top.addStretch(1)
        self.btn_ai = QPushButton("AI 生成草案")
        self.btn_ai.setVisible(False)
        self.btn_ai.setToolTip(f"根据上下文生成可编辑草稿；正文生成用 {shortcut_hint('G')}")
        self.btn_ai.clicked.connect(lambda: self.ai_draft_requested.emit())
        top.addWidget(self.btn_ai)
        self.btn_version = QPushButton("版本历史")
        self.btn_version.setVisible(False)
        self.btn_version.clicked.connect(lambda: self.version_requested.emit())
        top.addWidget(self.btn_version)
        self.btn_save = QPushButton(f"保存 ({shortcut_hint('S')})")
        self.btn_save.setObjectName('primaryAction')
        self.btn_save.setVisible(False)
        self.btn_save.clicked.connect(lambda: self.save_requested.emit())
        top.addWidget(self.btn_save)
        lay.addLayout(top)

        # ---- 分板块字段区 ----
        self.meta_panel = QWidget()
        self.meta_panel.setObjectName('metadataPanel')
        self.mf = QFormLayout(self.meta_panel)
        self.mf.setContentsMargins(22, 22, 22, 22)
        self.mf.setSpacing(14)

        # 章节字段
        self.ch_vol = QSpinBox(); self.ch_vol.setRange(1, 999)
        self.ch_no = QSpinBox(); self.ch_no.setRange(1, 9999)
        self.ch_title = QLineEdit()
        self.ch_status = QComboBox(); self.ch_status.addItems(STATUSES)
        self.ch_bind = QComboBox()          # 对应章纲条目（二期：跟纲）
        self.mf.addRow("卷", self.ch_vol)
        self.mf.addRow("章节号", self.ch_no)
        self.mf.addRow("标题", self.ch_title)
        self.mf.addRow("状态", self.ch_status)
        self.mf.addRow("对应章纲", self.ch_bind)

        # 词条字段
        self.set_cat = QComboBox(); self.set_cat.addItems(CATS)
        self.set_term = QLineEdit()
        self.mf.addRow("分类", self.set_cat)
        self.mf.addRow("词条名", self.set_term)

        # 大纲字段
        self.ol_lv = QComboBox(); self.ol_lv.addItems(OL_LEVELS)
        self.ol_title = QLineEdit()
        self.mf.addRow("层级", self.ol_lv)
        self.mf.addRow("标题", self.ol_title)

        # 伏笔字段
        self.fh_type = QComboBox(); self.fh_type.addItems(FH_TYPES)
        self.fh_planted = QLineEdit()
        self.fh_plan = QLineEdit()
        self.fh_status = QComboBox(); self.fh_status.addItems(FH_STATUSES)
        self.mf.addRow("类型", self.fh_type)
        self.mf.addRow("埋设章节", self.fh_planted)
        self.mf.addRow("计划回收章节", self.fh_plan)
        self.mf.addRow("状态", self.fh_status)


        # ---- 编辑区 ----
        self.tabs = QTabWidget()
        self.edit = QPlainTextEdit()
        self.edit.setObjectName("mainEdit")
        from PySide6.QtGui import QFont
        f = QFont(ui_font_family(), 11)
        self.edit.setFont(f)
        self.card_edit = QPlainTextEdit()
        self.card_edit.setObjectName("cardEdit")
        self.card_edit.setPlaceholderText(
            "本章章节卡（随上下文包注入 AI）：\n- 本章目标：\n- 场景列表：\n- 出场人物：\n- 结尾卡点/悬念：")
        self.tabs.addTab(self.edit, "正文")
        self.tabs.addTab(self.card_edit, "章节卡")
        self.tabs.addTab(self.meta_panel, "章节信息")
        lay.addWidget(self.tabs, 1)

        # 三期：正文实时字数（标题栏左侧同步更新）
        self.edit.textChanged.connect(self._live_count)

        self.edit.setPlaceholderText(
            f"在左侧选择内容开始编辑。\n\n章节：写正文（Markdown），{shortcut_hint('S')} 保存并自动生成版本快照；「章节卡」页签填本章要求。\n{shortcut_hint('G')} 调 AI 生成本章。")

        QShortcut(save_sequence(), self, activated=self._on_ctrl_s)
        QShortcut(QKeySequence("Ctrl+G"), self, activated=lambda: self.generate_shortcut.emit())

    def _on_ctrl_s(self):
        if self.mode != MODE_EMPTY:
            self.save_requested.emit()

    def _live_count(self):
        if self.mode == MODE_CHAPTER:
            n = len(self.edit.toPlainText())
            self.meta_label.setText(f"字数：{n} · 版本随保存自增")

    # ---------- 字段组显隐 ----------
    def _show_fields(self, group):
        for name, widgets in {
            "chapter": [self.ch_vol, self.ch_no, self.ch_title, self.ch_status,
                        self.ch_bind],
            "setting": [self.set_cat, self.set_term],
            "outline": [self.ol_lv, self.ol_title],
            "foreshadow": [self.fh_type, self.fh_planted, self.fh_plan, self.fh_status],
        }.items():
            vis = (name == group)
            for w in widgets:
                self.mf.setRowVisible(w, vis)
        self.tabs.setTabVisible(2, group is not None)

    # ---------- 模式切换 ----------
    def _reset(self, mode, chapter=False):
        self.mode = mode
        self.chapter_id = None
        self.setting_id = None
        self.outline_id = None
        self.foreshadow_id = None
        self.tabs.setTabVisible(1, chapter)
        self.tabs.setTabVisible(2, mode != MODE_EMPTY)
        self.tabs.setTabText(2, '章节信息' if chapter else '属性')
        self.tabs.setCurrentIndex(0)
        self.tabs.setTabText(0, "正文" if chapter else "内容")
        self.edit.clear()
        self.card_edit.clear()
        self.btn_save.setVisible(mode != MODE_EMPTY)
        self.btn_ai.setVisible(mode != MODE_EMPTY)
        self.btn_version.setVisible(chapter)

    def show_empty(self):
        self._reset(MODE_EMPTY)
        self.title_label.setText("未选择内容")
        self.meta_label.setText("")
        self._show_fields(None)

    def show_chapter(self, row, bind_options=None):
        """bind_options: [(outline_id, title), ...] 可绑定的章纲条目"""
        self._reset(MODE_CHAPTER, chapter=True)
        self.chapter_id = row["id"]
        self._show_fields("chapter")
        self.ch_vol.setValue(row["volume"])
        self.ch_no.setValue(row["chapter_no"])
        self.ch_title.setText(row["title"])
        self.ch_status.blockSignals(True)
        self.ch_status.setCurrentText(row["status"])
        self.ch_status.blockSignals(False)
        # 章纲绑定下拉
        self.ch_bind.blockSignals(True)
        self.ch_bind.clear()
        self.ch_bind.addItem("(未绑定)", 0)
        if bind_options:
            for oid, title in bind_options:
                self.ch_bind.addItem(title[:24], oid)
        idx = self.ch_bind.findData(row["outline_id"] or 0)
        self.ch_bind.setCurrentIndex(idx if idx >= 0 else 0)
        self.ch_bind.blockSignals(False)
        self.title_label.setText(f"第{row['chapter_no']}章 {row['title']}")
        self.meta_label.setText(f"字数：{len(row['content'])} · 版本随保存自增")
        self.edit.setPlainText(row["content"])
        self.card_edit.setPlainText(row["chapter_card"])

    def show_setting(self, row):
        self._reset(MODE_SETTING)
        self.setting_id = row["id"]
        self._show_fields("setting")
        self.set_cat.blockSignals(True)
        self.set_cat.setCurrentText(row["category"])
        self.set_cat.blockSignals(False)
        self.set_term.setText(row["term"])
        self.title_label.setText(f"设定词条 · {row['term']}")
        self.meta_label.setText(f"更新时间：{row['updated_at']}")
        self.edit.setPlainText(row["definition"])

    def show_outline(self, row):
        self._reset(MODE_OUTLINE)
        self.outline_id = row["id"]
        self._show_fields("outline")
        self.ol_lv.blockSignals(True)
        self.ol_lv.setCurrentText(row["level"])
        self.ol_lv.blockSignals(False)
        self.ol_title.setText(row["title"])
        self.title_label.setText(f"大纲 · {row['level']}：{row['title']}")
        self.meta_label.setText(f"更新时间：{row['updated_at']}")
        self.edit.setPlainText(row["content"])

    def show_foreshadow(self, row):
        self._reset(MODE_FORESHADOW)
        self.foreshadow_id = row["id"]
        self._show_fields("foreshadow")
        self.fh_type.blockSignals(True)
        self.fh_type.setCurrentText(row["ftype"])
        self.fh_type.blockSignals(False)
        self.fh_planted.setText(row["planted_ch"])
        self.fh_plan.setText(row["plan_ch"])
        self.fh_status.blockSignals(True)
        self.fh_status.setCurrentText(row["status"])
        self.fh_status.blockSignals(False)
        self.title_label.setText(f"伏笔 · {row['ftype']}（{row['status']}）")
        self.meta_label.setText(f"埋设：{row['planted_ch'] or '—'} · 计划回收：{row['plan_ch'] or '—'}")
        self.edit.setPlainText(row["content"])

    # ---------- 取值 ----------
    def current_text(self):
        return self.edit.toPlainText()

    def card_text(self):
        return self.card_edit.toPlainText()

    def chapter_fields(self):
        return dict(volume=self.ch_vol.value(), chapter_no=self.ch_no.value(),
                    title=self.ch_title.text().strip(), status=self.ch_status.currentText(),
                    outline_id=self.ch_bind.currentData() or 0)

    def setting_fields(self):
        return dict(category=self.set_cat.currentText(), term=self.set_term.text().strip())

    def outline_fields(self):
        return dict(level=self.ol_lv.currentText(), title=self.ol_title.text().strip())

    def foreshadow_fields(self):
        return dict(ftype=self.fh_type.currentText(), planted_ch=self.fh_planted.text().strip(),
                    plan_ch=self.fh_plan.text().strip(), status=self.fh_status.currentText())
