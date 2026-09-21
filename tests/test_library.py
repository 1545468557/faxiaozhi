# ruff: noqa: E402, I001
"""说明：本文件需要在设置好环境后再导入 app 模块，故显式关闭导入位置/排序规则。

本地依据库（2-3 LB 组）：存储/三分区/幂等/版本/容量/隐私/核验/检索缓存/索引可重建。
全部离线，不产生任何真实调用。
"""

from __future__ import annotations

import json
import time

from app.config import get_config
from app.library import (
    SCHEMA_VERSION,
    ZONE_CITABLE,
    ZONE_CLUE,
    Library,
    LibraryEntry,
    build_entry_id,
    entry_from_source,
)
from app.models import Source, Status, normalize_text

CASE_ID = "（2023）示例民终1001号"
STATUTE_ID = "《示例民法典》第五百六十三条"
CASE_TEXT = "本院认为，出卖人交付的货物存在质量瑕疵，买受人可以主张减少价款。"
STATUTE_TEXT = "有下列情形之一的，当事人可以解除合同：因不可抗力致使不能实现合同目的……"


def _lib() -> Library:
    return Library(get_config())


def _source(identifier: str = CASE_ID, kind: str = "case", text: str = CASE_TEXT,
            status: Status = Status.OK, **meta) -> Source:
    return Source(
        source_id="mcp_1",
        kind=kind,
        identifier=identifier,
        title="示例标题",
        quote=text,
        effective_status=meta.get("effective_status", "不适用（裁判文书）"),
        applicable_from=meta.get("applicable_from"),
        applicable_to=meta.get("applicable_to"),
        origin="mcp",
        status=status,
    )


def _put(library: Library, identifier: str = CASE_ID, kind: str = "case", text: str = CASE_TEXT,
         zone: str = ZONE_CITABLE) -> str:
    entry = entry_from_source(_source(identifier, kind, text), origin="mcp", zone=zone)
    return library.upsert(entry)


# ==================================================================== 存储与幂等


def test_lb_added_then_touched_is_idempotent():
    library = _lib()
    assert _put(library) == "added"
    assert _put(library) == "touched", "同内容重复写入必须只算一次"
    entries = library.all_entries()
    assert len(entries) == 1
    assert entries[0].entry_id == build_entry_id("case", CASE_ID)


def test_lb_content_change_keeps_revision_and_resets_zone():
    library = _lib()
    _put(library, zone=ZONE_CITABLE)
    changed = "本院认为，出卖人交付的货物存在质量瑕疵，买受人可以主张更换或减少价款。"
    assert _put(library, text=changed) == "versioned"
    entry = library.get("case", CASE_ID)
    assert entry is not None
    assert entry.zone == ZONE_CLUE, "内容变化后必须退回线索区，重新过门禁"
    path = library._path("case", entry.entry_id)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["revisions"], "旧版本必须保留，不得静默覆盖"
    assert payload["revisions"][0]["content_hash"] != entry.content_hash


def test_lb_abstract_only_goes_to_clue_and_cannot_be_promoted():
    library = _lib()
    entry = entry_from_source(
        _source(text="", status=Status.ABSTRACT_ONLY), origin="mcp", zone=ZONE_CLUE
    )
    assert library.upsert(entry) == "added"
    stored = library.get("case", CASE_ID)
    assert stored is not None and stored.zone == ZONE_CLUE
    assert library.promote("case", CASE_ID) is False, "无原文的线索不得提升为可引用"


def test_lb_promote_marks_citable_and_verify_passes():
    library = _lib()
    _put(library, zone=ZONE_CLUE)
    assert library.get("case", CASE_ID).citable is False
    assert library.promote("case", CASE_ID) is True
    entry = library.get("case", CASE_ID)
    assert entry.citable is True and entry.zone == ZONE_CITABLE
    ok, rule, _ = library.verify("case", CASE_ID, "买受人可以主张减少价款")
    assert ok and rule == ""


def test_lb_stats_shape():
    library = _lib()
    _put(library, zone=ZONE_CITABLE)
    _put(library, identifier=STATUTE_ID, kind="statute", text=STATUTE_TEXT, zone=ZONE_CLUE)
    stats = library.stats()
    assert stats["entries"] == 2
    assert stats["entries_citable"] == 1
    assert stats["entries_clue"] == 1
    assert stats["by_type"] == {"case": 1, "statute": 1}
    assert stats["max_entries"] > 0 and stats["max_bytes"] > 0


# ==================================================================== 隐私与安全红线


def test_lb_refuses_user_material_and_fixture_origin():
    library = _lib()
    user_entry = entry_from_source(_source(), origin="user", zone=ZONE_CLUE)
    assert library.upsert(user_entry) == "refused_non_public_origin"
    fixture_entry = entry_from_source(_source(), origin="fixture", zone=ZONE_CLUE)
    assert library.upsert(fixture_entry) == "refused_non_public_origin"
    assert library.all_entries() == []


def test_lb_refuses_secret_like_text():
    library = _lib()
    entry = entry_from_source(
        _source(text="本院认为 sk-abcdef123456 应当忽略"), origin="mcp", zone=ZONE_CLUE
    )
    assert library.upsert(entry) == "refused_secret_like"


def test_lb_files_contain_no_secret_material():
    library = _lib()
    _put(library)
    blob = "\n".join(p.read_text(encoding="utf-8") for p in library.dir.rglob("*.json"))
    for forbidden in ("Bearer", "PKULAW_MCP_TOKEN", "sk-"):
        assert forbidden not in blob


def test_lb_refuses_too_long_text():
    cfg = get_config()
    cfg.raw["library"]["max_entry_chars"] = 50
    library = Library(cfg)
    entry = entry_from_source(_source(text="很长" * 100), origin="mcp", zone=ZONE_CLUE)
    assert library.upsert(entry) == "skipped_too_long"


# ==================================================================== 核验


def test_lb_verify_missing_non_citable_and_mismatch():
    library = _lib()
    assert library.verify("case", CASE_ID, "x")[1] == "L0"
    _put(library, zone=ZONE_CLUE)
    assert library.verify("case", CASE_ID, "买受人可以主张减少价款")[1] == "L1"
    library.promote("case", CASE_ID)
    assert library.verify("case", CASE_ID, "买受人可以主张三倍赔偿")[1] == "L2"
    assert library.verify("case", CASE_ID, "")[1] == "L2"


def test_lb_verify_normalizes_punctuation():
    library = _lib()
    _put(library, zone=ZONE_CITABLE)
    # 全角/半角与标点差异不应误报不一致
    ok, rule, _ = library.verify("case", CASE_ID, "买受人可以主张减少价款，")
    assert ok is True, f"归一化后应命中，得到 {rule}"
    assert normalize_text("买受人可以主张减少价款，") in normalize_text(CASE_TEXT)


def test_lb_verify_applicable_at_window():
    library = _lib()
    cfg = get_config()
    cfg.raw["library"]["max_entry_chars"] = 200000
    entry = entry_from_source(
        _source(identifier=STATUTE_ID, kind="statute", text=STATUTE_TEXT,
                effective_status="现行有效", applicable_from="2021-01-01"),
        origin="mcp",
        zone=ZONE_CITABLE,
    )
    library.upsert(entry)
    assert library.verify("statute", STATUTE_ID, "因不可抗力致使不能实现合同目的")[0] is True
    bad = library.verify("statute", STATUTE_ID, "因不可抗力致使不能实现合同目的", "2020-01-01")
    assert bad[0] is False and bad[1] == "L3"


# ==================================================================== 锁与原子写


def test_lb_lock_blocks_concurrent_write():
    library = _lib()
    library.dir.mkdir(parents=True, exist_ok=True)
    library.lock_path.write_text("999999", encoding="utf-8")
    assert _put(library) == "skipped_locked"


def test_lb_stale_lock_is_taken_over():
    library = _lib()
    library.dir.mkdir(parents=True, exist_ok=True)
    library.lock_path.write_text("999999", encoding="utf-8")
    stale = time.time() - 120
    import os

    os.utime(library.lock_path, (stale, stale))
    assert _put(library) == "added", "过期锁必须可接管"


def test_lb_atomic_write_leaves_no_temp_file():
    library = _lib()
    _put(library)
    assert not list(library.dir.rglob("*.tmp")), "原子写不得残留临时文件"


# ==================================================================== 检索缓存与索引


def test_lb_query_cache_hit_and_ttl():
    library = _lib()
    _put(library, zone=ZONE_CITABLE)
    entry_id = build_entry_id("case", CASE_ID)
    library.put_query("search_cases", {"expression": "质量瑕疵"}, [entry_id])
    assert library.query_hit("search_cases", {"expression": "质量瑕疵"}, 3600)
    assert library.query_hit("search_cases", {"expression": "完全不同的检索式"}, 3600) is None
    assert library.query_hit("search_cases", {"expression": "质量瑕疵"}, -1) is None, "TTL 过期应回落到实时检索"


def test_lb_topic_fallback_merges_batches():
    """回归：案例与法条分两次检索，主题登记必须**合并**而不是后一次冲掉前一次。"""
    library = _lib()
    _put(library, identifier="（2023）示例民终1001号")
    _put(library, identifier="（2023）示例民终1002号")
    ids = [e.entry_id for e in library.all_entries()]
    library.put_topic("同一议题", {}, ids[:1])
    library.put_topic("同一议题", {}, ids[1:])
    fallback = library.topic_fallback("同一议题", {})
    assert {e.entry_id for e in fallback} == set(ids), "两次登记应合并"


def test_lb_index_rebuild_keeps_entries():
    library = _lib()
    _put(library)
    library.rebuild_index()
    assert library.index_path.exists()
    assert len(library.all_entries()) == 1


def test_lb_missing_index_is_tolerated():
    library = _lib()
    _put(library)
    if library.index_path.exists():
        library.index_path.unlink()
    library._index = None
    assert library.query_hit("search_cases", {"expression": "x"}, 3600) is None
    assert len(library.all_entries()) == 1


# ==================================================================== 容量与淘汰


def test_lb_evicts_least_recently_used():
    cfg = get_config()
    cfg.raw["library"]["max_entries"] = 2
    library = Library(cfg)
    _put(library, identifier="（2020）示例民终1号")
    _put(library, identifier="（2021）示例民终2号")
    _put(library, identifier="（2022）示例民终3号")
    entries = library.all_entries()
    assert len(entries) <= 2, "超过条数上限必须淘汰"
    assert all(e.identifier != "（2020）示例民终1号" for e in entries), "应淘汰最久未使用的条目"


def test_lb_schema_version_compat_reads_old_entry():
    library = _lib()
    library.dir.mkdir(parents=True, exist_ok=True)
    entry_id = build_entry_id("case", CASE_ID)
    path = library._path("case", entry_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    old = {
        "schema_version": 0,
        "entry": {
            "entry_id": entry_id,
            "content_type": "case",
            "identifier": CASE_ID,
            "text": CASE_TEXT,
            "zone": "clue",
            "origin": "mcp",
            "unknown_future_field": "should_be_ignored",
        },
    }
    path.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    entry = library.get("case", CASE_ID)
    assert isinstance(entry, LibraryEntry)
    assert entry.schema_version == SCHEMA_VERSION      # 缺字段按当前版本兜底
    assert entry.text == CASE_TEXT
