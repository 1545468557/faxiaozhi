"""检索：直接复用现成的法宝工具层，但**不做核验**

为什么复用：`app/tools/mcp.py`（596 行：JSON-RPC 调用、熔断、字段映射、参数转换）
是纯粹的管道，重写一遍只是把 12 个端点的坑再踩一次，不产生任何产品价值。
`McpProvider(cfg, logical, kind, endpoints).call(**args)` 不依赖旧的状态机，可以直接用。

为什么不做核验：产品决定（2026-09-20）——不要逐条核验，只要能聊。
所以这里只把材料**原样**交给模型和界面，不做门禁判定，也不声称"已核验"。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

LOG = logging.getLogger("faxiaozhi.v3.retrieve")

MAX_SOURCES_PER_KIND = 4
QUOTE_CHARS = 1200

JOBS = (
    {"logical": "search_statutes", "kind": "statute", "args": lambda q: {"query": q, "applicable_at": ""}},
    {"logical": "search_cases", "kind": "case", "args": lambda q: {"expression": q, "limit": 6}},
)


def _brief(source: Any) -> dict[str, Any]:
    def get(name: str, fallback: str = "") -> str:
        value = getattr(source, name, fallback)
        return "" if value is None else str(value)

    return {
        "kind": get("kind", "statute"),
        "title": get("title") or get("identifier"),
        "identifier": get("identifier"),
        "court": get("court"),
        "decided_on": get("decided_on"),
        "uri": get("uri"),
        "origin_text": get("origin_text"),
        "quote": get("quote")[:QUOTE_CHARS],
    }


def _run_one(cfg: Any, job: dict[str, Any], query: str) -> tuple[list[dict[str, Any]], str]:
    from app.tools.mcp import McpProvider

    endpoints = (cfg.mcp_endpoints() or {}).get(job["logical"]) or []
    if not endpoints:
        return [], f"{job['logical']} 未配置地址"

    provider = McpProvider(cfg, job["logical"], job["kind"], endpoints)
    result = provider.call(**job["args"](query))
    sources = [_brief(item) for item in (getattr(result, "sources", None) or [])]
    note = ""
    status = str(getattr(result, "status", ""))
    if not sources:
        note = str(getattr(result, "detail", "")) or f"{job['logical']} 未返回结果"
    elif "abstract" in status.lower():
        note = "本次只取到摘要，没有全文"
    return sources[:MAX_SOURCES_PER_KIND], note


async def search(cfg: Any, query: str, enabled: bool = True) -> dict[str, Any]:
    """并行查法条与案例。任何一路失败都只记备注，不阻塞回答。"""
    if not enabled:
        return {"status": "skipped", "sources": [], "notes": []}

    query = (query or "").strip()
    if not query:
        return {"status": "skipped", "sources": [], "notes": []}

    async def one(job: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
        try:
            return await asyncio.to_thread(_run_one, cfg, job, query)
        except Exception as exc:  # 接口失败 ≠ 没有相关规定，如实记下来
            LOG.warning("检索失败（%s）：%s", job["logical"], type(exc).__name__)
            return [], f"{job['logical']} 调用失败（{type(exc).__name__}）"

    results = await asyncio.gather(*(one(job) for job in JOBS))
    sources: list[dict[str, Any]] = []
    notes: list[str] = []
    for items, note in results:
        sources.extend(items)
        if note:
            notes.append(note)

    return {"status": "ok" if sources else "empty", "sources": sources, "notes": notes}
