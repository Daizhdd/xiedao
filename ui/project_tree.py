# -*- coding: utf-8 -*-
"""左侧项目树：书 / 设定档案 / 章节"""
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QMenu
from PySide6.QtCore import Signal, Qt, QSize
from ui.icons import line_icon

KIND_PROJECT = "project"
KIND_CATEGORY = "category"
KIND_SETTING = "setting"
KIND_CHAPTER = "chapter"
KIND_OUTLINE = "outline"
KIND_FORESHADOW = "foreshadow"


class ProjectTree(QTreeWidget):
    project_selected = Signal(int)
    chapter_selected = Signal(int)
    setting_selected = Signal(int)
    outline_selected = Signal(int)
    foreshadow_selected = Signal(int)
    request_new_project = Signal()
    request_edit_project = Signal(int)
    request_delete_project = Signal(int)
    request_new_chapter = Signal(int)
    request_new_setting = Signal(int, str)
    request_new_outline = Signal(int, str, int)  # (pid, level, 预填卷号0=不指定)
    request_new_foreshadow = Signal(int)
    request_edit_outline = Signal(int)
    request_edit_foreshadow = Signal(int)
    request_delete_outline = Signal(int)
    request_delete_foreshadow = Signal(int)
    request_ai_gen = Signal(int, str)   # (pid, kind: setting/卷纲/章纲/foreshadow)
    request_export = Signal(int, str)   # (pid, fmt: txt/md/assets)
    request_new_chapter_from_outline = Signal(int, int)  # 二期 (pid, outline_id)
    request_audit = Signal(int)         # 二期 进度对账
    request_pipeline = Signal(int, int) # v1.9 流水线 (pid, vol; 0=进对话框再选)

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.setHeaderHidden(True)
        self.setIconSize(QSize(18,18))
        self.itemClicked.connect(self._on_click)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_menu)

    # ---------- 数据加载 ----------
    def load(self):
        self.clear()
        for p in self.db.get_projects():
            node = QTreeWidgetItem([p['title']])
            node.setIcon(0, line_icon('book', self))
            node.setData(0, Qt.UserRole, (KIND_PROJECT, p["id"]))
            self.addTopLevelItem(node)

    def load_project_detail(self, pid):
        """展开某本书的设定档案 + 章节"""
        # 找到对应顶层节点
        root = None
        for i in range(self.topLevelItemCount()):
            it = self.topLevelItem(i)
            if it.data(0, Qt.UserRole) and it.data(0, Qt.UserRole)[1] == pid:
                root = it
                break
        if root is None:
            return
        root.takeChildren()

        cat_node = QTreeWidgetItem(["设定档案"])
        cat_node.setIcon(0, line_icon('folder', self))
        cat_node.setData(0, Qt.UserRole, (KIND_CATEGORY, None))
        root.addChild(cat_node)
        # 按分类分组
        settings = self.db.get_settings(pid)
        groups = {}
        for s in settings:
            groups.setdefault(s["category"], []).append(s)
        for cat in ("力量体系", "地理", "历史", "术语", "人物"):
            if cat not in groups:
                continue
            c_node = QTreeWidgetItem([f"{cat}"])
            c_node.setData(0, Qt.UserRole, (KIND_CATEGORY, cat))
            cat_node.addChild(c_node)
            for s in groups[cat]:
                s_node = QTreeWidgetItem([f"{s['term']}"])
                s_node.setData(0, Qt.UserRole, (KIND_SETTING, s["id"]))
                c_node.addChild(s_node)

        # 大纲（二期层级结构：第N卷(卷纲) → 该卷章纲 → 已绑章节；未分卷章纲单列）
        ol_node = QTreeWidgetItem(["大纲"])
        ol_node.setIcon(0, line_icon('outline', self))
        ol_node.setData(0, Qt.UserRole, (KIND_CATEGORY, "outline"))
        root.addChild(ol_node)
        outlines = self.db.get_outlines(pid)
        chapters = self.db.get_chapters(pid)
        ch_by_outline = {}
        for c in chapters:
            if c["outline_id"]:
                ch_by_outline.setdefault(c["outline_id"], []).append(c)
        vol_outlines = sorted([o for o in outlines if o["level"] == "卷纲"],
                              key=lambda x: (x["sort_no"], x["id"]))
        pos_of = {o["id"]: i + 1 for i, o in enumerate(vol_outlines)}

        def eff_vol(o):
            return o["volume"] or pos_of.get(o["id"], 0)

        def add_outline_leaf(parent_item, o):
            title = o["title"]
            if o["level"] == "章纲":
                chs = ch_by_outline.get(o["id"], [])
                mark = f"（{len(chs)}章）" if chs else "（未建章）"
                node = QTreeWidgetItem([f"{title} {mark}"])
                node.setData(0, Qt.UserRole, (KIND_OUTLINE, o["id"], "章纲"))
                parent_item.addChild(node)
                for c in sorted(chs, key=lambda x: x["chapter_no"]):
                    empty = "" if c["content"].strip() else "[空] "
                    leaf = QTreeWidgetItem([f"第{c['chapter_no']}章 {c['title']} "
                                            f"{empty}（{c['status']}）"])
                    leaf.setIcon(0, line_icon('file', self))
                    leaf.setData(0, Qt.UserRole, (KIND_CHAPTER, c["id"]))
                    node.addChild(leaf)
            else:
                node = QTreeWidgetItem([f"{title}"])
                node.setData(0, Qt.UserRole, (KIND_OUTLINE, o["id"], "卷纲"))
                parent_item.addChild(node)

        # v1.6：章节叶子挂在大纲树下，未绑定的归入各卷「未归纲章节」兜底节点
        def add_unbound(parent_item, vol):
            lst = [c for c in chapters
                   if not c["outline_id"] and c["volume"] == vol]
            if not lst:
                return None
            u_node = QTreeWidgetItem([f"未归纲章节（{len(lst)}）"])
            u_node.setData(0, Qt.UserRole, (KIND_CATEGORY, f"unbound:{vol}"))
            parent_item.addChild(u_node)
            for c in sorted(lst, key=lambda x: x["chapter_no"]):
                empty = "" if c["content"].strip() else "[空] "
                leaf = QTreeWidgetItem([f"第{c['chapter_no']}章 {c['title']} "
                                        f"{empty}（{c['status']}）"])
                leaf.setIcon(0, line_icon('file', self))
                leaf.setData(0, Qt.UserRole, (KIND_CHAPTER, c["id"]))
                u_node.addChild(leaf)
            return u_node

        vol_nums = sorted({eff_vol(o) for o in vol_outlines})
        shown_vols = set()
        for n in vol_nums:
            v_name = next((o["title"] for o in vol_outlines if eff_vol(o) == n), f"第{n}卷")
            v_node = QTreeWidgetItem([f"第{n}卷 《{v_name}》"])
            v_node.setData(0, Qt.UserRole, (KIND_CATEGORY, f"outline:vol:{n}"))
            ol_node.addChild(v_node)
            shown_vols.add(n)
            vo = next((o for o in vol_outlines if eff_vol(o) == n), None)
            if vo:
                v_info = QTreeWidgetItem(
                    [f"卷纲：{(vo['content'][:24] + '…') if len(vo['content']) > 24 else vo['content']}"])
                v_info.setData(0, Qt.UserRole, (KIND_OUTLINE, vo["id"], "卷纲"))
                v_node.addChild(v_info)
            for o in [x for x in outlines
                      if x["level"] == "章纲" and eff_vol(x) == n]:
                add_outline_leaf(v_node, o)
            add_unbound(v_node, n)

        # 未分卷的章纲 + 不在任何已知卷下的未归纲章节
        loose = [o for o in outlines
                 if o["level"] == "章纲" and eff_vol(o) not in shown_vols]
        stray = [c for c in chapters if not c["outline_id"]
                 and c["volume"] not in shown_vols]
        if loose or stray:
            l_node = QTreeWidgetItem(["未分卷"])
            l_node.setData(0, Qt.UserRole, (KIND_CATEGORY, "outline:vol:0"))
            ol_node.addChild(l_node)
            for o in loose:
                add_outline_leaf(l_node, o)
            if stray:
                u_node = QTreeWidgetItem([f"未归纲章节（{len(stray)}）"])
                u_node.setData(0, Qt.UserRole, (KIND_CATEGORY, "unbound:0"))
                l_node.addChild(u_node)
                for c in sorted(stray, key=lambda x: (x["volume"], x["chapter_no"])):
                    empty = "" if c["content"].strip() else "[空] "
                    leaf = QTreeWidgetItem(
                        [f"第{c['chapter_no']}章 {c['title']} {empty}（{c['status']}）"])
                    leaf.setIcon(0, line_icon('file', self))
                    leaf.setData(0, Qt.UserRole, (KIND_CHAPTER, c["id"]))
                    u_node.addChild(leaf)

        # 伏笔台账
        fh_node = QTreeWidgetItem(["伏笔台账"])
        fh_node.setIcon(0, line_icon('link', self))
        fh_node.setData(0, Qt.UserRole, (KIND_CATEGORY, "foreshadow"))
        root.addChild(fh_node)
        for f in self.db.get_foreshadows(pid):
            mark = "已回收" if f["status"] == "已回收" else "待回收"
            f_node = QTreeWidgetItem([f"{mark} [{f['ftype']}] {f['content'][:14]}…"])
            f_node.setData(0, Qt.UserRole, (KIND_FORESHADOW, f["id"]))
            fh_node.addChild(f_node)

        root.setExpanded(True)
        cat_node.setExpanded(True)
        ol_node.setExpanded(True)
        fh_node.setExpanded(True)

    # ---------- 交互 ----------
    def _on_click(self, item, col):
        data = item.data(0, Qt.UserRole)
        if not data:
            return
        kind, rid = data[0], data[1]
        if kind == KIND_PROJECT:
            self.project_selected.emit(rid)
            self.load_project_detail(rid)
        elif kind == KIND_SETTING:
            self.setting_selected.emit(rid)
        elif kind == KIND_CHAPTER:
            self.chapter_selected.emit(rid)
        elif kind == KIND_OUTLINE:
            self.outline_selected.emit(rid)
        elif kind == KIND_FORESHADOW:
            self.foreshadow_selected.emit(rid)

    def _root_pid_of(self, item):
        """v1.6 修复：向上回溯找所属书节点 id（旧版只认顶层选中态，点深层节点返回 None）"""
        while item is not None and item.parent() is not None:
            item = item.parent()
        if item is None:
            return None
        d = item.data(0, Qt.UserRole)
        return d[1] if d and d[0] == KIND_PROJECT else None

    def _outline_eff_vol(self, oid):
        """卷纲条目的有效卷号：volume 优先，缺省按排序位置"""
        o = self.db.get_outline(oid)
        if o is None or o["level"] != "卷纲":
            return 0
        if o["volume"]:
            return o["volume"]
        vols = sorted([x for x in self.db.get_outlines(o["project_id"])
                       if x["level"] == "卷纲"],
                      key=lambda x: (x["sort_no"], x["id"]))
        for i, x in enumerate(vols):
            if x["id"] == oid:
                return i + 1
        return 0

    def _on_menu(self, pos):
        item = self.itemAt(pos)
        pid = self._current_pid()
        if item is not None:
            pid = self._root_pid_of(item) or pid
        if item is None:
            m = QMenu(self)
            a = m.addAction("新建项目…")
            a.triggered.connect(lambda: self.request_new_project.emit())
            m.exec(self.viewport().mapToGlobal(pos))
            return
        kind, rid = item.data(0, Qt.UserRole)[0], item.data(0, Qt.UserRole)[1]
        m = QMenu(self)
        if kind == KIND_PROJECT:
            a1 = m.addAction("编辑项目…")
            a1.triggered.connect(lambda: self.request_edit_project.emit(rid))
            a2 = m.addAction("新建章节…")
            a2.triggered.connect(lambda: self.request_new_chapter.emit(rid))
            a3 = m.addAction("新建设定词条…")
            a3.triggered.connect(lambda: self.request_new_setting.emit(rid, "术语"))
            a4 = m.addAction("新建卷纲…")
            a4.triggered.connect(lambda: self.request_new_outline.emit(rid, "卷纲", 0))
            a5 = m.addAction("新建章纲…")
            a5.triggered.connect(lambda: self.request_new_outline.emit(rid, "章纲", 0))
            a6 = m.addAction("新建伏笔…")
            a6.triggered.connect(lambda: self.request_new_foreshadow.emit(rid))
            m.addSeparator()
            b1 = m.addAction("AI 生成词条草案…")
            b1.triggered.connect(lambda: self.request_ai_gen.emit(rid, "setting"))
            b2 = m.addAction("AI 生成卷纲草案…")
            b2.triggered.connect(lambda: self.request_ai_gen.emit(rid, "卷纲"))
            b3 = m.addAction("AI 生成章纲草案…")
            b3.triggered.connect(lambda: self.request_ai_gen.emit(rid, "章纲"))
            b4 = m.addAction("AI 生成伏笔草案…")
            b4.triggered.connect(lambda: self.request_ai_gen.emit(rid, "foreshadow"))
            m.addSeparator()
            ex = m.addMenu("导出本书…")
            a_txt = ex.addAction("导出 TXT（上传用）")
            a_txt.triggered.connect(lambda: self.request_export.emit(rid, "txt"))
            a_md = ex.addAction("导出 Markdown（含设定/大纲）")
            a_md.triggered.connect(lambda: self.request_export.emit(rid, "md"))
            a_assets = ex.addAction("导出素材包（设定/大纲/伏笔）")
            a_assets.triggered.connect(lambda: self.request_export.emit(rid, "assets"))
            m.addSeparator()
            d0 = m.addAction("进度对账（章纲↔章节）…")
            d0.triggered.connect(lambda: self.request_audit.emit(rid))
            p0 = m.addAction(line_icon('play', self), "一键写完本卷（流水线）…")
            p0.triggered.connect(lambda: self.request_pipeline.emit(rid, 0))
            m.addSeparator()
            a7 = m.addAction("删除项目")
            a7.triggered.connect(lambda: self.request_delete_project.emit(rid))
        elif kind == KIND_CATEGORY and isinstance(rid, str):
            if rid == "outline":
                a = m.addAction("新建卷纲…")
                a.triggered.connect(lambda: self.request_new_outline.emit(pid, "卷纲", 0))
                b = m.addAction("新建章纲…")
                b.triggered.connect(lambda: self.request_new_outline.emit(pid, "章纲", 0))
                m.addSeparator()
                g1 = m.addAction("AI 生成卷纲草案…")
                g1.triggered.connect(lambda: self.request_ai_gen.emit(pid, "卷纲"))
                g2 = m.addAction("AI 生成章纲草案…")
                g2.triggered.connect(lambda: self.request_ai_gen.emit(pid, "章纲"))
                m.addSeparator()
                d = m.addAction("进度对账（章纲↔章节）…")
                d.triggered.connect(lambda: self.request_audit.emit(pid))
            elif rid.startswith("outline:vol:"):
                vol = int(rid.split(":", 2)[2])
                if vol > 0:
                    a = m.addAction(f"新建章纲（归入第{vol}卷）…")
                    a.triggered.connect(
                        lambda _=False, v=vol: self.request_new_outline.emit(pid, "章纲", v))
                    if pid:
                        n1 = m.addAction(f"新建章节（第{vol}卷）…")
                        n1.triggered.connect(
                            lambda: self.request_new_chapter.emit(pid))
                        g = m.addAction(f"AI 生成本卷章纲草案…（第{vol}卷区间自动编号）")
                        g.triggered.connect(
                            lambda _c=False, v=vol: self.request_ai_gen.emit(pid, f"章纲@{v}"))
                        p = m.addAction(line_icon('play', self), f"一键写完第{vol}卷（流水线）…")
                        p.triggered.connect(
                            lambda _c=False, v=vol: self.request_pipeline.emit(pid, v))
                    m.addSeparator()
            elif rid.startswith("outline:"):
                lv = rid.split(":", 1)[1]
                a = m.addAction(f"新建{lv}…")
                a.triggered.connect(lambda: self.request_new_outline.emit(pid, lv, 0))
                b = m.addAction(f"AI 生成{lv}草案…")
                b.triggered.connect(lambda: self.request_ai_gen.emit(pid, lv))
            elif rid == "foreshadow":
                a = m.addAction("新建伏笔…")
                a.triggered.connect(lambda: self.request_new_foreshadow.emit(pid))
                b = m.addAction("AI 生成伏笔草案…")
                b.triggered.connect(lambda: self.request_ai_gen.emit(pid, "foreshadow"))
            elif rid in ("力量体系", "地理", "历史", "术语", "人物"):
                a = m.addAction(f"新建「{rid}」词条…")
                a.triggered.connect(lambda: self.request_new_setting.emit(pid, rid))
                b = m.addAction(f"AI 生成「{rid}」词条草案…")
                b.triggered.connect(lambda: self.request_ai_gen.emit(pid, "setting"))
        elif kind == KIND_OUTLINE:
            data = item.data(0, Qt.UserRole)
            level = data[2] if len(data) > 2 else "章纲"
            if level == "章纲":
                a0 = m.addAction("从这条章纲新建章节…")
                a0.triggered.connect(
                    lambda: self.request_new_chapter_from_outline.emit(pid, rid))
                m.addSeparator()
            else:
                ev = self._outline_eff_vol(rid) if pid else 0
                b = m.addAction("AI 生成本卷章纲草案…（按该卷区间自动编号）"
                                if ev else "AI 生成本卷章纲草案…")
                if pid and ev:
                    b.triggered.connect(
                        lambda _c=False, v=ev: self.request_ai_gen.emit(pid, f"章纲@{v}"))
                    p = m.addAction(line_icon('play', self), f"一键写完第{ev}卷（流水线）…")
                    p.triggered.connect(
                        lambda _c=False, v=ev: self.request_pipeline.emit(pid, v))
                else:
                    b.setEnabled(False)
                    b.setToolTip("请先在「编辑大纲」中为该卷设置所属卷号")
                m.addSeparator()
            a1 = m.addAction("编辑…")
            a1.triggered.connect(lambda: self.request_edit_outline.emit(rid))
            a2 = m.addAction("删除")
            a2.triggered.connect(lambda: self.request_delete_outline.emit(rid))
        elif kind == KIND_FORESHADOW:
            a1 = m.addAction("编辑…")
            a1.triggered.connect(lambda: self.request_edit_foreshadow.emit(rid))
            a2 = m.addAction("删除")
            a2.triggered.connect(lambda: self.request_delete_foreshadow.emit(rid))
        m.exec(self.viewport().mapToGlobal(pos))

    def _current_pid(self):
        for i in range(self.topLevelItemCount()):
            it = self.topLevelItem(i)
            if it.isSelected() and it.data(0, Qt.UserRole):
                return it.data(0, Qt.UserRole)[1]
        return None
