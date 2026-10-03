# -*- coding: utf-8 -*-
"""审稿 agent：对照写稿用的上下文包自审，返回（分数, 问题清单）。
不合格由流水线带问题清单重写并复审。缺少可核查证据的输出记为未审。"""
import json

from ai import client as aiclient
from ai import prompts
from ai import evidence_review
from ai import story_memory

TEXT_CLIP = 12000


def parse_verdict(text):
    """从模型输出中提取 {"score": int, "issues": [...]}。
    返回 (score:int|None, issues:list[str])；解析不出返回 (None, [])。"""
    if not text:
        return None, []
    s = text.strip()
    if s.startswith("```"):                      # 剥掉 markdown 代码块
        s = s.strip("`")
        if s.startswith(("json\n", "JSON\n")):
            s = s[4:]
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end <= start:
        return None, []
    try:
        obj = json.loads(s[start:end + 1])
    except ValueError:
        return None, []
    score = obj.get("score")
    try:
        score = max(0, min(100, int(score)))
    except (TypeError, ValueError):
        return None, []
    issues = [str(x) for x in (obj.get("issues") or []) if str(x).strip()]
    return score, issues


def review_chapter(cfg, write_prompt, chapter_text, log=None, thinking="disabled",
                   sources=None, on_structured=None, budget=None,
                   cancel_event=None):
    """审一章。write_prompt: 写稿时用的 user 消息（含上下文包与章纲），
    审稿依据与写稿依据同源，保证『审的就是写的依据』。
    thinking 由调用方按写作偏好传入（默认关思考）。
    返回 (score|None, issues)。"""
    user = (f"{write_prompt}\n\n【正文】\n{chapter_text[:TEXT_CLIP]}\n\n"
            "请审稿并输出 JSON。")
    if sources is not None:
        refs = '\n'.join(f'- {key}：{value[:450]}' for key, value in sources.items())
        user += ('\n【可核查依据】\n' + (refs or '（暂无）')
                 + '\n请使用分项分数、正文逐字引文及依据编号的结构化 JSON；'
                   '客观冲突缺少可核查依据时只作主观建议。')
    extra = {'budget': budget} if budget is not None else {}
    extra.update(aiclient.structured_output_options(cfg))
    raw = aiclient.chat_once(cfg, [
        {"role": "system", "content": prompts.SYSTEM_REVIEWER},
        {"role": "user", "content": user},
    ], temperature=0.2, max_tokens=2048, timeout=300,
       thinking=thinking, cancel_event=cancel_event, **extra)
    if sources is not None:
        try:
            detail = evidence_review.parse_report(raw, chapter_text, sources)
            if on_structured:
                on_structured(story_memory.body_hash(chapter_text), raw, detail)
            return detail['score'], [x['explanation'] for x in detail['issues']]
        except ValueError as exc:
            if log:
                log(f'  ⚠ 审稿缺少可核查证据或格式不符（{exc}），本章按未审处理')
            return None, []
    score, issues = parse_verdict(raw)
    if score is None and log:
        log("  ⚠ 审稿输出无法解析（本章将按未审稿标注）")
    return score, issues


def rewrite_prompt(write_prompt, chapter_text, issues):
    """带问题清单的重写 prompt：依据不变，附加问题与上一稿"""
    return (f"{write_prompt}\n\n"
            "【上一稿存在的问题（重写必须全部解决，其余要求不变）】\n"
            + "".join(f"- {x}\n" for x in issues)
            + f"\n【上一稿正文（可保留可用部分，问题处必须重写）】\n{chapter_text[:5000]}")
