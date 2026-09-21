"""咨询生成失败与续跑回归：离线模型、隔离存储，不调用真实服务。"""

from __future__ import annotations

import asyncio
import copy
import json
from collections import Counter

import httpx
import pytest

import app.server as server
import app.workflows.consult as consult_module
import app.workflows.runtime as runtime_module
from app.config import get_config
from app.errors import ApiError
from app.knowledge import KnowledgeBase
from app.models import Source, Status, ToolResult
from app.session import Session, SessionStore
from app.workflows import WORKFLOW_FOR_BRANCH, launch
from app.workflows.runtime import RunContext
from tests.conftest import set_policy

QUESTION = "测试当事人在房屋租赁期限内遇到产权转让且被要求搬离，请分析我的租约如何处理。"


@pytest.fixture
async def api(monkeypatch):
    monkeypatch.setattr(server, "STORE", SessionStore(max_concurrent=20))
    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def drain_events(session):
    events = []
    while not session.events.empty():
        events.append(session.events.get_nowait())
    return events


async def finish_task(session):
    return await asyncio.wait_for(session.run_tasks[-1], timeout=10)


def stored_args(session):
    path = get_config().runs_dir / f"{session.run_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))["args"]


@pytest.mark.parametrize("error_code", ["schema_invalid", "model_unavailable"])
async def test_api_answer_retry_preserves_three_rounds_and_only_reruns_answer(api, monkeypatch, error_code):
    set_policy(allow_synthetic=True)
    agent_calls = Counter()
    tool_calls = Counter()
    queries = []
    original_agent = RunContext.agent
    original_dispatch = consult_module.dispatch_tool

    async def agent(ctx, **kwargs):
        label = kwargs["label"]
        agent_calls[label] += 1
        if label == "consult_answer" and agent_calls[label] == 1:
            raise ApiError(error_code, "离线模拟解答失败")
        value = await original_agent(ctx, **kwargs)
        if label == "consult_identify":
            value = copy.deepcopy(value)
            value.update(
                need_clarify=True,
                clarify_questions=[f"请补充第 {ctx.session.consult['rounds'] + 1} 项事实。"],
                assumptions=["租赁关系成立且未被依法解除。"],
            )
        return value

    def dispatch(session, call):
        tool_calls[call.name] += 1
        queries.append(call.arguments.get("query") or call.arguments.get("expression"))
        return original_dispatch(session, call)

    monkeypatch.setattr(RunContext, "agent", agent)
    monkeypatch.setattr(consult_module, "dispatch_tool", dispatch)
    sid = (await api.post("/api/session", json={"branch": "consult"})).json()["session_id"]
    session = server.STORE.get(sid)
    assert (await api.post(f"/api/session/{sid}/message", json={"text": QUESTION})).status_code == 200
    await finish_task(session)
    for round_number in range(3):
        assert session.consult["awaiting"]
        response = await api.post(
            f"/api/session/{sid}/consult/answer",
            json={"facts": f"补充第{round_number + 1}轮：双方有书面租约且租金已支付，合同仍在约定租期内。"},
        )
        assert response.status_code == 200
        await finish_task(session)

    assert session.failed_step == "解答"
    assert session.can_retry
    assert session.consult["rounds"] == 3
    assert session.topic == QUESTION
    assert queries == [QUESTION, QUESTION], "三轮补充不能把原检索主题覆盖成最后一句回答"
    assert not session.consult.get("answer")
    failure_events = drain_events(session)
    assert failure_events[-1] == {
        "event": "error", "data": {"error": {"code": error_code, "message": "离线模拟解答失败"}},
    }
    assert not any(event["event"] == "done" for event in failure_events)
    old_facts = list(session.consult["facts"])
    old_issue = copy.deepcopy(session.consult["issue"])
    old_assumptions = list(session.consult["assumptions"])
    old_sources = set(session.source_pool)
    old_run = session.run_id
    calls_before = dict(agent_calls)
    tools_before = dict(tool_calls)
    assert stored_args(session)["question"] == ""

    response = await api.post(f"/api/session/{sid}/retry", json={})
    assert response.status_code == 200
    assert response.json()["resumed_from"] == "解答"
    result = await finish_task(session)
    assert result["status"] == "answered"
    assert session.run_id == old_run
    assert session.consult["facts"] == old_facts
    assert session.consult["issue"] == old_issue
    assert session.consult["assumptions"] == old_assumptions
    assert session.consult["rounds"] == 3
    assert set(session.source_pool) == old_sources
    assert agent_calls["consult_identify"] == calls_before["consult_identify"]
    assert agent_calls["consult_answer"] == calls_before["consult_answer"] + 1
    assert dict(tool_calls) == tools_before
    assert queries == [QUESTION, QUESTION]
    assert not session.can_retry
    assert session.consult["passed"]
    events = drain_events(session)
    assert events[-1]["event"] == "done"
    assert not any(event["event"] in {"clarify", "error"} for event in events)
    assert next(event for event in events if event["event"] == "retry")["data"]["step"] == "解答"
    for path in get_config().runs_dir.glob(f"{old_run}.*"):
        assert QUESTION not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("failed_phase", ["identify", "retrieve"])
async def test_earlier_failures_retry_without_losing_facts_or_skipping_failed_phase(api, monkeypatch, failed_phase):
    set_policy(allow_synthetic=True)
    calls = Counter()
    original_agent = RunContext.agent
    original_dispatch = consult_module.dispatch_tool

    async def agent(ctx, **kwargs):
        label = kwargs["label"]
        calls[label] += 1
        if failed_phase == "identify" and label == "consult_identify" and calls[label] == 1:
            raise ApiError("model_unavailable")
        result = await original_agent(ctx, **kwargs)
        if label == "consult_identify":
            result = {**result, "need_clarify": False}
        return result

    def dispatch(session, call):
        calls[call.name] += 1
        if failed_phase == "retrieve" and calls[call.name] == 1:
            return ToolResult(tool=call.name, status=Status.INTERFACE_ERROR, detail="离线模拟接口失败")
        return original_dispatch(session, call)

    monkeypatch.setattr(RunContext, "agent", agent)
    monkeypatch.setattr(consult_module, "dispatch_tool", dispatch)
    sid = (await api.post("/api/session", json={"branch": "consult"})).json()["session_id"]
    session = server.STORE.get(sid)
    await api.post(f"/api/session/{sid}/message", json={"text": QUESTION})
    first = await finish_task(session)
    assert first["error"] == ("model_unavailable" if failed_phase == "identify" else "mcp_unavailable")
    facts = list(session.consult["facts"])
    await api.post(f"/api/session/{sid}/retry", json={})
    assert (await finish_task(session))["status"] == "answered"
    assert session.consult["facts"] == facts
    assert calls["consult_identify"] == (2 if failed_phase == "identify" else 1)
    assert calls["search_cases"] == (1 if failed_phase == "identify" else 2)
    assert calls["search_statutes"] == (1 if failed_phase == "identify" else 2)


@pytest.mark.parametrize("branch", ["research", "consult", "contract"])
@pytest.mark.parametrize("route", ["retry", "session_resume", "run_resume"])
async def test_all_resume_routes_preserve_original_workflow(api, monkeypatch, branch, route):
    session = server.STORE.create(branch=branch)
    session.run_id = f"route-{branch}-{route}"
    session.mark_failed("测试步骤", "离线失败")
    state = {"workflow": WORKFLOW_FOR_BRANCH[branch], "args": {}}
    (get_config().runs_dir / f"{session.run_id}.json").write_text(json.dumps(state), encoding="utf-8")
    recorded = []

    async def fake_launch(session, cfg, kb, args, **kwargs):
        recorded.append((session.branch, kwargs.get("workflow")))
        return {"status": "completed"}

    monkeypatch.setattr(server, "launch", fake_launch)
    url = (
        f"/api/runs/{session.run_id}/resume" if route == "run_resume"
        else f"/api/session/{session.id}/{'resume' if route == 'session_resume' else 'retry'}"
    )
    response = await api.post(url, json={})
    assert response.status_code == 200
    assert recorded == [(branch, WORKFLOW_FOR_BRANCH[branch])]


async def test_resume_rejects_branch_override(api):
    state = {"workflow": "legal-contract", "args": {"contract": True}}
    (get_config().runs_dir / "branch-mismatch.json").write_text(json.dumps(state), encoding="utf-8")
    response = await api.post("/api/runs/branch-mismatch/resume", json={"branch": "research"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


async def test_cold_consult_resume_still_refuses_and_releases_lock(monkeypatch):
    set_policy(allow_synthetic=True)
    cfg = get_config()
    session = Session(branch="consult")
    await launch(session, cfg, KnowledgeBase(cfg), {"question": QUESTION * 2, "consult": True},
                 workflow="legal-consult", scenario="consult_bad_schema")
    assert session.can_retry
    args = stored_args(session)
    calls = []

    async def must_not_call_agent(ctx, **kwargs):
        calls.append(kwargs["label"])
        raise AssertionError("重启后不得凭脱敏文本重新生成")

    monkeypatch.setattr(RunContext, "agent", must_not_call_agent)
    cold_session = Session(branch="consult")
    result = await launch(cold_session, cfg, KnowledgeBase(cfg), args,
                          workflow="legal-consult", run_id=session.run_id)
    assert result == {"status": "failed", "error": "consult_not_recoverable"}
    assert calls == []
    assert not (cfg.runs_dir / f"{session.run_id}.lock").exists()
    assert drain_events(cold_session)[-1]["event"] == "error"


def test_load_preserves_user_material_in_live_session():
    cfg = get_config()
    session = Session(branch="consult")
    source = Source(source_id="user-retry", kind="case", origin="user", quote="本会话中保留的用户材料原文。")
    session.source_pool[source.source_id] = source
    ctx = RunContext(session, cfg, None, KnowledgeBase(cfg))
    ctx.snapshot_sources()
    ctx.load()
    assert session.source_pool[source.source_id] is source
    assert source.quote == "本会话中保留的用户材料原文。"


async def test_provider_initialization_failure_emits_error_and_retries_with_original_facts(api, monkeypatch):
    set_policy(allow_synthetic=True)
    original_build = runtime_module.build_provider
    builds = []

    def flaky_build(cfg, scenario="clean"):
        builds.append(scenario)
        if len(builds) == 1:
            raise ApiError("model_unavailable", "离线模拟模型 SDK 初始化失败")
        return original_build(cfg, scenario)

    monkeypatch.setattr(runtime_module, "build_provider", flaky_build)
    sid = (await api.post("/api/session", json={"branch": "consult"})).json()["session_id"]
    session = server.STORE.get(sid)
    question = QUESTION * 2  # 事实充足，重试后直接回答。
    response = await api.post(f"/api/session/{sid}/message", json={"text": question})
    assert response.status_code == 200
    assert await finish_task(session) == {"status": "failed", "error": "model_unavailable"}
    assert session.failed_step == "识别问题"
    assert session.can_retry
    assert session.consult["facts"] == [question]
    assert session.consult["rounds"] == 0
    assert stored_args(session)["question"] == ""
    events = drain_events(session)
    assert events[-1]["event"] == "error"
    assert events[-1]["data"]["error"]["code"] == "model_unavailable"
    assert not any(event["event"] == "done" for event in events)

    response = await api.post(f"/api/session/{sid}/retry", json={})
    assert response.status_code == 200
    assert (await finish_task(session))["status"] == "answered"
    assert session.consult["facts"] == [question]
    assert session.consult["rounds"] == 0
    assert not session.can_retry
    assert builds == ["clean", "clean"]
    assert drain_events(session)[-1]["event"] == "done"
