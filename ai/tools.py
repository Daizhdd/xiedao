# -*- coding: utf-8 -*-
"""登记 agent：写完一章后由模型自主调用工具维护设定库与伏笔台账。

工具集（刻意保持小而稳）：
- list_settings      查现有设定（防重复添加）
- add_setting        提交有证据的设定候选，作者确认后生效
- add_foreshadow     登记新埋伏笔
- resolve_foreshadow 把已被本章回收的伏笔标记为「已回收」

人机边界：登记是可逆的轻量操作（词条/台账随时可删改），直接入库；
正文与摘要仍走「AI草稿」人工复核流程。
"""
import json

from ai import client as aiclient
from ai import prompts
from ai import foreshadow_memory
from ai import setting_candidates

MAX_ROUNDS = 6
CONTENT_CLIP = 6000

CATEGORIES = ("人物", "力量体系", "术语", "地理", "历史")
FTYPES = ("悬念", "物件", "身份", "线索", "冲突")


def _spec(name, desc, props, required):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": required}}}


TOOLS_SPEC = [
    _spec("list_settings", "查看本书现有全部设定词条（用于避免重复添加）", {}, []),
    _spec("add_setting", "提交一条有正文证据的设定候选，等待作者确认后才生效",
           {"category": {"type": "string", "enum": list(CATEGORIES), "description": "分类"},
            "term": {"type": "string", "description": "词条名，2-10 字"},
            "definition": {"type": "string", "description": "候选定义：具体可执行的规则/表现/限制，50-150 字"},
            "quote": {"type": "string", "description": "本章正文中唯一可定位的逐字短句"}},
           ["category", "term", "definition", "quote"]),
    _spec("add_foreshadow", "登记一条本章新埋下的伏笔（悬念/钩子）",
          {"content": {"type": "string", "description": "伏笔内容，一句话讲清悬念是什么"},
           "ftype": {"type": "string", "enum": list(FTYPES), "description": "类型"},
           "plan_ch": {"type": "string", "description": "计划回收章节，如『第40章』；不确定可留空"},
           "quote": {"type": "string", "description": "正文中逐字存在的埋设证据"}},
          ["content", "ftype"]),
    _spec("resolve_foreshadow", "提交一条疑似回收候选，作者核验原文后确认（id 见任务上下文）",
          {"foreshadow_id": {"type": "integer", "description": "伏笔台账 id"},
           "quote": {"type": "string", "description": "本章正文中逐字存在的回收证据"},
           "note": {"type": "string", "description": "回收方式备注，可选"}},
          ["foreshadow_id"]),
]


# ---------- 执行器 ----------
def _exec_list_settings(db, pid):
    rows = db.get_settings(pid)
    if not rows:
        return "（本书还没有任何设定词条）"
    return "\n".join(f"- [{r['category']}] {r['term']}：{(r['definition'] or '')[:80]}"
                     for r in rows)


def _exec_add_setting(db, pid, args, chapter_id=0):
    category = args.get("category") or "术语"
    if category not in CATEGORIES:
        category = "术语"
    term = (args.get("term") or "").strip()
    definition = (args.get("definition") or "").strip()
    quote = (args.get("quote") or "").strip()
    if not term:
        return "错误：term 不能为空"
    if not chapter_id or not quote:
        return '错误：设定候选必须附本章正文中的逐字证据'
    chapter = db.get_chapter(chapter_id)
    if chapter is None or chapter['project_id'] != pid:
        return '错误：设定候选的来源章节不属于本书'
    try:
        candidate_id = setting_candidates.propose_candidate(
            db, chapter_id, category, term, definition, quote)
    except ValueError as exc:
        return f'错误：{exc}'
    return f"候选已记录：#{candidate_id} [{category}] {term}（待作者确认）"


def _exec_add_foreshadow(db, pid, chapter_no, args, chapter_id=0):
    content = (args.get("content") or "").strip()
    ftype = args.get("ftype") or "悬念"
    if not content:
        return "错误：content 不能为空"
    for r in db.get_foreshadows(pid):
        if r["content"][:12] == content[:12]:
            return f"跳过：相似伏笔已登记（{r['content'][:20]}…）"
    fid = db.add_foreshadow(pid, content, ftype if ftype in FTYPES else "悬念",
                            planted_ch=f"第{chapter_no}章",
                            plan_ch=(args.get("plan_ch") or "").strip())
    quote = (args.get('quote') or '').strip()
    if chapter_id and quote:
        try:
            foreshadow_memory.record_plant(db, fid, chapter_id, quote)
        except ValueError:
            return f'已登记伏笔：{content[:30]}（埋设证据待核对，尚不进入写作记忆）'
    return (f"已登记伏笔：{content[:30]}（{ftype}，埋于第{chapter_no}章；"
            + ('已核对原文）' if chapter_id and quote else '来源待核对）'))


def _exec_resolve_foreshadow(db, pid, args, chapter_id=0):
    fid = args.get("foreshadow_id")
    try:
        fid = int(fid)
    except (TypeError, ValueError):
        return "错误：foreshadow_id 必须是整数"
    f = db.get_foreshadow(fid)
    if f is None or f["project_id"] != pid:
        return f"错误：id={fid} 不属于本书"
    if f["status"] == "已回收":
        return f"跳过：伏笔 id={fid} 已是已回收状态"
    quote = (args.get('quote') or '').strip()
    if not chapter_id or not quote:
        return '错误：回收判断必须提供当前正文中的逐字证据'
    try:
        foreshadow_memory.propose_resolution(db, fid, chapter_id, quote)
    except ValueError as exc:
        return f'错误：{exc}'
    return f"疑似回收：id={fid} {f['content'][:30]}（待作者确认）"


# ---------- 登记主循环 ----------
def register_chapter(cfg, db, pid, chapter_no, title, content, log=None,
                     budget=None, cancel_event=None, chapter_id=0):
    """让模型读本章正文并自主调工具登记。返回事件描述列表（供日志展示）。"""
    events = []
    if chapter_id:
        chapter = db.get_chapter(chapter_id)
        if (chapter is None or chapter['project_id'] != pid
                or chapter['chapter_no'] != chapter_no
                or chapter['content'] != content):
            raise ValueError('登记来源章节与当前正文不一致')
    else:
        matches = [c for c in db.get_chapters(pid)
                   if c['chapter_no'] == chapter_no and c['content'] == content]
        chapter_id = matches[0]['id'] if len(matches) == 1 else 0
    open_fh = [f for f in db.get_foreshadows(pid) if f["status"] == "待回收"]
    fh_text = "\n".join(f"- id={f['id']}：{f['content'][:40]}（{f['ftype']}，埋于{f['planted_ch'] or '?'}）"
                        for f in open_fh) or "（暂无待回收伏笔）"
    user = (
        f"本章：第{chapter_no}章 {title}\n\n"
        f"【本章正文】\n{content[:CONTENT_CLIP]}\n\n"
        f"【现有设定词条（已存在的不要重复添加）】\n{_exec_list_settings(db, pid)}\n\n"
        f"【待回收伏笔台账（如本章已明确回收，调 resolve_foreshadow）】\n{fh_text}\n\n"
        "请开始登记。"
    )
    messages = [{"role": "system", "content": prompts.SYSTEM_REGISTRAR},
                {"role": "user", "content": user}]

    def _emit(msg):
        if log:
            log(msg)

    for _round in range(MAX_ROUNDS):
        if cancel_event is not None and cancel_event.is_set():
            raise aiclient.CallCancelled('用户已停止任务')
        extra = {'budget': budget} if budget is not None else {}
        resp = aiclient.chat_once_tools(cfg, messages, TOOLS_SPEC,
                                        thinking="disabled", cancel_event=cancel_event,
                                        **extra)
        calls = resp["tool_calls"]
        if not calls:
            final = resp["content"]
            if final:
                _emit(f"  登记完成：{final[:80]}")
            return events
        messages.append({"role": "assistant",
                         "content": resp["content"] or "",
                         "tool_calls": calls})
        for tc in calls:
            if cancel_event is not None and cancel_event.is_set():
                raise aiclient.CallCancelled('用户已停止任务')
            fn = (tc.get("function") or {})
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {}
            if name == "list_settings":
                result = _exec_list_settings(db, pid)
            elif name == "add_setting":
                result = _exec_add_setting(db, pid, args, chapter_id=chapter_id)
                if result.startswith("候选已记录"):
                    events.append("设定" + result)
                    _emit(f"  📌 {result}")
            elif name == "add_foreshadow":
                result = _exec_add_foreshadow(db, pid, chapter_no, args,
                                              chapter_id=chapter_id)
                if result.startswith("已登记"):
                    events.append(result[4:].strip())
                    _emit(f"  🪤 {result}")
            elif name == "resolve_foreshadow":
                result = _exec_resolve_foreshadow(db, pid, args,
                                                   chapter_id=chapter_id)
                if result.startswith("疑似回收"):
                    events.append(result)
                    _emit(f"  ✅ {result}")
            else:
                result = f"错误：未知工具 {name}"
            messages.append({"role": "tool",
                             "tool_call_id": tc.get("id", ""),
                             "content": result})
    _emit("  ⚠ 登记轮次达上限，中断本轮")
    return events
