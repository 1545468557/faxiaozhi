"""单一 Agent 循环（PRD 3.1，s01 + s15）：所有机制都挂在这一处。"""

from __future__ import annotations

import json
import logging
from typing import Any

from .config import get_config
from .errors import ApiError
from .hooks import register_hook, trigger_hooks
from .models import Status, ToolResult
from .schemas import validate
from .tools import REGISTRY, dispatch_tool

LOG = logging.getLogger("faxiaozhi.loop")


async def agent_loop(
    ctx: Any,
    *,
    label: str,
    prompt: str,
    schema: dict[str, Any] | None = None,
    tools: list[Any] | None = None,
    temperature: float | None = None,
) -> Any:
    """循环只有一处；权限、门禁、埋点、工作流全部挂在它上面。"""
    session = ctx.session
    cfg = get_config()
    max_steps = int(cfg.get("limits.max_agent_steps", 8))
    temperatures = cfg.get("model.temperature", {}) or {}
    if temperature is None:
        temperature = float(temperatures.get(_temp_key(label), 0.2))

    max_schema_retries = int(cfg.get("limits.max_schema_retries", 1))
    schema_attempts = 0
    tool_schemas = [_tool_schema(t) for t in (tools or [])]
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": ctx.system_prompt()},
        {"role": "user", "content": prompt},
    ]
    if schema is not None:
        # JSON mode 只保证 JSON 语法；真实 provider 不会自动把 schema 传给模型。
        # 首次生成与修正使用同一份校验定义，避免提示词与代码的字段约束脱节。
        messages[0]["content"] += (
            "\n\n【最终输出格式】最终回复只包含一个 JSON 值，不加 Markdown 围栏。"
            "必须遵循以下 JSON Schema 的字段、类型和 required；"
            "对象数组不得写成字符串数组，不得为满足格式编造事实或引用：\n"
            + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
        )

    for _ in range(max_steps):
        ctx.count_step()
        response = await ctx.provider.chat(
            messages=messages,
            tools=tool_schemas or None,
            schema=schema,
            temperature=temperature,
            meta={"label": label, "phase": ctx.phase_name},
        )
        ctx.record_usage(response.usage)

        if response.tool_calls:
            messages.append(_assistant_tool_message(response))
            for call in response.tool_calls:
                result = dispatch_tool(session, call)
                ctx.emit("tool", result.event())
                ctx.on_tool_result(call.name, result)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": call.name,
                        "content": result.to_model_json(),
                    }
                )
            continue

        if schema is not None:
            try:
                if response.finish_reason == "length":
                    raise ApiError("schema_invalid", "输出超过长度限制，响应被截断；请精简文字并保持完整 JSON")
                value = json.loads(response.text)
                value = validate(schema, value)      # 会把「形态归一」写回，必须用返回值
                return value
            except (json.JSONDecodeError, ApiError) as exc:
                detail = exc.message if isinstance(exc, ApiError) else "输出不是合法 JSON"
                if schema_attempts < max_schema_retries:
                    schema_attempts += 1
                    ctx.session.add_gap("schema", f"{label} 结构校验未通过，已重试一次：{detail}")
                    messages.append({"role": "assistant", "content": response.text})
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                f"上一次输出未通过结构校验：{detail}。"
                                "请只输出修正后的 JSON（json 格式），不要任何解释文字。"
                            ),
                        }
                    )
                    continue
                raise ApiError(
                    "schema_invalid", f"模型输出未通过结构校验：{detail}（已重试 {schema_attempts} 次）"
                ) from exc
        return response.text

    raise ApiError("workflow_step_limit")


def _temp_key(label: str) -> str:
    if label.startswith("extract"):
        return "extract"
    if label.startswith("synthesize"):
        return "synthesize"
    return "retrieve"


def _tool_schema(tool: Any) -> dict[str, Any]:
    if isinstance(tool, str):
        registered = REGISTRY.get(tool)
        if registered is None:
            raise ApiError("internal_error", f"工具未注册：{tool}")
        return registered.schema()
    return tool


def _assistant_tool_message(response: Any) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": response.text or None,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments, ensure_ascii=False)},
            }
            for call in response.tool_calls
        ],
    }


# ------------------------------------------------------------------ 默认钩子


def install_default_hooks() -> None:
    """PostToolUse：依据池 / 状态台账 / 埋点 / 轨迹（**全部为仅观察类**）。

    引用门禁与表达边界是「降级而非阻断」，由 gates.runner.apply_gates 执行，
    真正的硬拦在 export_docx 的前置条件里（PRD 4.1.3）。
    """
    from .hooks import HOOKS
    from .observability import get_observability

    if HOOKS["PostToolUse"]:          # 幂等：重复安装不产生重复 handler
        return

    def source_pool_update(session: Any, tool_call: Any, output: ToolResult) -> None:
        if output.sources:
            get_observability().register_material(
                output.sources[0].text_fingerprint if output.sources[0].is_user_material else ""
            )

    def status_ledger(session: Any, tool_call: Any, output: ToolResult) -> None:
        obs = get_observability()
        obs.metric(
            event="tool_call",
            session_id=session.id,
            run_id=session.run_id,
            phase=session.phase,
            tool=output.tool,
            status=output.status.value,
            attempts=output.attempts,
            elapsed_ms=output.elapsed_ms,
            sources_returned=len(output.sources),
            error_kind=output.error_kind,
        )

    def metrics(session: Any, tool_call: Any, output: ToolResult) -> None:
        if output.status is not Status.OK:
            session.add_gap("evidence", f"{output.tool}：{output.detail}")

    def trajectory(session: Any, tool_call: Any, output: ToolResult) -> None:
        get_observability().trajectory(
            session_id=session.id,
            run_id=session.run_id,
            phase=session.phase,
            tool=output.tool,
            ok=output.status is Status.OK,
            kind=output.error_kind,
        )

    register_hook("PostToolUse", source_pool_update, blocking=False)
    register_hook("PostToolUse", status_ledger, blocking=False)
    register_hook("PostToolUse", metrics, blocking=False)
    register_hook("PostToolUse", trajectory, blocking=False)


def user_prompt_submit(session: Any, text: str) -> None:
    """输入审计（仅观察）：标记不当请求。"""
    import re

    forbidden = re.compile(r"(伪造证据|保证胜诉|规避法律|打擦边球)")
    if forbidden.search(text or ""):
        session.add_gap("audit", "输入中检测到不当请求标记，已记录（仅观察，不阻断）。")


__all__ = ["agent_loop", "install_default_hooks", "trigger_hooks", "user_prompt_submit"]
