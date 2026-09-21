"""类案检索与研究 workflow（PRD 3.5.3、5.1）。六个 phase，矩阵与分布由代码算。"""

from __future__ import annotations

import logging
import re
from typing import Any

from ..config import get_config
from ..errors import ApiError
from ..gates.runner import apply_gates, extract_claims
from ..llm import ToolCall
from ..materials import mark_material_corroboration
from ..models import Status
from ..prompts import build_extract_prompt, build_retrieval_prompt, build_synthesis_prompt
from ..schemas import CASE_ELEMENT_SCHEMA, POOL_SCHEMA, SYNTHESIS_SCHEMA
from ..tools import dispatch_tool
from .runtime import RunContext

LOG = logging.getLogger("faxiaozhi.research")


async def research_workflow(ctx: RunContext, args: dict[str, Any]) -> dict[str, Any]:
    cfg = get_config()
    session = ctx.session
    topic = str(args.get("topic") or session.topic)
    conditions: dict[str, Any] = dict(args.get("conditions") or {})
    max_candidates = int(cfg.get("limits.max_candidates", 30))
    min_sample = int(cfg.get("limits.min_sample_for_conclusion", 2))
    max_source_chars = int(cfg.get("limits.max_source_chars", 6000))

    session.topic = topic
    session.conditions = conditions

    # ---- Phase 1 检索（2-4：用户材料为主、检索为辅）------------------
    ctx.phase("检索")
    material_cases = _material_case_sources(session)
    sufficient = bool(material_cases) and len(material_cases) >= min_sample
    session.material_primary = sufficient
    pool: dict[str, Any] = {}
    if sufficient:
        # 触发规则 1：默认不检索——材料已够，不调法宝（省配额、防候选池被检索结果稀释）
        session.limitations.append(
            f"本次以你上传的 {len(material_cases)} 篇材料为主：材料已满足生成综合结论所需的最小样本量"
            f"（{min_sample} 篇），按「默认不检索」策略本次未调用法宝检索。"
        )
        ctx.log(f"材料为主：{len(material_cases)} 篇材料满足最小样本量，本次不调用法宝检索")
        ctx.emit(
            "tool",
            {
                "tool": "search_cases",
                "status": Status.OK.value,
                "detail": "材料充足，按「材料为主」策略跳过检索（法宝调用 0 次）",
                "sources_returned": 0,
                "origin": "user",
                "skipped": True,
            },
        )
    elif material_cases:
        # 触发规则 2：材料不足 → **由代码强制**补一次检索。
        # 修复（2-4 代操作验收实测缺陷 6）：原先这一步交给模型编排，而提示词又写着
        # 「材料为主、材料已能支撑结论时不要习惯性去检索」，真实模型于是根本不调工具
        # → 规则 2 形同虚设（桩模型下测试是绿的，真模型下不触发）。
        # 规则必须由代码执行，不靠模型自觉（与门禁同一原则），且确定性调用不花模型费。
        before_ids = set(session.source_pool)
        result = dispatch_tool(
            session,
            ToolCall(
                id="call_auto_supplement",
                name="search_cases",
                arguments={"expression": topic, "limit": max_candidates},
            ),
        )
        failed = result.status in (Status.INTERFACE_ERROR, Status.PARSE_ERROR)
        added = 0 if failed else _mark_supplement_sources(session, before_ids)
        if not failed:
            session.supplement_rounds += 1
        ctx.emit("tool", {**result.event(), "origin": "supplement", "auto": True})
        if failed:
            session.add_gap(
                "evidence",
                f"材料不足（{len(material_cases)} 篇 < {min_sample} 篇），自动补充检索失败"
                f"（{result.status.value}）：以现有材料出结论，不硬凑。",
            )
        else:
            ctx.log(
                f"材料不足：{len(material_cases)} 篇 < 最小样本量 {min_sample} 篇，"
                f"已自动补充检索（新增 {added} 条）"
            )
            session.limitations.append(
                f"本次上传材料 {len(material_cases)} 篇，少于生成综合结论所需的最小样本量 {min_sample} 篇，"
                f"已自动补充一次检索（新增 {added} 条，单独标注为「补充来源」）；"
                "你上传的材料仍排在候选池前面、仍是矩阵主体。"
            )
    else:
        try:
            pool = await ctx.agent(
                label="retrieve",
                prompt=build_retrieval_prompt(
                    topic, conditions, max_candidates, material_primary=False
                ),
                schema=POOL_SCHEMA,
                tools=["search_cases", "search_statutes"],
            )
        except ApiError as exc:
            # 检索步的模型输出不合规，不应阻断整条链路：工具已经真实调用过了
            session.add_gap(
                "evidence", f"检索编排输出不合规（{exc.message}），已改用工具真实返回装配候选池"
            )
            ctx.emit(
                "tool",
                {
                    "tool": "retrieve",
                    "status": Status.INSUFFICIENT.value,
                    "detail": exc.message,
                    "sources_returned": 0,
                },
            )

    candidates = _assemble_candidates(session, max_candidates)
    session.candidates = candidates
    session.sample.set_candidates([str(c["source_id"]) for c in candidates])
    conflicts = mark_material_corroboration(session.source_pool)
    if conflicts:
        session.limitations.append(
            f"检测到 {conflicts} 处来源冲突（用户材料与法宝对同一案号的原文不一致）："
            "已默认拦住，需你人工确认后才能导出。"
        )
        ctx.emit(
            "conflict",
            {
                "count": conflicts,
                "source_ids": [
                    s.source_id for s in session.source_pool.values() if s.corroboration == "conflict"
                ],
            },
        )
    echo = pool.get("conditions_echo") or {}
    if echo.get("ignored"):
        session.limitations.append("以下条件未生效：" + "；".join(str(x) for x in echo["ignored"]))
    ctx.log(f"候选池 {len(candidates)} 条（由代码从依据池装配）")
    ctx.snapshot_sources()

    if not candidates:
        if session.has_interface_error():
            # 2-3：法宝不可用时，只在这时用**本地依据库缓存**回退（同主题、此前真实检索所得）
            absorbed = _absorb_local_fallback(session, max_candidates)
            if absorbed:
                session.limitations.append(
                    "检索接口调用失败；本次候选池来自**本地依据库缓存**（内容为此前真实检索所得），"
                    "法宝未参与本次检索。来源均标注为「单源（本地依据库）」。可点击重试以重新联网检索。"
                )
                candidates = _assemble_candidates(session, max_candidates)
                session.candidates = candidates
                session.sample.set_candidates([str(c["source_id"]) for c in candidates])
                ctx.log(f"法宝不可用：改用本地依据库缓存，候选 {len(candidates)} 条")
                ctx.emit("tool", {"tool": "search_cases", "status": Status.OK.value,
                                  "detail": "本地依据库缓存回退", "sources_returned": len(candidates)})
                ctx.snapshot_sources()
            else:
                # 「接口失败 ≠ 没有案例」：绝不能说成「候选池为空，请调整条件」
                session.limitations.append(
                    "检索接口调用失败，本次未获得可核验依据。这不等于无相关案例："
                    "本次未产出结论的原因是接口没有返回数据，不是数据源里没有匹配。可点击重试。"
                )
                raise ApiError(
                    "mcp_unavailable",
                    "检索接口调用失败，本次未获得可核验依据。这不等于「无相关案例」。可点击重试。",
                )
        else:
            session.limitations.append("候选池为空，本次未做样本确认与综合，请调整条件后重试。")
            return await _finalize(ctx, min_sample, reason="候选池为空")

    if not candidates:
        session.limitations.append("本地依据库也没有可用缓存，本次未产出结论。")
        return await _finalize(ctx, min_sample, reason="无可用候选")

    # ---- Phase 2 确认样本（人工门，不可绕过）--------------------------
    ctx.phase("确认样本")
    await ctx.checkpoint("sample_confirm", payload={"candidates": candidates, "conditions_echo": echo})
    confirmed = session.sample.confirmed

    if len(confirmed) < min_sample:
        session.limitations.append(
            f"确认样本 {len(confirmed)} 篇，低于生成综合结论所需的最小样本量 {min_sample} 篇，"
            "本次仅产出单案要素，不生成综合结论。"
        )

    # ---- Phase 3 逐案提炼（pipeline，单项失败不阻断全局）--------------
    ctx.phase("逐案提炼")

    async def extract_one(source_id: str, index: int) -> dict[str, Any]:
        element = await ctx.agent(
            label=f"extract:{source_id}",
            prompt=build_extract_prompt(session, source_id, max_source_chars),
            schema=CASE_ELEMENT_SCHEMA,
            tools=["verify_citation"],
        )
        if isinstance(element, dict):
            # 「能算的别让模型写」：案件标识必须由代码对齐，否则矩阵会与样本对不上
            if element.get("case_id") != source_id:
                element["case_id"] = source_id
        return element

    cases: list[dict[str, Any]] = await ctx.pipeline(confirmed, extract_one)
    session.cases = [c for c in cases if isinstance(c, dict)]
    _align_case_ids(session)

    # ---- Phase 4 横向对比（代码计算）---------------------------------
    ctx.phase("横向对比")
    matrix_result = dispatch_tool(
        session, ToolCall(id="call_matrix", name="render_matrix", arguments={})
    )
    ctx.emit("tool", matrix_result.event())
    if matrix_result.status is not Status.OK:
        session.limitations.append(matrix_result.detail)
        return await _finalize(ctx, min_sample, reason=matrix_result.detail)

    # ---- Phase 5 综合（结构化输出强制带引用）-------------------------
    ctx.phase("综合")
    if len(confirmed) >= min_sample:
        synthesis = await ctx.agent(
            label="synthesize",
            prompt=build_synthesis_prompt(session, max_source_chars),
            schema=SYNTHESIS_SCHEMA,
            tools=[],
        )
        _humanize_source_ids(session, synthesis)
        session.synthesis = synthesis
        session.structured_output = synthesis
        session.pending_claims = extract_claims(session, "")
        report = apply_gates(session, applicable_at=conditions.get("applicable_at"))
        ctx.emit("gate", report.event())

        # 命中表达边界 → 自动改写一次再核验（PRD 4.3.2 要求「提示改写」；这里做成一次有界自动重试）
        if report.guard_hits:
            ctx.log("命中表达边界，正在自动改写一次后重新核验")
            session.add_gap("expression", "首次输出含越界表述，已自动改写一次：" + "；".join(report.guard_hits[:3]))
            retry = await ctx.agent(
                label="synthesize_rewrite",
                prompt=build_synthesis_prompt(session, max_source_chars)
                + "\n\n【必须改写的越界表述】\n"
                + "\n".join(f"- 「{h}」" for h in report.guard_hits[:5])
                + "\n请删掉这些表述，改为「本次确认样本 N 篇中 n 篇…」的口径，其余内容与引用保持不变。",
                schema=SYNTHESIS_SCHEMA,
                tools=[],
            )
            _humanize_source_ids(session, retry)
            session.synthesis = retry
            session.structured_output = retry
            session.pending_claims = extract_claims(session, "")
            report = apply_gates(session, applicable_at=conditions.get("applicable_at"))
            ctx.emit("gate", report.event())

        if report.rejected:
            session.limitations.append(
                f"有 {len(report.rejected)} 条引用未通过核验，相关结论已降级为「依据缺口」，未作为结论输出。"
            )
        if report.guard_hits:
            session.limitations.append("改写后仍存在越界表述，已阻止导出。")
    else:
        session.synthesis = None

    return await _finalize(ctx, min_sample)


_SOURCE_TOKEN = re.compile(r"\b(?:mcp_(?:case|statute)_\d+|user_[A-Za-z0-9]+)\b")


def humanize_source_ids(session: Any, text: str) -> str:
    """把文本里的内部来源编号（mcp_case_3 / user_xxx）换成人类可读的案号/法规名。

    确定性改写，不交给模型；2-5 咨询复用同一实现。
    """
    mapping = {
        sid: (source.identifier or source.title or sid)
        for sid, source in session.source_pool.items()
    }

    def sub(match: re.Match[str]) -> str:
        sid = match.group(0)
        label = mapping.get(sid)
        return f"「{label}」" if label else sid

    return _SOURCE_TOKEN.sub(sub, text or "")


def _humanize_source_ids(session: Any, synthesis: dict[str, Any]) -> None:
    """把结论正文里的内部来源编号替换成人类可读的案号/法规名（确定性改写，不交给模型）。"""

    def rewrite(text: str) -> str:
        return humanize_source_ids(session, text)

    for item in synthesis.get("conclusions") or []:
        if isinstance(item, dict):
            item["text"] = rewrite(str(item.get("text", "")))
    for key in ("summary", "notes"):
        if isinstance(synthesis.get(key), str):
            synthesis[key] = rewrite(synthesis[key])


def _align_case_ids(session: Any) -> None:
    """兜底：把提炼结果的 case_id 按「案号/法规名」对齐到来源编号（防模型写错）。"""
    by_identifier = {
        str(source.identifier): sid for sid, source in session.source_pool.items()
    }
    for case in session.cases:
        if not isinstance(case, dict):
            continue
        cid = str(case.get("case_id") or "")
        if cid in session.sample.confirmed_set:
            continue
        for sid in session.sample.confirmed:
            source = session.source_pool.get(sid)
            if source and cid and cid in str(source.identifier):
                case["case_id"] = sid
                break
        else:
            if cid in by_identifier:
                case["case_id"] = by_identifier[cid]


def _absorb_local_fallback(session: Any, limit: int) -> int:
    """法宝不可用时的**最后手段**：把同主题此前真实检索过的条目装回依据池。

    只在实时检索完全失败时调用；来源标记 `origin=local` / `corroboration=single_local` / `local_hit=True`。
    绝不在实时检索成功时使用（防候选池偏向历史）。
    """
    try:
        from ..library import entry_to_source, get_library

        entries = get_library().topic_fallback(session.topic, session.conditions)
    except Exception:
        return 0
    added = 0
    for entry in entries:
        if entry.content_type not in ("case", "statute"):
            continue
        source = entry_to_source(entry)
        source.local_hit = True
        source.corroboration = "single_local"
        if source.source_id in session.source_pool:
            # 已在池中（例如本会话已命中同一条本地缓存）也算回退可用
            if session.source_pool[source.source_id].origin == "local":
                added += 1
            continue
        session.source_pool[source.source_id] = source
        added += 1
    return added


def _material_case_sources(session: Any) -> list[Any]:
    """本次会话里可用的**案例角色用户材料**（已登记的、未 superseded）。"""
    registry = getattr(session, "material_registry", None)
    active_ids = {r.source_id for r in registry.active_case_materials()} if registry else set()
    out: list[Any] = []
    for source in session.source_pool.values():
        if not source.is_user_material or source.kind != "case" or source.superseded:
            continue
        if active_ids and source.source_id not in active_ids:
            continue
        out.append(source)
    return out


def _mark_supplement_sources(session: Any, before_ids: set[str]) -> int:
    """把本次补充检索新增的来源标为「补充来源」（不改变用户材料的主体地位）。"""
    added = 0
    for source_id, source in session.source_pool.items():
        if source_id in before_ids or source.is_user_material:
            continue
        source.supplement = True
        added += 1
    return added


def _assemble_candidates(session: Any, limit: int) -> list[dict[str, Any]]:
    """候选池 = 依据池里所有「案例」来源，**由代码生成**（PRD 附录 A 第 3 条）。

    2-4 排序：**用户上传材料永远排在前面**（材料为主），补充检索与法宝来源在后；
    已被新版本取代的材料（superseded）不参与候选。
    """
    rows: list[dict[str, Any]] = []
    for source in session.source_pool.values():
        if source.kind != "case" or source.superseded:
            continue
        rows.append(
            {
                "source_id": source.source_id,
                "identifier": source.identifier,
                "title": source.title,
                "court": source.court or "",
                "level": source.level or "",
                "region": source.region or "",
                "decided_on": source.decided_on or "",
                "status": source.status.value,
                "origin": source.origin,
                "origin_text": source.origin_text,
                "supplement": source.supplement,
                "user_verified": source.user_verified,
                "identifier_missing": source.identifier_missing,
                "corroboration": source.corroboration,
            }
        )
    rows.sort(
        key=lambda r: (
            0 if r.get("origin") == "user" else 1,
            str(r.get("decided_on") or ""),
            str(r.get("identifier") or ""),
        )
    )
    return rows[:limit]


async def _finalize(ctx: RunContext, min_sample: int, reason: str = "") -> dict[str, Any]:
    from ..gates.runner import gate_summary

    session = ctx.session
    ctx.phase("成稿")
    ctx.snapshot_sources()
    summary = gate_summary(session)
    output = {
        "run_id": ctx.run_id,
        "status": session.status,
        "reason": reason,
        "pool": session.candidates,
        "sample": {
            "confirmed": session.sample.confirmed,
            "excluded": session.sample.excluded,
            "locked": session.sample.locked,
        },
        "cases": session.cases,
        "matrix": session.matrix,
        "distribution": session.distribution,
        "synthesis": session.synthesis,
        "limitations": session.limitations,
        "gate": summary,
        "gaps": [g.__dict__ for g in session.gaps],
        "usage": ctx.usage,
        "first_response_ms": ctx.first_response_ms,
        "steps": ctx.steps,
    }
    if session.gate_report is None:
        ctx.emit("gate", {"accepted": 0, "rejected": 0, "guard_hits": 0, "coverage": 0.0})
    ctx.emit(
        "done",
        {
            "run_id": ctx.run_id,
            "status": "content_ready" if session.synthesis else "evidence_collected",
            "summary": {
                "candidates": len(session.candidates),
                "confirmed": len(session.sample.confirmed),
                "excluded": len(session.sample.excluded),
                "matrix_rows": len(session.matrix),
                "conclusions": len((session.synthesis or {}).get("conclusions") or []),
                "citations_accepted": (session.gate_report.accepted if session.gate_report else 0),
                "citations_rejected": (
                    len(session.gate_report.rejected) if session.gate_report else 0
                ),
                "gaps": len(session.gaps),
                "min_sample": min_sample,
            },
            "export_blockers": session.export_blockers(),
            "demo_mode": bool(get_config().get("policy.allow_synthetic", False)),
        },
    )
    return output


WORKFLOWS: dict[str, Any] = {"legal-research": research_workflow}

#: 公开别名（2-4）：服务端「补充检索」需重新装配候选池，复用同一处逻辑，不重复实现
assemble_candidates = _assemble_candidates
