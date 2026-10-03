# -*- coding: utf-8 -*-
"""AI 批量草案解析（与 UI 解耦）：设定 / 卷纲 / 章纲 / 伏笔。"""
import re


def strip_num(title, unit="章"):
    """去掉标题里的『第N章/卷』前缀"""
    return re.sub(rf"^第\d+{unit}\s*", "", (title or "").strip()).strip()


_CJK = re.compile(r"[\u4e00-\u9fff]")


def sane_outline_title(title):
    """卷纲/章纲标题合法性：去掉「第N章/卷」编号后必须含汉字（或为纯编号）。
    拦截 9-27 事故那类坏解析——模型把剧情元素当标题吐出来（如「第10章 by ___」），
    这样的标题会一路变成章节名。返回 True/False。"""
    t = (title or "").strip()
    if not t:
        return False
    rest = strip_num(strip_num(t, "章"), "卷")
    return rest == "" or bool(_CJK.search(rest))


def parse_batch(text, kind, start_num=None):
    """解析 AI 批量草案文本为条目列表 [(原始行, 字段dict), ...]。
    start_num: 自动补「第N章/卷」前缀的起始号（None=不补）。"""
    out = []
    n = start_num or 1
    unit = "章" if kind == "章纲" else "卷"
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("```", "- 格式", "格式", "要求")):
            continue
        body = line.lstrip("-•*· ").strip()
        is_header = body.startswith("#")
        body = re.sub(r"^[#]+\s*", "", body)              # markdown 标题
        body = re.sub(r"^\d+[.、）)]\s*", "", body)         # 1. / 1、/ 1）
        body = re.sub(r"^[①②③④⑤⑥⑦⑧⑨⑩]\s*", "", body)    # 圈数字
        body = body.replace("**", "").replace("＊＊", "")   # 加粗
        if not body:
            continue
        # 无分隔符的标题行（如「## 卷纲规划」）是章节标签，不是条目
        if is_header and "：" not in body and ":" not in body and "|" not in body:
            continue
        if kind == "setting":
            if "：" in body:
                term, _, definition = body.partition("：")
                out.append((line, {"category": "术语", "term": term.strip(),
                                   "definition": definition.strip()}))
            elif ":" in body:
                term, _, definition = body.partition(":")
                out.append((line, {"category": "术语", "term": term.strip(),
                                   "definition": definition.strip()}))
        elif kind in ("卷纲", "章纲"):
            sep = "：" if "：" in body else ":"
            t, _, c = body.partition(sep)
            t, c = t.strip(), c.strip()
            # Some live MiMo replies repeat the volume prefix for subordinate
            # stage goals. Keep those details with their actual parent volume.
            stage = re.match(r'^第(\d+)卷\s*阶段目标\s*\d+(?:\s|[（(]|$)', t)
            if kind == '卷纲' and stage and out:
                parent = re.match(r'^第(\d+)卷(?:\s|[^\d])', out[-1][1]['title'])
                if parent and parent.group(1) == stage.group(1):
                    out[-1][1]['content'] += '\n' + body
                    continue
            # 兜底：标题没带「第N章/卷」就自动补号，保证条目是完整名称
            if start_num is not None and not re.match(rf"^第\d+{unit}", t):
                t = f"第{n}{unit} {t}"
                n += 1
            out.append((line, {"title": t, "content": c}))
        elif kind == "foreshadow":
            parts = [p.strip() for p in body.split("|")]
            if len(parts) >= 1:
                out.append((line, {"content": parts[0],
                                   "ftype": parts[1] if len(parts) > 1 and parts[1] else "悬念",
                                   "planted_ch": parts[2] if len(parts) > 2 else "",
                                   "plan_ch": parts[3] if len(parts) > 3 else ""}))
    return out
