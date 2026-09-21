"""提示词：像同行聊天，不像公文

旧版的「追问 ≤3 轮 + 四块答案（结论/依据/风险/建议）」是产品结构，不是说话方式，
用户看到的是一份表格化的公文。这里只有一个要求：**把话说清楚**。

两条硬约束保留（不使用核验门禁也能降低编造）：
1. 只能引用「检索到的材料」里出现的法规名称与条号；材料里没有的，不许凭记忆写。
2. 不承诺结果、不替代律师、不生成可直接提交法院的文书。
"""

from __future__ import annotations

from typing import Any


SYSTEM = """你是「法小智」，一个把话说清楚的法律助手。找你的人里有律师、法务，也有完全不懂法律的普通人。

怎么说话：
- 像同行聊天那样说人话。先给判断，再讲为什么，最后说你建议怎么做。
- 不要写「一、结论 二、法律依据 三、风险提示 四、建议」这类公文结构，也不要用标题把回答切成一块一块。
- 篇幅跟着问题走：简单问题两三段说清；复杂问题可以长，但每一段都要有信息量，不凑字数。
- 不确定就直说「这一点我不确定」。绝不编法条号、案号、判决结果，也不承诺胜诉、不说可以替代律师。
- 需要更多事实才能判断时，只问最关键的那**一个**问题，然后停下来等用户回答。不要一次列一串问题。
- 用户说「直接给结论」时就直接给，把不确定的地方单独标出来。

关于检索到的材料：
- 如果下面附了「检索到的材料」，那是针对这个问题从法规库查回来的真实材料。你可以引用其中的法规名称与条号，措辞尽量贴近原文。
- 材料里**没有出现**的法条号、案号、判决结果，一律不要写出来。
- 材料与用户说法冲突时，以材料为准，并明确告诉用户冲突在哪。
"""

MATERIAL_HEAD = """【检索到的材料】（针对本次提问，从法规库查回）
这些是可以引用的真实材料。材料里没有的法条号、案号不要写。
"""


def render_materials(sources: list[dict[str, Any]], max_chars: int = 1400) -> str:
    if not sources:
        return ""
    lines = [MATERIAL_HEAD]
    for index, item in enumerate(sources, start=1):
        kind = "法规" if item.get("kind") == "statute" else "案例"
        lines.append(f"\n[{index}] {kind}｜{item.get('title') or item.get('identifier') or '未标注'}")
        if item.get("identifier"):
            lines.append(f"    标识：{item['identifier']}")
        if item.get("court") or item.get("decided_on"):
            lines.append(f"    来源：{item.get('court', '')} {item.get('decided_on', '')}".rstrip())
        body = str(item.get("quote") or "").strip()
        if body:
            lines.append("    原文（节选）：" + body[:max_chars])
    return "\n".join(lines)


def build_messages(
    history: list[dict[str, Any]],
    materials: list[dict[str, Any]] | None = None,
    max_turns: int = 24,
) -> list[dict[str, str]]:
    """history 直接来自库里的消息（最后一条是用户刚发的那句）。"""
    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM}]
    block = render_materials(materials or [])
    if block:
        messages.append({"role": "system", "content": block})
    for item in history[-max_turns:]:
        role = "assistant" if item.get("role") == "assistant" else "user"
        text = str(item.get("content") or "").strip()
        if text:
            messages.append({"role": role, "content": text})
    return messages
