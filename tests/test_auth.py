"""账号底座（迭代 2-1）：注册 / 登录 / 限速 / 退出 / 邀请码。

约定：用临时库（AUTH_DB 指向 tmp），不碰真实账号库。
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_DB", str(tmp_path / "auth.sqlite3"))
    monkeypatch.setenv("AUTH_LOGIN_MAX_PER_MINUTE", "5")
    import app.auth.core as core

    importlib.reload(core)
    from app import server

    importlib.reload(server)
    return TestClient(server.app), core


def _invite(core) -> str:
    return core.generate_invite("测试用")["code"]


def test_register_login_me_logout(client):
    http, core = client
    code = _invite(core)
    created = http.post("/api/auth/register", json={"invite_code": code, "username": "lawyer1", "password": "passw0rd!"})
    assert created.status_code == 200, created.text
    assert created.json()["user"]["username"] == "lawyer1"
    assert "fzx_session" in created.headers.get("set-cookie", "")
    assert http.get("/api/auth/me").json()["user"]["username"] == "lawyer1"

    http.post("/api/auth/logout")
    assert http.get("/api/auth/me").status_code == 401

    again = http.post("/api/auth/login", json={"username": "lawyer1", "password": "passw0rd!"})
    assert again.status_code == 200
    assert http.get("/api/auth/me").json()["user"]["role"] == "owner"  # 第一个用户是 owner


def test_invite_is_one_time_and_not_plaintext(client):
    http, core = client
    code = _invite(core)
    assert http.post("/api/auth/register", json={"invite_code": code, "username": "user1", "password": "passw0rd!"}).status_code == 200
    # 同一个邀请码再用一次 → 失败
    reuse = http.post("/api/auth/register", json={"invite_code": code, "username": "user2", "password": "passw0rd!"})
    assert reuse.status_code == 422
    assert reuse.json()["error"]["code"] == "invite_invalid"
    # 列表里不含码原文
    listing = http.get("/api/admin/invites")
    assert listing.status_code == 200
    assert all("code" not in item for item in listing.json()["items"])
    assert code not in listing.text


def test_weak_password_and_bad_invite_rejected(client):
    http, core = client
    weak = http.post("/api/auth/register", json={"invite_code": _invite(core), "username": "user3", "password": "123"})
    assert weak.status_code == 422 and weak.json()["error"]["code"] == "password_weak"
    bad = http.post("/api/auth/register", json={"invite_code": "nope", "username": "user4", "password": "passw0rd!"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invite_invalid"


def test_login_failures_are_generic_and_rate_limited(client):
    http, core = client
    http.post("/api/auth/register", json={"invite_code": _invite(core), "username": "user5", "password": "passw0rd!"})
    http.post("/api/auth/logout")
    wrong = http.post("/api/auth/login", json={"username": "user5", "password": "bad-pass"})
    missing = http.post("/api/auth/login", json={"username": "no-such-user", "password": "bad-pass"})
    # 不区分"用户不存在/密码错"
    assert wrong.json()["error"]["code"] == missing.json()["error"]["code"] == "auth_failed"
    # 连续失败触发限速
    codes = [http.post("/api/auth/login", json={"username": "user5", "password": "bad-pass"}).status_code for _ in range(6)]
    assert 429 in codes


def test_password_never_stored_in_plaintext(client, tmp_path):
    http, core = client
    http.post("/api/auth/register", json={"invite_code": _invite(core), "username": "user6", "password": "SuperSecret1"})
    raw = (tmp_path / "auth.sqlite3").read_bytes()
    assert b"SuperSecret1" not in raw


def test_admin_invite_requires_owner(client):
    http, core = client
    # 未登录不能生成
    assert http.post("/api/admin/invites", json={"note": "x"}).status_code == 401
    http.post("/api/auth/register", json={"invite_code": _invite(core), "username": "owner9", "password": "passw0rd!"})
    made = http.post("/api/admin/invites", json={"note": "给同事"})
    assert made.status_code == 200 and made.json()["code"].startswith("fzx-")
    # 第二个用户不是 owner → 拒绝
    http.post("/api/auth/logout")
    second_code = made.json()["code"]
    http.post("/api/auth/register", json={"invite_code": second_code, "username": "member1", "password": "passw0rd!"})
    assert http.post("/api/admin/invites", json={"note": "x"}).status_code == 403
