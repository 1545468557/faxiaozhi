"""单题动态追问与主动跳过：真实工作流/API + 离线模型和隔离数据。"""

from __future__ import annotations

import asyncio
import copy
from collections import Counter

import httpx
import pytest

import app.server as server
import app.workflows.consult as consult_module
from app.config import get_config
from app.errors import ApiError
from app.prompts import build_consult_identify_prompt
from app.session import Session, SessionStore
from app.workflows.runtime import RunContext
from tests.conftest import set_policy

QUESTION = "房东卖房后新房东要求我搬离"


@pytest.fixture
async def api(monkeypatch):
    set_policy(allow_synthetic=True)
    monkeypatch.setattr(server, "STORE", SessionStore(max_concurrent=20))
    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def finish(session):
    return await asyncio.wait_for(session.run_tasks[-1], timeout=10)


def drain(session):
    events = []
    while not session.events.empty():
        events.append(session.events.get_nowait())
    return events


async def start(api):
    sid = (await api.post("/api/session", json={"branch": "consult"})).json()["session_id"]
    response = await api.post(f"/api/session/{sid}/message", json={"text": QUESTION})
    assert response.status_code == 200
    session = server.STORE.get(sid)
    await finish(session)
    assert session.consult["awaiting"]
    return session


def issue(questions, need=True):
    return {
        "legal_relation": "房屋租赁",
        "disputes": ["房屋出售后是否继续履行租约"],
        "given_facts": ["用户提供了房屋出售的信息"],
        "missing_facts": ["是否已经交付房屋", "租约是否仍在约定期限内"],
        "possible_claims": [],
        "need_clarify": need,
        "clarify_questions": questions,
        "assumptions": ["对未说明的事实仅作条件性分析"],
    }


@pytest.mark.parametrize("raw_question", [
    "租约是否仍在约定期限内？备用问题是否已经交房？",
    "租约是否仍在约定期限内？\n备用问题是否已经交房？",
    "租约是否仍在约定期限内？；备用问题是否已经交房？",
])
async def test_one_actual_question_is_shared_by_state_sse_and_asked(api, monkeypatch, raw_question):
    original_agent = RunContext.agent

    async def agent(ctx, **kwargs):
        if kwargs["label"] == "consult_identify":
            return issue([raw_question, "未发送的候选问题？"])
        return await original_agent(ctx, **kwargs)

    monkeypatch.setattr(RunContext, "agent", agent)
    monkeypatch.setitem(get_config().raw["consult"], "max_questions_per_round", 9)
    session = await start(api)
    sent = next(event for event in drain(session) if event["event"] == "clarify")["data"]
    assert sent["questions"] == ["租约是否仍在约定期限内？"]
    assert session.consult["issue"]["clarify_questions"] == sent["questions"]
    assert session.consult["asked"] == sent["questions"]
    assert session.consult["rounds"] == 1
    assert session.consult["max_rounds"] == sent["max_rounds"] == 3
    assert sent["remaining"] == 2


@pytest.mark.parametrize("facts,expected_next", [
    ("已经签了书面合同，还剩八个月租期", None),
    ("没有书面合同，只口头说租一年", "房屋是否已经交付给你居住？"),
])
async def test_identifies_again_after_answer_to_end_early_or_change_next_question(
    api, monkeypatch, facts, expected_next,
):
    original_agent = RunContext.agent
    identify_facts = []

    async def agent(ctx, **kwargs):
        if kwargs["label"] == "consult_identify":
            known = list(ctx.session.consult["facts"])
            identify_facts.append(known)
            if len(known) == 1:
                return issue(["双方是否签了书面合同？", "旧候选问题不能直接照问？"])
            if "已经签了" in known[-1]:
                return issue([], need=False)
            return issue(["房屋是否已经交付给你居住？"])
        return await original_agent(ctx, **kwargs)

    monkeypatch.setattr(RunContext, "agent", agent)
    session = await start(api)
    drain(session)
    response = await api.post(f"/api/session/{session.id}/consult/answer", json={
        "facts": facts, "expected_round": 1,
    })
    assert response.status_code == 200
    result = await finish(session)
    assert identify_facts == [[QUESTION], [QUESTION, facts]]
    if expected_next:
        assert result["status"] == "awaiting_answer"
        assert session.consult["issue"]["clarify_questions"] == [expected_next]
        assert session.consult["asked"] == ["双方是否签了书面合同？", expected_next]
    else:
        assert result["status"] == "answered"
        assert session.consult["rounds"] == 1
        assert not session.consult["awaiting"]
        assert session.consult["issue"]["clarify_questions"] == []
        assert not any(event["event"] == "clarify" for event in drain(session))


@pytest.mark.parametrize("draft", ["", "我已入住一个月，租约还有十一个月，租金已按约支付。"])
async def test_skip_uses_optional_draft_without_identify_or_extra_rounds(api, monkeypatch, draft):
    session = await start(api)
    facts_before = list(session.consult["facts"])
    asked_before = list(session.consult["asked"])
    drain(session)
    calls = Counter()
    original_agent = RunContext.agent
    original_dispatch = consult_module.dispatch_tool

    async def agent(ctx, **kwargs):
        calls[kwargs["label"]] += 1
        assert kwargs["label"] != "consult_identify", "跳过不能再问一次模型是否需要追问"
        return await original_agent(ctx, **kwargs)

    def dispatch(current, call):
        calls[call.name] += 1
        return original_dispatch(current, call)

    monkeypatch.setattr(RunContext, "agent", agent)
    monkeypatch.setattr(consult_module, "dispatch_tool", dispatch)
    response = await api.post(f"/api/session/{session.id}/consult/answer", json={
        "skip_clarification": True, "facts": draft, "expected_round": 1,
    })
    assert response.status_code == 200
    assert (await finish(session))["status"] == "answered"
    assert session.consult["facts"] == facts_before + ([draft] if draft else [])
    assert session.consult["asked"] == asked_before
    assert session.consult["rounds"] == 1
    assert session.consult["max_rounds"] == 3
    assert session.consult["skipped_clarification"] is True
    assert session.consult["issue"]["clarify_questions"] == []
    assert all("尚未确认" in item for item in session.consult["assumptions"])
    assert "用户主动跳过" in "；".join(session.limitations)
    assert "已达上限" not in "；".join(session.limitations)
    assert calls["search_statutes"] == calls["search_cases"] == 1
    events = drain(session)
    assert not any(event["event"] == "clarify" for event in events)
    assert any(event["event"] == "gate" for event in events), "主动跳过不能跳过引用核验"
    assert events[-1]["event"] == "done"
    assert session.consult["passed"]
    assert session.topic == QUESTION


@pytest.mark.parametrize("payload", [
    {"skip_clarification": "true"}, {"skip_clarification": 1}, {"skip_clarification": None},
    {"skip_clarification": []}, {"facts": 123}, {"facts": None},
    {"facts": "补充事实", "expected_round": "1"}, {"facts": "补充事实", "expected_round": True},
    {"facts": "补充事实", "expected_round": 0}, {"facts": "补充事实", "expected_round": None},
])
async def test_answer_rejects_invalid_control_parameters_without_changing_state(api, payload):
    session = await start(api)
    before = copy.deepcopy(session.consult)
    tasks_before = len(session.run_tasks)
    response = await api.post(f"/api/session/{session.id}/consult/answer", json=payload)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"
    assert session.consult == before
    assert len(session.run_tasks) == tasks_before


@pytest.mark.parametrize("payload", [{}, {"skip_clarification": False}, {"facts": "  "}])
async def test_empty_normal_answer_remains_invalid(api, payload):
    session = await start(api)
    response = await api.post(f"/api/session/{session.id}/consult/answer", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "empty_input"
    assert session.consult["rounds"] == 1


async def test_duplicate_answer_and_skip_for_old_round_cannot_answer_the_next_question(api):
    session = await start(api)
    url = f"/api/session/{session.id}/consult/answer"
    payload = {"facts": "三千元", "expected_round": 1}
    assert (await api.post(url, json=payload)).status_code == 200
    await finish(session)
    assert session.consult["rounds"] == 2
    before = copy.deepcopy(session.consult)
    assert (await api.post(url, json=payload)).status_code == 400
    assert (await api.post(url, json={"skip_clarification": True, "expected_round": 1})).status_code == 400
    assert session.consult == before


async def test_skip_only_allowed_while_awaiting_and_new_question_resets_skip(api):
    sid = (await api.post("/api/session", json={"branch": "consult"})).json()["session_id"]
    url = f"/api/session/{sid}/consult/answer"
    assert (await api.post(url, json={"skip_clarification": True})).status_code == 400
    await api.post(f"/api/session/{sid}/message", json={"text": QUESTION})
    session = server.STORE.get(sid)
    await finish(session)
    assert (await api.post(url, json={"skip_clarification": True})).status_code == 200
    await finish(session)
    assert (await api.post(url, json={"skip_clarification": True})).status_code == 400
    response = await api.post(f"/api/session/{sid}/message", json={"text": "单位拖欠工资怎么办"})
    assert response.status_code == 200
    await finish(session)
    assert not session.consult.get("skipped_clarification")
    assert not session.consult.get("assumptions")
    assert session.consult["facts"] == ["单位拖欠工资怎么办"]
    assert session.consult["rounds"] == 1
    assert not any("主动跳过" in limitation for limitation in session.limitations)


async def test_skip_failure_retry_preserves_skip_and_draft_without_reidentifying(api, monkeypatch):
    session = await start(api)
    original_agent = RunContext.agent
    original_dispatch = consult_module.dispatch_tool
    calls = Counter()

    async def agent(ctx, **kwargs):
        label = kwargs["label"]
        calls[label] += 1
        assert label != "consult_identify"
        if label == "consult_answer" and calls[label] == 1:
            raise ApiError("model_unavailable")
        return await original_agent(ctx, **kwargs)

    def dispatch(current, call):
        calls[call.name] += 1
        return original_dispatch(current, call)

    monkeypatch.setattr(RunContext, "agent", agent)
    monkeypatch.setattr(consult_module, "dispatch_tool", dispatch)
    draft = "房屋已经交付，我还在居住，租约仍在期限内。"
    response = await api.post(f"/api/session/{session.id}/consult/answer", json={
        "skip_clarification": True, "facts": draft, "expected_round": 1,
    })
    assert response.status_code == 200
    assert (await finish(session))["error"] == "model_unavailable"
    assert session.can_retry
    assumptions = list(session.consult["assumptions"])
    response = await api.post(f"/api/session/{session.id}/retry", json={})
    assert response.status_code == 200
    assert (await finish(session))["status"] == "answered"
    assert session.consult["facts"] == [QUESTION, draft]
    assert session.consult["rounds"] == 1
    assert session.consult["skipped_clarification"] is True
    assert session.consult["assumptions"] == assumptions
    assert calls["search_cases"] == calls["search_statutes"] == 1
    assert calls["consult_answer"] == 2
    assert not any("已达上限" in limitation for limitation in session.limitations)


async def test_skip_keeps_global_concurrency_limit_and_does_not_consume_facts(api, monkeypatch):
    session = await start(api)
    before = copy.deepcopy(session.consult)
    tasks_before = len(session.run_tasks)
    monkeypatch.setattr(server.STORE, "max_concurrent", 0)
    response = await api.post(f"/api/session/{session.id}/consult/answer", json={
        "skip_clarification": True, "facts": "尚未提交的补充", "expected_round": 1,
    })
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "concurrency_queued"
    assert session.consult == before
    assert len(session.run_tasks) == tasks_before


def test_identify_prompt_requires_single_prioritized_question_and_dynamic_early_exit():
    prompt = build_consult_identify_prompt(Session(branch="consult"), [QUESTION, "已经签了书面合同"], 1, 3)
    assert "只能有 1 个问题" in prompt
    assert "最可能改变处理方向" in prompt
    assert "不必问满轮数" in prompt
    assert "用户刚补充的回答重新判断" in prompt
    assert "合并进这一项" in prompt
