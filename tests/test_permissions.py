"""权限三道闸门 + 钩子约束。"""

from __future__ import annotations

from app import permissions
from app.config import get_config
from app.hooks import clear_hooks, observe, register_hook, trigger_hooks
from app.permissions import check_permission, default_asker
from app.session import Session
from app.tools import get_tool


def call(tool_name: str, args: dict, session: Session | None = None, asker=None):
    return check_permission(session or Session(), get_tool(tool_name), args, asker=asker)


def test_hard_deny_write_outside_export_dir():
    allowed, message = call("export_docx", {"path": "/etc/passwd"})
    assert not allowed
    assert "导出目录之外" in message


def test_hard_deny_path_traversal_filename():
    allowed, _ = call("export_docx", {"filename": "../../evil.docx"})
    assert not allowed


def test_hard_deny_secret_in_arguments():
    allowed, message = call("export_docx", {"filename": "x.docx", "token": "Bearer abcdef123456"})
    assert not allowed
    assert "密钥" in message


def test_hard_deny_persisting_material():
    allowed, _ = call("parse_document", {"filename": "a.txt", "_persist_material": True})
    assert not allowed


def test_hard_deny_material_into_log():
    allowed, _ = call("parse_document", {"filename": "a.txt", "_contains_material_text": True})
    assert not allowed


def test_matrix_denied_without_locked_sample_and_does_not_ask():
    asked: list[str] = []

    def asker(session, tool, args, reason):
        asked.append(tool)
        return "allow"

    allowed, message = call("render_matrix", {}, asker=asker)
    assert not allowed
    assert "样本尚未确认" in message
    assert asked == []                      # hard 规则跳过闸门 3，不询问


def test_matrix_allowed_after_sample_locked():
    session = Session()
    session.sample.set_candidates(["a"])
    session.sample.confirm(["a"], [])
    allowed, _ = call("render_matrix", {}, session=session)
    assert allowed


def test_export_denied_when_not_ready():
    session = Session()
    allowed, message = call("export_docx", {"filename": "x.docx"}, session=session)
    assert not allowed
    assert "无法导出" in message


def test_foreground_false_denies_ask_tool():
    session = Session()
    session.foreground = False
    assert default_asker(session, "parse_document", {}, "理由") == "deny"


def test_foreground_true_allows_ask_tool_in_local_tool():
    assert default_asker(Session(), "parse_document", {}, "理由") == "allow"


def test_explicit_asker_deny_blocks_tool():
    allowed, _ = call("parse_document", {"filename": "a.txt"}, asker=lambda *a: "deny")
    assert not allowed


def test_audit_written_on_deny(tmp_path):
    obs = permissions.get_observability(get_config())
    original = obs.metrics_path
    obs.metrics_path = tmp_path / "metrics.jsonl"
    try:
        call("export_docx", {"path": "/etc/passwd"})
    finally:
        obs.metrics_path = original
    text = (tmp_path / "metrics.jsonl").read_text(encoding="utf-8")
    assert "permission_denied" in text


def test_only_observer_handler_returns_none():
    clear_hooks()
    calls: list[str] = []

    def handler(*_args):
        calls.append("hit")
        return "这会阻断吗？"

    register_hook("PostToolUse", observe(handler), blocking=False)
    assert trigger_hooks("PostToolUse", 1, 2) is None
    assert calls == ["hit"]


def test_blocking_handler_can_block():
    clear_hooks()
    register_hook("PreToolUse", lambda *_a: "不允许", blocking=True)
    assert trigger_hooks("PreToolUse", None) == "不允许"


def test_hook_exception_does_not_break_loop():
    clear_hooks()

    def boom(*_args):
        raise RuntimeError("boom")

    register_hook("PostToolUse", boom, blocking=True)
    register_hook("PostToolUse", lambda *_a: "ok-blocked", blocking=True)
    assert trigger_hooks("PostToolUse", None) == "ok-blocked"


def test_unknown_event_raises():
    try:
        register_hook("Nope", lambda: None)
    except ValueError as exc:
        assert "未知事件" in str(exc)
    else:                                   # pragma: no cover
        raise AssertionError("应当抛出 ValueError")
