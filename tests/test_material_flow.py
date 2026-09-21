# ruff: noqa: E402, I001
"""说明：本文件需要在设置好环境后再导入 app 模块，故显式关闭导入位置/排序规则。

MF 组 · 材料为主链路（阶段 2-4，20 条）：纯材料出报告、检索触发规则、补充检索、
冲突与人工裁决、导出标注、红线。全部离线，不产生任何真实调用。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from docx import Document
from fastapi.testclient import TestClient

from app.config import get_config
from app.gates.runner import apply_gates, extract_claims
from app.knowledge import KnowledgeBase
from app.llm import ToolCall
from app.materials import mark_material_corroboration
from app.models import Source, Status
from app.render.docx import document_text
from app.server import app
from app.session import STORE, Session
from app.tools import dispatch_tool
from app.workflows import launch
from app.workflows.research import assemble_candidates
from tests.conftest import run_research
from tests.test_materials import CASE_TEXT, _upload

client = TestClient(app)

TOPIC = "设备质量存在瑕疵时，买受人能否主张减少价款？"
CASE_A = CASE_TEXT
CASE_B = (
    "浙江省杭州市中级人民法院\n民事判决书\n（2022）浙01民终5678号\n"
    "本院认为，买受人已经验收使用的，主张减少价款应当扣减相应的使用费用，该抗辩成立。\n"
    "裁判结果：改判部分支持上诉请求。\n"
)
QUOTE_A = "设备质量存在瑕疵的，买受人可以主张减少价款，该主张于法有据"


# ==================================================================== 工具


def _tool_events(session: Session) -> list[dict]:
    path = get_config().metrics_path
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("session_id") == session.id and str(row.get("tool", "")).startswith("search_"):
            rows.append(row)
    return rows


async def _run_with_materials(
    texts: tuple[tuple[str, str], ...],
    *,
    verify: bool = True,
    allow_synthetic: bool = False,
) -> tuple[Session, list[Source]]:
    session = Session()
    sources: list[Source] = []
    for name, text in texts:
        result = _upload(session, name, text)
        assert result.status is Status.OK, result.detail
        sources.append(result.sources[0])
    if verify:
        for source in sources:
            session.verify_material(source.source_id)
    session = await run_research(topic=TOPIC, session=session, allow_synthetic=allow_synthetic)
    return session, sources


def _mcp_case(source_id: str, identifier: str, quote: str) -> Source:
    return Source(
        source_id=source_id,
        kind="case",
        identifier=identifier,
        title=f"法宝来源：{identifier}",
        quote=quote,
        effective_status="不适用（裁判文书）",
        origin="mcp",
    )


#: 法宝侧同案号但正文改过一处的版本（制造冲突用；正文足够长，会进入双源比对）
PEER_EDITED = CASE_A.replace("可以主张减少价款", "不得主张减少价款")


def _conflict_session() -> tuple[Session, Source, Source]:
    """构造「用户材料 vs 法宝同案号但原文不一致」的会话。"""
    session = Session()
    session.topic = TOPIC
    result = _upload(session, "conflict_case.txt", CASE_A)
    material = result.sources[0]
    session.verify_material(material.source_id)
    peer = _mcp_case("mcp_case_9", material.identifier, PEER_EDITED)
    session.source_pool[peer.source_id] = peer
    mark_material_corroboration(session.source_pool)
    return session, material, peer


def _prepare_exportable(session: Session, source: Source, quote: str) -> None:
    session.sample.set_candidates([source.source_id])
    session.sample.confirm([source.source_id], [])
    session.cases = [{"case_id": source.source_id, "identifier": source.identifier, "stance": "support"}]
    dispatch_tool(session, ToolCall(id="m1", name="render_matrix", arguments={}))
    session.synthesis = {
        "conclusions": [
            {
                "text": "本次确认样本中，买受人可以主张减少价款。",
                "citation_source_ids": [source.source_id],
                "citations": [
                    {
                        "source_id": source.source_id,
                        "identifier": source.identifier,
                        "quote": quote,
                    }
                ],
            }
        ]
    }
    session.structured_output = dict(session.synthesis)
    session.pending_claims = extract_claims(session, "")


# ==================================================================== MF23-24


async def test_mf23_pure_material_run_produces_report_and_export(tmp_path):
    session, sources = await _run_with_materials((("a.txt", CASE_A), ("b.txt", CASE_B)))
    assert session.material_primary is True
    assert len(session.candidates) == 2
    assert session.matrix and len(session.matrix) == 2
    assert session.synthesis and session.synthesis.get("conclusions")
    assert session.gate_report is not None and session.gate_report.accepted >= 1
    assert session.export_ready(), session.export_blockers()

    result = dispatch_tool(
        session, ToolCall(id="e1", name="export_docx", arguments={"filename": "纯材料报告.docx"})
    )
    assert result.status is Status.OK, result.detail
    target = Path(str(result.meta["path"]))
    assert target.exists()
    text = document_text(Document(str(target)))
    assert "材料来源清单" in text
    for source in sources:
        assert source.identifier in text
    # 回归（代操作验收措辞不一致 a）：验收清单要求报告始终写明「来源构成」，
    # 纯材料路径（无任何法宝来源）以前会被跳过，导致报告上看不到这行
    assert "本次来源构成" in text, "纯材料路径也必须写出来源构成"
    assert "补充检索来源 0 条" in text
    assert "常规检索来源 0 条" in text


async def test_mf24_material_sufficient_means_zero_retrieval_calls():
    session, _sources = await _run_with_materials((("a2.txt", CASE_A), ("b2.txt", CASE_B)))
    assert _tool_events(session) == [], "材料充足时不得调用法宝检索（默认不检索）"
    assert session.supplement_rounds == 0
    assert all(s.origin == "user" for s in session.source_pool.values())
    assert any("未调用法宝检索" in item for item in session.limitations)


# ==================================================================== MF25-29 检索为辅


async def test_mf25_insufficient_materials_trigger_one_automatic_supplement():
    session, sources = await _run_with_materials(
        (("only.txt", CASE_A),), allow_synthetic=True
    )
    assert session.supplement_rounds == 1, "材料不足时应自动补一次检索"
    supplemented = [s for s in session.source_pool.values() if s.supplement]
    assert supplemented, "补充来源必须单独标记"
    assert all(not s.is_user_material for s in supplemented)
    assert any("自动补充一次检索" in item for item in session.limitations)
    # 用户材料仍在候选池最前面
    assert session.candidates[0]["source_id"] == sources[0].source_id


async def test_mf25b_insufficient_supplement_is_code_driven_not_model_driven(monkeypatch):
    """回归（2-4 代操作验收缺陷 6）：材料不足时必须由**代码**补检索。

    修复前：这一步交给模型编排，且提示词写着「材料为主、不要习惯性去检索」，
    真实模型于是根本不调工具 → 规则 2 形同虚设（桩模型下测试是绿的，真模型下不触发）。
    这里把「模型编排检索」用的提示词函数改成直接报错：只要代码还依赖模型去补检索，本用例就会失败。
    """
    import app.workflows.research as research_mod

    def _boom(*args, **kwargs):
        raise AssertionError("材料不足时不得把补检索交给模型编排（规则必须由代码强制）")

    monkeypatch.setattr(research_mod, "build_retrieval_prompt", _boom)
    session, sources = await _run_with_materials((("only.txt", CASE_A),), allow_synthetic=True)
    assert session.supplement_rounds == 1, "材料不足时仍必须自动补一次检索（不靠模型自觉）"
    assert any(s.supplement for s in session.source_pool.values())


def test_mf26_manual_supplement_adds_marked_sources():
    with client:
        created = client.post("/api/session", json={}).json()
        sid = created["session_id"]
        client.post(
            f"/api/session/{sid}/upload",
            files=[
                ("files", ("s1.txt", CASE_A.encode(), "text/plain")),
                ("files", ("s2.txt", CASE_B.encode(), "text/plain")),
            ],
        )
        STORE.get(sid).topic = TOPIC
        response = client.post(f"/api/session/{sid}/supplement")
        snapshot = client.get(f"/api/session/{sid}/state").json()
    body = response.json()
    assert response.status_code == 200
    assert body["sources_added"] >= 1
    assert body["supplement"]["rounds_used"] == 1
    supplemented = [s for s in snapshot["sources"] if s["supplement"]]
    assert supplemented, "补充来源必须带 supplement 标记"
    assert all(s["origin_text"].startswith("补充来源") for s in supplemented)


def test_mf27_supplement_rounds_are_capped():
    with client:
        created = client.post("/api/session", json={}).json()
        sid = created["session_id"]
        client.post(
            f"/api/session/{sid}/upload",
            files=[("files", ("c1.txt", CASE_A.encode(), "text/plain"))],
        )
        STORE.get(sid).topic = TOPIC
        first = client.post(f"/api/session/{sid}/supplement").json()
        second = client.post(f"/api/session/{sid}/supplement").json()
        third = client.post(f"/api/session/{sid}/supplement")
    assert first["supplement"]["rounds_used"] == 1
    assert second["supplement"]["rounds_used"] == 2
    assert third.status_code == 429
    assert third.json()["error"]["code"] == "supplement_exhausted"


def test_mf28_supplement_does_not_change_material_primacy():
    session = Session()
    for name, text in (("u1.txt", CASE_A), ("u2.txt", CASE_B)):
        result = _upload(session, name, text)
        session.verify_material(result.sources[0].source_id)
    for index in range(2):
        peer = _mcp_case(f"mcp_case_s{index}", f"（2021）示例民终{3000 + index}号", "法宝补充来源原文。")
        peer.supplement = True
        session.source_pool[peer.source_id] = peer

    candidates = assemble_candidates(session, 30)
    origins = [c["origin"] for c in candidates]
    assert origins[:2] == ["user", "user"], "用户材料必须排在补充来源之前"
    assert "user" not in origins[2:]


def test_mf29_supplement_sources_never_displace_user_candidates():
    session = Session()
    first = _upload(session, "keep.txt", CASE_A).sources[0]
    session.verify_material(first.source_id)
    before = [c["source_id"] for c in assemble_candidates(session, 30)]
    for index in range(3):
        peer = _mcp_case(f"mcp_case_d{index}", f"（2020）示例民终{4000 + index}号", "法宝原文。")
        peer.supplement = True
        session.source_pool[peer.source_id] = peer
    after = [c["source_id"] for c in assemble_candidates(session, 30)]
    assert after[0] == first.source_id
    assert after[:1] == before[:1], "补充不得改变用户材料的顺序"


# ==================================================================== MF30 回归


async def test_mf30_run_without_materials_behaves_as_before():
    session = await run_research(topic=TOPIC, allow_synthetic=True)
    assert session.material_primary is False
    assert session.material_registry.records == {}
    assert session.supplement_rounds == 0
    assert session.candidates and all(s.get("origin") != "user" for s in session.candidates)
    assert session.matrix and session.synthesis and session.synthesis.get("conclusions")
    assert session.gate_report is not None and session.gate_report.accepted >= 1


# ==================================================================== MF31-32 印证


def test_mf31_same_case_number_different_text_is_a_conflict():
    session, material, _peer = _conflict_session()
    assert material.corroboration == "conflict"
    assert material.corroboration_text.startswith("来源冲突")
    assert "用户材料" in material.corroboration_text


def test_mf31b_material_with_header_lines_is_not_a_conflict():
    """实测修正的假冲突：用户材料带「案号 + 法院 + 日期 + 说明行」是常态，不能当成冲突。

    修复前这里会被判为「来源冲突」并挡住导出（真实冒烟跑出来的真问题）。
    """
    session = Session()
    material = _upload(session, "with_header.txt", CASE_A).sources[0]
    session.verify_material(material.source_id)
    # 法宝侧只返回裁判理由段（无案号/法院/日期等外围行）
    body = "本院认为，设备质量存在瑕疵的，买受人可以主张减少价款，该主张于法有据。裁判结果：驳回上诉，维持原判。"
    session.source_pool["mcp_case_hdr"] = _mcp_case("mcp_case_hdr", material.identifier, body)
    assert mark_material_corroboration(session.source_pool) == 0
    assert material.corroboration == "dual"


def test_mf31c_interior_one_char_edit_is_still_a_conflict():
    """同一份文书的**正文中间**改字 → 仍是冲突（M3 场景不得被放宽）。"""
    session = Session()
    material = _upload(session, "edited.txt", CASE_A).sources[0]
    session.verify_material(material.source_id)
    mutated = CASE_A.replace("该主张于法有据", "该主张于法无据")
    session.source_pool["mcp_case_edit"] = _mcp_case("mcp_case_edit", material.identifier, mutated)
    assert mark_material_corroboration(session.source_pool) == 1
    assert material.corroboration == "conflict"


def test_mf31d_too_short_text_is_not_judged():
    """文本太短不做双源判定：既不冒充一致，也不贸然判冲突。"""
    session = Session()
    material = _upload(session, "tiny.txt", CASE_A).sources[0]
    material.quote = "（2023）苏01民终1234号"
    session.source_pool["mcp_case_tiny"] = _mcp_case(
        "mcp_case_tiny", material.identifier, "本院认为，完全不同的一段短文本。"
    )
    assert mark_material_corroboration(session.source_pool) == 0
    assert material.corroboration == "not_applicable"


def test_mf32_same_case_number_same_text_is_dual():
    session = Session()
    material = _upload(session, "same.txt", CASE_A).sources[0]
    session.verify_material(material.source_id)
    session.source_pool["mcp_case_1"] = _mcp_case("mcp_case_1", material.identifier, material.quote)
    mark_material_corroboration(session.source_pool)
    assert material.corroboration == "dual"
    assert "用户材料 + 法宝" in material.corroboration_text


# ==================================================================== MF33-38 冲突与裁决


def test_mf33_conflict_blocks_export_with_r7():
    session, material, _peer = _conflict_session()
    _prepare_exportable(session, material, QUOTE_A)
    report = apply_gates(session)
    rules = {r.rule for r in report.rejected}
    assert "R7" in rules, "冲突来源的引用必须被 R7 拦下"
    blockers = session.export_blockers()
    assert any("来源冲突" in item for item in blockers)
    result = dispatch_tool(session, ToolCall(id="e2", name="export_docx", arguments={}))
    assert result.status is not Status.OK
    assert result.meta.get("precondition") or result.meta.get("denied")


def test_mf34_manual_resolution_allows_export(tmp_path):
    session, material, _peer = _conflict_session()
    _prepare_exportable(session, material, QUOTE_A)
    apply_gates(session)
    assert not session.export_ready()

    session.resolve_conflict(material.source_id, "prefer_user_material")
    apply_gates(session)
    assert session.export_ready(), session.export_blockers()

    result = dispatch_tool(
        session, ToolCall(id="e3", name="export_docx", arguments={"filename": "裁决后.docx"})
    )
    assert result.status is Status.OK, result.detail
    text = document_text(Document(str(result.meta["path"])))
    assert "依据用户上传材料，未经法宝印证；冲突已由人工确认" in text
    assert "已由人工裁决采用用户材料" in text


def test_mf35_resolution_notice_is_written_into_the_document(tmp_path):
    session, material, _peer = _conflict_session()
    _prepare_exportable(session, material, QUOTE_A)
    session.resolve_conflict(material.source_id, "prefer_user_material")
    apply_gates(session)
    result = dispatch_tool(
        session, ToolCall(id="e4", name="export_docx", arguments={"filename": "标注.docx"})
    )
    text = document_text(Document(str(result.meta["path"])))
    assert "冲突已由人工确认" in text
    assert str(material.identifier) in text


def test_mf36_resolution_is_audited_without_text():
    session, material, _peer = _conflict_session()
    with client:
        created = client.post("/api/session", json={}).json()
        sid = created["session_id"]
        live = STORE.get(sid)
        live.topic = TOPIC
        result = _upload(live, "audit_case.txt", CASE_A)
        source = result.sources[0]
        live.verify_material(source.source_id)
        live.source_pool["mcp_case_9"] = _mcp_case("mcp_case_9", source.identifier, PEER_EDITED)
        mark_material_corroboration(live.source_pool)
        response = client.post(
            f"/api/session/{sid}/conflict/resolve",
            json={"source_id": source.source_id, "decision": "prefer_user_material"},
        )
    assert response.status_code == 200
    written = get_config().metrics_path.read_text(encoding="utf-8")
    events = [json.loads(line) for line in written.splitlines() if '"conflict_resolved"' in line]
    assert any(e.get("source_id") == source.source_id for e in events)
    assert "本院认为" not in written, "审计不得写入材料原文"


def test_mf37_non_conflict_source_cannot_be_resolved():
    with client:
        created = client.post("/api/session", json={}).json()
        sid = created["session_id"]
        live = STORE.get(sid)
        source = _upload(live, "plain_case.txt", CASE_A).sources[0]
        response = client.post(
            f"/api/session/{sid}/conflict/resolve",
            json={"source_id": source.source_id, "decision": "prefer_user_material"},
        )
        bad_decision = client.post(
            f"/api/session/{sid}/conflict/resolve",
            json={"source_id": source.source_id, "decision": "auto_prefer_user"},
        )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "not_conflict"
    assert bad_decision.status_code == 400


def test_mf38_resolution_never_bypasses_unverified_material():
    session, material, _peer = _conflict_session()
    material.user_verified = False                      # 撤回人工核验
    session.material_registry.by_source(material.source_id).verified = False
    _prepare_exportable(session, material, QUOTE_A)
    session.resolve_conflict(material.source_id, "prefer_user_material")
    report = apply_gates(session)
    assert "R3" in {r.rule for r in report.rejected}, "裁决只能解冲突，不能绕过核验门"
    assert not session.export_ready()


# ==================================================================== MF39-42 导出与标注


async def test_mf39_export_lists_material_sources_with_review_state(tmp_path):
    session, sources = await _run_with_materials((("m1.txt", CASE_A), ("m2.txt", CASE_B)))
    result = dispatch_tool(
        session, ToolCall(id="e5", name="export_docx", arguments={"filename": "清单.docx"})
    )
    text = document_text(Document(str(result.meta["path"])))
    assert "材料来源清单（用户上传材料）" in text
    assert "m1.txt" in text and "m2.txt" in text
    assert "已核验" in text
    assert "不对材料本身的真伪做鉴定" in text
    assert all(source.identifier in text for source in sources)


def test_mf40_supplement_sources_are_identifiable():
    session = Session()
    material = _upload(session, "prim.txt", CASE_A).sources[0]
    session.verify_material(material.source_id)
    peer = _mcp_case("mcp_case_7", "（2019）示例民终7007号", "法宝补充来源原文。")
    peer.supplement = True
    session.source_pool[peer.source_id] = peer
    rows = {row["source_id"]: row for row in assemble_candidates(session, 30)}
    assert rows[peer.source_id]["supplement"] is True
    assert rows[peer.source_id]["origin_text"] == "补充来源（法宝）"
    assert rows[material.source_id]["origin_text"] == "用户材料"


def test_mf41_matrix_has_source_column():
    session = Session()
    material = _upload(session, "mx.txt", CASE_A).sources[0]
    session.verify_material(material.source_id)
    session.sample.set_candidates([material.source_id])
    session.sample.confirm([material.source_id], [])
    session.cases = [{"case_id": material.source_id, "identifier": material.identifier, "stance": "support"}]
    dispatch_tool(session, ToolCall(id="m2", name="render_matrix", arguments={}))
    assert session.matrix[0]["来源"] == "用户材料"


async def test_mf42_export_contains_no_secrets_or_addresses():
    session, _sources = await _run_with_materials((("k1.txt", CASE_A), ("k2.txt", CASE_B)))
    result = dispatch_tool(
        session, ToolCall(id="e6", name="export_docx", arguments={"filename": "无密钥.docx"})
    )
    text = document_text(Document(str(result.meta["path"])))
    assert "sk-" not in text
    assert "127.0.0.1" not in text
    assert "api.deepseek.com" not in text
    assert str(get_config().root) not in text
    assert "data/library" not in text


# ==================================================================== MF43-44 落盘红线（实测发现缺陷后的回归）


def _all_strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _all_strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _all_strings(value)


def _material_leaks(node: Any, materials: tuple[str, ...], window: int = 40, stride: int = 5) -> list[str]:
    """找出与用户材料原文共有 ≥window 字（规范化后）的字符串。

    注意：必须对**解码后的值**做比对（journal 是 JSON 文本，`\\n` 转义会让直接文本匹配漏报——
    实测中真的漏报过，所以这里按值递归）。
    """
    from app.models import normalize_text

    norms = [normalize_text(item) for item in materials]
    leaks: list[str] = []
    for text in _all_strings(node):
        norm = normalize_text(text)
        if len(norm) < window:
            continue
        for material in norms:
            if norm in material:
                leaks.append(text[:60])
                break
            for start in range(0, max(len(norm) - window, 0) + 1, stride):
                if norm[start : start + window] in material:
                    leaks.append(text[:60])
                    break
            else:
                continue
            break
    return leaks


async def test_mf43_material_text_never_lands_in_journal_or_output():
    """实测发现的真缺陷（2-4）：提炼结果是拿材料原文算的，原文会随 journal/output 落盘。"""
    session, _sources = await _run_with_materials((("j1.txt", CASE_A), ("j2.txt", CASE_B)))
    runs = get_config().runs_dir
    journal_lines = [
        json.loads(line)
        for line in (runs / f"{session.run_id}.journal.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    output = json.loads((runs / f"{session.run_id}.output.json").read_text(encoding="utf-8"))
    assert _material_leaks(journal_lines, (CASE_A, CASE_B)) == [], "journal 里出现了用户材料原文片段"
    assert _material_leaks(output, (CASE_A, CASE_B)) == [], "output 里出现了用户材料原文片段"
    assert any(
        "redacted_material" in json.dumps(row, ensure_ascii=False) for row in journal_lines
    ), "必须留下脱敏痕迹，便于核对"


async def test_mf44_restart_with_materials_refuses_instead_of_faking():
    """材料不落盘 → 重启后无法恢复；必须明确拒绝，不得用脱敏占位冒充真实结果。"""
    session, _sources = await _run_with_materials((("r1.txt", CASE_A), ("r2.txt", CASE_B)))
    restored = Session()
    restored.run_id = session.run_id
    result = await launch(
        restored,
        get_config(),
        KnowledgeBase(get_config()),
        {"topic": TOPIC, "conditions": {}},
        run_id=session.run_id,
    )
    assert result.get("status") == "failed"
    assert result.get("error") == "material_not_recoverable"
