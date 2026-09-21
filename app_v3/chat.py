"""一轮对话的编排：落库 → 检索 → 流式生成 → 落库

和旧版最大的不同：
- 不再有"一次运行一条 SSE、追问即结束运行、轮数由代码计数"的状态机；
  用户想说几轮就说几轮，追问是模型自己在回答里问出来的。
- 回答流式吐字，前端边收边显示。
- 失败就是失败（超时/网络/服务不可用），文案直说，不把失败说成"没有相关规定"。
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from .model import offline, retrieve_while_offline, stream_reply
from .prompt import build_messages
from .retrieve import search

LOG = logging.getLogger("faxiaozhi.v3.chat")

INTERRUPTED = "\n\n（回答未生成完就被中断，可以重发一次。）"


def _friendly(exc: Exception) -> str:
    name = type(exc).__name__
    text = str(exc)
    if "Timeout" in name or "timeout" in text.lower():
        return "模型服务响应超时，本次没有生成回答，请重试。"
    if "Authentication" in name or "401" in text:
        return "模型服务的密钥无效或已过期，请检查本地配置。"
    if "RateLimit" in name or "429" in text:
        return "模型服务请求过于频繁，稍后再试。"
    return "生成回答时出错，本次没有完成，请重试。"


async def run_turn(
    cfg: Any,
    store: Any,
    payload: dict[str, Any],
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    message = str(payload.get("message") or "").strip()
    if not message:
        yield "error", {"code": "empty_message", "message": "请先说说你想问什么。"}
        return

    # 离线模式下默认不查法规库（不外发任何请求）；模型走 stub 时也一样
    use_retrieval = payload.get("use_retrieval") is not False and (not offline() or retrieve_while_offline())
    session = store.ensure_session(payload.get("session_id"), title=message)
    sid = str(session["session_id"])
    store.append_message(sid, "user", message)
    yield "meta", {"session_id": sid, "is_stub": offline(), "title": session.get("title") or ""}

    started = time.time()
    got: dict[str, Any] = {"status": "skipped", "sources": [], "notes": []}
    if use_retrieval:
        got = await search(cfg, message, enabled=True)
        if got["sources"]:
            yield "sources", {"items": got["sources"], "notes": got["notes"]}
        elif got["notes"]:
            # 检索失败也要说清楚：接口失败不等于没有相关规定
            yield "note", {"text": "检索没成功：" + "；".join(got["notes"]) + "。这轮回答没有引用检索结果。"}

    history = store.messages(sid)
    messages = build_messages(history, got["sources"])

    chunks: list[str] = []
    try:
        async for piece in stream_reply(cfg, messages):
            chunks.append(piece)
            yield "delta", {"text": piece}
    except Exception as exc:  # noqa: BLE001 —— 真实失败必须让用户看见
        LOG.warning("生成失败：%s / %s", type(exc).__name__, exc)
        if chunks:
            store.append_message(sid, "assistant", "".join(chunks) + INTERRUPTED, got["sources"])
        yield "error", {"code": type(exc).__name__, "message": _friendly(exc)}
        return

    text = "".join(chunks).strip()
    if not text:
        yield "error", {"code": "empty_answer", "message": "模型这次没有返回内容，请重发一次。"}
        return

    saved = store.append_message(sid, "assistant", text, got["sources"])
    yield "done", {
        "session_id": sid,
        "message_id": saved["id"],
        "chars": len(text),
        "sources": len(got["sources"]),
        "elapsed_ms": int((time.time() - started) * 1000),
    }
