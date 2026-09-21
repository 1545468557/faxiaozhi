"""合同审查 workflow（阶段 2-6；PRD §1.2 / §5.2 / §3.2.6 / C-12 / C-14）。

产品经理 2026-09-18 确认的五项决策（台账 D13）：
① 合同复用「上传材料」通道 + 新增角色 `contract`；
② 立场确认做成**硬门**（`checkpoint: stance_confirm`，不选不得继续）；
③ 「建议条文」只给**示例表述**，每条强制标注「须经律师审定」，不生成可直接签署/提交法院的定稿；
④ 风险等级 = **模型给建议 + 代码校验取值范围**；
⑤ V1 **一次审一份**合同。

与 2-4 / 2-5 一致的原则：**规则由代码强制**。
- 条款切分与原文偏移由代码算（`app/contract.py`），模型不碰偏移；
- 依据检索由代码直调工具（不靠模型是否愿意调）；
- 锚点必须能逐字落回原文，落不回就标「定位失败」，**不给错位置**；
- 引用核验、表达边界、上限截断全部由代码判定与留痕。
"""

from __future__ import annotations

import logging
from typing import Any

from ..config import get_config
from ..contract import ClauseSet, locate_anchor, split_clauses
from ..errors import ApiError
from ..gates.runner import apply_gates, extract_claims
from ..llm import ToolCall
from ..models import Status
from ..prompts import build_contract_clauses_prompt, build_contract_risk_prompt
from ..schemas import CONTRACT_CLAUSES_SCHEMA, risk_items_schema
from ..tools import dispatch_tool
from .research import humanize_source_ids
from .runtime import STANCE_LABELS, STANCES, RunContext

LOG = logging.getLogger("faxiaozhi.contract")

#: 建议改法必须带的标注（C-14：不得让用户以为可以直接拿去用）
REVIEW_MARK = "须经律师审定"
#: 触发风险的条文字数下限（太短的锚点无法可靠高亮）
MIN_ANCHOR_CHARS = 6


async def contract_workflow(ctx: RunContext, args: dict[str, Any]) -> dict[str, Any]:
    cfg = get_config()
    session = ctx.session
    session.branch = "contract"
    state: dict[str, Any] = session.contract

    max_clauses = int(cfg.get("contract.max_clauses", 200))
    max_risks = int(cfg.get("contract.max_risks", 40))
    max_chars = int(cfg.get("contract.max_chars", 60000))
    max_source_chars = int(cfg.get("limits.max_source_chars", 6000))
    max_candidates = int(cfg.get("limits.max_candidates", 30))
    kinds = [str(x) for x in (cfg.get("contract.risk.kinds") or [])] or [
        "illegal",
        "commercial",
        "wording",
    ]
    levels = [str(x) for x in (cfg.get("contract.risk.levels") or [])] or ["high", "medium", "low"]

    # ---- C0 找到合同（必须有，且必须已核验）-------------------------------
    source = _contract_source(session)
    if source is None:
        raise ApiError(
            "empty_material",
            "请先在右侧「上传材料」上传合同（角色选「合同」）再发起审查。",
        )
    if not source.user_verified:
        raise ApiError(
            "empty_material",
            "合同尚未核验：请先点「本人已核验」，再发起审查（未核验的材料不得作为审查对象）。",
        )
    text = source.quote or ""
    if not text.strip():
        raise ApiError(
            "contract_parse_failed",
            "这份合同没有可解析的正文。请提供可复制文字的版本（docx / pdf / txt / md）；扫描件暂不支持。",
        )
    filename = source.identifier or source.title or "合同"
    state["filename"] = filename
    state["text"] = text          # 仅会话内存；界面需要它做原文↔风险联动；落盘前会被脱敏
    session.topic = f"{filename} 合同审查"

    # ---- C1 ★立场确认（人工门，代码强制，不可绕过）------------------------
    if not state.get("stance"):
        preliminary = split_clauses(text, max_clauses)
        state["parties"] = _infer_parties_safe(text)
        state["status"] = "awaiting_stance"
        ctx.log("等待确认审查立场（未确认不得继续）")
        await ctx.checkpoint(
            "stance_confirm",
            payload={
                "filename": filename,
                "parties": state["parties"],
                "clauses": len(preliminary),
                "options": [{"value": value, "label": STANCE_LABELS[value]} for value in STANCES],
                "note": "立场不同，风险结论会不同；未确认立场不得进入解析与检索。",
            },
        )
    stance = str(state.get("stance") or "")
    if stance not in STANCES:
        raise ApiError("invalid_request", "未确认审查立场，无法继续。")
    ctx.log(f"审查立场：{STANCE_LABELS[stance]}")

    # ---- C2 解析 + 原文定位（切块由代码算）--------------------------------
    ctx.phase("解析合同")
    clause_set = split_clauses(text, max_clauses)
    if not clause_set.clauses:
        raise ApiError(
            "contract_parse_failed",
            "这份合同切不出任何条款。请提供可复制文字的版本（docx / pdf / txt / md）。",
        )
    if clause_set.truncated:
        session.limitations.append(
            f"合同条款数超过上限（{max_clauses} 条），本次仅审查前 {len(clause_set)} 条，"
            "后续条款**未被审查**，请勿据此认为其余条款无风险。"
        )
    if len(text) > max_chars:
        session.limitations.append(
            f"合同正文超过 {max_chars} 字，超出部分未送入模型，"
            "**尾部条款可能未被审查**；这是能力边界，不代表没有风险。"
        )

    named = await ctx.agent(
        label="contract_clauses",
        prompt=build_contract_clauses_prompt(
            _clause_digest(clause_set), head=text[:2000]
        ),
        schema=CONTRACT_CLAUSES_SCHEMA,
        tools=[],
    )
    _apply_clause_meta(clause_set, named)
    state["parties"] = {**state.get("parties", {}), **_clean_parties(named.get("parties"))}
    state["clauses"] = clause_set.brief()

    # ---- C3 依据检索（**代码直调**，不靠模型自觉）------------------------
    ctx.phase("检索依据")
    queries = _retrieval_queries(clause_set, limit=3)
    failed = 0
    for index, query in enumerate(queries):
        result = dispatch_tool(
            session,
            ToolCall(
                id=f"call_statutes_{index}",
                name="search_statutes",
                arguments={"query": query, "applicable_at": ""},
            ),
        )
        ctx.emit("tool", result.event())
        ctx.on_tool_result("search_statutes", result)
        if result.status in (Status.INTERFACE_ERROR, Status.PARSE_ERROR):
            failed += 1
    case_query = "；".join(queries) or session.topic
    case_result = dispatch_tool(
        session,
        ToolCall(
            id="call_cases",
            name="search_cases",
            arguments={"expression": case_query, "limit": max_candidates},
        ),
    )
    ctx.emit("tool", case_result.event())
    ctx.on_tool_result("search_cases", case_result)
    if case_result.status in (Status.INTERFACE_ERROR, Status.PARSE_ERROR):
        failed += 1

    if failed == len(queries) + 1 and not _retrieved_sources(session):
        # 接口失败 ≠ 没有相关规定（C-05）
        session.limitations.append(
            "依据检索接口调用失败，本次未获得可核验依据。这不等于「没有问题规定」："
            "没有依据的原因是接口没有返回数据。可点击重试。"
        )
        raise ApiError(
            "mcp_unavailable",
            "依据检索接口调用失败，本次未获得可核验依据。这不等于「没有问题规定」。可点击重试。",
        )

    # ---- C4 风险识别与分级 ------------------------------------------------
    ctx.phase("风险识别")
    payload = await ctx.agent(
        label="contract_risk",
        prompt=build_contract_risk_prompt(session, clause_set, stance, max_source_chars),
        schema=risk_items_schema(kinds, levels),
        tools=[],
    )
    risks = _normalize_risks(session, clause_set, payload.get("risks") or [], max_risks)

    # ---- C5 引用核验 + 表达边界 ------------------------------------------
    ctx.phase("核验")
    report = _screen(session, risks)
    ctx.emit("gate", report.event())

    if report.guard_hits:
        ctx.log("命中表达边界，正在自动改写一次后重新核验")
        session.add_gap(
            "expression", "首次输出含越界表述，已自动改写一次：" + "；".join(report.guard_hits[:3])
        )
        try:
            retry = await ctx.agent(
                label="contract_risk_rewrite",
                prompt=build_contract_risk_prompt(session, clause_set, stance, max_source_chars)
                + "\n\n【必须改写的越界表述】\n"
                + "\n".join(f"- 「{h}」" for h in report.guard_hits[:5])
                + "\n请删除这些表述，改为客观、不承诺结果的口径（不得出现「可直接签署」「可直接提交法院」"
                "「无需律师审阅」「不存在任何风险」等表述），其余内容与依据保持不变。",
                schema=risk_items_schema(kinds, levels),
                tools=[],
            )
        except ApiError as exc:
            session.add_gap("schema", f"改写输出不合规（{exc.message}），保留首次输出并阻止导出。")
        else:
            risks = _normalize_risks(session, clause_set, retry.get("risks") or [], max_risks)
            report = _screen(session, risks)
            ctx.emit("gate", report.event())

    unverified = [r for r in risks if r.get("basisStatus") == "rejected"]
    if unverified:
        session.limitations.append(
            f"有 {len(unverified)} 条风险的法律依据未通过核验，已降级为「提示性风险」，不作为有依据的风险输出。"
        )
    if report.guard_hits:
        session.limitations.append("改写后仍存在越界表述，已阻止导出。")

    state["risks"] = risks
    state["status"] = "reviewed" if risks else "insufficient"
    state["counts"] = _counts(risks, kinds, levels)
    return _finalize(ctx, clauses_count=len(clause_set), risks=risks)


# ------------------------------------------------------------------ 内部


def _retrieved_sources(session: Any) -> list[Any]:
    """检索得到的来源（不含用户上传的材料）——判断“接口到底有没有拿到东西”。"""
    return [s for s in session.source_pool.values() if not s.is_user_material]


def _contract_source(session: Any) -> Any:
    for source in session.source_pool.values():
        if source.is_user_material and source.kind == "contract" and not source.superseded:
            return source
    return None


def _infer_parties_safe(text: str) -> dict[str, str]:
    try:
        from ..contract import infer_parties

        return infer_parties(text)
    except Exception:  # 推断只是默认值，失败不得影响主流程
        return {}


def _clean_parties(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key in ("party_a", "party_b"):
        value = str(raw.get(key) or "").strip()
        if value:
            out[key] = value
    return out


def _clause_digest(clause_set: ClauseSet, per_clause_limit: int = 500) -> str:
    from ..contract import clause_index_for_model

    return clause_index_for_model(clause_set, per_clause_limit=per_clause_limit)


def _apply_clause_meta(clause_set: ClauseSet, named: dict[str, Any]) -> None:
    """只接受**已存在**的 clause_id：模型不得新增条款（切块以代码为准）。"""
    mapping: dict[str, dict[str, Any]] = {}
    for item in named.get("clauses") or []:
        if isinstance(item, dict):
            mapping[str(item.get("clause_id") or "")] = item
    for clause in clause_set.clauses:
        meta = mapping.get(clause.clause_id)
        if not meta:
            continue
        heading = str(meta.get("heading") or "").strip()
        summary = str(meta.get("summary") or "").strip()
        if heading:
            clause.heading = heading
        if summary:
            clause.summary = summary


def _retrieval_queries(clause_set: ClauseSet, limit: int = 3) -> list[str]:
    """用条款标题拼检索式（**代码生成，不问模型**）。"""
    out: list[str] = []
    for clause in clause_set.clauses:
        heading = (clause.heading or "").strip()
        if not heading or heading in {"合同首部", "（无编号段落）"}:
            continue
        if heading in out:
            continue
        out.append(heading)
        if len(out) >= limit:
            break
    if not out:
        out = ["合同 权利义务 违约责任"]
    return out


def _normalize_risks(
    session: Any,
    clause_set: ClauseSet,
    raw_risks: list[Any],
    max_risks: int,
) -> list[dict[str, Any]]:
    """把模型给的风险条目**逐条落到原文上**，并做代码侧校验。"""
    risks: list[dict[str, Any]] = []
    dropped_locate = 0
    missing_mark = 0
    for raw in raw_risks:
        if not isinstance(raw, dict):
            continue
        if len(risks) >= max_risks:
            session.limitations.append(
                f"风险条目数超过上限（{max_risks} 条），超出部分未列出——这是上限，不是「没有更多风险」。"
            )
            break
        risk_id = f"r{len(risks) + 1:03d}"
        clause_id = str(raw.get("clause_id") or "")
        clause = clause_set.get(clause_id)
        anchor = str(raw.get("anchor_text") or "").strip()
        full_text = str(session.contract.get("text") or "")
        located = locate_anchor(full_text, clause, anchor) if clause else None
        if clause is None or located is None or len(anchor) < MIN_ANCHOR_CHARS:
            # 定位失败：保留条目（别丢风险），但不给错位置、不参与原文联动
            dropped_locate += 1
            start = end = -1
            locate_ok = False
        else:
            start, end = located
            locate_ok = True
        suggestion = str(raw.get("suggestion") or "").strip()
        if suggestion and REVIEW_MARK not in suggestion:
            # C-14：代码强制标注，不靠模型自觉
            suggestion = f"{suggestion}（{REVIEW_MARK}）"
            missing_mark += 1
        issue = humanize_source_ids(session, str(raw.get("issue") or ""))
        suggestion = humanize_source_ids(session, suggestion)
        risks.append(
            {
                "riskId": risk_id,
                "kind": str(raw.get("kind") or ""),
                "level": str(raw.get("level") or ""),
                "clauseId": clause_id,
                "clauseHeading": (clause.heading if clause else ""),
                "anchorText": anchor,
                "start": start,
                "end": end,
                "locateOk": locate_ok,
                "issue": issue,
                "basis": _clean_basis(raw.get("basis")),
                "basisStatus": "no_basis" if not _clean_basis(raw.get("basis")) else "pending",
                "suggestion": suggestion,
                "confidence": str(raw.get("confidence") or ""),
            }
        )
    if dropped_locate:
        session.add_gap(
            "locate",
            f"有 {dropped_locate} 条风险无法在合同原文中定位（锚点对不上），已标记「定位失败」，"
            "不参与原文联动；请人工核对。",
        )
    if missing_mark:
        session.add_gap(
            "expression",
            f"有 {missing_mark} 条建议改法未带「{REVIEW_MARK}」标注，已由代码补齐。",
        )
    return risks


def _clean_basis(raw: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        source_id = str(item.get("source_id") or "")
        quote = str(item.get("quote") or "")
        if not source_id or not quote:
            continue
        out.append(
            {
                "source_id": source_id,
                "identifier": str(item.get("identifier") or ""),
                "quote": quote,
            }
        )
    return out


def _screen(session: Any, risks: list[dict[str, Any]]) -> Any:
    """把风险条目交给三条门禁。

    每条风险的「文本」拼成 `riskId｜issue｜suggestion`：
    - 引用核验只针对有依据的条目；
    - 表达边界能扫到 issue 与 suggestion（建议改法最容易越界）；
    - 门禁返回的 `_passed_conclusions` 里带着 riskId 前缀，据此回写 verified。
    """
    claims = []
    for risk in risks:
        if not risk.get("basis"):
            continue
        claims.append(
            {
                "text": f"{risk['riskId']}｜{risk['issue']}｜{risk['suggestion']}",
                "citation_source_ids": [c["source_id"] for c in risk["basis"]],
                "citations": risk["basis"],
            }
        )
    session.structured_output = {"conclusions": claims}
    session.pending_claims = extract_claims(session, "")
    report = apply_gates(session)

    passed = {
        str(text).split("｜", 1)[0]
        for text in (session.structured_output or {}).get("_passed_conclusions") or []
    }
    for risk in risks:
        if not risk.get("basis"):
            risk["basisStatus"] = "no_basis"
            continue
        risk["basisStatus"] = "verified" if risk["riskId"] in passed else "rejected"
    return report


def _counts(risks: list[dict[str, Any]], kinds: list[str], levels: list[str]) -> dict[str, Any]:
    by_kind = {kind: 0 for kind in kinds}
    by_level = {level: 0 for level in levels}
    for risk in risks:
        if risk.get("kind") in by_kind:
            by_kind[risk["kind"]] += 1
        if risk.get("level") in by_level:
            by_level[risk["level"]] += 1
    return {
        "total": len(risks),
        "by_kind": by_kind,
        "by_level": by_level,
        "verified": sum(1 for r in risks if r.get("basisStatus") == "verified"),
        "no_basis": sum(1 for r in risks if r.get("basisStatus") == "no_basis"),
        "locate_failed": sum(1 for r in risks if not r.get("locateOk")),
    }


def _finalize(ctx: RunContext, clauses_count: int, risks: list[dict[str, Any]]) -> dict[str, Any]:
    from ..gates.runner import gate_summary

    session = ctx.session
    ctx.phase("成稿")
    ctx.snapshot_sources()
    summary = gate_summary(session)
    state = session.contract
    # 界面只展示通过 / 未通过核验两类；两类都给用户看，但标注不同
    output = {
        "run_id": ctx.run_id,
        "status": state.get("status"),
        "branch": "contract",
        "contract": {
            "filename": state.get("filename"),
            "stance": state.get("stance"),
            "stanceLabel": STANCE_LABELS.get(str(state.get("stance")), ""),
            "parties": state.get("parties") or {},
            "clauses": state.get("clauses") or [],
            "clauses_count": clauses_count,
            "risks": risks,
            "counts": state.get("counts") or {},
        },
        "limitations": session.limitations,
        "gate": summary,
        "gaps": [g.__dict__ for g in session.gaps],
        "usage": ctx.usage,
        "first_response_ms": ctx.first_response_ms,
        "steps": ctx.steps,
    }
    ctx.emit(
        "done",
        {
            "run_id": ctx.run_id,
            "status": "content_ready" if risks else "evidence_collected",
            "summary": {
                "clauses": clauses_count,
                "risks": len(risks),
                "risks_verified": (state.get("counts") or {}).get("verified", 0),
                "risks_no_basis": (state.get("counts") or {}).get("no_basis", 0),
                "citations_accepted": summary.get("accepted", 0),
                "citations_rejected": summary.get("rejected", 0),
            },
        },
    )
    return output


def humanize_risk(session: Any, text: str) -> str:
    """把风险文本里的内部来源编号换成可读案号/法规名。"""
    return humanize_source_ids(session, text)


WORKFLOWS: dict[str, Any] = {"legal-contract": contract_workflow}
