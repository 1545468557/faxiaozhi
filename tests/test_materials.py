# ruff: noqa: E402, I001
"""说明：本文件需要在设置好环境后再导入 app 模块，故显式关闭导入位置/排序规则。

MT 组 · 材料本身（阶段 2-4，22 条）：角色 / 标识 / 版本 / 核验 / 批量 / 红线 / 定位 / 释放。
全部离线，不产生任何真实调用。
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.config import get_config
from app.errors import ApiError
from app.gates.citation import CitationGate, GatePolicy
from app.knowledge import KnowledgeBase
from app.llm import ToolCall, build_provider
from app.materials import ROLE_CASE, ROLE_STATUTE, detect_role, extract_identifier, locate
from app.models import Citation, Source, Status
from app.observability import get_observability
from app.server import app
from app.session import STORE, Session
from app.tools import dispatch_tool
from app.tools.document import parse_bytes
from app.workflows.runtime import RunContext

client = TestClient(app)

#: 案例材料（含案号；首句 ≥10 字，便于离线模型逐字引用）
CASE_TEXT = (
    "江苏省南京市中级人民法院\n民事判决书\n（2023）苏01民终1234号\n"
    "本院认为，设备质量存在瑕疵的，买受人可以主张减少价款，该主张于法有据。\n"
    "裁判结果：驳回上诉，维持原判。\n"
)
#: 法条材料（法规名 + 条号；无案号）
STATUTE_TEXT = (
    "《中华人民共和国民法典》第五百七十七条 当事人一方不履行合同义务或者履行合同义务不符合约定的，"
    "应当承担继续履行、采取补救措施或者赔偿损失等违约责任。\n"
)
#: 既无案号也无条款的普通文本（标识只能退回文件名）
PLAIN_TEXT = (
    "本文整理的是一起设备采购纠纷的要点摘录，涉及交付验收与价款调整两部分内容，"
    "供课程讨论使用，不构成任何法律意见。\n"
    "第一部分说明验收标准，第二部分说明价款调整的常见做法。\n"
)
#: 用户材料里的敏感正文片段（任何地方都不许出现）
SECRET = "甲方某某科技有限公司与乙方某某贸易有限公司设备采购专用条款内容"

FAKE_BINARY = b"MZ\x90\x00" + b"\x00" * 200


def _upload(session: Session, name: str, text: str, role: str | None = None):
    material_id = "m" + name.replace(".", "")[:8]
    session.materials[material_id] = (text.encode("utf-8"), name)
    args: dict[str, object] = {"filename": name, "material_id": material_id}
    if role:
        args["role"] = role
    result = dispatch_tool(
        session, ToolCall(id=f"p_{material_id}", name="parse_document", arguments=args)
    )
    return result


def _source_of(session: Session, name: str) -> Source:
    for source in session.source_pool.values():
        if name in (source.title or ""):
            return source
    raise AssertionError(f"未找到来源：{name}")


# ==================================================================== MT1 角色


def test_mt01_case_role_is_default():
    result = parse_bytes(CASE_TEXT.encode("utf-8"), "case1.txt")
    source = result.sources[0]
    assert source.kind == ROLE_CASE
    assert source.origin == "user"
    assert source.effective_status == "不适用（裁判文书）"
    assert result.meta["role_source"] == "detected"        # 有案号 → 正文识别
    assert source.is_user_material and source.user_verified is False


def test_mt02_statute_material_is_detected_and_not_citable_before_review():
    result = parse_bytes(STATUTE_TEXT.encode("utf-8"), "statute1.txt")
    source = result.sources[0]
    assert source.kind == ROLE_STATUTE
    assert source.effective_status == "unknown"            # 未核验 → 只能作线索
    assert result.meta["role_source"] == "detected"
    ok, rule, _ = CitationGate(GatePolicy()).check(
        Citation(source_id=source.source_id, identifier=source.identifier, quote="当事人一方不履行合同义务"),
        {source.source_id: source},
    )
    assert ok is False, "未核验的法条材料不得引用"
    assert rule in {"R3", "R6"}   # R3（未核验）先拦；核验后仍由 R6（效力 unknown）拦（见 MT12）


def test_mt03_manual_role_overrides_detection():
    as_statute = parse_bytes(CASE_TEXT.encode("utf-8"), "case2.txt", requested_role=ROLE_STATUTE)
    assert as_statute.sources[0].kind == ROLE_STATUTE
    assert as_statute.meta["role_source"] == "manual"
    as_case = parse_bytes(STATUTE_TEXT.encode("utf-8"), "statute2.txt", requested_role=ROLE_CASE)
    assert as_case.sources[0].kind == ROLE_CASE
    assert as_case.meta["role_source"] == "manual"
    assert as_case.sources[0].effective_status == "不适用（裁判文书）"


# ==================================================================== MT4-6 标识


def test_mt04_case_number_is_taken_from_body():
    identifier, source, missing = extract_identifier(CASE_TEXT, "随便的名字.txt", ROLE_CASE)
    assert identifier == "（2023）苏01民终1234号"
    assert source == "text" and missing is False


def test_mt05_law_name_and_article_are_taken_from_body():
    identifier, source, missing = extract_identifier(STATUTE_TEXT, "x.txt", ROLE_STATUTE)
    assert identifier == "《中华人民共和国民法典》第五百七十七条"
    assert source == "text" and missing is False


def test_mt06_unrecognised_identifier_falls_back_to_filename_and_is_flagged():
    result = parse_bytes(PLAIN_TEXT.encode("utf-8"), "案例摘录-张三诉李四.txt")
    assert result.meta["identifier_source"] == "filename"
    assert result.meta["identifier_missing"] is True
    assert result.sources[0].identifier == "案例摘录-张三诉李四.txt"
    # 必须在文案里如实标注，不许假装规范
    assert "未识别到案号" in result.detail


# ==================================================================== MT7-8 批量


def test_mt07_batch_upload_partial_failure_does_not_block_others():
    with client:
        created = client.post("/api/session", json={}).json()
        sid = created["session_id"]
        response = client.post(
            f"/api/session/{sid}/upload",
            files=[
                ("files", ("good1.txt", CASE_TEXT.encode("utf-8"), "text/plain")),
                ("files", ("fake.exe", FAKE_BINARY, "application/octet-stream")),
                ("files", ("good2.txt", (CASE_TEXT.replace("1234", "5678")).encode(), "text/plain")),
            ],
        )
        body = response.json()
        state = client.get(f"/api/session/{sid}/state").json()
    assert response.status_code == 200
    assert len(body["materials"]) == 2
    assert len(body["rejected"]) == 1
    assert body["rejected"][0]["filename"] == "fake.exe"
    assert body["rejected"][0]["error"] == "type_mismatch"
    assert len(state["materials"]) == 2
    assert all(m["role"] == "case" for m in state["materials"])


def test_mt08_batch_over_limit_is_refused_clearly():
    from app.config import get_config as _cfg

    cfg = _cfg()
    cfg.raw.setdefault("materials", {})["max_files_per_upload"] = 2
    try:
        with client:
            created = client.post("/api/session", json={}).json()
            sid = created["session_id"]
            response = client.post(
                f"/api/session/{sid}/upload",
                files=[
                    ("files", ("a.txt", CASE_TEXT.encode(), "text/plain")),
                    ("files", ("b.txt", CASE_TEXT.encode(), "text/plain")),
                    ("files", ("c.txt", CASE_TEXT.encode(), "text/plain")),
                ],
            )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "too_many_files"
    finally:
        cfg.raw["materials"]["max_files_per_upload"] = 20


# ==================================================================== MT9-12 核验与红线


def _gate() -> CitationGate:
    cfg = get_config()
    return CitationGate(
        GatePolicy(
            allow_synthetic=False,
            require_user_material_review=bool(cfg.get("policy.require_user_material_review", True)),
            min_quote_length=int(cfg.get("policy.min_quote_length", 8)),
        )
    )


def test_mt09_unverified_material_cannot_be_cited():
    session = Session()
    result = _upload(session, "case_a.txt", CASE_TEXT)
    source = result.sources[0]
    ok, rule, _ = _gate().check(
        Citation(
            source_id=source.source_id,
            identifier=source.identifier,
            quote="设备质量存在瑕疵的，买受人可以主张减少价款",
        ),
        session.source_pool,
    )
    assert ok is False and rule == "R3"


def test_mt10_verified_material_can_be_cited():
    session = Session()
    result = _upload(session, "case_b.txt", CASE_TEXT)
    source = result.sources[0]
    session.verify_material(source.source_id)
    ok, rule, reason = _gate().check(
        Citation(
            source_id=source.source_id,
            identifier=source.identifier,
            quote="设备质量存在瑕疵的，买受人可以主张减少价款",
        ),
        session.source_pool,
    )
    assert ok is True, (rule, reason)


def test_mt11_rewritten_quote_is_refused_by_r5():
    session = Session()
    result = _upload(session, "case_c.txt", CASE_TEXT)
    source = result.sources[0]
    session.verify_material(source.source_id)
    ok, rule, _ = _gate().check(
        Citation(
            source_id=source.source_id,
            identifier=source.identifier,
            quote="设备质量存在瑕疵的，买受人可以主张三倍赔偿",       # 改写/编造
        ),
        session.source_pool,
    )
    assert ok is False and rule == "R5"


def test_mt12_statute_material_stays_clue_even_after_review():
    session = Session()
    result = _upload(session, "statute_c.txt", STATUTE_TEXT)
    source = result.sources[0]
    session.verify_material(source.source_id)              # 人工核验也只解 R3，不解 R6
    ok, rule, _ = _gate().check(
        Citation(
            source_id=source.source_id,
            identifier=source.identifier,
            quote="当事人一方不履行合同义务或者履行合同义务不符合约定的",
        ),
        session.source_pool,
    )
    assert ok is False and rule == "R6"


# ==================================================================== MT13-15 版本


def test_mt13_same_identifier_keeps_latest_only():
    session = Session()
    first = _upload(session, "v1.txt", CASE_TEXT)
    old_source = first.sources[0]
    second = _upload(session, "v2.txt", CASE_TEXT.replace("驳回上诉，维持原判", "改判支持诉请"))
    new_source = second.sources[0]

    record = session.material_registry.by_source(new_source.source_id)
    assert record.version == 2
    assert old_source.source_id not in session.source_pool, "旧版必须退出依据池"
    assert session.material_registry.by_source(old_source.source_id).superseded is True
    assert second.meta["superseded"], "必须返回被取代的旧版本，供界面提示"


def test_mt14_superseded_version_never_enters_candidates_or_matrix():
    from app.workflows.research import assemble_candidates

    session = Session()
    first = _upload(session, "w1.txt", CASE_TEXT)
    old_id = first.sources[0].source_id
    _upload(session, "w2.txt", CASE_TEXT.replace("驳回上诉，维持原判", "改判支持诉请"))

    candidates = assemble_candidates(session, 30)
    assert candidates, "新版本应当成为候选"
    assert old_id not in {c["source_id"] for c in candidates}
    assert all(c["source_id"] != old_id for c in candidates)


def test_mt15_version_notice_is_visible_in_snapshot_and_detail():
    session = Session()
    _upload(session, "n1.txt", CASE_TEXT)
    second = _upload(session, "n2.txt", CASE_TEXT.replace("驳回上诉，维持原判", "改判支持诉请"))
    assert "本次采用最新上传" in second.detail
    snapshot = session.material_snapshot()
    assert len(snapshot) == 2
    flags = {row["filename"]: row["superseded"] for row in snapshot}
    assert flags["n1.txt"] is True and flags["n2.txt"] is False
    assert any("已被新版取代" in row["version_label"] for row in snapshot)


# ==================================================================== MT16-19 红线


def test_mt16_material_text_never_enters_messages():
    session = Session()
    _upload(session, "secret.txt", SECRET + "。" + CASE_TEXT)
    blob = json.dumps(session.messages, ensure_ascii=False)
    assert SECRET not in blob, "材料原文绝不允许进入会话消息记录"
    assert all(entry.get("kind") == "material_index" for entry in session.messages)
    assert "locator_index" in session.messages[0]


def test_mt17_material_text_never_lands_on_disk():
    session = Session()
    _upload(session, "secret2.txt", SECRET + "。" + CASE_TEXT)
    ctx = RunContext(
        session=session,
        cfg=get_config(),
        provider=build_provider(get_config(), "clean"),
        kb=KnowledgeBase(get_config()),
        run_id="mt17-material",
    )
    ctx.snapshot_sources()
    on_disk = ctx.sources_path.read_text(encoding="utf-8")
    assert SECRET not in on_disk
    assert "redacted" in on_disk, "用户材料只落指纹与长度"
    journal = ctx.journal_path.read_text(encoding="utf-8") if ctx.journal_path.exists() else ""
    assert SECRET not in journal


def test_mt18_material_never_enters_local_library():
    from app.library import get_library

    session = Session()
    _upload(session, "secret3.txt", SECRET + "。" + CASE_TEXT)
    session.verify_material(_source_of(session, "secret3.txt").source_id)
    library = get_library()
    assert library.stats()["entries"] == 0
    assert library.all_entries() == []
    entries = library.topic_fallback(session.topic) if session.topic else []
    assert entries == []


def test_mt19_metrics_and_trajectory_never_contain_material_text():
    session = Session()
    result = _upload(session, "secret4.txt", SECRET + "。" + CASE_TEXT)
    obs = get_observability()
    obs.register_material(result.sources[0].text_fingerprint)
    obs.metric(event="material_uploaded", session_id=session.id, chars=123)
    obs.trajectory(session_id=session.id, tool="parse_document", ok=True)
    obs.audit(event="sample_change", session_id=session.id, reason="测试")
    written = get_config().metrics_path.read_text(encoding="utf-8")
    assert SECRET not in written
    assert SECRET[:20] not in written


# ==================================================================== MT20-22 定位 / 边界 / 释放


def test_mt20_locator_index_points_at_identifier_without_quoting_text():
    session = Session()
    result = _upload(session, "locate.txt", CASE_TEXT)
    record = session.material_registry.by_source(result.sources[0].source_id)
    loc = record.locator_index
    assert loc["identifier_line"] >= 1
    assert loc["lines"] >= 3
    assert loc["chars"] == record.chars
    assert "行" in loc["anchor"]
    summary = session.messages[-1]["summary"]
    assert "行" in summary and "locate.txt" in summary
    assert "本院认为" not in summary, "定位摘要不得包含原文"


def test_mt21_empty_and_overlong_material():
    empty = parse_bytes(b"too short", "empty.txt")
    assert empty.status is Status.PARSE_ERROR
    cfg = get_config()
    old = cfg.get("materials.max_material_chars", 200000)
    cfg.raw.setdefault("materials", {})["max_material_chars"] = 300
    try:
        long_text = (CASE_TEXT + "补充说明。" * 200).encode("utf-8")
        result = parse_bytes(long_text, "long.txt")
        assert result.status is Status.OK
        assert result.meta["truncated"] is True
        assert result.meta["chars"] == 300
        assert "截断" in result.detail
    finally:
        cfg.raw["materials"]["max_material_chars"] = old


def test_mt22_closing_session_releases_materials():
    session = STORE.create()
    sid = session.id
    _upload(session, "release.txt", SECRET + "。" + CASE_TEXT)
    assert session.materials and session.material_registry.records
    STORE.close(sid)
    assert session.materials == {}
    assert session.material_registry.records == {}
    try:
        STORE.get(sid)
    except ApiError as exc:
        assert exc.code == "session_not_found"
    else:                                                  # pragma: no cover
        raise AssertionError("会话关闭后不得再取到材料")


# ==================================================================== 角色判定细节


def test_detect_role_and_locate_are_pure_functions():
    assert detect_role(CASE_TEXT, "a.txt")[0] == ROLE_CASE
    assert detect_role(STATUTE_TEXT, "b.txt")[0] == ROLE_STATUTE
    assert detect_role(PLAIN_TEXT, "c.txt")[0] == ROLE_CASE          # 默认案例
    locator = locate(CASE_TEXT, "（2023）苏01民终1234号")
    assert locator["identifier_line"] == 3
