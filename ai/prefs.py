# -*- coding: utf-8 -*-
"""写作偏好（交接建议项 2）：MiMo 输出预算与深度思考模式的用户设置。

历史：这两个值曾写死在 ai/pipeline.py（MIMO_WRITER_TOKENS=16384；正文、
章节摘要、卷摘要关思考）。现在持久化到 app_settings 表，配置入口在
ui/dialogs.ConfigDialog「写作偏好」。作用域与旧的写死逻辑一致：仅对
直连 MiMo（api.xiaomimimo.com + mimo-*）的请求生效，其他模型仍用
max_tokens，不发送任何 MiMo 私有参数。
"""
import os

KEY_BUDGET = "mimo_writer_budget"     # ""=自动(16384)；或 1024..MAX_BUDGET 的整数
KEY_THINKING = "mimo_thinking_mode"   # auto / always_off / always_on

AUTO_BUDGET = 16384
MIN_BUDGET = 1024
MAX_BUDGET = 65536

THINK_MODES = ("auto", "always_off", "always_on")

_DEFAULTS = {"budget": None, "thinking": "auto"}
DEFAULT_PREFS = dict(_DEFAULTS)


def load(db_path):
    """读写作偏好；任何异常都回落默认值（流水线 worker 线程容错）。
    budget=None 表示「自动」。"""
    budget_raw = think_raw = ""
    try:
        from db import DB
        if not db_path or not os.path.exists(db_path):
            return dict(_DEFAULTS)
        db = DB(db_path)
        try:
            return load_from_db(db)
        finally:
            db.close()
    except Exception:  # noqa —— 读不到设置不影响写作主流程
        pass
    return {"budget": _parse_budget(budget_raw),
            "thinking": think_raw if think_raw in THINK_MODES else "auto"}


def load_from_db(db):
    """Capture preferences using the task owner's existing connection."""
    budget_raw = (db.get_setting(KEY_BUDGET) or '').strip()
    think_raw = (db.get_setting(KEY_THINKING) or '').strip()
    return {'budget': _parse_budget(budget_raw),
            'thinking': think_raw if think_raw in THINK_MODES else 'auto'}


def _parse_budget(raw):
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if MIN_BUDGET <= value <= MAX_BUDGET else None


def writer_budget(prefs):
    """MiMo 正文写作的单次输出预算（token）。"""
    return prefs.get("budget") or AUTO_BUDGET


def thinking_for(prefs, role):
    """按角色解析 thinking 参数（客户端只会把它发给直连 MiMo 的请求）。
    role: writer / planner / summary / rollup / reviewer / other
      auto       = 正文、大纲规划、摘要、卷摘要、审稿关思考
      always_off = 一律关思考
      always_on  = 一律保持模型默认（思考会占用输出额度）
    """
    mode = prefs.get("thinking", "auto") if prefs else "auto"
    if mode == "always_off":
        return "disabled"
    if mode == "always_on":
        return None
    return "disabled" if role in ("writer", "planner", "summary", "rollup", "reviewer") \
        else None
