"""v3 HTTP 入口

接口（全部同源给前端，经前端的 BFF 转发；浏览器不直连）：
  GET    /api/health              探活
  GET    /api/bootstrap           能力与离线状态（界面据此标注）
  POST   /api/chat                发一句话，SSE 流式回（meta / sources / note / delta / done / error）
  GET    /api/chat/{sid}          取整条时间线（刷新恢复用）
  GET    /api/sessions            会话列表（"我的"页用）
  DELETE /api/session/{sid}       删除一个会话

启动：
  APP_PORT=8011 uv run python -m app_v3.main
  MODEL_PROVIDER=stub FAXIAOZHI_OFFLINE=1 ...   # 离线，不花钱
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.config import get_config

from .chat import run_turn
from .model import offline, ping
from .store import Store

LOG = logging.getLogger("faxiaozhi.v3")

DISCLOSURE = "以上内容由 AI 生成，仅供学习与研究参考，不构成正式法律意见。涉及实际案件或重大权益，请由执业律师结合完整材料复核。"

app = FastAPI(title="法小智 v3", version="3.0.0")
_store: Store | None = None


def store() -> Store:
    global _store
    if _store is None:
        path = Path(os.environ.get("V3_DB", "")).expanduser() if os.environ.get("V3_DB") else get_config().path("paths.data_dir", "data") / "v3.sqlite3"
        _store = Store(path)
        LOG.info("会话库：%s", path)
    return _store


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "is_stub": offline()}


@app.get("/api/bootstrap")
async def bootstrap() -> dict[str, Any]:
    cfg = get_config()
    model = await ping(cfg)
    endpoints = cfg.mcp_endpoints() or {}
    return {
        "agent": {"name": "法小智", "id": "faxiaozhi_chat"},
        "model": model,
        "retrieval": {
            "enabled": bool(endpoints.get("search_statutes") or endpoints.get("search_cases")),
            "configured": bool(cfg.mcp_token),
            "kinds": sorted(k for k in ("search_statutes", "search_cases") if endpoints.get(k)),
            "verified": False,
        },
        "disclosure": DISCLOSURE,
    }


@app.post("/api/chat")
async def chat(request: Request) -> StreamingResponse:
    payload = await request.json()
    cfg = get_config()

    async def events() -> AsyncIterator[str]:
        try:
            async for name, data in run_turn(cfg, store(), payload or {}):
                yield _sse(name, data)
        except Exception as exc:  # noqa: BLE001 —— 兜底：任何未预期异常都要作为 error 事件收尾
            LOG.exception("对话未预期失败")
            yield _sse("error", {"code": type(exc).__name__, "message": "服务内部出错，本次没有完成，请重试。"})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"cache-control": "no-cache", "x-accel-buffering": "no", "connection": "keep-alive"},
    )


@app.get("/api/chat/{session_id}")
async def history(session_id: str, limit: int = 200) -> dict[str, Any]:
    found = store().get_session(session_id)
    if not found:
        raise HTTPException(status_code=404, detail={"error": {"code": "session_not_found", "message": "会话不存在。"}})
    return {"session_id": session_id, "title": found.get("title", ""), "messages": store().messages(session_id, limit)}


@app.get("/api/sessions")
async def sessions(limit: int = 50) -> dict[str, Any]:
    return {"sessions": store().list_sessions(limit)}


@app.delete("/api/session/{session_id}")
async def delete_session(session_id: str) -> dict[str, Any]:
    return {"deleted": store().delete_session(session_id)}


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    port = int(os.environ.get("APP_PORT", "8011"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
