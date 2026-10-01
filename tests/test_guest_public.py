"""Public mode keeps a login-free UI while isolating each browser's records."""

import importlib
import json

import pytest
from fastapi.testclient import TestClient

from app import server as legacy
from app.auth import guard
from app.config import get_config


def _sid(sse: str) -> str:
    import json

    for line in sse.splitlines():
        if line.startswith("data:"):
            payload = json.loads(line[5:].strip())
            if payload.get("session_id"):
                return payload["session_id"]
    raise AssertionError("missing session id")


def test_public_mode_requires_a_persistent_signing_secret(monkeypatch):
    monkeypatch.setenv("AUTH_GUEST_MODE", "1")
    monkeypatch.delenv("GUEST_COOKIE_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="GUEST_COOKIE_SECRET"):
        guard.assert_guest_ready()


def test_guest_cookie_is_shared_across_backends_but_not_visitors(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRE_LOGIN", "1")
    monkeypatch.setenv("AUTH_GUEST_MODE", "1")
    monkeypatch.setenv("GUEST_COOKIE_SECRET", "test-secret-with-at-least-thirty-two-characters")
    monkeypatch.setenv("AUTH_DB", str(tmp_path / "auth.sqlite3"))
    monkeypatch.setenv("V3_DB", str(tmp_path / "v3.sqlite3"))

    import app_v3.main as v3

    importlib.reload(v3)
    with TestClient(legacy.app) as old_api:
        first_response = old_api.get("/api/statutes/status")
        assert first_response.status_code == 200
        token = old_api.cookies.get("fzx_guest")
        assert token
        assert "HttpOnly" in first_response.headers.get("set-cookie", "")
        assert old_api.get("/api/metrics").status_code == 403
        assert old_api.get("/api/auth/me").status_code == 404
        created = old_api.post("/api/session", json={"branch": "consult"})
        assert created.status_code == 200
        legacy_sid = created.json()["session_id"]

    stranger = TestClient(legacy.app)
    assert stranger.get(f"/api/session/{legacy_sid}/state").status_code == 404

    first = TestClient(v3.app)
    first.cookies.set("fzx_guest", token)
    response = first.post("/api/chat", json={"message": "你好", "use_retrieval": False})
    assert response.status_code == 200
    sid = _sid(response.text)
    assert first.get("/api/chat/" + sid).status_code == 200

    second = TestClient(v3.app)
    assert second.get("/api/chat/" + sid).status_code == 404
    assert second.get("/api/sessions").json()["sessions"] == []
    assert second.cookies.get("fzx_guest") != token

    tampered = TestClient(v3.app)
    tampered.cookies.set("fzx_guest", token[:-1] + ("0" if token[-1] != "0" else "1"))
    refreshed = tampered.get("/api/sessions")
    assert refreshed.status_code == 200
    assert "fzx_guest=" in refreshed.headers.get("set-cookie", "")


def test_paid_calls_have_guest_and_global_daily_caps(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRE_LOGIN", "1")
    monkeypatch.setenv("AUTH_GUEST_MODE", "1")
    monkeypatch.setenv("GUEST_COOKIE_SECRET", "test-secret-with-at-least-thirty-two-characters")
    monkeypatch.setenv("GUEST_QUOTA_DB", str(tmp_path / "quota.sqlite3"))
    monkeypatch.setenv("V3_DB", str(tmp_path / "v3.sqlite3"))
    monkeypatch.setenv("GUEST_DAILY_LIMIT", "2")
    monkeypatch.setenv("GUEST_GLOBAL_DAILY_LIMIT", "3")
    import app_v3.main as v3

    importlib.reload(v3)
    first = TestClient(v3.app)
    second = TestClient(v3.app)
    for _ in range(2):
        assert first.post("/api/chat", json={"message": "测试", "use_retrieval": False}).status_code == 200
    assert first.post("/api/chat", json={"message": "测试"}).status_code == 429
    assert second.post("/api/chat", json={"message": "测试", "use_retrieval": False}).status_code == 200
    assert second.post("/api/chat", json={"message": "测试"}).status_code == 429


def test_run_resume_rejects_other_guest_journal(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRE_LOGIN", "1")
    monkeypatch.setenv("AUTH_GUEST_MODE", "1")
    monkeypatch.setenv("GUEST_COOKIE_SECRET", "test-secret-with-at-least-thirty-two-characters")
    monkeypatch.setenv("GUEST_QUOTA_DB", str(tmp_path / "quota.sqlite3"))
    first = TestClient(legacy.app)
    second = TestClient(legacy.app)
    assert first.get("/api/statutes/status").status_code == 200
    assert second.get("/api/statutes/status").status_code == 200
    owner = guard._guest_owner(first.cookies.get("fzx_guest"))
    run_id = "guest-ownership-test"
    state_file = get_config().runs_dir / f"{run_id}.json"
    state_file.write_text(
        json.dumps({"run_id": run_id, "owner": owner, "workflow": "legal-research", "args": {}}),
        encoding="utf-8",
    )
    try:
        assert second.post(f"/api/runs/{run_id}/resume").status_code == 404
        other_session = second.post("/api/session", json={"branch": "research"}).json()["session_id"]
        assert second.post(f"/api/session/{other_session}/resume", json={"run_id": run_id}).status_code == 404
        assert first.post("/api/runs/../../resume").status_code == 404
    finally:
        state_file.unlink(missing_ok=True)
