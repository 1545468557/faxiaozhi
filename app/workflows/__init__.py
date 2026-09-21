"""workflow 注册表与启动入口。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..config import Config
from ..errors import ApiError
from ..knowledge import KnowledgeBase
from ..session import Session
from .consult import consult_workflow
from .contract import contract_workflow
from .research import WORKFLOWS as _RESEARCH_WORKFLOWS
from .research import research_workflow
from .runtime import RunContext

LOG = logging.getLogger("faxiaozhi.workflow")

#: 2-5/2-6：三个分支共用一套 Harness，只差 workflow 名称（PRD 3.5.1）
WORKFLOWS: dict[str, Any] = {
    **_RESEARCH_WORKFLOWS,
    "legal-consult": consult_workflow,
    "legal-contract": contract_workflow,
}

WORKFLOW_FOR_BRANCH: dict[str, str] = {
    "research": "legal-research",
    "consult": "legal-consult",
    "contract": "legal-contract",
}

__all__ = [
    "WORKFLOWS",
    "WORKFLOW_FOR_BRANCH",
    "RunContext",
    "consult_workflow",
    "contract_workflow",
    "launch",
    "research_workflow",
]


async def launch(
    session: Session,
    cfg: Config,
    kb: KnowledgeBase,
    args: dict[str, Any],
    workflow: str = "legal-research",
    run_id: str | None = None,
    scenario: str = "clean",
) -> dict[str, Any]:
    """执行一次 workflow；异常一律转成 `error` 事件收尾（不静默断流）。"""
    ctx = RunContext(
        session=session, cfg=cfg, provider=None, kb=kb,
        workflow=workflow, args=args, run_id=run_id,
    )
    session.run_id = ctx.run_id
    session.scenario = scenario
    try:
        ctx.acquire_lock()
        ctx.load()
        # 2-4：用户材料不落盘（C-12），重启后提炼/引用无法恢复——
        # 宁可明确拒绝，也不用“脱敏占位”冒充真实结果（底线 10）。
        # 2-5：咨询的问题原文同样不落盘，重启后同样不可恢复。
        consult_context_present = (
            session.branch == "consult"
            and session.consult.get("run_id") == ctx.run_id
            and bool(session.consult.get("facts"))
        )
        if ctx.material_redacted and not session.materials and not consult_context_present:
            if session.branch == "consult":
                raise ApiError("consult_not_recoverable")
            if session.branch == "contract":
                raise ApiError("contract_not_recoverable")
            raise ApiError(
                "material_not_recoverable",
                "本次研究使用了你上传的材料；材料原文不落盘，服务重启后无法继续。"
                "请重新上传材料后重新发起研究。",
            )
    except ApiError as exc:
        ctx.emit("error", exc.body())
        ctx.release_lock()
        return {"status": "failed", "error": exc.code}

    handler = WORKFLOWS.get(workflow)
    failed_step = session.failed_step
    session.clear_failed()                                    # 新一轮运行覆盖旧的失败标记
    max_retries = int(cfg.get("limits.max_retries_per_run", 3))
    if session.retry_count:
        ctx.emit(
            "retry",
            {
                "step": failed_step or "",
                "attempt": session.retry_count,
                "max_attempts": max_retries,
            },
        )
    try:
        output = await handler(ctx, args)                      # type: ignore[misc]
        ctx.finish("completed", output)
        return output
    except ApiError as exc:
        LOG.warning("workflow 失败：%s｜%s", exc.code, exc.message[:200])
        ctx.invalidate_current_phase()                        # 2-2：作废失败步骤存档，重试才真的重跑
        session.mark_failed(ctx.phase_name, exc.message)      # 2-2：记下失败步骤，供 /retry 续跑
        ctx.finish("failed", {"error": exc.code})
        ctx.emit("error", exc.body())
        return {"status": "failed", "error": exc.code}
    except asyncio.CancelledError:
        raise
    except Exception:                                         # 兜底：不泄露堆栈
        LOG.exception("workflow 未预期异常")
        ctx.invalidate_current_phase()
        session.mark_failed(ctx.phase_name, "服务内部错误，已完成部分已保存，可重试。")
        ctx.finish("failed", {})
        ctx.emit("error", ApiError("internal_error").body())
        return {"status": "failed", "error": "internal_error"}
    finally:
        ctx.release_lock()
