"""引用门禁六条规则 + 两个经典陷阱（空标识、标点差异）。"""

from __future__ import annotations

from app.gates.citation import CitationGate, GatePolicy
from app.models import Citation, Claim, Source, Status


def src(**kwargs) -> Source:
    base = dict(
        source_id="s1",
        kind="case",
        identifier="（2023）示例民终1001号",
        quote="本院认为，出卖人交付的货物存在质量瑕疵，买受人可以主张减少价款。",
        effective_status="现行有效",
        origin="mcp",
        synthetic=False,
        status=Status.OK,
    )
    base.update(kwargs)
    return Source(**base)


def gate(**policy_kwargs) -> CitationGate:
    return CitationGate(GatePolicy(**policy_kwargs))


OK_QUOTE = "出卖人交付的货物存在质量瑕疵"
OK_IDENT = "（2023）示例民终1001号"


def test_r1_abstract_only_rejected():
    pool = {"s1": src(status=Status.ABSTRACT_ONLY)}
    ok, rule, reason = gate().check(
        Citation("s1", OK_IDENT, OK_QUOTE), pool
    )
    assert not ok and rule == "R1"
    assert "摘要" in reason


def test_r1_interface_error_rejected_and_wording_not_absence():
    pool = {"s1": src(status=Status.INTERFACE_ERROR)}
    ok, rule, reason = gate().check(Citation("s1", OK_IDENT, OK_QUOTE), pool)
    assert not ok and rule == "R1"
    assert "不存在" not in reason


def test_r1_missing_source_rejected():
    ok, rule, _ = gate().check(Citation("nope", OK_IDENT, OK_QUOTE), {})
    assert not ok and rule == "R1"


def test_r1_no_match_status_rejected():
    pool = {"s1": src(status=Status.NO_MATCH)}
    ok, rule, _ = gate().check(Citation("s1", OK_IDENT, OK_QUOTE), pool)
    assert not ok and rule == "R1"


def test_r2_synthetic_blocked_by_default():
    pool = {"s1": src(synthetic=True, origin="fixture")}
    ok, rule, reason = gate().check(Citation("s1", OK_IDENT, OK_QUOTE), pool)
    assert not ok and rule == "R2"
    assert "夹具" in reason


def test_r2_synthetic_allowed_in_demo_mode():
    pool = {"s1": src(synthetic=True, origin="fixture")}
    ok, _, _ = gate(allow_synthetic=True).check(Citation("s1", OK_IDENT, OK_QUOTE), pool)
    assert ok


def test_r3_user_material_needs_manual_review():
    pool = {"s1": src(kind="user_material", origin="user", synthetic=False)}
    ok, rule, reason = gate().check(Citation("s1", OK_IDENT, OK_QUOTE), pool)
    assert not ok and rule == "R3"
    assert "人工核验" in reason
    pool["s1"].user_verified = True
    ok, _, _ = gate().check(Citation("s1", OK_IDENT, OK_QUOTE), pool)
    assert ok


def test_r3_review_can_be_disabled_by_policy():
    pool = {"s1": src(kind="user_material", origin="user")}
    ok, _, _ = gate(require_user_material_review=False).check(
        Citation("s1", OK_IDENT, OK_QUOTE), pool
    )
    assert ok


def test_r4_empty_identifier_is_a_hard_trap():
    """空标识：'' in s 恒为 True，必须显式判空（否则会放行编造引用）。"""
    pool = {"s1": src()}
    ok, rule, reason = gate().check(Citation("s1", "", OK_QUOTE), pool)
    assert not ok and rule == "R4"
    assert "空" in reason


def test_r4_identifier_mismatch():
    pool = {"s1": src()}
    ok, rule, _ = gate().check(Citation("s1", "（2029）示例民终9999号", OK_QUOTE), pool)
    assert not ok and rule == "R4"


def test_r4_identifier_punctuation_tolerance():
    pool = {"s1": src()}
    ok, _, _ = gate().check(Citation("s1", "（2023） 示例民终1001号", OK_QUOTE), pool)
    assert ok


def test_r5_rewritten_quote_rejected():
    pool = {"s1": src()}
    ok, rule, reason = gate().check(
        Citation("s1", OK_IDENT, "本院认为，出卖人交付的货物存在重大质量瑕疵，买受人有权解除合同。"),
        pool,
    )
    assert not ok and rule == "R5"
    assert "编造" in reason or "改写" in reason


def test_r5_short_quote_rejected():
    pool = {"s1": src()}
    ok, rule, _ = gate().check(Citation("s1", OK_IDENT, "质量瑕疵"), pool)
    assert not ok and rule == "R5"


def test_r5_quote_after_redaction_rejected():
    """恢复后用户材料原文已释放 → 拒绝（宁可拒绝，不放宽核验）。"""
    pool = {"s1": src(kind="user_material", origin="user", user_verified=True, quote="")}
    ok, rule, reason = gate().check(Citation("s1", OK_IDENT, OK_QUOTE), pool)
    assert not ok and rule == "R5"
    assert "释放" in reason


def test_r6_unknown_effective_status_rejected():
    pool = {"s1": src(effective_status="unknown")}
    ok, rule, _ = gate().check(Citation("s1", OK_IDENT, OK_QUOTE), pool)
    assert not ok and rule == "R6"


def test_r6_abolished_statute_rejected():
    pool = {"s1": src(kind="statute", effective_status="已废止")}
    ok, rule, _ = gate().check(Citation("s1", OK_IDENT, OK_QUOTE), pool)
    assert not ok and rule == "R6"


def test_r6_applicable_at_out_of_range():
    pool = {
        "s1": src(
            kind="statute", effective_status="现行有效",
            applicable_from="2021-01-01", applicable_to="2023-12-31",
        )
    }
    ok, rule, reason = gate().check(Citation("s1", OK_IDENT, OK_QUOTE), pool, applicable_at="2024-06-01")
    assert not ok and rule == "R6"
    assert "失效日" in reason


def test_screen_partial_rejection_keeps_passing_claims():
    pool = {"s1": src(), "s2": src(source_id="s2", identifier="（2024）示例民终3004号")}
    claims = [
        Claim("正常结论", [Citation("s1", OK_IDENT, OK_QUOTE)]),
        Claim("编造结论", [Citation("s2", "（2024）示例民终3004号", "本院认为，该合同自始无效。")]),
    ]
    result = gate().screen(claims, pool)
    assert len(result.passed) == 1
    assert len(result.degraded) == 1
    assert result.report.accepted == 1
    assert result.report.coverage == 0.5


def test_screen_degraded_when_all_citations_blocked():
    pool = {"s1": src(status=Status.INTERFACE_ERROR)}
    result = gate().screen([Claim("x", [Citation("s1", OK_IDENT, OK_QUOTE)])], pool)
    assert not result.passed
    assert result.report.gaps


def test_screen_claim_without_citation_is_rejected():
    result = gate().screen([Claim("没有引用的结论", [])], {"s1": src()})
    assert not result.passed
    assert result.report.rejected[0].rule == "R0"
