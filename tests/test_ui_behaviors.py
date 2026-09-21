# ruff: noqa: E402, I001
"""说明：本文件需要在设置好环境后再导入 app 模块，故显式关闭导入位置/排序规则。

界面行为补验（2-2 U 组，6 条）：把 2-1 遗留的人工点击项做成自动化回归——
A6 未确认样本拿不到矩阵、A11 越界表述被拦、A12 两种文案区分、会话空闲释放、
并发满返回 429、勾选状态不丢。不依赖人工点击，也不产生真实调用。
"""

from __future__ import annotations

from pathlib import Path

from app.gates.expression import ExpressionGuard
from app.gates.runner import apply_gates
from app.llm import ToolCall
from app.models import Status
from app.session import SESSION_AWAITING, SESSION_CLOSED, Session, SessionStore
from app.tools import dispatch_tool

INDEX_HTML = Path(__file__).resolve().parent.parent / "web" / "index.html"


def _html() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


# ==================================================================== U39 / A6


def test_u39_a6_matrix_is_refused_until_sample_is_confirmed():
    """A6：未确认样本时，接口直接拒绝产出矩阵（不问、不默认继续）。"""
    session = Session()
    session.sample.set_candidates(["s1", "s2"])          # 有候选但**未锁定**
    result = dispatch_tool(session, ToolCall(id="c1", name="render_matrix", arguments={}))
    assert result.status is Status.INSUFFICIENT
    assert result.error_kind in {"precondition", "permission"}
    assert result.meta.get("denied") or result.meta.get("precondition")
    assert session.matrix == []


def test_u39b_confirmed_sample_unlocks_matrix():
    session = Session()
    session.sample.set_candidates(["s1", "s2"])
    session.sample.confirm(["s1"], ["s2"])
    session.cases = [
        {"case_id": "s1", "identifier": "（2023）示例民终1号", "stance": "support"},
    ]
    result = dispatch_tool(session, ToolCall(id="c1", name="render_matrix", arguments={}))
    assert result.status is Status.OK
    assert len(session.matrix) == 1
    assert session.distribution["denominator"] == 1


# ==================================================================== U40 / A11


def test_u40_a11_out_of_scope_expression_is_detected_and_blocks_export():
    """A11：越界表述被表达边界拦下，并阻止导出。"""
    guard = ExpressionGuard([])
    hits = guard.scan("各地法院普遍支持买受人主张。")
    assert hits and hits[0]["category"] == "总体化推断"

    session = Session()
    session.topic = "测试议题"
    session.matrix = [{"identifier": "（2023）示例民终1号"}]
    session.structured_output = {
        "conclusions": [{"text": "各地法院普遍支持买受人主张。", "citations": []}]
    }
    apply_gates(session)

    assert session.expression_hits, "越界表述必须被记录"
    blockers = "；".join(session.export_blockers())
    assert "越界表述" in blockers
    assert session.export_ready() is False
    # 改写提示必须给出可执行口径（不是只说「有问题」）
    assert any("本次样本" in hint for hint in session.gate_report.gaps)


def test_u40b_clean_text_is_not_blocked():
    guard = ExpressionGuard([])
    assert guard.scan("本次确认样本 3 篇中，2 篇支持减少价款。") == []


# ==================================================================== U41 / A12


def test_u41_a12_no_match_and_interface_error_use_different_copy():
    from app.models import STATUS_TEXT

    no_match = STATUS_TEXT[Status.NO_MATCH]
    interface = STATUS_TEXT[Status.INTERFACE_ERROR]
    assert no_match != interface
    assert "接口" not in no_match
    assert "接口调用失败" in interface and "不等于" in interface

    html = _html()
    assert 'no_match: "没匹配到"' in html
    assert 'interface_error: "接口调用失败"' in html
    assert "这不等于「没有相关案例」" in html
    assert "这与「接口失败」不同" in html


# ==================================================================== U42 空闲释放


def test_u42_idle_session_releases_concurrency_slot():
    store = SessionStore(max_concurrent=1, ttl_seconds=0)
    first = store.create()
    assert first.queued is False
    first.last_active -= 10                     # 人为变旧
    second = store.create()                     # 触发 sweep，名额应归还
    assert first.status is SESSION_CLOSED
    assert second.queued is False, "空闲会话释放后，新会话不该继续排队"
    assert store.active_count() == 1


def test_u42b_cleanup_is_visible_in_state():
    session = Session()
    session.status = "closed"
    assert session.snapshot()["status"] == "closed"


# ==================================================================== U43 并发满 429


def test_u43_concurrency_full_is_a_visible_429():
    from fastapi.testclient import TestClient

    from app.server import app
    from app.session import STORE

    original = STORE.max_concurrent
    try:
        with TestClient(app) as client:
            STORE.max_concurrent = -1               # 启动后再制造「名额已满」（lifespan 会用配置覆盖）
            created = client.post("/api/session", json={}).json()
            assert created["queued"] is True
            assert "上限" in created["message"]
            followup = client.post(
                f"/api/session/{created['session_id']}/message", json={"text": "议题"}
            )
        assert followup.status_code == 429
        assert followup.json()["error"]["code"] == "concurrency_queued"
    finally:
        STORE.max_concurrent = original


# ==================================================================== U44 勾选不丢


def test_u44_selection_survives_redraws_in_frontend_state():
    """勾选状态存在 state.selection，后台轮询重绘时只读不重置（回归 2-1 实测问题 3）。"""
    html = _html()
    assert "selection: {}" in html
    assert "if (!(c.source_id in state.selection))" in html, "首次渲染才给默认值，重绘不得覆盖"
    assert "state.selection[box.value] = box.checked" in html, "勾选必须写回 state"
    assert "const PRESELECT" in html, "默认预选数量必须可配（实测问题 2 的回归）"
    # 重试按钮只在失败步骤存在时出现，且带「不会重跑」的说明
    assert "btn-retry" in html
    assert "已完成的步骤不会重跑" in html


# ==================================================================== U45 旧存档不得卡死会话


def test_u45_stale_run_still_creates_a_session():
    """2-4 验收实测缺陷：旧存档 + 服务重启后，页面不得停在「无会话」状态。

    修复前：ensureSession() 走到「可按存档续跑」分支后直接 return，
    state.sessionId 仍为 null，上传落到 /api/session/null/upload → 404（界面「没反应」）。
    修复后：先新建会话，再提示可续跑旧任务。
    """
    html = _html()
    create_at = html.index('const res = await api("/api/session"')
    recover_at = html.index("btn-recover")
    assert create_at < recover_at, "必须先建立新会话，再提示按存档续跑"
    assert "走 run_id 恢复" not in html, "旧的提前 return 分支必须删除"
    # 已建立会话时保持幂等，不重复创建
    assert "if (state.sessionId) return state.sessionId;" in html


def test_u45b_upload_guards_against_null_session():
    """doUpload 必须有空会话保护，不能向 /api/session/null/upload 发请求。"""
    html = _html()
    start = html.index("async function doUpload()")
    end = html.index('document.querySelectorAll(".tabs button")', start)
    body = html[start:end]
    assert "await ensureSession();" in body, "上传前必须确保会话存在"
    assert "会话未建立，请刷新页面后重试" in body, "无会话时要给可读的失败文案，而非静默 404"


# ===================================================== U46 名额被闲置会话占满


def test_u46_full_quota_evicts_oldest_idle_session():
    """回归（2-4 代操作验收缺陷 4）：名额满时应回收最旧的空闲会话，而不是把用户锁在门外。

    修复前 active_count() 把「只打开过页面、没在跑」的会话也计入上限，而它们要空闲 30 分钟才释放，
    用户多开几次页面就点不了「开始研究」（报「同时在跑的研究已达上限」——而实际没有研究在跑）。
    """
    store = SessionStore(max_concurrent=2, ttl_seconds=1800)
    oldest = store.create()
    oldest.last_active -= 100
    newer = store.create()
    newer.last_active -= 10

    fresh = store.create()
    assert fresh.queued is False, "名额满时应回收空闲会话，而不是让新会话排队"
    assert store.active_count() == 2
    assert oldest.status is SESSION_CLOSED, "应回收最旧的那个空闲会话"
    assert newer.status != SESSION_CLOSED


def test_u46b_awaiting_checkpoint_is_busy_and_never_evicted():
    """正等人确认（awaiting_checkpoint）的会话属「进行中」，不得回收。"""
    store = SessionStore(max_concurrent=1, ttl_seconds=1800)
    busy = store.create()
    busy.status = SESSION_AWAITING

    fresh = store.create()
    assert fresh.queued is True, "名额真被占用时应如实排队"
    assert busy.status is not SESSION_CLOSED


def test_u46c_session_with_running_task_is_never_evicted():
    """有未完成任务（在跑）的会话不得回收。"""
    store = SessionStore(max_concurrent=1, ttl_seconds=1800)
    running = store.create()

    class _Pending:
        def done(self):
            return False

    running.run_tasks = [_Pending()]
    fresh = store.create()
    assert fresh.queued is True
    assert running.status is not SESSION_CLOSED


# ===================================================== U47 窄屏不得被宽表撑破


def test_u47_mobile_single_column_and_wide_matrix_do_not_break_the_page():
    """回归（2-4 代操作验收缺陷 5）：375px 下生成矩阵后整页被撑到 680px。

    根因：窄屏单列写的是 `grid-template-columns:1fr`，`1fr` 轨道的 auto 最小尺寸被宽表撑开；
    且矩阵表没有横向滚动容器。实测隐藏 #matrix 后 docWidth 由 680 → 360。
    """
    html = _html()
    assert "@media (max-width: 900px)" in html
    assert "grid-template-columns:minmax(0,1fr);" in html, (
        "窄屏单列必须用 minmax(0,1fr)，否则宽表会把整页撑开"
    )
    assert "#matrix { overflow-x:auto; }" in html, "宽表必须能横向滚动，不得撑破页面"
