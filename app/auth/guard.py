"""登录门与数据归属（2026-09-27 隔离改造）。

## 为什么是中间件，而不是给每个路由加依赖

勘探结论：8010 有 **~25 个业务接口零鉴权**，v3 连 `app.auth` 都没 import。
逐个加装饰器的问题是**漏一个就是一个越权口子**，而且以后新加接口还会忘。
所以门口只做一件事：`/api/**` 除白名单外一律要求有效登录态。
覆盖面由**路径规则**决定，不依赖人的记性。

## 门口挡住的是「没登录的人」，不是「登了录但拿着别人 id 的人」

这两件事必须分开想：
- 登录门 → 解决「游客能不能用」；
- 归属校验 → 解决「A 能不能拿 B 的 sid / 文件名」。
后者必须在**每个数据入口**单独校验（`owns()`），中间件帮不上忙。

## 策略开关

`AUTH_REQUIRE_LOGIN=0` 可以关掉登录门（本地开发/跑离线验收脚本方便）。
关掉时**大声警告**，并且所有数据会落到同一个 `_anonymous` 桶里 —— 也就是回到改造前的口径。
**公网部署必须保持开启**（默认就是开）。
"""

from __future__ import annotations

import contextvars
import hashlib
import hmac
import logging
import os
import re
import secrets
import sqlite3
import time
from datetime import date
from pathlib import Path
from typing import Any

from fastapi.responses import JSONResponse

LOG = logging.getLogger("faxiaozhi.guard")

COOKIE_NAME = "fzx_session"
GUEST_COOKIE_NAME = "fzx_guest"
GUEST_DAYS = 30

#: 未登录时的数据桶名。只在 `AUTH_REQUIRE_LOGIN=0` 时会出现 ——
#: 这时所有人共用一个桶，也就是改造前的口径。
ANONYMOUS = "_anonymous"

# 白名单：只放「登录本身」和「不含任何用户数据的探活/能力声明」。
# 法规库（/api/statutes）**没有**放进来 —— 公开部署下最安全的默认是「除登录外全要登录」，
# 以后想让法规查找免登录，在这里加一条前缀即可（一行的事）。
PUBLIC_PREFIXES: tuple[str, ...] = ("/api/auth/",)
PUBLIC_EXACT: frozenset[str] = frozenset({"/api/health", "/api/bootstrap"})

#: 需要登录的**非** `/api` 前缀。
#: 导出文件下载挂在 `/exports/{name}`，改造前它在登录门之外 ——
#: 也就是说只要知道（或猜到）文件名就能下载，绕过一切鉴权。
PROTECTED_EXTRA_PREFIXES: tuple[str, ...] = ("/exports/",)

_warned = False


def require_login_enabled() -> bool:
    """登录门是否开启。默认开；只有显式写 0/false/no/off 才关。"""
    return str(os.environ.get("AUTH_REQUIRE_LOGIN", "1")).strip().lower() not in {"0", "false", "no", "off"}


def guest_mode_enabled() -> bool:
    return str(os.environ.get("AUTH_GUEST_MODE", "0")).strip().lower() in {"1", "true", "yes", "on"}


def assert_guest_ready() -> None:
    if guest_mode_enabled() and len(os.environ.get("GUEST_COOKIE_SECRET", "")) < 32:
        raise RuntimeError("AUTH_GUEST_MODE=1 时必须配置至少 32 字符的 GUEST_COOKIE_SECRET")


def _warn_once() -> None:
    global _warned
    if _warned:
        return
    _warned = True
    LOG.warning(
        "⚠ AUTH_REQUIRE_LOGIN=0：登录门已关闭，所有数据落在同一个 _anonymous 桶里，"
        "任何访问者都能看到全部数据。这只适合本机开发，**公网部署必须打开**。"
    )


# ------------------------------------------------------------------ 当前用户

def cookie_token(request: Any) -> str | None:
    """从 Cookie 里取会话 token（取不到返回 None）。"""
    return _cookie(request, COOKIE_NAME)


def _cookie(request: Any, target: str) -> str | None:
    for part in (request.headers.get("cookie") or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == target:
            return value or None
    return None


def _guest_owner(token: str | None) -> str | None:
    if not token or len(token) > 200:
        return None
    parts = token.split(".")
    if len(parts) != 4 or parts[0] != "v1" or not parts[1].isdigit():
        return None
    issued = int(parts[1])
    if issued > time.time() + 60 or time.time() - issued > GUEST_DAYS * 86400:
        return None
    if not re.fullmatch(r"[0-9a-f]{32}", parts[2]) or not re.fullmatch(r"[0-9a-f]{64}", parts[3]):
        return None
    payload = ".".join(parts[:3])
    expected = hmac.new(os.environ["GUEST_COOKIE_SECRET"].encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, parts[3]):
        return None
    return "g_" + hashlib.sha256(parts[2].encode()).hexdigest()[:32]


def request_principal(request: Any) -> dict[str, Any] | None:
    """Use a real account when present; otherwise assign a signed guest identity."""
    if hasattr(request.state, "fzx_principal"):
        return request.state.fzx_principal
    user = current_user(request)
    if user is not None or not guest_mode_enabled():
        request.state.fzx_principal = user
        return user
    assert_guest_ready()
    token = _cookie(request, GUEST_COOKIE_NAME)
    owner = _guest_owner(token)
    if owner is None:
        payload = f"v1.{int(time.time())}.{secrets.token_hex(16)}"
        signature = hmac.new(os.environ["GUEST_COOKIE_SECRET"].encode(), payload.encode(), hashlib.sha256).hexdigest()
        token = f"{payload}.{signature}"
        owner = _guest_owner(token)
        request.state.fzx_new_guest_cookie = token
    principal = {"id": owner, "role": "guest"}
    request.state.fzx_principal = principal
    return principal


def attach_guest_cookie(response: Any, request: Any) -> Any:
    token = getattr(request.state, "fzx_new_guest_cookie", None)
    if token:
        secure = str(os.environ.get("AUTH_COOKIE_SECURE", "0")).lower() in {"1", "true", "yes", "on"}
        response.set_cookie(GUEST_COOKIE_NAME, token, max_age=GUEST_DAYS * 86400,
                            httponly=True, secure=secure, samesite="lax", path="/")
    return response


def _costly_request(method: str, path: str) -> bool:
    if method.upper() != "POST":
        return False
    if path in {"/api/chat", "/api/statutes/agent"} or re.fullmatch(r"/api/runs/[^/]+/resume", path):
        return True
    match = re.fullmatch(r"/api/session/[^/]+/(.+)", path)
    return bool(match and match.group(1) in {
        "message", "resume", "retry", "checkpoint", "supplement", "consult/answer", "contract/review",
    })


def guest_limit_response(request: Any) -> JSONResponse | None:
    """Bound paid calls across both backends, even if guests discard cookies."""
    principal = request_principal(request)
    if not principal or principal.get("role") != "guest" or not _costly_request(request.method, request.url.path):
        return None
    owner = owner_key(principal)
    per_guest = max(1, int(os.environ.get("GUEST_DAILY_LIMIT", "20")))
    global_limit = max(1, int(os.environ.get("GUEST_GLOBAL_DAILY_LIMIT", "200")))
    configured = os.environ.get("GUEST_QUOTA_DB", "").strip()
    db = (Path(configured).expanduser() if configured else
          Path(__file__).resolve().parents[2] / "data" / "guest_quota.sqlite3")
    db.parent.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    with sqlite3.connect(db, timeout=10) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS guest_usage (
            day TEXT NOT NULL, owner TEXT NOT NULL, calls INTEGER NOT NULL,
            PRIMARY KEY(day, owner)
        )""")
        conn.execute("BEGIN IMMEDIATE")
        counts = {}
        for key in (owner, "*"):
            row = conn.execute("SELECT calls FROM guest_usage WHERE day=? AND owner=?", (today, key)).fetchone()
            counts[key] = row[0] if row else 0
        if counts[owner] >= per_guest or counts["*"] >= global_limit:
            conn.rollback()
            return JSONResponse(status_code=429, content={"error": {
                "code": "rate_limited", "message": "今日体验次数已用完，请明天再试。",
            }})
        for key in (owner, "*"):
            conn.execute("""INSERT INTO guest_usage (day, owner, calls) VALUES (?,?,1)
                ON CONFLICT(day,owner) DO UPDATE SET calls=calls+1""", (today, key))
        conn.commit()
    return None


def current_user(request: Any, *, touch: bool = False) -> dict[str, Any] | None:
    """当前登录用户；未登录/已过期返回 None。

    `touch=False`（默认）不更新 `last_seen_at` —— 中间件每个请求都会调用这里，
    带写操作会变成「每个 API 调用一次 SQLite 写」，没必要。
    需要续期的场景（`/api/auth/me`）显式传 `touch=True`。
    """
    from . import core

    try:
        return core.session_from_request(cookie_token(request), touch=touch)
    except Exception:  # noqa: BLE001 —— 账号库异常不应该让整个服务 500
        LOG.exception("读取登录态失败")
        return None


def blocked_response(request: Any) -> JSONResponse | None:
    """中间件用：该拦就返回 401 响应，放过就返回 None。"""
    if guest_mode_enabled() and request.url.path.startswith("/api/auth/"):
        return JSONResponse(status_code=404, content={"error": {"code": "not_found", "message": "接口不存在。"}})
    if not require_login_enabled():
        _warn_once()
        return None
    path = request.url.path
    if not (
        path == "/api"
        or path.startswith("/api/")
        or any(path.startswith(prefix) for prefix in PROTECTED_EXTRA_PREFIXES)
    ):
        return None  # 静态页面/文档不拦（它们的接口调用会被拦，够用）
    if any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES) or path in PUBLIC_EXACT:
        return None
    if current_user(request) is not None or guest_mode_enabled():
        return None
    return JSONResponse(
        status_code=401,
        content={"error": {"code": "auth_required", "message": "请先登录。"}},
    )


# ------------------------------------------------------------------ 归属

def owner_key(user: dict[str, Any] | None) -> str:
    """数据归属用的桶名。

    未登录（只在关了登录门时可能发生）落到 `_anonymous`：
    这样「没账号」时行为与改造前一致（所有人共用一个桶），
    而「有账号」时天然按用户分开。
    """
    if not user:
        return ANONYMOUS
    return str(user["id"])


def owner_dir(root: Path, user: dict[str, Any] | None) -> Path:
    """按用户切子目录（`root/<user_id>/`）。**目录名只由整数主键或 `_anonymous` 组成**，
    不含任何用户输入，所以不会引入路径穿越。"""
    path = Path(root) / owner_key(user)
    path.mkdir(parents=True, exist_ok=True)
    return path


def owner_dir_for(root: Path, owner: str) -> Path:
    """按归属桶名切子目录（`root/<owner>/`）。

    目录名只由整数主键或 `_anonymous` 组成，**不含任何用户输入**，不会引入路径穿越。
    """
    path = Path(root) / owner
    path.mkdir(parents=True, exist_ok=True)
    return path


def current_owner_dir(root: Path) -> Path:
    """当前请求的归属子目录 —— 导出文件按这个分桶。

    「按人分目录」比「写完之后再过滤」更稳：别人的文件**根本不在查找范围内**，
    所以列表、下载、覆盖写都不需要额外的归属判断。
    """
    return owner_dir_for(root, current_owner())


def owns(record_owner: Any, user: dict[str, Any] | None) -> bool:
    """判断一条记录的归属是否属于当前用户。

    `record_owner` 允许是 int / str / None：
    - None 表示「无主数据」，**任何人都拿不到**（宁可拒绝，不猜归属）；
    - 与 `owner_key(user)` 相等才算自己的。
    """
    if record_owner is None:
        return False
    return str(record_owner) == owner_key(user)


# ------------------------------------------------------------------ 会话归属

#: 8010 的会话类接口**全部**是 `/api/session/{sid}/...` 这个形状
#: （state / message / resume / events / upload / supplement / conflict / material /
#: materials / evidence / export / consult / contract / degradation / retry / checkpoint）。
#: 所以在中间件里按这个形状做一次归属校验，就能覆盖全部 ——
#: 而不是去改 17 个 `STORE.get()` 调用点（改 17 处**一定会漏一处**）。
_SESSION_PATH = re.compile(r"^/api/session/(?P<sid>[^/]+)")


def session_id_in_path(path: str) -> str | None:
    """从 `/api/session/{sid}/...` 里取出 sid；不是这个形状就返回 None。

    注意 `/api/sessions`（复数，v3 的列表接口）不会命中 —— 正则要求 `session/` 后面还有一段。
    """
    match = _SESSION_PATH.match(path)
    return match.group("sid") if match else None


def session_not_found_response() -> JSONResponse:
    """与会话层 `STORE.get()` 失败**完全同形**的 404。

    归属不符与「真的不存在」必须回同一个响应 —— 回 403 等于承认「这个 sid 是真的，只是不是你的」，
    那本身就是信息泄漏。这里复用 `ApiError` 以保证状态码与错误体不会和会话层漂移。
    """
    from ..errors import ApiError

    exc = ApiError("session_not_found", "会话不存在或已结束，请重新开始。")
    return JSONResponse(status_code=exc.http_status, content=exc.body())


# ------------------------------------------------------------------ 当前请求的归属

#: 当前请求的数据归属（`owner_key(current_user)`）。
#:
#: 为什么用 contextvar 而不是给 19 个 `STORE.get()` 调用点逐个加参数：
#: 那些调用点分散在 800 多行的 `app/server.py` 里，逐个改**一定会漏一处**，
#: 而漏掉的那一处就是越权口子。改成「中间件按请求设一次、会话层按需读取」之后，
#: 归属由**一处**决定，新增接口也自动带上。
#:
#: 语义：不在请求上下文里（后台任务、脚本、测试直接构造）时是 `_anonymous` ——
#: 也就是改造前的口径，不会把无辜的数据判成越权。
_CURRENT_OWNER: contextvars.ContextVar[str] = contextvars.ContextVar("fzx_owner", default=ANONYMOUS)


def set_current_owner(owner: str) -> None:
    _CURRENT_OWNER.set(owner)


def current_owner() -> str:
    return _CURRENT_OWNER.get()
