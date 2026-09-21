"""工具分派、六值状态、参数校验、文件安全。"""

from __future__ import annotations

import pytest

from app.config import get_config
from app.llm import ToolCall
from app.models import Status
from app.session import Session
from app.tools import REGISTRY, dispatch_tool, get_tool
from app.tools.document import detect_kind, parse_bytes
from app.tools.fixtures import FixtureProvider
from app.tools.legal import safe_filename


def test_registry_has_expected_tools():
    assert set(REGISTRY) == {
        "search_statutes",
        "search_cases",
        "verify_citation",
        "parse_document",
        "render_matrix",
        "export_docx",
    }


def test_unknown_tool_returns_parse_error():
    result = dispatch_tool(Session(), ToolCall("x", "nope", {}))
    assert result.status is Status.PARSE_ERROR
    assert "未知工具" in result.detail


def test_invalid_arguments_return_parse_error():
    result = dispatch_tool(Session(), ToolCall("x", "search_cases", {"bad": 1}))
    assert result.status is Status.PARSE_ERROR
    assert "参数不合法" in result.detail


def test_tool_exception_becomes_interface_error(monkeypatch):
    tool = get_tool("search_cases")

    def boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(tool, "handler", boom)
    result = dispatch_tool(Session(), ToolCall("x", "search_cases", {"expression": "q"}))
    assert result.status is Status.INTERFACE_ERROR
    assert result.error_kind == "tool"


def test_search_cases_absorbs_sources_into_pool():
    session = Session()
    result = dispatch_tool(session, ToolCall("x", "search_cases", {"expression": "质量瑕疵"}))
    assert result.sources
    assert session.source_pool


def test_fixture_statuses_are_distinct():
    cfg = get_config()
    details = {}
    for scenario in ("clean", "no_match", "interface_error", "abstract_only", "insufficient"):
        result = FixtureProvider(cfg, scenario=scenario).search_cases(expression="q")
        details[scenario] = result.detail
    assert len(set(details.values())) == len(details)


def test_interface_error_is_not_no_match():
    cfg = get_config()
    failed = FixtureProvider(cfg, scenario="interface_error").search_cases(expression="q")
    empty = FixtureProvider(cfg, scenario="no_match").search_cases(expression="q")
    assert failed.status is Status.INTERFACE_ERROR
    assert empty.status is Status.NO_MATCH
    assert "不等于" in failed.detail
    assert "不存在" not in failed.detail


def test_conditions_echo_records_applied_and_ignored():
    cfg = get_config()
    result = FixtureProvider(cfg, scenario="clean").search_cases(
        expression="q", cause="买卖合同纠纷", weird="ignored-value"
    )
    assert "cause" in result.meta["applied_conditions"]
    assert any("weird" in item for item in result.meta["ignored_conditions"])


def test_since_filter_can_flag_insufficient():
    cfg = get_config()
    result = FixtureProvider(cfg, scenario="clean").search_cases(expression="q", since="2099-01-01")
    assert result.status in (Status.INSUFFICIENT, Status.NO_MATCH)


def test_verify_citation_ok_then_fail():
    from tests.conftest import set_policy

    set_policy(allow_synthetic=True)          # 夹具数据在演示模式下才可引用（门禁 R2）
    session = Session()
    search = dispatch_tool(session, ToolCall("x", "search_cases", {"expression": "q"}))
    source = search.sources[0]
    quote = source.quote[:20]
    same = {"source_id": source.source_id, "identifier": source.identifier}
    ok = dispatch_tool(session, ToolCall("y", "verify_citation", {**same, "quote": quote}))
    assert ok.status is Status.OK
    bad = dispatch_tool(
        session,
        ToolCall("z", "verify_citation", {**same, "quote": "本院认为，该合同自始无效，被告应当返还全部款项。"}),
    )
    assert bad.status is Status.INSUFFICIENT
    assert bad.meta["rule"] == "R5"


def test_verify_citation_unknown_source_is_precondition_failure():
    result = dispatch_tool(
        Session(),
        ToolCall("y", "verify_citation", {"source_id": "nope", "identifier": "x", "quote": "一二三四五六七八"}),
    )
    assert result.status is Status.INSUFFICIENT
    assert result.meta.get("precondition") is True


def test_render_matrix_blocked_before_sample_lock():
    session = Session()
    result = dispatch_tool(session, ToolCall("m", "render_matrix", {}))
    assert result.status is Status.INSUFFICIENT
    assert "样本尚未确认" in result.detail


def test_render_matrix_after_lock_returns_rows():
    session = Session()
    session.sample.set_candidates(["a"])
    session.sample.confirm(["a"], [])
    session.cases = [{"case_id": "a", "identifier": "（2023）1号", "stance": "support"}]
    result = dispatch_tool(session, ToolCall("m", "render_matrix", {}))
    assert result.status is Status.OK
    assert len(session.matrix) == 1


def test_safe_filename_strips_dangerous_chars():
    for raw in ("../../etc/passwd", "a/b/c", "..\\..\\win.ini", "a\x00b.docx"):
        cleaned = safe_filename(raw)
        assert "/" not in cleaned and "\\" not in cleaned
        assert ".." not in cleaned
        assert "\x00" not in cleaned
    assert safe_filename("../..") == "research_memo"
    assert safe_filename("") == "research_memo"


@pytest.mark.parametrize(
    ("data", "filename", "expected"),
    [
        (b"%PDF-1.4 content", "a.pdf", "pdf"),
        (b"PK\x03\x04zipdata", "a.docx", "docx"),
        (b"\xd0\xcf\x11\xe0ole", "a.docx", None),
        (b"hello", "a.txt", "text"),
        (b"hello", "a.exe", None),
    ],
)
def test_detect_kind(data, filename, expected):
    kind, error = detect_kind(data, filename)
    assert kind == expected


def test_renamed_executable_is_rejected():
    result = parse_bytes(b"MZ\x90\x00" + b"\x00" * 100, "fake.docx")
    assert result.status is Status.PARSE_ERROR
    assert result.error_kind == "type_mismatch"


def test_oversized_file_is_rejected():
    big = b"a" * (21 * 1024 * 1024)
    result = parse_bytes(big, "big.txt")
    assert result.status is Status.PARSE_ERROR
    assert result.error_kind == "file_too_large"


def test_txt_parse_produces_user_material_source():
    text = "第一条 本合同自双方签字之日起生效。" * 5
    result = parse_bytes(text.encode("utf-8"), "contract.txt")
    assert result.status is Status.OK
    source = result.sources[0]
    assert source.is_user_material
    assert source.user_verified is False
    assert source.origin == "user"


def test_short_text_is_parse_error():
    result = parse_bytes(b"too short", "a.txt")
    assert result.status is Status.PARSE_ERROR


def test_missing_material_id_is_parse_error():
    result = dispatch_tool(Session(), ToolCall("p", "parse_document", {"filename": "a.txt"}))
    assert result.status is Status.PARSE_ERROR


def test_export_docx_writes_inside_export_dir():
    session = Session()
    session.topic = "测试议题"
    session.sample.set_candidates(["a"])
    session.sample.confirm(["a"], [])
    session.cases = [{"case_id": "a", "identifier": "（2023）1号", "stance": "support"}]
    dispatch_tool(session, ToolCall("m", "render_matrix", {}))
    from app.gates.runner import apply_gates

    session.structured_output = {"conclusions": []}
    session.pending_claims = []
    apply_gates(session)
    result = dispatch_tool(session, ToolCall("e", "export_docx", {"filename": "unit-test.docx"}))
    assert result.status is Status.OK
    from pathlib import Path

    path = Path(result.meta["path"])
    assert path.exists()
    path.unlink()


def test_document_parse_error_text_table_matches_prd():
    from app.models import STATUS_TEXT

    assert "替换格式" in STATUS_TEXT[Status.PARSE_ERROR]
    assert "不等于" in STATUS_TEXT[Status.INTERFACE_ERROR]
    assert "这不代表相关案例不存在" in STATUS_TEXT[Status.NO_MATCH]
