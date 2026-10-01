"""v3 HTTP 入口

接口（全部同源给前端，经前端的 BFF 转发；浏览器不直连）：
  GET    /api/health              探活（免登录）
  GET    /api/bootstrap           能力与离线状态（免登录）
  POST   /api/chat                发一句话，SSE 流式回（meta / sources / note / delta / done / error）
  GET    /api/chat/{sid}          取整条时间线（刷新恢复用）
  GET    /api/sessions            会话列表（"我的"页用）
  DELETE /api/session/{sid}       删除一个会话

**登录门（2026-09-27 隔离改造）**
改造前这个进程**整段没有鉴权** —— 经 BFF 谁都能用、不用登录，而且 `ensure_session`
认下任意 sid（拿到别人的会话 id 就能读记录）。现在与 8010 用同一套门口
（`app.auth.guard`）：`/api/**` 除白名单外一律要求有效登录态；
归属校验在 `store` 层按 `user_id` 做。

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
from .store import SessionOwnedByOther, Store

LOG = logging.getLogger("faxiaozhi.v3")

# 先加载 .env，确保首个请求经过登录门时也使用本机部署配置。
get_config()
from app.auth import guard as auth_guard  # noqa: E402

auth_guard.assert_guest_ready()

DISCLOSURE = (
    "以上内容由 AI 生成，仅供学习与研究参考，不构成正式法律意见。"
    "涉及实际案件或重大权益，请由执业律师结合完整材料复核。"
)

app = FastAPI(title="法小智 v3", version="3.0.0")
_store: Store | None = None


def store() -> Store:
    global _store
    if _store is None:
        path = (Path(os.environ["V3_DB"]).expanduser() if os.environ.get("V3_DB")
                else get_config().path("paths.data_dir", "data") / "v3.sqlite3")
        _store = Store(path)
        LOG.info("会话库：%s", path)
    return _store


@app.middleware("http")
async def _login_gate(request: Request, call_next):
    """与 8010 同一套门口：`/api/**` 除白名单（登录本身 + 探活/能力声明）外要登录态。

    白名单来自 `guard.PUBLIC_PREFIXES` / `guard.PUBLIC_EXACT`，那里面已经包含
    `/api/health` 与 `/api/bootstrap`，这个进程不用再单独维护一份。
    `AUTH_REQUIRE_LOGIN=0` 可以关掉（本机开发方便），但公网必须开着。
    """
    from app.auth import guard

    blocked = guard.blocked_response(request)
    if blocked is not None:
        return blocked
    guard.request_principal(request)
    limited = guard.guest_limit_response(request)
    if limited is not None:
        return guard.attach_guest_cookie(limited, request)
    return guard.attach_guest_cookie(await call_next(request), request)


def _owner(request: Request) -> str:
    """当前请求的数据归属桶名。未登录只在关了登录门时可能发生，落 `_anonymous`。"""
    from app.auth import guard

    return guard.owner_key(guard.request_principal(request))


def _session_not_found() -> HTTPException:
    """归属不符与真的不存在**回同一个响应** —— 不回 403，否则「这个 sid 是真的」就泄漏了。"""
    return HTTPException(status_code=404, detail={"error": {"code": "session_not_found", "message": "会话不存在。"}})


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
    owner = _owner(request)

    # 归属不符的 sid 在**开流之前**就挡掉，别让它变成一个 SSE error 事件（那样前端拿不到 404 语义）。
    claimed = (payload or {}).get("session_id")
    if claimed and store().user_of(str(claimed)) not in (None, owner):
        raise _session_not_found()

    async def events() -> AsyncIterator[str]:
        try:
            async for name, data in run_turn(cfg, store(), payload or {}, user_id=owner):
                yield _sse(name, data)
        except SessionOwnedByOther:
            # 兜底（理论上上面已经挡掉了）：仍然按「不存在」说，不确认这个 sid 是真的
            yield _sse("error", {"code": "session_not_found", "message": "会话不存在。"})
        except Exception as exc:  # noqa: BLE001 —— 兜底：任何未预期异常都要作为 error 事件收尾
            LOG.exception("对话未预期失败")
            yield _sse("error", {"code": type(exc).__name__, "message": "服务内部出错，本次没有完成，请重试。"})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"cache-control": "no-cache", "x-accel-buffering": "no", "connection": "keep-alive"},
    )


@app.get("/api/chat/{session_id}")
async def history(request: Request, session_id: str, limit: int = 200) -> dict[str, Any]:
    owner = _owner(request)
    found = store().get_session(session_id, owner)
    if not found:
        raise _session_not_found()
    return {
        "session_id": session_id,
        "title": found.get("title", ""),
        "messages": store().messages(session_id, limit, user_id=owner),
    }


@app.get("/api/sessions")
async def sessions(request: Request, limit: int = 50) -> dict[str, Any]:
    return {"sessions": store().list_sessions(limit, user_id=_owner(request))}


@app.delete("/api/session/{session_id}")
async def delete_session(request: Request, session_id: str) -> dict[str, Any]:
    return {"deleted": store().delete_session(session_id, user_id=_owner(request))}


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    port = int(os.environ.get("APP_PORT") or os.environ.get("PORT") or "8011")
    host = os.environ.get("V3_HOST") or os.environ.get("APP_HOST") or "127.0.0.1"
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
