"""
共用 LLM client（透過 OpenRouter，支援 Anthropic prompt caching）。

關鍵設計：
- 直接用 httpx 呼叫 OpenRouter Chat Completions API，繞過 OpenAI SDK 的 pydantic
  strict typing，確保 `cache_control` 欄位不會在序列化時被丟掉。
- system 拆成「指示（不快取）+ vault（快取）」兩個 text block。
- 5 分鐘內連續打同一個 vault，第二題開始的 vault tokens 就走 cache_read 計費（10% 價）。

OpenRouter 對 Anthropic 模型的回應，會把 cache 資訊放進 OpenAI 格式的
`usage.prompt_tokens_details.cached_tokens`。
"""
from __future__ import annotations

import os
import sys
from typing import Any

import httpx


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_TIMEOUT = 120.0


def make_client() -> httpx.Client:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        print("OPENROUTER_API_KEY 未設定 — 請執行: . ~/.iask_openrouter_key", file=sys.stderr)
        sys.exit(2)
    return httpx.Client(
        timeout=DEFAULT_TIMEOUT,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://iask.pfl.internal",
            "X-Title": "iASK 2.0 PoC",
        },
    )


def build_system_blocks(instructions: str, vault_text: str) -> list[dict[str, Any]]:
    """system 拆兩塊：指示（不快取）+ vault（快取）。"""
    return [
        {"type": "text", "text": instructions},
        {
            "type": "text",
            "text": "<FAQ_KNOWLEDGE_BASE>\n" + vault_text + "\n</FAQ_KNOWLEDGE_BASE>",
            "cache_control": {"type": "ephemeral"},
        },
    ]


def call(
    client: httpx.Client,
    model: str,
    system_blocks: list[dict],
    history: list[dict],
    max_tokens: int = 2048,
    temperature: float | None = None,
) -> tuple[str, dict]:
    """
    呼叫 OpenRouter。回傳 (answer_text, usage_dict)。
    usage_dict 包含：input, output, cached_input, total
      - input：本次 fresh input（題目 + 不被快取的部分）
      - cached_input：命中 cache 的 token 數
      - 兩者之和 = OpenAI 格式的 prompt_tokens

    temperature=None 表示不送 temperature 欄位、由 model 自己用 default
    （Gemini 預設 ~1.0；Anthropic 預設 1.0）。Caller 可指定數值覆寫。
    """
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system_blocks}, *history],
        # 要求 OpenRouter 回傳 detailed usage（包含 cache info）
        "usage": {"include": True},
    }
    if temperature is not None:
        payload["temperature"] = temperature

    resp = client.post(OPENROUTER_URL, json=payload)
    resp.raise_for_status()
    data = resp.json()

    choice = data["choices"][0]
    answer = (choice.get("message") or {}).get("content") or ""

    usage = data.get("usage") or {}
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
    fresh_input = max(0, prompt_tokens - cached)

    return answer, {
        "input": fresh_input,
        "cached_input": cached,
        "output": completion_tokens,
        "total": prompt_tokens + completion_tokens,
    }


def estimate_cost_usd(model: str, usage: dict) -> float:
    """
    粗估這次呼叫的 USD 成本。
    Anthropic prompt caching：cache_read 約為標準 input 的 10%、cache_write 約 125%。
    OpenRouter 對 Anthropic 模型沿用相同比例。
    """
    # OpenRouter 上的標準價（2026-05；如有更動請更新）
    pricing = {
        "anthropic/claude-haiku-4.5": (1.0, 5.0),
        "anthropic/claude-sonnet-4.6": (3.0, 15.0),
        "anthropic/claude-opus-4.7": (5.0, 25.0),
        "google/gemini-2.0-flash-001": (0.10, 0.40),
        "google/gemini-2.0-flash-lite-001": (0.075, 0.30),
        "google/gemini-2.5-flash-lite": (0.10, 0.40),
        "google/gemini-2.5-flash": (0.30, 2.50),
        "google/gemini-3.1-flash-lite": (0.25, 1.50),
        "google/gemini-3-flash-preview": (0.50, 3.00),
        "google/gemini-3.5-flash": (1.50, 9.00),
    }
    p_in, p_out = pricing.get(model, (3.0, 15.0))
    # 簡化：假設 cached 全部是 cache_read（已寫入過）。如果是第一次（cache_write）會
    # 多 25% 寫入溢價，但這裡用估算值簡化。
    cost = (
        usage["input"] * p_in / 1_000_000
        + usage["cached_input"] * (p_in * 0.1) / 1_000_000
        + usage["output"] * p_out / 1_000_000
    )
    return cost
