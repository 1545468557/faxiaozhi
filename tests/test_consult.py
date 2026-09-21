# ruff: noqa: E402, I001
"""CS 组 · 法律咨询（阶段 2-5）。全部离线（stub 模型 + 无 MCP），不产生任何真实调用。

覆盖《阶段 2-5 技术开发文档》§十的 CS1–CS6：
问题识别 / 追问上限 / 依据与门禁 / 表达边界 / 隐私与导出 / 界面行为。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from docx import Document
from fastapi.testclient import TestClient

from app.config import get_config
from app.errors import ApiError
from app.gates.expression import ExpressionGuard
from app.gates.runner import apply_gates
from app.knowledge import KnowledgeBase
from app.models import Source, Status
from app.prompts import build_consult_identify_prompt
from app.render.docx import document_text
from app.server import app
from app.session import Session
from app.workflows import WORKFLOW_FOR_BRANCH, launch
from tests.conftest import set_policy

client = TestClient(app)

#: 短问题（离线 stub 判为「事实不足」→ 会追问）
SHORT_Q = "租客欠我房租，我能换锁吗"
#: 长问题（事实较全 → 不追问）
LONG_Q = (
    "我与承租人签订了一年期的房屋租赁合同，约定月租三千元、每季度首月五日前支付；"
    "对方已连续两个月未付租金，我已在微信上催告两次但对方拒不理睬。"
    "请问我是否可以自行更换门锁收回房屋？"
)


async def run_consult(
    question: str,
    session: Session | None = None,
    scenario: str = "clean",
    allow_synthetic: bool = True,
    timeout: float = 20.0,
) -> Session:
    set_policy(allow_synthetic=allow_synthetic)
    cfg = get_config()
    kb = KnowledgeBase(cfg)
    session = session or Session(branch="consult")
    task = asyncio.create_task(
        launch(
            session,
            cfg,
            kb,
            {"question": question, "consult": True},
            workflow="legal-consult",
            scenario=scenario,
        )
    )
    await asyncio.wait_for(task, timeout=timeout)
    return session


def _files_text(session: Session) -> str:
    """本次运行写盘的全部内容（journal / output / state / sources）。"""
    runs = get_config().runs_dir
    chunks: list[str] = []
    for suffix in (".json", ".journal.jsonl", ".output.json", ".sources.jsonl"):
        path = runs / f"{session.run_id}{suffix}"
        if path.exists():
            chunks.append(path.read_text(encoding="utf-8"))
    return "\n".join(chunks)


# ==================================================================== CS1 问题识别


async def test_cs01_identify_extracts_legal_relation_and_disputes():
    session = await run_consult(LONG_Q)
    issue = session.consult["issue"]
    assert issue["legal_relation"]
    assert issue["disputes"], "必须识别出争议焦点"
    assert isinstance(issue["given_facts"], list)


async def test_cs02_given_facts_and_missing_facts_are_separated():
    session = await run_consult(SHORT_Q)
    issue = session.consult["issue"]
    assert issue["given_facts"] and issue["missing_facts"]
    assert not set(issue["given_facts"]) & set(issue["missing_facts"])


def test_cs03_identify_prompt_forbids_citing_law_before_retrieval():
    """识别阶段还没检索，提示词必须明确禁止写具体法条号（C-02）。"""
    session = Session(branch="consult")
    prompt = build_consult_identify_prompt(session, ["示例问题"], 0, 3, [], 3)
    assert "不得凭记忆写具体法条号" in prompt
    assert "SOURCE" not in prompt.replace("【用户提供的信息", ""), "识别阶段不得提供任何原文块"


async def test_cs04_identify_output_never_contains_citations():
    """结构化产物里不存在引用字段——识别阶段不可能带引用。"""
    session = await run_consult(LONG_Q)
    assert "citations" not in json.dumps(session.consult["issue"], ensure_ascii=False)


# ==================================================================== CS2 追问


async def test_cs05_short_question_triggers_clarify_and_counts_one_round():
    session = await run_consult(SHORT_Q)
    assert session.consult["awaiting"] is True
    assert session.consult["rounds"] == 1, "每追问一次计数 +1（由代码维护）"
    assert session.consult["asked"], "必须记录问过什么，避免重复问"
    assert session.consult.get("answer") is None, "追问阶段不得提前给结论"


async def test_cs06_long_question_skips_clarify_entirely():
    session = await run_consult(LONG_Q)
    assert session.consult["awaiting"] is False
    assert session.consult["rounds"] == 0, "事实够用时不得追问"
    assert session.consult.get("answer"), "不追问就必须直接给解答"


async def test_cs07_clarifying_rounds_never_exceed_three():
    """连续补充很短的回答，追问轮数必须停在 3（PRD C-10 硬约束）。"""
    session = Session(branch="consult")
    session = await run_consult(SHORT_Q, session=session)
    for _ in range(6):
        if not session.consult.get("awaiting"):
            break
        session = await run_consult("补充：三千元", session=session)
    assert session.consult["rounds"] <= 3, f"追问轮数越界：{session.consult['rounds']}"
    assert session.consult["awaiting"] is False


async def test_cs08_after_cap_answer_is_based_on_explicit_assumptions():
    session = Session(branch="consult")
    session = await run_consult(SHORT_Q, session=session)
    for _ in range(6):
        if not session.consult.get("awaiting"):
            break
        session = await run_consult("补充：三千元", session=session)
    joined = "；".join(session.limitations)
    assert "追问已达上限" in joined, "到上限必须显式说明"
    assert "假设" in joined, "必须写清基于哪些假设，不得假装信息完整"
    assert session.consult.get("answer"), "到上限后必须出解答（而不是摆烂）"


async def test_cs09_questions_are_not_repeated_across_rounds():
    session = Session(branch="consult")
    session = await run_consult(SHORT_Q, session=session)
    for _ in range(6):
        if not session.consult.get("awaiting"):
            break
        session = await run_consult("补充：三千元", session=session)
    asked = session.consult["asked"]
    assert len(asked) == len(set(asked)), f"追问出现重复：{asked}"


async def test_cs10_questions_per_round_are_capped():
    session = await run_consult(SHORT_Q)
    limit = int(get_config().get("consult.max_questions_per_round", 3))
    assert 1 <= len(session.consult["asked"]) <= limit


# ==================================================================== CS3 依据与门禁


async def test_cs11_answer_citations_pass_the_citation_gate():
    session = await run_consult(LONG_Q)
    answer = session.consult["answer"]
    assert answer["conclusions"], "有依据时必须给出结论"
    assert session.gate_report is not None
    assert session.gate_report.accepted >= 1
    assert session.export_ready(), session.export_blockers()


async def test_cs12_retrieval_is_code_driven_for_both_statutes_and_cases():
    """依据检索由代码直接调用（不靠模型自觉）——法条与案例两类都必须真实发起。"""
    session = await run_consult(LONG_Q)
    kinds = {source.kind for source in session.source_pool.values()}
    assert "statute" in kinds, "必须检索法条"
    assert "case" in kinds, "必须检索案例"


async def test_cs13_bad_quote_is_rejected_and_not_used_as_conclusion():
    session = await run_consult(LONG_Q, scenario="bad_quote")
    assert session.gate_report is not None
    assert session.gate_report.rejected, "引用对不上必须被拦"
    passed = (session.structured_output or {}).get("_passed_conclusions") or []
    assert passed == [], "未通过核验的结论不得作为结论输出"
    assert any("未通过核验" in item for item in session.limitations)


async def test_cs14_no_match_yields_explicit_insufficient_not_fabrication():
    """检索无匹配 → 明确说「未检索到」，**绝不编造法条**（C-02）。"""
    session = await run_consult(LONG_Q, scenario="no_match")
    answer = session.consult["answer"]
    assert answer["conclusions"] == [], "没有依据就不许产出结论"
    assert "未检索到" in str(answer.get("insufficient") or "")
    assert session.consult["status"] == "insufficient"


async def test_cs15_interface_error_is_not_reported_as_no_law():
    """接口失败 ≠ 没有相关规定（C-05）：文案必须区分，且不得产出结论。"""
    session = Session(branch="consult")
    set_policy(allow_synthetic=False)
    cfg = get_config()
    task = asyncio.create_task(
        launch(
            session,
            cfg,
            KnowledgeBase(cfg),
            {"question": LONG_Q, "consult": True},
            workflow="legal-consult",
            scenario="interface_error",
        )
    )
    await asyncio.wait_for(task, timeout=20.0)
    joined = "；".join(session.limitations)
    assert "接口" in joined and "不等于" in joined
    assert "未检索到直接规定" not in joined, "接口失败不得说成「查不到」"
    assert not session.consult.get("answer")


async def test_cs16_consult_export_blockers_cover_conflict_and_gate():
    session = Session(branch="consult")
    blockers = "；".join(session.export_blockers())
    assert "尚未生成咨询解答" in blockers

    session.consult["answer"] = {"conclusions": [], "uncertainties": [], "next_steps": []}
    blockers = "；".join(session.export_blockers())
    assert "引用尚未经过门禁核验" in blockers


# ==================================================================== CS4 表达边界


async def test_cs17_overreach_is_rewritten_once_and_blocked_from_final_answer():
    session = await run_consult(LONG_Q, scenario="overreach")
    texts = "；".join(
        str(item.get("text", ""))
        for item in (session.consult.get("answer") or {}).get("conclusions") or []
    )
    assert "一定能赢" not in texts, "越界表述不得出现在最终解答里"
    assert any("越界表述" in gap.detail for gap in session.gaps), "改写必须留痕"


def test_cs18_consult_extra_patterns_come_from_config():
    cfg = get_config()
    phrases = cfg.get("consult.expression_extra") or []
    assert "一定能赢" in phrases and "本意见可替代律师" in phrases
    guard = ExpressionGuard([r"一定能赢", r"可直接提交法院"])
    hits = guard.scan("你放心，这个案子一定能赢，判决书可直接提交法院执行。")
    assert {h["text"] for h in hits} == {"一定能赢", "可直接提交法院"}


def test_cs19_apply_gates_uses_consult_extra_patterns_only_on_consult_branch():
    text = "你一定能赢。"
    consult = Session(branch="consult")
    consult.structured_output = {
        "conclusions": [{"text": text, "citation_source_ids": [], "citations": []}]
    }
    consult.pending_claims = []
    apply_gates(consult)
    assert consult.expression_hits, "咨询分支必须启用咨询专属拦截词"

    research = Session(branch="research")
    research.structured_output = {
        "conclusions": [{"text": text, "citation_source_ids": [], "citations": []}]
    }
    apply_gates(research)
    assert not any(h["text"] == "一定能赢" for h in research.expression_hits)


# ==================================================================== CS5 隐私与导出


async def test_cs20_question_text_never_lands_on_disk_or_metrics():
    """用户问题里可能含当事人具体事实：不落盘、不进 journal、不进指标（C-12 / D7）。"""
    marker = "张三丰在某小区被李四海拖欠租金并动手打人"
    session = await run_consult(f"{marker}，我该怎么办才能拿回钱？")
    dumped = _files_text(session)
    assert marker not in dumped, "问题原文不得写进运行存档"
    metrics = get_config().metrics_path
    if metrics.exists():
        assert marker not in metrics.read_text(encoding="utf-8"), "问题原文不得进指标文件"
    # 状态文件里 args 必须被脱敏
    state = json.loads((get_config().runs_dir / f"{session.run_id}.json").read_text(encoding="utf-8"))
    assert state["args"]["question"] == ""
    assert state["args"].get("question_redacted") is True


async def test_cs21_consult_does_not_touch_user_material_library():
    from app.library import get_library

    marker = "赵六光在仓库门口被王五甲拖欠货款"
    await run_consult(f"{marker}，我该怎么起诉？")
    stats = get_library().stats()
    assert stats.get("entries_citable", 0) >= 0
    library_dir = get_config().path("library.dir", "data/library")
    if library_dir.exists():
        joined = "\n".join(
            p.read_text(encoding="utf-8") for p in library_dir.rglob("*") if p.is_file()
        )
        assert marker not in joined, "咨询问题不得进本地依据库"


async def test_cs22_restart_without_question_refuses_instead_of_faking():
    """问题原文不落盘 → 恢复场景拿不到问题，必须明确拒绝（与 2-4 材料同一条底线）。"""
    session = Session(branch="consult")
    session.branch = "consult"
    cfg = get_config()
    result = await asyncio.wait_for(
        launch(session, cfg, KnowledgeBase(cfg), {"consult": True}, workflow="legal-consult"),
        timeout=10.0,
    )
    assert result["status"] == "failed"
    assert result["error"] == "consult_not_recoverable"


async def test_cs23_export_memo_contains_declaration_citations_and_gaps(tmp_path):
    session = await run_consult(LONG_Q)
    from app.llm import ToolCall
    from app.tools import dispatch_tool

    result = dispatch_tool(
        session,
        ToolCall(id="e1", name="export_docx", arguments={"filename": "咨询备忘.docx"}),
    )
    assert result.status is Status.OK, result.detail
    text = document_text(Document(str(result.meta["path"])))
    assert "咨询备忘" in text
    assert "不得替代律师" in text or "不构成正式法律意见" in text
    assert "本次用到的依据" in text
    assert "不确定与风险" in text
    assert "sk-" not in text and "/Users/" not in text


def test_cs24_consult_export_endpoint_rejects_non_consult_session():
    created = client.post("/api/session", json={"branch": "research"}).json()
    sid = created["session_id"]
    response = client.post(f"/api/session/{sid}/consult/export")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


def test_cs25_consult_answer_endpoint_requires_pending_question():
    created = client.post("/api/session", json={"branch": "consult"}).json()
    sid = created["session_id"]
    response = client.post(f"/api/session/{sid}/consult/answer", json={"facts": "三千元"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


# ==================================================================== CS6 界面与路由


def test_cs26_branch_routing_and_bootstrap_expose_consult():
    assert WORKFLOW_FOR_BRANCH["consult"] == "legal-consult"
    assert WORKFLOW_FOR_BRANCH["research"] == "legal-research"
    body = client.get("/api/bootstrap").json()
    consult = [m for m in body["modules"] if m["id"] == "consult"]
    assert consult and consult[0]["enabled"] is True, "2-5 起咨询模块必须可见"


def test_cs27_ui_has_consult_entry_and_round_counter():
    html = (Path(__file__).resolve().parent.parent / "web" / "index.html").read_text(encoding="utf-8")
    assert 'data-tab="consult"' in html, "界面必须有法律咨询入口"
    assert "最多" in html and "轮" in html, "界面必须显示追问轮数上限"
    assert "只作研究参考" in html or "不构成正式法律意见" in html


def test_cs28_ui_keeps_no_match_and_interface_error_copy_apart():
    html = (Path(__file__).resolve().parent.parent / "web" / "index.html").read_text(encoding="utf-8")
    assert 'no_match: "没匹配到"' in html
    assert 'interface_error: "接口调用失败"' in html
    assert "这不等于「没有相关案例」" in html


# ==================================================================== 回归：没弄坏 2-4


async def test_cs29_research_branch_is_unaffected():
    from tests.conftest import run_research

    session = await run_research(allow_synthetic=True)
    assert session.branch == "research"
    assert session.matrix, "2-4 的链路必须仍然产出矩阵"
    assert session.consult == {}, "研究分支不得写入咨询状态"


def test_cs30_consult_state_is_isolated_per_session():
    first = Session(branch="consult")
    second = Session(branch="consult")
    first.consult["facts"] = ["a"]
    assert second.consult == {}, "会话之间不得共享咨询状态"


# ==================================================================== 引用核验的兜底


def test_cs31_sources_without_quote_cannot_be_cited_in_consult():
    """无逐字原文 → 门禁必拒（宁可拒绝，不放宽）。"""
    session = Session(branch="consult")
    session.source_pool["s1"] = Source(
        source_id="s1", kind="statute", identifier="示例法第一条", quote="", origin="mcp"
    )
    session.structured_output = {
        "conclusions": [
            {
                "text": "示例结论",
                "citation_source_ids": ["s1"],
                "citations": [{"source_id": "s1", "identifier": "示例法第一条", "quote": ""}],
            }
        ]
    }
    session.pending_claims = []
    report = apply_gates(session)
    assert report.rejected, "空引用必须被拦"


def test_cs32_rounds_exhausted_error_code_exists_with_clear_message():
    error = ApiError("rounds_exhausted")
    assert error.http_status == 409
    assert "追问轮数已达上限" in error.message


# ==================================================================== 鲁棒性


async def test_cs33_unparsable_answer_fails_with_retry_instead_of_empty_success():
    """坏格式是失败，不能变成“已完成但没有结论”；案情与依据保留供当前步骤重试。"""
    session = await run_consult(LONG_Q, scenario="consult_bad_schema")
    assert session.consult.get("answer") is None
    assert session.consult["status"] == "failed"
    assert session.failed_step == "解答"
    assert session.can_retry
    events = []
    while not session.events.empty():
        events.append(session.events.get_nowait())
    assert events[-1]["event"] == "error"
    assert events[-1]["data"]["error"]["code"] == "schema_invalid"
    assert not any(event["event"] == "done" for event in events)


async def test_cs34_failed_answer_cannot_export_a_normal_memo():
    """系统生成失败不能冒充一份可导出的“依据不足”咨询备忘。"""
    session = await run_consult(LONG_Q, scenario="consult_bad_schema")
    assert session.consult["status"] == "failed"
    assert session.consult.get("answer") is None
    assert session.export_blockers()


async def test_cs35_uploaded_and_verified_material_survives_and_can_be_cited():
    """回归（实测发现）：咨询允许把上传材料当背景依据（PRD §3.2.6 的 consult 含 parse_document）。

    修复前 `_reset_for_new_question` 把 source_pool 整体清空，用户刚上传的材料会被删掉，
    表现为“传了却没被用上”。现在只清上一问检索到的来源。
    """
    from tests.test_materials import CASE_TEXT, _upload

    session = Session(branch="consult")
    result = _upload(session, "m.txt", CASE_TEXT)
    assert result.status is Status.OK, result.detail
    source = result.sources[0]
    session.verify_material(source.source_id)

    session = await run_consult(LONG_Q, session=session)
    assert source.source_id in session.source_pool, "已核验的用户材料不得在开新问题时被清掉"
    assert session.source_pool[source.source_id].is_user_material is True
