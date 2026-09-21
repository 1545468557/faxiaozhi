import pytest
from app.session import Session
from app.models import Source, Claim, Citation, Status
from app.errors import ApiError
from app.gates.runner import apply_gates


def setup_session():
    session = Session(branch="research")
    session.matrix = [{"case_id": "case-a", "结果": "测试结果"}]
    for sid, conflict in [("bad", True), ("good", False)]:
        session.source_pool[sid] = Source(source_id=sid, kind="statute", identifier=sid,
            quote="这是用于离线核验测试的完整法规原文内容", origin="mcp", status=Status.OK,
            effective_status="现行有效", corroboration="conflict" if conflict else "dual")
    session.pending_claims = [Claim(text=sid, citations=[Citation(source_id=sid, identifier=sid,
        quote=session.source_pool[sid].quote)]) for sid in ["bad", "good"]]
    return session


def test_exclusion_removes_whole_claim_not_conflict_guard():
    s = setup_session()
    s.exclude_conflict("bad")
    apply_gates(s)
    assert s.structured_output["_passed_conclusions"] == ["good"]
    assert "bad" in s.gate_report.degraded_texts
    assert s.cited_ids == ["good"]
    assert "bad" not in s.resolved_conflict_ids()
    assert not s.source_pool["bad"].manual_override
    assert not s.unresolved_conflicts()
    assert any("已不采用" in gap.detail for gap in s.gaps)


def test_no_remaining_conclusion_blocks_export():
    s = setup_session()
    s.pending_claims = s.pending_claims[:1]
    s.exclude_conflict("bad")
    apply_gates(s)
    assert any("没有通过核验的结论" in reason for reason in s.export_blockers())


def test_matrix_dependency_cannot_be_silently_removed():
    s = setup_session()
    s.matrix = [{"依据": "bad"}]
    with pytest.raises(ApiError):
        s.exclude_conflict("bad")
    assert not s.conflict_resolutions


def test_case_exclusion_requires_research_not_statute_workaround():
    s = setup_session()
    s.source_pool["bad"].kind = "case"
    with pytest.raises(ApiError):
        s.exclude_conflict("bad")
