# ruff: noqa: E402, I001
"""CT 组 · 合同审查（阶段 2-6）。全部离线（stub 模型 + 无 MCP），不产生任何真实调用。

覆盖《阶段 2-6 技术开发文档》§十的 CT1–CT6：
入口与立场 / 解析与原文定位 / 依据与门禁 / 表达边界与 C-14 / 隐私与导出 / 界面行为。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from docx import Document
from fastapi.testclient import TestClient

from app.config import get_config
from app.contract import (
    RISK_KIND_LABELS,
    locate_anchor,
    split_clauses,
)
from app.errors import ApiError
from app.gates.runner import apply_gates
from app.knowledge import KnowledgeBase
from app.materials import ROLE_CONTRACT, detect_role
from app.models import Status
from app.render.docx import document_text
from app.server import app
from app.session import Session
from app.tools import dispatch_tool
from app.workflows import launch
from app.workflows.contract import REVIEW_MARK, _normalize_risks
from tests.conftest import set_policy
from tests.test_materials import _upload

client = TestClient(app)

CONTRACT_TEXT = """房屋租赁合同

甲方（出租人）：张三
乙方（承租人）：李四

第一条 租赁物
甲方将其所有的位于某市某区的房屋出租给乙方居住使用，租期一年。

第二条 租金及支付
月租金三千元，乙方应于每月五日前支付，逾期按日千分之五计收违约金。

第三条 违约责任
乙方逾期支付租金的，甲方有权直接更换门锁并收回房屋，且不退还押金。

第四条 其他
本合同未尽事宜由双方协商解决。
"""


async def run_contract(
    contract_text: str | None = CONTRACT_TEXT,
    stance: str | None = "party_b",
    verify: bool = True,
    session: Session | None = None,
    scenario: str = "clean",
    allow_synthetic: bool = True,
    timeout: float = 25.0,
) -> tuple[Session, asyncio.Task | None]:
    """跑一次合同审查；到立场 checkpoint 时按 `stance` 自动提交（`stance=None` 则不提交）。"""
    set_policy(allow_synthetic=allow_synthetic)
    cfg = get_config()
    kb = KnowledgeBase(cfg)
    session = session or Session(branch="contract")
    if contract_text is not None:
        result = _upload(session, "房屋租赁合同.txt", contract_text, role=ROLE_CONTRACT)
        assert result.status is Status.OK, result.detail
        if verify:
            session.verify_material(result.sources[0].source_id)

    task = asyncio.create_task(
        launch(
            session,
            cfg,
            kb,
            {"contract": True},
            workflow="legal-contract",
            scenario=scenario,
        )
    )
    for _ in range(1200):
        if session.checkpoint_kind == "stance_confirm" or task.done():
            break
        await asyncio.sleep(0.005)
    if session.checkpoint_kind == "stance_confirm":
        if stance is None:
            return session, task            # 调用方自行检查“被挡住”
        session.checkpoint_value = {"stance": stance}
        session.checkpoint_waiter.set()
    await asyncio.wait_for(task, timeout=timeout)
    return session, task


def _risks(session: Session) -> list[dict]:
    return list(session.contract.get("risks") or [])


# ==================================================================== CT1 入口与立场


async def test_ct01_no_contract_is_refused_with_clear_reason():
    session, _ = await run_contract(contract_text=None)
    assert session.contract.get("risks") in (None, [])
    assert session.checkpoint_kind is None, "没有合同就不该走到立场确认"


async def test_ct02_contract_must_be_verified_before_review():
    session = Session(branch="contract")
    result = _upload(session, "未核验合同.txt", CONTRACT_TEXT, role=ROLE_CONTRACT)
    assert result.status is Status.OK
    cfg = get_config()
    output = await asyncio.wait_for(
        launch(session, cfg, KnowledgeBase(cfg), {"contract": True}, workflow="legal-contract"),
        timeout=15.0,
    )
    assert output["status"] == "failed"
    assert output["error"] == "empty_material"
    assert session.checkpoint_kind is None


async def test_ct03_stance_checkpoint_does_not_touch_sample_lock():
    """回归（文档 §7.4 标记的雷）：立场确认不得顺手锁上一个空样本。"""
    session, task = await run_contract(stance=None)
    assert session.checkpoint_kind == "stance_confirm", "未确认立场时必须停在人工门"
    assert session.sample.locked is False, "立场确认不得污染样本锁"
    assert session.sample.confirmed == [] and session.sample.excluded == []
    session.checkpoint_value = {"stance": "party_b"}
    session.checkpoint_waiter.set()
    await asyncio.wait_for(task, timeout=25.0)
    assert session.contract["stance"] == "party_b"


async def test_ct04_stance_must_be_in_whitelist():
    session, task = await run_contract(stance="party_c")
    assert session.contract.get("stance") is None
    assert "party_a" in " ".join(
        option["value"] for option in [{"value": "party_a"}, {"value": "party_b"}, {"value": "neutral"}]
    )
    assert task.done()


async def test_ct05_endpoint_rejects_invalid_stance():
    created = client.post("/api/session", json={"branch": "contract"}).json()
    sid = created["session_id"]
    response = client.post(
        f"/api/session/{sid}/checkpoint",
        json={"kind": "stance_confirm", "payload": {"stance": "party_c"}},
    )
    assert response.status_code in (400, 409)
    assert response.json()["error"]["code"] in {"invalid_request", "checkpoint_mismatch"}


async def test_ct06_inferred_parties_are_used_as_defaults():
    session, task = await run_contract(stance=None)
    parties = session.contract.get("parties") or {}
    assert parties.get("party_a") == "张三"
    assert parties.get("party_b") == "李四"
    session.checkpoint_value = {"stance": "neutral"}
    session.checkpoint_waiter.set()
    await asyncio.wait_for(task, timeout=25.0)


async def test_ct07_contract_role_is_recognised_and_can_be_changed():
    role, source = detect_role(CONTRACT_TEXT, "房屋租赁合同.txt")
    assert role == ROLE_CONTRACT and source == "detected"
    session = Session(branch="contract")
    result = _upload(session, "房屋租赁合同.txt", CONTRACT_TEXT, role=ROLE_CONTRACT)
    assert result.status is Status.OK, result.detail
    assert result.sources[0].kind == ROLE_CONTRACT
    labels = {record.role_label for record in session.material_registry.records.values()}
    assert labels == {"合同"}


def test_ct08_contract_endpoints_reject_other_branches():
    created = client.post("/api/session", json={"branch": "research"}).json()
    sid = created["session_id"]
    assert client.post(f"/api/session/{sid}/contract/review").status_code == 400
    assert client.post(f"/api/session/{sid}/contract/export").status_code == 400


# ==================================================================== CT2 解析与定位


def test_ct09_clause_offsets_are_exact():
    clause_set = split_clauses(CONTRACT_TEXT)
    assert len(clause_set.clauses) >= 4
    for clause in clause_set.clauses:
        assert CONTRACT_TEXT[clause.start : clause.end] == clause.text, clause.clause_id


def test_ct10_headings_are_recognised():
    headings = [c.heading for c in split_clauses(CONTRACT_TEXT).clauses]
    assert "合同首部" in headings
    assert any(h.startswith("第三条") for h in headings)


def test_ct11_plain_text_falls_back_to_paragraphs():
    clause_set = split_clauses("这是第一段内容，长度足够定位使用。\n\n这是第二段内容，也足够长。")
    assert len(clause_set.clauses) == 2
    assert all(c.heading == "（无编号段落）" for c in clause_set.clauses)


def test_ct12_truncation_is_explicit():
    clause_set = split_clauses(CONTRACT_TEXT, max_clauses=2)
    assert clause_set.truncated is True
    assert len(clause_set.clauses) == 2


async def test_ct13_long_contract_is_truncated_with_a_visible_note():
    # 一个超长条款：正文超过 contract.max_chars，必须显式说明“尾部可能未被审查”
    long_text = "房屋租赁合同\n甲方：张三\n乙方：李四\n第一条 长条款\n" + "本合同条款内容足够长以便定位使用。" * 4000
    assert len(long_text) > int(get_config().get("contract.max_chars", 60000))
    session, _ = await run_contract(contract_text=long_text)
    joined = " ".join(session.limitations)
    assert "未送入模型" in joined or "未被审查" in joined


async def test_ct14_parse_failure_is_reported_not_silent():
    session = Session(branch="contract")
    result = _upload(session, "空的合同.txt", "   \n  ", role=ROLE_CONTRACT)
    assert result.status is not Status.OK
    assert "empty" in (result.error_kind or "") or result.status is Status.INSUFFICIENT


def test_ct15_anchor_locate_and_reject():
    clause_set = split_clauses(CONTRACT_TEXT)
    clause = [c for c in clause_set.clauses if "更换门锁" in c.text][0]
    located = locate_anchor(CONTRACT_TEXT, clause, "甲方有权直接更换门锁并收回房屋")
    assert located is not None
    assert CONTRACT_TEXT[located[0] : located[1]] == "甲方有权直接更换门锁并收回房屋"
    assert locate_anchor(CONTRACT_TEXT, clause, "合同里没有这句话") is None


# ==================================================================== CT3 依据与门禁


async def test_ct16_risks_carry_located_anchors_and_verified_basis():
    session, _ = await run_contract()
    risks = _risks(session)
    assert risks, "必须产出风险条目"
    assert session.gate_report is not None
    assert session.gate_report.accepted >= 1
    for risk in risks:
        assert risk["kind"] in {"illegal", "commercial", "wording"}
        assert risk["level"] in {"high", "medium", "low"}


async def test_ct17_anchor_failure_is_marked_and_excluded_from_linkage():
    session, _ = await run_contract(scenario="contract_bad_anchor")
    risks = _risks(session)
    assert risks
    assert all(risk["locateOk"] is False for risk in risks)
    assert all(risk["start"] == -1 and risk["end"] == -1 for risk in risks)
    assert any("无法在合同原文中定位" in gap.detail for gap in session.gaps)


async def test_ct18_risk_without_basis_is_downgraded_not_fabricated():
    session, _ = await run_contract(scenario="contract_no_basis")
    risks = _risks(session)
    assert any(risk["basisStatus"] == "no_basis" for risk in risks)
    assert session.contract["counts"]["no_basis"] >= 1


async def test_ct19_rejected_citation_downgrades_only_that_risk():
    session, _ = await run_contract(scenario="bad_quote")
    risks = _risks(session)
    assert session.gate_report is not None and session.gate_report.rejected
    assert all(risk["basisStatus"] != "verified" for risk in risks if risk.get("basis"))


async def test_ct20_status_counts_add_up():
    session, _ = await run_contract()
    counts = session.contract["counts"]
    assert counts["total"] == len(_risks(session))
    assert counts["total"] == counts["verified"] + counts["no_basis"] + sum(
        1 for r in _risks(session) if r["basisStatus"] == "rejected"
    )
    assert sum(counts["by_level"].values()) == counts["total"]


async def test_ct21_risk_without_anchor_cannot_be_forced_into_the_contract():
    """锚点必须是条款里**逐字出现**的片段；凭空写的片段定位必然失败。"""
    session, _ = await run_contract()
    clause_set = split_clauses(CONTRACT_TEXT)
    clause = clause_set.clauses[0]
    assert locate_anchor(CONTRACT_TEXT, clause, "甲方自愿放弃全部权利") is None


async def test_ct22_interface_failure_is_not_reported_as_no_regulation():
    session = Session(branch="contract")
    result = _upload(session, "房屋租赁合同.txt", CONTRACT_TEXT, role=ROLE_CONTRACT)
    session.verify_material(result.sources[0].source_id)
    cfg = get_config()
    set_policy(allow_synthetic=False)
    task = asyncio.create_task(
        launch(
            session,
            cfg,
            KnowledgeBase(cfg),
            {"contract": True},
            workflow="legal-contract",
            scenario="interface_error",
        )
    )
    for _ in range(1200):
        if session.checkpoint_kind == "stance_confirm" or task.done():
            break
        await asyncio.sleep(0.005)
    if session.checkpoint_kind == "stance_confirm":
        session.checkpoint_value = {"stance": "party_b"}
        session.checkpoint_waiter.set()
    await asyncio.wait_for(task, timeout=25.0)
    joined = " ".join(session.limitations)
    assert "接口" in joined and "不等于" in joined
    assert not session.contract.get("risks")


async def test_ct23_max_risks_is_enforced_with_a_note():
    session, _ = await run_contract()
    cfg = get_config()
    assert cfg.get("contract.max_risks", 40) > 0
    assert len(_risks(session)) <= int(cfg.get("contract.max_risks", 40))


# ==================================================================== CT4 表达边界与 C-14


async def test_ct24_overreach_is_rewritten_and_blocked_from_the_report():
    session, _ = await run_contract(scenario="overreach")
    risks = _risks(session)
    text = " ".join(f"{r['issue']} {r['suggestion']}" for r in risks)
    assert "可直接签署" not in text
    assert "不存在任何风险" not in text
    assert any("越界表述" in gap.detail for gap in session.gaps)


async def test_ct25_review_mark_is_forced_by_code():
    session, _ = await run_contract(scenario="contract_no_mark")
    for risk in _risks(session):
        if risk.get("suggestion"):
            assert REVIEW_MARK in risk["suggestion"], "建议改法必须带审定标注（代码强制）"
    assert any(REVIEW_MARK in gap.detail for gap in session.gaps)


def test_ct26_contract_guard_uses_contract_extra_patterns_only_on_contract_branch():
    text = "这份合同可直接签署，无需律师审阅。"
    contract = Session(branch="contract")
    contract.structured_output = {
        "conclusions": [{"text": text, "citation_source_ids": [], "citations": []}]
    }
    contract.pending_claims = []
    apply_gates(contract)
    hits = {h["text"] for h in contract.expression_hits}
    assert "可直接签署" in hits and "无需律师审阅" in hits

    research = Session(branch="research")
    research.structured_output = {
        "conclusions": [{"text": text, "citation_source_ids": [], "citations": []}]
    }
    apply_gates(research)
    assert not any(h["text"] == "可直接签署" for h in research.expression_hits)


def test_ct27_kind_and_level_labels_are_complete():
    assert RISK_KIND_LABELS == {"illegal": "违法", "commercial": "商业不利", "wording": "措辞不清"}


def test_ct28_out_of_range_level_fails_schema_validation():
    from app.schemas import risk_items_schema, validate

    schema = risk_items_schema(["illegal", "commercial", "wording"], ["high", "medium", "low"])
    payload = {
        "risks": [
            {
                "kind": "illegal",
                "level": "urgent",           # 越界
                "clause_id": "c001",
                "anchor_text": "某段原文",
                "issue": "示例",
            }
        ]
    }
    try:
        validate(schema, payload)
    except ApiError as exc:
        assert exc.code == "schema_invalid"
    else:
        raise AssertionError("越界的 level 必须被结构校验拦下")


def test_ct29_missing_anchor_fails_schema_validation():
    from app.schemas import risk_items_schema, validate

    schema = risk_items_schema(["illegal"], ["high"])
    try:
        validate(schema, {"risks": [{"kind": "illegal", "level": "high", "clause_id": "c001"}]})
    except ApiError as exc:
        assert exc.code == "schema_invalid"
    else:
        raise AssertionError("缺 anchor_text 必须被结构校验拦下")


# ==================================================================== CT5 隐私与导出


async def test_ct30_contract_text_never_lands_on_disk_or_metrics():
    marker = "张三丰与李四海房屋租赁专用条款内容需保密处理不得外泄"
    session, _ = await run_contract(
        contract_text=(
            "房屋租赁合同\n甲方（出租人）：张三丰\n乙方（承租人）：李四海\n"
            f"第一条 租赁物\n{marker}，双方均应严格履行本合同项下的全部义务。\n"
            "第二条 租金\n月租金三千元，乙方应于每月五日前支付，逾期计收违约金。\n"
        )
    )
    runs = get_config().runs_dir
    dumped = []
    for suffix in (".json", ".journal.jsonl", ".output.json", ".sources.jsonl"):
        path = runs / f"{session.run_id}{suffix}"
        if path.exists():
            dumped.append(path.read_text(encoding="utf-8"))
    assert marker not in "\n".join(dumped), "合同原文不得落盘"
    metrics = get_config().metrics_path
    if metrics.exists():
        assert marker not in metrics.read_text(encoding="utf-8")


async def test_ct31_restart_without_contract_refuses_instead_of_faking():
    session = Session(branch="contract")
    cfg = get_config()
    result = await asyncio.wait_for(
        launch(session, cfg, KnowledgeBase(cfg), {"contract": True}, workflow="legal-contract"),
        timeout=10.0,
    )
    assert result["status"] == "failed"
    assert result["error"] == "empty_material"


def test_ct32_export_blocked_before_stance_and_before_risks():
    session = Session(branch="contract")
    blockers = "；".join(session.export_blockers())
    assert "尚未确认审查立场" in blockers
    assert "尚未完成风险识别" in blockers


async def test_ct33_export_contains_required_sections():
    session, _ = await run_contract()
    result = dispatch_tool(session, __import__("app.llm", fromlist=["ToolCall"]).ToolCall(
        id="e1", name="export_docx", arguments={"filename": "合同审查报告.docx"}
    ))
    assert result.status is Status.OK, result.detail
    text = document_text(Document(str(result.meta["path"])))
    for section in ("合同审查报告", "审查立场", "风险清单", "依据缺口", "方法与局限"):
        assert section in text, f"报告缺少「{section}」"
    assert "不等于没有风险" in text
    assert "须经律师审定" in text
    assert "sk-" not in text and "/Users/" not in text


def test_ct34_contract_material_is_not_citable_as_legal_basis():
    """合同是审查对象，不是法律依据：其效力状态刻意不在 R6 白名单内。"""
    session = Session(branch="contract")
    result = _upload(session, "房屋租赁合同.txt", CONTRACT_TEXT, role=ROLE_CONTRACT)
    source = result.sources[0]
    session.verify_material(source.source_id)
    assert source.effective_status == "不适用（合同文本）"

    from app.gates.citation import CitationGate, GatePolicy
    from app.models import Citation

    gate = CitationGate(GatePolicy(allow_synthetic=True))
    passed, rule, reason = gate.check(
        Citation(source_id=source.source_id, identifier=source.identifier, quote=CONTRACT_TEXT[:30]),
        session.source_pool,
    )
    assert passed is False
    assert rule == "R6"


# ==================================================================== CT6 界面与路由


def test_ct35_branch_routing_and_bootstrap_expose_contract():
    from app.workflows import WORKFLOW_FOR_BRANCH

    assert WORKFLOW_FOR_BRANCH["contract"] == "legal-contract"
    body = client.get("/api/bootstrap").json()
    module = [m for m in body["modules"] if m["id"] == "contract"]
    assert module and module[0]["enabled"] is True


def test_ct36_ui_has_contract_entry_stance_card_and_linkage():
    html = (Path(__file__).resolve().parent.parent / "web" / "index.html").read_text(encoding="utf-8")
    assert 'data-tab="contract"' in html, "界面必须有合同审查入口"
    assert 'id="contract-panel"' in html
    assert "审查立场" in html, "必须有立场选择"
    assert 'id="contract-text"' in html and 'id="contract-risks"' in html, "必须有原文↔风险联动容器"
    assert "不等于" in html or "未提示" in html


# ==================================================================== 回归：没弄坏前两个模块


async def test_ct37_research_and_consult_are_unaffected():
    from tests.conftest import run_research

    research = await run_research(allow_synthetic=True)
    assert research.matrix, "2-4 链路必须仍然产出矩阵"
    assert research.contract == {} and research.consult == {}


def test_ct38_contract_state_is_isolated_per_session():
    first = Session(branch="contract")
    second = Session(branch="contract")
    first.contract["stance"] = "party_a"
    assert second.contract == {}


def test_ct39_normalize_risks_records_gap_for_bad_anchor():
    session = Session(branch="contract")
    session.contract["text"] = CONTRACT_TEXT
    clause_set = split_clauses(CONTRACT_TEXT)
    risks = _normalize_risks(
        session,
        clause_set,
        [
            {
                "kind": "illegal",
                "level": "high",
                "clause_id": "c002",
                "anchor_text": "完全不存在的原文片段",
                "issue": "示例",
            }
        ],
        40,
    )
    assert risks[0]["locateOk"] is False
    assert any(g.kind == "locate" for g in session.gaps)


def test_ct40_document_text_helper_is_utf8_safe():
    assert json.dumps({"k": "合同"}, ensure_ascii=False) == '{"k": "合同"}'


# ============================================ 2-6 实测修正：冲突文案不得写错


def test_ct41_export_blocker_message_matches_the_actual_conflict_side():
    """回归：本地依据库 vs 法宝的冲突不是“用户材料”问题，文案不得写成“用户材料与法宝”。"""
    from app.models import Source, Status

    session = Session(branch="contract")
    session.contract.update({"stance": "party_b", "status": "reviewed", "risks": [{"riskId": "r1"}]})
    session.source_pool["mcp_statute_9"] = Source(
        source_id="mcp_statute_9",
        kind="statute",
        identifier="示例法规第九条",
        title="示例法规",
        quote="示例原文",
        origin="mcp",
        status=Status.OK,
        corroboration="conflict",
    )
    blockers = "；".join(session.export_blockers())
    assert "本地依据库与法宝" in blockers
    assert "用户材料与法宝" not in blockers


def test_ct42_user_material_conflict_keeps_its_own_wording():
    session = Session(branch="contract")
    session.contract.update({"stance": "party_b", "status": "reviewed", "risks": [{"riskId": "r1"}]})
    from tests.test_materials import CASE_TEXT, _upload

    result = _upload(session, "case.txt", CASE_TEXT)
    source = result.sources[0]
    session.source_pool[source.source_id].corroboration = "conflict"
    blockers = "；".join(session.export_blockers())
    assert "用户材料与法宝" in blockers
    assert "以我上传的材料为准" in blockers
