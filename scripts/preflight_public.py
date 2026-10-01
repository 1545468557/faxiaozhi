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
    return {"ok": all(checks.values()), "checks": checks}


def main() -> None:
    report = inspect()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
