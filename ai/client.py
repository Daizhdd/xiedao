# -*- coding: utf-8 -*-
"""AI 小说工作台 · 多模型客户端（OpenAI 兼容协议）
支持 provider: deepseek / openai_compat / ollama
用标准库 urllib，无额外依赖。
"""
import json
import os
import queue
import socket
import threading
import time
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime
from ai.transport import request_options

PROVIDER_HINTS = {
    "deepseek": "https://api.deepseek.com/v1",
    "openai_compat": "",
    "ollama": "http://localhost:11434/v1",
}

DNS_TIMEOUT = 5.0    # 域名解析死线：getaddrinfo 本身无超时，卡住时必须自己掐


def _log_api(msg):
    """模型调用日志（data/api_debug.log）：卡在哪一步一目了然"""
    try:
        from db import DB_PATH
        path = os.path.join(os.path.dirname(DB_PATH), "api_debug.log")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"[{datetime.now():%m-%d %H:%M:%S}] {msg}\n")
    except Exception:
        pass


_DNS_CACHE = {}          # host -> (ip, 时间戳)；热路径零 DNS 调用
_DNS_TTL = 600


class CallCancelled(InterruptedError):
    """调用方主动取消模型请求。"""


class OutputBudgetExhausted(RuntimeError):
    """模型耗尽本次输出额度后仍未给出正文。"""


class ModelHTTPError(RuntimeError):
    """Structured API rejection, shared by streaming and non-streaming calls."""

    def __init__(self, cfg, status_code, detail):
        self.status_code = status_code
        self.detail = detail
        self.retryable = status_code not in (400, 401, 402, 403, 404, 405, 421, 422)
        self.rejected = not self.retryable
        _provider, base, model = _config_parts(cfg)
        host = urllib.parse.urlparse(base).hostname or base
        reasons = {
            401: 'API Key 无效或认证失败，请在「配置模型」检查密钥。',
            402: '账户余额不足，请为此服务账户充值，或在「配置模型」选择可用模型后重试。',
            403: '服务拒绝访问，请检查账户权限或服务地区限制。',
        }
        reason = reasons.get(status_code, detail)
        super().__init__(f'模型「{_cfg_value(cfg, "name", "?")}」（{model}，{host}）'
                         f'接口报错 HTTP {status_code}：{reason}')


def _cfg_value(cfg, key, default=""):
    """读取 dict 或 sqlite3.Row 配置。"""
    try:
        value = cfg[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


def _config_parts(cfg):
    provider = _cfg_value(cfg, "provider", "")
    base = (_cfg_value(cfg, "base_url", "") or PROVIDER_HINTS.get(provider, "")).rstrip("/")
    if not base:
        raise RuntimeError("未配置 API 地址（base_url）")
    model = _cfg_value(cfg, "model", "") or (
        "deepseek-chat" if provider == "deepseek" else "qwen2.5")
    return provider, base, model


def is_direct_mimo(cfg):
    """Only use MiMo-specific request fields for Xiaomi's own API endpoint."""
    try:
        _provider, base, model = _config_parts(cfg)
    except RuntimeError:
        return False
    return (urllib.parse.urlparse(base).hostname == "api.xiaomimimo.com"
            and model.lower().startswith("mimo-"))


def _generation_options(cfg, max_tokens, thinking=None):
    if is_direct_mimo(cfg):
        options = {"max_completion_tokens": max_tokens}
        if thinking in ("enabled", "disabled"):
            options["thinking"] = {"type": thinking}
        return options
    return {"max_tokens": max_tokens}


def structured_output_options(cfg):
    """Only enable documented JSON mode for directly configured MiMo APIs."""
    supported = {'mimo-v2.6-flash', 'mimo-v2.6-pro', 'mimo-v2.6-pro-ultraspeed',
                 'mimo-v2.5-pro', 'mimo-v2.5'}
    return ({'response_format': {'type': 'json_object'}}
            if is_direct_mimo(cfg) and _cfg_value(cfg, 'model').lower() in supported else {})


def _run_with_deadline(fn, timeout, cancel_event=None):
    """在线程中执行阻塞网络调用，以 timeout 限定整次调用时长。

    urllib 的 timeout 只限制单次 socket 等待；服务端持续发送数据时可能一直不返回。
    守护线程让调用方仍能按总时限返回。取消只结束等待，不会强行终止底层 socket。
    """
    if timeout is None:
        return fn()
    timeout = max(0.0, float(timeout))
    if timeout == 0:
        _log_api("CALL timeout immediately (0s deadline)")
        raise TimeoutError("模型调用超过 0s 总时限，已放弃本次尝试")
    result = queue.Queue(maxsize=1)

    def _work():
        try:
            result.put((True, fn()))
        except BaseException as exc:  # 保留 KeyboardInterrupt 等回调异常
            result.put((False, exc))

    worker = threading.Thread(target=_work, daemon=True)
    worker.start()
    deadline = time.monotonic() + timeout
    while True:
        if cancel_event is not None and cancel_event.is_set():
            _log_api("CALL cancelled by caller")
            raise CallCancelled("模型调用已取消")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _log_api(f"CALL timeout after {timeout:g}s")
            raise TimeoutError(f"模型调用超过 {timeout:g}s 总时限，已放弃本次尝试")
        try:
            ok, value = result.get(timeout=min(0.1, remaining))
        except queue.Empty:
            continue
        if ok:
            return value
        raise value


def _response_text(message):
    """提取文本正文；拒绝格式正确但没有可用正文的响应。"""
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") for part in content
            if isinstance(part, dict) and part.get("type") == "text")
    if not isinstance(content, str) or not content.strip():
        _log_api("RESPONSE invalid: empty text content")
        raise RuntimeError("模型返回了空正文（推理可能超预算或服务端异常）")
    return content.strip()


def _usage_summary(data):
    usage = data.get("usage") if isinstance(data, dict) else None
    if not isinstance(usage, dict):
        return "usage unavailable"
    fields = (
        ("prompt", usage.get("prompt_tokens", usage.get("input_tokens"))),
        ("completion", usage.get("completion_tokens", usage.get("output_tokens"))),
        ("total", usage.get("total_tokens")),
    )
    values = [f"{name}={value}" for name, value in fields if value is not None]
    details = usage.get("completion_tokens_details") or {}
    reasoning = details.get("reasoning_tokens") if isinstance(details, dict) else None
    if isinstance(reasoning, int):
        values.append(f"reasoning={reasoning}")
        completion = usage.get("completion_tokens", usage.get("output_tokens"))
        if isinstance(completion, int):
            values.append(f"visible={max(0, completion - reasoning)}")
    return "usage " + (", ".join(values) if values else "unavailable")


def _make_request(cfg, url, body, stream=False):
    provider = _cfg_value(cfg, "provider", "")
    headers = {"Content-Type": "application/json"}
    if stream:
        headers["Accept"] = "text/event-stream"
    if provider != "ollama":
        headers["Authorization"] = f"Bearer {_cfg_value(cfg, 'api_key', '') or 'ollama'}"
    return urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")


def _resolve(host, timeout=DNS_TIMEOUT):
    """带死线的域名解析 + 缓存。getaddrinfo 卡住（DNS/网络异常）时会无限阻塞
    甚至锁死 GIL，所以：缓存命中直接返回；未命中放守护线程里掐表。"""
    ip, ts = _DNS_CACHE.get(host, (None, 0))
    if ip and time.time() - ts < _DNS_TTL:
        return ip
    out = {}

    def _do():
        try:
            # getaddrinfo 会释放 GIL（gethostbyname 不会——DNS 卡住时会把
            # 整个进程锁死，一切超时全部失效），必须用它死线才有意义
            info = socket.getaddrinfo(host, None)
            out["ip"] = info[0][4][0] if info else ""
        except Exception as e:  # noqa
            out["err"] = e

    th = threading.Thread(target=_do, daemon=True)
    th.start()
    th.join(timeout)
    if "ip" in out:
        _DNS_CACHE[host] = (out["ip"], time.time())
        return out["ip"]
    if "err" in out:
        raise RuntimeError(f"域名解析失败（{host}）：{out['err']}")
    raise RuntimeError(
        f"域名解析超时（{host}，{timeout:g}s）：DNS/网络异常。"
        "可点「不等了」后重试，或检查网络后重启软件。")


def chat_once(cfg, messages, temperature=0.8, max_tokens=4096, timeout=180,
              cancel_event=None, thinking=None, budget=None, response_format=None):
    """cfg: ai_configs 行（dict-like）；messages: [{role, content}, ...]
    返回生成文本。失败抛异常（带中文信息）。
    """
    _provider, _base, model = _config_parts(cfg)
    reserved = budget.reserve(max_tokens) if budget is not None else max_tokens

    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }
    body.update(_generation_options(cfg, reserved, thinking))
    if response_format is not None:
        body['response_format'] = response_format

    def _call():
        data = None
        rejected = False
        try:
            data = _post(cfg, body, timeout)
        except ModelHTTPError as exc:
            rejected = exc.rejected
            raise
        finally:
            if budget is not None:
                budget.finish(data, reserved, rejected=rejected)
        try:
            message = data["choices"][0]["message"]
            if not isinstance(message, dict):
                raise TypeError("message 不是对象")
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(
                f"模型返回格式异常：{json.dumps(data, ensure_ascii=False)[:300]}") from e
        try:
            return _response_text(message)
        except RuntimeError as exc:
            usage = data.get("usage") if isinstance(data, dict) else None
            used = (usage or {}).get("completion_tokens") if isinstance(usage, dict) else None
            finish_reason = (data.get("choices") or [{}])[0].get("finish_reason")
            if ((isinstance(used, (int, float)) and used >= reserved)
                    or finish_reason == "length"):
                _log_api(f"RESPONSE budget exhausted: completion={used}, cap={max_tokens}")
                raise OutputBudgetExhausted(
                    f"模型已耗尽 {reserved} token 输出额度但未返回正文") from exc
            raise

    return _run_with_deadline(_call, timeout, cancel_event)


def chat_stream(cfg, messages, temperature=0.8, max_tokens=4096, timeout=300,
                on_delta=None, cancel_event=None, thinking=None):
    """v1.8 流式生成：SSE 逐段回调 on_delta(增量文本)，最终返回完整文本。
    适用于所有 OpenAI 兼容端点（deepseek / openai_compat / ollama）。"""
    _provider, base, model = _config_parts(cfg)
    url = base + "/chat/completions"

    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
    }
    body.update(_generation_options(cfg, max_tokens, thinking))
    req = _make_request(cfg, url, body, stream=True)

    def _call():
        host = urllib.parse.urlparse(url).hostname or ""
        tag = f"{_cfg_value(cfg, 'name', '?')} {model} -> {host}"
        started = time.monotonic()
        _log_api(f"POST start {tag} (stream)")
        full = []
        usage = None
        try:
            _resolve(host)
            with urllib.request.urlopen(req, timeout=timeout, **request_options(req)) as resp:
                for raw in resp:
                    line = raw.decode("utf-8", "ignore").strip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        obj = json.loads(payload)
                    except ValueError:
                        continue
                    if isinstance(obj.get("usage"), dict):
                        usage = obj["usage"]
                    choices = obj.get("choices") or [{}]
                    delta = (choices[0].get("delta") or {}) if choices else {}
                    piece = delta.get("content") or ""
                    if piece:
                        full.append(piece)
                        if on_delta:
                            on_delta(piece)
            text = "".join(full)
            if not text.strip():
                raise RuntimeError("模型返回了空正文（推理可能超预算或服务端异常）")
            _log_api(f"POST ok    {tag} in {time.monotonic() - started:.1f}s | "
                     f"{_usage_summary({'usage': usage})} | resp: {text[:120]!r}")
            return text
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")[:300]
            _log_api(f"POST http{e.code} {tag} in {time.monotonic() - started:.1f}s: {detail[:120]}")
            raise ModelHTTPError(cfg, e.code, detail) from e
        except urllib.error.URLError as e:
            _log_api(f"POST urle  {tag} in {time.monotonic() - started:.1f}s: {e.reason}")
            raise RuntimeError(
                f"无法连接模型服务（{_cfg_value(cfg, 'name', '?')}）：{e.reason}。检查 base_url 与网络") from e
        except Exception as e:
            _log_api(f"POST err   {tag} in {time.monotonic() - started:.1f}s: "
                     f"{type(e).__name__} {e}")
            raise

    return _run_with_deadline(_call, timeout, cancel_event)


def simple_chat(cfg, system, user, **kw):
    """便捷封装：一条 system + 一条 user"""
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": user})
    return chat_once(cfg, msgs, **kw)


def _post(cfg, body, timeout):
    """公共请求体发送：返回解析后的 JSON。带 DNS 死线与调用日志。"""
    _provider, base, _model = _config_parts(cfg)
    url = base + "/chat/completions"
    host = urllib.parse.urlparse(url).hostname or ""
    tag = f"{_cfg_value(cfg, 'name', '?')} {body.get('model', '?')} -> {host}"
    t0 = time.monotonic()
    cap = body.get("max_completion_tokens", body.get("max_tokens"))
    thinking = (body.get("thinking") or {}).get("type", "default")
    _log_api(f"POST start {tag} cap={cap} thinking={thinking}")
    try:
        _resolve(host)                   # 解析卡住 = 秒级失败，不悬死
        req = _make_request(cfg, url, body)
        with urllib.request.urlopen(req, timeout=timeout, **request_options(req)) as resp:
            try:
                data = json.loads(resp.read().decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as e:
                raise RuntimeError("模型响应不是有效 JSON") from e
        try:
            _peek = data["choices"][0]["message"].get("content") or ""
        except Exception:  # noqa
            _peek = str(data)[:120]
        _log_api(f"POST ok    {tag} in {time.monotonic() - t0:.1f}s | "
                 f"{_usage_summary(data)} | resp: {_peek[:120]!r}")
        return data
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:300]
        _log_api(f"POST http{e.code} {tag} in {time.monotonic() - t0:.1f}s: {detail[:120]}")
        raise ModelHTTPError(cfg, e.code, detail) from e
    except urllib.error.URLError as e:
        _log_api(f"POST urle  {tag} in {time.monotonic() - t0:.1f}s: {e.reason}")
        raise RuntimeError(
            f"无法连接模型服务（{_cfg_value(cfg, 'name', '?')}）：{e.reason}。检查 base_url 与网络") from e
    except Exception as e:  # noqa
        _log_api(f"POST err   {tag} in {time.monotonic() - t0:.1f}s: {type(e).__name__} {e}")
        raise


def chat_once_tools(cfg, messages, tools_spec, temperature=0.3, max_tokens=2048,
                    timeout=180, cancel_event=None, thinking=None, budget=None):
    """单轮工具调用：返回助手消息 dict {content, tool_calls}（不执行工具）。
    tools_spec: OpenAI 格式 [{"type":"function","function":{...}}]。
    模型不支持工具时抛 RuntimeError，由调用方决定降级。"""
    _provider, _base, model = _config_parts(cfg)
    reserved = budget.reserve(max_tokens) if budget is not None else max_tokens
    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
        "tools": tools_spec,
        "tool_choice": "auto",
    }
    body.update(_generation_options(cfg, reserved, thinking))
    def _call():
        data = None
        rejected = False
        try:
            data = _post(cfg, body, timeout)
        except ModelHTTPError as exc:
            rejected = exc.rejected
            raise
        finally:
            if budget is not None:
                budget.finish(data, reserved, rejected=rejected)
        try:
            msg = data["choices"][0]["message"]
            if not isinstance(msg, dict):
                raise TypeError("message 不是对象")
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(
                f"模型返回格式异常：{json.dumps(data, ensure_ascii=False)[:300]}") from e
        calls = msg.get("tool_calls") or []
        content = msg.get("content")
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") for part in content
                if isinstance(part, dict) and part.get("type") == "text")
        if content is None:
            content = ""
        if not isinstance(content, str):
            raise RuntimeError("模型返回格式异常：message.content 不是文本")
        content = content.strip()
        if not content and not calls:
            _log_api("RESPONSE invalid: empty content and no tool_calls")
            raise RuntimeError("模型返回了空正文且未包含工具调用")
        return {"content": content, "tool_calls": calls}

    return _run_with_deadline(_call, timeout, cancel_event)


if __name__ == "__main__":
    # 冒烟：不实际联网，仅验证模块可导入
    print("ai.client OK")
