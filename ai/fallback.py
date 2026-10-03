# -*- coding: utf-8 -*-
"""模型自动降级链（v3 P1）：所有调用一律从用户默认模型开始，不做任何角色固化。
某类调用在默认模型上连续异常（空正文/悬挂/接口报错）达到阈值时，本次调用自动
换用其他已配置模型重跑；下次调用仍从默认模型开始，选择权始终在用户手里。
降级记录持久化在 data/api_debug.log，让用户自己看清哪个模型更适合干什么。

线程约束：guard 在 worker 线程调用；配置快照只能由主线程 set_configs 灌入，
或由持有独立 DB 连接的线程（PipelineWorker.run）调 refresh 自取。"""
import threading

from ai import client as aiclient

THRESHOLD = 2            # 同类调用连续失败达到该次数才降级（首次失败先如实报错）

_lock = threading.Lock()
_configs = []            # 配置快照 [{name, provider, base_url, api_key, model}, ...]
_streaks = {}            # kind -> 默认模型连续失败次数（默认模型成功才清零）
_default_identity = None


class ModelFallbackError(RuntimeError):
    """Keep both the original failure and every attempted backup visible."""

    def __init__(self, failures):
        self.failures = failures
        lines = ['本步所有可用模型均失败：']
        for index, (name, error) in enumerate(failures):
            role = '默认模型' if index == 0 else '备用模型'
            lines.append(f'{role}「{name}」：{error}')
        super().__init__('\n\n'.join(lines))


def set_configs(rows):
    """主线程调用：灌入 ai_configs 快照（sqlite Row 或 dict 列表均可）。"""
    global _configs
    snap = []
    for r in rows:
        d = dict(r)
        if (d.get("base_url") or "").strip() and (d.get("model") or "").strip():
            snap.append(d)
    with _lock:
        _configs = snap


def refresh(db_path):
    """worker 线程自取快照（用自己的 DB 连接，不跨线程共享连接）。"""
    try:
        from db import DB
        db = DB(db_path)
        try:
            set_configs(db.get_ai_configs())
        finally:
            db.close()
    except Exception:  # noqa —— 拿不到配置就不降级，不影响主流程
        pass


def reset():
    """清空快照与连败计数（测试用）。"""
    global _default_identity
    with _lock:
        _configs.clear()
        _streaks.clear()
        _default_identity = None


def _same(a, b):
    a, b = dict(a), dict(b)
    return (a.get("id", a.get("name")) == b.get("id", b.get("name"))
            and a.get("base_url") == b.get("base_url")
            and a.get("model") == b.get("model"))


def note_failure(kind):
    with _lock:
        _streaks[kind] = _streaks.get(kind, 0) + 1
        return _streaks[kind]


def note_success(kind):
    with _lock:
        _streaks[kind] = 0


def guard(kind, cfg, run, on_switch=None, skip=(), threshold=THRESHOLD, configs=None):
    """一类调用的统一入口：run(cfg) -> result（run 内部自带的逐次重试照旧）。
    默认模型整链失败且该类调用连续失败达 THRESHOLD → 按配置顺序换其他模型
    重跑本次调用；全部备用也失败则抛最后一个错误。skip 中的异常（如用户停止）
    不计失败、不触发降级，原样上抛。"""
    global _default_identity
    cfg = dict(cfg)
    identity = (cfg.get("id", cfg.get("name")), cfg.get("base_url"), cfg.get("model"))
    with _lock:
        if identity != _default_identity:
            _streaks.clear()
            _default_identity = identity
    try:
        result = run(cfg)
        note_success(kind)
        return result
    except skip:
        raise
    except aiclient.CallCancelled:
        raise
    except Exception as e:  # noqa
        n = note_failure(kind)
        with _lock:
            alts = [dict(c) for c in (_configs if configs is None else configs)
                    if not _same(c, cfg)]
        # A response that used the whole output budget but still has no body
        # will not improve by retrying the same model with identical inputs.
        immediate = (isinstance(e, aiclient.OutputBudgetExhausted)
                     or isinstance(e, aiclient.ModelHTTPError) and not e.retryable)
        if (n < threshold and not immediate) or not alts:
            raise
        aiclient._log_api(
            f"FALLBACK kind={kind} 默认「{cfg.get('name')}」连续失败 {n} 次，"
            f"改用备用 {[c.get('name') for c in alts]}：{e}")
        failures = [(cfg.get('name', '?'), e)]
        for alt in alts:
            if on_switch:
                on_switch(f"  🔀 默认模型「{cfg.get('name')}」连续异常，"
                          f"已切换到「{alt.get('name')}」模型完成本步…")
            try:
                result = run(alt)
                aiclient._log_api(f"FALLBACK ok kind={kind} via {alt.get('name')}")
                return result
            except skip:
                raise
            except aiclient.CallCancelled:
                raise
            except Exception as e2:  # noqa
                failures.append((alt.get('name', '?'), e2))
        raise ModelFallbackError(failures) from failures[-1][1]
