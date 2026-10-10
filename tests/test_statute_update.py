import hashlib
import sqlite3
from datetime import date, timedelta

import pytest

from app import statutes
from scripts.apply_statute_update import promote
from scripts.check_statute_versions import compare, mark_superseded


def _db(path):
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE statutes (bbbs TEXT PRIMARY KEY, title TEXT NOT NULL, category TEXT,
                organ TEXT, publish_date TEXT, effective_date TEXT, status_code TEXT NOT NULL,
                status_text TEXT NOT NULL, source_file TEXT, char_len INTEGER,
                article_count INTEGER, imported_at TEXT);
            CREATE TABLE articles (id INTEGER PRIMARY KEY AUTOINCREMENT, bbbs TEXT NOT NULL,
                seq INTEGER NOT NULL, no TEXT, text TEXT NOT NULL);
            CREATE VIRTUAL TABLE articles_fts USING fts5(
                title, no, body, bbbs UNINDEXED, no_raw UNINDEXED, tokenize='unicode61');
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
            INSERT INTO meta VALUES ('imported_at', '2026-09-21');
        """)


def _candidate():
    body = ("第一条 本法为了保障劳动者合法权益，规范用人单位与劳动者之间的劳动关系，明确双方的权利和义务而制定。\n"
            "第二条 用人单位与劳动者建立劳动关系，应当遵守法律规定并依法订立劳动合同，双方应当诚实信用履行劳动合同。")
    return {
        "title": "中华人民共和国测试劳动法", "category": "法律", "organ": "全国人大常委会",
        "publish_date": "2026-09-30", "effective_date": "2026-10-01",
        "status_code": "valid", "status_text": "现行有效",
        "source_url": "https://flk.npc.gov.cn/detail2.html?id=example",
        "source_text": body, "source_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "expected_article_count": 2,
        "verified_at": "2026-10-01T09:00:00+08:00",
    }


def test_dry_run_apply_revision_and_idempotency(tmp_path):
    db = tmp_path / "statutes.sqlite3"
    _db(db)
    candidate = _candidate()
    assert promote(db, candidate)["action"] == "created"
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM statutes").fetchone()[0] == 0

    first = promote(db, candidate, apply=True)
    assert first["action"] == "created"
    assert promote(db, candidate, apply=True)["action"] == "unchanged"
    changed = dict(candidate, status_code="repealed", status_text="已废止")
    assert promote(db, changed, apply=True)["action"] == "updated"
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT status_code FROM statutes").fetchone()[0] == "repealed"
        assert conn.execute("SELECT count(*) FROM articles").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM articles_fts").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM statute_update_history").fetchone()[0] == 2
        assert conn.execute("SELECT value FROM meta WHERE key='imported_at'").fetchone()[0] == "2026-09-21"


def test_rejects_unverified_source_and_preserves_db(tmp_path):
    db = tmp_path / "statutes.sqlite3"
    _db(db)
    candidate = _candidate()
    candidate["source_url"] = "https://fake-npc.gov.cn.evil.example/"
    with pytest.raises(ValueError, match="source_url"):
        promote(db, candidate, apply=True)
    candidate["source_url"] = "https://flk.npc.gov.cn/detail2.html"
    candidate["source_sha256"] = "wrong"
    with pytest.raises(ValueError, match="SHA-256"):
        promote(db, candidate, apply=True)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM statutes").fetchone()[0] == 0


def test_newer_effective_version_marks_old_record_with_audit(tmp_path):
    db = tmp_path / "statutes.sqlite3"
    _db(db)
    candidate = _candidate()
    promote(db, candidate, apply=True)
    future = (date.today() + timedelta(days=30)).isoformat()
    with sqlite3.connect(db) as conn:
        finding = compare(conn, candidate["title"], {
            "issueDate": future, "implementDate": future,
            "timeliness": "现行有效",
            "citation": {"url": "https://www.pkulaw.com/chl/new.html"},
        })
    assert finding["result"] == "candidate"
    assert mark_superseded(db, [finding]) == 0  # future effective date
    finding["source_effective_date"] = "2026-09-30"
    assert mark_superseded(db, [finding]) == 1
    assert mark_superseded(db, [finding]) == 0
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT status_code FROM statutes").fetchone()[0] == "amended"
        assert conn.execute("SELECT count(*) FROM statute_status_history").fetchone()[0] == 1


def test_exact_old_title_is_not_returned_as_current(tmp_path, monkeypatch):
    db = tmp_path / "statutes.sqlite3"
    _db(db)
    candidate = dict(_candidate(), status_code="amended", status_text="已被修改")
    promote(db, candidate, apply=True)
    monkeypatch.setattr(statutes, "DB_PATH", db)
    result = statutes.search(candidate["title"])
    assert result["items"] == []
    assert result["stale_exact_title"] is True
    old = statutes.search(candidate["title"], include_invalid=True)
    assert old["items"][0]["status_code"] == "amended"
