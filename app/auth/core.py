"""账号与登录的核心实现（单模块：表少、语句简单，不引入 ORM）。

口径（2026-09-27 改造）：**手机号 + 验证码（主）/ 密码（辅）**，注册仍需邀请码。

- 账号标识从「用户名」换成**手机号**：`users.phone` 唯一。
- 登录两条路：`login_with_code`（主）与 `login_with_password`（辅，仅对注册时设过密码的账号可用）。
- 密码可以为空 —— 只走验证码注册的账号没有密码，这不等于「弱账号」，因为它的凭据是**手机号占有**。
- 验证码**只存哈希**（HMAC-SHA256，密钥在 env 或 `meta` 表），明文只存在于那一次发送里。
- 旧的用户名账号**不做猜测式迁移**：见 `_migrate_v1_to_v2`。

表结构版本：1（用户名）→ 2（手机号）。
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = 2

# 验证码用途：注册 / 登录 / 改密码。**用途隔离**，注册的码不能拿来登录。
PURPOSES = ("register", "login", "change_password")

_PHONE_RE = re.compile(r"^1[3-9]\d{9}$")
_CODE_RE = re.compile(r"^[0-9]+$")


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


# ---- 验证码策略（都做成函数，测试里可以直接改环境变量） ----

def _code_length() -> int:
    return int(os.environ.get("AUTH_CODE_LENGTH", "6") or 6)


def _code_ttl() -> int:
    """验证码有效期（秒）。默认 5 分钟。"""
    return int(os.environ.get("AUTH_CODE_TTL_SECONDS", "300") or 300)


def _code_max_attempts() -> int:
    """同一个码最多试错几次。"""
    return int(os.environ.get("AUTH_CODE_MAX_ATTEMPTS", "5") or 5)


def _code_min_interval() -> int:
    """同一手机号两次发码的最小间隔（秒）。"""
    return int(os.environ.get("AUTH_CODE_MIN_INTERVAL", "60") or 60)


def _code_max_per_hour() -> int:
    return int(os.environ.get("AUTH_CODE_MAX_PER_HOUR", "5") or 5)


def _code_max_per_day() -> int:
    return int(os.environ.get("AUTH_CODE_MAX_PER_DAY", "10") or 10)


# ------------------------------------------------------------------ 手机号

def normalize_phone(raw: str | None) -> str:
    """归一化为 11 位大陆手机号；带 +86 / 86 / 空格 / 短横线都能认。

    校验不通过就报错 —— 不做「宽松放行」，手机号是本系统的账号主键。
    """
    text = re.sub(r"[\s\-()（）]", "", (raw or "").strip())
    if text.startswith("+86"):
        text = text[3:]
    elif text.startswith("86") and len(text) == 13:
        text = text[2:]
    if not _PHONE_RE.match(text):
        raise AuthError("invalid_phone", "请输入 11 位手机号。")
    return text


def mask_phone(phone: str) -> str:
    """给界面/日志用：138****8888。"""
    return f"{phone[:3]}****{phone[-4:]}" if len(phone) == 11 else phone


# ------------------------------------------------------------------ 连接与表结构

_V2_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT NOT NULL UNIQUE,
    password_hash TEXT,
    password_salt TEXT,
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
CREATE TABLE IF NOT EXISTS phone_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT NOT NULL,
    purpose TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    consumed_at REAL
);
CREATE INDEX IF NOT EXISTS idx_phone_codes_lookup ON phone_codes (phone, purpose, id DESC);
CREATE INDEX IF NOT EXISTS idx_phone_codes_created ON phone_codes (phone, created_at);
"""


def _migrate_v1_to_v2(conn: sqlite3.Connection, columns: set[str]) -> None:
    """v1（username）→ v2（phone）。

    **刻意不自动迁移有数据的旧表**：用户名不是手机号，猜不出对应关系，
    瞎猜会把账号映射到别人的号上。所以有历史账号时**大声失败**，
    由人来决定怎么处理。
    """
    legacy = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    if legacy:
        raise RuntimeError(
            f"账号库还是 v1 结构（username），里面有 {legacy} 个账号，无法自动迁移到手机号："
            "用户名不等于手机号，猜不出对应关系。请先备份 data/auth.sqlite3，"
            "再决定是清空重来，还是手工把每个账号迁到手机号上。"
        )
    # 0 个账号：直接重建，没有信息可丢
    conn.executescript("DROP TABLE users;")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);")

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
    if columns and "phone" not in columns:
        _migrate_v1_to_v2(conn, columns)

    conn.executescript(_V2_SCHEMA)
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    conn.commit()
    return conn


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _public(row: sqlite3.Row) -> dict[str, Any]:
    """给接口用的用户视图：**不含哈希**，手机号带一个打码版给界面显示。"""
    return {
        "id": row["id"],
        "phone": row["phone"],
        "phone_masked": mask_phone(row["phone"]),
        "role": row["role"],
        "has_password": bool(row["password_hash"]),
    }


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


# ------------------------------------------------------------------ 验证码

def _code_secret(conn: sqlite3.Connection) -> bytes:
    """验证码哈希用的服务端密钥：优先 env，否则持久化在 meta 表（首次自动生成）。"""
    configured = os.environ.get("AUTH_CODE_SECRET", "").strip()
    if configured:
        return configured.encode("utf-8")
    row = conn.execute("SELECT value FROM meta WHERE key = 'code_secret'").fetchone()
    if row is not None:
        return bytes.fromhex(row["value"])
    secret = secrets.token_bytes(32)
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('code_secret', ?)", (secret.hex(),))
    conn.commit()
    return secret


def _hash_code(conn: sqlite3.Connection, phone: str, purpose: str, code: str) -> str:
    """验证码只存哈希。带上 phone/purpose 做盐，避免「两个号同一个码」互相顶用。"""
    message = f"{phone}:{purpose}:{code}".encode()
    return hmac.new(_code_secret(conn), message, hashlib.sha256).hexdigest()


def issue_code(raw_phone: str, purpose: str) -> dict[str, Any]:
    """发一条验证码。返回里**不含验证码明文**（明文只在那一次发送里）。

    顺序是「先查限额 → 生成 → 发送 → 落库」，发送失败会把记录撤掉，
    这样用户能立刻重试，不会被一条发不出去的记录卡 60 秒。
    """
    from . import sms

    phone = normalize_phone(raw_phone)
    if purpose not in PURPOSES:
        raise AuthError("invalid_purpose", "验证码用途不对。")

    now = time.time()
    with connect() as conn:
        recent = [
            row["created_at"]
            for row in conn.execute(
                "SELECT created_at FROM phone_codes WHERE phone = ? AND created_at > ? ORDER BY id DESC",
                (phone, now - 86400),
            )
        ]
        if recent:
            wait = _code_min_interval() - (now - max(recent))
            if wait > 0:
                raise AuthError("code_too_soon", f"验证码刚发过，请 {int(wait) + 1} 秒后再试。", status=429)
            if len([stamp for stamp in recent if stamp > now - 3600]) >= _code_max_per_hour():
                raise AuthError("code_rate_limited", "这个手机号短时间内验证码发得太多了，请稍后再试。", status=429)
            if len(recent) >= _code_max_per_day():
                raise AuthError("code_rate_limited", "这个手机号今天验证码发得太多了，请改天再试。", status=429)

        code = f"{secrets.randbelow(10 ** _code_length()):0{_code_length()}d}"
        code_hash = _hash_code(conn, phone, purpose, code)
        # 同号同用途的旧码作废：避免「上一轮验证码还能用」这种绕过
        conn.execute(
            "UPDATE phone_codes SET consumed_at = ? WHERE phone = ? AND purpose = ? AND consumed_at IS NULL",
            (now, phone, purpose),
        )
        cursor = conn.execute(
            "INSERT INTO phone_codes (phone, purpose, code_hash, created_at, expires_at) VALUES (?,?,?,?,?)",
            (phone, purpose, code_hash, now, now + _code_ttl()),
        )
        row_id = cursor.lastrowid
        conn.commit()

    try:
        channel = sms.send_code(phone, code, purpose)
    except Exception:
        with connect() as conn:
            conn.execute("DELETE FROM phone_codes WHERE id = ?", (row_id,))
            conn.commit()
        raise

    return {
        "phone_masked": mask_phone(phone),
        "expires_in": _code_ttl(),
        # 冷却时长下发，界面倒计时以后端为准 —— 免得两边各写一个 60 秒而对不上
        "retry_after": _code_min_interval(),
        "channel": channel,
        # 界面据此显示「当前为模拟发码」的提示；绝不回传验证码本身
        "mock": sms.is_mock(),
    }


def _check_code(conn: sqlite3.Connection, phone: str, purpose: str, code: str) -> int:
    """校验验证码，返回 `phone_codes.id`。

    **不消费**（不写 consumed_at）—— 消费交给调用方在同一个事务里做，
    这样「注册时手机号已占用」这类失败不会白烧掉一个验证码。
    试错会累加次数并立即提交（否则失败回滚会把计数也带走）。
    """
    generic = AuthError("code_invalid", "验证码不对或已过期。")
    text = (code or "").strip()
    if not _CODE_RE.match(text) or len(text) != _code_length():
        raise generic

    row = conn.execute(
        "SELECT * FROM phone_codes WHERE phone = ? AND purpose = ? AND consumed_at IS NULL ORDER BY id DESC LIMIT 1",
        (phone, purpose),
    ).fetchone()
    now = time.time()
    if row is None or row["expires_at"] < now:
        raise generic
    if row["attempts"] >= _code_max_attempts():
        raise AuthError("code_attempts_exceeded", "验证码错误次数过多，请重新获取。", status=429)

    if not hmac.compare_digest(row["code_hash"], _hash_code(conn, phone, purpose, text)):
        conn.execute("UPDATE phone_codes SET attempts = attempts + 1 WHERE id = ?", (row["id"],))
        conn.commit()
        raise generic
    return int(row["id"])


def _consume_code(conn: sqlite3.Connection, code_id: int) -> None:
    conn.execute("UPDATE phone_codes SET consumed_at = ? WHERE id = ?", (time.time(), code_id))


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

def register(
    invite_code: str, raw_phone: str, code: str, password: str | None = None
) -> tuple[dict[str, Any], int, str]:
    """邀请码 + 手机号 + 验证码注册；密码可选。返回 (用户, 会话秒数, token)。

    密码可选是有意的：主要凭据是「手机号 + 收得到验证码」，
    密码只是收不到短信时的备用手段。

    注册成功即登录 —— **会话在同一个事务里签发**。不要「先 register 再用同一个验证码
    调 login_with_code」，那个码已经被消费掉了，必然失败。
    """
    phone = normalize_phone(raw_phone)
    password = (password or "").strip()
    if password:
        check_password_strength(password)

    with connect() as conn:
        # 先验码（证明手机号归属），再查重，最后消费码与邀请码 —— 一次事务，失败即回滚
        code_id = _check_code(conn, phone, "register", code)

        if conn.execute("SELECT id FROM users WHERE phone = ?", (phone,)).fetchone() is not None:
            raise AuthError("phone_taken", "这个手机号已经注册过了，直接登录即可。")

        _consume_invite(conn, invite_code)

        first_user = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 0
        role = "owner" if first_user else "member"
        password_hash, salt = hash_password(password) if password else (None, None)
        cursor = conn.execute(
            "INSERT INTO users (phone, password_hash, password_salt, role, status, created_at) VALUES (?,?,?,?,?,?)",
            (phone, password_hash, salt, role, "active", _now()),
        )
        user_id = int(cursor.lastrowid)
        conn.execute("UPDATE invites SET used_by_user_id = ? WHERE code_hash = ?", (user_id, _code_hash(invite_code)))
        _consume_code(conn, code_id)
        conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (_now(), user_id))
        token = _issue_session(conn, user_id)
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    return _public(row), _session_days() * 86400, token


_ATTEMPTS: dict[str, list[float]] = {}


def _rate_limit(phone: str) -> None:
    now = time.time()
    window = [stamp for stamp in _ATTEMPTS.get(phone, []) if now - stamp < 60]
    if len(window) >= _max_attempts_per_minute():
        raise AuthError("rate_limited", "尝试次数过多，请等一分钟再试。", status=429)
    window.append(now)
    _ATTEMPTS[phone] = window


def _issue_session(conn: sqlite3.Connection, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(time.time() + _session_days() * 86400))
    conn.execute(
        "INSERT INTO auth_sessions (token_hash, user_id, created_at, expires_at, last_seen_at) VALUES (?,?,?,?,?)",
        (hashlib.sha256(token.encode()).hexdigest(), user_id, _now(), expires, _now()),
    )
    conn.commit()
    return token


def login_with_code(raw_phone: str, code: str) -> tuple[dict[str, Any], int, str]:
    """验证码登录（主路径）。"""
    phone = normalize_phone(raw_phone)
    _rate_limit(phone)
    with connect() as conn:
        code_id = _check_code(conn, phone, "login", code)
        row = conn.execute("SELECT * FROM users WHERE phone = ?", (phone,)).fetchone()
        if row is None:
            # 码是对的（手机号归属成立），但这个号没注册过：消费掉，别让它留着可复用
            _consume_code(conn, code_id)
            conn.commit()
            raise AuthError("auth_failed", "这个手机号还没有注册过，请先注册。", status=401)
        if row["status"] != "active":
            raise AuthError("user_disabled", "这个账号已被停用。", status=403)
        _consume_code(conn, code_id)
        conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (_now(), row["id"]))
        token = _issue_session(conn, row["id"])
        return _public(row), _session_days() * 86400, token


def login_with_password(raw_phone: str, password: str) -> tuple[dict[str, Any], int, str]:
    """密码登录（辅路径）。失败提示与「手机号不存在」**完全一致**，防枚举。"""
    phone = normalize_phone(raw_phone)
    _rate_limit(phone)
    generic = AuthError(
        "auth_failed",
        "手机号或密码不对。如果注册时没设密码，请改用验证码登录。",
        status=401,
    )
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE phone = ?", (phone,)).fetchone()
        # 没设过密码的账号也走同一个提示 —— 否则「这个号存不存在」就被问出来了
        if row is None or not row["password_hash"]:
            raise generic
        if not verify_password(password or "", row["password_hash"], row["password_salt"]):
            raise generic
        if row["status"] != "active":
            raise AuthError("user_disabled", "这个账号已被停用。", status=403)
        conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (_now(), row["id"]))
        token = _issue_session(conn, row["id"])
        return _public(row), _session_days() * 86400, token


def session_from_request(cookie_token: str | None, *, touch: bool = False) -> dict[str, Any] | None:
    """用 Cookie 里的 token 换当前用户；无效/过期返回 None。

    `touch=False`（默认）**不写库**：登录门中间件每个请求都要查一次登录态，
    若顺手更新 `last_seen_at` 就变成「每个 API 调用一次 SQLite 写」。
    需要续期的场景（`/api/auth/me`）显式传 `touch=True`。
    """
    if not cookie_token:
        return None
    with connect() as conn:
        row = conn.execute(
            """SELECT s.id AS sid, s.expires_at, u.id, u.phone, u.role, u.status, u.password_hash
               FROM auth_sessions s JOIN users u ON u.id = s.user_id
               WHERE s.token_hash = ?""",
            (hashlib.sha256(cookie_token.encode()).hexdigest(),),
        ).fetchone()
        if row is None or row["expires_at"] < _now() or row["status"] != "active":
            return None
        if touch:
            conn.execute("UPDATE auth_sessions SET last_seen_at = ? WHERE id = ?", (_now(), row["sid"]))
            conn.commit()
        return {**_public(row), "expires_at": row["expires_at"]}


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
    # 这是唯一需要「续期」语义的入口：用户主动问「我是谁」时顺手更新 last_seen_at
    user = session_from_request(cookie_token, touch=True)
    if user is None:
        raise AuthError("auth_required", "请先登录。", status=401)
    return user


def cookie_flags() -> str:
    base = f"Path=/; HttpOnly; SameSite=Lax; Max-Age={_session_days() * 86400}"
    return base + ("; Secure" if _cookie_secure() else "")


def change_password(
    cookie_token: str | None,
    new_password: str,
    old_password: str | None = None,
    code: str | None = None,
) -> None:
    """改密码 / 首次设密码。

    两种验证方式二选一：
    - `old_password`：常规改密；
    - `code`（purpose=change_password）：**只走验证码注册、没有密码**的账号首次设密码，
      或忘记原密码时用。

    改完**作废该用户的其它会话**（当前这条保留）。
    """
    user = session_from_request(cookie_token)
    if user is None:
        raise AuthError("auth_required", "请先登录。", status=401)
    check_password_strength(new_password)

    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
        if row is None:
            raise AuthError("auth_required", "请先登录。", status=401)

        if old_password:
            if not row["password_hash"] or not verify_password(old_password, row["password_hash"], row["password_salt"]):
                raise AuthError("auth_failed", "原密码不对。", status=401)
        elif code:
            code_id = _check_code(conn, row["phone"], "change_password", code)
            _consume_code(conn, code_id)
        else:
            raise AuthError("empty_input", "请填原密码，或改用验证码验证。")

        password_hash, salt = hash_password(new_password)
        conn.execute(
            "UPDATE users SET password_hash = ?, password_salt = ? WHERE id = ?",
            (password_hash, salt, user["id"]),
        )
        keep = hashlib.sha256((cookie_token or "").encode()).hexdigest()
        conn.execute("DELETE FROM auth_sessions WHERE user_id = ? AND token_hash != ?", (user["id"], keep))
        conn.commit()
