# -*- coding: utf-8 -*-
"""写道 · 对话优先主界面：侧边栏工作区 + 任务活动流 + 自然语言指挥台。

布局：左侧书架+本书资源树；中间任务活动流（用户气泡 /
搭档气泡 / 可展开的任务活动卡）；底部快捷块 + 输入框。
自然语言经 ai.intent 路由：命中任务就启动流水线，否则直接当创作搭档回答。
"""
from ui.icons import icon_button, line_icon, icon_text
import json
import time
import traceback

from PySide6.QtWidgets import (QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
                               QListWidget, QListWidgetItem, QLabel, QScrollArea,
                               QPlainTextEdit, QPushButton, QFrame, QSizePolicy,
                               QMessageBox, QApplication, QFileDialog, QCheckBox,
                               QLineEdit, QSplitter, QGridLayout, QProgressBar, QLayout)
from PySide6.QtCore import Qt, QThread, Signal, QTimer

from ai import client as aiclient
from ai import intent as aintent
from ai import bookqa as abookqa
from ai import fallback as fb
from ai import prompts
from ai import parse as aparse
import task_ledger as ledger
import task_execution as execution
from ui.editor_dialog import ChapterEditorDialog
from ui.theme import chat_qss, build_qss, load_dark_pref, save_dark_pref, configure_fonts
from ui import dialogs
from ui.background import SimpleWorker
from ui.brand import BrandMark
from ui.task_cards import ActivityCard, PlanCard, _bubble_frame
from ui.task_coordinator import get_coordinator, outcome
from ui.shortcuts import shortcut_hint










class ChatWindow(QMainWindow):
    @property
    def worker(self):
        return self._tasks.worker

    @worker.setter
    def worker(self, value):
        self._tasks.worker = value

    def __init__(self, db):
        super().__init__()
        configure_fonts()
        self.db = db
        ledger.initialize(self.db)
        self._tasks = get_coordinator(self.db)
        self.setWindowTitle("写道 · 写作书房")
        self.resize(1360, 860)
        self.setMinimumSize(960, 660)
        self.current_pid = None
        self.thinker = None
        self._intent_worker = None
        self._active_task_card = None
        self._qa_worker = None               # 书目问答的模型调用线程
        self._intent_seq = 0
        self._batch_seq = 0
        self._pro = None                     # 经典三栏窗口引用（防 GC）
        self.histories = {}                  # pid -> [(role, text), ...]

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.workspace_splitter = QSplitter(Qt.Horizontal)
        self.workspace_splitter.setHandleWidth(1)
        self.workspace_splitter.setChildrenCollapsible(False)
        root.addWidget(self.workspace_splitter)

        # ---- 可调整的书架与资源导航 ----
        side = QWidget()
        side.setObjectName("chatSidebar")
        side.setMinimumWidth(220)
        side.setMaximumWidth(340)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(18, 22, 18, 18)
        sv.setSpacing(12)
        brand = QHBoxLayout()
        mark = BrandMark()
        brand.addWidget(mark)
        brand_words = QVBoxLayout()
        brand_words.setSpacing(2)
        brand_name = QLabel('写道')
        brand_name.setObjectName('brandName')
        brand_caption = QLabel('让故事，继续向前')
        brand_caption.setObjectName('mutedText')
        brand_words.addWidget(brand_name)
        brand_words.addWidget(brand_caption)
        brand.addLayout(brand_words)
        brand.addStretch()
        sv.addLayout(brand)
        sv.addSpacing(12)
        shelf_head = QHBoxLayout()
        lab_shelf = QLabel("我的书架")
        lab_shelf.setObjectName("sideLabel")
        shelf_head.addWidget(lab_shelf)
        shelf_head.addStretch(1)
        self.btn_new_book = icon_button("新书", 'plus')
        self.btn_new_book.setObjectName("chipBtn")
        self.btn_new_book.setCursor(Qt.PointingHandCursor)
        shelf_head.addWidget(self.btn_new_book)
        sv.addLayout(shelf_head)
        self.book_search = QLineEdit()
        self.book_search.setObjectName('bookSearch')
        self.book_search.setPlaceholderText('搜索作品')
        self.book_search.setClearButtonEnabled(True)
        self.book_search.textChanged.connect(self._filter_books)
        sv.addWidget(self.book_search)
        self.book_list = QListWidget()
        self.book_list.setObjectName("bookList")
        self.book_list.setMinimumHeight(110)
        self.book_list.setMaximumHeight(190)
        sv.addWidget(self.book_list)
        lab_tree = QLabel("作品资源")
        lab_tree.setObjectName("sideLabel")
        sv.addWidget(lab_tree)
        from ui.project_tree import ProjectTree
        self.tree = ProjectTree(self.db)
        self.tree.setObjectName("resourceTree")
        sv.addWidget(self.tree, 1)
        foot = QHBoxLayout()
        self.btn_config = icon_button("配置模型", 'settings')
        self.btn_config.setObjectName("chipBtn")
        self.btn_config.setCursor(Qt.PointingHandCursor)
        foot.addWidget(self.btn_config)
        foot.addStretch(1)
        sv.addLayout(foot)
        self.workspace_splitter.addWidget(side)

        # ---- 右侧对话区 ----
        right = QWidget()
        right.setObjectName('creationWorkspace')
        right.setMinimumWidth(510)
        rv = QVBoxLayout(right)
        rv.setContentsMargins(28, 22, 28, 22)
        rv.setSpacing(16)
        top_row = QHBoxLayout()
        self.header = QLabel("选一本书开始")
        self.header.setObjectName("chatHeader")
        self.header.setWordWrap(True)
        header_words = QVBoxLayout()
        header_words.setSpacing(5)
        header_words.addWidget(self.header)
        self.book_meta = QLabel('创作搭档 · 书稿与任务都保存在本地')
        self.book_meta.setObjectName('mutedText')
        header_words.addWidget(self.book_meta)
        top_row.addLayout(header_words, 1)
        self.btn_style = QPushButton('作品文风')
        self.btn_style.setObjectName('quietBtn')
        self.btn_style.clicked.connect(self._open_style_profile)
        top_row.addWidget(self.btn_style)
        self.btn_overview = QPushButton('概览')
        self.btn_overview.setObjectName('quietBtn')
        self.btn_overview.setCheckable(True)
        self.btn_overview.setChecked(True)
        self._overview_requested = True
        self.btn_overview.toggled.connect(self._toggle_overview)
        top_row.addWidget(self.btn_overview)
        self.btn_clear_chat = QPushButton("清空对话")
        self.btn_clear_chat.setObjectName('quietBtn')
        self.btn_clear_chat.setCursor(Qt.PointingHandCursor)
        self.btn_clear_chat.clicked.connect(self._clear_chat)
        top_row.addWidget(self.btn_clear_chat)
        rv.addLayout(top_row)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.stream_host = QWidget()
        self.stream = QVBoxLayout(self.stream_host)
        self.stream.setSizeConstraint(QLayout.SetMinimumSize)
        self.stream.setContentsMargins(4, 16, 4, 16)
        self.stream.setSpacing(18)
        self.stream.addStretch(1)
        self.scroll.setWidget(self.stream_host)
        self._stream_scroll_max = self.scroll.verticalScrollBar().maximum()
        self.scroll.verticalScrollBar().rangeChanged.connect(self._stream_range_changed)
        rv.addWidget(self.scroll, 1)

        composer_panel = QFrame()
        composer_panel.setObjectName('composerPanel')
        composer_layout = QVBoxLayout(composer_panel)
        composer_layout.setContentsMargins(16, 12, 16, 12)
        composer_layout.setSpacing(10)
        chips = QHBoxLayout()
        for text, handler in (
                ("写下一卷", self._chip_next_vol),
                ("全书创作", self._chip_book),
                ("写作进度", self._chip_progress),
                ("继续编辑", self._chip_latest)):
            b = QPushButton(text)
            b.setObjectName("chipBtn")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(handler)
            chips.addWidget(b)
        chips.addStretch(1)
        composer_layout.addLayout(chips)

        input_row = QHBoxLayout()
        self.composer = QPlainTextEdit()
        self.composer.setObjectName("composer")
        self.composer.setPlaceholderText(
            "写下你的创作要求，或问问这本书里的故事…")
        self.composer.setFixedHeight(76)
        self.composer.installEventFilter(self)
        self.btn_send = icon_button('发送', 'send')
        self.btn_send.setObjectName("sendBtn")
        self.btn_send.setFixedSize(88, 36)
        self.btn_send.clicked.connect(self._send)
        composer_layout.addWidget(self.composer)
        self.composer_model = QLabel('尚未配置模型')
        self.composer_model.setObjectName('mutedText')
        input_row.addWidget(self.composer_model)
        input_row.addStretch()
        shortcut_hint = QLabel('Enter 发送 · Shift+Enter 换行')
        shortcut_hint.setObjectName('keyboardHint')
        input_row.addWidget(shortcut_hint)
        input_row.addWidget(self.btn_send)
        composer_layout.addLayout(input_row)
        rv.addWidget(composer_panel)

        self.workspace_splitter.addWidget(right)
        self._build_book_overview()
        self.workspace_splitter.addWidget(self.inspector)
        self.workspace_splitter.setStretchFactor(1, 1)
        self.workspace_splitter.setSizes([252, 850, 258])
        self.setCentralWidget(central)

        self.btn_new_book.clicked.connect(self._new_book)
        self.btn_config.clicked.connect(self._config_models)
        self.book_list.currentRowChanged.connect(self._on_book)
        self._bind_tree()
        self._build_menu()
        self.apply_theme(load_dark_pref())
        self.reload_books()

    def _build_book_overview(self):
        self.inspector = QScrollArea()
        self.inspector.setObjectName('overviewScroll')
        self.inspector.setWidgetResizable(True)
        self.inspector.setFrameShape(QFrame.NoFrame)
        self.inspector.setMinimumWidth(238)
        self.inspector.setMaximumWidth(290)
        panel = QFrame()
        panel.setObjectName('bookInspector')
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(22, 26, 22, 22)
        layout.setSpacing(18)
        heading = QLabel('作品概览')
        heading.setObjectName('overviewTitle')
        layout.addWidget(heading)
        caption = QLabel('每一步推进，都看得见')
        caption.setObjectName('mutedText')
        layout.addWidget(caption)
        layout.addSpacing(6)
        self.progress_value = QLabel('0 / — 章')
        self.progress_value.setObjectName('progressValue')
        layout.addWidget(self.progress_value)
        self.book_progress = QProgressBar()
        self.book_progress.setObjectName('bookProgress')
        self.book_progress.setTextVisible(False)
        self.book_progress.setFixedHeight(5)
        layout.addWidget(self.book_progress)
        self.progress_details = QLabel('选择作品后显示规划进度')
        self.progress_details.setObjectName('mutedText')
        self.progress_details.setWordWrap(True)
        layout.addWidget(self.progress_details)
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(20)
        self.overview_metrics = {}
        for index, (key, label) in enumerate((
                ('words', '正文总字数'), ('review', '待人工复核'),
                ('settings', '设定候选'), ('foreshadows', '未回收伏笔'))):
            block = QVBoxLayout()
            value = QLabel('0')
            value.setObjectName('metricValue')
            text = QLabel(label)
            text.setObjectName('mutedText')
            block.addWidget(value)
            block.addWidget(text)
            grid.addLayout(block, index // 2, index % 2)
            self.overview_metrics[key] = value
        layout.addLayout(grid)
        layout.addSpacing(10)
        self.btn_latest = QPushButton('继续编辑最新章节')
        self.btn_latest.setObjectName('primaryAction')
        self.btn_latest.clicked.connect(self._chip_latest)
        self.btn_review_queue = QPushButton('查看待复核章节')
        self.btn_review_queue.clicked.connect(self._open_review_queue)
        self.btn_setting_queue = QPushButton('审核设定候选')
        self.btn_setting_queue.clicked.connect(self._open_setting_candidates)
        for button in (self.btn_latest, self.btn_review_queue, self.btn_setting_queue):
            button.setMinimumHeight(36)
            layout.addWidget(button)
        layout.addSpacing(6)
        synopsis_heading = QLabel('故事主线')
        synopsis_heading.setObjectName('sideLabel')
        layout.addWidget(synopsis_heading)
        self.overview_logline = QLabel('写下一句话概念，为这本书定下方向。')
        self.overview_logline.setObjectName('overviewSynopsis')
        self.overview_logline.setWordWrap(True)
        layout.addWidget(self.overview_logline)
        layout.addStretch()
        self.btn_foreshadows = QPushButton('伏笔与回收 →')
        self.btn_foreshadows.setObjectName('quietBtn')
        self.btn_foreshadows.clicked.connect(self._open_foreshadow_evidence)
        layout.addWidget(self.btn_foreshadows)
        self.inspector.setWidget(panel)
        self._overview_pending_chapter = None

    def _filter_books(self, text):
        for index in range(self.book_list.count()):
            item = self.book_list.item(index)
            item.setHidden(text.strip().casefold() not in item.text().casefold())

    def _filter_resources(self):
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            data = item.data(0, Qt.UserRole)
            item.setHidden(bool(self.current_pid and data and data[1] != self.current_pid))

    def _toggle_overview(self, checked):
        self._overview_requested = checked
        self._adapt_workspace()

    def _adapt_workspace(self):
        if hasattr(self, 'inspector'):
            self.inspector.setVisible(self._overview_requested and self.width() >= 1180)
            self.btn_overview.setToolTip(
                '窗口较窄时自动收起作品概览' if self.width() < 1180 else '显示或收起作品概览')

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._adapt_workspace()

    def _refresh_workspace_summary(self):
        project = self.db.get_project(self.current_pid) if self.current_pid else None
        cfg = self.db.get_default_config()
        self.composer_model.setText(
            f"当前模型 · {cfg['name'] or cfg['model']}" if cfg else '尚未配置模型')
        rows = self.db.conn.execute(
            'SELECT id,volume,chapter_no,title,status,LENGTH(TRIM(content)) AS chars '
            'FROM chapters WHERE project_id=? ORDER BY volume,chapter_no,id',
            (self.current_pid,)).fetchall() if project else []
        written = [row for row in rows if row['chars']]
        pending = [row for row in written if (row['status'] or '').startswith('AI草稿')]
        pending.sort(key=lambda row: {'AI草稿·待核': 0, 'AI草稿·未审': 1,
                                      'AI草稿·低分': 2}.get(row['status'], 3))
        self._overview_pending_chapter = pending[0]['id'] if pending else None
        target = ((project['plan_volumes'] or 0) * (project['plan_chapters'] or 10)
                  if project else 0)
        self.progress_value.setText(f"{len(written)} / {target or '—'} 章")
        self.book_progress.setRange(0, max(target, len(written), 1))
        self.book_progress.setValue(len(written))
        self.progress_details.setText(
            f"规划 {project['plan_volumes'] or '待定'} 卷 · 已完成 {len(written)} 章正文"
            if project else '选择作品后显示规划进度')
        count_settings = self.db.conn.execute(
            "SELECT COUNT(*) FROM setting_candidates WHERE project_id=? AND status='candidate'",
            (self.current_pid,)).fetchone()[0] if project else 0
        count_foreshadows = self.db.conn.execute(
            "SELECT COUNT(*) FROM foreshadows WHERE project_id=? AND status!='已回收'",
            (self.current_pid,)).fetchone()[0] if project else 0
        values = dict(words=f"{sum(row['chars'] for row in written):,}",
                      review=str(len(pending)), settings=str(count_settings),
                      foreshadows=str(count_foreshadows))
        for key, value in values.items():
            self.overview_metrics[key].setText(value)
        self.btn_latest.setEnabled(bool(rows))
        self.btn_review_queue.setEnabled(bool(pending))
        self.btn_setting_queue.setEnabled(bool(count_settings and rows))
        self.btn_style.setEnabled(bool(project))
        self.btn_foreshadows.setEnabled(bool(project))
        if project:
            self.header.setText(project['title'])
            self.book_meta.setText(' · '.join(str(project[key]) for key in
                                            ('genre', 'audience', 'status') if project[key]))
            self.overview_logline.setText(project['logline'] or '写下一句话概念，为这本书定下方向。')
            if rows:
                self.btn_latest.setText(f"继续编辑 · 第{rows[-1]['chapter_no']}章")
        else:
            self.header.setText('选一本书开始')
            self.book_meta.setText('创作搭档 · 书稿与任务都保存在本地')
            self.overview_logline.setText('写下一句话概念，为这本书定下方向。')

    def _open_review_queue(self):
        if self._overview_pending_chapter:
            self._edit_chapter(self._overview_pending_chapter)

    def _open_setting_candidates(self):
        if not self.current_pid:
            return
        chapter = self.db.conn.execute(
            'SELECT id FROM chapters WHERE project_id=? ORDER BY volume,chapter_no,id LIMIT 1',
            (self.current_pid,)).fetchone()
        if chapter:
            from ui.setting_candidates_dialog import SettingCandidatesDialog
            SettingCandidatesDialog(self.db, chapter['id'], self).exec()
            self._refresh_tree()

    # ---------- 主题 ----------
    def _build_menu(self):
        mb = self.menuBar()
        mv = mb.addMenu("视图")
        self.act_dark = mv.addAction("深色主题")
        self.act_dark.setCheckable(True)
        self.act_dark.setChecked(load_dark_pref())

        def _toggle(dark):
            save_dark_pref(dark)
            app = QApplication.instance()
            if app:
                app.setStyleSheet(build_qss(dark))
            self.apply_theme(dark)

        self.act_dark.toggled.connect(_toggle)
        mv.addSeparator()
        act_pro = mv.addAction("经典三栏工作台（旧）")
        act_pro.triggered.connect(self._open_pro)
        act_fh = mv.addAction('伏笔证据与回收')
        act_fh.triggered.connect(self._open_foreshadow_evidence)
        act_style = mv.addAction('本书作品文风')
        act_style.triggered.connect(self._open_style_profile)

    def _open_style_profile(self):
        if not self.current_pid:
            QMessageBox.information(self, '作品文风', '请先选择一本书。')
            return
        from ui.style_profile_dialog import StyleProfileDialog
        StyleProfileDialog(self.db, self.current_pid, self).exec()

    def _open_foreshadow_evidence(self):
        if not self.current_pid:
            QMessageBox.information(self, '伏笔证据', '请先选择一本书。')
            return
        from ui.foreshadow_evidence_dialog import ForeshadowEvidenceDialog
        ForeshadowEvidenceDialog(self.db, self.current_pid, self).exec()
        self._refresh_tree()

    def apply_theme(self, dark):
        """对话界面专属样式随主题整体切换"""
        self.setStyleSheet(build_qss(dark) + chat_qss(dark))

    # ---------- 书架 ----------
    def reload_books(self, select_pid=None):
        """刷新书架。选中放在解除信号屏蔽之后执行，确保 _on_book 被触发、
        current_pid 与对话流（含从库加载历史）真正切换。"""
        self.book_list.blockSignals(True)
        self.book_list.clear()
        target = -1
        for i, p in enumerate(self.db.get_projects()):
            it = QListWidgetItem(f"《{p['title']}》")
            it.setIcon(line_icon('book', self.book_list))
            it.setData(Qt.UserRole, p["id"])
            self.book_list.addItem(it)
            if p["id"] == (select_pid or self.current_pid):
                target = i
        self.tree.load()
        self.book_list.blockSignals(False)
        if target >= 0:
            self.book_list.setCurrentRow(target)
        elif self.book_list.count():
            self.book_list.setCurrentRow(0)
        self._filter_resources()
        self._filter_books(self.book_search.text())
        self._refresh_workspace_summary()
        self._adapt_workspace()

    def _on_book(self, row):
        it = self.book_list.item(row)
        if it is None:
            self.current_pid = None
            return
        self._set_current_book(it.data(Qt.UserRole))

    def _set_current_book(self, pid):
        """书架与资源树的统一换书入口：同步选中态、头部、对话流、资源树"""
        if pid == self.current_pid:
            self.tree.load_project_detail(pid)
            self._filter_resources()
            self._refresh_workspace_summary()
            return
        if self._task_busy():
            # 活动卡与检查点属于正在写作的书；运行期间保持书籍上下文不变。
            for i in range(self.book_list.count()):
                if self.book_list.item(i).data(Qt.UserRole) == self.current_pid:
                    self.book_list.blockSignals(True)
                    self.book_list.setCurrentRow(i)
                    self.book_list.blockSignals(False)
                    break
            self._add_agent_bubble("当前书还有写作任务在运行。请先完成或停止任务，再切换书籍。")
            return
        # 路由/问答在后台返回时不能被派发到新选中的书。
        self._intent_seq = getattr(self, "_intent_seq", 0) + 1
        self._batch_seq += 1
        if self.thinker is not None:
            self._finish_thinking()
        self.current_pid = pid
        for i in range(self.book_list.count()):
            if self.book_list.item(i).data(Qt.UserRole) == pid:
                self.book_list.blockSignals(True)
                self.book_list.setCurrentRow(i)
                self.book_list.blockSignals(False)
                break
        p = self.db.get_project(pid)
        self.header.setText(f"《{p['title']}》 · {p['status']}")
        self.tree.load_project_detail(pid)
        self._filter_resources()
        self._rebuild_stream()
        self._refresh_workspace_summary()

    def _new_book(self):
        dlg = dialogs.ProjectDialog(self.db, None, self)
        if dlg.exec() and dlg.pid:
            self.reload_books(select_pid=dlg.pid)

    def _open_pro(self):
        from ui.main_window import MainWindow
        self._pro = MainWindow(self.db)
        self._pro.tree.load()
        if self.current_pid:
            self._pro.current_pid = self.current_pid
            self._pro.tree.load_project_detail(self.current_pid)
            self._pro._on_project(self.current_pid)
        self._pro.show()

    # ---------- 资源树（侧边栏工作区） ----------
    def _bind_tree(self):
        t = self.tree
        t.project_selected.connect(self._set_current_book)
        t.chapter_selected.connect(self._edit_chapter)
        t.setting_selected.connect(self._edit_setting)
        t.outline_selected.connect(self._edit_outline)
        t.foreshadow_selected.connect(self._edit_foreshadow)
        t.request_new_project.connect(self._new_book)
        t.request_edit_project.connect(
            lambda pid: dialogs.ProjectDialog(self.db, pid, self).exec())
        t.request_delete_project.connect(self._delete_project)
        t.request_new_chapter.connect(self._new_chapter)
        t.request_new_setting.connect(self._new_setting)
        t.request_new_outline.connect(
            lambda pid, lv, vol: self._dlg_refresh(
                dialogs.OutlineDialog(self.db, pid, lv or "章纲", None,
                                      self, preset_volume=vol or 0)))
        t.request_new_foreshadow.connect(
            lambda pid: self._dlg_refresh(dialogs.ForeshadowDialog(self.db, pid, None, self)))
        t.request_edit_outline.connect(self._edit_outline)
        t.request_edit_foreshadow.connect(self._edit_foreshadow)
        t.request_delete_outline.connect(self._delete_outline)
        t.request_delete_foreshadow.connect(self._delete_foreshadow)
        t.request_ai_gen.connect(self._ai_batch_gen)
        t.request_export.connect(self._export_book)
        t.request_new_chapter_from_outline.connect(self._new_chapter_from_outline)
        t.request_audit.connect(
            lambda pid: dialogs.AuditDialog(self.db, pid, self).exec())
        t.request_pipeline.connect(self._tree_pipeline)

    def _refresh_tree(self):
        if self.current_pid:
            self.tree.load_project_detail(self.current_pid)
        self._filter_resources()
        self._refresh_workspace_summary()

    def _dlg_refresh(self, dlg):
        """弹编辑对话框，确认后刷新资源树"""
        if dlg.exec():
            self._refresh_tree()

    def _edit_chapter(self, cid):
        self._open_chapter_dialog(cid, '')

    def _edit_setting(self, sid):
        dlg = dialogs.SettingDialog(self.db, self.current_pid, "术语", sid, self)
        self._dlg_refresh(dlg)

    def _edit_outline(self, oid):
        self._dlg_refresh(dialogs.OutlineDialog(self.db, self.current_pid,
                                                "章纲", oid, self))

    def _edit_foreshadow(self, fid):
        self._dlg_refresh(dialogs.ForeshadowDialog(self.db, self.current_pid,
                                                   fid, self))

    def _delete_project(self, pid):
        if self.worker and self.worker.isRunning() and self.worker.pid == pid:
            self._add_agent_bubble("这本书正在写作，请先完成或停止任务，再删除。")
            return
        p = self.db.get_project(pid)
        if p is None:
            return
        if QMessageBox.question(
                self, "确认删除",
                f"确定删除《{p['title']}》？其设定、章节、版本等将先完整备份，"
                "再从书架删除。"
                ) == QMessageBox.StandardButton.Yes:
            try:
                backup_path = self.db.delete_project(pid)
            except Exception as exc:
                QMessageBox.critical(self, "删除未完成",
                                     f"未能完成备份或删除，书籍已保留：\n{exc}")
                return
            if self.current_pid == pid:
                self.current_pid = None
            self.reload_books()
            if not self.current_pid:
                self._clear_stream()
            QMessageBox.information(self, "删除完成",
                                    f"完整备份已保存：\n{backup_path}")

    def _new_chapter(self, pid):
        self._dlg_refresh(dialogs.ChapterDialog(self.db, pid, self))

    def _new_setting(self, pid, category):
        self._dlg_refresh(dialogs.SettingDialog(self.db, pid, category or "术语",
                                                None, self))

    def _delete_outline(self, oid):
        if QMessageBox.question(self, "确认删除", "确定删除该大纲条目？"
                                ) == QMessageBox.StandardButton.Yes:
            self.db.delete_outline(oid)
            self._refresh_tree()

    def _delete_foreshadow(self, fid):
        if QMessageBox.question(self, "确认删除", "确定删除该伏笔？"
                                ) == QMessageBox.StandardButton.Yes:
            self.db.delete_foreshadow(fid)
            self._refresh_tree()

    def _new_chapter_from_outline(self, pid, oid):
        """从章纲条目一键建章（与经典视图同一规则）"""
        import re as _re
        o = self.db.get_outline(oid)
        if o is None:
            return
        chapters = self.db.get_chapters(pid)
        title = o["title"].strip()
        m = _re.match(r"^第(\d+)章\s*(.*)$", title)
        if m:
            next_no = int(m.group(1))
            ch_title = m.group(2).strip() or title
        else:
            next_no = max([c["chapter_no"] for c in chapters], default=0) + 1
            ch_title = title
        vol = o["volume"] or (max([c["volume"] for c in chapters], default=1))
        card = f"- 本章目标：{o['content']}" if (o["content"] or "").strip() else ""
        self.db.create_chapter(pid, vol, next_no, ch_title,
                               chapter_card=card, outline_id=oid)
        self._refresh_tree()
        self._add_agent_bubble(f"已从章纲创建 第{next_no}章《{ch_title}》并绑定。"
                               "点侧边栏章节即可编辑，或对我说「写第"
                               f"{vol}卷」。")

    def _tree_pipeline(self, pid, vol):
        """资源树右键「一键写完本卷」→ 走聊天里的流水线"""
        self._set_current_book(pid)
        cfg = self._cfg()
        if cfg is None:
            self._add_agent_bubble("还没有配置 AI 模型，请先点左下角「配置模型」。")
            return
        self._tool_write_volume(cfg, {"vol": vol or self._next_vol()})

    def _export_book(self, pid, fmt):
        from ui.export_utils import build_export
        content, default_name, flt, title = build_export(self.db, pid, fmt)
        path, _ = QFileDialog.getSaveFileName(self, title, default_name, flt)
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
        except OSError as e:
            QMessageBox.warning(self, "导出失败", str(e))
            return
        n = len(self.db.get_chapters(pid))
        self._add_agent_bubble(f"已导出 {n} 章 ｜ {len(content)} 字：\n{path}")

    def _config_models(self):
        self._dlg_refresh(dialogs.ConfigDialog(self.db, self))
        self._refresh_workspace_summary()

    # ---------- 资源树：AI 批量草案（设定/卷纲/章纲/伏笔） ----------
    def _book_plan(self, pid):
        """(总卷数, 每卷章数)，与经典视图同规则"""
        import re as _re
        p = self.db.get_project(pid)
        V = int(p["plan_volumes"] or 0)
        M = max(int(p["plan_chapters"] or 10), 1)
        if not V:
            nums = []
            for o in self.db.get_outlines(pid):
                if o["level"] != "卷纲":
                    continue
                m = _re.match(r"^第(\d+)卷", o["title"].strip())
                if m:
                    nums.append(int(m.group(1)))
                elif o["volume"]:
                    nums.append(o["volume"])
            V = max(nums) if nums else 0
        return V, M

    def _next_seq(self, pid, kind):
        import re as _re
        unit = "章" if kind == "章纲" else "卷"
        nums = []
        for o in self.db.get_outlines(pid):
            if o["level"] != kind:
                continue
            m = _re.match(rf"^第(\d+){unit}", o["title"].strip())
            if m:
                nums.append(int(m.group(1)))
            elif o["volume"] and kind == "卷纲":
                nums.append(o["volume"])
        if kind == "章纲":
            nums += [c["chapter_no"] for c in self.db.get_chapters(pid)]
        return (max(nums) + 1) if nums else 1

    def _ai_batch_gen(self, pid, kind):
        cfg = self._cfg()
        if cfg is None:
            self._add_agent_bubble("还没有配置 AI 模型，请先点左下角「配置模型」。")
            return
        from PySide6.QtWidgets import QInputDialog
        project = dict(self.db.get_project(pid))
        settings_text = "\n".join(
            f"- {r['term']}：{r['definition']}" for r in self.db.get_settings(pid))

        vol = 0
        if kind.startswith("章纲@"):
            vol = int(kind.split("@", 1)[1])
            _V, M = self._book_plan(pid)
            s0, s1 = (vol - 1) * M + 1, vol * M
            vo = self._vol_line(pid, vol)
            user = prompts.gen_volume_chapters(
                project, settings_text, vol,
                vo["title"] if vo else f"第{vol}卷",
                vo["content"] if vo else "", s0, s1)
        elif kind == "章纲":
            _V, M = self._book_plan(pid)
            vol, ok = QInputDialog.getInt(
                self, "生成章纲",
                f"为第几卷生成章纲？\n（每卷 {M} 章）",
                int(_V or 1), 0, 999)
            if not ok:
                return
            self._ai_batch_gen(pid, f"章纲@{vol}")
            return
        elif kind == "卷纲":
            _V, _M = self._book_plan(pid)
            user = prompts.gen_volumes_plan(project, settings_text, total=_V or None)
        elif kind == "setting":
            user = prompts.gen_settings_batch(project, settings_text)
        elif kind == "foreshadow":
            existing = "\n".join(f"- {f['content'][:30]}"
                                 for f in self.db.get_foreshadows(pid))
            user = prompts.gen_foreshadows_batch(project, existing)
        else:
            return

        self._add_agent_bubble(f"正在生成{kind.replace('@', '第')}草案…")
        pid_holder = pid
        self._batch_seq += 1
        batch_seq = self._batch_seq

        def _call():
            return aiclient.chat_once(
                cfg, [{"role": "system", "content": prompts.SYSTEM_ASSIST},
                      {"role": "user", "content": user}], temperature=0.8)

        w = SimpleWorker(_call, self)
        w.done.connect(lambda text, s=batch_seq: self._batch_done_guarded(
            s, text, kind, pid_holder))
        w.failed.connect(lambda err, s=batch_seq: self._batch_failed_guarded(
            s, err, pid_holder))
        self._intent_worker = w
        w.start()

    def _vol_line(self, pid, vol):
        """第 vol 卷的卷纲条目（volume 字段优先，缺省按排序位置）"""
        from ai.context import vol_outline_for
        return vol_outline_for(self.db, pid, vol)

    def _strip_num(self, title, unit="章"):
        return aparse.strip_num(title, unit)

    def _batch_done_guarded(self, seq, text, kind, pid):
        if seq != self._batch_seq or pid != self.current_pid:
            return
        self._batch_done(text, kind, pid)

    def _batch_failed_guarded(self, seq, err, pid):
        if seq == self._batch_seq and pid == self.current_pid:
            self._add_agent_bubble(f"错误： {err}")

    def _batch_done(self, text, kind, pid):
        base_kind = kind.split("@", 1)[0]
        vol = int(kind.split("@", 1)[1]) if "@" in kind else 0
        start_num = None
        if base_kind == "章纲" and vol:
            _V, M = self._book_plan(pid)
            start_num = (vol - 1) * M + 1
        items = aparse.parse_batch(text, base_kind, start_num=start_num)
        if not items:
            self._add_agent_bubble(f"AI 输出未能解析出条目：\n{text[:400]}")
            return
        shown = []
        for line, data in items:
            if base_kind in ("卷纲", "章纲"):
                txt = (f"{data['title']}：{data['content']}"
                       if data.get("content") else data["title"])
                shown.append((txt, data))
            else:
                shown.append((line, data))
        title_map = {"setting": "AI 生成的设定词条（勾选后入库）",
                     "卷纲": "AI 生成的分卷大纲（勾选后入库）",
                     "章纲": f"AI 生成的章纲草案（第{vol}卷）" if vol else "AI 生成的章纲草案",
                     "foreshadow": "AI 生成的伏笔草案（勾选后入库）"}
        dlg = dialogs.AiPickDialog(title_map.get(base_kind, "AI 生成结果"), shown, self)
        if not dlg.exec():
            return
        self._store_batch(dlg.picked(), base_kind, pid, vol)
        self._add_agent_bubble(f"已入库，左侧资源树可查看与修改。")

    def _store_batch(self, picked, base_kind, pid, vol=0):
        import re as _re
        if base_kind in ("卷纲", "章纲"):
            bad = [d for d in picked
                   if not aparse.sane_outline_title(d.get("title", ""))]
            if bad:
                picked = [d for d in picked if d not in bad]
                self._add_agent_bubble(
                    "注意： 有 " + str(len(bad)) + " 条标题可疑（去编号后不含汉字，"
                    "疑似模型坏解析）已拦下，请重新生成或手改：\n"
                    + "\n".join(f"- {d.get('title', '')}" for d in bad[:5]))
        seq = 0
        seq_start = 0
        if base_kind == "章纲":
            _V, M = self._book_plan(pid)
            seq_start = (vol - 1) * M + 1
        elif base_kind == "卷纲":
            seq_start = self._next_seq(pid, "卷纲")
        created = 0
        for data in picked:
            if base_kind == "setting":
                self.db.add_setting(pid, data.get("category", "术语"),
                                    data["term"], data.get("definition", ""))
                created += 1
            elif base_kind == "卷纲":
                m = _re.match(r"^第(\d+)卷", data["title"])
                vnum = int(m.group(1)) if m else seq_start + seq
                name = self._strip_num(data["title"], "卷")
                title_out = f"第{vnum}卷 {name}" if name else data["title"]
                self.db.add_outline(pid, "卷纲", title_out,
                                    data.get("content", ""), volume=vnum)
                seq += 1
                created += 1
            elif base_kind == "章纲":
                n = seq_start + seq
                name = self._strip_num(data["title"], "章")
                title_out = f"第{n}章 {name}" if name else data["title"]
                self.db.add_outline(pid, "章纲", title_out,
                                    data.get("content", ""), volume=vol)
                seq += 1
                created += 1
            elif base_kind == "foreshadow":
                self.db.add_foreshadow(pid, data["content"],
                                       data.get("ftype", "悬念"),
                                       data.get("planted_ch", ""),
                                       data.get("plan_ch", ""))
                created += 1
        self._refresh_tree()
        return created

    # ---------- 活动流 ----------
    def history(self):
        """当前书的对话历史（内存缓存，首次从库加载）"""
        if self.current_pid not in self.histories:
            rows = self.db.get_chat_msgs(self.current_pid)
            self.histories[self.current_pid] = [
                (r["role"], r["content"]) for r in rows]
        return self.histories[self.current_pid]

    def _clear_chat(self):
        if not self.current_pid:
            return
        p = self.db.get_project(self.current_pid)
        if QMessageBox.question(
                self, "清空对话",
                f"清空《{p['title']}》的全部对话记录？任务成果（章节/设定）不受影响。"
                ) != QMessageBox.StandardButton.Yes:
            return
        self.db.clear_chat_msgs(self.current_pid)
        self.histories[self.current_pid] = []
        self._rebuild_stream()

    def _clear_stream(self, preserve=None):
        while self.stream.count() > 1:      # 末尾是 stretch
            item = self.stream.takeAt(0)
            w = item.widget()
            if w and w is not preserve:
                w.hide()
                w.deleteLater()
            lay = item.layout()
            if lay:
                while lay.count():
                    sub = lay.takeAt(0)
                    if sub.widget():
                        sub.widget().hide()
                        sub.widget().deleteLater()

    def _rebuild_stream(self):
        active_card = self._active_task_card
        if active_card is not None and active_card._done:
            active_card = None
        self._clear_stream(preserve=active_card)
        if not self.history():
            self._add_welcome()
        else:
            for role, text in self.history():
                if role == "user":
                    self._add_user_bubble(text, record=False)
                else:
                    self._add_agent_bubble(text, record=False)
        for run in ledger.list_recoverable(self.db, self.current_pid):
            card = QFrame()
            card.setObjectName("activityCard")
            lay = QVBoxLayout(card)
            lay.addWidget(icon_text(f"可恢复任务：{run['title']}", 'pause', card))
            row = QHBoxLayout()
            if run["mode"] in ledger.RESUMABLE_MODES:
                btn = icon_button("续跑", 'play')
                btn.setObjectName("logToggleBtn")
                btn.setCursor(Qt.PointingHandCursor)
                btn.clicked.connect(lambda _c=False, r=dict(run), w=card:
                                    self._resume_interrupted(r, card=w))
                row.addWidget(btn)
                row.addWidget(QLabel("已完成的章节自动跳过，从缺的地方接着写。"))
            elif run['mode'] == 'rebuild_book':
                btn = icon_button('重新执行重构', 'retry')
                btn.setToolTip('按原任务保存的要求重新规划并执行；会先备份当前原书。')
                btn.clicked.connect(lambda _c=False, r=run['id']: self._restart_rebuild(r))
                row.addWidget(btn)
                row.addWidget(QLabel('上次未完成；按原要求重新执行，任务开始后显示进度。'))
            else:
                row.addWidget(QLabel(
                    ("全书重构涉及整书备份/恢复，请重新发一次指令。"
                     if run['mode'] == 'rebuild_book' else
                     "请在章节编辑器重试失败步骤，或重新发起任务。")))
            row.addStretch(1)
            lay.addLayout(row)
            self.stream.insertWidget(self.stream.count() - 1, card)
        if active_card is not None:
            self.stream.insertWidget(self.stream.count() - 1, active_card)
        for plan in reversed(ledger.list_pending_plans(self.db, self.current_pid)):
            self._show_plan(None, {
                "name": plan["action"],
                "args": json.loads(plan["args_json"]),
                "steps": plan["steps"],
            }, existing_plan_id=plan["id"])
        self._scroll_bottom()

    def _add_welcome(self):
        """空态引导：展示最常用的写作和复核路径。"""
        wrapper = QWidget()
        outer = QVBoxLayout(wrapper)
        outer.addStretch(1)
        card = QFrame()
        card.setObjectName('emptyGuide')
        card.setMaximumWidth(620)
        body = QVBoxLayout(card)
        body.setContentsMargins(28, 24, 28, 26)
        body.setSpacing(12)
        eyebrow = QLabel('写道 · AI 写作工作台')
        eyebrow.setObjectName('guideEyebrow')
        title = QLabel('下一章，从这里开始。')
        title.setObjectName('guideTitle')
        tip = QLabel('在下方输入写作要求，或从左侧打开章节继续修改。')
        tip.setObjectName('guideText')
        body.addWidget(eyebrow)
        body.addWidget(title)
        body.addWidget(tip)
        for label in ('写作  ·  说「把第 1 卷写完」，按章节推进',
                      '重写  ·  生成候选稿，比较差异后再采纳',
                      '复核  ·  在章节编辑器查看证据、伏笔和审稿'):
            line = QLabel(label)
            line.setObjectName('guideText')
            body.addWidget(line)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(card)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(2)
        self.stream.insertWidget(self.stream.count() - 1, wrapper, 3)

    def _add_row(self, row):
        self.stream.insertLayout(self.stream.count() - 1, row)
        self._scroll_bottom()

    def _add_user_bubble(self, text, record=True):
        _f, row = _bubble_frame(text, "userBubble")
        self._add_row(row)
        if record:
            self.history().append(("user", text))
            if self.current_pid:
                self.db.add_chat_msg(self.current_pid, "user", text)

    def _add_agent_bubble(self, text, record=True):
        _f, row = _bubble_frame(text, "agentBubble")
        self._add_row(row)
        if record:
            self.history().append(("assistant", text))
            if self.current_pid:
                self.db.add_chat_msg(self.current_pid, "assistant", text)

    def _add_activity_card(self, title):
        card = ActivityCard(title)
        card.open_chapter.connect(self._open_chapter_dialog)
        self.stream.insertWidget(self.stream.count() - 1, card)
        self._scroll_bottom()
        return card

    def _scroll_bottom(self):
        sb = self.scroll.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _stream_range_changed(self, _minimum, maximum):
        # Qt computes wrapped bubble heights after insertion. Keep following
        # the bottom across those layouts, while preserving a manual scroll up.
        sb = self.scroll.verticalScrollBar()
        if sb.value() >= self._stream_scroll_max:
            sb.setValue(maximum)
        self._stream_scroll_max = maximum

    def _open_chapter_dialog(self, cid, _name):
        dlg = ChapterEditorDialog(self.db, cid, self)
        dlg.exec()
        self._refresh_tree()

    # ---------- 输入 ----------
    def eventFilter(self, obj, ev):
        from PySide6.QtGui import QKeyEvent
        if obj is self.composer and isinstance(ev, QKeyEvent) \
                and ev.key() in (Qt.Key_Return, Qt.Key_Enter) \
                and not (ev.modifiers() & Qt.ShiftModifier):
            self._send()
            return True
        return super().eventFilter(obj, ev)

    def _cfg(self):
        fb.set_configs(self.db.get_ai_configs())   # 降级链配置快照（主线程刷新）
        row = self.db.get_default_config()
        return dict(row) if row else None

    def _send(self):
        text = self.composer.toPlainText().strip()
        if not text:
            return
        if not self.current_pid:
            self._add_agent_bubble("先在左侧书架选一本书（或新建一本）。")
            return
        cfg = self._cfg()
        if cfg is None:
            self._add_agent_bubble(
                "还没有配置 AI 模型。点左下角「配置模型」添加一个（DeepSeek / 兼容接口均可）。")
            return
        self.composer.clear()
        ledger.supersede_pending_plans(self.db, self.current_pid)
        self._add_user_bubble(text)
        self._set_busy(True)

        pid = self.current_pid
        # SQLite 连接不能跨线程：书目上下文在主线程抓成纯数据快照，
        # 子线程里的 decide 只做网络调用、不碰 db
        ctx = aintent.book_context(self.db, pid)
        hist = list(self.history())[:-1]
        # 序号守卫：用户「不等了」取消后，慢返回的结果作废
        self._intent_seq = getattr(self, "_intent_seq", 0) + 1
        seq = self._intent_seq
        local = aintent.local_decision(ctx, text)
        if local is not None:
            self._dispatch_guarded(seq, pid, cfg, local)
            return
        self._intent_worker = SimpleWorker(
            lambda: aintent.decide(cfg, ctx, text, hist), self)
        self._intent_worker.done.connect(
            lambda r, s=seq, book=pid: self._dispatch_guarded(s, book, cfg, r))
        self._intent_worker.failed.connect(
            lambda e, s=seq, book=pid: self._intent_failed_guarded(s, book, e))
        self._intent_worker.start()

    def _dispatch_guarded(self, seq, pid, cfg, result):
        if seq != self._intent_seq or pid != self.current_pid:
            return    # 已被取消等待，结果丢弃
        try:
            self._dispatch(cfg, result)
        except Exception as exc:
            self._finish_thinking()
            self._task_start_failed(exc)

    def _intent_failed_guarded(self, seq, pid, err):
        if seq != self._intent_seq or pid != self.current_pid:
            return
        self._intent_failed(err)

    def _set_busy(self, busy):
        if busy:
            self._busy_t0 = time.time()
            box = QWidget()
            h = QHBoxLayout(box)
            h.setContentsMargins(0, 0, 0, 0)
            f, _row = _bubble_frame("…思考中", "agentBubble")
            self._think_label = f.findChild(QLabel)
            cancel = QPushButton("不等了")
            cancel.setObjectName("logToggleBtn")
            cancel.setCursor(Qt.PointingHandCursor)
            cancel.clicked.connect(self._cancel_wait)
            h.addWidget(f)
            h.addWidget(cancel)
            h.addStretch(1)
            self.thinker = box
            self.stream.insertWidget(self.stream.count() - 1, box)
            self._scroll_bottom()
            self._think_timer = QTimer(self)
            self._think_timer.setInterval(1000)
            self._think_timer.timeout.connect(self._tick_think)
            self._think_timer.start()
        self.btn_send.setEnabled(not busy)
        self.composer.setEnabled(not busy)

    def _tick_think(self):
        lab = getattr(self, "_think_label", None)
        if lab is None:
            return
        t = int(time.time() - getattr(self, "_busy_t0", time.time()))
        extra = " ｜ 模型可能拥堵，可点「不等了」先干别的" if t >= 45 else ""
        lab.setText(f"…思考中（{t}s）{extra}")

    def _cancel_wait(self):
        """放弃等待：作废本次结果，立即恢复输入；后台线程返回后静默丢弃"""
        self._intent_seq = getattr(self, "_intent_seq", 0) + 1
        self._finish_thinking()
        self._add_agent_bubble("已取消等待。若模型稍后返回，这条结果会被丢弃；"
                               "你可以重新发一句。")

    def _finish_thinking(self):
        timer = getattr(self, "_think_timer", None)
        if timer:
            timer.stop()
            self._think_timer = None
        if getattr(self, "thinker", None):
            self.thinker.deleteLater()
            self.thinker = None
        self._think_label = None
        self._set_busy(False)

    def _intent_failed(self, err):
        self._finish_thinking()
        self._add_agent_bubble(f"错误： 调用失败：{err}")

    def _dispatch(self, cfg, result):
        """意图路由结果：闲聊直接回；任务启动流水线"""
        self._finish_thinking()
        kind = result.get("type")
        pid = self.current_pid
        if kind == "chat":
            text = result.get("text") or "（空回复）"
            self._add_agent_bubble(text)
            return
        if kind == "plan":
            self._show_plan(cfg, result)
            return
        name = result.get("name")
        args = result.get("args") or {}
        if name == 'progress_report':
            self._add_agent_bubble(aintent.progress_text(self.db, pid))
            return
        if self.worker and self.worker.isRunning():
            self._add_agent_bubble("注意： 已有一个任务在跑（见上方活动卡）。等它完成或停止后再来。")
            return
        if name == "show_outline":
            self._tool_show_outline(args)
        elif name == "ask_book":
            self._tool_ask_book(cfg, args)
        elif name == "open_chapter":
            self._tool_open_chapter(args)
        elif name == "rewrite_chapter":
            self._tool_rewrite(cfg, args, "chapter")
        elif name == "rewrite_volume":
            self._tool_rewrite(cfg, args, "volume")
        elif name == "rewrite_book":
            self._tool_rewrite(cfg, args, "book")
        elif name == "rebuild_book":
            self._tool_rebuild(cfg, args)
        elif name == 'restart_rebuild':
            self._restart_rebuild(args.get('run_id'))
        elif name == "write_volume":
            self._tool_write_volume(cfg, args)
        elif name == "write_until":
            self._tool_write_until(cfg, args)
        elif name == "whole_book":
            self._tool_whole_book(cfg)
        else:
            self._add_agent_bubble("这个意图我还拿不准。可以直接说："
                                   "「把第N卷写完」「写到第N章」「重写第N章」"
                                   "「重写第N卷」「全书重写」「全书重构」「进度」，"
                                   "或用下方快捷块。")

    def _show_plan(self, cfg, result, existing_plan_id=None):
        """P1 两段式执行：渲染大动作计划卡，确认后才派发执行。
        要调整不点确认、直接再打字（新要求会生成新计划）。"""
        name = result.get("name")
        args = result.get("args") or {}
        steps = result.get("steps") or "（无步骤说明）"
        plan_pid = self.current_pid
        plan_seq = self._intent_seq
        plan_id = existing_plan_id or ledger.create_plan(
            self.db, plan_pid, name, args, steps)
        titles = {"rebuild_book": "全书重构计划", "rewrite_book": "全书重写计划"}
        extra = None
        if name == "rebuild_book":
            # P2：书都重写了旧伏笔可能全失效——给用户保留/清空的选择
            extra = QCheckBox("同时清空伏笔台账（重写后旧伏笔可能失效；清空前已完整备份）")

        def do_confirm():
            if plan_pid != self.current_pid or plan_seq != self._intent_seq:
                ledger.update_plan(self.db, plan_id, "superseded")
                self._add_agent_bubble("这份计划已过期，请按当前书籍和要求重新生成计划。")
                return False
            stored = ledger.get_plan(self.db, plan_id)
            if (stored is None or stored["status"] != "pending"
                    or stored["book_revision"] != ledger.book_revision(self.db, plan_pid)):
                ledger.update_plan(self.db, plan_id, "superseded")
                self._add_agent_bubble("这本书的内容已变化，原计划已失效，请重新生成。")
                return False
            if self._task_busy():
                self._busy_msg()
                return
            current_cfg = self._cfg()
            if current_cfg is None:
                self._add_agent_bubble("请先配置可用的模型，再执行计划。")
                return
            run_args = dict(args)
            if extra is not None and extra.isChecked():
                run_args["clear_foreshadows"] = True
            prior_worker = self.worker
            self._dispatch(current_cfg, {"type": "tool", "name": name,
                                         "args": run_args})
            ledger.update_plan(self.db, plan_id,
                               "executed" if self.worker is not prior_worker
                               else "not_started")
            return self.worker is not prior_worker

        def do_dismiss():
            ledger.update_plan(self.db, plan_id, "dismissed")
            self._add_agent_bubble(
                "好，先不动。要调整就直接说，比如「只重写第2卷」"
                "「改成3卷」「要求节奏再快点」，我会按新要求重新出计划。")

        self.stream.insertWidget(self.stream.count() - 1,
                                 PlanCard(titles.get(name, "执行计划"),
                                          steps, do_confirm, do_dismiss, extra))
        self._scroll_bottom()

    def _tool_rewrite(self, cfg, args, scope):
        """chapter/volume/book 重写先生成候选，作者采纳时才修改正文。"""
        if self._task_busy():
            self._busy_msg()
            return
        instr = (args.get("instruction") or "").strip()
        chapters = self.db.get_chapters(self.current_pid)
        if scope == "chapter":
            try:
                no = int(args.get("chapter_no"))
            except (TypeError, ValueError):
                self._add_agent_bubble("重写哪一章？给我个章节号。")
                return
            hits = [c for c in chapters if c["chapter_no"] == no]
            if not hits:
                self._add_agent_bubble(f"没有找到第{no}章。")
                return
            ch = max(hits, key=lambda c: c["id"])
            vol, only = ch["volume"], no
            has_old = bool((ch["content"] or "").strip())
            note = f"重写第{no}章《{ch['title']}》"
            if not has_old:
                note = f"第{no}章还没有正文，直接写"
            title = f"重写第{no}章"
        elif scope == "volume":
            try:
                vol = int(args.get("vol"))
            except (TypeError, ValueError):
                self._add_agent_bubble("重写哪一卷？给我个卷号。")
                return
            only = 0
            n_old = sum(1 for c in chapters
                        if c["volume"] == vol and (c["content"] or "").strip())
            if n_old == 0:
                self._add_agent_bubble(
                    f"第{vol}卷还没有任何正文，无需重写——直接说「把第{vol}卷写完」。")
                return
            note = f"重写第{vol}卷全部 {n_old} 章已有正文"
            title = f"重写第{vol}卷"
        else:
            vol, only = 1, 0
            n_old = sum(1 for c in chapters if (c["content"] or "").strip())
            if n_old == 0:
                self._add_agent_bubble(
                    "这本书还没有任何正文，无需重写——直接说「全书模式」来写。")
                return
            note = f"全书重写，共 {n_old} 章已有正文将逐章重新生成"
            title = "全书重写"
        extra = f"，要求：{instr}" if instr else ""
        self._add_agent_bubble(
            f"开始{note}{extra}。逐章写作+审稿。"
            "已有正文的结果会存为候选稿，当前正文保持不变；空章会直接补写。"
            "在章节编辑器打开「重写候选」，比较后选择整章或逐段采纳。")
        if scope == "book":
            factory = lambda: execution.create_worker(
                cfg, self.db.path, self.current_pid, 1,
                force_rewrite=True, rewrite_instruction=instr, whole_book=True,
                candidate_mode=True)
        else:
            factory = lambda: execution.create_worker(
                cfg, self.db.path, self.current_pid, vol,
                force_rewrite=True, rewrite_instruction=instr, candidate_mode=True,
                only_chapter_no=only)
        self._start_pipeline(title, factory)

    # ---------- 工具执行 ----------
    def _tool_show_outline(self, args):
        """列出已入库的卷纲/章纲（本地查询，不花 token）"""
        level = args.get("level") if args.get("level") in ("卷纲", "章纲") else "卷纲"
        rows = [o for o in self.db.get_outlines(self.current_pid)
                if o["level"] == level]
        if not rows:
            self._add_agent_bubble(
                f"这本书还没有{level}。要生成的话：在左侧资源树右键生成草案，"
                "或者直接对我说「全书模式」，大纲草案会递给你确认。")
            return
        lines = [f"- {o['title']}：{(o['content'] or '')[:60]}"
                 for o in rows]
        self._add_agent_bubble(
            f"《{self.db.get_project(self.current_pid)['title']}》现有 {level} "
            f"{len(rows)} 条：\n" + "\n".join(lines))

    def _tool_ask_book(self, cfg, args):
        """书目问答（v3 P0）：检索在主线程抓证据，模型作答放子线程。
        回答只依据检索结果，查不到模型会直说——不开编辑器、不动正文。"""
        q = (args.get("question") or "").strip()
        if not q:
            self._add_agent_bubble("想问书的哪方面？直接问就行，"
                                   "比如「主角叫什么」「第3章写了啥」「那个伏笔收了没」。")
            return
        if self._task_busy():
            self._busy_msg()
            return
        ev = abookqa.gather_evidence(self.db, self.current_pid, q)
        pid = self.current_pid
        self._set_busy(True)
        self._intent_seq = getattr(self, "_intent_seq", 0) + 1
        seq = self._intent_seq
        self._qa_worker = SimpleWorker(
            lambda: abookqa.answer(cfg, ev, q), self)
        self._qa_worker.done.connect(lambda r, s=seq, book=pid: self._qa_done(s, book, r))
        self._qa_worker.failed.connect(lambda e, s=seq, book=pid: self._qa_failed(s, book, e))
        self._qa_worker.start()

    def _qa_done(self, seq, pid, text):
        if seq != self._intent_seq or pid != self.current_pid:
            return    # 已被「不等了」取消，或之后有新发送，结果作废
        self._finish_thinking()
        self._add_agent_bubble((text or "").strip() or "（空回复）")

    def _qa_failed(self, seq, pid, err):
        if seq != self._intent_seq or pid != self.current_pid:
            return
        self._finish_thinking()
        self._add_agent_bubble(f"错误： 书目问答失败：{err}")

    def _tool_open_chapter(self, args):
        try:
            no = int(args.get("chapter_no"))
        except (TypeError, ValueError):
            self._add_agent_bubble("想看哪一章？给我个章节号。")
            return
        hits = [c for c in self.db.get_chapters(self.current_pid)
                if c["chapter_no"] == no]
        if not hits:
            self._add_agent_bubble(f"没有找到第{no}章。当前书里可能还没建到这一章。")
            return
        ch = max(hits, key=lambda c: c["id"])
        self._open_chapter_dialog(ch["id"], ch["title"])
        state = "已有正文" if (ch["content"] or "").strip() else "还没有正文"
        self._add_agent_bubble(f"已打开第{no}章《{ch['title']}》（{state}），"
                               f"改完 {shortcut_hint('S')} 保存。")

    def _task_busy(self):
        return self._tasks.busy

    def _busy_msg(self):
        self._add_agent_bubble("注意： 已有一个任务在跑（见上方活动卡）。"
                               "等它完成，或在活动卡里停止后再来。")

    def _tool_write_volume(self, cfg, args):
        try:
            vol = int(args.get("vol"))
        except (TypeError, ValueError):
            self._add_agent_bubble("写哪一卷？给我个卷号，或直接点「写完下一卷」。")
            return
        if self._task_busy():
            self._busy_msg()
            return
        self._add_agent_bubble(f"好，开始写第{vol}卷：补章纲 → 逐章写作 → 审稿 → "
                               "登记设定伏笔。过程看活动卡，产物点开可改。")
        self._start_pipeline(
            f"写第{vol}卷",
            lambda: execution.create_worker(cfg, self.db.path, self.current_pid, vol))

    def _tool_write_until(self, cfg, args):
        """『写到第N章』：补齐 1..N 的缺章与空正文，不覆盖已有正文。"""
        try:
            no = int(args.get("chapter_no"))
        except (TypeError, ValueError):
            no = 0
        if not no or no < 1:
            self._add_agent_bubble("写到第几章？给我个章号，比如「写到第20章」。")
            return
        if self._task_busy():
            self._busy_msg()
            return
        chapters = self.db.get_chapters(self.current_pid)
        complete = {c["chapter_no"] for c in chapters
                    if 1 <= c["chapter_no"] <= no and (c["content"] or "").strip()}
        missing = [n for n in range(1, no + 1) if n not in complete]
        if not missing:
            self._add_agent_bubble(
                f"这本书第1至第{no}章都有正文，没有要补的。"
                "想重写某章直接说「重写第N章」。")
            return
        self._add_agent_bubble(
            f"好，检查第1至第{no}章，补齐 {len(missing)} 章缺失或空正文："
            "缺的章纲自动补齐，然后逐章写作 → 审稿 → 登记设定伏笔。过程看活动卡。")
        self._start_pipeline(
            f"补写到第{no}章",
            lambda: execution.create_worker(cfg, self.db.path, self.current_pid, 1,
                                   until_no=no))

    def _tool_whole_book(self, cfg):
        if self._task_busy():
            self._busy_msg()
            return
        # 防线：全书模式是给新书用的。对已有正文的书发起 = 空转，
        # 无论模型怎么选，这里都拦下并给出正确路径
        written = sum(1 for c in self.db.get_chapters(self.current_pid)
                      if (c["content"] or "").strip())
        if written:
            self._add_agent_bubble(
                f"这本书已经有 {written} 章正文，全书模式跑在它上面只会"
                "「已有正文，跳过」空转一圈。\n想推倒重来的话有三条路：\n"
                "- **开新书**：点书架「＋ 新书」，立好项后再对我说「全书模式」\n"
                "- **改这本**：直接告诉我想动哪块（概念/书名/设定/某几章），"
                "我们先讨论清楚，改动我递草案给你确认\n"
                "- **补完本书**：只想往后写就说「把第N卷写完」")
            return
        self._add_agent_bubble("全书模式启动：设定 → 卷纲 → 逐卷写作 → 简介，"
                               "每个阶段会把草案递给你确认。")
        self._start_pipeline(
            "全书模式",
            lambda: execution.create_worker(cfg, self.db.path, self.current_pid, 1,
                                   whole_book=True))

    def _tool_rebuild(self, cfg, args):
        """全书重构：备份→删旧卷纲/章纲/正文→重新规划→逐章重写，无确认点"""
        if self._task_busy():
            self._busy_msg()
            return
        instr = (args.get("instruction") or "").strip()
        try:
            vols = int(args.get("volumes") or 0)
        except (TypeError, ValueError):
            vols = 0
        clear_fh = bool(args.get("clear_foreshadows"))
        scope = f"重新规划 {vols} 卷并全部重写" if vols else "重新规划全书并全部重写"
        card = self._start_pipeline(
            "全书重构",
            lambda: execution.create_worker(cfg, self.db.path, self.current_pid, 1,
                                   rebuild=True, rebuild_volumes=vols,
                                   rewrite_instruction=instr,
                                   clear_foreshadows=clear_fh,
                                   whole_book=True))
        if card is not None:
            card.log(f'执行范围：{scope}；先备份原书，再准备全部大纲，通过后才替换旧稿。')
            if instr:
                card.log('原始重构要求：' + instr)
            self._add_agent_bubble(f'全书重构任务已启动：{scope}。当前先备份并生成大纲，尚未开始正文；阶段、用量与章节进度见任务卡。')

    def _restart_rebuild(self, run_id):
        if self._task_busy():
            self._busy_msg()
            return
        try:
            worker = execution.restart_rebuild_worker(self.db, int(run_id), self.current_pid)
        except Exception as exc:
            self._task_start_failed(exc)
            return
        card = self._start_pipeline('重试·全书重构', lambda: worker)
        if card is not None:
            card.log('按上次完整执行快照重新执行；新任务重新规划全书，并先备份原书。')
            card.log('原始重构要求：' + worker.rewrite_instruction)
            self._add_agent_bubble('已按上次保存的要求重新启动全书重构。先生成并校验全部大纲，再写正文；执行状态见任务卡。')

    def _task_start_failed(self, exc):
        message = f'任务未启动：{exc}'
        aiclient._log_api(f'TASK start failed: {type(exc).__name__}: {exc}')
        card = self._add_activity_card('任务启动失败')
        card.finish('任务出错')
        card.stage_label.setText('启动失败 · 未开始写作')
        card.usage_label.setText('模型用量：未发起请求')
        card.log('错误：' + message)
        self._add_agent_bubble('错误：' + message)

    def _resume_interrupted(self, run, card=None):
        """Recover through the same task service used by initial execution."""
        run_id = int(run.get("id") or 0)
        if self._task_busy():
            self._busy_msg()
            return
        try:
            worker, warnings = execution.resume_worker(self.db, run_id, self.current_pid)
        except execution.TaskBudgetExhausted:
            try:
                worker, warnings = execution.resume_worker(
                    self.db, run_id, self.current_pid,
                    budget_override=execution.budget_settings(self.db))
            except execution.TaskRecoveryError as exc:
                self._add_agent_bubble(str(exc))
                return
            limits = worker._runtime_snapshot['budget']
            label = lambda value: str(value) if value else '不限'
            answer = QMessageBox.question(
                self, '提高任务预算',
                '原任务预算已用尽。是否使用你当前设置的新累计上限继续？\n'
                f"模型调用：{label(limits['max_calls'])} 次\n"
                f"输出预算：{label(limits['output_cap'])} token\n"
                '已经消耗的用量继续计入，写作和审稿要求沿用原任务。',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return
        except execution.TaskRecoveryError as exc:
            self._add_agent_bubble(str(exc))
            return
        for warning in warnings:
            self._add_agent_bubble(warning)
        instruction = worker.rewrite_instruction
        self._add_agent_bubble(
            f"继续上次中断的任务「{run['title']}」"
            + ("，要求仍是：" + instruction[:40] if instruction else "")
            + f"，模型沿用「{worker.cfg.get('name') or worker.cfg.get('model') or '原模型'}」"
            + "。已完成的章节自动跳过，从缺的地方接着写。")
        started = self._start_pipeline(f"续跑·{run['title']}", lambda: worker,
                                        parent_run_id=run_id)
        if started is not None:
            if card is not None:
                self.stream.removeWidget(card)
                card.deleteLater()

    def _start_pipeline(self, title, worker_factory, parent_run_id=0):
        """启动流水线任务并接活动卡。二次守卫（防并发路径竞态）：忙时静默拒启。"""
        if self._task_busy():
            return None
        try:
            session = self._tasks.prepare(title, worker_factory, self.current_pid, parent_run_id)
        except Exception as exc:
            self._task_start_failed(exc)
            return None
        worker, run_id = session.worker, session.run_id
        project = self.db.get_project(worker.pid)
        display_title = f"《{project['title']}》 · {title}" if project else title
        card = self._add_activity_card(display_title)
        self._active_task_card = card
        card.worker = session
        session.progress.connect(card.log)
        session.tick.connect(card.update_count)
        card.updated.connect(self._scroll_bottom)
        session.chapter_done.connect(
            lambda cid, name, _c=card, _run=run_id:
            self._chapter_completed(_c, _run, cid, name))
        # 草案正文必须随检查点一起上屏，杜绝盲签
        session.checkpoint_req.connect(lambda t, b, _c=card: _c.show_gate(t, b))
        session.finished_ok.connect(
            lambda msg, _c=card, _run=run_id, _w=worker:
            self._task_done(_c, msg, _run, _w))
        session.failed.connect(
            lambda err, _c=card, _run=run_id:
            self._task_failed(_c, err, _run))
        card.set_running(True)
        self.book_list.setEnabled(False)
        self.tree.setEnabled(False)
        try:
            session.start()
        except Exception as exc:
            session._failed(f'任务未启动：{exc}')
            session._settle()
            return None
        return card

    def _chapter_completed(self, card, run_id, cid, name):
        card.add_chapter(cid, name)
        self._refresh_workspace_summary()

    def _task_done(self, card, msg, run_id, worker):
        _status, label = outcome(worker, msg)
        card.finish(label)
        self._add_agent_bubble(msg)
        self.book_list.setEnabled(True)
        self.tree.setEnabled(True)
        self.reload_books()

    def _task_failed(self, card, err, run_id):
        card.finish("任务出错")
        self._add_agent_bubble(f"错误： 任务失败：{err}")
        self.book_list.setEnabled(True)
        self.tree.setEnabled(True)
        self._refresh_tree()

    # ---------- 快捷块 ----------
    def _next_vol(self):
        chapters = self.db.get_chapters(self.current_pid)
        zgs = [o for o in self.db.get_outlines(self.current_pid)
               if o["level"] == "章纲"]
        # 卷号 0 = 未分卷孤儿章纲，不参与「下一卷」推断（P2 进度口径）
        vols = sorted({o["volume"] for o in zgs if o["volume"]}
                      | {c["volume"] for c in chapters}) or [1]
        for v in vols:
            chs = [c for c in chapters if c["volume"] == v]
            if not chs or any(not (c["content"] or "").strip() for c in chs):
                return v
        return max(vols) + 1

    def _chip_next_vol(self):
        if not self.current_pid:
            return
        vol = self._next_vol()
        self._add_user_bubble(f"把第{vol}卷写完")
        cfg = self._cfg()
        if cfg is None:
            self._add_agent_bubble("还没有配置 AI 模型，请先点左下角「配置模型」。")
            return
        self._tool_write_volume(cfg, {"vol": vol})

    def _chip_book(self):
        if not self.current_pid:
            return
        self._add_user_bubble("启动全书模式")
        cfg = self._cfg()
        if cfg is None:
            self._add_agent_bubble("还没有配置 AI 模型，请先点左下角「配置模型」。")
            return
        self._tool_whole_book(cfg)

    def _chip_progress(self):
        if not self.current_pid:
            return
        self._add_agent_bubble(aintent.progress_text(self.db, self.current_pid))

    def _chip_latest(self):
        if not self.current_pid:
            return
        chs = self.db.get_chapters(self.current_pid)
        if not chs:
            self._add_agent_bubble("这本书还没有章节。")
            return
        ch = max(chs, key=lambda c: (c["volume"], c["chapter_no"]))
        self._open_chapter_dialog(ch["id"], ch["title"])
