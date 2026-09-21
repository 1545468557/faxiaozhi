"""HTTP 契约：错误结构、校验、并发排队、路径穿越、SSE 约定。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.errors import ApiError
from app.server import app

client = TestClient(app)


def test_bootstrap_shape():
    with client:
        body = client.get("/api/bootstrap").json()
    for key in ("agent", "modules", "model", "mcp", "policy", "limits", "disclosure"):
        assert key in body
    assert body["model"]["is_stub"] is True
    assert body["policy"]["allow_synthetic"] is False


def test_create_session_and_state():
    with client:
        created = client.post("/api/session", json={}).json()
        session_id = created["session_id"]
        state = client.get(f"/api/session/{session_id}/state").json()
    assert state["session_id"] == session_id
    assert state["sample"]["locked"] is False
    assert state["export"]["ready"] is False


def test_unknown_session_returns_unified_error():
    with client:
        response = client.get("/api/session/s_missing/state")
    assert response.status_code == 404
    body = response.json()
    assert set(body["error"]) >= {"code", "message"}
    assert body["error"]["code"] == "session_not_found"
    assert "Traceback" not in response.text


def test_empty_input_is_rejected():
    with client:
        created = client.post("/api/session", json={}).json()
        response = client.post(f"/api/session/{created['session_id']}/message", json={"text": "   "})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "empty_input"


def test_checkpoint_mismatch_is_rejected():
    with client:
        created = client.post("/api/session", json={}).json()
        response = client.post(
            f"/api/session/{created['session_id']}/checkpoint",
            json={"kind": "sample_confirm", "payload": {"confirmed": ["a"]}},
        )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "checkpoint_mismatch"


def test_empty_sample_is_rejected():
    with client:
        created = client.post("/api/session", json={}).json()
        session_id = created["session_id"]
        session = app.state.__dict__.get("_unused")
        from app.session import STORE

        store_session = STORE.get(session_id)
        store_session.checkpoint_kind = "sample_confirm"
        response = client.post(
            f"/api/session/{session_id}/checkpoint",
            json={"kind": "sample_confirm", "payload": {"confirmed": [], "excluded": []}},
        )
    assert session is None
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "empty_sample"


def test_export_blocked_when_nothing_generated():
    with client:
        created = client.post("/api/session", json={}).json()
        response = client.post(f"/api/session/{created['session_id']}/export", json={})
    assert response.status_code == 409
    body = response.json()["error"]
    assert body["code"] == "export_blocked"
    assert "无法导出" in body["message"] or "尚未" in body["message"]


def test_upload_rejects_renamed_binary():
    with client:
        created = client.post("/api/session", json={}).json()
        files = {"file": ("fake.docx", b"MZ\x90\x00" + b"\x00" * 200, "application/octet-stream")}
        response = client.post(f"/api/session/{created['session_id']}/upload", files=files)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "type_mismatch"


def test_upload_accepts_text_and_flags_review():
    """上传成功并标记「待核验」。

    2-4 语义变化（有意）：上传材料不再统一是 `user_material`，而是按角色取 `case` / `statute`；
    但 `origin` 恒为 `user`（这才是「用户材料」的判定依据，门禁 R3 与红线都基于它）。
    """
    with client:
        created = client.post("/api/session", json={}).json()
        payload = ("第一条 本合同自双方签字之日起生效。\n" * 10).encode("utf-8")
        files = {"file": ("contract.txt", payload, "text/plain")}
        response = client.post(f"/api/session/{created['session_id']}/upload", files=files)
        body = response.json()
        assert response.status_code == 200
        assert body["needs_review"] is True
        evidence = client.get(f"/api/session/{created['session_id']}/evidence").json()
    user_sources = [s for s in evidence["sources"] if s["origin"] == "user"]
    assert user_sources, "用户上传材料必须进入依据池"
    assert user_sources[0]["kind"] in {"case", "statute"}
    assert user_sources[0]["user_verified"] is False
    assert user_sources[0]["quote"] == "", "用户材料原文绝不经接口回显"


def test_evidence_hides_user_material_text():
    with client:
        created = client.post("/api/session", json={}).json()
        secret = "甲方某某公司与乙方某某公司设备采购合同专用条款内容示例"
        files = {"file": ("c.txt", (secret * 5).encode("utf-8"), "text/plain")}
        client.post(f"/api/session/{created['session_id']}/upload", files=files)
        response = client.get(f"/api/session/{created['session_id']}/evidence")
    assert secret not in response.text


def test_metrics_endpoint_returns_shape():
    with client:
        body = client.get("/api/metrics").json()
    for key in ("events", "citations", "by_status", "by_event"):
        assert key in body
    assert "traceable_rate" in body["citations"]


async def test_download_path_traversal_blocked():
    from app.server import download

    with pytest.raises(ApiError) as exc:
        await download("../../.env")
    assert exc.value.code == "path_escape"


def test_sse_helper_format():
    from app.server import _sse

    chunk = _sse("phase", {"name": "检索", "index": 1})
    assert chunk.startswith("event: phase\n")
    assert chunk.endswith("\n\n")
    assert "检索" in chunk


def test_concurrency_queue_returns_429_with_visible_message():
    """回归：并发排队必须是可见的错误（当初返回 202 被前端吞掉，用户看到「点了没反应」）。"""
    from app.session import STORE

    original = STORE.max_concurrent
    try:
        with client:
            # 注意：lifespan 会用 config.yaml 覆盖 max_concurrent，
            # 必须在启动之后再制造「名额已满」，否则单跑本用例会漏测（本次实测发现的假绿）。
            STORE.max_concurrent = -1
            response = client.post("/api/session", json={})
            assert response.status_code == 200
            sid = response.json()["session_id"]
            followup = client.post(f"/api/session/{sid}/message", json={"text": "议题"})
        assert followup.status_code == 429
        body = followup.json()["error"]
        assert body["code"] == "concurrency_queued"
        assert "上限" in body["message"]
    finally:
        STORE.max_concurrent = original


def test_idle_sessions_are_swept():
    from app.session import SESSION_CLOSED, SessionStore

    store = SessionStore(max_concurrent=1, ttl_seconds=0)
    first = store.create()
    first.last_active -= 10            # 人为变旧
    store.create()                     # 触发 sweep
    assert first.status == SESSION_CLOSED
    assert store.active_count() == 1


def test_index_page_served():
    with client:
        response = client.get("/")
    assert response.status_code == 200
    assert "法小智" in response.text
