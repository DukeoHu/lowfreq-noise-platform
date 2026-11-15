"""大模型客户端：兼容 OpenAI Chat Completions 协议的云厂商 / 内网网关。

不依赖厂商 SDK，只用 httpx 调 `/chat/completions`，通过环境变量切换模型服务。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import httpx

from app.services import llm_config

logger = logging.getLogger(__name__)

_RETRY_STATUS = {429, 500, 502, 503, 504}
# 输出被截断时逐级放大预算的上限；推理模型的思考 tokens 也计入 max_tokens
_MAX_TOKEN_CAP = 8192

_usage_state = threading.local()


@contextmanager
def capture_usage() -> Iterator[dict]:
    """在当前线程内累计模型调用的 usage（含截断重试的多次请求）。

    通过线程本地变量而不是修改 chat_json 返回值，保持既有调用与测试桩兼容。
    """
    previous = getattr(_usage_state, "sink", None)
    sink = {
        "requests": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "reasoning_tokens": 0,
    }
    _usage_state.sink = sink
    try:
        yield sink
    finally:
        _usage_state.sink = previous


def _record_usage(usage: dict) -> None:
    sink = getattr(_usage_state, "sink", None)
    if sink is None or not isinstance(usage, dict):
        return
    sink["requests"] += 1
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, (int, float)):
            sink[key] += int(value)
    reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
    if isinstance(reasoning, (int, float)):
        sink["reasoning_tokens"] += int(reasoning)


class LLMError(RuntimeError):
    """大模型调用或返回解析失败。"""


def get_config():
    """读取当前生效配置（数据库优先，其次环境变量）。"""
    return llm_config.load_runtime_config()


def is_configured() -> bool:
    return get_config().configured


def _extract_json(text: str) -> dict:
    """从模型输出中提取 JSON，兼容 ```json 代码块与前后多余文字。"""
    payload = text.strip()
    if payload.startswith("```"):
        payload = payload.strip("`")
        if payload.lower().startswith("json"):
            payload = payload[4:]
    start = payload.find("{")
    if start == -1:
        raise LLMError("模型未返回 JSON 对象")
    end = payload.rfind("}")
    if end <= start:
        raise LLMError("模型返回的 JSON 不完整（疑似被截断）")
    try:
        parsed = json.loads(payload[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMError(f"模型返回的 JSON 无法解析：{exc}") from exc
    if not isinstance(parsed, dict):
        raise LLMError("模型返回的 JSON 不是对象")
    return parsed


def _token_budgets(initial: int) -> list[int]:
    """按配置值逐级翻倍，直到上限；用于输出被截断时自动加预算重试。"""
    budgets: list[int] = []
    budget = max(int(initial), 64)
    while True:
        budgets.append(budget)
        if budget >= _MAX_TOKEN_CAP:
            return budgets
        budget = min(budget * 2, _MAX_TOKEN_CAP)


def _request(config, system: str, user: str, max_tokens: int) -> tuple[str, str | None, dict]:
    """发起一次 chat/completions 请求，返回 (正文, finish_reason, usage)。"""
    url = config.base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
        "max_tokens": max_tokens,
        "stream": False,
    }

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with httpx.Client(timeout=config.timeout_s) as client:
                response = client.post(url, json=payload, headers=headers)
            if response.status_code in _RETRY_STATUS:
                raise LLMError(f"模型服务暂时不可用（HTTP {response.status_code}）")
            if response.status_code >= 400:
                raise LLMError(f"模型调用失败（HTTP {response.status_code}）：{response.text[:300]}")
            data = response.json()
            choice = data["choices"][0]
            content = choice["message"].get("content") or ""
            return content, choice.get("finish_reason"), data.get("usage") or {}
        except (httpx.HTTPError, KeyError, IndexError, ValueError, LLMError) as exc:
            last_error = exc
            retryable = isinstance(exc, httpx.HTTPError) or (
                isinstance(exc, LLMError) and "暂时不可用" in str(exc)
            )
            if retryable and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            break
        finally:
            logger.debug("llm request finished (attempt %d)", attempt + 1)

    raise LLMError(str(last_error) if last_error else "模型调用失败")


def chat_json(system: str, user: str) -> dict:
    """调用模型并返回解析后的 JSON 对象；失败抛出 LLMError。

    推理模型的思考 tokens 也计入 max_tokens，预算不足时输出会被截断，
    因此遇到 finish_reason=length 会按 2 倍逐级抬高预算重试。
    """
    config = get_config()
    if not config.configured:
        raise LLMError("未配置大模型，请在设置页填写，或设置 LOWFREQ_LLM_* 环境变量")

    truncated: list[str] = []
    for budget in _token_budgets(config.max_tokens):
        content, finish_reason, usage = _request(config, system, user, budget)
        _record_usage(usage)
        if finish_reason == "length":
            reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
            truncated.append(f"{budget}（推理 {reasoning}）")
            logger.warning("llm output truncated at %s tokens, reasoning=%s", budget, reasoning)
            continue
        if not content.strip():
            raise LLMError(
                f"模型返回空内容（finish_reason={finish_reason}），请确认模型支持文本输出且额度充足"
            )
        try:
            return _extract_json(content)
        except LLMError as exc:
            raise LLMError(f"{exc}；模型输出片段：{content.strip()[:200]}") from exc

    raise LLMError(
        "模型输出被截断，已尝试最大输出 " + "、".join(truncated)
        + " tokens。推理模型会把思考计入该上限，请在设置页调大「最大输出 tokens」，"
        "或改用非推理模型（如 deepseek-chat）"
    )


def test_connection() -> str:
    """用最小请求验证配置是否可用，返回模型回复片段。"""
    result = chat_json(
        "你是一个连通性测试助手，只输出 JSON。",
        '请原样返回 {"ok": true}',
    )
    return json.dumps(result, ensure_ascii=False)[:200]
