# -*- coding: utf-8 -*-
"""Thread-safe per-task request budget; unknown usage consumes reservation."""
import threading


class BudgetPaused(RuntimeError):
    pass


class UsageBudget:
    def __init__(self, max_calls=0, output_cap=0, retry_limit=2, *, initial_usage=None):
        def _number(value, default=0):
            try:
                return max(0, int(value))
            except (TypeError, ValueError):
                return default
        self.max_calls = _number(max_calls)
        self.output_cap = _number(output_cap)
        self.retry_limit = _number(retry_limit, 2)
        self._lock = threading.Lock()
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.reasoning_tokens = 0
        self.unknown_calls = 0
        self.pending_calls = 0
        self.reserved_completion = 0
        for name in ('calls', 'prompt_tokens', 'completion_tokens', 'reasoning_tokens',
                     'unknown_calls', 'reserved_completion'):
            setattr(self, name, _number((initial_usage or {}).get(name, 0)))

    def snapshot_since(self, initial_usage):
        """Persist this attempt's delta while enforcing the whole task's limits."""
        snapshot = self.snapshot()
        return {name: max(0, value - (initial_usage or {}).get(name, 0))
                for name, value in snapshot.items()}

    def reserve(self, requested):
        with self._lock:
            if self.max_calls and self.calls >= self.max_calls:
                raise BudgetPaused('已达到本任务最大模型调用次数')
            amount = max(1, int(requested))
            if self.output_cap:
                remaining = self.output_cap - self.reserved_completion
                if remaining < 256:
                    raise BudgetPaused('本任务输出 token 预算不足，已暂停')
                amount = min(amount, remaining)
            self.calls += 1
            self.pending_calls += 1
            self.reserved_completion += amount
            return amount

    def finish(self, response, reserved, *, rejected=False):
        if rejected:
            # An explicit permanent HTTP rejection did not generate tokens.
            # Keep the request count, but release its output reservation.
            with self._lock:
                self.pending_calls = max(0, self.pending_calls - 1)
                self.reserved_completion = max(0, self.reserved_completion - reserved)
            return
        usage = response.get('usage') if isinstance(response, dict) else None
        if not isinstance(usage, dict):
            usage = {}
        prompt = usage.get('prompt_tokens', usage.get('input_tokens'))
        completion = usage.get('completion_tokens', usage.get('output_tokens'))
        details = usage.get('completion_tokens_details') or {}
        reasoning = details.get('reasoning_tokens') if isinstance(details, dict) else None
        with self._lock:
            self.pending_calls = max(0, self.pending_calls - 1)
            if isinstance(prompt, int) and prompt >= 0:
                self.prompt_tokens += prompt
            if isinstance(completion, int) and completion >= 0:
                self.completion_tokens += completion
                self.reserved_completion += completion - reserved
            else:
                self.unknown_calls += 1
            if isinstance(reasoning, int) and reasoning >= 0:
                self.reasoning_tokens += reasoning

    def snapshot(self):
        with self._lock:
            result = {name: getattr(self, name) for name in
                    ('calls', 'prompt_tokens', 'completion_tokens',
                     'reasoning_tokens', 'unknown_calls', 'reserved_completion')}
            result['unknown_calls'] += self.pending_calls
            return result
