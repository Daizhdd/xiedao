"""Model calls, cancellation, retries, and per-step budget accounting."""
import hashlib
import threading
import time
import task_ledger as ledger
from ai import client as aiclient, prompts, prefs as aprefs, fallback as fb, story_memory
from ai.usage_budget import BudgetPaused
from ai.task_errors import Stopped

def _thinking_role(system):
    """system 角色在写作偏好里的归类（thinking_for 按它决定思考开关）"""
    return {prompts.SYSTEM_WRITER: "writer",
            prompts.SYSTEM_ASSIST: "planner",
            prompts.SYSTEM_SUMMARY: "summary",
            prompts.SYSTEM_ROLLUP: "rollup"}.get(system, "other")

def _kind_of(system):
    """按 system 角色给调用分类（降级链按类统计连败）"""
    return {prompts.SYSTEM_WRITER: "写作正文",
            prompts.SYSTEM_ASSIST: "草案生成",
            prompts.SYSTEM_SUMMARY: "章节摘要",
            prompts.SYSTEM_ROLLUP: "卷滚动摘要"}.get(system, "生成")

def call(self, system, user, temperature=0.8, retries=2):
    """带硬性死线的模型调用 + 自动降级链：chat_once 放进守护线程执行，本线程
    只等队列。底层任何冻结（DNS/GIL/服务端悬挂）都无法拖死流水线——超时即
    放弃该次尝试并重试；默认模型整链失败且该类调用连续异常达阈值时，自动换用
    其他已配置模型重跑本步（活动卡标注），下次调用仍从默认模型开始。"""
    kind = _kind_of(system)
    stage = '故事事实提取' if system == story_memory.SYSTEM_EXTRACT else kind
    active_db = self._active_db
    fingerprint = hashlib.sha256(user.encode('utf-8')).hexdigest()
    if active_db is not None and self.task_run_id:
        ledger.record_phase(active_db, self.task_run_id, self._active_chapter,
                            kind, 'running', input_hash=fingerprint)
        self.progress.emit(f'  ⏳ 当前步骤：{stage}')

    def _run(c):
        return self._call_chain(c, system, user, temperature,
                                min(retries, self._budget.retry_limit)
                                if self._budget is not None else retries)

    # _call_chain has already retried the default model. One exhausted
    # chain is enough to try an available backup for this writing step.
    try:
        result = fb.guard(kind, self.cfg, _run,
                          on_switch=self.progress.emit,
                          skip=(Stopped, BudgetPaused,), threshold=1,
                          configs=self._fallback_configs)
        if active_db is not None and self.task_run_id:
            ledger.record_phase(active_db, self.task_run_id, self._active_chapter,
                                kind, 'completed', input_hash=fingerprint,
                                output_ref=hashlib.sha256((result or '').encode('utf-8')).hexdigest())
        return result
    except Exception as exc:
        if active_db is not None and self.task_run_id:
            ledger.record_phase(active_db, self.task_run_id, self._active_chapter,
                                kind, 'paused' if isinstance(exc, BudgetPaused) else 'failed',
                                input_hash=fingerprint, error_kind=type(exc).__name__)
        raise
    finally:
        if active_db is not None and self.task_run_id and self._budget is not None:
            snapshot = self._budget.snapshot()
            self._save_usage(active_db)
            self.progress.emit(
                f"模型用量：调用 {snapshot['calls']} 次 · 已知输出 "
                f"{snapshot['completion_tokens']} token · "
                f"未知 {snapshot['unknown_calls']} 次")

def call_chain(self, cfg, system, user, temperature, retries, *, call_timeout, max_tokens):
    """原重试链，cfg 参数化（降级时整链换模型重跑）。
    MiMo 直连的正文预算与思考开关由「写作偏好」控制（ai.prefs），
    其他模型始终使用 max_tokens，不附加 MiMo 私有参数。"""
    import queue as _queue
    direct_mimo = aiclient.is_direct_mimo(cfg)
    token_limit = (aprefs.writer_budget(self._prefs)
                   if direct_mimo and system == prompts.SYSTEM_WRITER
                   else max_tokens)
    thinking = aprefs.thinking_for(self._prefs, _thinking_role(system))

    def _attempt(q):
        try:
            extra = {'budget': self._budget} if self._budget is not None else {}
            if system == story_memory.SYSTEM_EXTRACT:
                extra.update(aiclient.structured_output_options(cfg))
            q.put(aiclient.chat_once(cfg, [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ], temperature=temperature, max_tokens=token_limit,
                timeout=call_timeout, cancel_event=self._cancel_event,
                thinking=thinking, **extra))
        except Stopped:
            q.put(Stopped())
        except Exception as e:  # noqa
            q.put(e)

    last = None
    for attempt in range(retries + 1):
        if self._stop:
            raise Stopped()
        # 心跳：让活动卡在模型生成的几十秒里也有动静，不至于像卡死
        self.progress.emit(
            f"  ⏳ 调用模型中（第 {attempt + 1}/{retries + 1} 次，"
            f"最长等 {call_timeout + 10}s）…")
        q = _queue.Queue()
        th = threading.Thread(target=_attempt, args=(q,), daemon=True)
        th.start()
        deadline = time.monotonic() + call_timeout + 10
        timed_out = False
        while True:
            if self._stop:
                raise Stopped()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                last = RuntimeError(
                    f"调用超过 {call_timeout + 10}s 无响应（网络/服务端冻结），已放弃本次尝试")
                self.progress.emit(f"  ⚠ {last}，立即尝试备用模型")
                timed_out = True
                break
            try:
                result = q.get(timeout=min(0.1, remaining))
                break
            except _queue.Empty:
                continue
        if timed_out:
            raise last
        if isinstance(result, Stopped):
            raise result
        if isinstance(result, BudgetPaused):
            raise result
        if isinstance(result, aiclient.CallCancelled) and self._stop:
            raise Stopped()
        if isinstance(result, aiclient.OutputBudgetExhausted):
            self.progress.emit("  ⚠ 模型耗尽输出额度却未给正文，立即尝试备用模型")
            raise result
        if isinstance(result, aiclient.ModelHTTPError) and not result.retryable:
            self.progress.emit(f"  ⚠ {result}；不再重复请求此模型")
            raise result
        if isinstance(result, TimeoutError):
            self.progress.emit("  ⚠ 模型调用已达 180 秒总时限，立即尝试备用模型")
            raise result
        if isinstance(result, Exception):
            last = result
            if "空正文" in str(result) or "返回了空" in str(result):
                self.progress.emit("  ⚠ 模型返回空正文（推理超预算或拥堵），重试…")
            elif attempt < retries:
                self.progress.emit(f"  ⚠ 调用失败（{attempt + 1}/{retries + 1}）：{result}，重试…")
            continue
        return result
    raise last
