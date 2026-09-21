"""红线 4 的可验证性：快照必须暴露「被降级的结论」，界面才能只展示通过核验的结论。

背景（阶段 3-2 实测发现）：门禁早已算出 passed / degraded 两类结论，但快照里只暴露
计数与规则明细，前端无法判断哪条结论不许展示，只能把模型输出**全部**渲染成结论。
本文件锁住这个契约：`gate_report.degraded_texts` 必须是**被降级**那些结论的原文。
"""

from __future__ import annotations

from app.gates.runner import apply_gates
from app.models import Source, Status
from app.session import Session


def _source(source_id: str, quote: str, identifier: str) -> Source:
    return Source(
        source_id=source_id,
        kind="case",
        identifier=identifier,
        quote=quote,
        effective_status="现行有效",
        origin="mcp",
        synthetic=False,
        status=Status.OK,
    )


def _session_with(conclusions: list[dict], pool: dict[str, Source]) -> Session:
    session = Session(id="s_test", branch="research")
    session.source_pool.update(pool)
    session.structured_output = {"conclusions": conclusions}
    session.pending_claims = []
    return session


OK_QUOTE = "本院认为，出卖人交付的货物存在质量瑕疵，买受人可以主张减少价款。"
BAD_QUOTE = "本院认为，出卖人交付的货物存在质量瑕疵，买受人可以主张三倍赔偿。"  # 原文里没有这句


def test_degraded_conclusion_text_is_exposed():
    """引用逐字对不上 → 该结论被降级，且它的原文出现在 degraded_texts 里。"""
    pool = {"s1": _source("s1", OK_QUOTE, "（2023）示例民终1001号")}
    session = _session_with(
        [
            {
                "text": "买受人可主张减少价款。",
                "citation_source_ids": ["s1"],
                "citations": [
                    {"source_id": "s1", "identifier": "（2023）示例民终1001号", "quote": "买受人可以主张减少价款"}
                ],
            },
            {
                "text": "买受人可主张三倍赔偿。",
                "citation_source_ids": ["s1"],
                "citations": [
                    {"source_id": "s1", "identifier": "（2023）示例民终1001号", "quote": BAD_QUOTE}
                ],
            },
        ],
        pool,
    )
    report = apply_gates(session)

    assert report.degraded_texts == ["买受人可主张三倍赔偿。"]
    assert report.accepted >= 1


def test_passed_conclusion_never_appears_in_degraded_texts():
    """通过核验的结论不得出现在 degraded_texts（否则界面会误删正确结论）。"""
    pool = {"s1": _source("s1", OK_QUOTE, "（2023）示例民终1001号")}
    session = _session_with(
        [
            {
                "text": "买受人可主张减少价款。",
                "citation_source_ids": ["s1"],
                "citations": [
                    {"source_id": "s1", "identifier": "（2023）示例民终1001号", "quote": "买受人可以主张减少价款"}
                ],
            }
        ],
        pool,
    )
    report = apply_gates(session)

    assert report.degraded_texts == []
    assert not report.rejected


def test_conclusion_without_citation_is_degraded_and_text_exposed():
    """R0（无引用）也要能被告知给界面：结论会被降级为「依据缺口」。"""
    pool = {"s1": _source("s1", OK_QUOTE, "（2023）示例民终1001号")}
    session = _session_with(
        [{"text": "没有任何引用的结论。", "citation_source_ids": [], "citations": []}],
        pool,
    )
    report = apply_gates(session)

    assert report.degraded_texts == ["没有任何引用的结论。"]
    assert any(r.rule == "R0" for r in report.rejected)


def test_event_payload_includes_degraded_texts():
    """`gate_report.event()`（SSE 与快照都走它）必须带上该字段。"""
    pool = {"s1": _source("s1", OK_QUOTE, "（2023）示例民终1001号")}
    session = _session_with(
        [
            {
                "text": "买受人可主张三倍赔偿。",
                "citation_source_ids": ["s1"],
                "citations": [
                    {"source_id": "s1", "identifier": "（2023）示例民终1001号", "quote": BAD_QUOTE}
                ],
            }
        ],
        pool,
    )
    report = apply_gates(session)
    payload = report.event()

    assert payload["degraded_texts"] == ["买受人可主张三倍赔偿。"]
    assert payload["rejected"] == len(report.rejected)
