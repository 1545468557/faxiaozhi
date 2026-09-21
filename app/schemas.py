"""结构化输出 schema 与最小校验器（不引入 jsonschema 依赖）。

【补充】SYNTHESIS_SCHEMA 在 PRD 基础上增加 `citations`：PRD 的 `citation_source_ids`
只给编号，无法逐字核验（门禁 R5 需要模型给出的引用原文）。两者同时要求，
`citation_source_ids` 满足 PRD 原样约束，`citations` 让 R4/R5 可被真实核验。
"""

from __future__ import annotations

from typing import Any

from .errors import ApiError

CITATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["source_id", "identifier", "quote"],
    "properties": {
        "source_id": {"type": "string"},
        "identifier": {"type": "string"},
        "quote": {"type": "string"},
    },
    "additionalProperties": False,
}

POOL_SCHEMA: dict[str, Any] = {
    # 说明：**候选池由代码从依据池装配**（PRD 附录 A 第 3 条）。
    # 模型只负责「决定检索式并调用工具」以及回显条件生效情况，不负责抄写候选清单。
    "type": "object",
    "properties": {
        "queries": {"type": "array", "items": {"type": "string"}},
        "conditions_echo": {
            "type": "object",
            "properties": {
                "applied": {"type": "array", "items": {"type": "string"}},
                "ignored": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": True,
        },
        "notes": {"type": "string"},
        "candidates": {
            "type": "array",
            "items": {"type": "object", "additionalProperties": True},
        },
    },
    "additionalProperties": True,
}

CASE_ELEMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["case_id", "facts", "claim", "issues", "holding", "result", "basis"],
    "properties": {
        "case_id": {"type": "string"},
        "facts": {"type": "string"},
        "claim": {"type": "string"},
        "issues": {"type": "array", "items": {"type": "string"}},
        "holding": {"type": "string"},
        "result": {"type": "string"},
        "basis": {"type": "array", "items": {"type": "string"}},
        "stance": {"type": "string", "enum": ["support", "oppose", "other", "unknown"]},
        "missing_fields": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}

SYNTHESIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["conclusions", "opposing_paths", "key_variables"],
    "properties": {
        "conclusions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["text", "citation_source_ids"],
                "properties": {
                    "text": {"type": "string"},
                    "citation_source_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                    },
                    "citations": {"type": "array", "items": CITATION_SCHEMA, "minItems": 1},
                    "similarities": {"type": "array", "items": {"type": "string"}},
                    "differences": {"type": "array", "items": {"type": "string"}},
                },
                "additionalProperties": False,
            },
        },
        "opposing_paths": {"type": "array", "items": {"type": "object"}},
        "key_variables": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}

CONSULT_IDENTIFY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["legal_relation", "disputes", "given_facts", "missing_facts", "need_clarify"],
    "properties": {
        "legal_relation": {"type": "string"},
        "disputes": {"type": "array", "items": {"type": "string"}},
        "given_facts": {"type": "array", "items": {"type": "string"}},
        "missing_facts": {"type": "array", "items": {"type": "string"}},
        "possible_claims": {"type": "array", "items": {"type": "string"}},
        "need_clarify": {"type": "boolean"},
        "clarify_questions": {"type": "array", "items": {"type": "string"}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}

CONSULT_ANSWER_SCHEMA: dict[str, Any] = {
    # 说明（2-5，实测教训）：解答步骤的 schema **只管形状，不管实质**。
    # 实质由引用门禁把关：没有逐字引用的结论会被 R0 降级、不会展示、不阻止导出以外的输出。
    # 因此这里只要求 `text`，引用字段允许缺失、允许模型多带字段——
    # 否则一个多余的键就会让整次咨询硬失败（真实冒烟 C3 实测）。
    "type": "object",
    "required": ["conclusions", "uncertainties", "next_steps", "insufficient"],
    "properties": {
        "conclusions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["text"],
                "properties": {
                    "text": {"type": "string"},
                    "citation_source_ids": {"type": "array", "items": {"type": "string"}},
                    "citations": {"type": "array", "items": CITATION_SCHEMA},
                },
                "additionalProperties": True,
            },
        },
        "uncertainties": {"type": "array", "items": {"type": "string"}},
        "next_steps": {"type": "array", "items": {"type": "string"}},
        "insufficient": {"type": "string"},
    },
    "additionalProperties": True,
}

CONTRACT_CLAUSES_SCHEMA: dict[str, Any] = {
    # 2-6：条款切块由代码完成；模型只负责「这块讲什么」以及甲乙方名称
    "type": "object",
    "required": ["clauses"],
    "properties": {
        "parties": {
            "type": "object",
            "properties": {"party_a": {"type": "string"}, "party_b": {"type": "string"}},
            "additionalProperties": False,
        },
        "clauses": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["clause_id"],
                "properties": {
                    "clause_id": {"type": "string"},
                    "heading": {"type": "string"},
                    "summary": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
    },
    "additionalProperties": False,
}


def risk_items_schema(kinds: list[str], levels: list[str]) -> dict[str, Any]:
    """合同风险 schema（2-6）。

    `kind` / `level` 的**枚举来自 config**（`contract.risk.kinds/levels`），所以产品侧加一个
    风险类型不需要改代码；枚举校验由既有的结构校验器完成——取值越界会触发内置重试。

    `basis` 允许为空：找不到直接依据时**宁可没有依据**，也不得编造法条（C-02）。
    缺少依据的条目由工作流标记为「提示性风险」。
    """
    return {
        "type": "object",
        "required": ["risks"],
        "properties": {
            "risks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["kind", "level", "clause_id", "anchor_text", "issue"],
                    "properties": {
                        "kind": {"type": "string", "enum": list(kinds)},
                        "level": {"type": "string", "enum": list(levels)},
                        "clause_id": {"type": "string"},
                        "anchor_text": {"type": "string"},
                        "issue": {"type": "string"},
                        "basis": {"type": "array", "items": CITATION_SCHEMA},
                        "suggestion": {"type": "string"},
                        "confidence": {"type": "string", "enum": list(levels)},
                    },
                    "additionalProperties": False,
                },
            }
        },
        "additionalProperties": False,
    }


_TYPES = {"object": dict, "array": list, "string": str, "integer": int, "number": (int, float), "boolean": bool}


def validate(schema: dict[str, Any], value: Any, path: str = "$") -> Any:
    if not isinstance(schema, dict):
        return value

    expected = schema.get("type")
    # 良性形态归一：只改「形状」，不丢内容、不改语义（真实链路里模型经常写错形状）
    if expected == "array" and isinstance(value, str) and value.strip():
        value = [value.strip()]                      # issues="争点一；争点二"
    elif expected == "array" and isinstance(value, dict):
        if schema.get("items", {}).get("type") == "object":
            # 单个结论/引用对象只补数组外壳，不把原文与来源字段转换成字符串。
            value = [value]
        else:
            value = [f"{k}：{v}" for k, v in value.items() if str(v).strip()]
    elif expected == "string" and isinstance(value, list):
        value = "；".join(str(v) for v in value if str(v).strip())   # facts=[...]
    elif expected == "string" and isinstance(value, dict):
        value = "；".join(f"{k}：{v}" for k, v in value.items() if str(v).strip())
    if expected:
        py_types = _TYPES[expected]
        if expected in ("integer", "number") and isinstance(value, bool):
            raise ApiError("schema_invalid", f"{path} 类型应为 {expected}")
        if not isinstance(value, py_types):
            raise ApiError("schema_invalid", f"{path} 类型应为 {expected}，实际为 {type(value).__name__}")

    if "enum" in schema and value not in schema["enum"]:
        raise ApiError("schema_invalid", f"{path} 取值不在允许范围：{value!r}")

    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                raise ApiError("schema_invalid", f"{path} 缺少必填字段：{key}")
        properties = schema.get("properties", {})
        for key, item in list(value.items()):
            if key in properties:
                value[key] = validate(properties[key], item, f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                raise ApiError("schema_invalid", f"{path} 出现未声明字段：{key}")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise ApiError(
                "schema_invalid", f"{path} 至少需要 {schema['minItems']} 项（当前 {len(value)} 项）"
            )
        if "items" in schema:
            for index, item in enumerate(value):
                value[index] = validate(schema["items"], item, f"{path}[{index}]")

    return value


def validate_tool_args(schema: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    """工具入参校验（生产级要点：参数校验不能省）。"""
    try:
        validate(schema, args)
    except ApiError as exc:
        raise ValueError(exc.message) from exc
    return args
