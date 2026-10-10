#!/usr/bin/env python3
"""Check the public, login-free deployment before exposing it to traffic.

Only reports whether settings exist; never prints keys, tokens or MCP URLs.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.auth import guard  # noqa: E402
from app.config import get_config  # noqa: E402


#: 各真实短信通道必需的配置项。少一项，切了通道也发不出去。
_SMS_CHANNEL_KEYS: dict[str, tuple[str, ...]] = {
    "aliyun": (
        "SMS_ALIYUN_ACCESS_KEY_ID",
        "SMS_ALIYUN_ACCESS_KEY_SECRET",
        "SMS_SIGN_NAME",
        "SMS_TEMPLATE_CODE",
    ),
    "pnvs": (
        "SMS_ALIYUN_ACCESS_KEY_ID",
        "SMS_ALIYUN_ACCESS_KEY_SECRET",
        "SMS_PNVS_SIGN_NAME",
        "SMS_PNVS_TEMPLATE_CODE",
    ),
}


def _sms_checks(cfg) -> dict[str, bool]:
    """短信通道必须是真的（2026-10-10 补）。

    `SMS_PROVIDER=mock` 是**最危险的一种漏配**，因为它不报错、看起来一切正常：
    它把**明文验证码**写进服务端日志和 `data/sms-outbox.jsonl`，效果等于
    「能拿到日志的人可以登录成任意手机号」。而本地开发长期就停在 mock，
    上线时最容易忘的正好是这一行 —— 所以放进 preflight，让它红。

    切了通道却没配密钥同样要红：那种状态下每个发码请求都会 502 `sms_unavailable`。
    """
    provider = cfg.env("SMS_PROVIDER").strip().lower()
    required = _SMS_CHANNEL_KEYS.get(provider, ())
    return {
        "sms_channel_is_real": provider not in {"", "mock"},
        "sms_channel_configured": bool(required) and all(cfg.env(key).strip() for key in required),
    }


def _sms_gate(cfg) -> tuple[dict[str, bool], list[str]]:
    """短信通道该不该拦住上线，取决于**发码接口是否真的可达**。

    `AUTH_GUEST_MODE=1`（公开体验模式）下，后端的门口会把整个 `/api/auth/*`
    返回 404（`app/auth/guard.py:blocked_response` 第一行）—— 即发码接口
    **根本调不到**，短信通道在这个模式下从头到尾不会被使用。

    所以口径分两种：

    - **登录模式**：短信没配好 = 上线即坏（每个发码请求 502）；`mock` 更糟，
      它把明文验证码写进日志，等于「拿到日志就能登录成任意手机号」。→ 硬拦。
    - **访客模式**：短信暂时用不到 → 只记警告，不拦上线。
      但**切回登录模式时它立刻变回硬拦**，所以警告要留在报告里，
      别让它悄悄消失，否则就成了「埋着的一颗雷」。

    2026-10-10 改：原先不分模式一律硬拦，结果是「明明跑在访客模式、
    根本不发短信」的部署也被堵在 Caddy 外面 —— 把关把错了地方。
    """
    sms = _sms_checks(cfg)
    if not guard.guest_mode_enabled():
        return sms, []
    reasons = {
        "sms_channel_is_real": "SMS_PROVIDER 还是 mock/空",
        "sms_channel_configured": f"SMS_PROVIDER={cfg.env('SMS_PROVIDER') or '(空)'} 的密钥没配齐",
    }
    warnings = [f"{name}：{reasons[name]}（当前 AUTH_GUEST_MODE=1，发码接口不可达，故只警告）"
                for name, passed in sms.items() if not passed]
    return {}, warnings


def inspect() -> dict:
    cfg = get_config()
    checks: dict[str, bool] = {
        "login_gate_enabled": guard.require_login_enabled(),
        "isolated_guest_mode": guard.guest_mode_enabled(),
        "https_only_cookie": cfg.env("AUTH_COOKIE_SECURE").lower() in {"1", "true", "yes", "on"},
        "guest_secret_present": len(cfg.env("GUEST_COOKIE_SECRET")) >= 32,
        "model_key_present": bool(cfg.model_api_key),
        "pkulaw_token_present": bool(cfg.mcp_token),
        "pkulaw_search_configured": bool(cfg.mcp_endpoints().get("search_statutes")),
    }
    sms_checks, warnings = _sms_gate(cfg)
    checks.update(sms_checks)
    db = ROOT / "data" / "statutes.sqlite3"
    checks["statutes_db_present"] = db.is_file()
    if checks["statutes_db_present"]:
        try:
            with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
                checks["statutes_db_nonempty"] = conn.execute("SELECT count(*) FROM statutes").fetchone()[0] > 0
        except sqlite3.Error:
            checks["statutes_db_nonempty"] = False
    else:
        checks["statutes_db_nonempty"] = False
    return {"ok": all(checks.values()), "checks": checks, "warnings": warnings}


def main() -> None:
    report = inspect()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
