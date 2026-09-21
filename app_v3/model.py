"""模型调用：真实流式（DeepSeek）+ 离线 stub

旧版 `app/llm.py` 是**非流式**的（一次拿整段 + 工具调用），所以界面只能等整段出来。
这里只要一件事：给一段 messages，把回复**一个字一个字**吐出来。

离线口径：`FAXIAOZHI_OFFLINE=1` 或 `MODEL_PROVIDER=stub` 时走 stub，不联网、不花钱，
用于开发与回归；stub 的输出必须能被界面明确标成示例，不能看起来像真实回答。
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from typing import Any

STUB_REPLY = (
    "（离线示例回答）这属于房屋买卖与租赁的关系问题。一般来说，租赁关系在先、买卖在后时，"
    "承租人仍然可以按原合同继续居住，新房东不能仅凭取得房屋所有权就要求你立刻搬走。\n\n"
    "具体能不能继续住、住到什么时候，取决于你的合同期限、是否办理了租赁登记，以及新房东买房时是否知情。\n\n"
    "你现在的租赁合同还有多久到期？有没有书面的合同或转账记录？这两点会直接影响怎么处理。"
)


def offline() -> bool:
    flag = str(os.environ.get("FAXIAOZHI_OFFLINE", "")).strip().lower()
    provider = str(os.environ.get("MODEL_PROVIDER", "")).strip().lower()
    return flag in {"1", "true", "yes", "on"} or provider == "stub"


def retrieve_while_offline() -> bool:
    """离线模式下是否仍去查法规库。

    默认不查（离线就该什么都不外发）；开发时想单独验检索链路可显式打开，
    此时会真的调用法宝端点（占用额度），但模型仍走 stub。
    """
    return str(os.environ.get("FAXIAOZHI_OFFLINE_RETRIEVAL", "")).strip().lower() in {"1", "true", "yes", "on"}


def _client(cfg: Any) -> Any:
    from openai import AsyncOpenAI

    return AsyncOpenAI(
        api_key=cfg.model_api_key,
        base_url=cfg.model_base_url,
        timeout=float(cfg.get("model.timeout_seconds", 120)),
        max_retries=0,
    )


async def stream_reply(cfg: Any, messages: list[dict[str, Any]], temperature: float = 0.3) -> AsyncIterator[str]:
    """按增量吐字。调用方负责把片段写进 SSE 的 `delta` 事件。"""
    if offline():
        async for chunk in _stub_stream():
            yield chunk
        return

    client = _client(cfg)
    try:
        stream = await client.chat.completions.create(
            model=cfg.model_id,
            messages=messages,
            temperature=temperature,
            max_tokens=int(cfg.get("model.max_tokens", 2048)),
            stream=True,
        )
        async for event in stream:
            choices = getattr(event, "choices", None) or []
            if not choices:
                continue
            piece = getattr(choices[0].delta, "content", None)
            if piece:
                yield piece
    finally:
        close = getattr(client, "close", None)
        if close is not None:
            try:
                await close()
            except Exception:  # 关闭失败不影响已吐出的内容
                pass


async def _stub_stream() -> AsyncIterator[str]:
    """把示例回答切段吐出，模拟流式的节奏；不联网。"""
    step = 6
    for index in range(0, len(STUB_REPLY), step):
        yield STUB_REPLY[index : index + step]
        await asyncio.sleep(0.03)


async def ping(cfg: Any) -> dict[str, Any]:
    """给 /api/bootstrap 用：只报告配置状态，不真的调用模型。"""
    return {
        "provider": "stub" if offline() else cfg.model_provider,
        "is_stub": offline(),
        "model_id": "stub" if offline() else cfg.model_id,
        "key_present": bool(cfg.model_api_key),
    }
