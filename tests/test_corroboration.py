# ruff: noqa: E402, I001
"""说明：本文件需要在设置好环境后再导入 app 模块，故显式关闭导入位置/排序规则。

复用心智与双源印证（2-3 CB 组）：dual / single / conflict 四态、R7 冲突拦截、导出拦截、
Word 与依据区展示、指标计算、候选池不偏向历史。全部离线。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_config
from app.gates.runner import apply_gates
from app.library import ZONE_CITABLE, ZONE_CLUE, Library, build_entry_id, entry_from_source
from app.models import Source, Status
from app.observability import Observability
from app.render.docx import document_text, render_research_memo
from app.session import Session
from app.sources import SourceRouter
from app.sources.local_library import LocalLibraryProvider

CASE_ID = "（2023）示例民终1001号"
CASE_TEXT = "本院认为，出卖人交付的货物存在质量瑕疵，买受人可以主张减少价款。"
QUOTE = "买受人可以主张减少价款"


@pytest.fixture(autouse=True)
def _restore_cfg():
    cfg = get_config()
    snapshot = json.loads(json.dumps(cfg.raw.get("sources", {})))
    library_snapshot = json.loads(json.dumps(cfg.raw.get("library", {})))
    yield
    cfg.raw["sources"] = snapshot
    cfg.raw["library"] = library_snapshot


def _source(origin="mcp", text=CASE_TEXT, identifier=CASE_ID) -> Source:
    return Source(source_id="s1", kind="case", identifier=identifier, title="示例",
                  quote=text, effective_status="不适用（裁判文书）", origin=origin,
                  status=Status.OK)


def _seed(library: Library, text=CASE_TEXT, zone=ZONE_CITABLE) -> None:
    library.upsert(entry_from_source(_source(text=text), origin="mcp", zone=zone))


# ==================================================================== 印证四态


def test_cb_dual_when_local_and_mcp_agree():
    library = Library(get_config())
    _seed(library)
    assert library.corroborate(_source(origin="mcp")) == "dual"


def test_cb_single_mcp_when_local_has_nothing():
    library = Library(get_config())
    assert library.corroborate(_source(origin="mcp")) == "single_mcp"


def test_cb_single_local_for_local_hits():
    library = Library(get_config())
    assert library.corroborate(_source(origin="local")) == "single_local"


def test_cb_conflict_when_texts_differ():
    library = Library(get_config())
    _seed(library, text="本院认为，出卖人交付的货物存在质量瑕疵，买受人可以主张更换。")
    assert library.corroborate(_source(origin="mcp")) == "conflict"


def test_cb_not_applicable_for_fixture_or_user_material():
    library = Library(get_config())
    assert library.corroborate(_source(origin="fixture")) == "not_applicable"
    assert library.corroborate(_source(origin="user")) == "not_applicable"


# ============================================ 2-6 实测修正：线索库不得判冲突


def test_cb_clue_zone_never_produces_a_conflict():
    """回归（2-6 真实冒烟跑出来的误报）：线索库只存摘要/线索，不得拿它判“与法宝不一致”。

    真实场景：同一份法规在不同运行里法宝返回的是**不同段落**，而库里按文档级标识存，
    文本只是那一次返回的片段——两者一比就报“冲突”，把导出白白挡住。
    """
    library = Library(get_config())
    _seed(library, text="这是同一份法规的另一个段落，与本次返回的片段完全不同。", zone=ZONE_CLUE)
    assert library.corroborate(_source(origin="mcp")) == "single_mcp"


def test_cb_containment_is_dual_not_conflict():
    """一方包含另一方 → dual（同一份文件的不同截取），不构成矛盾。"""
    library = Library(get_config())
    library.upsert(
        entry_from_source(
            _source(text=CASE_TEXT + "\n补充说明行：本段由导出工具附加。"),
            origin="mcp",
            zone=ZONE_CITABLE,
        )
    )
    assert library.corroborate(_source(origin="mcp")) == "dual"


# ==================================================================== R7 冲突拦截


def _session_with(quote: str, text: str, corroboration: str) -> Session:
    session = Session()
    session.topic = "测试议题"
    session.source_pool["s1"] = Source(
        source_id="s1", kind="case", identifier=CASE_ID, title="示例", quote=text,
        effective_status="不适用（裁判文书）", origin="mcp", status=Status.OK,
        corroboration=corroboration,
    )
    session.matrix = [{"identifier": CASE_ID}]
    session.structured_output = {
        "conclusions": [
            {"text": "买受人可以主张减少价款。",
             "citations": [{"source_id": "s1", "identifier": CASE_ID, "quote": quote}]}
        ]
    }
    return session


def test_cb_r7_blocks_conflict_and_export():
    session = _session_with(QUOTE, CASE_TEXT, "conflict")
    apply_gates(session)
    rules = {r.rule for r in session.gate_report.rejected}
    assert "R7" in rules
    blockers = "；".join(session.export_blockers())
    assert "来源冲突" in blockers
    assert session.export_ready() is False


def test_cb_conflict_conclusion_is_degraded():
    session = _session_with(QUOTE, CASE_TEXT, "conflict")
    apply_gates(session)
    assert session.structured_output["_passed_conclusions"] == []
    assert session.structured_output["_degraded_conclusions"]


def test_cb_dual_passes_and_promotes_to_library():
    cfg = get_config()
    library = Library(cfg)
    _seed(library, zone="clue")
    assert library.get("case", CASE_ID).zone == "clue"
    session = _session_with(QUOTE, CASE_TEXT, "dual")
    apply_gates(session)
    assert not session.gate_report.rejected
    assert library.get("case", CASE_ID).zone == ZONE_CITABLE, "通过门禁的来源应提升为可引用"
    assert session.export_ready() is True


# ==================================================================== 展示


def test_cb_word_shows_origin_and_corroboration(tmp_path: Path):
    session = _session_with(QUOTE, CASE_TEXT, "dual")
    session.sample.set_candidates(["s1"])
    session.sample.confirm(["s1"], [])
    session.synthesis = session.structured_output
    apply_gates(session)
    target = tmp_path / "memo.docx"
    render_research_memo(session, target)
    from docx import Document

    text = document_text(Document(str(target)))
    assert "来源：法宝" in text
    assert "印证：双源一致" in text


def test_cb_evidence_includes_corroboration_fields():
    from app.server import app

    with TestClient(app) as client:
        sid = client.post("/api/session", json={}).json()["session_id"]
        from app.session import STORE

        session = STORE.get(sid)
        session.source_pool["s1"] = _source(origin="local")
        session.source_pool["s1"].corroboration = "single_local"
        session.source_pool["s1"].local_hit = True
        body = client.get(f"/api/session/{sid}/evidence").json()
    first = body["sources"][0]
    assert first["corroboration"] == "single_local"
    assert "本地依据库" in first["corroboration_text"]
    assert first["local_hit"] is True


def test_cb_library_endpoint_exposes_no_sensitive_fields():
    from app.server import app

    with TestClient(app) as client:
        body = client.get("/api/library").json()
    assert "entries" in body and "providers" in body
    blob = json.dumps(body, ensure_ascii=False)
    assert "dir" not in body
    assert "Bearer" not in blob and "PKULAW" not in blob


# ==================================================================== 指标


class _MetricsCfg:
    def __init__(self, path) -> None:
        self.metrics_path = path

    def get(self, key, default=None):
        return 30 if key == "metrics.retention_days" else default


def test_cb_metrics_rates_filter_by_source(tmp_path: Path):
    path = tmp_path / "metrics.jsonl"
    rows = [
        {"ts": "t", "source": "real", "event": "corroboration", "corr_dual": 6,
         "corr_single_mcp": 2, "corr_single_local": 2, "corr_conflict": 1,
         "local_hits": 2, "sources_total": 11},
        {"ts": "t", "source": "test", "event": "corroboration", "corr_dual": 100,
         "corr_single_mcp": 0, "corr_single_local": 0, "corr_conflict": 0,
         "local_hits": 0, "sources_total": 100},
    ]
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    obs = Observability(_MetricsCfg(path))
    real = obs.summary(source="real")
    assert real["corroboration"]["dual"] == 6
    assert real["corroboration"]["conflict"] == 1
    assert abs(real["corroboration"]["rate"] - 0.6) < 1e-6          # 6 / (6+2+2)
    assert abs(real["library_hit_rate"] - round(2 / 11, 4)) < 1e-6
    assert obs.summary(source="test")["corroboration"]["dual"] == 100


# ==================================================================== 不偏向历史


def test_cb_topic_fallback_when_mcp_is_down():
    """法宝不可用时，同主题的本地缓存可以回退（这是本阶段核心目标）。"""
    import asyncio

    from conftest import run_research

    topic = "设备质量存在瑕疵时，买受人能否主张解除合同？"
    cfg = get_config()
    library = Library(cfg)
    entry_a = entry_from_source(
        _source(identifier="（2023）示例民终1001号"), origin="mcp", zone=ZONE_CITABLE
    )
    entry_b = entry_from_source(
        _source(
            identifier="（2023）示例民终1002号",
            text="本院认为，设备质量瑕疵轻微，买受人主张减价应结合使用情况认定。",
        ),
        origin="mcp",
        zone=ZONE_CITABLE,
    )
    library.upsert(entry_a)
    library.upsert(entry_b)
    library.put_topic(topic, {}, [entry_a.entry_id, entry_b.entry_id])

    session = asyncio.run(run_research(topic=topic, scenario="interface_error", timeout=30.0))
    assert session.candidates, "法宝不可用时应回退到本地依据库缓存"
    local_sources = [s for s in session.source_pool.values() if s.origin == "local"]
    assert local_sources and all(s.local_hit for s in local_sources)
    assert all(s.corroboration == "single_local" for s in local_sources)
    assert session.synthesis, "回退后应能一路走到综合结论"


def test_cb_topic_fallback_is_empty_without_cache():
    import asyncio

    from conftest import run_research

    session = asyncio.run(
        run_research(topic="一个从未检索过的新议题", scenario="interface_error", timeout=30.0)
    )
    assert session.candidates == []
    assert session.failed_step, "没有缓存时必须如实标记失败，不得编造"


def test_cb_candidate_pool_is_not_padded_from_history():
    cfg = get_config()
    library = Library(cfg)
    _seed(library)
    library.put_query("search_cases", {"expression": "旧检索式"}, [build_entry_id("case", CASE_ID)])

    class _Remote:
        name = "pkulaw_mcp"
        authoritative = True
        calls = 0

        def search(self, **args):
            from app.models import ToolResult

            self.calls += 1
            fresh = _source(identifier="（2024）新案号", text="本院认为，新案例的原文。")
            return ToolResult(tool="search_cases", status=Status.OK, sources=[fresh],
                              detail="fake mcp", meta={})

        def fetch(self, identifier, **args):
            return None

    remote = _Remote()
    router = SourceRouter(cfg, "case", [LocalLibraryProvider(cfg, "case", library=library), remote],
                          library=library)
    result = router.search(expression="新检索式")
    assert remote.calls == 1
    assert [s.identifier for s in result.sources] == ["（2024）新案号"], "不得用历史条目填充新检索"
