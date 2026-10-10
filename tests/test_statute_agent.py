"""法律检索 Agent 的两条底线：可追问，模型不能凭空生成条文。"""

import asyncio

from app import statute_agent


class _Config:
    model_provider = "deepseek"

    def mcp_endpoints(self):
        return {}


def test_planner_only_returns_real_search_results(monkeypatch):
    async def fake_plan(*_args, **_kwargs):
        return {
            "issue": "虚构法规咨询",
            "queries": ["不存在的法律"],
            "need_clarification": False,
            "clarifying_question": "",
            "assumptions": [],
            "applicable_at": "",
        }

    monkeypatch.setattr(statute_agent, "get_config", lambda: _Config())
    monkeypatch.setattr(statute_agent, "_PlanningContext", lambda _cfg: object())
    monkeypatch.setattr(statute_agent, "agent_loop", fake_plan)
    monkeypatch.setattr(statute_agent, "search", lambda *_args, **_kwargs: {"items": []})

    result = asyncio.run(statute_agent.search_with_agent({"question": "请查不存在的法律"}))
    assert result["status"] == "completed"
    assert result["items"] == []
    assert "未找到" in result["notice"]


def test_clarification_is_bounded_and_date_must_come_from_user(monkeypatch):
    async def fake_plan(*_args, **_kwargs):
        return {
            "issue": "劳动合同解除",
            "queries": ["试用期 解除"],
            "need_clarification": True,
            "clarifying_question": "辞退理由是什么？",
            "assumptions": [],
            "applicable_at": "2020-01-01",  # 模型臆测，用户并未提供
        }

    monkeypatch.setattr(statute_agent, "get_config", lambda: _Config())
    monkeypatch.setattr(statute_agent, "_PlanningContext", lambda _cfg: object())
    monkeypatch.setattr(statute_agent, "agent_loop", fake_plan)
    monkeypatch.setattr(statute_agent, "search", lambda *_args, **_kwargs: {"items": []})

    first = asyncio.run(statute_agent.search_with_agent({"question": "公司试用期辞退我"}))
    assert first["status"] == "needs_clarification"
    continued = asyncio.run(statute_agent.search_with_agent({"question": "公司试用期辞退我", "skip_clarification": True}))
    assert continued["status"] == "completed"
    assert continued["applicable_at"] == ""

    historical = statute_agent._local_hit(
        {"title": "某法", "no": "第一条", "text": "第一条 原文", "status_code": "valid", "effective_date": "2010-01-01"},
        "某法",
        "2020-01-01",
    )
    assert historical["citation_checked"] is False
    assert "历史版本" in historical["check_note"]
