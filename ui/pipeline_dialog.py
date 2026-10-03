# -*- coding: utf-8 -*-
"""一键写完本卷 · 流水线对话框（进度日志 / 断点续跑 / 可停止）"""
from ui.icons import icon_button, status_text
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                               QComboBox, QCheckBox, QSpinBox, QPlainTextEdit,
                               QProgressBar, QPushButton, QFormLayout, QGroupBox,
                               QMessageBox)

from ai.pipeline import DEFAULT_MIN_CHARS
import task_ledger as ledger
import task_execution as execution
from ui.task_coordinator import get_coordinator


class PipelineDialog(QDialog):
    chapter_done = Signal(int, str)  # 转发给主窗口刷新树

    def __init__(self, db, pid, vol, cfg, parent=None, title=""):
        super().__init__(parent)
        self.db = db
        self._tasks = get_coordinator(db)
        self.pid = pid
        self.cfg = cfg
        self.worker = None
        self.run_id = 0
        self.setWindowTitle("一键写完本卷 · 流水线")
        self.resize(640, 560)

        root = QVBoxLayout(self)
        head = QLabel(f"《{title}》 ｜ 给它一卷的目标，它自己串起 "
                      "章纲→章节卡→正文→摘要→滚动摘要 全链路。")
        head.setWordWrap(True)
        root.addWidget(head)

        form = QFormLayout()
        self.cb_vol = QComboBox()
        form.addRow("目标卷：", self.cb_vol)
        root.addLayout(form)
        self._load_volumes(vol)

        opt = QGroupBox("选项")
        ol = QVBoxLayout(opt)
        self.ck_auto_outline = QCheckBox("缺章纲时自动生成（直接入库，可后续手动改）")
        self.ck_auto_outline.setChecked(True)
        self.ck_skip = QCheckBox("跳过已有正文的章节（断点续跑，不覆盖人工内容）")
        self.ck_skip.setChecked(True)
        self.ck_card = QCheckBox("缺章节卡时自动补齐")
        self.ck_card.setChecked(True)
        self.ck_tools = QCheckBox("写完自动登记新设定/伏笔；如已开启故事记忆自动分析，也会提取事实候选")
        self.ck_tools.setChecked(True)
        self.ck_review = QCheckBox("写完自动审稿，不合格自动重写（每章多 1-3 次调用）")
        self.ck_review.setChecked(True)
        self.ck_distill = QCheckBox("卷末把高频审稿意见沉淀进风格规范（越写越贴你的要求）")
        self.ck_distill.setChecked(True)
        revrow = QHBoxLayout()
        revrow.addWidget(QLabel("审稿通过分数线："))
        self.sp_score = QSpinBox()
        self.sp_score.setRange(50, 95)
        self.sp_score.setValue(75)
        revrow.addWidget(self.sp_score)
        revrow.addStretch(1)
        minrow = QHBoxLayout()
        minrow.addWidget(QLabel("单章最低字数（低于则自动重写，最多 3 次）："))
        self.sp_min = QSpinBox()
        self.sp_min.setRange(200, 5000)
        self.sp_min.setValue(DEFAULT_MIN_CHARS)
        minrow.addWidget(self.sp_min)
        minrow.addStretch(1)
        for w in (self.ck_auto_outline, self.ck_skip, self.ck_card,
                  self.ck_tools, self.ck_review, self.ck_distill):
            ol.addWidget(w)
        ol.addLayout(revrow)
        ol.addLayout(minrow)
        root.addWidget(opt)

        self.btn_start = QPushButton("开始（写单卷）")
        self.btn_book = icon_button("全书模式（设定→卷纲→逐卷写作）", 'play')
        self.btn_stop = QPushButton("停止")
        self.btn_stop.setEnabled(False)
        self.btn_close = QPushButton("关闭")
        btns = QHBoxLayout()
        btns.addWidget(self.btn_start)
        btns.addWidget(self.btn_book)
        btns.addWidget(self.btn_stop)
        btns.addStretch(1)
        btns.addWidget(self.btn_close)
        root.addLayout(btns)

        # 全书模式检查点：草案预览 + 人工放行
        self.gate_box = QGroupBox("检查点 · 请审核草案")
        gv = QVBoxLayout(self.gate_box)
        self.gate_title = QLabel("")
        self.gate_title.setWordWrap(True)
        self.gate_text = QPlainTextEdit()
        self.gate_text.setReadOnly(True)
        gbtns = QHBoxLayout()
        self.btn_accept = icon_button("采纳入库并继续", 'check')
        self.btn_skip_stage = icon_button("跳过此阶段", 'skip')
        self.btn_stop_book = icon_button("停止全书", 'stop')
        gbtns.addWidget(self.btn_accept)
        gbtns.addWidget(self.btn_skip_stage)
        gbtns.addStretch(1)
        gbtns.addWidget(self.btn_stop_book)
        gv.addWidget(self.gate_title)
        gv.addWidget(self.gate_text, 1)
        gv.addLayout(gbtns)
        self.gate_box.setVisible(False)
        root.addWidget(self.gate_box)

        self.bar = QProgressBar()
        self.bar.setFormat("%v / %m 章")
        root.addWidget(self.bar)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        root.addWidget(self.log, 1)

        if cfg is None:
            self.log.setPlainText("错误： 还没有配置 AI 模型。请先在主界面右侧「配置」里添加模型，再运行流水线。")
            self.btn_start.setEnabled(False)

        self.btn_start.clicked.connect(self._start)
        self.btn_book.clicked.connect(self._start_book)
        self.btn_stop.clicked.connect(self._stop)
        self.btn_close.clicked.connect(self._close)
        self.btn_accept.clicked.connect(lambda: self._gate_answer(False))
        self.btn_skip_stage.clicked.connect(lambda: self._gate_answer(True))
        self.btn_stop_book.clicked.connect(self._gate_stop)

    # ---------- 卷列表 ----------
    def _load_volumes(self, cur):
        """候选卷 = 卷纲条目卷号 ∪ 现有章节卷号（预选 cur）"""
        from ai.context import eff_vol, sorted_vol_outlines
        vols = sorted_vol_outlines(self.db, self.pid)
        names = {}
        for o in vols:
            names[eff_vol(o, vols)] = o["title"]
        for c in self.db.get_chapters(self.pid):
            names.setdefault(c["volume"], f"第{c['volume']}卷")
        if not names:
            names = {1: "第1卷"}
        for v in sorted(names):
            self.cb_vol.addItem(f"第{v}卷 ｜ {names[v]}", v)
        idx = self.cb_vol.findData(int(cur or 1))
        self.cb_vol.setCurrentIndex(max(idx, 0))

    # ---------- 运行 ----------
    def _make_worker(self, whole_book):
        if self.cfg is None:
            return None
        if self._tasks.busy:
            self._log('已有一个写作任务在运行；请先完成或停止任务，再开始新的任务。')
            return None
        vol = self.cb_vol.currentData() or 1
        self.worker = execution.create_worker(
            self.cfg, self.db.path, self.pid, vol,
            auto_outline=self.ck_auto_outline.isChecked(),
            skip_existing=self.ck_skip.isChecked(),
            gen_card=self.ck_card.isChecked(),
            min_chars=self.sp_min.value(),
            use_tools=self.ck_tools.isChecked(),
            review=self.ck_review.isChecked(),
            min_score=self.sp_score.value(),
            distill_style=self.ck_distill.isChecked(),
            whole_book=whole_book)
        try:
            self.session = self._tasks.prepare(
                '经典工作台·全书' if whole_book else f'经典工作台·第{vol}卷',
                lambda: self.worker, self.pid)
            self.run_id = self.session.run_id
        except execution.TaskRecoveryError as exc:
            self.worker = None
            QMessageBox.information(self, '无法开始任务', str(exc))
            return None
        self.session.progress.connect(self._log)
        self.session.tick.connect(self._tick)
        self.session.chapter_done.connect(self.chapter_done.emit)
        self.session.checkpoint_req.connect(self._show_gate)
        self.session.finished_ok.connect(self._done)
        self.session.failed.connect(self._failed)
        self.bar.setValue(0)
        self.btn_start.setEnabled(False)
        self.btn_book.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.cb_vol.setEnabled(False)
        return self.worker

    def _start(self):
        if self._make_worker(whole_book=False):
            self.session.start()

    def _start_book(self):
        if self._make_worker(whole_book=True):
            self.session.start()

    # ---------- 检查点 ----------
    def _show_gate(self, title, body):
        self.gate_title.setText(title)
        self.gate_text.setPlainText(body)
        self.gate_box.setVisible(True)
        self.btn_accept.setEnabled(True)
        self.btn_skip_stage.setEnabled(True)
        self.btn_stop_book.setEnabled(True)

    def _gate_answer(self, skip):
        if not self.worker:
            return
        self.gate_box.setVisible(False)
        if skip:
            self.worker.gate_skip()
        else:
            self.worker.gate_continue()

    def _gate_stop(self):
        if not self.worker:
            return
        self.gate_box.setVisible(False)
        self.worker.stop()          # stop() 内部会放行门控

    def _stop(self):
        if self.worker:
            self.worker.stop()
            self._log("停止请求已发出（等当前调用返回后生效）…")
        self.btn_stop.setEnabled(False)

    def _log(self, msg):
        self.log.appendPlainText(status_text(msg))

    def _tick(self, done, total):
        self.bar.setMaximum(total)
        self.bar.setValue(done)

    def _done(self, msg):
        usage = ledger.get_usage(self.db, self.run_id)
        if usage is not None:
            self._log(f"用量：调用 {usage['calls']} 次，已知输出 {usage['completion_tokens']} token，"
                      f"未知 {usage['unknown_calls']} 次")
        self._log("== " + msg)
        self._reset_buttons()

    def _failed(self, err):
        self._log(f"错误： 流水线出错：{err}")
        self._reset_buttons()

    def _reset_buttons(self):
        self.btn_start.setEnabled(self.cfg is not None)
        self.btn_book.setEnabled(self.cfg is not None)
        self.btn_stop.setEnabled(False)
        self.cb_vol.setEnabled(True)

    def _close(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.gate_continue()   # 若阻塞在检查点，放行以便退出
            self.worker.wait(2000)
            if self.worker.isRunning():
                self._log('任务正在停止，请稍后关闭窗口。')
                return
        self.reject()
