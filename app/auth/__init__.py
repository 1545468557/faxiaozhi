"""账号底座（2026-09-27 改造）：邀请码注册 · **手机号 + 验证码 / 密码** 登录 · HttpOnly Cookie 会话。

口径：
- 账号标识是**手机号**；
- 登录两条路：`login_with_code`（主，短信验证码）与 `login_with_password`（辅，仅对注册时设过密码的账号）；
- 注册仍需**邀请码**（内部试用口径不变）；
- 密码用**标准库 scrypt** 存储（每用户随机盐，不存明文、不引入新依赖），**可以为空**；
- 验证码**只存 HMAC-SHA256 哈希**，明文只存在于那一次发送里；5 分钟有效、单码最多试错 5 次、同号 60 秒内不可重发；
- 短信通道可插拔（`app/auth/sms.py`）：默认 `mock` 不真发，只写日志与 `data/sms-outbox.jsonl`；
- 会话用随机 token，**库里只存 sha256**；Cookie 为 HttpOnly + SameSite=Lax，7 天；
- 登录失败限速（同一手机号 5 次/分钟）；
- 日志不记密码、验证码、邀请码原文、Cookie token。

**注意**：用户名登录已移除（`login()` 不再存在）。旧 v1 库（username 列）不做猜测式迁移，
详见 `core._migrate_v1_to_v2`。
"""

from .core import (  # noqa: F401
    AuthError,
    change_password,
    current_user_id,
    generate_invite,
    issue_code,
    list_invites,
    login_with_code,
    login_with_password,
    logout,
    mask_phone,
    me,
    normalize_phone,
    register,
    session_from_request,
)
