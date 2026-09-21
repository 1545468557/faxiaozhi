"""工具注册表与分派（PRD 3.2.1 / 3.2.4）。

红线：任何工具**不得抛异常到循环**；权限与前置条件在分派内完成；
分派结束后触发 PostToolUse 钩子（门禁 / 依据池 / 埋点）。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..config import get_config
from ..hooks import trigger_hooks
from ..models import Status, ToolResult
from ..permissions import check_permission
from ..schemas import validate_tool_args
from ..session import Session
from . import document, legal

LOG = logging.getLogger("faxiaozhi.tools")


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[..., ToolResult]
    permission: str = "allow"                 # allow | ask | conditional | deny
    side_effect: str = "none"                 # none | read_external | write_local
    layer: str = "⑤"
    returns_sources: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


BUILTIN_TOOLS: list[Tool] = [
    Tool(
        name="search_statutes",
        description=(
            "检索法律法规条文。返回原文、条款号、效力状态与适用时点。"
            "所有法条引用只能来自本工具或 verify_citation。"
        ),
        input_schema=legal.SEARCH_STATUTES_SCHEMA,
        handler=legal.search_statutes,
        permission="allow",
        side_effect="read_external",
        returns_sources=True,
    ),
    Tool(
        name="search_cases",
        description=(
            "检索裁判文书。返回案号、法院、审级、地域、裁判日期、原文与来源。"
            "用于构建候选案例池。"
        ),
        input_schema=legal.SEARCH_CASES_SCHEMA,
        handler=legal.search_cases,
        permission="allow",
        side_effect="read_external",
        returns_sources=True,
    ),
    Tool(
        name="verify_citation",
        description=(
            "核验一条拟引用：标识是否一致、原文是否一致、效力状态与适用时点是否匹配。"
            "核验不通过的引用不得进入输出。"
        ),
        input_schema=legal.VERIFY_CITATION_SCHEMA,
        handler=legal.verify_citation,
        permission="allow",
    ),
    Tool(
        name="parse_document",
        description="解析用户上传的材料（DOCX/PDF/TXT），产出定位索引与内容。内容仅会话内临时处理。",
        input_schema=document.PARSE_DOCUMENT_SCHEMA,
        handler=document.parse_document,
        permission="ask",
        returns_sources=True,
    ),
    Tool(
        name="render_matrix",
        description="生成跨案对比矩阵。只包含用户已确认的样本；样本未确认时会失败。",
        input_schema=legal.RENDER_MATRIX_SCHEMA,
        handler=legal.render_matrix,
        permission="conditional",
        layer="②",
    ),
    Tool(
        name="export_docx",
        description="导出研究备忘录（Word）。存在未通过核验的引用 / 样本残留 / 越界表述时会被拒绝。",
        input_schema=legal.EXPORT_DOCX_SCHEMA,
        handler=legal.export_docx,
        permission="conditional",
        side_effect="write_local",
    ),
]

REGISTRY: dict[str, Tool] = {tool.name: tool for tool in BUILTIN_TOOLS}


def get_tool(name: str) -> Tool | None:
    return REGISTRY.get(name)


# ---------------------------------------------------------------- 前置条件
def check_preconditions(session: Session, tool: Tool, args: dict[str, Any]) -> str | None:
    if tool.name == "render_matrix" and not session.sample.locked:
        return "样本尚未确认，无法生成对比矩阵。"
    if tool.name == "export_docx":
        blockers = session.export_blockers()
        if blockers:
            return "；".join(blockers)
    if tool.name == "verify_citation":
        if str(args.get("source_id", "")) not in session.source_pool:
            return "引用指向的来源不在依据池中，无法核验。"
    return None


# ---------------------------------------------------------------- 分派
def dispatch_tool(session: Session, tool_call: Any) -> ToolResult:
    name = getattr(tool_call, "name", "")
    args = dict(getattr(tool_call, "arguments", {}) or {})
    tool = REGISTRY.get(name)
    if tool is None:
        return ToolResult(
            tool=name or "unknown",
            status=Status.PARSE_ERROR,
            detail=f"未知工具：{name}",
            error_kind="unknown_tool",
        )

    try:
        validate_tool_args(tool.input_schema, args)
    except ValueError as exc:
        return ToolResult(
            tool=name,
            status=Status.PARSE_ERROR,
            detail=f"参数不合法：{exc}",
            error_kind="schema",
        )

    reason = check_preconditions(session, tool, args)
    if reason:
        return ToolResult(
            tool=name,
            status=Status.INSUFFICIENT,
            detail=reason,
            error_kind="precondition",
            meta={"precondition": True},
        )

    allowed, message = check_permission(session, tool, args)
    if not allowed:
        return ToolResult(
            tool=name,
            status=Status.INSUFFICIENT,
            detail=message,
            error_kind="permission",
            meta={"denied": True},
        )

    started = time.perf_counter()
    try:
        result = tool.handler(session, **args)
    except Exception as exc:                       # 兜底：绝不冒泡到循环
        LOG.exception("工具执行异常：%s", name)
        result = ToolResult(
            tool=name,
            status=Status.INTERFACE_ERROR,
            detail=f"工具执行异常：{type(exc).__name__}",
            error_kind="tool",
        )
    result.elapsed_ms = int((time.perf_counter() - started) * 1000)
    result.meta.setdefault("max_chars", int(get_config().get("limits.max_tool_result_chars", 4000)))

    if tool.returns_sources and result.sources:
        session.absorb_sources(result.sources)

    trigger_hooks("PostToolUse", session, tool_call, result)
    return result


__all__ = ["BUILTIN_TOOLS", "REGISTRY", "Tool", "dispatch_tool", "get_tool"]
