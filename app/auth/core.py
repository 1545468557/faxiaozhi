"""账号与登录的核心实现（单模块：表少、语句简单，不引入 ORM）。"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = 1


class AuthError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _db_path() -> Path:
    configured = os.environ.get("AUTH_DB", "").strip()
    path = Path(configured).expanduser() if configured else ROOT / "data" / "auth.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _session_days() -> int:
    return int(os.environ.get("AUTH_SESSION_DAYS", "7") or 7)


def _max_attempts_per_minute() -> int:
    return int(os.environ.get("AUTH_LOGIN_MAX_PER_MINUTE", "5") or 5)


def _cookie_secure() -> bool:
    return str(os.environ.get("AUTH_COOKIE_SECURE", "0")).strip().lower() in {"1", "true", "yes", "on"}


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            password_salt TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'member',
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            last_login_at TEXT
        );
        CREATE TABLE IF NOT EXISTS invites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code_hash TEXT NOT NULL UNIQUE,
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            expires_at TEXT,
            used_by_user_id INTEGER,
            used_at TEXT
        );
        CREATE TABLE IF NOT EXISTS auth_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_hash TEXT NOT NULL UNIQUE,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            last_seen_at TEXT
        );
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """
    )
    conn.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    conn.commit()
    return conn


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


# ------------------------------------------------------------------ 密码

def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """标准库 scrypt（n=2^14, r=8, p=1）；返回 (hash_hex, salt_hex)。"""
    salt_bytes = bytes.fromhex(salt) if salt else secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt_bytes, n=2**14, r=8, p=1, dklen=32)
    return digest.hex(), salt_bytes.hex()


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    candidate, _ = hash_password(password, salt)
    return hmac.compare_digest(candidate, password_hash)


def check_password_strength(password: str) -> None:
    if len(password or "") < 8:
        raise AuthError("password_weak", "密码至少 8 位。")


# ------------------------------------------------------------------ 邀请码

def _code_hash(code: str) -> str:
    return hashlib.sha256(code.strip().encode("utf-8")).hexdigest()


def generate_invite(note: str = "", expires_days: int = 30) -> dict[str, Any]:
    """生成一次性邀请码。**明文只返回这一次**，库里只存哈希。"""
    code = f"fzx-{secrets.token_urlsafe(9)}"
    expires = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(time.time() + max(1, expires_days) * 86400))
    with connect() as conn:
        conn.execute(
            "INSERT INTO invites (code_hash, note, created_at, expires_at) VALUES (?,?,?,?)",
            (_code_hash(code), note, _now(), expires),
        )
        conn.commit()
    return {"code": code, "note": note, "expires_at": expires}


def list_invites() -> list[dict[str, Any]]:
    """列出邀请码（**不含码原文**）。"""
    with connect() as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT id, note, created_at, expires_at, used_by_user_id, used_at"
                " FROM invites ORDER BY id DESC LIMIT 100"
            )
        ]


def _consume_invite(conn: sqlite3.Connection, code: str) -> None:
    row = conn.execute("SELECT * FROM invites WHERE code_hash = ?", (_code_hash(code or ""),)).fetchone()
    # 错误提示统一，避免枚举：无效 / 已用 / 过期 都是一个说法
    generic = AuthError("invite_invalid", "邀请码无效或已使用。")
    if row is None or row["used_at"] is not None:
        raise generic
    if row["expires_at"] and row["expires_at"] < _now():
        raise generic
    conn.execute("UPDATE invites SET used_at = ? WHERE id = ?", (_now(), row["id"]))


# ------------------------------------------------------------------ 注册 / 登录

def register(invite_code: str, username: str, password: str) -> dict[str, Any]:
    username = (username or "").strip()
    if not (3 <= len(username) <= 32):
        raise AuthError("invalid_username", "用户名长度需要在 3-32 位之间。")
    if not username.replace("_", "").replace("-", "").isalnum():
        raise AuthError("invalid_username", "用户名只能是字母、数字、下划线或短横线。")
    check_password_strength(password)
    password_hash, salt = hash_password(password)
    with connect() as conn:
        _consume_invite(conn, invite_code)
        exists = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if exists is not None:
            raise AuthError("username_taken", "这个用户名已经被占用。")
        first_user = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 0
        conn.execute(
            "INSERT INTO users (username, password_hash, password_salt, role, status, created_at) VALUES (?,?,?,?,?,?)",
            (username, password_hash, salt, "owner" if first_user else "member", "active", _now()),
        )
        user_id = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()["id"]
        conn.execute("UPDATE invites SET used_by_user_id = ? WHERE code_hash = ?", (user_id, _code_hash(invite_code)))
        conn.commit()
    return {"id": user_id, "username": username, "role": "owner" if first_user else "member"}


_ATTEMPTS: dict[str, list[float]] = {}


def _rate_limit(username: str) -> None:
    now = time.time()
    window = [stamp for stamp in _ATTEMPTS.get(username, []) if now - stamp < 60]
    if len(window) >= _max_attempts_per_minute():
        raise AuthError("rate_limited", "尝试次数过多，请等一分钟再试。", status=429)
    window.append(now)
    _ATTEMPTS[username] = window


def _issue_session(conn: sqlite3.Connection, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(time.time() + _session_days() * 86400))
    conn.execute(
        "INSERT INTO auth_sessions (token_hash, user_id, created_at, expires_at, last_seen_at) VALUES (?,?,?,?,?)",
        (hashlib.sha256(token.encode()).hexdigest(), user_id, _now(), expires, _now()),
    )
    conn.commit()
    return token


def login(username: str, password: str) -> tuple[dict[str, Any], int, str]:
    """返回 (用户, 会话有效秒数, token)。失败一律 `auth_failed`，不区分用户是否存在。"""
    username = (username or "").strip()
    _rate_limit(username)
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if row is None or not verify_password(password or "", row["password_hash"], row["password_salt"]):
            raise AuthError("auth_failed", "用户名或密码不对。", status=401)
        if row["status"] != "active":
            raise AuthError("user_disabled", "这个账号已被停用。", status=403)
        conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (_now(), row["id"]))
        token = _issue_session(conn, row["id"])
        return {"id": row["id"], "username": row["username"], "role": row["role"]}, _session_days() * 86400, token


def session_from_request(cookie_token: str | None) -> dict[str, Any] | None:
    """用 Cookie 里的 token 换当前用户；无效/过期返回 None。"""
    if not cookie_token:
        return None
    with connect() as conn:
        row = conn.execute(
            """SELECT s.id AS sid, s.expires_at, u.id, u.username, u.role, u.status
               FROM auth_sessions s JOIN users u ON u.id = s.user_id
               WHERE s.token_hash = ?""",
            (hashlib.sha256(cookie_token.encode()).hexdigest(),),
        ).fetchone()
        if row is None or row["expires_at"] < _now() or row["status"] != "active":
            return None
        conn.execute("UPDATE auth_sessions SET last_seen_at = ? WHERE id = ?", (_now(), row["sid"]))
        conn.commit()
        return {"id": row["id"], "username": row["username"], "role": row["role"], "expires_at": row["expires_at"]}


def current_user_id(cookie_token: str | None) -> int | None:
    user = session_from_request(cookie_token)
    return int(user["id"]) if user else None


def logout(cookie_token: str | None) -> None:
    if not cookie_token:
        return
    with connect() as conn:
        token_hash = hashlib.sha256(cookie_token.encode()).hexdigest()
        conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,))
        conn.commit()


def me(cookie_token: str | None) -> dict[str, Any]:
    user = session_from_request(cookie_token)
    if user is None:
        raise AuthError("auth_required", "请先登录。", status=401)
    return user


def cookie_flags() -> str:
    base = f"Path=/; HttpOnly; SameSite=Lax; Max-Age={_session_days() * 86400}"
    return base + ("; Secure" if _cookie_secure() else "")


def change_password(cookie_token: str | None, old_password: str, new_password: str) -> None:
    """改密码：校验旧密码 → 更新 scrypt 哈希 → **作废该用户的其它会话**（当前这条保留）。"""
    user = session_from_request(cookie_token)
    if user is None:
        raise AuthError("auth_required", "请先登录。", status=401)
    check_password_strength(new_password)
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
        if row is None or not verify_password(old_password or "", row["password_hash"], row["password_salt"]):
            raise AuthError("auth_failed", "原密码不对。", status=401)
        password_hash, salt = hash_password(new_password)
        conn.execute("UPDATE users SET password_hash = ?, password_salt = ? WHERE id = ?", (password_hash, salt, user["id"]))
        keep = hashlib.sha256((cookie_token or "").encode()).hexdigest()
        conn.execute("DELETE FROM auth_sessions WHERE user_id = ? AND token_hash != ?", (user["id"], keep))
        conn.commit()
