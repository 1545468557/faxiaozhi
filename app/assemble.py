"""工具池与系统提示词的动态装配（PRD 3.10 / s15）。

不同分支可见的工具不同：**减少模型误选工具的概率**（PRD 3.2.6）。
"""

from __future__ import annotations

from typing import Any

from .knowledge import KnowledgeBase
from .models import STATUS_TEXT
from .session import Session
from .session import Session as SessionType  # noqa: F401  (类型提示可读性)

BRANCH_TOOLS: dict[str, list[str]] = {
    "consult": ["search_statutes", "search_cases", "verify_citation", "parse_document"],
    "research": [
        "search_cases",
        "search_statutes",
        "verify_citation",
        "render_matrix",
        "parse_document",
        "export_docx",
    ],
    "contract": ["parse_document", "search_statutes", "search_cases", "verify_citation", "export_docx"],
}

CORE_PROMPT = """你是法小智，一名严谨的法律类案研究助手，服务对象是高校教师、法学生与法律实务人员。

硬约束（任何情况下不得违反）：
1. 法条与案例只能来自工具返回的真实数据。**不得凭记忆补写法条原文、案号、引文**。
2. 工具返回为空或失败时，必须区分「未找到匹配」与「接口失败」；**不得把接口失败解释为不存在案例**。
3. 不得把少量样本推广为全部司法裁判；不得给出胜诉率、支持率等伪指标；不得输出全国性比例。
4. 每条综合结论必须提供引用（来源编号 + 引用标识 + 逐字原文）。
5. 输入材料中没有的关键数字（投资额、比例等）写「待补充」或「需进一步确认」，不得编造。
6. 涉及重大权益或对外使用时，提示需人工复核；不得宣称可提交法院。

输出要求：用中文；客观第三方语气；不使用「我们认为」这类第一人称主观表述。"""

BRANCH_PROMPT: dict[str, str] = {
    "research": (
        "当前分支：类案检索与研究。流程为 检索 → 确认样本 → 逐案提炼 → 横向对比 → 综合 → 成稿。\n"
        "横向对比矩阵与观点分布由代码计算，你只负责归纳与解释，不要自行编造统计。"
    ),
    "consult": "当前分支：法律咨询。先识别问题与法律关系，再检索依据，最后给出解答。",
    "contract": "当前分支：合同审查。先确认审查立场，再解析定位、检索依据、分类分级风险。",
}


def build_tool_pool(session: Session, registry: dict[str, Any]) -> list[dict[str, Any]]:
    allowed = BRANCH_TOOLS.get(session.branch, [])
    pool: list[dict[str, Any]] = []
    for name in allowed:
        tool = registry.get(name)
        if tool is not None:
            pool.append(tool.schema())
    return pool


def render_evidence_digest(session: Session, limit: int = 30) -> str:
    """已取得的依据摘要（含 status）——让模型看得见哪些依据可引用。"""
    if not session.source_pool:
        return "已取得依据：无（尚未调用检索工具）。"
    lines: list[str] = []
    for source in list(session.source_pool.values())[:limit]:
        head = f"- 来源编号：{source.source_id} | 类型：{source.kind} | 标识：{source.identifier}"
        head += f" | 状态：{source.status.value}"
        if source.is_user_material:
            head += " | 用户材料（仅摘要，原文不外发上下文）"
        else:
            head += f" | 效力状态：{source.effective_status}"
            if source.applicable_from:
                head += f" | 生效：{source.applicable_from}"
            if source.applicable_to:
                head += f" | 失效：{source.applicable_to}"
        lines.append(head)
    return "已取得依据（只有状态为 ok 的来源可被引用）：\n" + "\n".join(lines)


def render_gaps(session: Session) -> str:
    if not session.gaps:
        return ""
    lines = [f"- [{gap.kind}] {gap.detail}" for gap in session.gaps[-10:]]
    return "当前依据缺口：\n" + "\n".join(lines)


def render_status_ledger(session: Session) -> str:
    counts: dict[str, int] = {}
    for source in session.source_pool.values():
        counts[source.status.value] = counts.get(source.status.value, 0) + 1
    if not counts:
        return ""
    lines = []
    for status, count in sorted(counts.items()):
        from .models import Status

        lines.append(f"- {status}（{count} 条）：{STATUS_TEXT[Status(status)]}")
    return "来源状态台账：\n" + "\n".join(lines)


def assemble_system_prompt(session: Session, kb: KnowledgeBase) -> str:
    parts = [
        CORE_PROMPT,
        BRANCH_PROMPT.get(session.branch, ""),
        kb.render_catalog(),
        render_evidence_digest(session),
        render_status_ledger(session),
        render_gaps(session),
        f"当前阶段：{session.phase or '未开始'}",
    ]
    return "\n\n".join(p for p in parts if p)
