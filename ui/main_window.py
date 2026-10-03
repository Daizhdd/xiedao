# -*- coding: utf-8 -*-
"""主窗口：三段式（项目树 / 编辑器 / AI 面板）"""
import re
import traceback

from PySide6.QtWidgets import (QMainWindow, QSplitter, QMessageBox, QWidget,
                               QVBoxLayout, QHBoxLayout, QLabel, QFileDialog,
                               QInputDialog)
from PySide6.QtCore import Qt, QThread, Signal, QTimer

from ui.project_tree import ProjectTree
from ui.editor import (Editor, MODE_CHAPTER, MODE_SETTING, MODE_OUTLINE,
                       MODE_FORESHADOW)
from ui.ai_panel import AIPanel
from ui.pipeline_dialog import PipelineDialog
from ui import dialogs
from ai import client as aiclient
from ai import prompts
from ai import parse as aparse
from ai.context import context_pack_for, vol_outline_for


class AIWorker(QThread):
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn())
        except Exception as e:  # noqa
            traceback.print_exc()
            self.failed.emit(str(e))


class AIStreamWorker(QThread):
    """v1.8 流式生成：delta 逐段发射，done 汇总全文"""
    delta = Signal(str)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, cfg, messages, temperature=0.8, max_tokens=4096, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.messages = messages
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._stopped = False

    def stop(self):
        self._stopped = True

    def run(self):
        buf = []

        def _on_delta(piece):
            if self._stopped:
                raise KeyboardInterrupt
            buf.append(piece)
            self.delta.emit(piece)

        try:
            text = aiclient.chat_stream(self.cfg, self.messages,
                                        temperature=self.temperature,
                                        max_tokens=self.max_tokens,
                                        on_delta=_on_delta)
        except Exception as e:  # noqa
            if not self._stopped:
                traceback.print_exc()
                self.failed.emit(str(e))
            return
        self.done.emit(text or "".join(buf))


class MainWindow(QMainWindow):
    def __init__(self, db):
        super().__init__()
        self.db = db
        self.setWindowTitle("写道 · 经典工作台")
        self.resize(1280, 800)
        self.current_pid = None
        self.current_chapter_id = None
        self._worker = None
        self._stream_worker = None
        self._gen_buf = ""
        self._flush_timer = QTimer(self, interval=120)
        self._flush_timer.timeout.connect(self._flush_stream)
        self._last_zg_vol = 1  # 批量生成章纲时记住上次归卷

        split = QSplitter(Qt.Horizontal)
        self.tree = ProjectTree(db)
        self.editor = Editor()
        self.ai = AIPanel()
        split.addWidget(self.tree)
        split.addWidget(self.editor)
        split.addWidget(self.ai)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 0)
        split.setSizes([260, 640, 320])
        self.setCentralWidget(split)

        self.statusBar().showMessage("就绪")
        self._bind()
        self._build_view_menu()

    # ---------- 三期：视图菜单（主题 / AI 面板折叠） ----------
    def _build_view_menu(self):
        from PySide6.QtWidgets import QApplication
        from ui.theme import build_qss, load_dark_pref, save_dark_pref
        mb = self.menuBar()
        mv = mb.addMenu("视图")

        app = QApplication.instance()
        dark = load_dark_pref()
        self.act_dark = mv.addAction("深色主题")
        self.act_dark.setCheckable(True)
        self.act_dark.setChecked(dark)
        if app:
            app.setStyleSheet(build_qss(dark))

        def _toggle_dark():
            d = self.act_dark.isChecked()
            save_dark_pref(d)
            if app:
                app.setStyleSheet(build_qss(d))
            self.statusBar().showMessage("已切换到" + ("深色" if d else "浅色") + "主题", 3000)

        self.act_dark.triggered.connect(_toggle_dark)

        self.act_ai = mv.addAction("显示 AI 面板")
        self.act_ai.setCheckable(True)
        self.act_ai.setChecked(True)

        def _toggle_ai(on):
            self.ai.setVisible(on)
            if on:
                split = self.centralWidget()
                if isinstance(split, QSplitter):
                    split.setSizes([260, 640, 320])

        self.act_ai.toggled.connect(_toggle_ai)
        memory_action = mv.addAction('本章故事记忆与证据')
        memory_action.triggered.connect(self._open_story_memory)
        rewrite_action = mv.addAction('本章重写候选与局部修改')
        rewrite_action.triggered.connect(self._open_rewrite_editor)
        fh_action = mv.addAction('伏笔证据与回收')
        fh_action.triggered.connect(self._open_foreshadow_evidence)
        style_action = mv.addAction('本书作品文风')
        style_action.triggered.connect(self._open_style_profile)

    def _open_style_profile(self):
        if not self.current_pid:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, '作品文风', '请先选择一本书。')
            return
        from ui.style_profile_dialog import StyleProfileDialog
        StyleProfileDialog(self.db, self.current_pid, self).exec()

    def _open_foreshadow_evidence(self):
        if not self.current_pid:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, '伏笔证据', '请先选择一本书。')
            return
        from ui.foreshadow_evidence_dialog import ForeshadowEvidenceDialog
        ForeshadowEvidenceDialog(self.db, self.current_pid, self).exec()
        self.tree.load_project_detail(self.current_pid)

    def _open_story_memory(self):
        if self.current_chapter_id is None:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, '故事记忆', '请先选择一章。')
            return
        if self.editor.current_text() != self.db.get_chapter(self.current_chapter_id)['content']:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, '故事记忆', '请先保存当前正文。')
            return
        from ui.story_memory_dialog import StoryMemoryDialog
        StoryMemoryDialog(self.db, self.current_chapter_id, self).exec()

    def _open_rewrite_editor(self):
        if self.current_chapter_id is None:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, '重写候选', '请先选择一章。')
            return
        if self.editor.current_text() != self.db.get_chapter(self.current_chapter_id)['content']:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, '重写候选', '请先保存当前正文。')
            return
        from ui.editor_dialog import ChapterEditorDialog
        ChapterEditorDialog(self.db, self.current_chapter_id, self).exec()
        self._on_chapter(self.current_chapter_id)

    # ---------- 信号绑定 ----------
    def _bind(self):
        t = self.tree
        t.project_selected.connect(self._on_project)
        t.chapter_selected.connect(self._on_chapter)
        t.setting_selected.connect(self._on_setting)
        t.outline_selected.connect(self._on_outline)
        t.foreshadow_selected.connect(self._on_foreshadow)
        t.request_new_project.connect(lambda: self._project_dialog())
        t.request_edit_project.connect(lambda pid: self._project_dialog(pid))
        t.request_delete_project.connect(self._delete_project)
        t.request_new_chapter.connect(self._chapter_dialog)
        t.request_new_setting.connect(self._setting_dialog)
        t.request_new_outline.connect(
            lambda pid, lv, vol: self._outline_dialog(pid, lv, None, vol))
        t.request_new_foreshadow.connect(self._foreshadow_dialog)
        t.request_edit_outline.connect(lambda oid: self._outline_dialog(self._pid_of_outline(oid), None, oid))
        t.request_edit_foreshadow.connect(lambda fid: self._foreshadow_dialog(self._pid_of_foreshadow(fid), fid))
        t.request_delete_outline.connect(self._delete_outline)
        t.request_delete_foreshadow.connect(self._delete_foreshadow)
        t.request_ai_gen.connect(self._ai_batch_gen)
        t.request_export.connect(self._export_project)
        t.request_new_chapter_from_outline.connect(self._new_chapter_from_outline)
        t.request_audit.connect(self._show_audit)
        t.request_pipeline.connect(self._open_pipeline)

        e = self.editor
        e.save_requested.connect(self._save_current)
        e.version_requested.connect(self._version_dialog)
        e.generate_shortcut.connect(self._ai_generate)
        e.ai_draft_requested.connect(self._ai_draft)

        a = self.ai
        a.config_requested.connect(self._config_dialog)
        a.generate_requested.connect(self._ai_generate)
        a.polish_requested.connect(self._ai_polish)
        a.summary_requested.connect(self._ai_summary)
        a.preview_requested.connect(self._preview_pack)

    # ---------- 事件处理 ----------
    def _on_project(self, pid):
        self.current_pid = pid
        p = self.db.get_project(pid)
        self.statusBar().showMessage(f"当前书：《{p['title']}》  {p['status']}")
        self.ai.set_enabled(True)
        self.ai.load_configs(self.db.get_ai_configs())
        self.editor.show_empty()

    def _on_chapter(self, cid):
        self.current_chapter_id = cid
        row = self.db.get_chapter(cid)
        if row:
            opts = self._chapter_bind_options(row["volume"])
            self.editor.show_chapter(row, bind_options=opts)

    def _chapter_bind_options(self, cur_vol=None):
        """可绑定的章纲条目：当前卷优先，其余靠后"""
        if not self.current_pid:
            return []
        rows = [o for o in self.db.get_outlines(self.current_pid)
                if o["level"] == "章纲"]
        rows.sort(key=lambda o: (cur_vol is not None and o["volume"] != cur_vol,
                                 o["sort_no"], o["id"]))
        return [(o["id"], o["title"]) for o in rows]

    def _on_setting(self, sid):
        for s in self.db.get_settings(self.current_pid):
            if s["id"] == sid:
                self.editor.show_setting(s)
                break

    def _on_outline(self, oid):
        row = self.db.get_outline(oid)
        if row:
            self.editor.show_outline(row)

    def _on_foreshadow(self, fid):
        row = self.db.get_foreshadow(fid)
        if row:
            self.editor.show_foreshadow(row)

    def _pid_of_outline(self, oid):
        r = self.db.get_outline(oid)
        return r["project_id"] if r else self.current_pid

    def _pid_of_foreshadow(self, fid):
        r = self.db.get_foreshadow(fid)
        return r["project_id"] if r else self.current_pid

    # ---------- 弹窗 ----------
    def _project_dialog(self, pid=None):
        dlg = dialogs.ProjectDialog(self.db, pid, self)
        if dlg.exec():
            self.tree.load()
            if pid is None and dlg.pid:
                self.tree.load_project_detail(dlg.pid)
                self._on_project(dlg.pid)
            elif pid:
                self._on_project(pid)

    def _delete_project(self, pid):
        p = self.db.get_project(pid)
        if p is None:
            return
        if QMessageBox.question(self, "确认删除",
                                f"确定删除《{p['title']}》？其设定、章节、版本等将先完整备份，"
                                "再从书架删除。") == QMessageBox.Yes:
            try:
                backup_path = self.db.delete_project(pid)
            except Exception as exc:
                QMessageBox.critical(self, "删除未完成",
                                     f"未能完成备份或删除，书籍已保留：\n{exc}")
                return
            self.tree.load()
            self.editor.show_empty()
            self.current_pid = None
            self.ai.set_enabled(False)
            QMessageBox.information(self, "删除完成",
                                    f"完整备份已保存：\n{backup_path}")

    def _chapter_dialog(self, pid):
        dlg = dialogs.ChapterDialog(self.db, pid, self)
        if dlg.exec():
            self.tree.load_project_detail(pid)
            self._on_chapter(dlg.cid)

    def _setting_dialog(self, pid, category):
        dlg = dialogs.SettingDialog(self.db, pid, category, parent=self)
        if dlg.exec():
            self.tree.load_project_detail(pid)

    def _outline_dialog(self, pid, level="章纲", oid=None, preset_vol=0):
        dlg = dialogs.OutlineDialog(self.db, pid, level or "章纲", oid,
                                    self, preset_volume=preset_vol)
        if dlg.exec():
            self.tree.load_project_detail(pid)
            if oid:
                self._on_outline(oid)

    def _foreshadow_dialog(self, pid, fid=None):
        dlg = dialogs.ForeshadowDialog(self.db, pid, fid, self)
        if dlg.exec():
            self.tree.load_project_detail(pid)
            if fid:
                self._on_foreshadow(fid)

    def _delete_outline(self, oid):
        if QMessageBox.question(self, "确认删除", "确定删除该大纲条目？") == QMessageBox.Yes:
            self.db.delete_outline(oid)
            self.tree.load_project_detail(self.current_pid)
            self.editor.show_empty()

    def _delete_foreshadow(self, fid):
        if QMessageBox.question(self, "确认删除", "确定删除该伏笔？") == QMessageBox.Yes:
            self.db.delete_foreshadow(fid)
            self.tree.load_project_detail(self.current_pid)
            self.editor.show_empty()

    def _show_audit(self, pid):
        dlg = dialogs.AuditDialog(self.db, pid, self)
        dlg.exec()

    def _open_pipeline(self, pid, vol):
        """v1.9 流水线：一键写完一卷（章纲→章节卡→正文→摘要→滚动摘要）"""
        cfg = self._cfg()
        if cfg is None:
            QMessageBox.warning(self, "提示", "请先在右侧「配置」中添加 AI 模型")
            return
        p = self.db.get_project(pid)
        dlg = PipelineDialog(self.db, pid, vol or 0, dict(cfg), self,
                             title=p["title"] if p else "")
        dlg.chapter_done.connect(
            lambda _cid, _name, _pid=pid: (
                self.tree.load_project_detail(_pid),
                self.statusBar().showMessage("流水线：新章节已入库（AI草稿）", 3000)))
        dlg.exec()

    def _new_chapter_from_outline(self, pid, oid):
        """二期：从章纲条目一键建章，标题/要点/绑定关系自动带入
        三期补：标题若是「第N章 xxx」格式，章节号取 N，章名去前缀"""
        o = self.db.get_outline(oid)
        if o is None:
            return
        chapters = self.db.get_chapters(pid)
        title = o["title"].strip()
        m = re.match(r"^第(\d+)章\s*(.*)$", title)
        if m:
            next_no = int(m.group(1))
            ch_title = m.group(2).strip() or title
        else:
            next_no = max([c["chapter_no"] for c in chapters], default=0) + 1
            ch_title = title
        vol = o["volume"] or (max([c["volume"] for c in chapters], default=1))
        card = f"- 本章目标：{o['content']}" if (o["content"] or "").strip() else ""
        cid = self.db.create_chapter(pid, vol, next_no, ch_title,
                                     chapter_card=card, outline_id=oid)
        self.tree.load_project_detail(pid)
        self._on_chapter(cid)
        self.statusBar().showMessage(
            f"已从章纲创建 第{next_no}章《{ch_title}》（已绑定），Ctrl+G 生成正文", 5000)

    def _version_dialog(self):
        if self.editor.mode == MODE_CHAPTER and self.current_chapter_id:
            dlg = dialogs.VersionDialog(self.db, self.current_chapter_id, self)
            if dlg.exec():
                row = self.db.get_chapter(self.current_chapter_id)
                self.editor.show_chapter(row)
                self.statusBar().showMessage("已回滚版本")

    def _config_dialog(self):
        dlg = dialogs.ConfigDialog(self.db, self)
        dlg.exec()
        self.ai.load_configs(self.db.get_ai_configs())

    # ---------- 保存 ----------
    def _save_current(self):
        mode = self.editor.mode
        if mode == MODE_CHAPTER and self.current_chapter_id:
            self.db.save_chapter(self.current_chapter_id,
                                 self.editor.current_text(), "手动保存")
            meta = self.editor.chapter_fields()
            if self.editor.card_text().strip():
                meta["chapter_card"] = self.editor.card_text()
            self.db.update_chapter_meta(self.current_chapter_id, **meta)
            row = self.db.get_chapter(self.current_chapter_id)
            self.editor.show_chapter(row)
            self.statusBar().showMessage("已保存（含版本快照）", 3000)
        elif mode == MODE_SETTING:
            if self.editor.setting_id:
                f = self.editor.setting_fields()
                if f["term"]:
                    self.db.update_setting(self.editor.setting_id,
                                           category=f["category"], term=f["term"],
                                           definition=self.editor.current_text())
                    self.statusBar().showMessage("词条已保存", 3000)
                else:
                    self.statusBar().showMessage("词条名不能为空，未保存", 3000)
        elif mode == MODE_OUTLINE:
            if self.editor.outline_id:
                f = self.editor.outline_fields()
                if f["title"]:
                    self.db.update_outline(self.editor.outline_id,
                                           level=f["level"], title=f["title"],
                                           content=self.editor.current_text())
                    self.statusBar().showMessage("大纲已保存", 3000)
                else:
                    self.statusBar().showMessage("标题不能为空，未保存", 3000)
        elif mode == MODE_FORESHADOW:
            if self.editor.foreshadow_id:
                f = self.editor.foreshadow_fields()
                try:
                    self.db.update_foreshadow(self.editor.foreshadow_id,
                                              ftype=f["ftype"], content=self.editor.current_text(),
                                              planted_ch=f["planted_ch"], plan_ch=f["plan_ch"],
                                              status=f["status"])
                except ValueError as exc:
                    QMessageBox.warning(self, '伏笔证据', str(exc))
                    return
                self.statusBar().showMessage("伏笔已保存", 3000)
        if self.current_pid:
            self.tree.load_project_detail(self.current_pid)

    # ---------- AI ----------
    def _cfg(self):
        cid = self.ai.current_config_id()
        cfg = self.db.get_ai_configs()
        for c in cfg:
            if c["id"] == cid:
                return c
        return self.db.get_default_config()

    def _context_pack(self):
        """三层记忆上下文包（与流水线共用 ai.context 实现）"""
        ch = self.db.get_chapter(self.current_chapter_id) \
            if self.current_chapter_id else None
        return context_pack_for(self.db, ch, pid=self.current_pid)

    # ---------- 导出 ----------
    def _build_export(self, pid, fmt):
        """返回 (content, default_name, file_filter)；实现移至 ui/export_utils 共用"""
        from ui.export_utils import build_export
        return build_export(self.db, pid, fmt)

    def _export_project(self, pid, fmt):
        content, default_name, flt, title = self._build_export(pid, fmt)
        path, _ = QFileDialog.getSaveFileName(self, title, default_name, flt)
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
        except OSError as e:
            QMessageBox.warning(self, "导出失败", str(e))
            return
        chapters = len(self.db.get_chapters(pid))
        QMessageBox.information(self, "导出完成",
                                f"已导出 {chapters} 章 ｜ {len(content)} 字\n{path}")

    def _preview_pack(self):
        if not self.current_pid:
            QMessageBox.warning(self, "提示", "请先选择一本书")
            return
        pack = self._context_pack()
        self.ai.set_out(f"【上下文注入包 · 三层记忆预览 ｜ 共约{len(pack)}字】\n\n" + pack)
        self.statusBar().showMessage("上下文包预览已生成", 3000)

    def _run_ai(self, fn, busy="AI 处理中…"):
        cfg = self._cfg()
        if cfg is None:
            QMessageBox.warning(self, "提示", "请先在「配置」中添加 AI 模型")
            return
        self.statusBar().showMessage(busy)
        self.ai.out.setPlainText("处理中…")
        self._worker = AIWorker(lambda: fn(cfg), self)
        self._worker.done.connect(self._ai_done)
        self._worker.failed.connect(self._ai_failed)
        self._worker.start()

    def _ai_done(self, text):
        self.statusBar().showMessage("完成", 3000)
        self.ai.set_out(text)

    def _ai_failed(self, err):
        self.statusBar().showMessage("AI 出错", 3000)
        self.ai.set_out(f"错误： {err}")

    # ---- 双模式：各环节 AI 草稿（填入编辑器，可编辑后手动保存）----
    def _ai_draft(self):
        cfg = self._cfg()
        if cfg is None:
            QMessageBox.warning(self, "提示", "请先在右侧「配置」中添加 AI 模型")
            return
        mode = self.editor.mode
        pid = self.current_pid
        project = self.db.get_project(pid) if pid else None
        settings_text = "\n".join(
            f"- {r['term']}：{r['definition']}"
            for r in (self.db.get_settings(pid) if pid else []))

        if mode == MODE_SETTING:
            term = self.editor.setting_fields()["term"]
            user = prompts.gen_setting_draft("", settings_text, term)
        elif mode == MODE_OUTLINE:
            lv = self.editor.outline_fields()["level"]
            user = prompts.gen_outline_draft(lv, dict(project), settings_text, "")
        elif mode == MODE_FORESHADOW:
            existing = "\n".join(f"- {f['content'][:30]}" for f in self.db.get_foreshadows(pid))
            user = prompts.gen_foreshadow_draft("", "", existing)
        elif mode == MODE_CHAPTER:
            if self.editor.tabs.currentIndex() == 1:
                row = self.db.get_chapter(self.current_chapter_id)
                outlines = "\n".join(f"- {o['title']}：{o['content'][:60]}" for o in self.db.get_outlines(pid))
                prev_summary = ""
                prevs = self.db.get_chapters(pid)
                if row in prevs:
                    i = prevs.index(row)
                    if i > 0:
                        prev_summary = prevs[i - 1]["summary"]
                user = prompts.gen_chapter_card_draft(outlines, prev_summary, settings_text, row["title"])
            else:
                self._ai_generate()
                return
        else:
            return

        self.statusBar().showMessage("AI 生成草稿中…")
        self._worker = AIWorker(lambda: aiclient.chat_once(
            cfg, [{"role": "system", "content": prompts.SYSTEM_ASSIST},
                  {"role": "user", "content": user}], temperature=0.7), self)
        self._worker.done.connect(self._draft_done)
        self._worker.failed.connect(self._ai_failed)
        self._worker.start()

    def _draft_done(self, text):
        if self.editor.mode == MODE_CHAPTER and self.editor.tabs.currentIndex() == 1:
            self.editor.card_edit.setPlainText(text)
        else:
            self.editor.edit.setPlainText(text)
        self.statusBar().showMessage("AI 草稿已填入（可编辑，Ctrl+S 保存）", 4000)

    # ---- 右键批量 AI 生成（勾选入库）----
    def _plan(self, project):
        """v1.7 全书规划：(总卷数, 每卷章数)。总卷数未设时按现有卷纲推断"""
        p = dict(project)
        V = int(p.get("plan_volumes") or 0)
        M = int(p.get("plan_chapters") or 10)
        if not V:
            nums = []
            for o in self.db.get_outlines(p["id"]):
                if o["level"] != "卷纲":
                    continue
                m = re.match(r"^第(\d+)卷", o["title"].strip())
                if m:
                    nums.append(int(m.group(1)))
                elif o["volume"]:
                    nums.append(o["volume"])
            V = max(nums) if nums else 0
        return V, M

    def _vol_line(self, pid, vol):
        """返回第 vol 卷的卷纲条目（volume 字段优先，缺省按排序位置）"""
        return vol_outline_for(self.db, pid, vol)

    @staticmethod
    def _strip_num(title, unit="章"):
        """去掉标题里的『第N章/卷』前缀"""
        return aparse.strip_num(title, unit)

    def _ai_batch_gen(self, pid, kind):
        cfg = self._cfg()
        if cfg is None:
            QMessageBox.warning(self, "提示", "请先在右侧「配置」中添加 AI 模型")
            return
        project = dict(self.db.get_project(pid))
        settings_text = "\n".join(f"- {r['term']}：{r['definition']}" for r in self.db.get_settings(pid))

        if kind == "setting":
            user = prompts.gen_settings_batch(project, settings_text)
        elif kind.startswith("章纲@"):
            # v1.7 跟纲模式：锁定该卷的章节号区间，并注入本卷卷纲原文
            vol = int(kind.split("@", 1)[1])
            _V, M = self._plan(project)
            s0, s1 = (vol - 1) * M + 1, vol * M
            vo = self._vol_line(pid, vol)
            user = prompts.gen_volume_chapters(
                project, settings_text, vol,
                vo["title"] if vo else f"第{vol}卷",
                vo["content"] if vo else "", s0, s1)
        elif kind == "章纲":
            # 全书级生成：先问卷号，再走跟纲模式
            V, M = self._plan(project)
            default_vol = int(V) if V else 1
            vol, ok = QInputDialog.getInt(
                self, "生成章纲",
                f"为第几卷生成章纲？\n（每卷 {M} 章；输入 1～{V or 'N'} 的卷号）",
                default_vol, 0, 999)
            if not ok:
                return
            self._ai_batch_gen(pid, f"章纲@{vol}")
            return
        elif kind == "卷纲":
            _V, _M = self._plan(project)
            user = prompts.gen_volumes_plan(project, settings_text,
                                            total=_V or None)
        elif kind == "foreshadow":
            existing = "\n".join(f"- {f['content'][:30]}" for f in self.db.get_foreshadows(pid))
            user = prompts.gen_foreshadows_batch(project, existing)
        else:
            return

        self.statusBar().showMessage(f"AI 生成{kind.replace('@', '第')}草案中…")
        self._worker = AIWorker(lambda: aiclient.chat_once(
            cfg, [{"role": "system", "content": prompts.SYSTEM_ASSIST},
                  {"role": "user", "content": user}], temperature=0.8), self)
        self._worker.done.connect(lambda t, k=kind, p=pid: self._batch_done(t, k, p))
        self._worker.failed.connect(self._ai_failed)
        self._worker.start()

    def _batch_done(self, text, kind, pid):
        base_kind = kind.split("@", 1)[0]
        vol = int(kind.split("@", 1)[1]) if "@" in kind else 0
        items = self._parse_batch(text, base_kind)
        if not items:
            self.ai.set_out(f"AI 未能解析出条目：\n{text[:800]}")
            return
        # 勾选列表按入库后的完整条目展示（标题：内容 一体）
        shown = []
        for line, data in items:
            if base_kind in ("卷纲", "章纲"):
                txt = f"{data['title']}：{data['content']}" if data.get("content") else data["title"]
                shown.append((txt, data))
            else:
                shown.append((line, data))
        title_map = {"setting": "AI 生成的设定词条",
                     "卷纲": "AI 生成的分卷大纲（全书整体规划）",
                     "章纲": f"AI 生成的章纲草案"
                     + (f"（第{vol}卷 ｜ 勾选后按区间连续编号）" if vol else "（勾选后统一连续编号）"),
                     "foreshadow": "AI 生成的伏笔草案"}
        dlg = dialogs.AiPickDialog(title_map.get(base_kind, "AI 生成结果"), shown, self)
        if not dlg.exec():
            return
        picked = dlg.picked()

        # ---- 编号与归卷策略（v1.7）----
        seq = 0
        seq_start = 0
        if base_kind == "章纲":
            if vol == 0:                       # 兼容旧入口：问卷号（记住上次）
                max_vol = max([c["volume"] for c in self.db.get_chapters(pid)], default=1)
                vol, ok = QInputDialog.getInt(
                    self, "章纲归卷",
                    f"这 {len(picked)} 条章纲归入第几卷？\n（0 = 未分卷）",
                    self._last_zg_vol, 0, 999)
                if not ok:
                    return
                self._last_zg_vol = max(vol, 1)
            _V, M = self._plan(self.db.get_project(pid))
            seq_start = (vol - 1) * M + 1      # 跟纲模式：锁区间
        elif base_kind == "卷纲":
            seq_start = self._next_seq(pid, "卷纲")

        created = 0
        if base_kind in ("卷纲", "章纲"):
            bad = [d for d in picked
                   if not aparse.sane_outline_title(d.get("title", ""))]
            if bad:
                picked = [d for d in picked if d not in bad]
                QMessageBox.warning(
                    self, "已拦截可疑标题",
                    "有 %d 条标题可疑（去编号后不含汉字，疑似模型坏解析），"
                    "未入库：\n%s" % (len(bad),
                                      "\n".join(d.get("title", "") for d in bad[:5])))
        for data in picked:
            if base_kind == "setting":
                self.db.add_setting(pid, data.get("category", "术语"),
                                    data["term"], data.get("definition", ""))
                created += 1
            elif base_kind == "卷纲":
                m = re.match(r"^第(\d+)卷", data["title"])
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
                                    data.get("content", ""),
                                    volume=vol)
                seq += 1
                created += 1
            elif base_kind == "foreshadow":
                self.db.add_foreshadow(pid, data["content"], data.get("ftype", "悬念"),
                                        data.get("planted_ch", ""), data.get("plan_ch", ""))
                created += 1
        self.tree.load_project_detail(pid)
        self.statusBar().showMessage(f"已创建 {created} 条{base_kind}", 4000)

    def _next_seq(self, pid, kind):
        """下一个可用编号：取已有大纲条目编号与（章纲时）现有章节号的最大值 + 1"""
        unit = "章" if kind == "章纲" else "卷"
        nums = []
        for o in self.db.get_outlines(pid):
            if o["level"] != kind:
                continue
            m = re.match(rf"^第(\d+){unit}", o["title"].strip())
            if m:
                nums.append(int(m.group(1)))
            elif o["volume"] and kind == "卷纲":
                nums.append(o["volume"])
        if kind == "章纲":
            nums += [c["chapter_no"] for c in self.db.get_chapters(pid)]
        return (max(nums) + 1) if nums else 1

    @staticmethod
    def _parse_batch(text, kind, start_num=None):
        """start_num: 自动补「第N章/卷」前缀的起始号（None=不补）"""
        return aparse.parse_batch(text, kind, start_num)

    def _ai_generate(self):
        if not self.current_pid:
            QMessageBox.warning(self, "提示", "请先选择一本书")
            return
        cfg = self._cfg()
        if cfg is None:
            QMessageBox.warning(self, "提示", "请先在「配置」中添加 AI 模型")
            return
        if self._stream_worker and self._stream_worker.isRunning():
            QMessageBox.information(self, "提示", "正在生成中，请等当前章节完成")
            return
        pack = self._context_pack()
        self.statusBar().showMessage("AI 流式生成章节中…")
        self._gen_buf = ""
        self.ai.set_out("")
        self._flush_timer.start()
        self._stream_worker = AIStreamWorker(cfg, [
            {"role": "system", "content": prompts.SYSTEM_WRITER},
            {"role": "user", "content": prompts.gen_chapter_prompt(pack)},
        ], parent=self)
        self._stream_worker.delta.connect(self._gen_delta)
        self._stream_worker.done.connect(self._gen_done)
        self._stream_worker.failed.connect(self._gen_failed)
        self._stream_worker.start()

    def _gen_delta(self, piece):
        self._gen_buf += piece

    def _flush_stream(self):
        if self._gen_buf:
            self.ai.set_out(self._gen_buf)
            sb = self.ai.out.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _gen_failed(self, err):
        self._flush_timer.stop()
        self.statusBar().showMessage("AI 出错", 3000)
        self.ai.set_out(f"错误： {err}\n\n（已收到的部分：\n{self._gen_buf[-800:]}）" if self._gen_buf else f"错误： {err}")

    def _gen_done(self, text):
        self._flush_timer.stop()
        self._ai_done(text)
        # 正文直接填入编辑器（可编辑草稿，Ctrl+S 保存）
        if self.editor.mode == MODE_CHAPTER:
            self.editor.edit.setPlainText(text)
        # 自动生成前情摘要入库，保证下一章有承接
        self._auto_summary(self.current_chapter_id, text)

    def _auto_summary(self, cid, content):
        if not cid or not content.strip():
            return
        cfg = self._cfg()
        if cfg is None:
            return

        def fn(c):
            return aiclient.chat_once(
                c, [{"role": "system", "content": prompts.SYSTEM_SUMMARY},
                    {"role": "user", "content": content[:8000]}], temperature=0.3)

        w = AIWorker(lambda: fn(cfg), self)
        w.done.connect(lambda t, _cid=cid: (
            self.db.update_chapter_meta(_cid, summary=t),
            self.statusBar().showMessage("正文已生成，前情摘要已自动更新", 3000),
            self._rollup_volume(_cid)))
        w.failed.connect(lambda e: self.statusBar().showMessage(
            "正文已生成（摘要更新失败，不影响正文）", 4000))
        w.start()

    def _rollup_volume(self, cid):
        """章节摘要入库后，把该卷「故事至今」滚动摘要一并更新（静默，失败不提示打扰）"""
        ch = self.db.get_chapter(cid)
        if ch is None or not (ch["summary"] or "").strip():
            return
        pid, vol = ch["project_id"], ch["volume"]
        old = self.db.get_volume_summary(pid, vol)
        cfg = self._cfg()
        if cfg is None:
            return

        def fn(c):
            return aiclient.chat_once(c, [
                {"role": "system", "content": prompts.SYSTEM_ROLLUP},
                {"role": "user", "content": prompts.rollup_prompt(
                    old, ch["chapter_no"], ch["title"], ch["summary"])},
            ], temperature=0.3)

        w = AIWorker(fn, self)
        w.done.connect(lambda t: (
            self.db.set_volume_summary(pid, vol, t),
            self.statusBar().showMessage(f"第{vol}卷滚动摘要已自动更新", 3000)))
        w.failed.connect(lambda e: None)  # 静默失败
        w.start()

    def _ai_polish(self):
        text = self.editor.current_text().strip()
        if not text:
            QMessageBox.warning(self, "提示", "编辑器里没有内容可润色")
            return

        def fn(cfg):
            from ai import style_profile
            style = style_profile.effective_style(self.db, self.current_pid) if self.current_pid else ''
            return aiclient.chat_once(cfg, [
                {"role": "system", "content": prompts.SYSTEM_POLISH},
                {"role": "user", "content": f'【作品风格】\n{style}\n【待润色正文】\n{text[:12000]}'},
            ], temperature=0.4)

        self._run_ai(fn, "AI 润色中…")

    def _ai_summary(self):
        if not self.current_chapter_id:
            QMessageBox.warning(self, "提示", "请先打开一章再生成摘要")
            return
        ch = self.db.get_chapter(self.current_chapter_id)
        text = ch["content"].strip()
        if not text:
            QMessageBox.warning(self, "提示", "本章还没有正文")
            return
        cfg = self._cfg()
        if cfg is None:
            QMessageBox.warning(self, "提示", "请先在「配置」中添加 AI 模型")
            return

        def fn():
            return aiclient.chat_once(cfg, [
                {"role": "system", "content": prompts.SYSTEM_SUMMARY},
                {"role": "user", "content": text[:8000]},
            ], temperature=0.3)

        _cid = self.current_chapter_id
        w = AIWorker(fn, self)
        w.done.connect(lambda t: (
            self.db.update_chapter_meta(_cid, summary=t),
            self._ai_done(t),
            self._rollup_volume(_cid)))
        w.failed.connect(self._ai_failed)
        self._worker = w
        w.start()
