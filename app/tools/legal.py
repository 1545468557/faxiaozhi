"""内置工具（PRD 3.2.2 的 V1 七工具中的六个；Workflow 在编排层实现）。"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from ..config import get_config
from ..errors import ApiError
from ..gates.citation import CitationGate, GatePolicy
from ..gates.sample import build_distribution, build_matrix
from ..models import Citation, Status, ToolResult
from ..session import Session
from ..sources import build_router

SEARCH_STATUTES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "法律问题或法条关键词"},
        "applicable_at": {
            "type": "string",
            "description": "适用时点，如 2023-01-01；用于区分现行规定与历史适用",
        },
    },
    "required": ["query"],
    "additionalProperties": False,
}

SEARCH_CASES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "expression": {"type": "string", "description": "检索式（语义 + 关键词）"},
        "cause": {"type": "string", "description": "案由（会拼进检索文本，接口无该筛选字段）"},
        "court": {"type": "string", "description": "审理法院名称"},
        "level": {"type": "string", "description": "参照级别，如 指导性案例/公报案例/普通案例"},
        "region": {"type": "string", "description": "审理法院省份全称，如 江苏省"},
        "since": {"type": "string", "description": "裁判日期起，如 2023-01-01"},
        "limit": {"type": "integer", "description": "返回上限，默认 30"},
    },
    "required": ["expression"],
    "additionalProperties": False,
}

VERIFY_CITATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "source_id": {"type": "string"},
        "identifier": {"type": "string", "description": "法规名称+条款号，或案号"},
        "quote": {"type": "string", "description": "拟引用的原文片段，须逐字"},
    },
    "required": ["source_id", "identifier", "quote"],
    "additionalProperties": False,
}

RENDER_MATRIX_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "fields": {"type": "array", "items": {"type": "string"}, "description": "矩阵列，缺省用标准列集"},
    },
    "additionalProperties": False,
}

EXPORT_DOCX_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "filename": {"type": "string", "description": "输出文件名（不含路径）"},
    },
    "additionalProperties": False,
}

_UNSAFE_NAME = re.compile(r"[\\/:*?\"<>|\x00-\x1f]")


def safe_filename(name: str, fallback: str = "research_memo") -> str:
    cleaned = _UNSAFE_NAME.sub("_", (name or "").strip())
    cleaned = cleaned.replace("..", "_").replace("/", "_").replace("\\", "_")
    cleaned = cleaned.strip("._ ") or fallback
    return cleaned[:80]


def search_statutes(session: Session, **args: Any) -> ToolResult:
    """法条检索：按 config.yaml: sources.statute.providers 的顺序走来源链。

    本地依据库只做「同一检索式命中复用」；未命中必然继续走北大法宝（不做本地优先检索）。
    """
    return build_router(get_config(), "statute", session).search(**args)


def search_cases(session: Session, **args: Any) -> ToolResult:
    return build_router(get_config(), "case", session).search(**args)


def verify_citation(session: Session, **args: Any) -> ToolResult:
    cfg = get_config()
    gate = CitationGate(
        GatePolicy(
            allow_synthetic=bool(cfg.get("policy.allow_synthetic", False)),
            min_quote_length=int(cfg.get("policy.min_quote_length", 8)),
        )
    )
    citation = Citation(
        source_id=str(args.get("source_id", "")),
        identifier=str(args.get("identifier", "")),
        quote=str(args.get("quote", "")),
    )
    ok, rule, reason = gate.check(
        citation, session.source_pool, resolved_conflicts=session.resolved_conflict_ids()
    )
    if ok:
        return ToolResult(
            tool="verify_citation",
            status=Status.OK,
            detail="引用核验通过。",
            meta={"identifier": citation.identifier},
        )
    return ToolResult(
        tool="verify_citation",
        status=Status.INSUFFICIENT,
        detail=f"引用核验未通过（{rule}）：{reason}",
        error_kind="citation_gate",
        meta={"rule": rule, "identifier": citation.identifier},
    )


def render_matrix(session: Session, **args: Any) -> ToolResult:
    confirmed = set(session.sample.confirmed)
    if not session.sample.locked:
        return ToolResult(
            tool="render_matrix",
            status=Status.INSUFFICIENT,
            detail="样本尚未确认，无法生成对比矩阵。",
            error_kind="permission",
            meta={"denied": True},
        )
    fields = args.get("fields") or None
    rows = build_matrix(session.cases, confirmed, fields)
    # 2-4：矩阵增加「来源」列（用户材料 / 法宝 / 补充来源 / 本地依据库），由代码写，不由模型写
    for row in rows:
        source = session.source_pool.get(str(row.get("case_id") or ""))
        if source is None:
            row["来源"] = ""
            continue
        row["来源"] = source.origin_text + ("（来源冲突）" if source.corroboration == "conflict" else "")
    if not rows:
        return ToolResult(
            tool="render_matrix",
            status=Status.INSUFFICIENT,
            detail="没有可用的确认样本，未生成矩阵。",
        )
    confirmed_cases = [c for c in session.cases if str(c.get("case_id")) in confirmed]
    distribution = build_distribution(confirmed_cases)
    session.matrix = rows
    session.distribution = distribution
    return ToolResult(
        tool="render_matrix",
        status=Status.OK,
        detail=f"已生成 {len(rows)} 行对比矩阵（由代码计算，仅含已确认样本）。",
        meta={"matrix": rows, "distribution": distribution},
    )


def export_docx(session: Session, **args: Any) -> ToolResult:
    cfg = get_config()
    from ..render.docx import render_consult_memo, render_contract_report, render_research_memo

    consult = session.branch == "consult"
    contract = session.branch == "contract"
    filename = safe_filename(str(args.get("filename") or _default_name(session)))
    if not filename.endswith(".docx"):
        filename += ".docx"
    target: Path = cfg.exports_dir / filename
    if not target.resolve().is_relative_to(cfg.exports_dir.resolve()):
        raise ApiError("path_escape", "禁止写入导出目录之外的路径。")
    if contract:
        render_contract_report(session, target)
    elif consult:
        render_consult_memo(session, target)
    else:
        render_research_memo(session, target)
    if contract:
        detail = "合同审查报告已导出为 Word。未提示风险的条款不等于没有风险，请人工复核。"
    elif consult:
        detail = "咨询备忘已导出为 Word。仅作研究与学习参考，不构成正式法律意见，请人工复核。"
    else:
        detail = "研究报告已导出为 Word。涉及重大权益时请人工复核后再使用。"
    return ToolResult(
        tool="export_docx",
        status=Status.OK,
        detail=detail,
        meta={"path": str(target), "download_url": f"/exports/{filename}", "filename": filename},
    )


def _default_name(session: Session) -> str:
    stamp = time.strftime("%Y%m%d-%H%M")
    topic = safe_filename(session.topic[:12] or "类案研究")
    return f"法小智_{topic}_{stamp}"
