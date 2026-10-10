"""账号底座（2026-09-27 改造）：手机号 + 验证码 / 密码 · 邀请码 · 限速 · 退出。

约定：
- 用临时库（`AUTH_DB`、`SMS_OUTBOX` 都指向 tmp），不碰真实账号库与真实发码文件；
- 验证码**从 mock 通道的 outbox 文件里读**，而不是去调内部函数 —— 这样测的是
  真实链路（发码 → 落文件 → 校验），内部改名不会让测试变成假绿。
"""

from __future__ import annotations

import importlib
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_DB", str(tmp_path / "auth.sqlite3"))
    monkeypatch.setenv("SMS_OUTBOX", str(tmp_path / "sms-outbox.jsonl"))
    monkeypatch.setenv("SMS_PROVIDER", "mock")
    monkeypatch.setenv("AUTH_LOGIN_MAX_PER_MINUTE", "5")
    # 测试里要连发验证码，把冷却关掉；冷却本身由 test_code_resend_cooldown 单独覆盖
    monkeypatch.setenv("AUTH_CODE_MIN_INTERVAL", "0")
    monkeypatch.setenv("AUTH_CODE_MAX_PER_HOUR", "100")
    monkeypatch.setenv("AUTH_CODE_MAX_PER_DAY", "100")
    import app.auth.core as core

    importlib.reload(core)
    from app import server

    importlib.reload(server)
    return TestClient(server.app), core


PHONE = "13812345678"
PHONE2 = "13900001111"


def _records(tmp_path) -> list[dict]:
    path = tmp_path / "sms-outbox.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _latest_code(tmp_path, purpose: str) -> str:
    matched = [record for record in _records(tmp_path) if record["purpose"] == purpose]
    assert matched, f"没有发出 purpose={purpose} 的验证码"
    return matched[-1]["code"]


def _send(http, phone: str, purpose: str = "login"):
    response = http.post("/api/auth/code", json={"phone": phone, "purpose": purpose})
    assert response.status_code == 200, response.text
    return response.json()


def _register(http, core, tmp_path, phone: str, password: str = "", invite: str | None = None):
    invite = invite or core.generate_invite("测试用")["code"]
    _send(http, phone, "register")
    return http.post(
        "/api/auth/register",
        json={"invite_code": invite, "phone": phone, "code": _latest_code(tmp_path, "register"), "password": password},
    )


# ------------------------------------------------------------------ 注册 / 登录

def test_register_login_me_logout_with_code(client, tmp_path):
    http, core = client
    created = _register(http, core, tmp_path, PHONE)
    assert created.status_code == 200, created.text
    user = created.json()["user"]
    assert user["phone"] == PHONE
    assert user["phone_masked"] == "138****5678", "手机号要有一个打码版给界面用"
    assert user["has_password"] is False, "没设密码也要能注册成功"
    assert "fzx_session" in created.headers.get("set-cookie", "")
    assert http.get("/api/auth/me").json()["user"]["phone"] == PHONE
    assert http.get("/api/auth/me").json()["user"]["role"] == "owner"  # 第一个用户是 owner

    http.post("/api/auth/logout")
    assert http.get("/api/auth/me").status_code == 401

    # 验证码登录
    _send(http, PHONE, "login")
    again = http.post("/api/auth/login", json={"phone": PHONE, "code": _latest_code(tmp_path, "login")})
    assert again.status_code == 200, again.text
    assert http.get("/api/auth/me").json()["user"]["phone"] == PHONE


def test_invite_is_one_time_and_not_plaintext(client, tmp_path):
    http, core = client
    invite = core.generate_invite("一次性")["code"]
    assert _register(http, core, tmp_path, PHONE, invite=invite).status_code == 200

    # 同一个邀请码再用一次 → 失败（换个手机号，排除“号已占用”的干扰）
    reuse = _register(http, core, tmp_path, PHONE2, invite=invite)
    assert reuse.status_code == 422
    assert reuse.json()["error"]["code"] == "invite_invalid"

    listing = http.get("/api/admin/invites")
    assert listing.status_code == 200
    assert all("code" not in item for item in listing.json()["items"])
    assert invite not in listing.text


def test_register_needs_invite_and_valid_code(client, tmp_path):
    http, core = client
    _send(http, PHONE, "register")
    code = _latest_code(tmp_path, "register")

    bad_invite = http.post("/api/auth/register", json={"invite_code": "nope", "phone": PHONE, "code": code})
    assert bad_invite.status_code == 422 and bad_invite.json()["error"]["code"] == "invite_invalid"

    bad_code = http.post(
        "/api/auth/register",
        json={"invite_code": core.generate_invite("x")["code"], "phone": PHONE, "code": "000000"},
    )
    assert bad_code.status_code == 422 and bad_code.json()["error"]["code"] == "code_invalid"


def test_weak_password_rejected_but_password_is_optional(client, tmp_path):
    http, core = client
    weak = _register(http, core, tmp_path, PHONE, password="123")
    assert weak.status_code == 422 and weak.json()["error"]["code"] == "password_weak"
    # 同一个号换个邀请码用合规密码注册 → 成功（上一步失败没把状态搞坏）
    assert _register(http, core, tmp_path, PHONE, password="passw0rd!").status_code == 200


def test_phone_already_registered(client, tmp_path):
    http, core = client
    assert _register(http, core, tmp_path, PHONE).status_code == 200
    dup = _register(http, core, tmp_path, PHONE)
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "phone_taken"


# ------------------------------------------------------------------ 验证码

def test_code_purpose_is_isolated(client, tmp_path):
    """注册的验证码不能拿来登录 —— 用途隔离，防止「一个码打通全部入口」。"""
    http, core = client
    _send(http, PHONE, "register")
    register_code = _latest_code(tmp_path, "register")

    # 用注册码去登录 → 登录码根本还没发过，必然失败
    denied = http.post("/api/auth/login", json={"phone": PHONE, "code": register_code})
    assert denied.status_code == 422 and denied.json()["error"]["code"] == "code_invalid"

    # 用登录码去注册 → 同样不认
    _send(http, PHONE, "login")
    login_code = _latest_code(tmp_path, "login")
    bad = http.post(
        "/api/auth/register",
        json={"invite_code": core.generate_invite("x")["code"], "phone": PHONE, "code": login_code},
    )
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "code_invalid"


def test_code_is_single_use(client, tmp_path):
    http, core = client
    assert _register(http, core, tmp_path, PHONE).status_code == 200
    http.post("/api/auth/logout")
    _send(http, PHONE, "login")
    code = _latest_code(tmp_path, "login")
    assert http.post("/api/auth/login", json={"phone": PHONE, "code": code}).status_code == 200
    http.post("/api/auth/logout")
    # 同一个码再来一次 → 已被消费
    reused = http.post("/api/auth/login", json={"phone": PHONE, "code": code})
    assert reused.status_code == 422 and reused.json()["error"]["code"] == "code_invalid"


def test_code_expires(client, tmp_path, monkeypatch):
    http, core = client
    assert _register(http, core, tmp_path, PHONE).status_code == 200
    http.post("/api/auth/logout")
    monkeypatch.setenv("AUTH_CODE_TTL_SECONDS", "-1")  # 立刻过期
    _send(http, PHONE, "login")
    expired = http.post("/api/auth/login", json={"phone": PHONE, "code": _latest_code(tmp_path, "login")})
    assert expired.status_code == 422 and expired.json()["error"]["code"] == "code_invalid"


def test_wrong_code_attempts_are_capped(client, tmp_path, monkeypatch):
    http, core = client
    assert _register(http, core, tmp_path, PHONE).status_code == 200
    http.post("/api/auth/logout")
    monkeypatch.setenv("AUTH_CODE_MAX_ATTEMPTS", "3")
    _send(http, PHONE, "login")

    for _ in range(3):
        wrong = http.post("/api/auth/login", json={"phone": PHONE, "code": "999999"})
        assert wrong.json()["error"]["code"] == "code_invalid"
    # 超过上限：连正确的码也不给了（必须重新获取）
    capped = http.post("/api/auth/login", json={"phone": PHONE, "code": _latest_code(tmp_path, "login")})
    assert capped.status_code == 429 and capped.json()["error"]["code"] == "code_attempts_exceeded"


def test_code_resend_cooldown(client, tmp_path, monkeypatch):
    http, _core = client
    monkeypatch.setenv("AUTH_CODE_MIN_INTERVAL", "60")
    assert _send(http, PHONE, "login")["retry_after"] == 60
    too_soon = http.post("/api/auth/code", json={"phone": PHONE, "purpose": "login"})
    assert too_soon.status_code == 429 and too_soon.json()["error"]["code"] == "code_too_soon"


def test_code_hourly_cap(client, tmp_path, monkeypatch):
    http, _core = client
    monkeypatch.setenv("AUTH_CODE_MAX_PER_HOUR", "2")
    assert _send(http, PHONE, "login")["retry_after"] == 0
    assert _send(http, PHONE, "login")["retry_after"] == 0
    blocked = http.post("/api/auth/code", json={"phone": PHONE, "purpose": "login"})
    assert blocked.status_code == 429 and blocked.json()["error"]["code"] == "code_rate_limited"


def test_code_is_never_stored_in_plaintext(client, tmp_path):
    """验证码只存哈希：库里那一列必须是 64 位 hex，且不含码原文。"""
    http, core = client
    assert _register(http, core, tmp_path, PHONE).status_code == 200
    _send(http, PHONE, "login")
    code = _latest_code(tmp_path, "login")

    with sqlite3.connect(tmp_path / "auth.sqlite3") as conn:
        rows = conn.execute("SELECT code_hash FROM phone_codes").fetchall()
    assert rows, "应该留下了验证码记录"
    for (stored,) in rows:
        assert len(stored) == 64 and all(char in "0123456789abcdef" for char in stored)
        assert code not in stored
    assert code not in (tmp_path / "auth.sqlite3").read_bytes().decode("latin-1")


def test_send_code_never_returns_the_code_itself(client, tmp_path):
    """接口不回传验证码 —— 否则 mock 通道就变成了「谁都能登录」的后门。"""
    http, _core = client
    payload = _send(http, PHONE, "login")
    assert payload["mock"] is True and payload["channel"] == "mock"
    assert "code" not in payload
    assert _latest_code(tmp_path, "login") not in json.dumps(payload)


# ------------------------------------------------------------------ 手机号

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("13812345678", "13812345678"),
        ("+8613812345678", "13812345678"),
        ("8613812345678", "13812345678"),
        ("138 1234 5678", "13812345678"),
        ("138-1234-5678", "13812345678"),
    ],
)
def test_phone_normalization(client, raw, expected):
    _http, core = client
    assert core.normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", ["", "12345", "12812345678", "1381234567", "abcdefghijk"])
def test_bad_phone_rejected(client, raw, tmp_path):
    http, _core = client
    response = http.post("/api/auth/code", json={"phone": raw, "purpose": "login"})
    assert response.status_code == 422 and response.json()["error"]["code"] == "invalid_phone"


def test_username_login_is_gone(client, tmp_path):
    """用户名登录已移除：旧客户端再发 username/password 会因缺手机号被拒。"""
    http, core = client
    assert _register(http, core, tmp_path, PHONE, password="passw0rd!").status_code == 200
    http.post("/api/auth/logout")
    legacy = http.post("/api/auth/login", json={"username": PHONE, "password": "passw0rd!"})
    assert legacy.status_code == 422 and legacy.json()["error"]["code"] == "invalid_phone"


# ------------------------------------------------------------------ 密码（辅路径）

def test_password_login_works_when_password_was_set(client, tmp_path):
    http, core = client
    assert _register(http, core, tmp_path, PHONE, password="passw0rd!").status_code == 200
    assert http.get("/api/auth/me").json()["user"]["has_password"] is True
    http.post("/api/auth/logout")
    ok = http.post("/api/auth/login", json={"phone": PHONE, "password": "passw0rd!"})
    assert ok.status_code == 200, ok.text


def test_password_login_failures_are_generic(client, tmp_path):
    """不区分「号没注册 / 没设过密码 / 密码错」—— 否则手机号是否注册就被问出来了。"""
    http, core = client
    assert _register(http, core, tmp_path, PHONE).status_code == 200          # 没设密码
    assert _register(http, core, tmp_path, PHONE2, password="passw0rd!").status_code == 200
    http.post("/api/auth/logout")

    no_password = http.post("/api/auth/login", json={"phone": PHONE, "password": "passw0rd!"})
    wrong = http.post("/api/auth/login", json={"phone": PHONE2, "password": "bad-pass"})
    missing = http.post("/api/auth/login", json={"phone": "13700000000", "password": "bad-pass"})
    codes = {no_password.json()["error"]["code"], wrong.json()["error"]["code"], missing.json()["error"]["code"]}
    assert codes == {"auth_failed"}

    # 连续失败触发限速
    statuses = [http.post("/api/auth/login", json={"phone": PHONE2, "password": "bad-pass"}).status_code for _ in range(6)]
    assert 429 in statuses


def test_password_never_stored_in_plaintext(client, tmp_path):
    http, core = client
    assert _register(http, core, tmp_path, PHONE, password="SuperSecret1").status_code == 200
    assert b"SuperSecret1" not in (tmp_path / "auth.sqlite3").read_bytes()


def test_change_password_with_old_password(client, tmp_path):
    http, core = client
    assert _register(http, core, tmp_path, PHONE, password="passw0rd!").status_code == 200

    bad = http.post("/api/auth/password", json={"old_password": "nope", "new_password": "newpass123"})
    assert bad.status_code == 401 and bad.json()["error"]["code"] == "auth_failed"

    ok = http.post("/api/auth/password", json={"old_password": "passw0rd!", "new_password": "newpass123"})
    assert ok.status_code == 200, ok.text
    http.post("/api/auth/logout")
    assert http.post("/api/auth/login", json={"phone": PHONE, "password": "newpass123"}).status_code == 200
    assert http.post("/api/auth/login", json={"phone": PHONE, "password": "passw0rd!"}).status_code == 401


def test_set_password_with_code_for_account_without_password(client, tmp_path):
    """没设过密码的账号：用验证码首次设密码（这是主路径注册后的常见状态）。"""
    http, core = client
    assert _register(http, core, tmp_path, PHONE).status_code == 200  # 已登录
    _send(http, PHONE, "change_password")
    ok = http.post(
        "/api/auth/password",
        json={"new_password": "newpass123", "code": _latest_code(tmp_path, "change_password")},
    )
    assert ok.status_code == 200, ok.text
    http.post("/api/auth/logout")
    assert http.post("/api/auth/login", json={"phone": PHONE, "password": "newpass123"}).status_code == 200


def test_change_password_needs_some_proof(client, tmp_path):
    http, core = client
    assert _register(http, core, tmp_path, PHONE).status_code == 200
    empty = http.post("/api/auth/password", json={"new_password": "newpass123"})
    assert empty.status_code == 422 and empty.json()["error"]["code"] == "empty_input"
    # 未登录不能改
    http.post("/api/auth/logout")
    anonymous = http.post("/api/auth/password", json={"old_password": "x", "new_password": "newpass123"})
    assert anonymous.status_code == 401


# ------------------------------------------------------------------ 邀请码（管理员）

def test_admin_invite_requires_owner(client, tmp_path):
    http, core = client
    assert http.post("/api/admin/invites", json={"note": "x"}).status_code == 401

    assert _register(http, core, tmp_path, PHONE).status_code == 200
    made = http.post("/api/admin/invites", json={"note": "给同事"})
    assert made.status_code == 200 and made.json()["code"].startswith("fzx-")

    http.post("/api/auth/logout")
    assert _register(http, core, tmp_path, PHONE2, invite=made.json()["code"]).status_code == 200
    assert http.post("/api/admin/invites", json={"note": "x"}).status_code == 403


# ------------------------------------------------------------------ 库结构迁移

def test_legacy_username_db_is_refused_not_guessed(tmp_path, monkeypatch):
    """v1 库里有账号时**大声失败**，不猜「用户名就是手机号」。"""
    db = tmp_path / "auth.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'member',
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                last_login_at TEXT
            );
            INSERT INTO users (username, password_hash, password_salt, created_at)
            VALUES ('olduser', 'h', 's', '2026-01-01T00:00:00+0800');
            """
        )
        conn.commit()

    monkeypatch.setenv("AUTH_DB", str(db))
    import app.auth.core as core

    importlib.reload(core)
    with pytest.raises(RuntimeError) as excinfo:
        core.connect()
    assert "v1" in str(excinfo.value) and "1 个账号" in str(excinfo.value)


def test_empty_legacy_db_is_rebuilt(tmp_path, monkeypatch):
    """v1 库但没有账号 → 直接重建，没有信息可丢。"""
    db = tmp_path / "auth.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'member',
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                last_login_at TEXT
            );
            """
        )
        conn.commit()

    monkeypatch.setenv("AUTH_DB", str(db))
    import app.auth.core as core

    importlib.reload(core)
    with core.connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
    assert "phone" in columns and "username" not in columns


# ------------------------------------------------------------------ 配置模板守卫

def test_env_template_has_no_inline_comments():
    """`.env.example` 里不许出现行内注释。

    本项目的 `.env` 解析是「极简解析」，**不剥离行内注释**：写成
    `AUTH_CODE_TTL_SECONDS=300  # 有效期` 会让值变成 `'300  # 有效期'`，
    然后 `int()` 直接抛 ValueError（2026-09-27 实测踩到，21 条测试一起变红）。
    配置看着对、运行时才炸，所以用守卫钉住。
    """
    from pathlib import Path

    template = Path(__file__).resolve().parents[1] / ".env.example"
    offenders = [
        line
        for line in template.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#") and "#" in line.split("=", 1)[-1]
    ]
    assert offenders == [], f"这些行把注释写在了值后面，会被当成值的一部分：{offenders}"
