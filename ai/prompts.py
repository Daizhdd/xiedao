# -*- coding: utf-8 -*-
"""AI 小说工作台 · 提示词模板（生成 / 润色 / 摘要 / 滚动摘要 / 上下文包）"""

# ---- Token 预算（按中文字符数近似估算）----
PACK_BUDGET = 6000          # 整个上下文注入包的字符总预算
CARD_LIMIT = 1200           # 章节卡上限
TAIL_LIMIT = 800            # 上一章结尾原文上限
VOL_SUMMARY_LIMIT = 700     # 卷级滚动摘要上限
RECENT_SUMMARY_LIMIT = 160  # 单章近程摘要上限
STYLE_LIMIT = 900           # 作品档案、作者规则与已采纳建议合计上限
FORESHADOW_LIMIT = 500      # 伏笔提醒上限
SETTING_ENTRY_LIMIT = 200   # 单条设定词条上限


def _clip(text, limit):
    text = (text or "").strip()
    return text[:limit] + "…" if len(text) > limit else text


# ---- 设定召回：命中优先，分类优先级补足 ----
CATEGORY_PRIORITY = {"人物": 0, "力量体系": 1, "术语": 2, "地理": 3, "历史": 4}


def recall_settings(rows, texts, max_items=12, min_fill=6):
    """rows: settings_dict 行；texts: 用于匹配命中的文本列表（章节卡/近两章结尾）。
    返回词条列表：命中的在前；不足 min_fill 时按分类优先级补足；总数不超过 max_items。"""
    blob = "".join(t for t in texts if t)
    matched, rest = [], []
    for r in rows:
        term = (r["term"] or "").strip()
        is_hit = len(term) >= 2 and term in blob
        (matched if is_hit else rest).append(r)
    matched.sort(key=lambda r: CATEGORY_PRIORITY.get(r["category"], 9))
    rest.sort(key=lambda r: CATEGORY_PRIORITY.get(r["category"], 9))
    out = matched[:max_items]
    if len(out) < min_fill:
        out += rest[:min_fill - len(out)]
    return out[:max_items]


# ---- 三层记忆上下文包组装 ----
# 实时层＝上一章结尾原文；近程层＝最近 N 章摘要；远程层＝卷级滚动摘要 + 书级主线
# 组装采用贪心预算分配：keep=True 的段必保，其余按优先级从低到高截断/丢弃
def build_context_pack(project, related_settings, chapter_card,
                       prev_tail="", recent_summaries=(), volume_summary="",
                       foreshadow_hint="", volume_outline="", chapter_outline="",
                       story_memory="", style_override=None):
    """project: dict/sqlite3.Row；related_settings: 召回后的词条列表；
    recent_summaries: [(chapter_no, title, summary), ...]；
    volume_summary: 该卷滚动摘要；prev_tail: 上一章结尾原文；
    volume_outline: 本卷对应卷纲内容；chapter_outline: 本章绑定章纲内容。"""
    project = dict(project) if not isinstance(project, dict) else project
    book_line = _clip(project.get("logline", ""), 100)
    style = _clip(project.get("style_sheet", "") if style_override is None
                  else style_override, STYLE_LIMIT)

    # (标签, 内容, keep) —— keep 段不参与预算裁剪；越靠后优先级越低
    sections = []
    if chapter_card:
        sections.append((f"【本章章节卡（务必按此执行）】",
                         _clip(chapter_card, CARD_LIMIT), True))
    if chapter_outline:
        sections.append(("【本章章纲要点（本章必须完成这段剧情推进）】",
                         _clip(chapter_outline, 400), True))
    if volume_outline:
        sections.append(("【本卷卷纲（本卷主线任务，本章情节服务于此）】",
                         _clip(volume_outline, 400), True))
    if prev_tail:
        sections.append(("【上一章结尾（续写铁律：从这里自然接续，人物状态/场景/悬念完全承接，"
                         "绝不重写已发生的情节）】", _clip(prev_tail, TAIL_LIMIT), True))
    if book_line:
        sections.append(("【全书主线（一句话，所有情节必须服务于此）】", book_line, True))
    if style:
        sections.append(("【风格规范】", style, True))
    if story_memory:
        sections.append(("【已确认人物状态（仅含本章之前的有来源事实）】",
                         _clip(story_memory, 850), False))
    if volume_summary:
        sections.append(("【本卷故事至今（远期记忆：本卷已发生的主线进展与关键转折）】",
                         _clip(volume_summary, VOL_SUMMARY_LIMIT), False))
    recents = [s for s in recent_summaries if s[2]]
    if recents:
        lines = [f"第{no}章 {title}：{_clip(s, RECENT_SUMMARY_LIMIT)}"
                 for no, title, s in recents]
        sections.append(("【近几章回顾（近期记忆：人物状态、事件因果由此衔接）】",
                         "\n".join(lines), False))
    if related_settings:
        s = "\n".join(f"- {_clip(r['term'], 20)}：{_clip(r['definition'], SETTING_ENTRY_LIMIT)}"
                      for r in related_settings)
        sections.append(("【关键设定（必须严格遵守，不得改设定名与规则）】", s, False))
    if foreshadow_hint:
        sections.append(("【伏笔提醒（如本章出现相关情节，请自然埋设或回收）】",
                         _clip(foreshadow_hint, FORESHADOW_LIMIT), False))

    # 总预算裁剪：只动非 keep 段，每轮先尝试截断到 300 字，仍超则整段丢弃
    def _total():
        return sum(len(h) + len(b) for h, b, _k in sections)

    while _total() > PACK_BUDGET and any(not k for _h, _b, k in sections):
        idx = max(i for i, (_h, _b, k) in enumerate(sections) if not k)
        h, b, k = sections[idx]
        nb = _clip(b, 300) if len(b) > 300 else b
        if nb != b and _total() - len(b) + len(nb) <= PACK_BUDGET:
            sections[idx] = (h, nb, k)
        else:
            sections.pop(idx)
    parts = [f"{h}\n{b}" for h, b, _k in sections]
    return "\n\n".join(parts)


SYSTEM_WRITER = (
    "你是一名小说写手，按本书作品档案与作者要求写作。"
    "视角、节奏、语言密度和对话风格均以本书设定为准。"
    "严格遵循用户提供的设定，不得改动人名、地名、术语与规则。"
    "上下文包含多层记忆：【本章章节卡】与【本章章纲要点】是本章硬性任务；"
    "【本卷卷纲】【全书主线】【本卷故事至今】【近几章回顾】是逐层递进的前情记忆，"
    "人物状态与因果必须与之吻合，剧情推进必须服务卷纲；"
    "【上一章结尾】是你落笔的第一句必须接住的地方。"
    "【续写铁律】如果上下文里提供了『上一章结尾』，你必须从那里自然接续："
    "人物状态、场景、悬念、时间线与上一章严丝合缝，只写新进展，"
    "绝不重写或复述上一章已发生的情节，绝不从零开头、绝不重新介绍主角。"
    "输出纯正文，不要任何解释、标题或 markdown 符号。"
)

SYSTEM_POLISH = (
    "你是小说编辑。保持原意、情节、叙述视角和作品档案要求，"
    "修复语病与重复，使对话自然、表达清楚，不擅自改变故事事实。"
    "直接输出润色后的全文，不要解释。"
)

SYSTEM_SUMMARY = (
    "你是小说助手。请用 80-150 字概括本章情节要点，"
    "只保留对后续章节有用的事件、人物状态、伏笔信息，"
    "输出纯文本，不要标题。"
)

SYSTEM_ROLLUP = (
    "你是小说助手。请把该卷已有的「故事至今」滚动摘要与最新一章的摘要合并，"
    "更新为一份新的滚动摘要。要求："
    "- 保留贯穿本卷的主线进展、人物关系变化、重要伏笔状态\n"
    "- 已彻底完结的小事件细节可以删去或压缩成一句\n"
    "- 300-500 字，时间顺序叙述，输出纯文本，不要标题。"
)


def rollup_prompt(old_rollup, chapter_no, title, chapter_summary):
    return (
        f"【本卷已有故事至今】\n{old_rollup or '（暂无，本卷第一份滚动摘要）'}\n\n"
        f"【新完成的一章】第{chapter_no}章 {title}\n本章摘要：{chapter_summary}\n\n"
        "请合并输出更新后的「本卷故事至今」滚动摘要。"
    )


def gen_chapter_prompt(context_pack, outline=""):
    u = f"{context_pack}\n\n" if context_pack else ""
    if outline:
        u += f"【大纲参考】\n{outline}\n\n"
    u += "请据此写出本章完整正文（2000-3000 字），结尾留一个钩子。"
    return u


def polish_prompt(text):
    return text


def summary_prompt(text):
    return f"以下是本章正文：\n\n{text[:8000]}\n\n请输出本章摘要。"


# ---- 双模式：各环节 AI 草稿提示词（草稿=可编辑，不直接入库）----
SYSTEM_ASSIST = (
    "你是网络小说创作助手，根据用户提供的上下文，直接给出可用的草稿内容。"
    "输出要具体、有网文质感、可直接采用或轻改。不要空话套话，不要解释过程。"
    "如果用户要求输出列表，用「- 」分条。"
)

# ---- 登记 agent：章节写完后自主维护设定库与伏笔台账（工具调用）----
SYSTEM_REGISTRAR = (
    "你是网文设定管理员，通过调用工具提出设定候选并维护伏笔台账。阅读本章正文后：\n"
    "1) 正文中首次出现且值得全局沉淀的设定（人物/力量体系/地理/历史/术语）→ 调 add_setting 提交候选，"
    "附上本章正文中唯一可定位的逐字短句；候选须经作者确认才进入正式设定库；"
    "已有词条不要重复添加，主角等一眼可知的信息也不要机械登记\n"
    "2) 本章新埋下的悬念/钩子 → 调 add_foreshadow（planted_ch 即本章）\n"
    "3) 本章明确回收了的伏笔（对照待回收台账）→ 调 resolve_foreshadow\n"
    "要求宁缺毋滥：只登记正文里确定出现的，不确定的不要编。"
    "全程用中文。需要时先调 list_settings 核对。登记完简要汇报做了什么；"
    "没有可登记的就直接回复「无」。"
)

# ---- 审稿 agent：对照上下文包自审，输出分数与问题清单 ----
SYSTEM_REVIEWER = (
    "你是资深网文主编，负责审稿。对照【审稿依据】逐项检查文末的【正文】：\n"
    "1. 设定一致性：人名/地名/术语/力量体系是否与【关键设定】冲突，有无擅自改设定\n"
    "2. 承接：是否自然接续【上一章结尾】，有无重写已发生情节、人物状态或时间线矛盾\n"
    "3. 跟纲：是否完成【本章章节卡】【本章章纲要点】的目标与结尾卡点\n"
    "4. 作品文风：是否符合本书作者确认的视角、节奏、表达规则，有无明显重复\n"
    "只报告确定存在的问题，不吹毛求疵；没有问题的方面不要硬挑。全程用中文。"
    "输出严格的 JSON（不要 markdown 代码块、不要解释）：\n"
    '{"score": 整数, "categories":{"人物一致性":整数,"事件因果":整数,'
    '"章纲完成度":整数,"文风":整数,"节奏":整数},'
    '"issues":[{"kind":"上述类别","severity":"严重/一般/建议",'
    '"body_quote":"正文逐字引文","source_ref":"fact:数字/outline:数字/空串",'
    '"source_quote":"依据中的逐字引文或空串","explanation":"问题原因",'
    '"suggestion":"具体修改建议"}]}\n'
    "客观冲突必须有可核查依据；正文引文必须在本章出现且只出现一次。"
    "source_ref 与 source_quote 同时为空时，severity 必须写建议，不得写一般或严重；"
    "有来源时必须从给定依据逐字引用，字符串内双引号必须正确转义。"
    "评分标准：90+ 无硬伤；75-89 有小瑕疵可发；60-74 有明显问题需重写；"
    "60 以下有硬伤（设定冲突/未跟纲/上下文断裂）。issues 无问题时为空数组。"
)


def gen_project_draft(genre, logline, audience):
    return (
        f"请为一部【{genre or '待定'}】题材的网文生成立项草案，要求：\n"
        f"- 一句话概念（基于：{logline or '未提供，请自行构思一个抓人的概念'}）\n"
        f"- 目标读者画像（平台：{audience or '番茄男频'}）\n"
        f"- 核心卖点/爽点（3 条）\n"
        f"- 差异化定位（与同类书不同的 2 点）\n"
        f"- 建议标题（3 个候选，男频网文风格）"
    )


def gen_setting_draft(category, existing, term):
    return (
        f"设定档案库中【{category}】分类，请为词条「{term}」撰写标准定义草稿。\n"
        f"已有相关设定：\n{existing or '（无，请自由发挥）'}\n\n"
        "要求：定义要具体可执行（规则/表现/限制），100-200 字，网文设定风格，不要与已有设定冲突。"
    )


def gen_outline_draft(level, project, settings_text, hint):
    req = "卷纲按分卷结构" if level == "卷纲" else "章纲按章节推进"
    return (
        f"请生成【{level}】草稿。\n"
        f"书：{project.get('title', '')} ｜ 一句话概念：{project.get('logline', '')} ｜ 卖点：{project.get('selling_point', '')}\n"
        f"现有设定：\n{settings_text or '（无）'}\n"
        f"补充要求：{hint or '按经典网文节奏（铺垫-冲突-高潮-转折）'}\n\n"
        f"要求：{req}，写出具体条目，每条一句话讲清要点，共 5-8 条。"
    )


def gen_chapter_card_draft(outline_text, prev_summary, settings_text, title):
    return (
        f"请为章节「{title or '本章'}」生成章节卡草稿。\n"
        f"相关大纲：\n{outline_text or '（无，请合理设计本章推进）'}\n"
        f"前情提要：\n{prev_summary or '（无，可能是第一章）'}\n"
        f"关键设定：\n{settings_text or '（无）'}\n\n"
        "章节卡格式（按此输出）：\n"
        "- 本章目标：\n- 场景列表：\n- 出场人物：\n- 结尾卡点/悬念：\n- 需要埋设/回收的伏笔（如有）："
    )


def gen_foreshadow_draft(chapter_title, chapter_summary, existing):
    return (
        f"本书当前章节「{chapter_title or '?'}」的摘要：\n{chapter_summary or '（无）'}\n"
        f"已登记的伏笔：\n{existing or '（暂无）'}\n\n"
        "请建议 3-5 条值得埋设的伏笔，格式：\n"
        "- 内容 | 类型（悬念/物件/身份/线索/冲突） | 建议埋设章节 | 建议回收章节"
    )


# ---- 右键批量生成（结果可勾选后批量入库）----
def gen_settings_batch(project, existing):
    return (
        f"书：{project.get('title', '')} ｜ 一句话概念：{project.get('logline', '')} ｜ 卖点：{project.get('selling_point', '')}\n"
        f"已有设定：\n{existing or '（无）'}\n\n"
        "请建议 4-6 个本书最需要的设定词条（可含力量体系/地理/历史/术语/人物），"
        "每条格式严格为：\n- 词条名：一句话标准定义（网文设定风格，具体可执行）"
    )


def gen_volumes_plan(project, settings_text, total=None):
    """v1.7：全书分卷大纲整体规划——从开头到结局一次性规划每一卷"""
    total = int(total or 0)
    chapters = max(int(project.get('plan_chapters') or 10), 1)
    head = (f"请为本书做【全书分卷大纲】整体规划，共 {total} 卷。"
            if total else
            "请为本书做【全书分卷大纲】整体规划，卷数由你按故事体量决定（4-6 卷）。")
    return (
        f"{head}\n"
        f"书：{project.get('title', '')} ｜ 概念：{project.get('logline', '')} ｜ 卖点：{project.get('selling_point', '')}\n"
        f"篇幅：每卷 {chapters} 章；请按这个篇幅安排阶段目标，不要另定章数。\n"
        f"现有设定：\n{settings_text or '（无）'}\n\n"
        "要求：\n"
        "- 从开篇到结局完整闭环：每卷写清核心冲突＋阶段目标＋与下一卷的衔接钩子\n"
        "- 卷间递进（矛盾升级/格局扩大/反派变强），最后一卷收拢全部伏笔给终局\n"
        "- 每条格式严格为：\n- 第v卷 卷名：本卷主线任务（一句话讲清这卷要解决什么）\n"
        "-『第v卷 名称』与冒号后的说明是一个整体条目，v 连续递增，不要另行分组。"
        "所有阶段目标写在本卷冒号后的同一段里，禁止另列『第v卷阶段目标1』等子条目。\n"
        + (f"- 最终只输出 {total} 行卷纲，第1卷至第{total}卷各出现一次。"
           f"第{total}卷必须完成结局，不要留下需要计划外新卷才能完成的主线。" if total else "")
    )


def gen_volume_chapters(project, settings_text, vol_no, vol_title,
                        vol_line, ch_from, ch_to):
    """v1.7：单卷章纲——严格跟卷纲走，编号锁定该卷区间"""
    n = int(ch_to) - int(ch_from) + 1
    return (
        f"请为本书第{vol_no}卷《{vol_title}》生成【章纲】，共 {n} 条。\n"
        f"书：{project.get('title', '')} ｜ 概念：{project.get('logline', '')}\n\n"
        f"【本卷卷纲（章纲必须逐章落实这条主线，不可偏离）】\n{vol_line or '（无，请按经典节奏自行设计）'}\n\n"
        f"现有设定：\n{settings_text or '（无）'}\n\n"
        "要求：\n"
        f"- 共 {n} 条，编号从第{ch_from}章到第{ch_to}章连续，不得跳号、重号或使用区间外的编号\n"
        "- 节奏按本卷结构推进：开篇承接前情→冲突升级→高潮→收束留钩子到下一卷\n"
        "- 每条格式严格为：\n- 第N章 章节名：本章发生的事＋结尾卡点\n"
        "- 『第N章 名称』与冒号后的说明是一个整体条目，每条一行"
    )


def gen_foreshadows_batch(project, existing):
    return (
        f"书：{project.get('title', '')} ｜ 概念：{project.get('logline', '')} ｜ 卖点：{project.get('selling_point', '')}\n"
        f"已登记伏笔：\n{existing or '（暂无）'}\n\n"
        "请建议 4-6 条适合本书的伏笔，每条格式严格为：\n"
        "- 内容 | 类型（悬念/物件/身份/线索/冲突） | 建议埋设章节 | 建议回收章节"
    )


# ---- 卷末沉淀：把高频审稿意见提炼成风格规范（自学习回路）----
def style_distill_prompt(existing, issues):
    return (
        f"【现有风格规范】\n{existing or '（无）'}\n\n"
        f"【本卷审稿意见汇总（反复出现的问题）】\n"
        + "".join(f"- {x}\n" for x in issues)
        + "\n请从审稿意见中提炼 3-6 条可执行的写作风格规范，每条一句话、具体可操作"
        "（如『每章结尾落在悬念句上，不用总结式收尾』『对话占比不低于四成』）。\n"
        "只输出新增条目，每行格式为「- 具体规则｜一句可模仿的文本示例」；"
        "不要重复现有规范，不要解释。建议将交由作者审核，不自动生效。"
    )


def gen_intro_draft(title, logline, genre, selling):
    return (
        f"请为网文《{title or '未命名'}》写一段平台上传用的作品简介（200 字以内）。\n"
        f"题材：{genre or '待定'} ｜ 一句话概念：{logline or '（无）'} ｜ 核心卖点：{selling or '（无）'}\n\n"
        "要求：\n"
        "- 开头 1-2 句抓人钩子（悬念/反差/金手指），不剧透结局\n"
        "- 交代主角处境与核心冲突，点出题材卖点（重生/系统/爽点/杀伐果断等）\n"
        "- 网文风格、口语化、有节奏感；不要用『本书讲述了…』『主角是…』这类说明书句式\n"
        "- 结尾留一个勾子。直接输出简介正文，不要标题。"
    )
