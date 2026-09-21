"""本地依据库（阶段 2-3）：把**公开**的法条与判例原文跨会话沉淀，供命中复用与逐字核验。

设计要点（对齐《阶段 2 第 3 阶段技术开发文档》）：

- **三分区**：`citable`（依据库，可引用）/ `clue`（线索库，如仅摘要或尚未通过门禁，永不作为引用）/
  不入库（失败返回、用户上传材料）。
- **不替代检索**：检索仍走法宝；本地库只在**同一检索式**或**同一标识**命中时复用。
- **红线**：用户上传材料原文绝不入库；库中不含密钥与真实地址。
- **可恢复**：原子写（临时文件 + os.replace）+ 文件锁；导入幂等；同标识内容变化保留 revisions，不静默覆盖；
  容量超限按最近未使用（LRU）淘汰；`index.json` 丢了可重建。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import Config
from .models import Source, Status, normalize_text

SCHEMA_VERSION = 1
ZONE_CITABLE = "citable"
ZONE_CLUE = "clue"
VALID_TYPES = ("case", "statute", "history")
VALID_ORIGINS = ("mcp", "imported")
#: 绝不允许作为库来源的 origin（用户材料红线）
FORBIDDEN_ORIGINS = ("user", "user_upload", "user_material", "fixture")
_SECRETISH = re.compile(r"(sk-[A-Za-z0-9]{6,}|Bearer\s+[A-Za-z0-9\-]{6,}|PKULAW_MCP_TOKEN)")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def _sha(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _slug(entry_id: str) -> str:
    readable = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", entry_id)[:40].strip("_") or "entry"
    return f"{readable}_{_sha(entry_id)[:8]}"


@dataclass
class LibraryEntry:
    entry_id: str
    content_type: str
    identifier: str
    title: str = ""
    text: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    zone: str = ZONE_CLUE
    origin: str = "mcp"
    fetched_at: str = ""
    imported_at: str = ""
    source_run_id: str | None = None
    content_hash: str = ""
    revisions: list[dict[str, Any]] = field(default_factory=list)
    last_used_at: str = ""
    schema_version: int = SCHEMA_VERSION

    @property
    def citable(self) -> bool:
        return self.zone == ZONE_CITABLE

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LibraryEntry:
        known = {f for f in cls.__dataclass_fields__}                     # type: ignore[attr-defined]
        return cls(**{k: v for k, v in (data or {}).items() if k in known})


def build_entry_id(content_type: str, identifier: str) -> str:
    return f"{content_type}:{normalize_text(identifier or '')}"


def entry_from_source(
    source: Source, *, origin: str = "mcp", run_id: str | None = None, zone: str = ZONE_CLUE
) -> LibraryEntry:
    kind = "statute" if source.kind == "statute" else "case"
    meta = {
        "court": source.court,
        "level": source.level,
        "region": source.region,
        "decided_on": source.decided_on,
        "effective_status": source.effective_status,
        "applicable_from": source.applicable_from,
        "applicable_to": source.applicable_to,
        "uri": source.uri,
        "status": source.status.value,
        "abstract_only": source.status is Status.ABSTRACT_ONLY,
    }
    return LibraryEntry(
        entry_id=build_entry_id(kind, source.identifier),
        content_type=kind,
        identifier=source.identifier,
        title=source.title,
        text=source.quote or "",
        meta={k: v for k, v in meta.items() if v not in (None, "")},
        zone=zone,
        origin=origin,
        fetched_at=_now(),
        imported_at=_now(),
        source_run_id=run_id,
        content_hash=_sha(normalize_text(source.quote or "")),
        last_used_at=_now(),
    )


def entry_to_source(entry: LibraryEntry, *, source_id: str | None = None) -> Source:
    meta = entry.meta or {}
    return Source(
        source_id=source_id or f"lib_{entry.content_type}_{_sha(entry.entry_id)[:8]}",
        kind="statute" if entry.content_type == "statute" else "case",
        title=entry.title,
        identifier=entry.identifier,
        quote=entry.text,
        effective_status=str(
            meta.get("effective_status")
            or ("不适用（裁判文书）" if entry.content_type == "case" else "unknown")
        ),
        applicable_from=meta.get("applicable_from"),
        applicable_to=meta.get("applicable_to"),
        court=meta.get("court"),
        level=meta.get("level"),
        region=meta.get("region"),
        decided_on=meta.get("decided_on"),
        uri=meta.get("uri"),
        origin="local",
        synthetic=False,
        status=Status.OK if (entry.text or "").strip() else Status.ABSTRACT_ONLY,
    )


class Library:
    """本地依据库。单进程使用；写入原子、幂等、可重建索引。"""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.enabled = bool(cfg.get("library.enabled", True))
        self.dir: Path = cfg.path("library.dir", "data/library")
        self.max_entries = int(cfg.get("library.max_entries", 50000))
        self.max_bytes = int(cfg.get("library.max_bytes", 2 * 1024**3))
        self.max_entry_chars = int(cfg.get("library.max_entry_chars", 200000))
        self.evict_policy = str(cfg.get("library.evict_policy", "lru"))
        self._index: dict[str, Any] | None = None

    # ------------------------------------------------------------ 路径
    def _type_dir(self, content_type: str) -> Path:
        return self.dir / (content_type if content_type in VALID_TYPES else "misc")

    def _path(self, content_type: str, entry_id: str) -> Path:
        return self._type_dir(content_type) / f"{_slug(entry_id)}.json"

    @property
    def index_path(self) -> Path:
        return self.dir / "index.json"

    @property
    def lock_path(self) -> Path:
        return self.dir / ".lock"

    # ------------------------------------------------------------ 锁与原子写
    @contextmanager
    def _lock(self):
        if not self.enabled:
            yield False
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        if self.lock_path.exists():
            age = time.time() - self.lock_path.stat().st_mtime
            if age < 60:
                yield False
                return
        self.lock_path.write_text(str(os.getpid()), encoding="utf-8")
        try:
            yield True
        finally:
            try:
                self.lock_path.unlink()
            except OSError:
                pass

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)

    # ------------------------------------------------------------ 读取
    def _load_file(self, path: Path) -> tuple[LibraryEntry, list[dict[str, Any]]] | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        entry = LibraryEntry.from_dict(data.get("entry") or data)
        return entry, list(data.get("revisions") or [])

    def get(self, content_type: str, identifier: str) -> LibraryEntry | None:
        if not self.enabled:
            return None
        path = self._path(content_type, build_entry_id(content_type, identifier))
        if not path.exists():
            return None
        loaded = self._load_file(path)
        return loaded[0] if loaded else None

    def all_entries(self) -> list[LibraryEntry]:
        out: list[LibraryEntry] = []
        if not self.dir.exists():
            return out
        for sub in VALID_TYPES:
            d = self._type_dir(sub)
            if not d.exists():
                continue
            for path in sorted(d.glob("*.json")):
                loaded = self._load_file(path)
                if loaded:
                    out.append(loaded[0])
        return out

    # ------------------------------------------------------------ 写入
    def upsert(self, entry: LibraryEntry) -> str:
        """写入/更新一条。返回动作：added / touched / versioned / skipped / refused_* 。"""
        if not self.enabled:
            return "skipped_disabled"
        if entry.origin in FORBIDDEN_ORIGINS:
            return "refused_non_public_origin"
        if not (entry.text or "").strip() and not (entry.meta or {}).get("abstract_only"):
            # 无原文的内容只允许作为线索（仅摘要），不允许写空文本
            return "skipped_empty_text"
        if len(entry.text) > self.max_entry_chars:
            return "skipped_too_long"
        if _SECRETISH.search(entry.text) or _SECRETISH.search(entry.identifier or ""):
            return "refused_secret_like"
        entry.content_hash = entry.content_hash or _sha(normalize_text(entry.text))
        if not (entry.text or "").strip():
            entry.content_hash = f"abstract:{entry.identifier}"
        entry.last_used_at = entry.last_used_at or _now()
        entry.imported_at = entry.imported_at or _now()
        if not entry.fetched_at:
            entry.fetched_at = entry.imported_at
        with self._lock() as ok:
            if not ok:
                return "skipped_locked"
            path = self._path(entry.content_type, entry.entry_id)
            existing = self._load_file(path) if path.exists() else None
            if existing is None:
                self._atomic_write(path, self._dump(entry, []))
                action = "added"
            elif existing[0].content_hash == entry.content_hash:
                existing[0].last_used_at = _now()
                existing[0].fetched_at = entry.fetched_at or existing[0].fetched_at
                self._atomic_write(path, self._dump(existing[0], existing[1]))
                action = "touched"
            else:
                # 内容变化：保留旧版本，新内容退回线索区（需重新核验）
                old = existing[0]
                revisions = [*existing[1], {"text": old.text, "content_hash": old.content_hash,
                                            "zone": old.zone, "origin": old.origin,
                                            "fetched_at": old.fetched_at}]
                entry.zone = ZONE_CLUE
                self._atomic_write(path, self._dump(entry, revisions))
                action = "versioned"
        self.evict()
        return action

    @staticmethod
    def _dump(entry: LibraryEntry, revisions: list[dict[str, Any]]) -> str:
        payload = {"schema_version": SCHEMA_VERSION, "entry": entry.to_dict(), "revisions": revisions}
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def promote(self, content_type: str, identifier: str) -> bool:
        """把已通过门禁的内容从线索区提升为可引用（依据库）。"""
        if not self.enabled:
            return False
        path = self._path(content_type, build_entry_id(content_type, identifier))
        with self._lock() as ok:
            if not ok or not path.exists():
                return False
            loaded = self._load_file(path)
            if not loaded or not (loaded[0].text or "").strip():
                return False
            loaded[0].zone = ZONE_CITABLE
            loaded[0].last_used_at = _now()
            self._atomic_write(path, self._dump(loaded[0], loaded[1]))
        return True

    def touch(self, content_type: str, identifier: str) -> None:
        entry = self.get(content_type, identifier)
        if entry is None:
            return
        entry.last_used_at = _now()
        path = self._path(content_type, entry.entry_id)
        loaded = self._load_file(path)
        self._atomic_write(path, self._dump(entry, loaded[1] if loaded else []))

    # ------------------------------------------------------------ 检索缓存（按检索式）
    @staticmethod
    def query_key(tool: str, args: dict[str, Any]) -> str:
        basis = json.dumps({"tool": tool, "args": args}, ensure_ascii=False, sort_keys=True, default=str)
        return _sha(basis)[:16]

    def put_query(self, tool: str, args: dict[str, Any], entry_ids: list[str]) -> None:
        if not self.enabled or not entry_ids:
            return
        index = self._load_index()
        index.setdefault("queries", {})[self.query_key(tool, args)] = {
            "tool": tool,
            "args": args,
            "entry_ids": entry_ids,
            "fetched_at": time.time(),
        }
        self._save_index(index)

    # ---- 主题级回退（仅在实时检索完全失败时使用；绝不用于补充成功检索的候选池）----
    @staticmethod
    def topic_key(topic: str, conditions: dict[str, Any] | None = None) -> str:
        basis = json.dumps(
            {"topic": normalize_text(topic or ""), "conditions": conditions or {}},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        return _sha(basis)[:16]

    def put_topic(self, topic: str, conditions: dict[str, Any] | None, entry_ids: list[str]) -> None:
        if not self.enabled or not topic or not entry_ids:
            return
        index = self._load_index()
        topics = index.setdefault("topics", {})
        key = self.topic_key(topic, conditions)
        existing = topics.get(key) or {}
        # 合并而不是覆盖：同一主题的案例与法条分两次检索，不能后一次把前一次冲掉
        merged = sorted(set(existing.get("entry_ids") or []) | set(entry_ids))
        topics[key] = {"entry_ids": merged, "fetched_at": time.time(), "topic": topic}
        self._save_index(index)

    def topic_fallback(self, topic: str, conditions: dict[str, Any] | None = None) -> list[LibraryEntry]:
        """法宝不可用时的**回退**：返回此前同一主题真实检索过的条目。

        只应在“实时检索完全失败”时调用（见 `app/workflows/research.py`），
        不得用于给成功的检索结果补充内容——那会让候选池偏向历史。
        """
        if not self.enabled or not topic:
            return []
        record = (self._load_index().get("topics") or {}).get(self.topic_key(topic, conditions))
        if not record:
            return []
        out: list[LibraryEntry] = []
        for entry_id in record.get("entry_ids") or []:
            content_type = str(entry_id).partition(":")[0]
            found = self.all_entries_by_id(content_type, str(entry_id))
            if found is not None:
                out.append(found)
        return out

    def query_hit(self, tool: str, args: dict[str, Any], ttl_seconds: int) -> list[LibraryEntry] | None:
        if not self.enabled:
            return None
        index = self._load_index()
        record = (index.get("queries") or {}).get(self.query_key(tool, args))
        if not record:
            return None
        if ttl_seconds < 0:
            return None
        if (time.time() - float(record.get("fetched_at") or 0)) > ttl_seconds:
            return None
        entries: list[LibraryEntry] = []
        for entry_id in record.get("entry_ids") or []:
            content_type = str(entry_id).partition(":")[0]
            found = self.all_entries_by_id(content_type, str(entry_id))
            if found is not None:
                entries.append(found)
        return entries or None

    def all_entries_by_id(self, content_type: str, entry_id: str) -> LibraryEntry | None:
        path = self._path(content_type, entry_id)
        if not path.exists():
            return None
        loaded = self._load_file(path)
        return loaded[0] if loaded else None

    # ------------------------------------------------------------ 索引（可重建）
    def _load_index(self) -> dict[str, Any]:
        if self._index is not None:
            return self._index
        if self.index_path.exists():
            try:
                self._index = json.loads(self.index_path.read_text(encoding="utf-8"))
                return self._index
            except json.JSONDecodeError:
                pass
        self._index = {"schema_version": SCHEMA_VERSION, "queries": {}, "topics": {}}
        return self._index

    def _save_index(self, index: dict[str, Any]) -> None:
        index["schema_version"] = SCHEMA_VERSION
        index["updated_at"] = _now()
        self._atomic_write(self.index_path, json.dumps(index, ensure_ascii=False, indent=2))
        self._index = index

    def rebuild_index(self) -> dict[str, Any]:
        """丢失 index.json 后按条目重建（测试：删掉索引仍能工作）。"""
        entries = self.all_entries()
        index = {"schema_version": SCHEMA_VERSION, "queries": {}, "topics": {}, "rebuilt_at": _now(),
                 "entry_count": len(entries)}
        self._save_index(index)
        return index

    # ------------------------------------------------------------ 容量与淘汰
    def _bytes(self) -> int:
        total = 0
        if not self.dir.exists():
            return 0
        for path in self.dir.rglob("*.json"):
            try:
                total += path.stat().st_size
            except OSError:
                continue
        return total

    def evict(self) -> int:
        """超限按最近未使用淘汰。返回淘汰条数。"""
        if not self.enabled:
            return 0
        entries = self.all_entries()
        count = len(entries)
        size = self._bytes()
        if count <= self.max_entries and size <= self.max_bytes:
            return 0
        order = sorted(entries, key=lambda e: (e.last_used_at or "", e.entry_id))
        evicted = 0
        for entry in order:
            if count <= self.max_entries and size <= self.max_bytes:
                break
            path = self._path(entry.content_type, entry.entry_id)
            try:
                size -= path.stat().st_size
                path.unlink()
                count -= 1
                evicted += 1
            except OSError:
                continue
        if evicted:
            index = self._load_index()
            index["queries"] = {
                key: rec
                for key, rec in (index.get("queries") or {}).items()
                if all(self._entry_exists(eid) for eid in rec.get("entry_ids") or [])
            }
            index["topics"] = {
                key: rec
                for key, rec in (index.get("topics") or {}).items()
                if any(self._entry_exists(eid) for eid in rec.get("entry_ids") or [])
            }
            self._save_index(index)
        return evicted

    def _entry_exists(self, entry_id: str) -> bool:
        content_type, _, _ = str(entry_id).partition(":")
        return self.all_entries_by_id(content_type, entry_id) is not None

    # ------------------------------------------------------------ 核验与印证
    def verify(
        self, content_type: str, identifier: str, quote: str, applicable_at: str | None = None
    ) -> tuple[bool, str, str]:
        """本地逐字核验。返回 (通过, 规则号, 原因)。"""
        entry = self.get(content_type, identifier)
        if entry is None:
            return False, "L0", "本地依据库中没有该来源"
        if not entry.citable:
            return False, "L1", "该内容在本地仅作检索线索，不能作为引用依据"
        normalized_quote = normalize_text(quote or "")
        if not normalized_quote:
            return False, "L2", "引用原文为空，不予核验"
        if normalized_quote not in normalize_text(entry.text):
            return False, "L2", "引用原文与本地依据库原文不一致"
        if applicable_at:
            frm = (entry.meta or {}).get("applicable_from")
            to = (entry.meta or {}).get("applicable_to")
            if frm and applicable_at < str(frm):
                return False, "L3", f"适用时点早于生效日 {frm}"
            if to and applicable_at > str(to):
                return False, "L3", f"适用时点晚于失效日 {to}"
        self.touch(content_type, identifier)
        return True, "", ""

    def corroborate(self, source: Source) -> str:
        """判定一条来源的印证状态：dual / single_mcp / single_local / conflict / not_applicable。

        2-6 实测修正（真实冒烟跑出来的误报）：
        - **线索库（zone=clue）不得用来判冲突**。线索库只存“摘要/线索”，本来就不可引用；
          而同一份法规在不同运行里，法宝返回的可能是**不同段落**——用线索库的片段去和
          MCP 的另一个片段比，会把同一份法规误判成“冲突”，白白挡住导出。
        - 两边规范化后互为包含 → 也算 dual（同一份文件的不同截取，与 2-4 对用户材料的口径一致）。
        """
        if source.origin == "local":
            return "single_local"
        if source.origin != "mcp":
            return "not_applicable"
        entry = self.get("statute" if source.kind == "statute" else "case", source.identifier)
        if entry is None or not (entry.text or "").strip() or not (source.quote or "").strip():
            return "single_mcp"
        if entry.zone != ZONE_CITABLE:
            # 线索区（含摘要）不是权威原文，不能拿它判“与法宝不一致”
            return "single_mcp"
        own = normalize_text(entry.text)
        other = normalize_text(source.quote)
        if own == other or own in other or other in own:
            return "dual"
        return "conflict"

    # ------------------------------------------------------------ 统计
    def stats(self) -> dict[str, Any]:
        entries = self.all_entries()
        by_zone = {ZONE_CITABLE: 0, ZONE_CLUE: 0}
        by_type: dict[str, int] = {}
        by_origin: dict[str, int] = {}
        for entry in entries:
            by_zone[entry.zone] = by_zone.get(entry.zone, 0) + 1
            by_type[entry.content_type] = by_type.get(entry.content_type, 0) + 1
            by_origin[entry.origin] = by_origin.get(entry.origin, 0) + 1
        index = self._load_index()
        return {
            "enabled": self.enabled,
            "dir": str(self.dir),
            "entries": by_zone.get(ZONE_CITABLE, 0) + by_zone.get(ZONE_CLUE, 0),
            "entries_citable": by_zone.get(ZONE_CITABLE, 0),
            "entries_clue": by_zone.get(ZONE_CLUE, 0),
            "by_type": by_type,
            "by_origin": by_origin,
            "bytes": self._bytes(),
            "max_entries": self.max_entries,
            "max_bytes": self.max_bytes,
            "updated_at": index.get("updated_at") or index.get("rebuilt_at") or "",
        }


_lib: Library | None = None


def get_library(cfg: Config | None = None) -> Library:
    global _lib
    if _lib is None or cfg is not None:
        from .config import get_config

        _lib = Library(cfg or get_config())
    return _lib
