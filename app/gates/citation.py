"""⭐ 引用门禁（PRD 4.1.2 六条规则）。

形态：PostToolUse 钩子（逐条剔除）+ export_docx 前置条件（硬拦）。
原则：**可以讨论，不能交付** —— 门禁不打断对话，但未通过核验的内容进不了导出的文档。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..models import (
    STATUS_TEXT,
    Citation,
    Claim,
    GateReport,
    Rejection,
    Source,
    Status,
    normalize_text,
)

#: 效力状态中可被引用的白名单（R6）
_EFFECTIVE_OK = {"现行有效", "有效", "尚未生效", "不适用（裁判文书）"}


def effective_status_ok(text: str | None) -> bool:
    """该效力状态是否可被引用（R6 的判定口径）。

    提示词层应与门禁用同一口径——不能让提示词说“可引用”、门禁却拒掉（2-5）。
    """
    status = (text or "").strip()
    if not status or status.lower() == "unknown":
        return False
    return status in _EFFECTIVE_OK


@dataclass
class GatePolicy:
    allow_synthetic: bool = False
    require_effective_status: bool = True
    require_quote_match: bool = True
    require_user_material_review: bool = True
    min_quote_length: int = 8


@dataclass
class ScreenResult:
    report: GateReport = field(default_factory=GateReport)
    passed: list[Claim] = field(default_factory=list)
    degraded: list[Claim] = field(default_factory=list)


class CitationGate:
    """模型无法禁用它，也无法让未核验引用逃过导出前置条件。"""

    def __init__(self, policy: GatePolicy) -> None:
        self.policy = policy

    # ------------------------------------------------------------ 单条核验
    def check(
        self,
        citation: Citation,
        pool: dict[str, Source],
        applicable_at: str | None = None,
        resolved_conflicts: set[str] | None = None,
    ) -> tuple[bool, str, str]:
        """返回 (是否通过, 规则编号, 失败原因)。"""
        source = pool.get(citation.source_id or "")

        # R1 来源必须存在且状态为 OK
        if source is None:
            return False, "R1", "引用指向的来源不在依据池中，无法核验"
        # R1b（2-4）：已被新版本取代的材料不得引用
        if source.superseded:
            return False, "R1", "该材料已被同标识的新版本取代（本次采用最新版），不得引用"
        if source.status is not Status.OK:
            return False, "R1", f"来源状态为「{STATUS_TEXT[source.status]}」，不是可引用依据"

        # R2 不得引用夹具数据（C-13）
        if source.synthetic and not self.policy.allow_synthetic:
            return False, "R2", "来源为演示夹具数据，不是真实检索结果"

        # R3 用户材料须经人工核验
        if source.is_user_material and self.policy.require_user_material_review:
            if not source.user_verified:
                return False, "R3", "用户上传材料未经人工核验，不得作为权威依据（可作线索）"

        # R4 引用标识与来源一致（⚠ 必须先判空："" in s 恒为 True）
        identifier = (citation.identifier or "").strip()
        if not identifier:
            return False, "R4", "引用标识为空，一律拒绝"
        source_identifier = (source.identifier or "").strip()
        if not source_identifier or normalize_text(identifier) != normalize_text(source_identifier):
            return False, "R4", "引用标识与来源不一致"

        # R5 引用原文与权威原文一致（规范化后子串包含）
        if self.policy.require_quote_match:
            quote = (citation.quote or "").strip()
            if len(quote) < self.policy.min_quote_length:
                return False, "R5", f"引用原文过短（少于 {self.policy.min_quote_length} 字），不予核验"
            if not (source.quote or "").strip():
                return False, "R5", "来源原文已释放（会话材料临时处理），无法逐字核验，已拒绝"
            if normalize_text(quote) not in normalize_text(source.quote):
                return False, "R5", "引用原文与权威原文不一致（疑似改写或编造）"

        # R6 效力状态与适用时点
        if self.policy.require_effective_status:
            status_text = (source.effective_status or "").strip()
            if not status_text or status_text.lower() == "unknown":
                return False, "R6", "效力状态未核对（unknown），不得引用"
            if status_text not in _EFFECTIVE_OK:
                return False, "R6", f"效力状态为「{status_text}」，不得作为现行依据引用"
            if applicable_at:
                if source.applicable_from and applicable_at < source.applicable_from:
                    return False, "R6", (
                        f"适用时点 {applicable_at} 早于该规定生效日 {source.applicable_from}"
                    )
                if source.applicable_to and applicable_at > source.applicable_to:
                    return False, "R6", (
                        f"适用时点 {applicable_at} 晚于该规定失效日 {source.applicable_to}"
                    )

        # R7 来源冲突（2-3 / 2-4）：法宝与本地依据库、或用户材料与法宝，对同一引用原文不一致
        #      → 不许自行选边；只有人工裁决「以我上传的材料为准」后才放行（放行必留痕）
        if source.corroboration == "conflict" and source.source_id not in (resolved_conflicts or set()):
            return False, "R7", "来源冲突：同一案号的原文在不同来源间不一致，需人工确认后才能使用"

        return True, "", ""

    # ------------------------------------------------------------ 批量核验
    def screen(
        self,
        claims: list[Claim],
        pool: dict[str, Source],
        applicable_at: str | None = None,
        resolved_conflicts: set[str] | None = None,
    ) -> ScreenResult:
        result = ScreenResult()
        report = result.report
        report.demo_mode = self.policy.allow_synthetic

        total = len(claims)
        clean = 0
        for claim in claims:
            if not claim.citations:
                report.rejected.append(
                    Rejection(rule="R0", reason="结论未提供任何引用，不作为结论输出")
                )
                report.gaps.append("有结论未提供引用，已降级为「依据缺口」")
                result.degraded.append(claim)
                continue

            ok_all = True
            for citation in claim.citations:
                passed, rule, reason = self.check(
                    citation, pool, applicable_at, resolved_conflicts=resolved_conflicts
                )
                if passed:
                    report.accepted += 1
                else:
                    ok_all = False
                    report.rejected.append(
                        Rejection(
                            rule=rule,
                            reason=reason,
                            identifier=citation.identifier,
                            quote=(citation.quote or "")[:80],
                        )
                    )

            if ok_all:
                clean += 1
                result.passed.append(claim)
            else:
                report.gaps.append(f"结论「{claim.text[:40]}…」存在未通过核验的引用")
                result.degraded.append(claim)

        report.coverage = (clean / total) if total else 1.0
        return result
