"""⭐ 样本锁（PRD 4.2）：三态 + 残留检测 + 由代码计算的分母与分布。

铁律：矩阵、分母、分布由代码算，**不由模型写**（附录 A 第 3 条）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..models import Source

MATRIX_COLUMNS = [
    "case_id",
    "identifier",
    "court",
    "level",
    "region",
    "decided_on",
    "issues",
    "holding",
    "result",
    "stance",
]

_STANCE_LABEL = {
    "support": "支持",
    "oppose": "相反",
    "other": "其他",
    "unknown": "未知",
}


@dataclass
class SampleLock:
    candidates: list[str] = field(default_factory=list)
    confirmed: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    locked: bool = False

    # ---------------------------------------------------------- 状态转换
    def set_candidates(self, ids: list[str]) -> None:
        seen: list[str] = []
        for i in ids:
            if i not in seen:
                seen.append(i)
        self.candidates = seen

    def confirm(self, confirmed: list[str], excluded: list[str]) -> None:
        """确认样本。排除的案子必须从 confirmed 移除（C-09：不残留排除案例）。"""
        excluded_set = {i for i in excluded}
        self.excluded = sorted(excluded_set)
        self.confirmed = [i for i in confirmed if i not in excluded_set and i in self.candidates]
        self.locked = True

    def reinclude(self, source_id: str) -> None:
        if source_id in self.excluded:
            self.excluded.remove(source_id)
        if source_id in self.candidates and source_id not in self.confirmed:
            self.confirmed.append(source_id)

    def exclude(self, source_id: str) -> None:
        if source_id in self.confirmed:
            self.confirmed.remove(source_id)      # ← 关键：同时移除
        if source_id not in self.excluded:
            self.excluded.append(source_id)

    # ---------------------------------------------------------- 残留检测
    def residues(self, cited_ids: list[str]) -> list[str]:
        return sorted({i for i in cited_ids if i in set(self.excluded)})

    @property
    def confirmed_set(self) -> set[str]:
        return set(self.confirmed)


def build_matrix(
    cases: list[dict[str, Any]],
    confirmed_ids: set[str],
    fields: list[str] | None = None,
) -> list[dict[str, Any]]:
    """跨案对比矩阵：**纯函数，只吃已确认样本**（模型无权参与）。"""
    columns = fields or MATRIX_COLUMNS
    rows: list[dict[str, Any]] = []
    for case in cases:
        cid = str(case.get("case_id") or "")
        if cid not in confirmed_ids:
            continue
        row: dict[str, Any] = {}
        for col in columns:
            value = case.get(col)
            if isinstance(value, list):
                value = "；".join(str(v) for v in value)
            row[col] = value if value is not None else ""
        rows.append(row)
    rows.sort(key=lambda r: str(r.get("identifier") or ""))
    return rows


def build_distribution(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """观点分布：分母与范围声明由代码生成（C-06 的结构性保证）。"""
    counts: dict[str, int] = {"support": 0, "oppose": 0, "other": 0, "unknown": 0}
    for case in cases:
        stance = str(case.get("stance") or "unknown")
        counts[stance if stance in counts else "unknown"] += 1
    total = len(cases)
    text = (
        f"本次确认样本 {total} 篇中，支持 {counts['support']} 篇、"
        f"相反 {counts['oppose']} 篇、其他 {counts['other']} 篇、"
        f"未能判定 {counts['unknown']} 篇。"
        "该分布仅描述本次确认样本，不代表全国裁判比例；"
        "未检出相反观点不代表不存在相反裁判。"
    )
    return {
        "counts": counts,
        "denominator": total,
        "labels": {k: _STANCE_LABEL.get(k, k) for k in counts},
        "text": text,
    }


def sources_for_sample(sources: dict[str, Source], confirmed: list[str]) -> list[Source]:
    return [sources[i] for i in confirmed if i in sources]
