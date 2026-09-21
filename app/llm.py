"""模型客户端：真实 provider（DeepSeek，OpenAI 兼容 + function calling）与离线 stub。

原则（PRD 3.1.3）：模型不可用时**不产出半成品**；宁可返回「接口失败」。
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from dataclasses import dataclass, field
from typing import Any

from .config import Config
from .errors import ApiError
from .models import Status
from .schemas import (
    CASE_ELEMENT_SCHEMA,
    CONSULT_ANSWER_SCHEMA,
    CONSULT_IDENTIFY_SCHEMA,
    CONTRACT_CLAUSES_SCHEMA,
    POOL_SCHEMA,
    SYNTHESIS_SCHEMA,
    risk_items_schema,
    validate,
)

LOG = logging.getLogger("faxiaozhi.llm")


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    finish_reason: str = ""


_SOURCE_BLOCK = re.compile(r"<<<SOURCE\n(.*?)\nSOURCE>>>", re.S)
_SOURCE_BLOCK_ID = re.compile(r"<<<SOURCE id=(\S+)\n(.*?)\nSOURCE>>>", re.S)
_SOURCE_ID = re.compile(r"来源编号：(\S+)")
_IDENTIFIER = re.compile(r"引用标识：(.+)")


class BaseProvider:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.max_tokens = int(cfg.get("model.max_tokens", 4000))
        self.timeout = int(cfg.get("model.timeout_seconds", 120))
        self.max_retries = int(cfg.get("model.max_retries", 3))

    async def chat(self, **kwargs: Any) -> LLMResponse:      # pragma: no cover - 接口
        raise NotImplementedError


# ------------------------------------------------------------------ 离线 stub


class StubProvider(BaseProvider):
    """确定性假模型：走真实的 tool_use 结构，行为可断言。**不冒充真实模型。**"""

    def __init__(self, cfg: Config, scenario: str = "clean") -> None:
        super().__init__(cfg)
        self.scenario = scenario or "clean"

    async def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        schema: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
        **_ignored: Any,
    ) -> LLMResponse:
        meta = meta or {}
        label = str(meta.get("label", ""))
        prompt = _last_user_text(messages)
        tool_results = _tool_results(messages)

        if label == "retrieve":
            if not tool_results:
                return LLMResponse(
                    tool_calls=[
                        ToolCall(id="call_cases", name="search_cases", arguments=_search_args(prompt)),
                        ToolCall(
                            id="call_statutes",
                            name="search_statutes",
                            arguments={"query": _topic_of(prompt), "applicable_at": ""},
                        ),
                    ]
                )
            return LLMResponse(text=_stub_pool(tool_results))

        if label.startswith("extract:"):
            return LLMResponse(text=_stub_element(prompt, self.scenario))

        if label == "synthesize":
            return LLMResponse(text=_stub_synthesis(prompt, self.scenario))

        if label == "consult_identify":
            return LLMResponse(text=_stub_consult_identify(prompt))

        if label.startswith("consult_answer"):
            if self.scenario == "consult_bad_schema":
                # 专用于回归：模型输出不是合法 JSON（2-5 真实冒烟 C3 实测过的情形）
                return LLMResponse(text="这不是一段 JSON")
            # 改写那一次不再输出越界表述，否则门禁永远过不了（与真实链路的预期一致）
            return LLMResponse(
                text=_stub_consult_answer(
                    prompt, self.scenario, allow_overreach=label == "consult_answer"
                )
            )

        if label == "contract_clauses":
            return LLMResponse(text=_stub_contract_clauses(prompt))

        if label.startswith("contract_risk"):
            return LLMResponse(
                text=_stub_contract_risk(
                    prompt, self.scenario, allow_overreach=label == "contract_risk"
                )
            )

        return LLMResponse(text="")


def _last_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user" and isinstance(message.get("content"), str):
            return message["content"]
    return ""


def _tool_results(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") == "tool":
            try:
                out.append(json.loads(message.get("content") or "{}"))
            except json.JSONDecodeError:
                continue
    return out


def _topic_of(prompt: str) -> str:
    match = re.search(r"议题[:：]\s*(.+)", prompt)
    return match.group(1).strip()[:60] if match else "类案检索"


def _search_args(prompt: str) -> dict[str, Any]:
    args: dict[str, Any] = {"expression": _topic_of(prompt)}
    for key, pattern in (
        ("cause", r"案由[:：]\s*(\S+)"),
        ("court", r"法院[:：]\s*(\S+)"),
        ("level", r"层级[:：]\s*(\S+)"),
        ("region", r"地域[:：]\s*(\S+)"),
        ("since", r"起始日期[:：]\s*(\S+)"),
    ):
        match = re.search(pattern, prompt)
        if match:
            args[key] = match.group(1)
    return args


def _stub_pool(tool_results: list[dict[str, Any]]) -> str:
    candidates: list[dict[str, Any]] = []
    applied: list[str] = []
    ignored: list[str] = []
    for result in tool_results:
        for source in result.get("sources", []):
            if str(source.get("kind")) != "case":
                continue
            candidates.append(
                {
                    "source_id": source.get("source_id", ""),
                    "identifier": source.get("identifier", ""),
                    "title": source.get("title", ""),
                    "court": source.get("court") or "",
                    "level": source.get("level") or "",
                    "region": source.get("region") or "",
                    "decided_on": source.get("decided_on") or "",
                    "status": source.get("status", "ok"),
                }
            )
        applied += list(result.get("applied_conditions", []) or [])
        ignored += list(result.get("ignored_conditions", []) or [])
    payload = {
        "queries": ["（离线 stub）" + (_topic_of("") or "类案检索")],
        "conditions_echo": {"applied": sorted(set(applied)), "ignored": sorted(set(ignored))},
        "candidates": candidates,
    }
    validate(POOL_SCHEMA, payload)
    return json.dumps(payload, ensure_ascii=False)


def _stub_element(prompt: str, scenario: str) -> str:
    block = _SOURCE_BLOCK.search(prompt)
    text = (block.group(1) if block else "").strip()
    sid = (_SOURCE_ID.search(prompt) or [None, "unknown"])[1]  # type: ignore[index]
    identifier = (_IDENTIFIER.search(prompt) or [None, ""])[1].strip()  # type: ignore[index]
    sentences = [s for s in re.split(r"[。；\n]", text) if len(s.strip()) >= 10]
    basis_quote = (sentences[0] + "。") if sentences else text[:40]
    if scenario == "bad_quote":
        basis_quote = "本案经审理认为，该合同条款自始无效，被告应当全额返还。"
    payload = {
        "case_id": sid,
        "facts": text[:150],
        "claim": (sentences[1] if len(sentences) > 1 else text[:80]),
        "issues": [identifier or "争议焦点待补充"],
        "holding": (sentences[2] if len(sentences) > 2 else text[:80]),
        "result": "支持（见裁判原文）",
        "basis": [basis_quote],
        "stance": _stance_of(sid),
        "missing_fields": [],
    }
    validate(CASE_ELEMENT_SCHEMA, payload)
    return json.dumps(payload, ensure_ascii=False)


def _first_sentence(text: str) -> str:
    sentences = [s.strip() for s in re.split(r"[。；\n]", text or "") if len(s.strip()) >= 10]
    return (sentences[0] + "。") if sentences else ""


def _stance_of(sid: str) -> str:
    table = ["support", "oppose", "other", "support", "support", "oppose", "other", "support"]
    return table[sum(ord(c) for c in sid) % len(table)]


def _stub_synthesis(prompt: str, scenario: str) -> str:
    entries = re.findall(r"- 来源编号：(\S+) \| 引用标识：(.+)", prompt)
    blocks = {sid: text for sid, text in _SOURCE_BLOCK_ID.findall(prompt)}
    conclusions: list[dict[str, Any]] = []
    for index, (sid, identifier) in enumerate(entries[:3]):
        quote = _first_sentence(blocks.get(sid, ""))
        if not quote:
            quote = "（离线 stub 无法取得逐字原文）" + identifier[:20]
        if scenario == "bad_quote":
            quote = "本条规定，当事人一方明确表示不履行主要债务的，对方可以解除合同。"
        conclusions.append(
            {
                "text": f"结论 {index + 1}：就「{identifier}」，本次确认样本中的裁判观点可以归纳为一致方向。",
                "citation_source_ids": [sid],
                "citations": [{"source_id": sid, "identifier": identifier.strip(), "quote": quote}],
                "similarities": [],
                "differences": [],
            }
        )
    if scenario == "overreach":
        conclusions.append(
            {
                "text": "总体而言，全国法院对此类争议的支持率高达 87%，胜诉率明显偏高。",
                "citation_source_ids": [entries[0][0]] if entries else ["s0"],
                "citations": [
                    {
                        "source_id": entries[0][0] if entries else "s0",
                        "identifier": entries[0][1].strip() if entries else "x",
                        "quote": "（离线 stub 引用）越界示例",
                    }
                ],
            }
        )
    payload = {
        "conclusions": conclusions,
        "opposing_paths": [],
        "key_variables": ["合同履行情况", "当事人举证能力"],
    }
    if not conclusions:
        payload["conclusions"] = []
    validate(SYNTHESIS_SCHEMA, payload)
    return json.dumps(payload, ensure_ascii=False)


def _stub_consult_identify(prompt: str) -> str:
    """离线 stub 的「问题识别」（2-5）。

    确定性启发：用户提供的信息很短（< 60 字）时 → 每轮选一个尚未问过的问题；
    这样测试能稳定地覆盖「追问 / 到上限 / 事实完整不追问」三条路径。
    """
    head = prompt.split("【追问进度】")[0]
    block = head.split("【用户提供的信息", 1)[-1]
    facts = [
        line.split(". ", 1)[1].strip()
        for line in block.splitlines()
        if re.match(r"^\d+\. ", line)
    ]
    need = sum(len(item) for item in facts) < 60
    candidates = ["请问涉案金额是多少？", "请问事情发生在什么时候？", "双方是否有书面约定？"]
    asked_heading = "【已经问过的问题（不要重复）】"
    asked_block = prompt.split(asked_heading, 1)[-1] if asked_heading in prompt else ""
    next_question = next((question for question in candidates if question not in asked_block), None)
    payload: dict[str, Any] = {
        "legal_relation": "（离线 stub）示例法律关系",
        "disputes": ["示例争议焦点一", "示例争议焦点二"],
        "given_facts": facts[:3] or ["（离线 stub 未识别到事实）"],
        "missing_facts": ["金额", "时间", "是否书面"],
        "possible_claims": ["示例主张"],
        "need_clarify": need,
        "clarify_questions": (
            [next_question]
            if need and next_question
            else []
        ),
        "assumptions": ["（离线 stub）假设未提及的事实不影响结论"] if need else [],
    }
    validate(CONSULT_IDENTIFY_SCHEMA, payload)
    return json.dumps(payload, ensure_ascii=False)


def _stub_consult_answer(prompt: str, scenario: str, allow_overreach: bool = True) -> str:
    """离线 stub 的「咨询解答」（2-5）：逐字引用 SOURCE 块，保证能过引用门禁。"""
    entries = re.findall(
        r"- 来源编号：(\S+) \| 类型：\S+ \| 引用标识：(.+?)(?: \| 效力状态：.*)?$",
        prompt,
        re.M,
    )
    blocks = {sid: text for sid, text in _SOURCE_BLOCK_ID.findall(prompt)}
    conclusions: list[dict[str, Any]] = []
    for index, (sid, identifier) in enumerate(entries[:3]):
        quote = _first_sentence(blocks.get(sid, ""))
        if not quote:
            quote = "（离线 stub 无法取得逐字原文）" + identifier[:20]
        if scenario == "bad_quote":
            quote = "本条规定，当事人一方明确表示不履行主要债务的，对方可以解除合同。"
        conclusions.append(
            {
                "text": f"依据{identifier.strip()}，就本次问题可以得出示例结论 {index + 1}。",
                "citation_source_ids": [sid],
                "citations": [
                    {"source_id": sid, "identifier": identifier.strip(), "quote": quote}
                ],
            }
        )
    if allow_overreach and scenario == "overreach" and entries:
        sid, identifier = entries[0]
        conclusions.append(
            {
                "text": "你一定能赢，全国法院支持率高达 87%。",
                "citation_source_ids": [sid],
                "citations": [
                    {
                        "source_id": sid,
                        "identifier": identifier.strip(),
                        "quote": _first_sentence(blocks.get(sid, "")) or "越界示例",
                    }
                ],
            }
        )
    payload: dict[str, Any] = {
        "conclusions": conclusions,
        "uncertainties": ["（离线 stub）示例不确定项"] if conclusions else [],
        "next_steps": ["（离线 stub）示例下一步"],
        "insufficient": (
            "" if conclusions else "未检索到直接规定；建议放宽检索条件或补充事实后重试。"
        ),
    }
    validate(CONSULT_ANSWER_SCHEMA, payload)
    return json.dumps(payload, ensure_ascii=False)


_CLAUSE_BLOCK = re.compile(r"<<<CLAUSE id=(\S+) heading=(.*?)\n(.*?)\nCLAUSE>>>", re.S)


def _first_clause_anchor(body: str) -> str:
    """从条款正文里取一个足够长的逐字片段当锚点（离线 stub 用）。"""
    for piece in re.split(r"[。；;\n]", body or ""):
        piece = piece.strip()
        if len(piece) >= 6:
            return piece[:60]
    return (body or "").strip()[:30]


def _stub_contract_clauses(prompt: str) -> str:
    """离线 stub 的合同条款命名（2-6）：只对代码给出的 clause_id 逐条补标题与摘要。"""
    clauses = []
    for clause_id, heading, body in _CLAUSE_BLOCK.findall(prompt):
        head = heading.strip()
        if head in ("（无）", "（无编号段落）", ""):
            head = "条款"
        clauses.append(
            {
                "clause_id": clause_id,
                "heading": head[:16],
                "summary": (_first_sentence(body) or "（离线 stub 摘要）")[:60],
            }
        )
    payload: dict[str, Any] = {"parties": {}, "clauses": clauses}
    validate(CONTRACT_CLAUSES_SCHEMA, payload)
    return json.dumps(payload, ensure_ascii=False)


def _stub_contract_risk(prompt: str, scenario: str, allow_overreach: bool = True) -> str:
    """离线 stub 的合同风险识别（2-6）：锚点取条款原文，依据取 SOURCE 块。"""
    blocks = {sid: text for sid, text in _SOURCE_BLOCK_ID.findall(prompt)}
    entries = re.findall(
        r"- 来源编号：(\S+) \| 类型：\S+ \| 引用标识：(.+?)(?: \| 效力状态：.*)?$", prompt, re.M
    )
    sid = entries[0][0] if entries else ""
    identifier = entries[0][1].strip() if entries else ""
    quote = _first_sentence(blocks.get(sid, "")) if sid else ""

    kinds = ["illegal", "commercial", "wording"]
    levels = ["high", "medium", "low"]
    risks: list[dict[str, Any]] = []
    for index, (clause_id, _heading, body) in enumerate(_CLAUSE_BLOCK.findall(prompt)[:3]):
        anchor = _first_clause_anchor(body)
        if scenario == "contract_bad_anchor":
            anchor = "合同里根本没有这一句话用来测试定位失败"
        risk: dict[str, Any] = {
            "kind": kinds[index % len(kinds)],
            "level": levels[index % len(levels)],
            "clause_id": clause_id,
            "anchor_text": anchor,
            "issue": f"（离线 stub）示例风险 {index + 1}：该条款的约定可能对当事人不利。",
            "suggestion": "（离线 stub）建议补充明确约定（须经律师审定）",
            "confidence": "medium",
        }
        if scenario == "bad_quote":
            quote = "本条规定，当事人一方明确表示不履行主要债务的，对方可以解除合同。"
        if sid and quote and not (scenario == "contract_no_basis" and index == 0):
            risk["basis"] = [
                {"source_id": sid, "identifier": identifier, "quote": quote}
            ]
        if scenario == "contract_no_mark" and index == 1:
            risk["suggestion"] = "（离线 stub）这条建议故意不带审定标注"
        risks.append(risk)

    if allow_overreach and scenario == "overreach" and risks:
        risks[0]["suggestion"] = "可直接签署，无需律师审阅。"
        risks[0]["issue"] = "这条没有风险，不存在任何风险。"

    payload_risks: dict[str, Any] = {"risks": risks}
    validate(risk_items_schema(kinds, levels), payload_risks)
    return json.dumps(payload_risks, ensure_ascii=False)


# ------------------------------------------------------------------ 真实 provider


class DeepSeekProvider(BaseProvider):
    def __init__(self, cfg: Config) -> None:
        super().__init__(cfg)
        self.model_id = cfg.model_id
        self.base_delay = float(cfg.get("model.base_delay_seconds", 0.5))
        try:
            from openai import AsyncOpenAI

            self.client = AsyncOpenAI(
                api_key=cfg.model_api_key,
                base_url=cfg.model_base_url,
                timeout=self.timeout,
                max_retries=0,            # 重试由我们自己控制（可断言、可退避）
            )
        except Exception as exc:          # SDK 初始化失败的兜底
            LOG.exception("模型 SDK 初始化失败")
            raise ApiError("model_unavailable", f"模型 SDK 初始化失败：{type(exc).__name__}") from exc

    async def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        schema: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
        temperature: float = 0.2,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model_id,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if schema:
            payload["response_format"] = {"type": "json_object"}

        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = await self.client.chat.completions.create(**payload)
                return _to_llm_response(response)
            except Exception as exc:      # 超时 / 429 / 5xx / 网络
                last_error = exc
                kind = type(exc).__name__
                if attempt < self.max_retries:
                    delay = self.base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.2)
                    LOG.warning("模型调用失败（%s），第 %s 次重试", kind, attempt)
                    await asyncio.sleep(delay)
                    continue
        LOG.error("模型调用最终失败：%s", type(last_error).__name__)
        raise ApiError("model_unavailable") from last_error


def _to_llm_response(response: Any) -> LLMResponse:
    choice = response.choices[0]
    message = choice.message
    text = (message.content or "").strip()

    tool_calls: list[ToolCall] = []
    for call in getattr(message, "tool_calls", None) or []:
        raw = call.function.arguments or "{}"
        try:
            arguments = json.loads(raw)
        except json.JSONDecodeError:
            arguments = {"_raw": raw}
        tool_calls.append(ToolCall(id=call.id, name=call.function.name, arguments=arguments))

    usage = {}
    if getattr(response, "usage", None) is not None:
        usage = {
            "in": int(response.usage.prompt_tokens or 0),
            "out": int(response.usage.completion_tokens or 0),
        }

    if not text and not tool_calls:
        # 空响应：重试一次由上层承担（PRD 3.1.3）；此处直接报错，不产出半成品
        raise ApiError("model_unavailable", "模型返回空响应，未产出任何内容。")

    if choice.finish_reason == "length":
        LOG.warning("模型输出被截断（max_tokens）")

    return LLMResponse(text=text, tool_calls=tool_calls, usage=usage, finish_reason=choice.finish_reason or "")


def build_provider(cfg: Config, scenario: str = "clean") -> BaseProvider:
    if cfg.model_provider == "deepseek":
        return DeepSeekProvider(cfg)
    return StubProvider(cfg, scenario=scenario)


# 保留给工具复用（失败状态与 PRD 降级矩阵对齐）
MODEL_FAILURE_STATUS = Status.INTERFACE_ERROR
