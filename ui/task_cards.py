"""Reusable conversation and task views; no task persistence logic."""
from ui.icons import icon_button, IconLabel, status_text, legacy_ui_text
import time
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (QFrame, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QPlainTextEdit, QScrollArea, QWidget, QSizePolicy, QProgressBar)

class ActivityCard(QFrame):
    """任务活动卡：标题 + 可展开日志 + 产物章节按钮 + 检查点（草案预览+放行按钮）"""
    open_chapter = Signal(int, str)
    updated = Signal()      # 有新日志 → 主窗口跟随滚动

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setObjectName("activityCard")
        self.worker = None
        self._done = False
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(10)
        head = QHBoxLayout()
        self.title_label = QLabel(title)
        self.title_label.setObjectName("activityTitle")
        head.addWidget(self.title_label, 1)
        self.status_label = QLabel('准备')
        self.status_label.setObjectName('taskStatus')
        head.addWidget(self.status_label)
        self.btn_toggle = QPushButton("日志")
        self.btn_toggle.setObjectName("logToggleBtn")
        self.btn_toggle.setFlat(True)
        self.btn_toggle.clicked.connect(self._toggle_log)
        head.addWidget(self.btn_toggle)
        self.btn_stop_run = icon_button("停止", 'stop')
        self.btn_stop_run.setObjectName("logToggleBtn")
        self.btn_stop_run.setCursor(Qt.PointingHandCursor)
        self.btn_stop_run.setToolTip("请求停止：当前模型调用返回后生效（最长约 3 分钟）")
        self.btn_stop_run.setVisible(False)
        self.btn_stop_run.clicked.connect(self._stop_run)
        head.addWidget(self.btn_stop_run)
        v.addLayout(head)
        self.stage_label = QLabel('准备任务…')
        self.stage_label.setObjectName('activityStage')
        v.addWidget(self.stage_label)
        self.usage_label = QLabel('模型用量：等待请求')
        self.usage_label.setObjectName('activityUsage')
        v.addWidget(self.usage_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName('taskProgress')
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.setRange(0, 1)
        v.addWidget(self.progress_bar)
        self._started_at = 0.0
        self._stage_text = '准备任务'
        self._count_text = ''
        self._elapsed = QTimer(self)
        self._elapsed.timeout.connect(self._refresh_stage)

        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("activityLog")
        self.log_view.setReadOnly(True)
        self.log_view.setFont(self._mono())
        self.log_view.setVisible(False)
        self.log_view.setMinimumHeight(120)
        self.log_view.setMaximumHeight(260)
        v.addWidget(self.log_view)

        self.chapter_scroller = QScrollArea()
        self.chapter_scroller.setWidgetResizable(True)
        self.chapter_scroller.setFrameShape(QFrame.NoFrame)
        self.chapter_scroller.setFixedHeight(44)
        self.chapter_scroller.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        chapter_host = QWidget()
        self.chapters_row = QHBoxLayout(chapter_host)
        self.chapters_row.setContentsMargins(0, 0, 0, 0)
        self.chapters_row.setSpacing(6)
        self.chapter_scroller.setWidget(chapter_host)
        self.chapter_scroller.hide()
        v.addWidget(self.chapter_scroller)

        self.gate_title = QLabel("")
        self.gate_title.setObjectName("gateTitle")
        self.gate_title.setVisible(False)
        v.addWidget(self.gate_title)
        # 检查点草案预览：让用户看清要采纳的到底是什么
        self.gate_text = QPlainTextEdit()
        self.gate_text.setObjectName("activityLog")
        self.gate_text.setReadOnly(True)
        self.gate_text.setFont(self._mono())
        self.gate_text.setVisible(False)
        self.gate_text.setMinimumHeight(120)
        self.gate_text.setMaximumHeight(220)
        v.addWidget(self.gate_text)
        gate_row = QHBoxLayout()
        self.btn_accept = icon_button("采纳入库", 'check')
        self.btn_skip = icon_button("跳过", 'skip')
        self.btn_stop = icon_button("停止", 'stop')
        for b in (self.btn_accept, self.btn_skip, self.btn_stop):
            b.setVisible(False)
            gate_row.addWidget(b)
        gate_row.addStretch(1)
        v.addLayout(gate_row)
        self.btn_accept.clicked.connect(self._gate_continue)
        self.btn_skip.clicked.connect(self._gate_skip)
        self.btn_stop.clicked.connect(self._gate_stop)

    @staticmethod
    def _mono():
        from PySide6.QtGui import QFont
        return QFont("Consolas", 9)

    def _toggle_log(self):
        self.log_view.setVisible(not self.log_view.isVisible())

    def log(self, msg):
        msg = status_text(msg)
        if msg.startswith('—— ['):
            self._stage_text = msg.split('] ', 1)[-1][:55]
            self._refresh_stage()
        elif '当前步骤：' in msg:
            self._stage_text = msg.split('当前步骤：', 1)[-1][:55]
            self._refresh_stage()
        elif msg.startswith('模型用量：'):
            self.usage_label.setText(msg)
        if msg.strip().startswith(('错误：', '注意：')):
            self.log_view.setVisible(True)
        self.log_view.appendPlainText(msg)
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())
        self.updated.emit()    # 让主界面跟着活动滚

    def set_running(self, running):
        self.btn_stop_run.setVisible(running)
        self.status_label.setText('运行中' if running else '已结束')
        if running:
            self._started_at = time.monotonic()
            self._elapsed.start(1000)
        else:
            self._elapsed.stop()

    def update_count(self, done, total):
        self._count_text = f' · 本卷 {done}/{total} 章' if total else ''
        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(done)
        self._refresh_stage()

    def _refresh_stage(self):
        elapsed = int(time.monotonic() - self._started_at) if self._started_at else 0
        self.stage_label.setText(f'{self._stage_text}{self._count_text} · 已运行 {elapsed//60}:{elapsed%60:02d}')

    def _stop_run(self):
        if self.worker:
            self.log("已请求停止：等当前模型调用返回后生效（最多约 3 分钟），"
                     "已完成部分保留。")
            self.btn_stop_run.setEnabled(False)
            self.worker.stop()

    def add_chapter(self, cid, name):
        btn = icon_button(f"{name}", 'pen')
        btn.setObjectName("chapterBtn")
        btn.setFlat(True)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda _c=False, i=cid, n=name:
                            self.open_chapter.emit(i, n))
        self.chapters_row.addWidget(btn)
        self.chapter_scroller.show()

    def finish(self, final_title=None):
        self._done = True
        self.set_running(False)
        if final_title:
            self.title_label.setText(final_title)
            self.status_label.setText('待处理' if '出错' in final_title else '已结束')
        self.log_view.setVisible(False)
        self.btn_stop_run.setVisible(False)
        self._hide_gate()

    def show_gate(self, title, body=""):
        """检查点：标题 + 草案正文预览 + 放行按钮。正文必须可见，杜绝盲签"""
        self.gate_title.setText('等待确认 · ' + title)
        self.gate_title.setVisible(True)
        self.gate_text.setPlainText(body or "（本阶段无草案正文）")
        self.gate_text.setVisible(True)
        for b in (self.btn_accept, self.btn_skip, self.btn_stop):
            b.setVisible(True)
        self.log_view.setVisible(True)

    def _hide_gate(self):
        self.gate_title.setVisible(False)
        self.gate_text.setVisible(False)
        for b in (self.btn_accept, self.btn_skip, self.btn_stop):
            b.setVisible(False)

    def _gate_continue(self):
        if self.worker:
            self.log("已采纳，继续下一阶段…")
            self._hide_gate()
            self.worker.gate_continue()

    def _gate_skip(self):
        if self.worker:
            self.log("已跳过此阶段")
            self._hide_gate()
            self.worker.gate_skip()

    def _gate_stop(self):
        if self.worker:
            self.log("已请求停止")
            self._hide_gate()
            self.worker.stop()

def _bubble_frame(text, name):
    """返回 (气泡frame, 行布局)；行布局含对齐 stretch，用 insertLayout 上屏。
    颜色由全局 chat_qss 的对象名选择器接管，随主题联动。"""
    if name == 'agentBubble':
        text = legacy_ui_text(text)
    f = QFrame()
    f.setObjectName(name)          # userBubble / agentBubble
    inner = QVBoxLayout(f)
    inner.setContentsMargins(0, 0, 0, 0)
    lab = QLabel(text)
    lab.setWordWrap(True)
    if name == "agentBubble":
        # AI 回复常带 markdown（加粗/列表），渲染而不是裸显示
        lab.setTextFormat(Qt.TextFormat.MarkdownText)
    lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
    lab.setMaximumWidth(620)
    if name == 'userBubble':
        first_line = (text or '').split('\n', 1)[0]
        lab.setMinimumWidth(min(360, max(60, lab.fontMetrics().horizontalAdvance(first_line) + 12)))
    inner.addWidget(lab)
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    if name == "userBubble":
        row.addStretch(1)
        row.addWidget(f)
    else:
        f.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        row.addWidget(f, 1)
    return f, row

class PlanCard(QFrame):
    """P1 两段式执行：大动作（全书重构/全书重写）先渲染一行计划，
    用户点「确认执行」才真跑；「先不动」搁置，调整要求直接再打字。"""

    def __init__(self, title, steps, on_confirm, on_dismiss, extra=None,
                 parent=None):
        super().__init__(parent)
        self.setObjectName("agentBubble")     # 复用气泡配色，随主题联动
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        head = QLabel(f"**{title}**（大动作，确认后才执行）")
        head.setTextFormat(Qt.TextFormat.MarkdownText)
        body = QLabel(steps.replace("\n", "  \n"))
        body.setTextFormat(Qt.TextFormat.MarkdownText)
        for lab in (head, body):
            lab.setWordWrap(True)
            lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lab.setMaximumWidth(620)
            if lab is head:
                heading = QHBoxLayout()
                heading.setSpacing(8)
                heading.addWidget(IconLabel('shield', self))
                heading.addWidget(lab, 1)
                v.addLayout(heading)
            else:
                v.addWidget(lab)
        if extra is not None:                 # 可选勾选项（如伏笔台账处理）
            v.addWidget(extra)
        h = QHBoxLayout()
        h.setContentsMargins(0, 0, 0, 0)
        self.btn_ok = icon_button("确认执行", 'play')
        self.btn_ok.setObjectName("sendBtn")
        self.btn_no = icon_button("先不动", 'pause')
        self.btn_no.setObjectName("logToggleBtn")
        for b in (self.btn_ok, self.btn_no):
            b.setCursor(Qt.PointingHandCursor)
            h.addWidget(b)
        h.addStretch(1)
        v.addLayout(h)
        self._on_confirm = on_confirm
        self._on_dismiss = on_dismiss
        self.btn_ok.clicked.connect(self._confirm)
        self.btn_no.clicked.connect(self._dismiss)

    def _confirm(self):
        outcome = self._on_confirm()
        if outcome is True:
            self._lock("已确认，执行中…")
        elif outcome is False:
            self._lock("计划已失效")

    def _dismiss(self):
        self._lock("已搁置")
        self._on_dismiss()

    def _lock(self, text):
        self.btn_ok.setEnabled(False)
        self.btn_no.setEnabled(False)
        self.btn_ok.setText(text)
