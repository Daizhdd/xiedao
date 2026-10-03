# -*- coding: utf-8 -*-
"""意图路由器：把用户的自然语言变成任务（工具调用）或闲聊回复。
一次调用双职——命中工具就返回动作，否则直接以创作搭档身份回答。"""
import json
import re
import time

from ai import client as aiclient
from ai import fallback as fb
from ai.context import eff_vol, sorted_vol_outlines

HISTORY_TURNS = 6
INTENT_TIMEOUT = 90      # 聊天路由不需要长输出；拥堵时快速失败给 UI 反馈
INTENT_RETRIES = 1

# 用户明确要"真删真改"的关键词：模型选错工具时用于代码级纠正
_REWRITE_PAT = re.compile(r"重写|删掉|删了|推翻|推倒|重来|重新规划|重新生成|重构")

# 『写到第N章』类补写指令（含中文数字章号）——选错工具时纠正为 write_until
_UNTIL_PAT = re.compile(
    r"(?:写到|补到|补充到|补写到|续写到|写够|更新到)第([0-9零一二两三四五六七八九十]+)章")
_CN_DIGIT = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _cn_num(s):
    """『十』『十二』『二十』『二十一』→ int；解析不了返回 0"""
    s = (s or "").strip()
    if s.isdigit():
        return int(s)
    if "十" in s:
        tens, _, ones = s.partition("十")
        if (tens and tens not in _CN_DIGIT) or (ones and ones not in _CN_DIGIT):
            return 0
        t = _CN_DIGIT.get(tens, 1) if tens else 1
        o = _CN_DIGIT.get(ones, 0) if ones else 0
        return t * 10 + o
    if len(s) == 1:
        return _CN_DIGIT.get(s, 0)
    return 0

# 大动作（P1 两段式执行）：路由命中后先返回计划等用户确认，不直接开跑
BIG_ACTIONS = ("rebuild_book", "rewrite_book")


def latest_task(db, pid):
    """Read actual execution state; conversation promises are not tasks."""
    if not db.conn.execute("SELECT 1 FROM sqlite_master WHERE name='task_runs'").fetchone():
        return None
    row = db.conn.execute(
        'SELECT id,title,mode,status,error FROM task_runs WHERE project_id=? ORDER BY id DESC LIMIT 1',
        (pid,)).fetchone()
    return dict(row) if row else None


def local_decision(context, message):
    """Exact task continuation and status queries must not depend on tool calling."""
    text = re.sub(r'\s+', '', message or '')
    is_question = text.endswith(('？', '?'))
    text = text.rstrip('。！!？?')
    if re.fullmatch(r'(?:请|帮我)?(?:全书重构|重构任务)(?:开始了吗|启动了吗|在运行吗|进度如何|进度|写到哪了)', text):
        return {'type': 'tool', 'name': 'progress_report', 'args': {}}
    if is_question:
        return None
    if re.fullmatch(r'(?:请|帮我)?(?:继续|接着|恢复|重试)(?:完成|执行|启动|进行)?'
                    r'(?:上次的?|之前的?)?(?:全书重构|重构全书)(?:任务)?', text):
        task = context.get('latest_task')
        if task and task['mode'] == 'rebuild_book':
            if task['status'] in ('failed', 'interrupted', 'stopped', 'partial', 'paused_budget'):
                return {'type': 'tool', 'name': 'restart_rebuild', 'args': {'run_id': task['id']}}
            return {'type': 'tool', 'name': 'progress_report', 'args': {}}
        return {'type': 'chat', 'text': '这本书没有可重试的全书重构任务。请说明重构要求，生成执行计划后再启动。'}
    if re.fullmatch(r'(?:请|帮我)?(?:开始|启动|执行)?(?:全书重构|重构全书)', text):
        return _plan_result('rebuild_book', {'instruction': message, 'volumes': 0}, context, message)
    return None


def _spec(name, desc, props, required):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": required}}}


INTENT_TOOLS = [
    _spec("write_volume", "启动「写完一卷」流水线：自动补章纲、逐章写作+审稿+登记设定伏笔。"
          "用户说『把第N卷写完』『继续写』『开写下一卷』时调用",
          {"vol": {"type": "integer", "description": "目标卷号（阿拉伯数字）"}},
          ["vol"]),
    _spec("write_until", "把本书继续写到第N章：从现有最大章号往后，缺的章纲自动补齐，"
          "再逐章写作+审稿+登记。用户说『写到第N章』『补到第N章』『补充到第N章』"
          "『写够N章』这类带目标章号的续写时调用；『第十章』要换算成 10 传入",
          {"chapter_no": {"type": "integer", "description": "目标章号（阿拉伯数字）"}},
          ["chapter_no"]),
    _spec("whole_book", "启动全书模式：从设定草案→分卷大纲→逐卷写作→简介，"
          "每个阶段出草案等用户确认。仅用于从零开一本新书；"
          "本书已有正文时不要调用（只会全部跳过）",
          {}, []),
    _spec("progress_report", "汇报写作进度。用户问『写到哪了』『进度如何』『完成了多少』时调用",
          {}, []),
    _spec("show_outline", "查看本书已入库的大纲条目。用户说『列出卷纲』『看看章纲』『大纲是什么』时调用",
          {"level": {"type": "string", "enum": ["卷纲", "章纲"],
                     "description": "看哪一级大纲"}},
          ["level"]),
    _spec("ask_book", "回答关于本书已写内容的问题：主角/配角是谁、人物关系、某段情节、"
          "设定细节、伏笔进展、某章写了什么。凡用户在『问』故事内容（而不是要动手改），"
          "一律调这个，把用户的问题原话放进 question",
          {"question": {"type": "string", "description": "用户的问题原话"}},
          ["question"]),
    _spec("open_chapter", "打开某一章的编辑器查看/修改。仅当用户明确要打开编辑器"
          "（『打开第N章』『我要改第N章』）时调用；单纯问章节内容用 ask_book",
          {"chapter_no": {"type": "integer", "description": "章节号"}},
          ["chapter_no"]),
    _spec("rewrite_chapter", "真实执行：重写某一章的正文（旧稿自动备份为版本可回滚），"
          "重写后自动审稿。用户说『把第N章改掉/重写第N章/第N章不行换掉』时调用",
          {"chapter_no": {"type": "integer", "description": "章节号"},
           "instruction": {"type": "string",
                           "description": "用户的修改要求原文，如『节奏快点』『结局更狠』；没有则留空"}},
          ["chapter_no"]),
    _spec("rewrite_volume", "真实执行：重写整卷所有已有章节（旧稿备份可回滚，逐章自动审稿）。"
          "用户说『把第N卷重写』『第N卷全部重来』时调用",
          {"vol": {"type": "integer", "description": "卷号"},
           "instruction": {"type": "string", "description": "修改要求，没有则留空"}},
          ["vol"]),
    _spec("rewrite_book", "真实执行：全书重写——保留现有卷纲/章纲，把本书所有已有章节"
          "逐章重写（旧稿备份可回滚）。用户说『全书重写』且没提要重排大纲时调用",
          {"instruction": {"type": "string", "description": "修改要求，如『一定要符合书名』；没有则留空"}},
          []),
    _spec("rebuild_book", "真实执行的全书重构：先自动完整备份，然后删除旧卷纲/章纲/全部正文，"
          "按用户要求重新规划卷纲与章纲并逐章全部重写，全程无需确认。"
          "用户说『全书内容删掉重写』『推翻重来先写两卷』『重构全书』这类要真删真改的指令时调用",
          {"instruction": {"type": "string", "description": "全书总要求，如『一定要符合书名』"},
           "volumes": {"type": "integer", "description": "重写几卷；用户没说就传 0（按项目规划）"}},
          ["instruction"]),
]


def progress_text(db, pid, project=None):
    """本地进度报告（不花 token）。卷口径与流水线一致：卷号取「卷纲有效卷号
    ∪ 章节实际 volume」，重构换成新卷号×新章节号后依然自洽；
    未分卷的孤儿章纲单独提示，不混进任何卷的计数。"""
    project = project or db.get_project(pid)
    chapters = db.get_chapters(pid)
    written = [c for c in chapters if (c["content"] or "").strip()]
    # AI草稿系列状态（含 ·低分 / ·未审）都算「待人工复核」
    ai_draft = [c for c in written
                if (c["status"] or "").startswith("AI草稿")]
    lines = [f"《{project['title']}》进度：",
             f"- 章节共 {len(chapters)} 章，已有正文 {len(written)} 章"
             + (f"（其中 AI草稿 {len(ai_draft)} 章待你复核）" if ai_draft else "")]
    task = latest_task(db, pid)
    statuses = {'running': '正在执行', 'failed': '失败，当前未在写作',
                'interrupted': '已中断，当前未在写作', 'stopped': '已停止',
                'partial': '部分完成，当前未在写作', 'paused_budget': '预算暂停',
                'completed': '已完成', 'resumed': '已由后续任务接管'}
    if task:
        lines.append(f"- 最近实际任务：{task['title']}（{statuses.get(task['status'], task['status'])}）")
        if task['mode'] == 'rebuild_book' and task['status'] in ('failed', 'interrupted', 'stopped', 'partial', 'paused_budget'):
            lines.append('- 全书重构尚未完成；现有章节数不代表重构进度，可点「重新执行重构」按原要求重试。')
    else:
        lines.append('- 这本书尚未启动实际写作任务。')
    vol_rows = sorted_vol_outlines(db, pid)
    vol_nums = sorted({eff_vol(o, vol_rows) for o in vol_rows}
                      | {c["volume"] for c in chapters if c["volume"]})
    zgs = [o for o in db.get_outlines(pid) if o["level"] == "章纲"]
    zg_by_vol = {}
    for o in zgs:
        zg_by_vol.setdefault(o["volume"] or 0, []).append(o)
    for vol in vol_nums:
        chs = [c for c in chapters if c["volume"] == vol]
        done = sum(1 for c in chs if (c["content"] or "").strip())
        parts = [f"已写 {done}/{len(chs)} 章"]
        if zg_by_vol.get(vol):
            parts.insert(0, f"章纲 {len(zg_by_vol[vol])} 条")
        lines.append(f"- 第{vol}卷：{'，'.join(parts)}")
    orphans = zg_by_vol.get(0, [])
    if orphans:
        lines.append(f"- 另有 {len(orphans)} 条未分卷章纲（不计入任何卷）")
    fh = db.get_foreshadows(pid)
    if fh:
        wait = sum(1 for f in fh if f["status"] == "待回收")
        lines.append(f"- 伏笔：待回收 {wait}，已回收 {len(fh) - wait}")
    latest = max(chapters, key=lambda c: (c["volume"], c["chapter_no"])) if chapters else None
    if latest:
        state = "有正文" if (latest["content"] or "").strip() else "空"
        lines.append(f"- 最新：第{latest['chapter_no']}章 {latest['title']}（{state}）")
    return "\n".join(lines)


def book_context(db, pid):
    """书目上下文快照（v3：结构化书籍档案，让路由器开卷有书）。
    必须在主线程调用（SQLite 连接不能跨线程），
    返回纯数据 dict 供子线程里的 decide 使用；书不存在返回 None。"""
    project = db.get_project(pid)
    if project is None:
        return None
    chapters = db.get_chapters(pid)
    written = sum(1 for c in chapters if (c["content"] or "").strip())
    zgs = [o for o in db.get_outlines(pid) if o["level"] == "章纲"]
    fh = db.get_foreshadows(pid)
    fh_wait = sum(1 for f in fh if f["status"] == "待回收")
    chars = db.get_settings(pid, category="人物")
    characters = "\n".join(
        f"  - {r['term']}：{(r['definition'] or '').strip()[:60]}"
        for r in chars[:12]) or "  （设定库还没有人物词条）"
    vols = sorted([o for o in db.get_outlines(pid) if o["level"] == "卷纲"],
                  key=lambda x: (x["sort_no"], x["id"]))
    vol_lines = "\n".join(
        f"  第{(o['volume'] or i + 1)}卷 {o['title']}：{(o['content'] or '').strip()[:50]}"
        for i, o in enumerate(vols[:8])) or "  （还没有卷纲）"
    recent = "\n".join(
        f"  第{c['chapter_no']}章 {c['title']}（{(c['summary'] or '无摘要')[:100]}）"
        for c in chapters[-3:])
    return {
        "title": project["title"],
        "latest_task": latest_task(db, pid),
        "genre": project["genre"] or "?",
        "logline": (project["logline"] or "?")[:60],
        "written": written,
        "chapters": len(chapters),
        "outlines": len(zgs),
        "settings": len(db.get_settings(pid)),
        "fh_wait": fh_wait,
        "fh_total": len(fh),
        "characters": characters,
        "vols": vol_lines,
        "recent": recent or "  （还没有章节）",
    }


def _context_block(ctx):
    return (
        f"【当前书目】《{ctx['title']}》题材：{ctx['genre']}，"
        f"概念：{ctx['logline']}\n"
        f"【状态】正文 {ctx['written']}/{ctx['chapters']} 章，章纲 {ctx['outlines']} 条，"
        f"设定 {ctx['settings']} 条，伏笔待回收 {ctx['fh_wait']}/{ctx['fh_total']} 条\n"
        f"【主要角色】\n{ctx['characters']}\n"
        f"【分卷大纲】\n{ctx['vols']}\n"
        f"【最近章节】\n{ctx['recent']}")


def _text_tool_call(content):
    """部分模型（实测 mimo）会把工具调用当纯文本吐在正文里：
    <tool_call><parameter=fn><parameter=k>v</parameter>...</tool_call>
    或 <tool_call>{"name":...,"arguments":{...}}</tool_call>。
    这里把它们抠成 (name, args)；不是工具调用返回 None。"""
    if not content or "<tool_call>" not in content:
        return None
    m = re.search(r"<tool_call>(.*?)</(?:tool_call|function)?>", content, re.S)
    if not m:
        return None
    inner = m.group(1).strip()
    try:
        obj = json.loads(inner)
        if isinstance(obj, dict) and obj.get("name"):
            return obj["name"], obj.get("arguments") or {}
    except ValueError:
        pass
    head = re.match(r"<parameter=([A-Za-z_][A-Za-z0-9_]*)>", inner)
    if not head:
        return None
    name = head.group(1)
    args = {}
    for km in re.finditer(r"<parameter=([A-Za-z_][A-Za-z0-9_]*)>(.*?)</parameter>",
                          inner[head.end():], re.S):
        key, val = km.group(1), km.group(2).strip()
        if re.fullmatch(r"-?\d+", val):
            val = int(val)
        args[key] = val
    return name, args


def _plan_result(name, args, context, message):
    """大动作的两段式计划：一行步骤 + 原始参数。UI 渲染成计划卡，
    用户点确认后才真正执行（_dispatch 收到 tool 才开跑）。"""
    if name == "rebuild_book":
        try:
            vols = int(args.get("volumes") or 0)
        except (TypeError, ValueError):
            vols = 0
        scope = (f"重新规划 {vols} 卷的卷纲与章纲" if vols
                 else "重新规划全书卷纲与章纲")
        steps = (f"1. 旧稿完整备份成 JSON（含版本史、伏笔台账）→ 2. {scope} → "
                 "3. 解析通过后才删旧数据 → 4. 逐章重写全部正文，中途不再确认；"
                 "任一环节失败书本保持原样。伏笔台账默认保留，计划卡上可勾选清空")
    else:
        steps = (f"保留现有卷纲章纲，逐章重写全部已有正文"
                 f"（约 {context.get('written', 0)} 章）；"
                 "旧稿自动备份为版本，编辑器「版本历史」可回滚")
    instr = (args.get("instruction") or "").strip()
    if instr:
        steps += f"\n总要求：{instr}"
    return {"type": "plan", "name": name, "args": args, "steps": steps}


def decide(cfg, context, message, history=()):
    """意图路由 + 闲聊双职。context: book_context() 快照（主线程抓好传入，
    本函数不做任何 DB 访问，可安全跑在子线程）。
    返回 {"type":"chat","text":...}、{"type":"tool","name":...,"args":{...}}
    或大动作计划 {"type":"plan","name":...,"args":{...},"steps":...}（待确认）。"""
    if not context:
        raise RuntimeError("请先选择一本书")
    local = local_decision(context, message)
    if local is not None:
        return local
    system = (
        "你是用户的网文创作搭档，兼任任务路由。"
        + _context_block(context)
        + "\n【规则】\n"
        "- 真实执行必须返回工具调用，不能只在文字中宣称已经开始写作或重构。\n"
        "- 用户在讨论中做选择或表态（如『就第3个』『选1』『还是算了』），"
        "且没有附带具体写作要求时，是接着上一句话继续聊：直接以搭档身份回应\n"
        "- 用户要真实改动已有内容（『把第N章改掉/重写』『重写第N卷』『全书重写』"
        "『彻底推翻重来』『删掉重写』）→ rewrite_chapter / rewrite_volume / "
        "rewrite_book / rebuild_book，把用户的附加要求（节奏、风格、"
        "必须符合书名等）原文放进 instruction；要重排卷纲章纲就用 rebuild_book\n"
        "- write_volume 仅用于给没有正文的章写新稿；whole_book 仅用于从零开一本新书\n"
        "- 用户说『（继续/补充）写到第N章』『补到第N章』『写够第N章』→ write_until，"
        "chapter_no=目标章号（『第十章』传 10）；这不是重写、也不是写完某一卷\n"
        "- 凡是关于故事内容的问题（人物是谁、人物关系、某段情节、设定细节、"
        "伏笔进展、某章写了什么）→ 调 ask_book，把问题原话放进 question，"
        "禁止用 open_chapter 代替回答。若上面的【主要角色】【分卷大纲】"
        "档案已足够回答也可以直接答；细节拿不准就 ask_book，不要编\n"
        "- progress_report / show_outline / ask_book 是只读查询，随时可用；"
        "open_chapter 仅在用户明确要打开编辑器看或改某一章时调用\n"
        "- 回复里绝不提工具名（write_volume、ask_book 这类词），"
        "用自然的话描述你要做什么\n"
        "- 用户说『继续写』『下一卷』且没给卷号时，选还没有正文的最靠前卷；"
        "完全无法推断就在回复里问一句\n"
        "- 其余一切（聊剧情、求点子、讨论设定、写作建议）直接以创作搭档身份回答，"
        "结合书目上下文给具体有干货的内容，不空话\n- 全程中文")
    msgs = [{"role": "system", "content": system}]
    for role, content in list(history)[-HISTORY_TURNS:]:
        if role in ("user", "assistant") and content:
            msgs.append({"role": role, "content": content[:1200]})
    msgs.append({"role": "user", "content": message})

    def _route_chain(c):
        last = None
        for attempt in range(INTENT_RETRIES + 1):
            try:
                return aiclient.chat_once_tools(c, msgs, INTENT_TOOLS,
                                                temperature=0.4, max_tokens=2048,
                                                timeout=INTENT_TIMEOUT,
                                                thinking="disabled")
            except Exception as e:  # noqa
                last = e
                if attempt < INTENT_RETRIES:
                    time.sleep(1.5)     # 拥堵抖动：短暂停后重试一次
        raise last

    resp = fb.guard("意图路由", cfg, _route_chain)
    calls = resp.get("tool_calls") or []
    name = args = None
    if calls:
        fn = calls[0].get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except ValueError:
            args = {}
        name = fn.get("name", "")
    else:
        # 工具调用被模型当纯文本吐出来（mimo 实测）→ 从正文抠出
        tc = _text_tool_call(resp.get("content") or "")
        if tc:
            name, args = tc
    if name:
        # 代码级兜底：用户话里明确要"真删真改"而模型却选了"写新内容"类工具时，
        # 直接纠正为正确的重写/重构工具（不依赖模型自觉）。
        # 纠正出的 rebuild_book 同样走两段式计划确认（P1：纠正保留为最后防线）
        if (name in ("write_volume", "whole_book")
                and context.get("written") and _REWRITE_PAT.search(message or "")):
            m_ch = re.search(r"第(\d+)章", message or "")
            m_vol = re.search(r"第(\d+)卷", message or "")
            if m_ch:
                return {"type": "tool", "name": "rewrite_chapter",
                        "args": {"chapter_no": int(m_ch.group(1)),
                                 "instruction": message}}
            if m_vol:
                return {"type": "tool", "name": "rewrite_volume",
                        "args": {"vol": int(m_vol.group(1)),
                                 "instruction": message}}
            return _plan_result("rebuild_book",
                                {"instruction": message,
                                 "volumes": int(args.get("vol") or 0)},
                                context, message)
        # 『写到第N章』代码级兜底：模型塞给 write_volume/whole_book 时纠正为
        # write_until；write_until 章号缺失时从原话补上（支持中文数字『第十章』）
        m_until = _UNTIL_PAT.search(message or "")
        if m_until:
            no = _cn_num(m_until.group(1))
            if name in ("write_volume", "whole_book") and no:
                return {"type": "tool", "name": "write_until",
                        "args": {"chapter_no": no}}
            if name == "write_until" and no:
                try:
                    had = int(args.get("chapter_no") or 0)
                except (TypeError, ValueError):
                    had = 0
                if not had:
                    args = {"chapter_no": no}
        if name in BIG_ACTIONS:
            return _plan_result(name, args, context, message)
        return {"type": "tool", "name": name, "args": args}
    return {"type": "chat", "text": (resp.get("content") or "").strip()}
