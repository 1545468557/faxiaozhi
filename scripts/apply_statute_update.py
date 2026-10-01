#!/usr/bin/env python3
"""Safely promote one independently verified, complete statute into the search DB.

The weekly agent prepares a JSON candidate after checking an official or PKULAW
page. This script validates the candidate and preserves the previous DB version.
It does not fetch or infer legal text itself. Dry run is the default.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

try:
    from scripts.import_statutes import cjk_space, split_articles
except ModuleNotFoundError:  # direct `python scripts/apply_statute_update.py`
    from import_statutes import cjk_space, split_articles

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "statutes.sqlite3"
ALLOWED_HOSTS = ("npc.gov.cn", "gov.cn", "court.gov.cn", "pkulaw.com", "pkulaw.cn")
STATUS_CODES = {"valid", "pending", "amended", "repealed", "invalid", "na"}


def _date(value: str, field: str) -> str:
    if not value:
        return ""
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise ValueError(f"{field} 必须是 YYYY-MM-DD") from exc


def validate(candidate: dict) -> tuple[dict, list[tuple[str, str]]]:
    title = str(candidate.get("title") or "").strip()
    body = str(candidate.get("source_text") or "").strip()
    url = str(candidate.get("source_url") or "").strip()
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not any(host == base or host.endswith("." + base) for base in ALLOWED_HOSTS):
        raise ValueError("source_url 必须是官方或北大法宝 HTTPS 原文链接")
    if len(title) < 4 or len(body) < 80:
        raise ValueError("法规标题或完整原文缺失")
    if candidate.get("status_code") not in STATUS_CODES:
        raise ValueError("必须明确效力状态；不能凭空认定现行有效")
    published = _date(str(candidate.get("publish_date") or ""), "publish_date")
    effective = _date(str(candidate.get("effective_date") or ""), "effective_date")
    if not published:
        raise ValueError("缺少公布日期，无法判定版本")
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if candidate.get("source_sha256") != digest:
        raise ValueError("原文 SHA-256 不一致")
    verified = str(candidate.get("verified_at") or "")
    try:
        datetime.fromisoformat(verified.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("缺少有效的 verified_at") from exc
    articles = split_articles(body)
    if not articles or any(not text.strip() for _, text in articles):
        raise ValueError("原文无法解析为完整条文")
    actual_count = sum(no not in {"（题注）", "（全文）"} for no, _ in articles)
    if candidate.get("expected_article_count") != actual_count:
        raise ValueError("条文数量与人工核验的原文不一致")
    normalized = {
        "title": title,
        "source_text": body,
        "source_url": url,
        "source_sha256": digest,
        "publish_date": published,
        "effective_date": effective,
        "status_code": candidate["status_code"],
        "status_text": str(candidate.get("status_text") or "").strip(),
        "category": str(candidate.get("category") or "").strip(),
        "organ": str(candidate.get("organ") or "").strip(),
        "verified_at": verified,
        "bbbs": str(candidate.get("bbbs") or "").strip(),
    }
    if not normalized["status_text"] or not normalized["category"]:
        raise ValueError("缺少效力说明或法规分类")
    return normalized, articles


def promote(db: Path, candidate: dict, *, apply: bool = False) -> dict:
    item, articles = validate(candidate)
    if not db.is_file():
        raise ValueError(f"找不到法规库：{db}")
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
        matches = conn.execute("SELECT * FROM statutes WHERE title = ?", (item["title"],)).fetchall()
        if item["bbbs"]:
            old = conn.execute("SELECT * FROM statutes WHERE bbbs = ?", (item["bbbs"],)).fetchone()
            if old is None or old["title"] != item["title"]:
                raise ValueError("bbbs 与法规标题不匹配")
        elif len(matches) > 1:
            raise ValueError("同名法规有多个版本，必须指定 bbbs")
        else:
            old = matches[0] if matches else None
        bbbs = old["bbbs"] if old else "official:" + hashlib.sha256(
            (item["title"] + "|" + item["publish_date"]).encode("utf-8")
        ).hexdigest()[:24]
        previous = [dict(row) for row in conn.execute(
            "SELECT no, text FROM articles WHERE bbbs = ? ORDER BY seq", (bbbs,)
        )] if old else []
        new_rows = [{"no": no, "text": text} for no, text in articles]
        unchanged = bool(old and previous == new_rows
                         and all(old[key] == item[key] for key in (
                             "status_code", "status_text", "publish_date", "effective_date",
                             "category", "organ"))
                         and old["source_file"] == item["source_url"])
        report = {"action": "unchanged" if unchanged else "updated" if old else "created",
                  "bbbs": bbbs, "title": item["title"], "articles": len(articles),
                  "source_url": item["source_url"], "applied": bool(apply and not unchanged)}
        if not apply or unchanged:
            conn.rollback()
            return report
        conn.execute("""CREATE TABLE IF NOT EXISTS statute_update_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, bbbs TEXT NOT NULL, changed_at TEXT NOT NULL,
            source_url TEXT NOT NULL, source_sha256 TEXT NOT NULL, verified_at TEXT NOT NULL,
            previous_statute_json TEXT, previous_articles_json TEXT
        )""")
        now = datetime.now(UTC).isoformat()
        conn.execute("""INSERT INTO statute_update_history
            (bbbs, changed_at, source_url, source_sha256, verified_at,
             previous_statute_json, previous_articles_json) VALUES (?,?,?,?,?,?,?)""",
            (bbbs, now, item["source_url"], item["source_sha256"], item["verified_at"],
             json.dumps(dict(old), ensure_ascii=False) if old else None,
             json.dumps(previous, ensure_ascii=False) if old else None))
        values = (bbbs, item["title"], item["category"], item["organ"],
                  item["publish_date"], item["effective_date"], item["status_code"],
                  item["status_text"], item["source_url"], len(item["source_text"]),
                  len(articles), now)
        if old:
            conn.execute("DELETE FROM articles_fts WHERE bbbs = ?", (bbbs,))
            conn.execute("DELETE FROM articles WHERE bbbs = ?", (bbbs,))
            conn.execute("""UPDATE statutes SET title=?, category=?, organ=?, publish_date=?,
                effective_date=?, status_code=?, status_text=?, source_file=?, char_len=?,
                article_count=?, imported_at=? WHERE bbbs=?""", values[1:] + (bbbs,))
        else:
            conn.execute("INSERT INTO statutes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", values)
        for seq, (no, text) in enumerate(articles, 1):
            conn.execute("INSERT INTO articles (bbbs, seq, no, text) VALUES (?,?,?,?)", (bbbs, seq, no, text))
            conn.execute("INSERT INTO articles_fts (title, no, body, bbbs, no_raw) VALUES (?,?,?,?,?)",
                         (cjk_space(item["title"]), cjk_space(no), cjk_space(text), bbbs, no))
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('last_incremental_update_at', ?)", (now,))
        conn.commit()
        return report
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path, help="核验后的单篇法规 JSON")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--apply", action="store_true", help="通过校验后写入；默认只预检")
    args = parser.parse_args()
    candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
    print(json.dumps(promote(args.db, candidate, apply=args.apply), ensure_ascii=False))


if __name__ == "__main__":
    main()
