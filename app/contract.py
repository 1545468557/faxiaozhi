"""合同切块与原文定位（阶段 2-6）。

**纯确定性代码，不调模型**。分工：
- 代码负责「切在哪里、原文偏移是多少」——这决定了风险条目能不能真的点回原文；
- 模型只负责「这块讲什么、有没有风险、依据是什么」。

一条硬约束：`full_text[clause.start:clause.end] == clause.text`。
所以本模块所有偏移都用**原文字符下标**，且只通过「收缩尾部空白」来修正 end，
绝不重新拼装文本（否则偏移会漂移，原文高亮就会点错位置）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: 条款标题：第X条 / 第X章 / 一、 / （一） / 1. / 1、
_HEADING = re.compile(
    r"(?m)^[ \t　]*("
    r"第[一二三四五六七八九十百零两0-9]+[条章]"
    r"|[一二三四五六七八九十]+、"
    r"|（[一二三四五六七八九十百零两0-9]+）"
    r"|\([一二三四五六七八九十百零两0-9]+\)"
    r"|[0-9]{1,3}[.、]"
    r")"
)

#: 风险类型 / 等级的中文标签（报告与界面共用一份，避免两处走偏）
RISK_KIND_LABELS: dict[str, str] = {
    "illegal": "违法",
    "commercial": "商业不利",
    "wording": "措辞不清",
}
RISK_LEVEL_LABELS: dict[str, str] = {"high": "高", "medium": "中", "low": "低"}

#: 当事人称谓（用于从首部推断甲乙方名称，给立场选择做默认值）
#: 注意用非捕获组：正则里只允许出现「名称」这一个捕获组，否则 group(1) 会拿到称谓本身
_PARTY_LABEL = {
    "party_a": re.compile(r"(?:甲方|出租方|出租人|买方|需方|发包人|委托人)"),
    "party_b": re.compile(r"(?:乙方|承租方|承租人|卖方|供方|承包人|受托人)"),
}
_NAME = r"[^\n，,；;：:（(]{2,40}"


@dataclass
class Clause:
    """一个可定位的条款块。"""

    clause_id: str
    heading: str
    text: str
    start: int
    end: int
    line: int
    #: 模型给的一句话摘要（可选；切块与偏移永远由代码维护）
    summary: str = ""

    def brief(self) -> dict[str, Any]:
        return {
            "clause_id": self.clause_id,
            "heading": self.heading,
            "text": self.text,
            "summary": self.summary,
            "start": self.start,
            "end": self.end,
            "line": self.line,
        }


@dataclass
class ClauseSet:
    clauses: list[Clause] = field(default_factory=list)
    #: 是否因为超出上限被截断（界面与报告必须显式说明，不得静默丢弃）
    truncated: bool = False

    def __len__(self) -> int:
        return len(self.clauses)

    def get(self, clause_id: str) -> Clause | None:
        for clause in self.clauses:
            if clause.clause_id == clause_id:
                return clause
        return None

    def brief(self) -> list[dict[str, Any]]:
        return [clause.brief() for clause in self.clauses]


def split_clauses(text: str, max_clauses: int = 200) -> ClauseSet:
    """按条款标题切块；没有标题时退化为按段落切分。偏移一律指向原文。"""
    body = text or ""
    if not body.strip():
        return ClauseSet()

    matches = list(_HEADING.finditer(body))
    clauses: list[Clause] = []
    if not matches:
        return _split_by_paragraphs(body, max_clauses)

    # 首个标题之前的内容（合同首部：标题、当事人、鉴于条款）单独成块
    if matches[0].start() > 0:
        end = _trim_end(body, 0, matches[0].start())
        if end > 0:
            clauses.append(_make_clause(body, 0, end, "合同首部", "c001"))

    for index, match in enumerate(matches):
        if len(clauses) >= max_clauses:
            return ClauseSet(clauses=clauses, truncated=True)
        start = match.start()
        raw_end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        end = _trim_end(body, start, raw_end)
        if end <= start:
            continue
        clauses.append(
            _make_clause(body, start, end, match.group(0).strip(), f"c{len(clauses) + 1:03d}")
        )
    return ClauseSet(clauses=clauses, truncated=len(clauses) >= max_clauses)


def _split_by_paragraphs(text: str, max_clauses: int) -> ClauseSet:
    clauses: list[Clause] = []
    for match in re.finditer(r"[^\n][\s\S]*?(?=\n[ \t　]*\n|\Z)", text):
        if len(clauses) >= max_clauses:
            return ClauseSet(clauses=clauses, truncated=True)
        start = match.start()
        end = _trim_end(text, start, match.end())
        if end <= start:
            continue
        clauses.append(_make_clause(text, start, end, "（无编号段落）", f"c{len(clauses) + 1:03d}"))
    return ClauseSet(clauses=clauses, truncated=len(clauses) >= max_clauses)


def _trim_end(text: str, start: int, end: int) -> int:
    while end > start and text[end - 1] in " \t\r\n\u3000":
        end -= 1
    return end


def _make_clause(text: str, start: int, end: int, heading: str, clause_id: str) -> Clause:
    chunk = text[start:end]
    line = text.count("\n", 0, start) + 1
    return Clause(clause_id=clause_id, heading=heading, text=chunk, start=start, end=end, line=line)


# ------------------------------------------------------------------ 锚点定位


def locate_anchor(text: str, clause: Clause, anchor: str) -> tuple[int, int] | None:
    """把模型给出的原文片段定位回**合同全文**的字符区间。

    先逐字找；找不到再退一步做「忽略空白」的宽松匹配（模型常把换行写成空格），
    仍然定位失败就返回 None —— 宁可标「定位失败」，也不给一个错的位置。
    """
    needle = (anchor or "").strip()
    if not needle:
        return None
    index = clause.text.find(needle)
    if index >= 0:
        return clause.start + index, clause.start + index + len(needle)
    loose = _find_ignoring_space(clause.text, needle)
    if loose is None:
        return None
    return clause.start + loose[0], clause.start + loose[1]


def _find_ignoring_space(text: str, needle: str) -> tuple[int, int] | None:
    """忽略空白与全角空格的匹配；返回在 text 中的原始区间。"""
    squeezed_chars: list[str] = []
    index_map: list[int] = []
    for position, char in enumerate(text):
        if char in " \t\r\n\u3000":
            continue
        squeezed_chars.append(char)
        index_map.append(position)
    target = [c for c in needle if c not in " \t\r\n\u3000"]
    if not target:
        return None
    haystack = "".join(squeezed_chars)
    found = haystack.find("".join(target))
    if found < 0:
        return None
    start = index_map[found]
    end = index_map[found + len(target) - 1] + 1
    return start, end


# ------------------------------------------------------------------ 当事人推断


def infer_parties(text: str) -> dict[str, str]:
    """从合同首部推断甲乙方名称，作为立场选择的默认值（**只是默认值，用户可改**）。"""
    head = (text or "")[:3000]
    parties: dict[str, str] = {}
    for key, label in _PARTY_LABEL.items():
        match = re.search(rf"{label.pattern}[^\n]{{0,10}}?[：:]\s*({_NAME})", head)
        if match:
            parties[key] = match.group(1).strip()
            continue
        # 没有冒号的写法：「甲方 某某公司」
        match = re.search(rf"{label.pattern}\s+({_NAME})", head)
        if match:
            parties[key] = match.group(1).strip()
    return parties


def clause_index_for_model(clause_set: ClauseSet, per_clause_limit: int | None = None) -> str:
    """给模型的条款清单（**不含偏移**，偏移由代码维护，不让模型碰）。

    `per_clause_limit`：单块送入的字数上限（命名步骤不需要全文，避免把上下文撞爆）。
    """
    lines: list[str] = []
    for clause in clause_set.clauses:
        body = clause.text if per_clause_limit is None else clause.text[:per_clause_limit]
        lines.append(f"<<<CLAUSE id={clause.clause_id} heading={clause.heading or '（无）'}")
        lines.append(body)
        lines.append("CLAUSE>>>")
    return "\n".join(lines)


__all__ = [
    "RISK_KIND_LABELS",
    "RISK_LEVEL_LABELS",
    "Clause",
    "ClauseSet",
    "clause_index_for_model",
    "infer_parties",
    "locate_anchor",
    "split_clauses",
]
