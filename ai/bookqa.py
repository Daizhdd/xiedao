# -*- coding: utf-8 -*-
"""书目问答检索层（v3 P0）。
凡关于故事内容的问题（人物/情节/设定/伏笔/某章写了什么），先在本层检索证据，
再让模型只依据证据作答——查不到就说查不到，杜绝编造。

线程约束与 intent 相同：gather_evidence 必须在主线程调用（SQLite 连接不能
跨线程），返回纯数据 dict；answer 只做网络调用，可安全跑在子线程。"""
import re

from ai import client as aiclient
from ai import fallback as fb
from ai import book_index

QA_TIMEOUT = 120        # 短回答不需要长死线；模型悬挂时 120s 必失败给反馈
_HEAD_TAIL = 1500       # 命中章节正文首尾各截取长度
_MAX_CHAPTERS = 3       # 最多带几章证据
_MAX_CHARS = 6          # 常备人物词条上限
_MAX_HITS = 4           # 关键词命中的其他词条上限
_CHAR_CLIP = 120        # 单条设定定义截断
_MAX_FORESHADOWS = 8    # 伏笔台账条目上限
QA_EVIDENCE_BUDGET = 6000

# 问题里的虚词/疑问词不算检索关键词（单字过滤双字组）
_STOP = set("的了吗呢吧啊呀哦嘛么什谁哪怎如何为啥多少几是很都也就还在有个这那与和及或把被向往会能要")


def _keywords(question):
    """中文无分词，用双字滑窗 + 去停用字当关键词：
    『主角叫什么名字』→ 主角/角叫/名字。"""
    q = re.sub(r"\s+", "", question or "")
    if len(q) < 2:
        return []
    kws, seen = [], set()
    for i in range(len(q) - 1):
        g = q[i:i + 2]
        if g in seen or any(ch in _STOP for ch in g):
            continue
        seen.add(g)
        kws.append(g)
        if len(kws) >= 24:      # 超长问题只取前段，限制检索开销
            break
    return kws


SYSTEM_QA = (
    "你是网文创作搭档，正在回答用户关于『这本书已写内容』的问题。"
    "铁律：只依据【检索结果】作答；检索结果里没有的信息，"
    "直接说「书里目前还没写到/设定库里没有记录」，绝不编造情节、人名、设定。"
    "涉及章节内容的可以注明是第几章。回答简洁直接，先给答案再给依据，全程中文。")


def _fmt_setting(r):
    d = (r["definition"] or "").strip()
    d = d[:_CHAR_CLIP] + "…" if len(d) > _CHAR_CLIP else d
    return f"{(r['category'] or '')[:20]}｜{(r['term'] or '')[:60]}：{d}"


def gather_evidence(db, pid, question):
    """主线程调用：抓与问题相关的证据，返回纯数据 dict（不含 DB 对象）。
    设定、伏笔与章节全文分别检索；只读取选中章节的正文。
    正文中部命中带位置片段；问『第N章』时直接定位并排除后续章节。"""
    project = db.get_project(pid)
    if project is None:
        return None
    q = (question or "").strip()
    kws = _keywords(q)
    m = re.search(r"第(\d+)章", q)
    want_no = int(m.group(1)) if m else 0

    # 1) 设定库：人物词条常备（人物问题是最高频），其余词条按关键词双向命中
    rows = db.get_settings(pid)
    char_ids = {r["id"] for r in rows if (r["category"] or "") == "人物"}
    chars = [r for r in rows if r["id"] in char_ids]
    hits = []
    for r in rows:
        if r["id"] in char_ids:
            continue
        term = (r["term"] or "").strip()
        blob = term + (r["definition"] or "")
        if (len(term) >= 2 and term in q) or any(k in blob for k in kws):
            hits.append(r)
    settings_lines = [_fmt_setting(r) for r in chars[:_MAX_CHARS]]
    settings_lines += [_fmt_setting(r) for r in hits[:_MAX_HITS]]

    # 2) 伏笔台账：关键词命中；问「伏笔」本身时把台账带上
    fh_lines = []
    if kws or "伏笔" in q:
        fh = db.get_foreshadows(pid)
        scored = [f for f in fh if any(k in f["content"] for k in kws)]
        take = scored if scored else (fh if "伏笔" in q else [])
        for f in take[:_MAX_FORESHADOWS]:
            fh_lines.append(
                f"[{f['status']}] {f['content'][:100]}"
                f"（埋设：{f['planted_ch'] or '?'}，计划回收：{f['plan_ch'] or '未定'}）")

    # 3) 章节：标题/章节卡/摘要关键词计分 + 「第N章」直接定位
    ranked, backend = book_index.search(db, pid, q, kws, limit=_MAX_CHAPTERS)
    picked, picked_ids = [], set()
    if want_no:
        c = db.conn.execute('SELECT id FROM chapters WHERE project_id=? AND chapter_no=? '
                            'ORDER BY id DESC LIMIT 1', (pid, want_no)).fetchone()
        if c:
            picked.append(c)
            picked_ids.add(c["id"])
    for c in ranked:
        if want_no and c['chapter_no'] > want_no:
            continue
        if len(picked) >= _MAX_CHAPTERS:
            break
        if c["id"] not in picked_ids:
            picked.append(c)
            picked_ids.add(c["id"])
    ch_list = []
    for c in picked:
        c = db.get_chapter(c['id'])
        content = (c["content"] or "").strip()
        ch_list.append({
            "no": c["chapter_no"], "vol": c["volume"], "title": c["title"],
            "summary": (c["summary"] or "").strip(),
            "written": bool(content),
            "head": content[:_HEAD_TAIL] if content else "",
            # 正文不长时 head 已覆盖全文，不重复带 tail
            "tail": content[-_HEAD_TAIL:] if len(content) > _HEAD_TAIL * 2 else "",
            "matches": book_index.excerpts(content, q, kws),
        })

    return {
        "title": project["title"],
        "genre": project["genre"] or "未知",
        "logline": (project["logline"] or "").strip()[:80],
        "settings": settings_lines,
        "foreshadows": fh_lines,
        "chapters": ch_list,
        "search_backend": backend,
    }


def _evidence_block_unbounded(ev):
    lines = [f"《{ev['title'][:120]}》题材：{ev['genre'][:40]}；一句话概念：{ev['logline'][:80] or '（无）'}"]
    lines.append("【检索结果】")
    if ev["settings"]:
        lines.append("◇ 设定库命中：")
        lines += [f"  {s}" for s in ev["settings"]]
    if ev["foreshadows"]:
        lines.append("◇ 伏笔台账命中：")
        lines += [f"  {f}" for f in ev["foreshadows"]]
    if ev["chapters"]:
        lines.append("◇ 相关章节：")
        for c in ev["chapters"]:
            lines.append(f"  第{c['no']}章《{c['title'][:120]}》（第{c['vol']}卷）")
            if not c["written"]:
                lines.append("  （本章还没有正文）")
                continue
            if c["summary"]:
                lines.append(f"  摘要：{c['summary'][:200]}")
            for match in c.get('matches', []):
                lines.append(f"  正文命中片段（第{match['start'] + 1}字附近）：{match['text']}")
            if c["head"] and not c.get('matches'):
                lines.append(f"  正文开头：{c['head'][:800]}…")
            if c["tail"] and not c.get('matches'):
                lines.append(f"  正文结尾：…{c['tail'][-800:]}")
    if not (ev["settings"] or ev["foreshadows"] or ev["chapters"]):
        lines.append("  （无命中内容）")
    return "\n".join(lines)


def _evidence_block(ev):
    """Cap what enters the model even for oversized metadata or old records."""
    return _evidence_block_unbounded(ev)[:QA_EVIDENCE_BUDGET]


def answer(cfg, evidence, question):
    """子线程调用：模型只依据证据作答，返回文本。失败抛异常由 UI 呈现。
    默认模型连续异常时自动换用其他已配置模型完成本步（降级链）。"""
    if not (evidence['settings'] or evidence['foreshadows'] or evidence['chapters']):
        return '书里目前还没写到，或设定库里没有记录；没有找到可供回答的依据。'
    user = f"{_evidence_block(evidence)}\n\n【问题】{(question or '')[:1000]}\n请回答。"
    return fb.guard(
        "书目问答", cfg,
        lambda c: aiclient.simple_chat(c, SYSTEM_QA, user,
                                       temperature=0.3, max_tokens=800,
                                       timeout=QA_TIMEOUT))
