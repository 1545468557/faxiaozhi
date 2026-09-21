"""⭐ 表达边界（PRD 4.3）：五类越界表述拦截；规则可配置，配错不崩。"""

from __future__ import annotations

import logging
import re

LOG = logging.getLogger("faxiaozhi.expression")

#: 五类拦截清单（PRD 4.3.1）
DEFAULT_PATTERNS: dict[str, list[str]] = {
    "总体化推断": [
        r"全国.{0,10}法院.{0,10}(比例|占比|趋势)",
        r"(全部|所有|绝大多数|普遍|各地).{0,8}法院",
        r"总体而言.{0,8}(支持|认定|倾向于)",
    ],
    "伪指标": [
        r"胜诉率",
        r"支持率",
        r"驳回率",
        r"改判率",
        r"胜诉概率",
    ],
    "无来源百分比": [
        r"(高达|约为|超过|约占|达到)\s*\d+(?:\.\d+)?\s*%",
    ],
    "确定性承诺": [
        r"可以确定",
        r"必然(?:会)?",
        r"法院一定会",
        r"肯定(?:会)?(?:被)?(?:支持|采纳)",
    ],
    "存在性断言": [
        r"该案(?:例)?不存在",
        r"没有(?:相关|任何)判例",
        r"不存在(?:相关|类似)?案(?:例|件)",
        r"无此类判例",
    ],
}


class ExpressionGuard:
    def __init__(self, extra_patterns: list[str] | None = None) -> None:
        self.compiled: list[tuple[str, re.Pattern[str]]] = []
        for category, patterns in DEFAULT_PATTERNS.items():
            for pattern in patterns:
                self._add(category, pattern)
        for pattern in extra_patterns or []:
            self._add("自定义", pattern)

    def _add(self, category: str, pattern: str) -> None:
        try:
            self.compiled.append((category, re.compile(pattern)))
        except re.error as exc:                       # 配置写错不得导致主流程崩溃
            LOG.warning("表达边界正则无效，已忽略：%s（%s）", pattern, exc)

    def scan(self, text: str) -> list[dict[str, str]]:
        hits: list[dict[str, str]] = []
        for category, regex in self.compiled:
            for match in regex.finditer(text or ""):
                hits.append({"category": category, "text": match.group(0)})
        return hits

    @staticmethod
    def rewrite_hint(hits: list[dict[str, str]], denominator: int | None = None) -> str:
        base = "以下表述越界，请改为「本次样本 N 篇中 n 篇…」的口径："
        items = "、".join(f"{h['category']}「{h['text']}」" for h in hits[:5])
        suffix = f"（当前样本量 {denominator} 篇）" if denominator else ""
        return base + items + suffix
