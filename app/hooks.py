"""钩子注册表与分发（PRD 3.4 / s04）。

约束：**仅观察类 handler 必须返回 None**（不得有阻断语义）；hook 内异常不得影响主循环。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

LOG = logging.getLogger("faxiaozhi.hooks")

EVENTS = ("UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop")

HOOKS: dict[str, list[dict[str, Any]]] = {event: [] for event in EVENTS}


def register_hook(event: str, handler: Callable[..., Any], blocking: bool = False) -> None:
    if event not in HOOKS:
        raise ValueError(f"未知事件：{event}")
    HOOKS[event].append({"handler": handler, "blocking": blocking})


def clear_hooks() -> None:
    for event in EVENTS:
        HOOKS[event].clear()


def trigger_hooks(event: str, *args: Any) -> str | None:
    """返回非 None 表示阻断（仅 blocking handler 可阻断）。"""
    reason: str | None = None
    for entry in HOOKS.get(event, []):
        try:
            out = entry["handler"](*args)
        except Exception:                     # 钩子异常不影响主循环
            LOG.exception("hook 失败：%s", entry["handler"].__name__)
            continue
        if entry["blocking"] and out:
            reason = str(out)
    return reason


def observe(handler: Callable[..., Any]) -> Callable[..., None]:
    """装饰器：把仅观察类 handler 的返回值强制压成 None，避免隐性控制流。"""

    def wrapper(*args: Any, **kwargs: Any) -> None:
        handler(*args, **kwargs)
        return None

    wrapper.__name__ = getattr(handler, "__name__", "observer")
    return wrapper
