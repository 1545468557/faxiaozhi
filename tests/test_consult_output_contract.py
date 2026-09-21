"""咨询结构契约回归：复现字符串结论、缺 text、空对象与截断响应。"""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from app.config import get_config
from app.errors import ApiError
from app.llm import LLMResponse
from app.loop import agent_loop
from app.prompts import build_consult_answer_prompt
from app.schemas import CONSULT_ANSWER_SCHEMA, validate
from app.session import Session


def answer():
    return {
        "conclusions": [{
            "text": "需要结合已经取得的材料判断。",
            "citation_source_ids": ["source_1"],
            "citations": [{"source_id": "source_1", "identifier": "测试来源", "quote": "测试原文"}],
        }],
        "uncertainties": ["仍需核实时间"],
        "next_steps": ["保留相关材料"],
        "insufficient": "",
    }


class SequenceProvider:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []

    async def chat(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        return next(self.responses)


def context(provider):
    return SimpleNamespace(
        session=Session(branch="consult"), provider=provider, phase_name="解答",
        system_prompt=lambda: "依据材料回答。", count_step=lambda: None,
        record_usage=lambda _: None,
    )


async def generate(provider):
    return await agent_loop(
        context(provider), label="consult_answer", prompt="仅使用测试材料。",
        schema=CONSULT_ANSWER_SCHEMA, tools=[],
    )


def test_prompt_separates_object_and_string_arrays():
    prompt = build_consult_answer_prompt(Session(branch="consult"))
    assert "数组元素一律是字符串" not in prompt
    example = json.loads(prompt.split("格式示例（仅说明结构，占位文字不能作为真实引用）：", 1)[1])
    assert validate(CONSULT_ANSWER_SCHEMA, example) == example
    assert isinstance(example["conclusions"][0], dict)
    assert isinstance(example["conclusions"][0]["citations"][0], dict)


@pytest.mark.asyncio
async def test_real_failure_shapes_remain_retryable_instead_of_becoming_empty_success(monkeypatch):
    monkeypatch.setitem(get_config().raw.setdefault("limits", {}), "max_schema_retries", 1)
    first = answer()
    first["conclusions"] = ["一段普通文字"]
    second = answer()
    second["conclusions"][0].pop("text")
    provider = SequenceProvider(LLMResponse(text=json.dumps(first)), LLMResponse(text=json.dumps(second)))
    with pytest.raises(ApiError, match="text") as error:
        await generate(provider)
    assert error.value.code == "schema_invalid"
    assert len(provider.calls) == 2
    # 初次调用就得到完整约束，修正调用仍携带同一份定义。
    for call in provider.calls:
        encoded = call["messages"][0]["content"].split("不得为满足格式编造事实或引用：\n", 1)[1]
        assert json.loads(encoded) == CONSULT_ANSWER_SCHEMA
    assert "$.conclusions[0]" in provider.calls[1]["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_missing_text_can_be_corrected_without_changing_citations():
    invalid = answer()
    invalid["conclusions"][0].pop("text")
    expected = answer()
    provider = SequenceProvider(LLMResponse(text=json.dumps(invalid)), LLMResponse(text=json.dumps(expected)))
    assert await generate(provider) == expected
    assert len(provider.calls) == 2


@pytest.mark.asyncio
async def test_truncated_even_valid_json_requires_complete_regeneration():
    expected = answer()
    provider = SequenceProvider(
        LLMResponse(text=json.dumps(expected), finish_reason="length"),
        LLMResponse(text=json.dumps(expected), finish_reason="stop"),
    )
    assert await generate(provider) == expected
    assert "被截断" in provider.calls[1]["messages"][-1]["content"]


def test_empty_payload_is_a_format_failure_not_an_answer():
    with pytest.raises(ApiError, match="conclusions"):
        validate(CONSULT_ANSWER_SCHEMA, {})


def test_single_objects_keep_exact_text_and_citations_when_wrapped_as_arrays():
    expected = answer()
    raw = copy.deepcopy(expected)
    raw["conclusions"] = raw["conclusions"][0]
    raw["conclusions"]["citations"] = raw["conclusions"]["citations"][0]
    assert validate(CONSULT_ANSWER_SCHEMA, raw) == expected


def test_format_validation_never_invents_evidence_for_unreferenced_text():
    raw = answer()
    raw["conclusions"] = [{"text": "未提供依据的陈述"}]
    validated = validate(CONSULT_ANSWER_SCHEMA, raw)
    assert validated["conclusions"] == [{"text": "未提供依据的陈述"}]
    # 没有来源仍交给引用门禁处理，不能通过结构归一补造 citation。
    from app.workflows.consult import _screen

    session = Session(branch="consult")
    _screen(session, validated)
    assert not (session.structured_output or {}).get("_passed_conclusions")
