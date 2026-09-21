"""会话态（纯内存，PRD 8.2）。材料原文只在这里，不落盘。"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .errors import ApiError
from .gates.sample import SampleLock
from .materials import MaterialRecord, MaterialRegistry
from .models import Claim, DegradationRecord, Gap, GateReport, Source, Status

SESSION_ACTIVE = "active"
SESSION_AWAITING = "awaiting_checkpoint"
SESSION_CLOSED = "closed"


@dataclass
class Session:
    id: str = field(default_factory=lambda: "s_" + uuid.uuid4().hex[:12])
    branch: str = "research"
    purpose: str | None = None
    foreground: bool = True
    queued: bool = False
    #: 补充检索轮数上限（2-4；来自 config: sources.supplement.max_supplement_rounds）
    max_supplement_rounds: int = 2
    #: 离线降级场景（clean / no_match / interface_error / abstract_only / insufficient）
    scenario: str = "clean"
    #: 强制走离线夹具（忽略已配置的真实 MCP 地址）
    force_offline: bool = False

    # 输入与要素
    topic: str = ""
    conditions: dict[str, Any] = field(default_factory=dict)
    conditions_confirmed: bool = False
    asked_rounds: int = 0
    stance: str | None = None
    #: 本次会话上传的材料（**仅内存，不落盘**）：material_id -> (bytes, filename)
    materials: dict[str, tuple[bytes, str]] = field(default_factory=dict)
    #: 2-4：材料登记表（角色 / 标识 / 核验 / 版本 / 定位索引；**不含原文**）
    material_registry: MaterialRegistry = field(default_factory=MaterialRegistry)
    #: 2-4：材料与法宝同案号比对的冲突来源（source_id -> 裁决记录，仅会话内存）
    conflict_resolutions: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: 2-4：本次研究已用的补充检索轮数（上限见 config: sources.supplement.max_supplement_rounds）
    supplement_rounds: int = 0
    #: 2-4：本次是否走「用户材料为主」路径（仅用于文案与指标，不参与判定）
    material_primary: bool = False
    #: 2-5 法律咨询：问题识别 / 追问计数 / 解答（**仅内存**；用户案情原文不落盘、不进 journal）
    consult: dict[str, Any] = field(default_factory=dict)
    #: 2-6 合同审查：立场 / 条款块 / 风险条目（**仅内存**；合同原文不落盘，C-12）
    contract: dict[str, Any] = field(default_factory=dict)

    # 依据与样本
    source_pool: dict[str, Source] = field(default_factory=dict)
    sample: SampleLock = field(default_factory=SampleLock)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    cases: list[dict[str, Any]] = field(default_factory=list)
    matrix: list[dict[str, Any]] = field(default_factory=list)
    distribution: dict[str, Any] = field(default_factory=dict)
    synthesis: dict[str, Any] | None = None
    limitations: list[str] = field(default_factory=list)

    # 门禁产物
    gate_report: GateReport | None = None
    gaps: list[Gap] = field(default_factory=list)
    pending_claims: list[Claim] = field(default_factory=list)
    expression_hits: list[dict[str, str]] = field(default_factory=list)
    cited_ids: list[str] = field(default_factory=list)
    structured_output: dict[str, Any] | None = None

    # 运行时
    messages: list[dict[str, Any]] = field(default_factory=list)
    phase: str = ""
    status: str = SESSION_ACTIVE
    run_id: str | None = None
    events: asyncio.Queue = field(default_factory=asyncio.Queue)
    checkpoint_waiter: asyncio.Event = field(default_factory=asyncio.Event)
    checkpoint_kind: str | None = None
    checkpoint_value: dict[str, Any] | None = None
    wants_resume: bool = False

    # 降级与失败（2-2 新增）
    degradations: list[DegradationRecord] = field(default_factory=list)
    failed_step: str | None = None
    failed_reason: str | None = None
    retry_count: int = 0

    # 指标
    metrics: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    first_response_ms: int | None = None
    total_ms: int | None = None

    # ------------------------------------------------------------ 依据池
    def absorb_sources(self, sources: list[Source]) -> None:
        for src in sources:
            self.source_pool[src.source_id] = src

    # ------------------------------------------------------------ 材料（2-4）
    def register_material(
        self, record: MaterialRecord, source: Source | None = None
    ) -> list[MaterialRecord]:
        """登记材料并应用版本规则：同标识新版本 → 旧版 superseded 且退出依据池。

        返回被取代的旧版本记录（界面据此提示「检测到 N 个版本，本次采用最新上传」）。
        """
        outcome = self.material_registry.register(record)
        for old in outcome.superseded:
            self.source_pool.pop(old.source_id, None)      # 旧版不得再被引用
        if source is not None:
            self.source_pool[source.source_id] = source
        self._index_material_message(record)
        return outcome.superseded

    def _index_material_message(self, record: MaterialRecord) -> None:
        """把「定位索引 + 摘要」记入会话消息。**只记结构信息，不记原文**（C-12）。"""
        from .materials import locator_summary

        self.messages.append(
            {
                "role": "system",
                "kind": "material_index",
                "material_id": record.material_id,
                "source_id": record.source_id,
                "role_label": record.role_label,
                "identifier": record.identifier,
                "identifier_source": record.identifier_source,
                "identifier_missing": record.identifier_missing,
                "chars": record.chars,
                "locator_index": dict(record.locator_index or {}),
                "summary": locator_summary(record),
                "note": "材料原文仅在本会话内存中临时处理，此处只登记定位索引与摘要",
            }
        )

    def verify_material(self, source_id: str) -> MaterialRecord | None:
        source = self.source_pool.get(source_id)
        if source is None or source.superseded:
            return None
        source.user_verified = True
        return self.material_registry.mark_verified(source_id)

    def material_snapshot(self) -> list[dict[str, Any]]:
        return self.material_registry.snapshot()

    def superseded_material_ids(self) -> set[str]:
        return {
            s.source_id for s in self.source_pool.values() if s.superseded
        } | {r.source_id for r in self.material_registry.superseded()}

    # ------------------------------------------------------------ 冲突与裁决（2-4）
    def conflicting_sources(self) -> list[Source]:
        return [s for s in self.source_pool.values() if s.corroboration == "conflict"]

    def resolved_conflict_ids(self) -> set[str]:
        return {sid for sid, item in self.conflict_resolutions.items() if item.get("decision") != "exclude_source"}

    def unresolved_conflicts(self) -> list[Source]:
        return [s for s in self.conflicting_sources() if s.source_id not in self.conflict_resolutions]

    def excluded_conflict_ids(self) -> set[str]:
        return {sid for sid, item in self.conflict_resolutions.items() if item.get("decision") == "exclude_source"}

    def exclude_conflict(self, source_id: str) -> dict[str, Any]:
        """Exclude a conflicting statute; never mark it as verified or choose a version."""
        source = self.source_pool.get(source_id)
        if self.branch != "research" or source is None or source.kind != "statute" or source.corroboration != "conflict":
            raise ApiError("invalid_request", "目前仅支持在类案报告中排除存在冲突的法规依据。")
        if self.status == SESSION_ACTIVE and self.run_id and self.gate_report is None:
            raise ApiError("invalid_request", "请等待当前研究完成后处理依据冲突。")
        import json
        matrix_text = json.dumps(self.matrix, ensure_ascii=False)
        if source_id in matrix_text or (source.identifier and source.identifier in matrix_text):
            raise ApiError("invalid_request", "该依据出现在对比表中，需要重新生成对比内容，不能直接排除后导出。")
        record = {"source_id": source_id, "identifier": source.identifier, "decision": "exclude_source", "at": _now()}
        self.conflict_resolutions[source_id] = record
        source.manual_override = False
        note = f"已不采用存在冲突的依据：{source.identifier or source_id}；依赖该依据的整条结论不作为报告结论输出，未判定任一版本正确。"
        if not any(g.detail == note for g in self.gaps):
            self.add_gap("evidence", note)
        return record

    def resolve_conflict(self, source_id: str, decision: str) -> dict[str, Any]:
        """人工裁决「以我上传的材料为准」。只记编号、时间与决策，**不记原文**。"""
        source = self.source_pool.get(source_id)
        if source is None or source.corroboration != "conflict":
            raise ApiError("not_conflict", "该来源不是冲突来源，无需裁决。")
        record = {
            "source_id": source_id,
            "identifier": source.identifier,
            "decision": decision,
            "at": _now(),
        }
        self.conflict_resolutions[source_id] = record
        source.manual_override = True
        self.messages.append(
            {
                "role": "system",
                "kind": "conflict_resolution",
                "source_id": source_id,
                "identifier": source.identifier,
                "decision": decision,
                "note": "人工裁决：依据用户上传材料，未经法宝印证；冲突已由人工确认",
            }
        )
        return record

    def conflict_snapshot(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for source in self.conflicting_sources():
            record = self.conflict_resolutions.get(source.source_id)
            out.append(
                {
                    "source_id": source.source_id,
                    "identifier": source.identifier,
                    "origin": source.origin,
                    "is_user_material": source.is_user_material,
                    "resolved": record is not None,
                    "decision": (record or {}).get("decision"),
                    "resolved_at": (record or {}).get("at"),
                }
            )
        return out

    def add_gap(self, kind: str, detail: str) -> None:
        self.gaps.append(Gap(kind=kind, detail=detail))

    def touch(self) -> None:
        self.last_active = time.time()

    # ------------------------------------------------------------ 降级与重试
    def note_degradation(self, record: DegradationRecord) -> None:
        self.degradations.append(record)

    def has_interface_error(self) -> bool:
        """是否发生过「接口失败」。用于保证失败不被说成「没有相关案例」。"""
        return any(d.status is Status.INTERFACE_ERROR for d in self.degradations)

    def degradation_summary(self) -> dict[str, Any]:
        by_status: dict[str, int] = {}
        for record in self.degradations:
            key = record.status.value
            by_status[key] = by_status.get(key, 0) + 1
        return {
            "total": len(self.degradations),
            "by_status": by_status,
            "retryable": sum(1 for d in self.degradations if d.retryable),
        }

    def mark_failed(self, step: str, reason: str) -> None:
        self.failed_step = step or "检索"
        self.failed_reason = reason

    def clear_failed(self) -> None:
        self.failed_step = None
        self.failed_reason = None

    @property
    def can_retry(self) -> bool:
        return self.failed_step is not None and bool(self.run_id)

    # ------------------------------------------------------------ 导出前置条件
    def export_blockers(self) -> list[str]:
        """导出前的确定性判定（PRD 4.1.4 / 4.2.3 / 4.3.2）。宁可拒绝，不放宽。"""
        if self.branch == "consult":
            return self._consult_export_blockers()
        if self.branch == "contract":
            return self._contract_export_blockers()
        reasons: list[str] = []
        if not self.matrix:
            reasons.append("尚未生成对比矩阵（样本可能未确认）")
        unresolved = self.unresolved_conflicts()
        if unresolved:
            names = "、".join(s.identifier or s.source_id for s in unresolved[:3])
            reasons.append(
                f"存在 {len(unresolved)} 处来源内容冲突：{names}，"
                "请核对来源，或不采用该依据及依赖它的结论后重新核验。"
            )
        if self.excluded_conflict_ids() and not (self.structured_output or {}).get("_passed_conclusions"):
            reasons.append("排除冲突依据后没有通过核验的结论，请补充可靠依据后重新研究。")
        residues = self.sample.residues(self.cited_ids)
        if residues:
            reasons.append(
                "检测到已排除案例仍被引用：" + "、".join(residues) + "，无法导出。"
            )
        if self.gate_report is None:
            reasons.append("引用尚未经过门禁核验，无法导出。")
        else:
            rejected = [r for r in self.gate_report.rejected if r.rule != "R0"]
            if rejected:
                rules = sorted({r.rule for r in rejected})
                why = "；".join(sorted({r.reason for r in rejected})[:3])
                reasons.append(
                    f"存在 {len(rejected)} 条未通过核验的引用（规则 {'/'.join(rules)}）：{why}，无法导出。"
                )
        if self.expression_hits:
            categories = sorted({h["category"] for h in self.expression_hits})
            reasons.append("输出含越界表述（" + "、".join(categories) + "），请修改后导出。")
        return reasons

    def export_ready(self) -> bool:
        return not self.export_blockers()

    def _consult_export_blockers(self) -> list[str]:
        """2-5：咨询导出「咨询备忘」的判定。口径与研究报告一致（引用/冲突/边界）。

        区别只在于：咨询没有矩阵与样本锁，取而代之的是“必须已经给出解答”。
        """
        reasons: list[str] = []
        if not (self.consult or {}).get("answer"):
            reasons.append("尚未生成咨询解答，无法导出。")
        return reasons + self._shared_export_blockers()

    def _contract_export_blockers(self) -> list[str]:
        """2-6：合同审查报告导出的判定。立场未确认 / 未出风险结果都不得导出。"""
        reasons: list[str] = []
        state = self.contract or {}
        if not state.get("stance"):
            reasons.append("尚未确认审查立场，无法导出。")
        if state.get("status") not in {"reviewed", "insufficient"}:
            reasons.append("尚未完成风险识别，无法导出。")
        if not state.get("risks") and state.get("status") != "insufficient":
            reasons.append("未产出任何风险条目，无法导出。")
        return reasons + self._shared_export_blockers()

    def _shared_export_blockers(self) -> list[str]:
        """咨询与合同审查共用的导出前置条件（引用 / 冲突 / 边界）。"""
        reasons: list[str] = []
        unresolved = self.unresolved_conflicts()
        if unresolved:
            names = "、".join(s.identifier or s.source_id for s in unresolved[:3])
            user_side = [s for s in unresolved if s.is_user_material]
            if user_side:
                reasons.append(
                    f"存在 {len(unresolved)} 处来源冲突（用户材料与法宝对同一标识的原文不一致）：{names}，"
                    "需人工确认「以我上传的材料为准」后才能导出。"
                )
            else:
                # 2-6 实测：本地依据库 vs 法宝的冲突不是“用户材料”问题，文案不得写错
                reasons.append(
                    f"存在 {len(unresolved)} 处来源冲突（本地依据库与法宝对同一标识的原文不一致）：{names}，"
                    "需人工核对后再导出。"
                )
        if self.gate_report is None:
            reasons.append("引用尚未经过门禁核验，无法导出。")
        else:
            rejected = [r for r in self.gate_report.rejected if r.rule != "R0"]
            if rejected:
                rules = sorted({r.rule for r in rejected})
                why = "；".join(sorted({r.reason for r in rejected})[:3])
                reasons.append(
                    f"存在 {len(rejected)} 条未通过核验的引用（规则 {'/'.join(rules)}）：{why}，无法导出。"
                )
        if self.expression_hits:
            categories = sorted({h["category"] for h in self.expression_hits})
            reasons.append("输出含越界表述（" + "、".join(categories) + "），请修改后导出。")
        return reasons

    # ------------------------------------------------------------ 快照（界面用）
    def snapshot(self) -> dict[str, Any]:
        return {
            "session_id": self.id,
            "branch": self.branch,
            "topic": self.topic,
            "conditions": self.conditions,
            "phase": self.phase,
            "status": self.status,
            "run_id": self.run_id,
            "queued": self.queued,
            "first_response_ms": self.first_response_ms,
            "total_ms": self.total_ms,
            "candidates": self.candidates,
            "sample": {
                "locked": self.sample.locked,
                "confirmed": self.sample.confirmed,
                "excluded": self.sample.excluded,
            },
            "cases": self.cases,
            "matrix": self.matrix,
            "distribution": self.distribution,
            "synthesis": self.synthesis,
            "limitations": self.limitations,
            "gate_report": self.gate_report.event() if self.gate_report else None,
            "expression_hits": self.expression_hits,
            "gaps": [g.__dict__ for g in self.gaps],
            "sources": [s.brief() for s in self.source_pool.values()],
            "materials": self.material_snapshot(),
            "conflicts": self.conflict_snapshot(),
            "supplement": {
                "rounds_used": self.supplement_rounds,
                "max_rounds": self.max_supplement_rounds,
                "material_primary": self.material_primary,
                "remaining": max(0, self.max_supplement_rounds - self.supplement_rounds),
            },
            "export": {
                "ready": self.export_ready(),
                "blockers": self.export_blockers(),
            },
            "consult": dict(self.consult),
            "contract": dict(self.contract),
            "degradations": [d.event() for d in self.degradations],
            "degradation_summary": self.degradation_summary(),
            "failed_step": self.failed_step,
            "failed_reason": self.failed_reason,
            "retry_count": self.retry_count,
            "can_retry": self.can_retry,
        }


class SessionStore:
    def __init__(self, max_concurrent: int = 4, ttl_seconds: int = 1800) -> None:
        self._sessions: dict[str, Session] = {}
        self.max_concurrent = max_concurrent
        self.ttl_seconds = ttl_seconds

    def sweep(self) -> int:
        """释放空闲超时的会话，避免内存无限增长（PRD 8.2 单进程、会话内存态）。"""
        now = time.time()
        dead = [
            sid
            for sid, s in self._sessions.items()
            if s.status != SESSION_CLOSED and now - s.last_active > self.ttl_seconds
        ]
        for sid in dead:
            self._release(self._sessions[sid])
            del self._sessions[sid]
        return len(dead)

    @staticmethod
    def _release(session: Session) -> None:
        """会话结束：释放材料原文与登记表（2-4：关闭后材料不可再取）。"""
        session.status = SESSION_CLOSED
        session.materials.clear()
        session.material_registry.release()

    def create(self, branch: str = "research", purpose: str | None = None) -> Session:
        self.sweep()
        # 修复（2-4 代操作验收实测缺陷 4）：名额已满时，先回收「最旧的、没在跑任务的」空闲会话。
        # 原来只要未关闭就占名额，而空闲会话要 30 分钟才释放 —— 用户多开几次页面就会把自己锁在门外
        # （表现为「无法开始研究：同时在跑的研究已达上限」，而实际根本没有研究在跑）。
        if self.active_count() >= self.max_concurrent:
            self._evict_oldest_idle()
        session = Session(branch=branch, purpose=purpose)
        if self.active_count() >= self.max_concurrent:
            session.queued = True
        self._sessions[session.id] = session
        return session

    def _is_running(self, session: Session) -> bool:
        """会话是否真的在跑任务（有未完成的任务，或正等人确认）。忙的会话一律不得回收。"""
        if session.status == SESSION_AWAITING:
            return True
        return any(
            not task.done() for task in getattr(session, "run_tasks", []) if task is not None
        )

    def _evict_oldest_idle(self) -> str | None:
        """回收最旧的一个空闲会话；一个可回收的都没有时返回 None（名额确实被真实占用）。"""
        idle = [
            (session.last_active, sid)
            for sid, session in self._sessions.items()
            if session.status != SESSION_CLOSED and not session.queued and not self._is_running(session)
        ]
        if not idle:
            return None
        idle.sort()
        _, sid = idle[0]
        self._release(self._sessions[sid])
        del self._sessions[sid]
        return sid

    def active_count(self) -> int:
        return sum(1 for s in self._sessions.values() if s.status != SESSION_CLOSED)

    def get(self, session_id: str) -> Session:
        session = self._sessions.get(session_id)
        if session is None or session.status == SESSION_CLOSED:
            raise ApiError("session_not_found", "会话不存在或已结束，请重新开始。")
        session.touch()
        return session

    def close(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session:
            self._release(session)

    def all_sessions(self) -> list[Session]:
        return list(self._sessions.values())


STORE = SessionStore()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())
