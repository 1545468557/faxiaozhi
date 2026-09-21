"""权限系统（PRD 3.3 / s03）：三道闸门，默认拒绝。"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .errors import ApiError
from .observability import get_observability
from .session import Session

_SECRET_RE = re.compile(r"(sk-[A-Za-z0-9]{8,}|Bearer\s+[A-Za-z0-9\-]{8,})")


# ---------------------------------------------------------------- 闸门 1：硬拒绝
def _writes_outside_export(session: Session, tool: str, args: dict[str, Any]) -> bool:
    if tool != "export_docx":
        return False
    raw = str(args.get("path") or args.get("filename") or "")
    if not raw:
        # 未指定文件名：由 export_docx 自己生成安全默认名（它还会再做一次目录内校验）
        return False
    from .config import get_config

    export_dir = get_config().exports_dir.resolve()
    candidate = Path(raw)
    if "/" not in raw and "\\" not in raw:
        candidate = export_dir / raw
    try:
        return not candidate.resolve().is_relative_to(export_dir)
    except (OSError, ValueError):
        return True


def _contains_secret(session: Session, tool: str, args: dict[str, Any]) -> bool:
    return bool(_SECRET_RE.search(str(args)))


def _persists_material(session: Session, tool: str, args: dict[str, Any]) -> bool:
    return args.get("_persist_material") is True


def _material_into_log(session: Session, tool: str, args: dict[str, Any]) -> bool:
    return args.get("_contains_material_text") is True


HARD_DENY: list[tuple[str, Callable[[Session, str, dict[str, Any]], bool], str]] = [
    ("写入会话目录之外的路径", _writes_outside_export, "禁止写入导出目录之外的任何路径。"),
    ("把材料原文写入日志或轨迹", _material_into_log, "禁止将案件/合同原文写入日志或轨迹文件。"),
    ("密钥出现在输出", _contains_secret, "输出中检测到密钥，已阻断。"),
    ("对会话材料做持久化", _persists_material, "会话材料仅临时处理，禁止持久化。"),
]


# ---------------------------------------------------------------- 闸门 2：规则匹配
def _matrix_needs_lock(session: Session, args: dict[str, Any]) -> bool:
    return not session.sample.locked


def _export_needs_ready(session: Session, args: dict[str, Any]) -> bool:
    return not session.export_ready()


def _workflow_needs_ask(session: Session, args: dict[str, Any]) -> bool:
    return str(args.get("name", "")) in {"legal-research"}


PERMISSION_RULES: list[dict[str, Any]] = [
    {
        "tools": ["parse_document"],
        "when": lambda s, a: True,
        "message": "即将解析你上传的材料，内容仅在本会话临时处理，不会保存。是否继续？",
        "hard": False,
    },
    {
        "tools": ["render_matrix"],
        "when": _matrix_needs_lock,
        "message": "样本尚未确认，无法生成对比矩阵。",
        "hard": True,
    },
    {
        "tools": ["export_docx"],
        "when": _export_needs_ready,
        "message": "存在未通过核验的引用 / 已排除样本残留 / 越界表述，无法导出。",
        "hard": True,
    },
    {
        "tools": ["Workflow"],
        "when": _workflow_needs_ask,
        "message": "即将启动类案研究编排，将调用检索接口。是否继续？",
        "hard": False,
    },
]


def check_permission(
    session: Session,
    tool: Any,
    args: dict[str, Any],
    asker: Callable[[Session, str, dict[str, Any], str], str] | None = None,
) -> tuple[bool, str]:
    """返回 (是否允许, 说明)。命中即拒绝，**不询问**。"""
    obs = get_observability()
    name = getattr(tool, "name", str(tool))

    # 闸门 1
    for label, predicate, message in HARD_DENY:
        if predicate(session, name, args):
            obs.audit(
                event="permission_denied", session_id=session.id, tool=name, gate=1, reason=label
            )
            return False, message

    # 闸门 2：规则匹配
    matched: list[dict[str, Any]] = []
    for rule in PERMISSION_RULES:
        if name in rule["tools"] and rule["when"](session, args):
            matched.append(rule)

    for rule in matched:
        if rule["hard"]:
            obs.audit(
                event="permission_denied",
                session_id=session.id,
                tool=name,
                gate=2,
                reason=rule["message"][:60],
            )
            return False, rule["message"]

    permission = getattr(tool, "permission", "allow")
    if permission == "deny":
        obs.audit(event="permission_denied", session_id=session.id, tool=name, gate=2, reason="tool_deny")
        return False, "该操作被拒绝：工具权限为 deny。"

    # 闸门 3：用户审批
    if matched or permission == "ask":
        reason = matched[0]["message"] if matched else f"即将执行 {name}，是否继续？"
        if asker is None:
            asker = default_asker
        decision = asker(session, name, args, reason)
        if decision != "allow":
            obs.audit(
                event="permission_denied", session_id=session.id, tool=name, gate=3, reason="user_deny"
            )
            return False, f"该操作被拒绝：{reason}"
        return True, reason

    return True, ""


def default_asker(
    session: Session, tool: str, args: dict[str, Any], reason: str
) -> str:
    """**异步 / 后台轮次直接拒绝**，不与前台争抢输入（PRD 3.3.4）。"""
    if not session.foreground:
        return "deny"
    # 本阶段为单人本地工具：前台轮次视为已授权，交互确认在界面呈现
    return "allow"


def raise_if_denied(session: Session, tool: Any, args: dict[str, Any]) -> None:
    allowed, message = check_permission(session, tool, args)
    if not allowed:
        code = "sample_not_locked" if "样本尚未确认" in message else "permission_denied"
        if "无法导出" in message:
            code = "export_blocked"
        raise ApiError(code, message)
