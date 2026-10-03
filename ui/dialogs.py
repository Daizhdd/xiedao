# -*- coding: utf-8 -*-
"""弹窗：项目 / 词条 / 章节 / 模型配置 / 版本历史 / 大纲 / 伏笔"""
import re

from PySide6.QtWidgets import (QDialog, QFormLayout, QLineEdit, QComboBox,
                               QTextEdit, QVBoxLayout, QHBoxLayout, QPushButton,
                               QListWidget, QListWidgetItem, QMessageBox,
                               QDialogButtonBox, QSpinBox, QLabel, QSplitter,
                               QGroupBox, QCheckBox, QScrollArea, QWidget, QFrame)
from PySide6.QtCore import Qt, Signal, QThread

from ai import client as aiclient
from ai import prompts
from ai import prefs as aprefs
from ai import story_memory


class DialogWorker(QThread):
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn())
        except Exception as e:  # noqa
            self.failed.emit(str(e))


def _okcancel():
    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    bb.button(QDialogButtonBox.Ok).setText("确定")
    bb.button(QDialogButtonBox.Cancel).setText("取消")
    return bb


# ---------- 项目 ----------
class ProjectDialog(QDialog):
    def __init__(self, db, pid=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.pid = pid
        self.setWindowTitle("编辑项目" if pid else "新建项目")
        self.setMinimumWidth(460)
        f = QFormLayout(self)
        row = self.db.get_project(pid) if pid else None
        self.ed_title = QLineEdit(row["title"] if row else "")
        self.ed_logline = QLineEdit(row["logline"] if row else "")
        self.ed_genre = QLineEdit(row["genre"] if row else "")
        self.ed_audience = QLineEdit(row["audience"] if row else "")
        self.ed_intro = QTextEdit(row["intro"] if row else "")
        self.ed_intro.setFixedHeight(90)
        self.ed_intro.setPlaceholderText("平台上传用的作品简介（AI 可生成，也可自己写）")
        self.ed_selling = QTextEdit(row["selling_point"] if row else "")
        self.ed_selling.setFixedHeight(70)
        self.cb_status = QComboBox()
        self.cb_status.addItems(["立项", "写作中", "完结"])
        if row:
            self.cb_status.setCurrentText(row["status"])
        # v1.7 全书规划：总卷数 / 每卷章数（生成卷纲与章纲按此编号）
        self.sp_volumes = QSpinBox()
        self.sp_volumes.setRange(0, 99)
        self.sp_chapters = QSpinBox()
        self.sp_chapters.setRange(1, 200)
        self.sp_volumes.setValue(row["plan_volumes"] if row else 6)
        self.sp_chapters.setValue(row["plan_chapters"] if row else 10)
        self.sp_volumes.setToolTip("0 = 未设定（生成卷纲时 AI 自定）；设定后一次性规划全书分卷")
        self.sp_chapters.setToolTip("每卷生成的章纲条数；第N卷的章节号区间=(N-1)*本值+1 ～ N*本值")
        f.addRow("书名 *", self.ed_title)
        f.addRow("一句话概念", self.ed_logline)
        f.addRow("题材赛道", self.ed_genre)
        f.addRow("平台/读者", self.ed_audience)
        f.addRow("作品简介", self.ed_intro)
        f.addRow("核心卖点", self.ed_selling)
        f.addRow("计划总卷数", self.sp_volumes)
        f.addRow("每卷章数", self.sp_chapters)
        f.addRow("状态", self.cb_status)
        btns = QHBoxLayout()
        self.btn_ai = QPushButton("AI 帮我填")
        self.btn_ai.clicked.connect(self._ai_fill)
        self.btn_intro_ai = QPushButton("AI 生成简介")
        self.btn_intro_ai.clicked.connect(self._ai_intro)
        btns.addWidget(self.btn_ai)
        btns.addWidget(self.btn_intro_ai)
        bb = _okcancel()
        bb.accepted.connect(self._save)
        btns.addWidget(bb)
        f.addRow(btns)

    def _ai_intro(self):
        cfg = self.db.get_default_config()
        if cfg is None:
            QMessageBox.warning(self, "提示", "请先在主界面右侧「配置」中添加 AI 模型")
            return
        self.btn_intro_ai.setEnabled(False)
        self.btn_intro_ai.setText("AI 生成中…")
        user = prompts.gen_intro_draft(
            self.ed_title.text().strip() or "替死鬼",
            self.ed_logline.text().strip(),
            self.ed_genre.text().strip(),
            self.ed_selling.toPlainText().strip())
        self._w2 = DialogWorker(lambda: aiclient.chat_once(
            cfg, [{"role": "system", "content": prompts.SYSTEM_ASSIST},
                  {"role": "user", "content": user}], temperature=0.8), self)
        self._w2.done.connect(self._intro_ready)
        self._w2.failed.connect(self._intro_err)
        self._w2.start()

    def _intro_ready(self, text):
        self.btn_intro_ai.setEnabled(True)
        self.btn_intro_ai.setText("AI 生成简介")
        self.ed_intro.setPlainText(text.strip().strip('"').strip())

    def _intro_err(self, err):
        self.btn_intro_ai.setEnabled(True)
        self.btn_intro_ai.setText("AI 生成简介")
        QMessageBox.warning(self, "AI 出错", err)

    def _ai_fill(self):
        cfg = self.db.get_default_config()
        if cfg is None:
            QMessageBox.warning(self, "提示", "请先在主界面右侧「配置」中添加 AI 模型")
            return
        self.btn_ai.setEnabled(False)
        self.btn_ai.setText("AI 生成中…")
        user = prompts.gen_project_draft(
            self.ed_genre.text().strip(),
            self.ed_logline.text().strip(),
            self.ed_audience.text().strip())
        self._w = DialogWorker(lambda: aiclient.chat_once(
            cfg, [{"role": "system", "content": prompts.SYSTEM_ASSIST},
                  {"role": "user", "content": user}], temperature=0.8), self)
        self._w.done.connect(self._draft_ready)
        self._w.failed.connect(self._draft_err)
        self._w.start()

    def _draft_ready(self, text):
        self.btn_ai.setEnabled(True)
        self.btn_ai.setText("AI 帮我填")
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        log, aud, sells, title_cand = "", "", [], ""
        for l in lines:
            b = re.sub(r"^[-•*]\s*", "", l)
            if "一句话概念" in b:
                log = b.split("：", 1)[-1].strip() if "：" in b else b
            elif "读者" in b and not aud:
                aud = b.split("：", 1)[-1].strip() if "：" in b else b
            elif "标题" in b or "书名" in b:
                cand = b.split("：", 1)[-1] if "：" in b else b
                title_cand = title_cand or re.split(r"[、，,]", cand)[0].strip().strip("《》")
            elif any(k in b for k in ("卖点", "爽点", "核心", "差异")):
                sells.append(b)
        if log and not self.ed_logline.text().strip():
            self.ed_logline.setText(log)
        if aud and not self.ed_audience.text().strip():
            self.ed_audience.setText(aud)
        if title_cand and not self.ed_title.text().strip():
            self.ed_title.setText(title_cand)
        if sells:
            cur = self.ed_selling.toPlainText().strip()
            self.ed_selling.setPlainText((cur + "\n" if cur else "") + "\n".join(sells))

    def _draft_err(self, err):
        self.btn_ai.setEnabled(True)
        self.btn_ai.setText("AI 帮我填")
        QMessageBox.warning(self, "AI 出错", err)

    def _save(self):
        title = self.ed_title.text().strip()
        if not title:
            QMessageBox.warning(self, "提示", "书名不能为空")
            return
        data = dict(
            title=title,
            logline=self.ed_logline.text().strip(),
            genre=self.ed_genre.text().strip(),
            audience=self.ed_audience.text().strip(),
            selling_point=self.ed_selling.toPlainText().strip(),
            intro=self.ed_intro.toPlainText().strip(),
            status=self.cb_status.currentText(),
            plan_volumes=self.sp_volumes.value(),
            plan_chapters=self.sp_chapters.value(),
        )
        if self.pid:
            self.db.update_project(self.pid, **data)
        else:
            self.pid = self.db.create_project(**data)
        self.accept()


# ---------- 设定词条 ----------
class SettingDialog(QDialog):
    def __init__(self, db, pid, category, sid=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.pid = pid
        self.sid = sid
        self.setWindowTitle("编辑词条" if sid else "新建词条")
        self.setMinimumWidth(460)
        f = QFormLayout(self)
        row = self.db.get_settings(pid) and next(
            (r for r in self.db.get_settings(pid) if r["id"] == sid), None) if sid else None
        self.cb_cat = QComboBox()
        self.cb_cat.addItems(["力量体系", "地理", "历史", "术语", "人物"])
        if row:
            self.cb_cat.setCurrentText(row["category"])
        else:
            self.cb_cat.setCurrentText(category)
        self.ed_term = QLineEdit(row["term"] if row else "")
        self.ed_def = QTextEdit(row["definition"] if row else "")
        self.ed_def.setMinimumHeight(140)
        f.addRow("分类", self.cb_cat)
        f.addRow("词条名 *", self.ed_term)
        f.addRow("标准定义", self.ed_def)
        bb = _okcancel()
        bb.accepted.connect(self._save)
        f.addRow(bb)

    def _save(self):
        term = self.ed_term.text().strip()
        if not term:
            QMessageBox.warning(self, "提示", "词条名不能为空")
            return
        if self.sid:
            self.db.update_setting(self.sid,
                                   category=self.cb_cat.currentText(),
                                   term=term,
                                   definition=self.ed_def.toPlainText().strip())
        else:
            self.db.add_setting(self.pid, self.cb_cat.currentText(), term,
                                self.ed_def.toPlainText().strip())
        self.accept()


# ---------- 新建章节 ----------
class ChapterDialog(QDialog):
    def __init__(self, db, pid, parent=None):
        super().__init__(parent)
        self.db = db
        self.pid = pid
        self.setWindowTitle("新建章节")
        f = QFormLayout(self)
        chapters = self.db.get_chapters(pid)
        next_no = max([c["chapter_no"] for c in chapters], default=0) + 1
        self.sp_vol = QSpinBox()
        self.sp_vol.setRange(1, 999)
        self.sp_vol.setValue(max([c["volume"] for c in chapters], default=1))
        self.sp_no = QSpinBox()
        self.sp_no.setRange(1, 9999)
        self.sp_no.setValue(next_no)
        self.ed_title = QLineEdit(f"第{next_no}章")
        f.addRow("卷", self.sp_vol)
        f.addRow("章节号", self.sp_no)
        f.addRow("标题", self.ed_title)
        bb = _okcancel()
        bb.accepted.connect(self._save)
        f.addRow(bb)

    def _save(self):
        title = self.ed_title.text().strip() or f"第{self.sp_no.value()}章"
        self.cid = self.db.create_chapter(
            self.pid, self.sp_vol.value(), self.sp_no.value(), title)
        self.accept()


# ---------- 模型配置 ----------
class ConfigDialog(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("AI 模型配置")
        self.resize(700, 690)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)
        header = QLabel('模型连接与写作设置')
        header.setObjectName('dialogTitle')
        lay.addWidget(header)
        sub = QLabel('模型连接在上方；写作偏好、故事记忆和任务预算可独立保存。')
        sub.setObjectName('dialogSubtitle')
        lay.addWidget(sub)
        self.listw = QListWidget()
        self.listw.setMinimumHeight(88)
        self.listw.setMaximumHeight(140)
        lay.addWidget(self.listw)
        btns = QHBoxLayout()
        b_add = QPushButton("新增")
        b_edit = QPushButton("编辑")
        b_del = QPushButton("删除")
        b_default = QPushButton("设为默认")
        for b, fn in ((b_add, self._add), (b_edit, self._edit),
                      (b_del, self._delete), (b_default, self._set_default)):
            b.clicked.connect(fn)
            btns.addWidget(b)
        btns.addStretch(1)
        lay.addLayout(btns)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        settings = QWidget()
        groups = QVBoxLayout(settings)
        groups.setContentsMargins(0, 6, 4, 6)
        groups.setSpacing(12)
        groups.addWidget(self._build_write_prefs())
        groups.addWidget(self._build_memory_prefs())
        groups.addWidget(self._build_task_budget())
        groups.addStretch(1)
        scroll.setWidget(settings)
        lay.addWidget(scroll, 1)
        footer = QHBoxLayout()
        footer.addStretch(1)
        b_close = QPushButton("关闭")
        b_close.clicked.connect(self.accept)
        footer.addWidget(b_close)
        lay.addLayout(footer)
        self._reload()

    def _reload(self):
        self.listw.clear()
        self._rows = {}
        for c in self.db.get_ai_configs():
            star = "（默认）" if c["is_default"] else ""
            item = QListWidgetItem(f"{c['name']}（{c['provider']}）{star}")
            item.setData(1, c["id"])
            self.listw.addItem(item)
            self._rows[c["id"]] = c

    def _build_write_prefs(self):
        """写作偏好：MiMo 直连请求的输出预算与深度思考（其余模型不受影响）"""
        box = QGroupBox("写作偏好（仅对直连 MiMo 的请求生效，其他模型不受影响）")
        f = QFormLayout(box)

        self.cb_budget = QComboBox()
        budget_items = [("自动（推荐 16384）", "")]
        budget_items += [(str(v), str(v)) for v in
                         (8192, 12288, 16384, 24576, 32768, 49152, 65536)]
        for label, data in budget_items:
            self.cb_budget.addItem(label, data)
        stored_budget = (self.db.get_setting(aprefs.KEY_BUDGET) or "").strip()
        idx = self.cb_budget.findData(stored_budget)
        if idx < 0 and stored_budget:      # 自定义值也能原样显示
            self.cb_budget.addItem(stored_budget, stored_budget)
            idx = self.cb_budget.count() - 1
        self.cb_budget.setCurrentIndex(idx if idx >= 0 else 0)
        f.addRow("正文输出预算（token）", self.cb_budget)

        self.cb_thinking = QComboBox()
        think_items = [("自动（正文、大纲规划、摘要、审稿关闭思考）", "auto"),
                       ("全部关闭（更省额度、出稿更快）", "always_off"),
                       ("保持模型默认（思考会占用输出额度）", "always_on")]
        for label, data in think_items:
            self.cb_thinking.addItem(label, data)
        stored_think = (self.db.get_setting(aprefs.KEY_THINKING) or "").strip()
        idx = self.cb_thinking.findData(stored_think)
        self.cb_thinking.setCurrentIndex(idx if idx >= 0 else 0)
        f.addRow("深度思考", self.cb_thinking)

        tip = QLabel("仅对直连 MiMo 生效。输出预算用于正文；自动思考策略覆盖"
                     "正文、大纲规划、章节卡、摘要与审稿，避免思考耗尽额度却没有内容。")
        tip.setWordWrap(True)
        f.addRow(tip)

        row = QHBoxLayout()
        b_save = QPushButton("保存偏好")
        b_save.clicked.connect(self._save_write_prefs)
        row.addWidget(b_save)
        row.addStretch(1)
        f.addRow(row)
        return box

    def _save_write_prefs(self):
        self.db.set_setting(aprefs.KEY_BUDGET,
                            self.cb_budget.currentData() or "")
        self.db.set_setting(aprefs.KEY_THINKING,
                            self.cb_thinking.currentData() or "auto")
        QMessageBox.information(self, "已保存",
                                "写作偏好已保存，下一次写作任务开始时生效。")

    def _build_memory_prefs(self):
        box = QGroupBox('故事记忆（所有模型）')
        row = QVBoxLayout(box)
        self.ck_auto_memory = QCheckBox('写完自动分析事实候选（每章可能增加一次模型调用）')
        self.ck_auto_memory.setChecked(
            self.db.get_setting(story_memory.AUTO_EXTRACT_SETTING, 'off') == 'on')
        row.addWidget(self.ck_auto_memory)
        row.addWidget(QLabel('默认关闭；也可在章节编辑器手动点「分析当前正文」。候选需作者确认。'))
        save = QPushButton('保存故事记忆设置')
        save.clicked.connect(self._save_memory_prefs)
        row.addWidget(save)
        return box

    def _save_memory_prefs(self):
        self.db.set_setting(story_memory.AUTO_EXTRACT_SETTING,
                            'on' if self.ck_auto_memory.isChecked() else 'off')
        QMessageBox.information(self, '已保存', '故事记忆自动分析设置已保存。')

    def _build_task_budget(self):
        box = QGroupBox('单次写作任务预算（0 为不限制）')
        layout = QFormLayout(box)
        def stored_int(key, default=0):
            try:
                return max(0, int(self.db.get_setting(key, str(default)) or default))
            except (TypeError, ValueError):
                return default
        self.sp_task_calls = QSpinBox()
        self.sp_task_calls.setRange(0, 100000)
        self.sp_task_calls.setValue(stored_int('task_budget.max_calls'))
        self.sp_task_output = QSpinBox()
        self.sp_task_output.setRange(0, 10000000)
        self.sp_task_output.setValue(stored_int('task_budget.output_tokens'))
        self.sp_task_retries = QSpinBox()
        self.sp_task_retries.setRange(0, 8)
        self.sp_task_retries.setValue(stored_int('task_budget.retries', 2))
        layout.addRow('最多模型调用', self.sp_task_calls)
        layout.addRow('最多输出 token', self.sp_task_output)
        layout.addRow('每步最大重试', self.sp_task_retries)
        note = QLabel('无服务端用量时按请求上限预留预算；用量仍显示为未知。'
                      '预算在下一次任务开始时读取。')
        note.setWordWrap(True)
        layout.addRow(note)
        save = QPushButton('保存任务预算')
        save.clicked.connect(self._save_task_budget)
        layout.addRow(save)
        return box

    def _save_task_budget(self):
        for key, value in (('task_budget.max_calls', self.sp_task_calls.value()),
                           ('task_budget.output_tokens', self.sp_task_output.value()),
                           ('task_budget.retries', self.sp_task_retries.value())):
            self.db.set_setting(key, value)
        QMessageBox.information(self, '已保存', '任务预算已保存，下一次任务开始时生效。')

    def _form(self, cid=None):
        dlg = QDialog(self)
        dlg.setWindowTitle("编辑模型" if cid else "新增模型")
        f = QFormLayout(dlg)
        row = self._rows.get(cid)
        e_name = QLineEdit(row["name"] if row else "")
        e_provider = QComboBox()
        e_provider.addItems(["deepseek", "openai_compat", "ollama"])
        if row:
            e_provider.setCurrentText(row["provider"])
        e_base = QLineEdit(row["base_url"] if row else "")
        e_key = QLineEdit(row["api_key"] if row else "")
        e_model = QLineEdit(row["model"] if row else "")
        f.addRow("名称", e_name)
        f.addRow("类型", e_provider)
        f.addRow("Base URL", e_base)
        f.addRow("API Key", e_key)
        f.addRow("模型名", e_model)
        bb = _okcancel()
        f.addRow(bb)
        ok = bb.accepted
        vals = {}

        def _go():
            if not e_name.text().strip():
                QMessageBox.warning(dlg, "提示", "名称不能为空")
                return
            vals.update(name=e_name.text().strip(),
                        provider=e_provider.currentText(),
                        base_url=e_base.text().strip(),
                        api_key=e_key.text().strip(),
                        model=e_model.text().strip())
            dlg.accept()

        ok.connect(_go)
        dlg.exec()
        return vals or None

    def _add(self):
        v = self._form()
        if v:
            if self.listw.count() == 0:
                v["is_default"] = 1
            self.db.add_ai_config(**v)
            self._reload()

    def _edit(self):
        cid = self._selected()
        if not cid:
            return
        v = self._form(cid)
        if v:
            self.db.update_ai_config(cid, **v)
            self._reload()

    def _delete(self):
        cid = self._selected()
        if not cid:
            return
        self.db.delete_ai_config(cid)
        self._reload()

    def _set_default(self):
        cid = self._selected()
        if not cid:
            return
        self.db.update_ai_config(cid, is_default=1)
        self._reload()

    def _selected(self):
        it = self.listw.currentItem()
        return it.data(1) if it else None


# ---------- 大纲条目 ----------
class OutlineDialog(QDialog):
    def __init__(self, db, pid, level="章纲", oid=None, parent=None,
                 preset_volume=None):
        super().__init__(parent)
        self.db = db
        self.pid = pid
        self.oid = oid
        self.setWindowTitle("编辑大纲" if oid else "新建大纲")
        self.setMinimumWidth(520)
        row = None
        if oid:
            for o in self.db.get_outlines(pid):
                if o["id"] == oid:
                    row = o
                    break
        f = QFormLayout(self)
        self.cb_level = QComboBox()
        self.cb_level.addItems(["卷纲", "章纲"])
        self.ed_title = QLineEdit(row["title"] if row else "")
        self.ed_content = QTextEdit(row["content"] if row else "")
        self.ed_content.setMinimumHeight(160)
        max_vol = max([c["volume"] for c in db.get_chapters(pid)], default=1)
        # 卷号提示值：卷纲/章纲都按现有最大卷递增，0=未分卷
        sug = (max_vol + 1) if (not row or not row["volume"]) else row["volume"]
        self.sp_volume = QSpinBox()
        self.sp_volume.setRange(0, 999)
        self.sp_volume.setToolTip("章纲归入第几卷；卷纲与之对齐可让章节自动跟随；0=未分卷")
        if row and row["volume"]:
            self.sp_volume.setValue(row["volume"])
        elif oid:
            self.sp_volume.setValue(0)
        elif preset_volume:
            self.sp_volume.setValue(preset_volume)
        else:
            self.sp_volume.setValue(sug)
        if row:
            self.cb_level.setCurrentText(row["level"])
        else:
            self.cb_level.setCurrentText(level)
        f.addRow("层级", self.cb_level)
        f.addRow("所属卷", self.sp_volume)
        f.addRow("标题 *", self.ed_title)
        f.addRow("内容", self.ed_content)
        bb = _okcancel()
        bb.accepted.connect(self._save)
        f.addRow(bb)

    def _save(self):
        title = self.ed_title.text().strip()
        if not title:
            QMessageBox.warning(self, "提示", "标题不能为空")
            return
        vol = self.sp_volume.value() if self.cb_level.currentText() == "章纲" else self.sp_volume.value()
        if self.oid:
            self.db.update_outline(self.oid, level=self.cb_level.currentText(),
                                   title=title, content=self.ed_content.toPlainText().strip(),
                                   volume=vol)
        else:
            self.oid = self.db.add_outline(self.pid, self.cb_level.currentText(), title,
                                           self.ed_content.toPlainText().strip(), volume=vol)
        self.accept()


# ---------- 进度对账（二期：章纲 → 章节绑定情况） ----------
class AuditDialog(QDialog):
    def __init__(self, db, pid, parent=None):
        super().__init__(parent)
        self.db = db
        self.pid = pid
        p = db.get_project(pid)
        self.setWindowTitle(f"进度对账 · 《{p['title']}》")
        self.resize(760, 560)
        self._outer = QVBoxLayout(self)
        self._body = QVBoxLayout()
        self._body.setSpacing(8)
        self._outer.addLayout(self._body)
        self._populate()

    def _clear_body(self):
        while self._body.count():
            item = self._body.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
            sub = item.layout()
            if sub:
                while sub.count():
                    s2 = sub.takeAt(0)
                    if s2.widget():
                        s2.widget().deleteLater()

    def _populate(self):
        self._clear_body()
        db, pid = self.db, self.pid
        from PySide6.QtWidgets import (QTableWidget, QTableWidgetItem,
                                       QHeaderView, QRadioButton, QFrame,
                                       QScrollArea)
        from ai.parse import sane_outline_title

        # 卷纲 → 卷号映射（volume 为 0 时按排序位置推）
        vol_outlines = sorted([o for o in db.get_outlines(pid) if o["level"] == "卷纲"],
                              key=lambda x: (x["sort_no"], x["id"]))
        pos_of = {o["id"]: i + 1 for i, o in enumerate(vol_outlines)}

        chapters = db.get_chapters(pid)
        by_outline = {}
        for c in chapters:
            if c["outline_id"]:
                by_outline.setdefault(c["outline_id"], []).append(c)

        rows = []
        for o in db.get_outlines(pid):
            eff_vol = o["volume"] or (pos_of.get(o["id"], 0) if o["level"] == "卷纲" else 0)
            if o["level"] == "卷纲":
                rows.append((f"第{eff_vol}卷（卷纲）", o["title"], "—", "—"))
                continue
            chs = by_outline.get(o["id"], [])
            if chs:
                detail = "、".join(
                    f"第{c['chapter_no']}章{'' if c['content'].strip() else '（空）'}"
                    for c in chs)
                state = "已建章" + ("·有正文" if any(c["content"].strip() for c in chs) else "·正文为空")
            else:
                detail = "—"
                state = "未建章"
            rows.append((f"第{eff_vol}卷" if eff_vol else "未分卷",
                         f"[章纲] {o['title']}", state, detail))

        unbound = [c for c in chapters if not c["outline_id"]]
        for c in unbound:
            rows.append(("—", "—", "[章节] 未绑定章纲",
                         f"第{c['chapter_no']}章 {c['title']}"))

        self.table = QTableWidget(len(rows), 4)
        self.table.setHorizontalHeaderLabels(["所属卷", "大纲条目", "状态", "对应章节"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        for r, row in enumerate(rows):
            for col, val in enumerate(row):
                self.table.setItem(r, col, QTableWidgetItem(val))
        self._body.addWidget(self.table, 1)

        total_ch = len(chapters)
        written = sum(1 for c in chapters if c["content"].strip())
        info = QLabel(f"共 {total_ch} 章（{written} 章有正文，{total_ch - written} 章为空） ｜ "
                      f"{len(unbound)} 章未绑定章纲")
        self._body.addWidget(info)

        # ---- 可疑章纲/卷纲标题（去编号后不含汉字，疑似模型坏解析）----
        susp = [o for o in db.get_outlines(pid)
                if o["level"] in ("章纲", "卷纲")
                and not sane_outline_title(o["title"])]
        if susp:
            self._body.addWidget(QLabel(
                "注意： 以下大纲标题可疑（去编号后不含汉字，疑似模型坏解析），"
                "建议在资源树右键改正后再续写："))
            for o in susp[:8]:
                self._body.addWidget(QLabel(f"　· [{o['level']}] {o['title']}"))

        # ---- 重复章号：同卷同章号多章，可安全合并 ----
        dupes = db.find_duplicate_chapters(pid)
        if dupes:
            self._body.addWidget(QLabel(
                f"注意： 发现重复章号 {len(dupes)} 组。合并会把多余章的全部正文与"
                "版本史快照进保留章（不丢内容），合并前自动整书备份。"))
            for vol, no, chs in dupes:
                frame = QFrame()
                frame.setFrameShape(QFrame.StyledPanel)
                fv = QVBoxLayout(frame)
                fv.addWidget(QLabel(
                    f"第{vol}卷 第{no}章 有 {len(chs)} 章："))
                keep_default = max(chs, key=lambda c: (len(c["content"] or ""), c["id"]))
                group = {}
                for c in chs:
                    rb = QRadioButton(
                        f"id={c['id']} 《{c['title']}》 "
                        f"{len(c['content'] or '')}字 · {c['status']}")
                    rb.setChecked(c["id"] == keep_default["id"])
                    group[c["id"]] = rb
                    fv.addWidget(rb)
                btn_merge = QPushButton("合并到选中的章")
                btn_merge.setObjectName("logToggleBtn")

                def _do_merge(_=False, g=group, chs=chs):
                    keep_id = next(i for i, rb in g.items() if rb.isChecked())
                    drops = [c["id"] for c in chs if c["id"] != keep_id]
                    if QMessageBox.question(
                            self, "确认合并",
                            "将先整书备份，再把多余章并入保留章："
                            "正文与版本史全部快照保留，随后删除多余章。继续？"
                            ) != QMessageBox.StandardButton.Yes:
                        return
                    try:
                        from book_backup import backup_project
                        backup = backup_project(db, pid, reason="merge_duplicates")
                        actions = db.merge_duplicate_chapters(keep_id, drops)
                    except Exception as exc:
                        QMessageBox.critical(self, "合并未完成",
                                             f"备份或合并失败，书籍未改动：\n{exc}")
                        return
                    QMessageBox.information(
                        self, "合并完成",
                        f"备份：{backup}\n\n" + "\n".join(actions))
                    self._populate()

                fv.addWidget(btn_merge)
                self._body.addWidget(frame)


# ---------- 伏笔 ----------
class ForeshadowDialog(QDialog):
    def __init__(self, db, pid, fid=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.pid = pid
        self.fid = fid
        self.setWindowTitle("编辑伏笔" if fid else "新建伏笔")
        self.setMinimumWidth(520)
        row = None
        if fid:
            for f in self.db.get_foreshadows(pid):
                if f["id"] == fid:
                    row = f
                    break
        f = QFormLayout(self)
        self.cb_type = QComboBox()
        self.cb_type.addItems(["悬念", "物件", "身份", "线索", "冲突"])
        self.ed_content = QTextEdit(row["content"] if row else "")
        self.ed_content.setMinimumHeight(120)
        self.ed_planted = QLineEdit(row["planted_ch"] if row else "")
        self.ed_plan = QLineEdit(row["plan_ch"] if row else "")
        self.cb_status = QComboBox()
        self.cb_status.addItems(["待回收", "疑似回收", "已回收"])
        if row:
            self.cb_type.setCurrentText(row["ftype"])
            self.cb_status.setCurrentText(row["status"])
        f.addRow("类型", self.cb_type)
        f.addRow("内容 *", self.ed_content)
        f.addRow("埋设章节", self.ed_planted)
        f.addRow("计划回收章节", self.ed_plan)
        f.addRow("状态", self.cb_status)
        bb = _okcancel()
        bb.accepted.connect(self._save)
        f.addRow(bb)

    def _save(self):
        content = self.ed_content.toPlainText().strip()
        if not content:
            QMessageBox.warning(self, "提示", "伏笔内容不能为空")
            return
        if self.cb_status.currentText() == '已回收':
            old = self.db.get_foreshadow(self.fid) if self.fid else None
            if old is None or old['status'] != '已回收':
                QMessageBox.warning(self, '回收证据',
                                    '请到「伏笔证据与回收」核对疑似回收的原文后确认。')
                return
        if self.fid:
            self.db.update_foreshadow(self.fid, ftype=self.cb_type.currentText(),
                                      content=content, planted_ch=self.ed_planted.text().strip(),
                                      plan_ch=self.ed_plan.text().strip(),
                                      status=self.cb_status.currentText())
        else:
            self.fid = self.db.add_foreshadow(self.pid, content, self.cb_type.currentText(),
                                              self.ed_planted.text().strip(),
                                              self.ed_plan.text().strip(),
                                              self.cb_status.currentText())
        self.accept()


# ---------- AI 生成结果勾选入库 ----------
class AiPickDialog(QDialog):
    def __init__(self, title, items, parent=None):
        """items: [(显示文本, 结构化 dict)]，勾选后 picked() 返回选中 dict 列表"""
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(560, 420)
        self._items = items
        lay = QVBoxLayout(self)
        tip = QLabel("AI 生成的草稿，勾选要创建的条目（可全不选/部分选）：")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        self.listw = QListWidget()
        for text, data in items:
            it = QListWidgetItem(text)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked)
            it.setData(1, data)
            self.listw.addItem(it)
        lay.addWidget(self.listw)
        btns = QHBoxLayout()
        b_all = QPushButton("全选")
        b_none = QPushButton("全不选")
        b_ok = QPushButton("创建所选")
        b_cancel = QPushButton("放弃")
        b_all.clicked.connect(lambda: self._set_all(True))
        b_none.clicked.connect(lambda: self._set_all(False))
        b_ok.clicked.connect(self.accept)
        b_cancel.clicked.connect(self.reject)
        btns.addWidget(b_all); btns.addWidget(b_none)
        btns.addStretch(1)
        btns.addWidget(b_ok); btns.addWidget(b_cancel)
        lay.addLayout(btns)

    def _set_all(self, on):
        st = Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
        for i in range(self.listw.count()):
            self.listw.item(i).setCheckState(st)

    def picked(self):
        out = []
        for i in range(self.listw.count()):
            it = self.listw.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                out.append(it.data(1))
        return out


# ---------- 版本历史 ----------
class VersionDialog(QDialog):
    def __init__(self, db, chapter_id, parent=None):
        super().__init__(parent)
        self.db = db
        self.chapter_id = chapter_id
        self.setWindowTitle("版本历史")
        self.resize(600, 360)
        lay = QVBoxLayout(self)
        self.listw = QListWidget()
        lay.addWidget(self.listw)
        for v in self.db.get_versions(chapter_id):
            snap = v["snapshot"][:30].replace("\n", " ")
            item = QListWidgetItem(
                f"v{v['version']}  {v['created_at']}  {v['note']}  |  {snap}…")
            item.setData(1, v["id"])
            self.listw.addItem(item)
        btns = QHBoxLayout()
        b_restore = QPushButton("回滚到选中版本")
        b_close = QPushButton("关闭")
        b_restore.clicked.connect(self._restore)
        b_close.clicked.connect(self.accept)
        btns.addWidget(b_restore)
        btns.addStretch(1)
        btns.addWidget(b_close)
        lay.addLayout(btns)

    def _restore(self):
        it = self.listw.currentItem()
        if not it:
            return
        if QMessageBox.question(self, "确认", "回滚将覆盖当前正文（当前内容会自动存为快照）。继续？") == QMessageBox.Yes:
            self.db.restore_version(self.chapter_id, it.data(1))
            self.accept()
