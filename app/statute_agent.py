"""法律检索 Agent：模型只规划检索，条文与核验结果只来自工具。

本入口不保存用户问题或模型输出到 journal。法规库负责实际检索；本地结果
不足时补一次北大法宝检索。模型未配置时明确降级为普通关键词检索。
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
from datetime import date
from typing import Any

from .config import get_config
from .errors import ApiError
from .gates.citation import CitationGate, GatePolicy
from .llm import ToolCall, build_provider
from .loop import agent_loop
from .models import Citation, Status
from .session import Session
from .statutes import search
from .tools import dispatch_tool


PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["issue", "queries", "need_clarification", "clarifying_question", "assumptions", "applicable_at"],
    "properties": {
        "issue": {"type": "string"},
        "queries": {"type": "array", "items": {"type": "string"}},
        "need_clarification": {"type": "boolean"},
        "clarifying_question": {"type": "string"},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "applicable_at": {"type": "string"},
    },
    "additionalProperties": False,
}


class _PlanningContext:
    """复用共用 Agent Loop 的结构校验和步数限制，不持久化案情。"""

    def __init__(self, cfg: Any) -> None:
        self.cfg = cfg
        self.session = Session(branch="statute")
        self.provider = build_provider(cfg)
        self.phase_name = "规划法规检索"
        self.steps = 0

    def system_prompt(self) -> str:
        return (
            "你是法律法规检索规划助手。只理解问题并制定检索词，不提供法律结论，"
            "不得编造法规、条文或引用。信息不足但仍可检索时，先检索并列出假设；"
            "只有缺少关键事实会改变检索方向时才追问，最多一个问题。"
        )

    def count_step(self) -> None:
        self.steps += 1
        if self.steps > min(4, int(self.cfg.get("limits.max_agent_steps", 8))):
            raise ApiError("workflow_step_limit")

    def record_usage(self, _usage: dict[str, int]) -> None:
        pass


def _clean_query(value: str) -> str:
    text = re.sub(r"[^\w\u3400-\u9fff\s]", " ", value or "", flags=re.UNICODE)
    return " ".join(text.split())[:60]


def _queries(plan: dict[str, Any], question: str) -> list[str]:
    out: list[str] = []
    for raw in (plan.get("queries") or []):
        query = _clean_query(str(raw))
        if len(query) >= 2 and query not in out:
            out.append(query)
        if len(out) == 3:
            break
    return out or [_clean_query(question)]


def _applicable_at(plan: dict[str, Any], user_text: str) -> str:
    raw = str(plan.get("applicable_at") or "").strip()
    # 不接受模型凭空推断的日期；只允许用户明确写出的 YYYY-MM-DD。
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw) or raw not in user_text:
        return ""
    try:
        date.fromisoformat(raw)
    except ValueError:
        return ""
    return raw


def _local_hit(hit: dict[str, Any], query: str, applicable_at: str) -> dict[str, Any]:
    status_ok = hit.get("status_code") == "valid"
    effective = str(hit.get("effective_date") or "")
    date_ok = not applicable_at or not effective or effective <= applicable_at
    # 本地库只标记当前状态，无法证明某个历史时点的条文版本；有指定日期时不放行引用。
    verified = bool(status_ok and date_ok and not applicable_at and hit.get("text") and hit.get("no"))
    if applicable_at:
        reason = "指定时点需要核对当时有效的法规版本，本地库不能单独完成历史版本核验"
    elif verified:
        reason = "本地库条文与效力字段检查通过；具体适用及官方原文仍需核对"
    else:
        reason = "该条文的效力状态尚未通过检查，请人工核对"
    return {
        **hit,
        "source": "local",
        "matched_query": query,
        "citation_checked": verified,
        "check_note": reason,
        "uri": "",
    }


def _mcp_hits(question: str, applicable_at: str, limit: int, include_invalid: bool) -> tuple[list[dict[str, Any]], str]:
    session = Session(branch="statute")
    result = dispatch_tool(
        session,
        ToolCall(
            id="statute_agent_mcp",
            name="search_statutes",
            arguments={"query": question, "applicable_at": applicable_at},
        ),
    )
    if result.status is Status.NO_MATCH:
        return [], ""
    if result.status is not Status.OK:
        return [], result.detail or "外部法源暂不可用"

    gate = CitationGate(GatePolicy())
    pool = {source.source_id: source for source in result.sources}
    out: list[dict[str, Any]] = []
    for source in result.sources[:limit]:
        if source.kind != "statute" or source.synthetic:
            continue
        if not include_invalid and source.effective_status not in {"现行有效", "有效"}:
            continue
        valid, _, reason = gate.check(
            Citation(source_id=source.source_id, identifier=source.identifier, quote=source.quote),
            pool,
            applicable_at=applicable_at or None,
        )
        no_match = re.search(r"第[一二三四五六七八九十百千零\d]+条(?:之[一二三四五六七八九十]+)?", source.identifier)
        out.append({
            "bbbs": "",
            "source_id": source.source_id,
            "title": source.title or source.identifier,
            "no": no_match.group(0) if no_match else "",
            "text": source.quote,
            "organ": "",
            "publish_date": "",
            "effective_date": source.applicable_from or "",
            "status_code": "valid" if source.effective_status in {"现行有效", "有效"} else "unknown",
            "status_text": source.effective_status,
            "rank": 0,
            "source": "mcp",
            "matched_query": question,
            "citation_checked": valid,
            "check_note": "与本次法源返回的原文及效力字段一致；具体适用仍需核对" if valid else reason,
            "uri": source.uri or "",
        })
    return out, ""


async def search_with_agent(payload: dict[str, Any]) -> dict[str, Any]:
    question = str(payload.get("question") or "").strip()
    clarification = str(payload.get("clarification") or "").strip()
    skip_clarification = payload.get("skip_clarification") is True
    include_invalid = payload.get("include_invalid") is True
    if len(question) < 2 or len(question) > 500 or len(clarification) > 500:
        raise ApiError("invalid_request", "请用 2—500 字描述法律问题，补充信息不超过 500 字。")
    cfg = get_config()
    user_text = question + (f"\n补充事实：{clarification}" if clarification else "")
    model_used = cfg.model_provider != "stub"
    if model_used:
        ctx = _PlanningContext(cfg)
        plan = await agent_loop(
            ctx,
            label="statute_plan",
            prompt=(
                "请根据以下问题生成 1—3 个简短的法律检索式，每个检索式尽量不超过 3 个词；"
                "不要写完整句子，不要编造具体条号。applicable_at 只能填写用户明确给出的 YYYY-MM-DD，"
                "否则填空字符串。需要追问时只给一个关键问题。\n"
                f"用户问题：{user_text}\n"
                f"用户选择按现有信息继续：{'是' if skip_clarification else '否'}"
            ),
            schema=PLAN_SCHEMA,
            tools=[],
            temperature=0.1,
        )
    else:
        plan = {
            "issue": question,
            "queries": [_clean_query(question)],
            "need_clarification": False,
            "clarifying_question": "",
            "assumptions": [],
            "applicable_at": "",
        }

    if plan.get("need_clarification") and plan.get("clarifying_question") and not (clarification or skip_clarification):
        return {
            "status": "needs_clarification",
            "question": str(plan["clarifying_question"])[:180],
            "issue": str(plan.get("issue") or question)[:180],
            "mode": "model" if model_used else "rules",
        }

    queries = _queries(plan, question)
    applicable_at = _applicable_at(plan, user_text)
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    attempted: list[str] = []
    for query in queries:
        attempted.append(query)
        try:
            found = search(query, include_invalid=include_invalid, limit=10)["items"]
        except (sqlite3.OperationalError, ValueError):
            found = []
        for hit in found:
            key = (str(hit["bbbs"]), str(hit["no"]))
            if key not in seen:
                items.append(_local_hit(hit, query, applicable_at))
                seen.add(key)
        if len(items) >= 8:
            break

    # 全句 AND 匹配过窄时，拆成短词再查一轮；仍由真实检索工具给出条文。
    if not items:
        for term in [x for x in re.split(r"[\s，。；、]+", " ".join(queries)) if 2 <= len(x) <= 12][:4]:
            if term in attempted:
                continue
            attempted.append(term)
            try:
                found = search(term, include_invalid=include_invalid, limit=5)["items"]
            except (sqlite3.OperationalError, ValueError):
                continue
            for hit in found:
                key = (str(hit["bbbs"]), str(hit["no"]))
                if key not in seen:
                    items.append(_local_hit(hit, term, applicable_at))
                    seen.add(key)
            if len(items) >= 5:
                break

    # 多检索式命中的候选统一重排；优先让标题和正文同时覆盖争议关键词的条文靠前。
    terms = {
        term
        for query in queries
        for term in query.split()
        if 2 <= len(term) <= 12 and term not in {"法律", "规定", "哪些", "可以"}
    }
    employer_termination = any(word in question for word in ("辞退", "开除")) and any(
        word in question for word in ("公司", "单位", "老板", "企业")
    )

    def relevance(hit: dict[str, Any]) -> tuple[int, int, float]:
        title = str(hit.get("title") or "")
        body = str(hit.get("text") or "")
        overlap = sum((3 if term in title else 0) + (2 if term in body else 0) for term in terms)
        if employer_termination:
            if "用人单位" in body and "解除" in body:
                overlap += 8
            if "劳动者可以" in body and "解除" in body and "用人单位" not in body:
                overlap -= 6
        tier = int(hit.get("tier", 3))
        return (overlap, -tier, -float(hit.get("score") or 0))

    items.sort(key=relevance, reverse=True)

    mcp_note = ""
    if len(items) < 3 and cfg.mcp_endpoints().get("search_statutes"):
        external, mcp_note = await asyncio.to_thread(
            _mcp_hits, str(plan.get("issue") or question)[:120], applicable_at, 6, include_invalid
        )
        known = {(str(x["title"]), str(x["no"]), str(x["text"])[:80]) for x in items}
        for hit in external:
            key = (str(hit["title"]), str(hit["no"]), str(hit["text"])[:80])
            if key not in known:
                items.append(hit)
                known.add(key)

    items = items[:10]
    if not items:
        notice = (
            "本地法规库未命中，外部法源调用也未成功；这不代表没有相关规定。请尝试更具体的关键词。"
            if mcp_note else "当前检索范围内未找到匹配条文。可补充法律关系、争议行为或更具体的关键词。"
        )
    else:
        notice = "模型未配置，本次按关键词检索。" if not model_used else ""
        if mcp_note:
            notice += " 外部法源暂不可用，已展示本地库结果。"

    return {
        "status": "completed",
        "mode": "model" if model_used else "rules",
        "issue": str(plan.get("issue") or question)[:180],
        "queries": attempted,
        "applicable_at": applicable_at,
        "assumptions": [str(x)[:160] for x in (plan.get("assumptions") or [])[:3]],
        "items": items,
        "notice": notice.strip(),
    }
