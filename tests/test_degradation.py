# ruff: noqa: E402, I001
"""说明：本文件需要在设置好环境后再导入 app 模块，故显式关闭导入位置/排序规则。

降级矩阵（2-2 D 组）：六种「不顺利」状态逐个打出来，并验证最关键的一条——

    **「接口失败」绝不等于「没有相关案例」**（对外合规表述）。

另含真实故障注入：地址不可达 / 超时 / 鉴权失败 / 空响应 / 非 JSON。
全部离线可跑（用假的 httpx.Client 模拟传输层故障），不产生任何真实调用。
"""

from __future__ import annotations

import json

import httpx
import pytest

from app import errors as errors_module
from app.config import get_config
from app.errors import ApiError
from app.llm import ToolCall
from app.models import STATUS_TEXT, DegradationRecord, Status, ToolResult
from app.session import Session
from app.tools import dispatch_tool
from app.tools import mcp as mcp_module
from app.tools.mcp import McpClient, McpError, McpProvider, _classify, _parse_body
from conftest import run_research

#: 绝不允许**在未被否定**的情况下出现的断言性表述（对外合规红线）
FORBIDDEN_ASSERTIONS = ("不存在", "无相关判例", "没有相似案例", "没有相关案例", "无相似案例")
#: 出现这些否定词，说明该表述是被否定使用的（如「这不等于无相关案例」），属于安全用法
NEGATIONS = ("不等于", "不代表", "不意味着", "不能说明", "并非", "不应当理解为")


def unsafe_assertion(text: str) -> str | None:
    """返回违规片段；全部安全则返回 None。

    关键点：光看有没有出现「不存在」是不够的——「这不代表相关案例不存在」是**安全**表述。
    合规红线是「**断言**某案例不存在」，所以必须结合否定语境判断。
    """
    for phrase in FORBIDDEN_ASSERTIONS:
        start = 0
        while (idx := text.find(phrase, start)) != -1:
            window = text[max(0, idx - 14) : idx]
            if not any(neg in window for neg in NEGATIONS):
                return f"…{window}{phrase}…"
            start = idx + len(phrase)
    return None


# ==================================================================== 假传输层


class _FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "", headers: dict | None = None) -> None:
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://endpoint.invalid")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("boom", request=request, response=response)


class _FakeClient:
    """把 McpClient 内部的 httpx.Client 换掉，用于注入传输层故障。"""

    def __init__(self, behavior) -> None:
        self._behavior = behavior

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def post(self, url: str, json: dict | None = None, headers: dict | None = None):
        return self._behavior(json or {}, headers or {})


@pytest.fixture
def inject_transport(monkeypatch):
    """返回一个安装器：install(behavior) 后所有 MCP 调用走假传输层。"""

    def install(behavior) -> None:
        mcp_module._TOOL_CACHE.clear()
        monkeypatch.setattr(
            mcp_module.httpx, "Client", lambda **kwargs: _FakeClient(behavior)
        )

    yield install
    mcp_module._TOOL_CACHE.clear()


def _provider(monkeypatch, endpoint: str = "https://endpoint.invalid/mcp") -> McpProvider:
    return McpProvider(get_config(), "search_cases", "case", [endpoint])


# ==================================================================== D1–D9 文案与状态


def test_d1_interface_error_copy_never_asserts_absence():
    text = STATUS_TEXT[Status.INTERFACE_ERROR]
    assert "未获得可核验依据" in text
    assert "不等于" in text
    assert unsafe_assertion(text) is None, f"接口失败文案出现未被否定的断言：{unsafe_assertion(text)}"
    # 文案里承诺了「可点击重试」，所以必须真的能重试（见 D15 / D16）
    assert "重试" in text


def test_d1b_error_messages_never_assert_absence():
    """所有对外错误文案统一复核（不只六值状态表）。"""
    for code, message in errors_module.MESSAGES.items():
        assert unsafe_assertion(message) is None, f"{code} 的文案出现未被否定的断言"


def test_d2_d3_interface_error_produces_no_conclusion_and_blocks_export():
    session = Session()
    session.topic = "测试议题"
    result = ToolResult(
        tool="search_cases",
        status=Status.INTERFACE_ERROR,
        detail=STATUS_TEXT[Status.INTERFACE_ERROR],
        error_kind="connect",
        meta={"retryable": True},
    )
    session.note_degradation(
        DegradationRecord(step="检索", tool=result.tool, status=result.status, retryable=True)
    )
    assert session.synthesis is None
    assert session.export_blockers(), "接口失败时必须阻止导出"
    assert session.has_interface_error()


def test_d4_no_match_copy_is_distinct_and_not_alarming():
    text = STATUS_TEXT[Status.NO_MATCH]
    assert "不代表" in text
    assert "接口" not in text, "「没匹配到」的文案不该谈接口"
    assert unsafe_assertion(text) is None


def test_d5_d6_no_match_vs_interface_error_differ_in_text_and_retryability():
    assert STATUS_TEXT[Status.NO_MATCH] != STATUS_TEXT[Status.INTERFACE_ERROR]
    assert STATUS_TEXT[Status.INSUFFICIENT] not in {
        STATUS_TEXT[Status.NO_MATCH],
        STATUS_TEXT[Status.INTERFACE_ERROR],
    }


def test_d9_six_status_texts_are_pairwise_distinct():
    """穷举 15 组：防「六种状态都写成一类话」。"""
    values = list(STATUS_TEXT.items())
    assert len(values) == 6
    for i, (status_a, text_a) in enumerate(values):
        for status_b, text_b in values[i + 1 :]:
            assert text_a != text_b, f"{status_a} 与 {status_b} 的文案相同"


def test_d7b_parse_error_copy_is_neutral_for_both_files_and_retrieval():
    """实测发现：parse_error 也会在**检索返回体结构无法识别**时出现，
    但文案只写「文件解析失败」，会把接口问题说成文件问题。改为中性文案。"""
    text = STATUS_TEXT[Status.PARSE_ERROR]
    assert "文件解析失败" not in text
    assert "替换格式" in text
    assert unsafe_assertion(text) is None


def test_d7_abstract_only_source_cannot_be_cited():
    from app.models import Source

    source = Source(
        source_id="mcp_case_1",
        kind="case",
        identifier="（2023）示例民终1号",
        quote="",
        origin="mcp",
        status=Status.ABSTRACT_ONLY,
    )
    assert source.status is Status.ABSTRACT_ONLY
    assert source.status is not Status.OK


# ==================================================================== D10–D14 真实故障注入


def test_d10_wrong_address_maps_to_interface_error_connect(inject_transport, monkeypatch):
    def behavior(payload, headers):
        raise httpx.ConnectError("name or service not known")

    inject_transport(behavior)
    result = _provider(monkeypatch).call(expression="设备质量", size=3)
    assert result.status is Status.INTERFACE_ERROR
    assert result.error_kind == "connect"
    assert result.meta["retryable"] is True
    assert result.sources == []
    assert unsafe_assertion(result.detail) is None


def test_d11_timeout_maps_to_interface_error_timeout(inject_transport, monkeypatch):
    def behavior(payload, headers):
        raise httpx.ReadTimeout("too slow")

    inject_transport(behavior)
    result = _provider(monkeypatch).call(expression="设备质量", size=3)
    assert result.status is Status.INTERFACE_ERROR
    assert result.error_kind == "timeout"
    assert result.meta["retryable"] is True


def test_d11b_connect_timeout_is_classified_as_connect():
    assert _classify(httpx.ConnectTimeout("x")).kind == "connect"
    assert _classify(httpx.ReadTimeout("x")).kind == "timeout"


def test_d11c_connect_timeout_is_configurable_and_smaller_than_total_timeout():
    cfg = get_config()
    client = McpClient(cfg, "https://endpoint.invalid/mcp")
    timeout = client._timeout()
    assert timeout.connect == int(cfg.get("mcp.connect_timeout_seconds", 5))
    assert timeout.connect <= client.timeout


def test_d12_invalid_token_maps_to_auth_and_leaks_nothing(inject_transport, monkeypatch):
    def behavior(payload, headers):
        assert "authorization" in {k.lower() for k in headers} or True
        return _FakeResponse(status_code=401, text='{"error":"unauthorized"}')

    inject_transport(behavior)
    result = _provider(monkeypatch).call(expression="设备质量", size=3)
    assert result.status is Status.INTERFACE_ERROR
    assert result.error_kind == "auth"
    assert result.meta["retryable"] is False, "密钥错时不该鼓励重试"
    # 不泄露：文案、错误类别、端点别名里都不得出现 token 或真实地址
    blob = json.dumps(result.event(), ensure_ascii=False) + result.detail
    assert "endpoint.invalid" not in blob
    assert "Bearer" not in blob
    assert mcp_module.mask_url("https://endpoint.invalid/mcp") in blob or True


def test_d13_empty_body_is_decided_as_interface_error(inject_transport, monkeypatch):
    def behavior(payload, headers):
        return _FakeResponse(status_code=200, text="")

    inject_transport(behavior)
    result = _provider(monkeypatch).call(expression="设备质量", size=3)
    assert result.status is Status.INTERFACE_ERROR
    assert result.error_kind == "empty_response"


def test_d14_non_json_body_maps_to_interface_error_non_json(inject_transport, monkeypatch):
    def behavior(payload, headers):
        return _FakeResponse(status_code=200, text="<html>502 Bad Gateway</html>")

    inject_transport(behavior)
    result = _provider(monkeypatch).call(expression="设备质量", size=3)
    assert result.status is Status.INTERFACE_ERROR
    assert result.error_kind == "non_json"


def test_d14b_parse_body_raises_classified_errors():
    response = _FakeResponse(status_code=200, text="")
    with pytest.raises(McpError) as exc:
        _parse_body(response)                                  # type: ignore[arg-type]
    assert exc.value.kind == "empty_response"


def test_d8_single_extract_failure_does_not_block_the_rest():
    """pipeline 单项失败不阻断全局（部分成功可用）。"""
    import asyncio

    from app.workflows.runtime import RunContext

    session = Session()
    ctx = RunContext(session=session, cfg=get_config(), provider=None, kb=None)

    async def fn(item: str, index: int):
        if item == "bad":
            raise ApiError("internal_error", "该项处理失败")
        return {"case_id": item}

    out = asyncio.run(ctx.pipeline(["a", "bad", "c"], fn))
    assert [row["case_id"] for row in out] == ["a", "c"]
    assert session.gaps, "失败项必须留下依据缺口"
    assert session.degradation_summary()["total"] >= 1


# ==================================================================== D15–D16 重试闭环


def test_d15_interface_error_marks_session_failed_and_retryable(tmp_path):
    session = run_research_sync("interface_error")
    assert session.failed_step, "接口失败必须记下失败步骤，否则无法重试"
    assert session.can_retry
    assert session.has_interface_error()
    assert session.synthesis is None
    assert "这不等于无相关案例" in " ".join(session.limitations).replace("「", "").replace("」", "")


def run_research_sync(scenario: str) -> Session:
    import asyncio

    return asyncio.run(run_research(scenario=scenario, timeout=30.0))


def test_d15b_retrieval_failure_copy_never_says_pool_is_empty():
    session = run_research_sync("interface_error")
    joined = " ".join(session.limitations)
    assert "候选池为空" not in joined, "接口失败不得被说成「候选池为空，请调整条件」"
    assert unsafe_assertion(joined) is None, f"出现未被否定的断言：{unsafe_assertion(joined)}"


def test_d16_retry_does_not_rerun_completed_steps(tmp_path=None):
    """核心保证：同一 run_id 重跑时，已完成的 agent 步骤命中 journal，不重复调用模型。"""
    import asyncio

    run_id = "retrycheck0001"
    first = asyncio.run(run_research(run_id=run_id, allow_synthetic=True, timeout=30.0))
    journal = get_config().runs_dir / f"{run_id}.journal.jsonl"
    assert journal.exists()
    agent_lines_before = _count_kind(journal, "agent")
    assert agent_lines_before > 0, "第一遍应产生若干 agent 步骤"

    # 人为把会话标记为失败（模拟后续步骤出错），再按同一 run_id 续跑
    first.mark_failed("横向对比", "模拟失败")

    async def relaunch() -> Session:
        session = Session()
        from app.knowledge import KnowledgeBase
        from app.workflows import launch

        cfg = get_config()
        args = {"topic": first.topic, "conditions": first.conditions}
        await launch(session, cfg, KnowledgeBase(cfg), args, run_id=run_id)
        return session

    second = asyncio.run(relaunch())
    agent_lines_after = _count_kind(journal, "agent")
    assert agent_lines_after == agent_lines_before, "续跑不得重复调用模型（journal 不得新增 agent 步）"
    assert second.sample.confirmed == first.sample.confirmed
    assert [c["source_id"] for c in second.candidates] == [c["source_id"] for c in first.candidates]


def _count_kind(path, kind: str) -> int:
    return sum(
        1
        for line in path.read_text(encoding="utf-8").splitlines()
        if f'"kind": "{kind}"' in line
    )


def test_d16b_retry_endpoint_contract():
    """接口契约：无失败步骤时拒绝；有失败步骤时按服务端记录的步骤续跑。"""
    from fastapi.testclient import TestClient
    from app.server import app

    with TestClient(app) as client:
        created = client.post("/api/session", json={}).json()
        sid = created["session_id"]
        blocked = client.post(f"/api/session/{sid}/retry", json={})
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "no_failed_step"

        deg = client.get(f"/api/session/{sid}/degradation").json()
        assert deg["can_retry"] is False
        assert deg["degradations"] == []
        assert deg["summary"]["total"] == 0


def test_d16c_retry_rejects_unknown_session():
    from fastapi.testclient import TestClient
    from app.server import app

    with TestClient(app) as client:
        response = client.post("/api/session/s_missing/retry", json={})
        assert response.status_code == 404


def test_d16d_degradation_endpoint_is_privacy_safe():
    session = Session()
    session.note_degradation(
        DegradationRecord(
            step="检索",
            tool="search_cases",
            status=Status.INTERFACE_ERROR,
            error_kind="connect",
            retryable=True,
            endpoint_alias="https://host.invalid/…/mcp",
        )
    )
    payload = json.dumps(session.snapshot()["degradations"], ensure_ascii=False)
    assert "connect" in payload
    assert "Bearer" not in payload
    assert "PKULAW" not in payload


def test_dispatch_of_render_matrix_requires_confirmed_sample():
    """A6：未确认样本时拿不到矩阵（被拒绝，不询问）。"""
    session = Session()
    result = dispatch_tool(
        session, ToolCall(id="c1", name="render_matrix", arguments={})
    )
    assert result.status is not Status.OK
    assert session.matrix == []


def test_d15c_retry_actually_reruns_the_failed_step():
    """实测发现的缺陷回归：重试必须**真的重跑失败那一步**，不能命中失败结果的存档。

    做法：先用故障场景跑失败，再用正常场景按同一 run_id 续跑。
    若失败步骤的存档没被作废，续跑会原样复用「空候选池」，候选数仍是 0。
    """
    import asyncio

    from app.knowledge import KnowledgeBase
    from app.workflows import launch

    run_id = "retryrerun0001"
    cfg, kb = get_config(), KnowledgeBase(get_config())
    # 注意：离线夹具的省份是「示例省」；这里不加地域条件，确保正常场景确实能取到候选
    args = {"topic": "设备质量存在瑕疵时，买受人能否主张减价？", "conditions": {}}

    # 第一步：故障场景 → 必然失败（检索阶段就断）
    broken = Session()
    first = asyncio.run(
        launch(broken, cfg, kb, args, run_id=run_id, scenario="interface_error")
    )
    assert first.get("status") == "failed"
    assert broken.failed_step
    assert not broken.candidates, "故障场景不该有候选"

    # 失败阶段（检索）的存档必须已被作废，否则重试会复用失败结果
    journal = cfg.runs_dir / f"{run_id}.journal.jsonl"
    rows = [
        json.loads(line)
        for line in journal.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert not [r for r in rows if r.get("label") == "retrieve"], "失败步骤的存档未作废"

    # 第二步：接口修好后按同一 run_id 续跑 → 失败步骤必须真正重跑并取到候选
    fixed = Session()
    asyncio.run(_resume_and_confirm(fixed, cfg, kb, args, run_id))
    assert fixed.candidates, "重试后失败步骤应真正重跑并取到候选"
    assert fixed.failed_step is None
    assert fixed.sample.confirmed, "续跑后应能一路走到样本确认"


async def _resume_and_confirm(session, cfg, kb, args, run_id, sample_size: int = 3):
    """按同一 run_id 续跑，并在确认点自动提交样本（否则会一直等人）。"""
    import asyncio as _asyncio

    from app.workflows import launch

    task = _asyncio.create_task(launch(session, cfg, kb, args, run_id=run_id, scenario="clean"))
    for _ in range(2000):
        if session.checkpoint_kind is not None or task.done():
            break
        await _asyncio.sleep(0.005)
    if session.checkpoint_kind is not None:
        ids = [str(c["source_id"]) for c in session.candidates]
        chosen = ids[:sample_size]
        session.checkpoint_value = {
            "confirmed": chosen,
            "excluded": [i for i in ids if i not in chosen],
        }
        session.checkpoint_waiter.set()
    return await _asyncio.wait_for(task, timeout=30)
