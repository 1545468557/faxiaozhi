#!/usr/bin/env python3
"""Compare a bounded local watchlist with PKULAW's per-title version chain.

This is a change detector, not a source of complete text. It never writes to
the statute DB. Official new-law discovery is a separate weekly agent step.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import UTC, date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_config  # noqa: E402
from app.tools.mcp import McpClient, unwrap_result  # noqa: E402

WATCHLIST = (
    "中华人民共和国民法典", "中华人民共和国劳动合同法", "中华人民共和国公司法",
    "中华人民共和国劳动法", "中华人民共和国刑法", "中华人民共和国民事诉讼法",
    "中华人民共和国个人信息保护法", "中华人民共和国数据安全法",
    "中华人民共和国行政处罚法", "中华人民共和国安全生产法",
)


def compare(conn: sqlite3.Connection, title: str, current: dict) -> dict:
    rows = conn.execute("SELECT bbbs, publish_date, status_code FROM statutes WHERE title = ?", (title,)).fetchall()
    local = max(rows, key=lambda row: (row[1] or "", row[2] == "valid")) if rows else None
    issue_date = str(current.get("issueDate") or "")
    implement_date = str(current.get("implementDate") or "")
    status = str(current.get("timeliness") or "")
    local_date = local[1] if local else ""
    status_codes = {"现行有效": "valid", "有效": "valid", "已废止": "repealed",
                    "已被修改": "amended", "失效": "invalid", "尚未生效": "pending"}
    source_code = status_codes.get(status)
    changed = bool(issue_date and (not local or issue_date > local_date or
                   (issue_date == local_date and source_code and source_code != local[2])))
    unresolved = not issue_date or bool(local and issue_date < local_date)
    return {
        "title": title,
        "result": "candidate" if changed else "unresolved" if unresolved else "current",
        "local_bbbs": local[0] if local else "",
        "local_publish_date": local_date,
        "source_publish_date": issue_date,
        "source_effective_date": implement_date,
        "source_status": status,
        "source_url": (current.get("citation") or {}).get("url") or "",
    }


def mark_superseded(db: Path, findings: list[dict]) -> int:
    """Remove an older edition from default search after version metadata confirms replacement."""
    count = 0
    with sqlite3.connect(db) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""CREATE TABLE IF NOT EXISTS statute_status_history (
            id INTEGER PRIMARY KEY, bbbs TEXT NOT NULL, changed_at TEXT NOT NULL,
            old_status_code TEXT NOT NULL, old_status_text TEXT NOT NULL,
            source_url TEXT NOT NULL, newer_publish_date TEXT NOT NULL
        )""")
        for finding in findings:
            if (finding.get("result") != "candidate" or
                finding.get("source_status") not in {"现行有效", "有效"} or
                not finding.get("source_effective_date") or
                finding["source_effective_date"] > date.today().isoformat() or
                not finding.get("local_bbbs") or
                not finding.get("source_url", "").startswith("https://www.pkulaw.com/")):
                continue
            bbbs = finding["local_bbbs"]
            old = conn.execute(
                "SELECT status_code, status_text, publish_date FROM statutes WHERE bbbs=?", (bbbs,)
            ).fetchone()
            if not old or old[0] != "valid" or old[2] >= finding["source_publish_date"]:
                continue
            conn.execute("""INSERT INTO statute_status_history
                (bbbs, changed_at, old_status_code, old_status_text, source_url, newer_publish_date)
                VALUES (?,?,?,?,?,?)""", (bbbs, datetime.now(UTC).isoformat(), old[0], old[1],
                                      finding["source_url"], finding["source_publish_date"]))
            conn.execute("UPDATE statutes SET status_code='amended', status_text='已被修改' WHERE bbbs=?", (bbbs,))
            count += 1
        conn.commit()
    return count


def run(db: Path, titles: tuple[str, ...] = WATCHLIST, *, mark_old: bool = False) -> dict:
    cfg = get_config()
    endpoints = cfg.mcp_endpoints().get("statute_history") or []
    if not endpoints:
        raise RuntimeError("未配置北大法宝法规沿革 MCP")
    # Aggregate endpoint is working even when the dedicated history endpoint times out.
    endpoint = next((url for url in endpoints if "mcp-law-agg" in url), endpoints[0])
    client = McpClient(cfg, endpoint)
    tools = [str(tool.get("name") or "") for tool in client.list_tools()]
    name = next((tool for tool in tools if tool.endswith("list_law_versions")), "")
    if not name:
        raise RuntimeError("法规沿革 MCP 不提供 list_law_versions")
    results: list[dict] = []
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        for title in titles:
            try:
                payload = unwrap_result(client.call_tool(name, {"lawTitle": title}))
                current = payload.get("currentVersion") if isinstance(payload, dict) else None
                if not isinstance(current, dict):
                    raise ValueError("缺少 currentVersion")
                results.append(compare(conn, title, current))
            except Exception as exc:
                results.append({"title": title, "result": "error", "message": type(exc).__name__})
    marked = mark_superseded(db, results) if mark_old else 0
    return {"checked_at": datetime.now(UTC).isoformat(), "scope": "watched_titles_only",
            "checked_titles": len(titles), "marked_superseded": marked, "results": results}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "statutes.sqlite3")
    parser.add_argument("--title", action="append", help="只检查指定标题；可重复")
    parser.add_argument("--mark-superseded", action="store_true",
                        help="将已被较新生效版本替代的旧版移出默认检索，并留痕")
    args = parser.parse_args()
    titles = tuple(args.title) if args.title else WATCHLIST
    print(json.dumps(run(args.db, titles, mark_old=args.mark_superseded), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
