"""账号底座（迭代 2-1）：邀请码注册 · 用户名密码登录 · HttpOnly Cookie 会话。

口径（产品经理确认的推荐项）：
- 登录方式：**邀请码 + 用户名 + 密码**（不接短信/邮箱/第三方，0 元）；
- 未登录不能用（本轮先提供能力，拦截在 2-2 统一加）；
- 密码用**标准库 scrypt** 存储（每用户随机盐，不存明文、不引入新依赖）；
- 会话用随机 token，**库里只存 sha256**；Cookie 为 HttpOnly + SameSite=Lax，7 天；
- 登录失败限速（同一用户名 5 次/分钟）；
- 日志不记密码、邀请码原文、Cookie token。
"""

from .core import (  # noqa: F401
    AuthError,
    current_user_id,
    generate_invite,
    list_invites,
    login,
    logout,
    me,
    register,
    session_from_request,
)
