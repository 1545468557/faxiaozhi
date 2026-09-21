"""会话与消息的落盘（SQLite）

旧版把问答留在内存，服务重启就丢。这里最小实现两张表，写到 `data/v3.sqlite3`：
- sessions：会话（标题、时间）
- messages：消息（角色、正文、来源、顺序）

并发口径：FastAPI 的 async 端点里做的是短小的同步写，用一把锁串起来即可；
不在 SQLite 上做多进程并发（本地单进程运行）。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id          TEXT PRIMARY KEY,
  title       TEXT NOT NULL DEFAULT '',
  created_at  REAL NOT NULL,
  updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
  id          TEXT PRIMARY KEY,
  session_id  TEXT NOT NULL,
  role        TEXT NOT NULL,
  content     TEXT NOT NULL,
  sources     TEXT NOT NULL DEFAULT '[]',
  created_at  REAL NOT NULL,
  seq         INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, seq);
"""

TITLE_MAX = 40


def _session_view(row: dict[str, Any]) -> dict[str, Any]:
    """统一键名：库里列名是 `id`，对外一律叫 `session_id`（前端只用这个名字）。"""
    view = dict(row)
    view["session_id"] = str(view.get("id") or view.get("session_id") or "")
    return view


class Store:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ---------------------------------------------------------------- 会话

    def create_session(self, title: str = "") -> dict[str, Any]:
        now = time.time()
        sid = uuid.uuid4().hex[:16]
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (sid, title.strip()[:TITLE_MAX], now, now),
            )
            self._conn.commit()
        return _session_view({"id": sid, "title": title.strip()[:TITLE_MAX], "created_at": now, "updated_at": now})

    def get_session(self, sid: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
        return _session_view(dict(row)) if row else None

    def ensure_session(self, sid: str | None, title: str = "") -> dict[str, Any]:
        """给定 id 就用它（不存在则建，方便前端自己生成 id）；没给就新建。"""
        if sid:
            found = self.get_session(sid)
            if found:
                return found
            now = time.time()
            with self._lock:
                self._conn.execute(
                    "INSERT INTO sessions (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (sid, title.strip()[:TITLE_MAX], now, now),
                )
                self._conn.commit()
            return _session_view({"id": sid, "title": title.strip()[:TITLE_MAX], "created_at": now, "updated_at": now})
        return self.create_session(title)

    def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT s.*, (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS message_count "
                "FROM sessions s ORDER BY s.updated_at DESC LIMIT ?",
                (max(1, min(200, limit)),),
            ).fetchall()
        return [_session_view(dict(row)) for row in rows]

    def delete_session(self, sid: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
            self._conn.execute("DELETE FROM messages WHERE session_id = ?", (sid,))
            self._conn.commit()
        return cur.rowcount > 0

    # ---------------------------------------------------------------- 消息

    def append_message(
        self,
        sid: str,
        role: str,
        content: str,
        sources: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        now = time.time()
        mid = uuid.uuid4().hex[:16]
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS last FROM messages WHERE session_id = ?", (sid,)
            ).fetchone()
            seq = int(row["last"]) + 1
            self._conn.execute(
                "INSERT INTO messages (id, session_id, role, content, sources, created_at, seq) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (mid, sid, role, content, json.dumps(sources or [], ensure_ascii=False), now, seq),
            )
            # 会话标题取第一句用户消息；更新时间跟着走
            self._conn.execute(
                "UPDATE sessions SET updated_at = ?, "
                "title = CASE WHEN title = '' AND ? = 'user' THEN ? ELSE title END WHERE id = ?",
                (now, role, content.strip().replace("\n", " ")[:TITLE_MAX], sid),
            )
            self._conn.commit()
        return {"id": mid, "session_id": sid, "role": role, "content": content, "sources": sources or [], "created_at": now, "seq": seq}

    def messages(self, sid: str, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY seq ASC LIMIT ?",
                (sid, max(1, min(1000, limit))),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["sources"] = json.loads(item.get("sources") or "[]")
            except json.JSONDecodeError:
                item["sources"] = []
            out.append(item)
        return out
