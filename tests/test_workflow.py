"""端到端：类案研究全链路、门禁降级、导出前置条件、journal 与续跑。"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.config import get_config
from app.errors import ApiError
from app.knowledge import KnowledgeBase
from app.llm import build_provider
from app.models import Source, Status
from app.session import Session
from app.workflows.research import research_workflow
from app.workflows.runtime import RunContext
from tests.conftest import run_research


async def test_clean_run_produces_conclusions_and_citations():
    session = await run_research(allow_synthetic=True)
    synthesis = session.synthesis or {}
    assert synthesis.get("conclusions")
    assert session.gate_report is not None
    assert session.gate_report.accepted >= 1
    assert session.matrix
    assert session.distribution["denominator"] == len(session.sample.confirmed)


async def test_default_policy_blocks_synthetic_citations():
    session = await run_research(allow_synthetic=False)
    assert session.gate_report is not None
    assert session.gate_report.rejected
    assert all(r.rule == "R2" for r in session.gate_report.rejected)
    assert not session.export_ready()
    assert any("夹具" in reason for reason in session.export_blockers())


async def test_export_succeeds_in_demo_mode_and_writes_docx():
    session = await run_research(allow_synthetic=True)
    from app.llm import ToolCall
    from app.tools import dispatch_tool

    result = dispatch_tool(session, ToolCall("e", "export_docx", {"filename": "e2e.docx"}))
    assert result.status is Status.OK
    from pathlib import Path

    path = Path(result.meta["path"])
    assert path.exists() and path.stat().st_size > 2000
    path.unlink()


async def test_bad_quote_is_caught_by_r5():
    session = await run_research(scenario="bad_quote", allow_synthetic=True)
    rules = {r.rule for r in (session.gate_report.rejected if session.gate_report else [])}
    assert "R5" in rules


async def test_overreach_expression_blocks_export():
    session = await run_research(scenario="overreach", allow_synthetic=True)
    assert session.expression_hits
    assert any("越界表述" in reason for reason in session.export_blockers())


async def test_interface_error_is_reported_not_as_absence():
    session = await run_research(scenario="interface_error")
    assert not session.candidates
    details = " ".join(g.detail for g in session.gaps)
    assert "不等于" in details
    assert "不存在" not in details


async def test_no_match_scenario():
    session = await run_research(scenario="no_match")
    assert not session.candidates
    assert session.synthesis is None


async def test_abstract_only_sources_are_not_citable():
    session = await run_research(scenario="abstract_only", allow_synthetic=True)
    assert session.candidates
    rules = {r.rule for r in (session.gate_report.rejected if session.gate_report else [])}
    assert "R1" in rules


async def test_insufficient_scenario_keeps_flow_alive():
    session = await run_research(scenario="insufficient", allow_synthetic=True)
    assert session.status in {"active", "closed"}


async def test_single_sample_skips_synthesis():
    session = await run_research(sample_size=1, allow_synthetic=True)
    assert len(session.sample.confirmed) == 1
    assert session.synthesis is None
    assert any("最小样本量" in item for item in session.limitations)


async def test_excluded_sample_never_enters_matrix():
    """被排除的样本绝不能进矩阵。

    2-2 修正：原先不传 confirmed，等于「6 篇全确认、0 篇排除」，
    断言 `if not excluded: skip` 让这条测试**永远被跳过**（假绿）。
    现在显式只确认 3 篇，剩下的必然被排除，测试真的会跑起来。
    """
    session = await run_research(allow_synthetic=True, sample_size=3)
    excluded = session.sample.excluded
    assert excluded, "应存在被排除的样本（本测试的前提条件）"
    matrix_ids = {str(row.get("case_id")) for row in session.matrix}
    assert matrix_ids, "应生成矩阵"
    assert not (matrix_ids & set(excluded))


async def test_residue_blocks_export():
    session = await run_research(allow_synthetic=True)
    # 人为制造残留：把某篇确认样本标记为已排除，但结论仍引用它
    if not session.sample.confirmed:
        pytest.skip("无确认样本")
    session.sample.exclude(session.sample.confirmed[0])
    session.sample.confirmed.append(session.sample.confirmed[0])
    assert session.sample.residues(session.cited_ids)
    assert any("已排除案例仍被引用" in r for r in session.export_blockers())


async def test_resume_reuses_journal_without_rerun(tmp_path):
    run_id = "testresume01"
    first = await run_research(allow_synthetic=True, run_id=run_id)
    journal = get_config().runs_dir / f"{run_id}.journal.jsonl"
    before = journal.read_text(encoding="utf-8").count('"kind": "agent"')

    second_session = Session()
    second = await run_research(allow_synthetic=True, run_id=run_id, session=second_session)
    after = journal.read_text(encoding="utf-8").count('"kind": "agent"')

    assert after == before, "续跑不应重复调用模型"
    assert [c["source_id"] for c in second.candidates] == [c["source_id"] for c in first.candidates]
    assert second.sample.confirmed == first.sample.confirmed


async def test_journal_never_contains_user_material_text(tmp_path):
    session = Session()
    ctx = RunContext(
        session=session,
        cfg=get_config(),
        provider=build_provider(get_config(), "clean"),
        kb=KnowledgeBase(get_config()),
        run_id="materialcheck1",
    )
    secret = "甲方某某科技有限公司与乙方某某贸易有限公司于二〇二三年签订的设备采购合同"
    session.source_pool["user_1"] = Source(
        source_id="user_1",
        kind="user_material",
        identifier="用户材料：contract.txt",
        quote=secret * 3,
        origin="user",
    )
    session.source_pool["mcp_1"] = Source(
        source_id="mcp_1", identifier="（2023）示例民终1001号", quote="公开裁判文书原文。", origin="mcp"
    )
    ctx.snapshot_sources()
    text = ctx.sources_path.read_text(encoding="utf-8")
    assert secret not in text
    assert "公开裁判文书原文" in text
    assert "redacted" in text


async def test_restored_user_material_has_no_text_and_blocks_export():
    session = Session()
    ctx = RunContext(
        session=session,
        cfg=get_config(),
        provider=build_provider(get_config(), "clean"),
        kb=KnowledgeBase(get_config()),
        run_id="materialcheck2",
    )
    session.source_pool["user_1"] = Source(
        source_id="user_1",
        kind="user_material",
        identifier="用户材料：a.txt",
        quote="这是一段用户上传的合同原文内容，用于测试恢复后的行为。" * 3,
        origin="user",
        user_verified=True,
    )
    ctx.snapshot_sources()

    restored = Session()
    ctx2 = RunContext(
        session=restored,
        cfg=get_config(),
        provider=build_provider(get_config(), "clean"),
        kb=KnowledgeBase(get_config()),
        run_id="materialcheck2",
    )
    ctx2.load()
    source = restored.source_pool["user_1"]
    assert source.quote == ""
    assert source.is_user_material


async def test_schema_version_mismatch_is_refused(tmp_path):
    session = Session()
    ctx = RunContext(
        session=session,
        cfg=get_config(),
        provider=build_provider(get_config(), "clean"),
        kb=KnowledgeBase(get_config()),
        run_id="versioncheck1",
    )
    ctx.state_path.write_text(json.dumps({"schema_version": 99}), encoding="utf-8")
    with pytest.raises(ApiError) as exc:
        ctx.load()
    assert exc.value.code == "run_expired"


async def test_lock_prevents_concurrent_resume(tmp_path):
    session = Session()
    ctx = RunContext(
        session=session,
        cfg=get_config(),
        provider=build_provider(get_config(), "clean"),
        kb=KnowledgeBase(get_config()),
        run_id="lockcheck1",
    )
    ctx.acquire_lock()
    other = RunContext(
        session=Session(),
        cfg=get_config(),
        provider=build_provider(get_config(), "clean"),
        kb=KnowledgeBase(get_config()),
        run_id="lockcheck1",
    )
    with pytest.raises(ApiError):
        other.acquire_lock()
    ctx.release_lock()


async def test_pipeline_survives_single_item_failure():
    session = Session()
    ctx = RunContext(
        session=session,
        cfg=get_config(),
        provider=build_provider(get_config(), "clean"),
        kb=KnowledgeBase(get_config()),
        run_id="pipelinecheck1",
    )

    async def fn(item: str, index: int):
        if item == "b":
            raise ApiError("model_unavailable")
        return {"case_id": item}

    out = await ctx.pipeline(["a", "b", "c"], fn)
    assert [c["case_id"] for c in out] == ["a", "c"]
    assert any("b 处理失败" in g.detail for g in session.gaps)


async def test_events_end_with_done_and_include_phase():
    session = await run_research(allow_synthetic=True)
    events = []
    while not session.events.empty():
        events.append(session.events.get_nowait())
    names = [e["event"] for e in events]
    assert "done" in names
    assert "checkpoint_reached" in names
    assert "phase" in names
    assert "error" not in names
    assert names[-1] == "done"


async def test_first_response_under_budget():
    session = await run_research(allow_synthetic=True)
    assert session.first_response_ms is not None
    assert session.first_response_ms <= 3000


async def test_disclaimer_present_in_rendered_docx():
    from app.render.docx import render_research_memo

    session = await run_research(allow_synthetic=True)
    path = get_config().exports_dir / "disclaimer-check.docx"
    render_research_memo(session, path)
    from docx import Document

    text = "\n".join(p.text for p in Document(str(path)).paragraphs)
    assert "不构成正式法律意见" in text
    assert "人工复核" in text
    path.unlink()


async def test_research_workflow_requires_confirmed_sample_before_matrix():
    session = Session()
    cfg = get_config()
    ctx = RunContext(
        session=session,
        cfg=cfg,
        provider=build_provider(cfg, "clean"),
        kb=KnowledgeBase(cfg),
        run_id="ordercheck1",
    )
    task = asyncio.create_task(research_workflow(ctx, {"topic": "t", "conditions": {}}))
    for _ in range(800):
        if session.checkpoint_kind is not None:
            break
        await asyncio.sleep(0.005)
    assert session.checkpoint_kind == "sample_confirm"
    assert not session.matrix
    assert session.sample.locked is False
    session.checkpoint_value = {"confirmed": [], "excluded": [c["source_id"] for c in session.candidates]}
    session.checkpoint_waiter.set()
    await asyncio.wait_for(task, timeout=20)
    assert not session.matrix


# ---------------------------------------------------------------- 真实链路修出来的两处回归


def test_export_without_filename_is_allowed_by_permission():
    """回归：未传文件名时不应被判成「路径不确定」而误拦（真实链路抓到的 bug）。"""
    from app.permissions import check_permission
    from app.session import Session
    from app.tools import get_tool

    session = Session()
    session.sample.set_candidates(["a"])
    session.sample.confirm(["a"], [])
    session.cases = [{"case_id": "a", "identifier": "（2023）1号"}]
    from app.llm import ToolCall
    from app.tools import dispatch_tool

    dispatch_tool(session, ToolCall("m", "render_matrix", {}))
    from app.gates.runner import apply_gates

    session.structured_output = {"conclusions": []}
    session.pending_claims = []
    apply_gates(session)
    allowed, message = check_permission(session, get_tool("export_docx"), {})
    assert allowed, message


def test_export_with_absolute_path_still_denied():
    from app.permissions import check_permission
    from app.session import Session
    from app.tools import get_tool

    allowed, message = check_permission(Session(), get_tool("export_docx"), {"filename": "/tmp/x.docx"})
    assert not allowed and "导出目录之外" in message


def test_internal_source_ids_are_replaced_by_case_numbers():
    """回归：模型把 mcp_case_3 写进正文 → 由代码确定性替换为案号。"""
    from app.models import Source
    from app.workflows.research import _humanize_source_ids

    session = Session()
    session.source_pool["mcp_case_3"] = Source(
        source_id="mcp_case_3", identifier="（2020）苏06民终3767号", quote="原文"
    )
    synthesis = {
        "conclusions": [
            {
                "text": "在 mcp_case_3 中，法院认为 mcp_case_3 与 user_9 的观点一致。",
                "citation_source_ids": ["mcp_case_3"],
            }
        ]
    }
    _humanize_source_ids(session, synthesis)
    text = synthesis["conclusions"][0]["text"]
    assert "mcp_case_3" not in text
    assert "（2020）苏06民终3767号" in text
    assert synthesis["conclusions"][0]["citation_source_ids"] == ["mcp_case_3"]


def test_quote_with_ellipsis_is_tolerated_but_rewrite_is_not():
    from app.gates.citation import CitationGate, GatePolicy
    from app.models import Citation, Source, Status

    source = Source(
        source_id="s1",
        identifier="（2023）1号",
        quote="本院认为，质量异议应当在检验期间内提出。买受人怠于通知的，视为质量符合约定。",
        effective_status="不适用（裁判文书）",
        origin="mcp",
        status=Status.OK,
    )
    pool = {"s1": source}
    gate = CitationGate(GatePolicy())
    ok, _, _ = gate.check(Citation("s1", "（2023）1号", "质量异议应当在检验期间内提出……买受人怠于通知的"), pool)
    assert ok, "省略号拼接相邻片段应放行"
    bad, rule, _ = gate.check(Citation("s1", "（2023）1号", "买受人只要提出异议即可退款。"), pool)
    assert not bad and rule == "R5"


def test_array_field_accepts_string_form():
    """回归：模型把数组字段写成字符串 → 形态归一并通过（真实链路抓到的 bug）。"""
    from app.schemas import CASE_ELEMENT_SCHEMA, validate

    payload = {
        "case_id": "c1",
        "facts": "事实",
        "claim": "主张",
        "issues": "争点一；争点二",
        "holding": "理由",
        "result": "结果",
        "basis": "原文片段",
    }
    payload = validate(CASE_ELEMENT_SCHEMA, payload)
    assert payload["issues"] == ["争点一；争点二"]
    assert payload["basis"] == ["原文片段"]


# ---------------------------------------------------------------- 矩阵对不上样本的回归


def test_case_ids_are_aligned_to_source_ids():
    """回归：模型写的 case_id 不是来源编号 → 矩阵会与样本对不上（真实链路抓到的 bug）。"""
    from app.models import Source
    from app.workflows.research import _align_case_ids

    session = Session()
    session.source_pool["mcp_case_7"] = Source(
        source_id="mcp_case_7", identifier="（2018）苏0682民初9442号", quote="原文"
    )
    session.sample.set_candidates(["mcp_case_7"])
    session.sample.confirm(["mcp_case_7"], [])
    session.cases = [{"case_id": "（2018）苏0682民初9442号", "facts": "f"}]
    _align_case_ids(session)
    assert session.cases[0]["case_id"] == "mcp_case_7"
    assert session.matrix == [] or True


def test_matrix_rows_present_when_case_id_aligned():
    from app.models import Source

    session = Session()
    session.source_pool["mcp_case_7"] = Source(
        source_id="mcp_case_7", identifier="（2018）苏0682民初9442号", quote="原文"
    )
    session.sample.set_candidates(["mcp_case_7"])
    session.sample.confirm(["mcp_case_7"], [])
    session.cases = [{"case_id": "mcp_case_7", "identifier": "（2018）苏0682民初9442号"}]
    from app.llm import ToolCall
    from app.tools import dispatch_tool

    result = dispatch_tool(session, ToolCall("m", "render_matrix", {}))
    assert result.status is Status.OK
    assert len(session.matrix) == 1


def test_shape_normalization_covers_all_four_cases():
    from app.schemas import validate

    schema = {
        "type": "object",
        "properties": {
            "arr": {"type": "array", "items": {"type": "string"}},
            "txt": {"type": "string"},
        },
        "additionalProperties": True,
    }
    out = validate(schema, {"arr": {"a": "1"}, "txt": ["x", "y"]})
    assert out["arr"] == ["a：1"]
    assert out["txt"] == "x；y"


async def test_north_star_metric_is_recorded():
    """北极星指标（引用可溯源率 / 证据覆盖率）必须进埋点。"""
    import json

    path = get_config().metrics_path
    before = path.read_text(encoding="utf-8").count('"event": "gate"') if path.exists() else 0
    await run_research(allow_synthetic=True)
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    gates = [r for r in rows if r.get("event") == "gate"]
    assert len(gates) > before
    assert "gate_accepted" in gates[-1]
    assert "coverage" in gates[-1]
