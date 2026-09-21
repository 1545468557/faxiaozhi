"""法律咨询 workflow（阶段 2-5；PRD 1.2 / 3.2.6 / 9.2 / C-10 / C-12 / C-14）。

产品经理 2026-09-18 确认的五项决策（台账 D12）：
① 「追问 ≤3 轮」= **AI 向用户澄清事实**的轮数；用户继续提问不受限；
② 咨询**不加 checkpoint**（对话式）：追问即结束本次运行，用户再发一条消息即续；
③ 可导出「咨询备忘」（**不经过模型工具池**，由产品层按钮触发）；
④ 拒答边界全部配置化（config: consult.expression_extra）；
⑤ 依据照走双源印证（复用 2-3 / 2-4）。

与 2-4 的教训一致：**规则由代码强制**。
- 追问轮数由代码计数（`state["rounds"]`），不靠模型自觉；
- 依据检索由代码直接调用工具（不靠模型是否愿意调），同 2-4 缺陷 6 的修法；
- 引用核验、表达边界、缺口全部由代码判定。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from ..config import get_config
from ..errors import ApiError
from ..gates.runner import apply_gates, extract_claims
from ..llm import ToolCall
from ..models import Status
from ..prompts import build_consult_answer_prompt, build_consult_identify_prompt
from ..schemas import CONSULT_ANSWER_SCHEMA, CONSULT_IDENTIFY_SCHEMA
from ..tools import dispatch_tool
from .research import humanize_source_ids
from .runtime import RunContext

LOG = logging.getLogger("faxiaozhi.consult")


async def consult_workflow(ctx: RunContext, args: dict[str, Any]) -> dict[str, Any]:
    cfg = get_config()
    session = ctx.session
    session.branch = "consult"

    max_rounds = min(3, max(0, int(cfg.get("consult.max_clarifying_rounds", 3))))
    max_questions = 1  # 每轮只问一个关键事实，配置不能放宽这条产品约束。
    max_candidates = int(cfg.get("limits.max_candidates", 30))
    max_source_chars = int(cfg.get("limits.max_source_chars", 6000))

    state: dict[str, Any] = session.consult
    state.setdefault("facts", [])
    state.setdefault("asked", [])
    state.setdefault("rounds", 0)
    state["max_rounds"] = max_rounds

    # 仅内存保存进度与案情；同 run 的重试不再把上一条补充当作新问题。
    # run_id 必须匹配，不能把别的运行或重启后缺失的私有上下文拿来续跑。
    resuming = state.get("run_id") == ctx.run_id and bool(state.get("facts"))
    text = str(args.get("question") or args.get("text") or "").strip()
    if not resuming:
        skipping = args.get("skip_clarification") is True
        if skipping and (not state.get("awaiting") or not state.get("facts")):
            raise ApiError("invalid_request", "当前没有待回答的追问，不能跳过。")
        if not text and not skipping:
            raise ApiError("consult_not_recoverable")
        if state.get("awaiting"):
            if text:
                state["facts"].append(text)
            state["awaiting"] = False
        else:
            _reset_for_new_question(session, text)
        state["run_id"] = ctx.run_id
        state["resume_phase"] = "identify"
        if skipping:
            # 用户主动结束所有追问：不再花一轮模型识别，未知事实不能伪装成已知事实。
            state["skipped_clarification"] = True
            issue = dict(state.get("issue") or {})
            unknown = list(dict.fromkeys(
                str(item).strip()
                for item in [*(issue.get("missing_facts") or []), *(issue.get("clarify_questions") or [])]
                if str(item).strip()
            ))
            state["assumptions"] = [
                f"尚未确认（若补充事实已说明则以补充为准）：{item}" for item in unknown
            ] or ["尚未补充的关键事实未知；仅按已提供事实作条件性分析。"]
            issue.update(given_facts=list(state["facts"]), need_clarify=False, clarify_questions=[])
            state["issue"] = issue
            state["resume_phase"] = "retrieve"
            state["status"] = "retrieving"
            session.limitations.append(
                "【咨询】用户主动跳过剩余追问；以下解答仅基于已提供事实，未确认事项不作为既定事实："
                + "；".join(state["assumptions"])
            )
            ctx.log("用户主动跳过剩余追问，开始检索依据并按现有事实作答")

    if state.get("resume_phase") == "identify":
        # ---- Phase 1 问题识别 -------------------------------------------------
        ctx.phase("识别问题")
        try:
            issue = await ctx.agent(
                label="consult_identify",
                prompt=build_consult_identify_prompt(
                    session, state["facts"], state["rounds"], max_rounds, state["asked"], max_questions
                ),
                schema=CONSULT_IDENTIFY_SCHEMA,
                tools=[],
            )
        except ApiError as exc:
            if exc.code != "schema_invalid":
                raise
            # 识别失败不应把整次咨询弄死：降级为“不追问，直接检索依据”（2-5 实测）
            session.add_gap("schema", f"问题识别输出不合规（{exc.message}），已跳过追问直接检索依据。")
            ctx.emit(
                "tool",
                {
                    "tool": "consult_identify",
                    "status": Status.INSUFFICIENT.value,
                    "detail": exc.message,
                    "sources_returned": 0,
                },
            )
            issue = {
                "legal_relation": "",
                "disputes": [],
                "given_facts": list(state["facts"]),
                "missing_facts": [],
                "possible_claims": [],
                "need_clarify": False,
                "clarify_questions": [],
                "assumptions": [],
            }
        questions = _pick_questions(issue, state["asked"], max_questions)
        will_ask = issue.get("need_clarify") and state["rounds"] < max_rounds and questions
        # 快照与 SSE 只包含真正发出的单题，不能让页面恢复出未发送的候选问题。
        issue["clarify_questions"] = questions if will_ask else []
        state["issue"] = issue
        if will_ask:
            state["rounds"] += 1
            state["asked"].extend(questions)
            state["awaiting"] = True
            ctx.log(f"第 {state['rounds']} 轮追问（最多 {max_rounds} 轮）")
            ctx.emit(
                "clarify",
                {
                    "rounds_used": state["rounds"],
                    "max_rounds": max_rounds,
                    "remaining": max(0, max_rounds - state["rounds"]),
                    "questions": questions,
                },
            )
            state["status"] = "clarifying"
            return {
                "run_id": ctx.run_id,
                "status": "awaiting_answer",
                "branch": "consult",
                "rounds_used": state["rounds"],
                "max_rounds": max_rounds,
                "remaining": max(0, max_rounds - state["rounds"]),
                "questions": questions,
                "issue": issue,
            }

        assumptions: list[str] = []
        if issue.get("need_clarify"):
            # 到上限（或模型没给出可用问题）→ 基于假设作答，且假设必须写明
            raw = issue.get("assumptions") or issue.get("missing_facts") or []
            assumptions = [str(x) for x in raw][:5]
            reason = (
                f"追问已达上限（{max_rounds} 轮）" if state["rounds"] >= max_rounds
                else "尚有事实缺口，本轮没有新的有效追问"
            )
            session.limitations.append(
                f"【咨询】{reason}，以下解答基于假设："
                + ("；".join(assumptions) if assumptions else "（未提供的关键事实）")
            )
            ctx.log(f"{reason}，基于现有事实与假设作答")

        state["assumptions"] = assumptions
        state["resume_phase"] = "retrieve"

    issue = state["issue"]
    assumptions = state.get("assumptions") or []

    if state.get("resume_phase") == "retrieve":
        # ---- Phase 2 依据检索（**代码强制调用**，不靠模型自觉）-----------------
        ctx.phase("检索依据")
        tool_specs = [
            ("call_statutes", "search_statutes", {"query": session.topic, "applicable_at": ""}),
            (
                "call_cases",
                "search_cases",
                {"expression": session.topic, "limit": max_candidates},
            ),
        ]
        failed = 0
        for call_id, name, arguments in tool_specs:
            result = dispatch_tool(session, ToolCall(id=call_id, name=name, arguments=arguments))
            ctx.emit("tool", result.event())
            ctx.on_tool_result(name, result)
            if result.status in (Status.INTERFACE_ERROR, Status.PARSE_ERROR):
                failed += 1

        if failed == len(tool_specs) and not session.source_pool:
            # 「接口失败 ≠ 没有相关规定」（C-05）：绝不能说成「查不到」
            session.limitations.append(
                "依据检索接口调用失败，本次未获得可核验依据。这不等于「没有相关规定」："
                "没有结论的原因是接口没有返回数据。可点击重试。"
            )
            raise ApiError(
                "mcp_unavailable",
                "依据检索接口调用失败，本次未获得可核验依据。这不等于「没有相关规定」。可点击重试。",
            )

        state["resume_phase"] = "answer"

    # ---- Phase 3 解答（结构化输出强制带引用）------------------------------
    ctx.phase("解答")
    try:
        answer = await ctx.agent(
            label="consult_answer",
            prompt=build_consult_answer_prompt(session, issue, assumptions, max_source_chars),
            schema=CONSULT_ANSWER_SCHEMA,
            tools=[],
        )
    except ApiError:
        # 模型失败不是“依据不足”：保留错误码，由 launch 发 error 并开放当前步骤重试。
        # facts / issue / assumptions / source_pool 都留在本会话内存，不重新追问或检索。
        state["status"] = "failed"
        raise
    _humanize(session, answer)
    report = _screen(session, answer)
    ctx.emit("gate", report.event())

    # 命中表达边界 → 自动改写一次再核验（与 2-4 同一套处理）
    if report.guard_hits:
        ctx.log("命中表达边界，正在自动改写一次后重新核验")
        session.add_gap(
            "expression", "首次输出含越界表述，已自动改写一次：" + "；".join(report.guard_hits[:3])
        )
        try:
            retry = await ctx.agent(
                label="consult_answer_rewrite",
                prompt=build_consult_answer_prompt(session, issue, assumptions, max_source_chars)
                + "\n\n【必须改写的越界表述】\n"
                + "\n".join(f"- 「{h}」" for h in report.guard_hits[:5])
                + "\n请删掉这些表述，改为客观、不承诺结果的口径（不得出现胜诉承诺、百分百、替代律师、"
                "可直接提交法院等表述），其余内容与引用保持不变。",
                schema=CONSULT_ANSWER_SCHEMA,
                tools=[],
            )
        except ApiError:
            state["status"] = "failed"
            raise
        else:
            _humanize(session, retry)
            answer = retry
            report = _screen(session, answer)
            ctx.emit("gate", report.event())

    if report.rejected:
        session.limitations.append(
            f"有 {len(report.rejected)} 条引用未通过核验，相关结论已降级为「依据缺口」，未作为结论输出。"
        )
    if report.guard_hits:
        session.limitations.append("改写后仍存在越界表述，已阻止导出。")

    state["answer"] = answer
    passed_final = (session.structured_output or {}).get("_passed_conclusions") or []
    state["status"] = "answered" if passed_final else "insufficient"
    # 界面只展示**通过门禁**的结论（未通过的不当结论展示）
    state["passed"] = list(passed_final)
    state["max_rounds"] = max_rounds
    return _finalize(ctx, answer)


# ------------------------------------------------------------------ 内部工具


def _reset_for_new_question(session: Any, text: str) -> None:
    """新问题 = 新的一轮咨询：清掉上一问**检索到的**来源，但**保留用户上传的材料**。

    2-5：咨询允许上传背景材料（PRD §3.2.6 的 consult 工具白名单含 parse_document）。
    实测发现：早期版本把 source_pool 整体清空，会把用户刚上传的材料一并删掉，
    用户看到“上传了却没被用上”。这里只清非用户材料的部分。
    """
    session.consult.update(
        {"facts": [text], "asked": [], "rounds": 0, "status": "identifying", "awaiting": False}
    )
    session.consult.pop("answer", None)
    session.consult.pop("issue", None)
    session.consult.pop("passed", None)
    session.consult.pop("assumptions", None)
    session.consult.pop("skipped_clarification", None)
    session.topic = text
    # 只保留用户材料，其余（上一问的法条/案例）全部重查
    session.source_pool = {
        sid: source for sid, source in session.source_pool.items() if source.is_user_material
    }
    session.gate_report = None
    session.expression_hits = []
    session.pending_claims = []
    session.structured_output = None
    session.cited_ids = []
    session.gaps.clear()
    session.limitations = []


def _pick_questions(issue: dict[str, Any], asked: list[str], limit: int) -> list[str]:
    """取本轮的追问问题：去重（含与历史问题重复的）、限个数、只保留非空。"""
    seen = {str(item).strip() for item in asked}
    out: list[str] = []
    for raw in issue.get("clarify_questions") or issue.get("missing_facts") or []:
        # 模型偶尔把多个问句/多行塞进一项；只发第一句，余下事项后续重新识别。
        question = re.split(r"(?<=[？?])\s*|[\r\n;；]+", str(raw).strip(), maxsplit=1)[0].strip()
        if not question or question in seen:
            continue
        seen.add(question)
        out.append(question)
        if len(out) >= min(limit, 1):
            break
    return out


def _humanize(session: Any, answer: dict[str, Any]) -> None:
    """把内部来源编号换成可读案号/法规名（确定性改写）。"""
    for item in answer.get("conclusions") or []:
        if isinstance(item, dict):
            item["text"] = humanize_source_ids(session, str(item.get("text", "")))
    for key in ("uncertainties", "next_steps"):
        if isinstance(answer.get(key), list):
            answer[key] = [humanize_source_ids(session, str(x)) for x in answer[key]]
    if isinstance(answer.get("insufficient"), str):
        answer["insufficient"] = humanize_source_ids(session, answer["insufficient"])


def _screen(session: Any, answer: dict[str, Any]) -> Any:
    """把解答交给三条门禁（引用核验 + 表达边界），结构上伪装成 `conclusions`。"""
    session.structured_output = {"conclusions": answer.get("conclusions") or []}
    session.pending_claims = extract_claims(session, "")
    return apply_gates(session)


def _finalize(ctx: RunContext, answer: dict[str, Any]) -> dict[str, Any]:
    from ..gates.runner import gate_summary

    session = ctx.session
    ctx.phase("完成")
    ctx.snapshot_sources()
    summary = gate_summary(session)
    passed = (session.structured_output or {}).get("_passed_conclusions") or []
    state = session.consult
    output = {
        "run_id": ctx.run_id,
        "status": state.get("status", "answered"),
        "branch": "consult",
        "consult": {
            "rounds_used": state.get("rounds", 0),
            "max_rounds": state["max_rounds"],
            "skipped_clarification": bool(state.get("skipped_clarification")),
            "assumptions": state.get("assumptions") or [],
            "issue": state.get("issue") or {},
            "answer": answer,
            "passed_conclusions": passed,
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
            "status": "content_ready" if passed else "evidence_collected",
            "summary": {
                "conclusions": len(passed),
                "citations_accepted": summary.get("accepted", 0),
                "citations_rejected": summary.get("rejected", 0),
                "rounds_used": state.get("rounds", 0),
            },
        },
    )
    return output


WORKFLOWS: dict[str, Any] = {"legal-consult": consult_workflow}
