"""门禁执行器：从模型产出抽取 Claim → 过引用门禁 → 过表达边界。

可讨论，不能交付：这里**只降级与记录**；真正的硬拦在 `export_docx` 前置条件里（PRD 4.1.3）。
"""

from __future__ import annotations

import re
from typing import Any

from ..models import Citation, Claim, GateReport
from ..session import Session
from .citation import CitationGate, GatePolicy
from .expression import ExpressionGuard

_CLAUSE = re.compile(r"第[一二三四五六七八九十百零\d]+条")
_CASE_NO = re.compile(r"[（(]\d{4}[）)][^\s，。；]{0,20}?第?\d+号")


def extract_claims(session: Session, text: str) -> list[Claim]:
    """优先用结构化输出；兜底用正则扫描散文里的案号/条款号（PRD 4.1.3）。"""
    structured = session.structured_output or {}
    conclusions = structured.get("conclusions")
    if isinstance(conclusions, list) and conclusions:
        claims: list[Claim] = []
        for item in conclusions:
            citations: list[Citation] = []
            for raw in item.get("citations") or []:
                citations.append(
                    Citation(
                        source_id=str(raw.get("source_id", "")),
                        identifier=str(raw.get("identifier", "")),
                        quote=str(raw.get("quote", "")),
                    )
                )
            if not citations:
                for sid in item.get("citation_source_ids") or []:
                    source = session.source_pool.get(str(sid))
                    citations.append(
                        Citation(
                            source_id=str(sid),
                            identifier=(source.identifier if source else ""),
                            quote="",  # 无逐字原文 → 门禁 R5 必拒（宁可拒绝）
                        )
                    )
            claims.append(Claim(text=str(item.get("text", "")), citations=citations))
        return claims

    # 兜底：散文里出现案号或条款号即视为待核验引用
    claims = []
    for sentence in re.split(r"[。！？\n]", text or ""):
        found = _CASE_NO.findall(sentence) + _CLAUSE.findall(sentence)
        if not found:
            continue
        citations = []
        for token in found:
            for source in session.source_pool.values():
                if token in (source.identifier or ""):
                    citations.append(
                        Citation(source_id=source.source_id, identifier=token, quote="")
                    )
        claims.append(Claim(text=sentence.strip(), citations=citations))
    return claims


def apply_gates(session: Session, applicable_at: str | None = None) -> GateReport:
    from ..config import get_config

    cfg = get_config()
    policy = GatePolicy(
        allow_synthetic=bool(cfg.get("policy.allow_synthetic", False)),
        require_effective_status=bool(cfg.get("policy.require_effective_status", True)),
        require_quote_match=bool(cfg.get("policy.require_quote_match", True)),
        require_user_material_review=bool(
            cfg.get("policy.require_user_material_review", True)
        ),
        min_quote_length=int(cfg.get("policy.min_quote_length", 8)),
    )
    gate = CitationGate(policy)
    extra_patterns = list(cfg.get("policy.forbidden_patterns", []) or [])
    if session.branch == "consult":
        # 2-5：咨询专属拒答边界。配置里写的是普通短语（如“一定能赢”），
        # 这里转义成正则并入表达边界（配置化，随时可加；写错也不会崩）。
        for phrase in cfg.get("consult.expression_extra", []) or []:
            text = str(phrase).strip()
            if text:
                extra_patterns.append(re.escape(text))
    elif session.branch == "contract":
        # 2-6：合同审查专属拒答边界（如“可直接签署”“无需律师审阅”）
        for phrase in cfg.get("contract.expression_extra", []) or []:
            text = str(phrase).strip()
            if text:
                extra_patterns.append(re.escape(text))
    guard = ExpressionGuard(extra_patterns)

    claims = session.pending_claims or extract_claims(session, "")
    excluded = session.excluded_conflict_ids()
    omitted = [claim for claim in claims if any(c.source_id in excluded for c in claim.citations)]
    active_claims = [claim for claim in claims if not any(c.source_id in excluded for c in claim.citations)]
    result = gate.screen(
        active_claims,
        session.source_pool,
        applicable_at=applicable_at or None,
        resolved_conflicts=session.resolved_conflict_ids(),
    )
    result.degraded.extend(omitted)
    _promote_verified_sources(session, result)
    # 红线 4：界面只展示通过核验的结论。把被降级的结论原文暴露出去，
    # 前端据此把它们从结论区移到「依据缺口」（否则界面无从判断哪条不许展示）。
    result.report.degraded_texts = [c.text for c in result.degraded]

    # 表达边界：扫描已通过的结论 + 综合文本
    texts = [c.text for c in result.passed] + [c.text for c in result.degraded]
    synthesis = session.synthesis or {}
    for key in ("summary", "text"):
        if isinstance(synthesis.get(key), str):
            texts.append(synthesis[key])
    hits: list[dict[str, str]] = []
    for text in texts:
        hits.extend(guard.scan(text))
    session.expression_hits = hits
    if hits:
        result.report.guard_hits = [f"{h['category']}：{h['text']}" for h in hits]
        result.report.gaps.append(ExpressionGuard.rewrite_hint(hits, session.distribution.get("denominator")))

    # 记录被引用到的来源编号（抽样残留检测用）
    cited: list[str] = []
    for claim in active_claims:
        cited.extend(c.source_id for c in claim.citations)
    session.cited_ids = sorted(set(cited))

    session.gate_report = result.report
    # 埋点：北极星指标（引用可溯源率）与证据覆盖率（PRD 1.4 / 6.2）
    try:
        from ..observability import get_observability

        get_observability().metric(
            event="gate",
            session_id=session.id,
            run_id=session.run_id,
            gate_accepted=result.report.accepted,
            gate_rejected=len(result.report.rejected),
            guard_hits=len(result.report.guard_hits),
            coverage=result.report.coverage,
            demo_mode=result.report.demo_mode,
        )
    except Exception:                       # 埋点失败不影响主流程
        pass
    for gap in result.report.gaps:
        session.add_gap("citation", gap)
    session.structured_output = session.structured_output or {}
    session.structured_output["_passed_conclusions"] = [c.text for c in result.passed]
    session.structured_output["_degraded_conclusions"] = [c.text for c in result.degraded]
    return result.report


def _promote_verified_sources(session: Session, result: Any) -> None:
    """2-3：把**通过门禁**的来源从线索区提升为可引用（本地依据库），并记录印证指标。

    只有通过引用的来源才进「依据库」；未通过（含仅摘要、冲突）留在线索区，不可引用。
    """
    try:
        from ..library import get_library
        from ..observability import get_observability

        library = get_library()
        for claim in result.passed:
            for citation in claim.citations:
                source = session.source_pool.get(citation.source_id)
                # 2-4：用户材料不入本地依据库（红线），因此也不做提升
                if source is None or source.synthetic or source.is_user_material:
                    continue
                library.promote("statute" if source.kind == "statute" else "case", source.identifier)

        counts = {"dual": 0, "single_mcp": 0, "single_local": 0, "conflict": 0, "not_applicable": 0}
        local_hits = 0
        for source in session.source_pool.values():
            counts[source.corroboration] = counts.get(source.corroboration, 0) + 1
            if source.local_hit:
                local_hits += 1
        get_observability().metric(
            event="corroboration",
            session_id=session.id,
            run_id=session.run_id,
            corr_dual=counts["dual"],
            corr_single_mcp=counts["single_mcp"],
            corr_single_local=counts["single_local"],
            corr_conflict=counts["conflict"],
            local_hits=local_hits,
            sources_total=sum(counts.values()),
        )
    except Exception:                       # 指标/沉淀失败不影响主流程
        pass


def gate_summary(session: Session) -> dict[str, Any]:
    if session.gate_report is None:
        return {"accepted": 0, "rejected": 0, "guard_hits": 0, "coverage": 0.0}
    return session.gate_report.event()
