# -*- coding: utf-8 -*-
"""导出工具：从 MainWindow 抽出的纯函数版，供对话界面与经典视图共用。"""
import re


def build_export(db, pid, fmt):
    """返回 (content, default_name, file_filter, dialog_title)"""
    p = db.get_project(pid)
    chapters = db.get_chapters(pid)
    settings = db.get_settings(pid)
    outlines = db.get_outlines(pid)
    foreshadows = db.get_foreshadows(pid)
    safe = re.sub(r'[\\/:*?"<>|]', "_", p["title"]) or "book"

    if fmt == "txt":
        intro = p["intro"] or p["logline"] or ""
        lines = [p["title"], "", intro, ""] if intro else [p["title"], ""]
        cur_vol = None
        for c in chapters:
            if c["volume"] != cur_vol:
                cur_vol = c["volume"]
                lines += [f"第{cur_vol}卷", ""]
            lines += [f"第{c['chapter_no']}章 {c['title']}", ""]
            lines += [c["content"], ""]
        return "\n".join(lines), f"{safe}.txt", "TXT 文本 (*.txt)", "导出 TXT（上传用）"
    if fmt == "assets":
        parts = [f"# 《{p['title']}》素材包", ""]
        parts += ["## 立项", f"- 一句话概念：{p['logline']}", f"- 题材：{p['genre']}",
                  f"- 读者：{p['audience']}", f"- 卖点：\n{p['selling_point']}", ""]
        parts += ["## 设定档案"]
        cur_cat = None
        for s in settings:
            if s["category"] != cur_cat:
                cur_cat = s["category"]
                parts += [f"### {cur_cat}", ""]
            parts += [f"- **{s['term']}**：{s['definition']}", ""]
        parts += ["## 大纲"]
        for o in outlines:
            parts += [f"- [{o['level']}] {o['title']}：{o['content']}", ""]
        parts += ["## 伏笔台账"]
        for f in foreshadows:
            parts += [f"- [{f['status']}][{f['ftype']}] {f['content']}（埋设：{f['planted_ch'] or '—'}，回收：{f['plan_ch'] or '—'}）", ""]
        return "\n".join(parts), f"{safe}_素材包.md", "Markdown (*.md)", "导出素材包"
    # md
    intro = p["intro"] or p["logline"] or ""
    parts = [f"# 《{p['title']}》", "", f"> {intro}",
             f"> 题材：{p['genre']} ｜ 读者：{p['audience']} ｜ 状态：{p['status']}", "",
             f"> 卖点：{p['selling_point']}", ""]
    parts += ["## 设定档案"]
    for s in settings:
        parts += [f"- **{s['term']}**（{s['category']}）：{s['definition']}", ""]
    parts += ["## 大纲"]
    for o in outlines:
        parts += [f"- [{o['level']}] {o['title']}：{o['content']}", ""]
    parts += ["## 正文"]
    for c in chapters:
        parts += [f"### 第{c['chapter_no']}章 {c['title']}", "", c["content"], ""]
    return "\n".join(parts), f"{safe}.md", "Markdown (*.md)", "导出 Markdown"
