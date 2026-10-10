"""会话与消息的落盘（SQLite）

旧版把问答留在内存，服务重启就丢。这里最小实现两张表，写到 `data/v3.sqlite3`：
- sessions：会话（标题、时间、**归属 user_id**）
- messages：消息（角色、正文、来源、顺序）

**归属（2026-09-27 隔离改造）**
`sessions.user_id` 存 `app.auth.guard.owner_key(user)` —— 用户主键字符串
（未登录只在关了登录门时出现，落 `_anonymous`）。

改造前这张表没有归属列，这是两个真实口子的根源：
1. `list_sessions()` 把**所有人**的会话都列出来；
2. `ensure_session(sid)` 会**认下任意 sid** —— 拿到别人的会话 id 就能读他的记录、往里追加消息。

所以现在：`ensure_session` 遇到「存在但归属不符」的 sid 一律抛 `SessionOwnedByOther`，
上层按「会话不存在」回 404 —— **不要回 403**：403 等于告诉对方「这个 sid 是真的，只是不是你的」。

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

#: 无归属（只在登录门关闭时出现）——与 `app.auth.guard.ANONYMOUS` 同值。
ANON = "_anonymous"

#: 建表。**必须与建索引分开** —— 见下面 `_migrate_add_owner_column` 的说明。
TABLES_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id          TEXT PRIMARY KEY,
  title       TEXT NOT NULL DEFAULT '',
  user_id     TEXT NOT NULL DEFAULT '_anonymous',
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
"""

#: 建索引。**一定要在补列之后跑**：改造前的库里 `sessions` 没有 `user_id`，
#: 而 `CREATE TABLE IF NOT EXISTS` 对已存在的表是空操作 —— 于是「建表 + 建索引」写在
#: 同一个 script 里时，`idx_sessions_user` 会撞上「no such column: user_id」直接抛错。
#: （这条是我自己的迁移用例抓出来的，不是推测。）
INDEXES_SCHEMA = """
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, seq);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id, updated_at DESC);
"""

TITLE_MAX = 40


class SessionOwnedByOther(Exception):
    """这个 session_id 存在，但不属于当前用户。

    上层必须把它**当作「会话不存在」**处理（404，不是 403）——
    否则「403 说明这个 sid 是真的」本身就泄漏了信息。
    """


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
            # 顺序不能动：建表 → 补 `user_id` 列 → 建索引。
            # 旧库上 `CREATE TABLE IF NOT EXISTS` 是空操作，所以补列必须发生在建索引之前。
            self._conn.executescript(TABLES_SCHEMA)
            self._migrate_add_owner_column()
            self._conn.executescript(INDEXES_SCHEMA)
            self._conn.commit()

    def _migrate_add_owner_column(self) -> None:
        """给改造前建好的库补上 `user_id` 列。

        `ALTER TABLE ... ADD COLUMN` 带 `NOT NULL DEFAULT` 在 SQLite 上是允许的，
        存量行会拿到 `_anonymous` —— 也就是「改造前所有人共用一个桶」的原口径。
        注意：这里**不猜测**任何历史会话该归谁，无主就是无主。
        """
        cols = {row["name"] for row in self._conn.execute("PRAGMA table_info(sessions)")}
        if "user_id" not in cols:
            self._conn.execute(
                f"ALTER TABLE sessions ADD COLUMN user_id TEXT NOT NULL DEFAULT '{ANON}'"
            )

    # ---------------------------------------------------------------- 会话

    def _row(self, sid: str) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()

    def user_of(self, sid: str) -> str | None:
        """这个会话属于谁；不存在返回 None。"""
        row = self._row(sid)
        return str(row["user_id"]) if row is not None else None

    def belongs_to(self, sid: str, user_id: str) -> bool:
        return self.user_of(sid) == user_id

    def create_session(self, title: str = "", user_id: str = ANON) -> dict[str, Any]:
        now = time.time()
        sid = uuid.uuid4().hex[:16]
        clean = title.strip()[:TITLE_MAX]
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions (id, title, user_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (sid, clean, user_id, now, now),
            )
            self._conn.commit()
        return _session_view({"id": sid, "title": clean, "user_id": user_id, "created_at": now, "updated_at": now})

    def get_session(self, sid: str, user_id: str | None = None) -> dict[str, Any] | None:
        """取会话。给了 `user_id` 就顺带校验归属，不符返回 None（不抛异常，读路径用）。"""
        row = self._row(sid)
        if row is None:
            return None
        if user_id is not None and str(row["user_id"]) != user_id:
            return None
        return _session_view(dict(row))

    def ensure_session(
        self,
        sid: str | None,
        title: str = "",
        user_id: str = ANON,
    ) -> dict[str, Any]:
        """给定 id 就用它（不存在则建，方便前端自己生成 id）；没给就新建。

        **归属不符的 sid 一律拒**（抛 `SessionOwnedByOther`）——这是修掉
        「任意 sid 可接管」的关键。前端自己生成 id 的场景不受影响：
        那个 id 在新库里通常不存在，走的是「建」的分支。
        """
        clean = title.strip()[:TITLE_MAX]
        if not sid:
            return self.create_session(title, user_id)

        row = self._row(sid)
        if row is not None:
            if str(row["user_id"]) != user_id:
                raise SessionOwnedByOther(sid)
            return _session_view(dict(row))

        now = time.time()
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO sessions (id, title, user_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (sid, clean, user_id, now, now),
                )
                self._conn.commit()
        except sqlite3.IntegrityError:
            # 并发下别人刚好抢先建了同一个 id：重新判归属，别把别人的会话交出去
            row = self._row(sid)
            if row is None or str(row["user_id"]) != user_id:
                raise SessionOwnedByOther(sid) from None
            return _session_view(dict(row))
        return _session_view({"id": sid, "title": clean, "user_id": user_id, "created_at": now, "updated_at": now})

    def list_sessions(self, limit: int = 50, user_id: str | None = None) -> list[dict[str, Any]]:
        """会话列表。给了 `user_id` 就只列自己的 —— 这是「我的」页不串号的前提。"""
        sql = (
            "SELECT s.*, (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS message_count "
            "FROM sessions s {where} ORDER BY s.updated_at DESC LIMIT ?"
        )
        where = "WHERE s.user_id = ?" if user_id is not None else ""
        params: tuple[Any, ...] = (user_id, max(1, min(200, limit))) if user_id is not None else (max(1, min(200, limit)),)
        with self._lock:
            rows = self._conn.execute(sql.format(where=where), params).fetchall()
        return [_session_view(dict(row)) for row in rows]

    def delete_session(self, sid: str, user_id: str | None = None) -> bool:
        """删除会话。给了 `user_id` 就只删得掉自己的。"""
        with self._lock:
            if user_id is None:
                cur = self._conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
            else:
                cur = self._conn.execute(
                    "DELETE FROM sessions WHERE id = ? AND user_id = ?", (sid, user_id)
                )
            deleted = cur.rowcount > 0
            if deleted:
                self._conn.execute("DELETE FROM messages WHERE session_id = ?", (sid,))
            self._conn.commit()
        return deleted

    # ---------------------------------------------------------------- 消息

    def append_message(
        self,
        sid: str,
        role: str,
        content: str,
        sources: list[dict[str, Any]] | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        if user_id is not None and not self.belongs_to(sid, user_id):
            raise SessionOwnedByOther(sid)
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

    def messages(self, sid: str, limit: int = 200, user_id: str | None = None) -> list[dict[str, Any]]:
        """取消息。给了 `user_id` 就先验归属，不是自己的返回空列表（不抛、不泄漏存在性）。"""
        if user_id is not None and not self.belongs_to(sid, user_id):
            return []
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
