"""离线夹具来源：**全部标注 synthetic=true**，默认被门禁 R2 拦截（C-13）。

仅用于：① 无 MCP 地址时的降级；② 自动化回归测试。绝不冒充真实检索。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import Config
from ..models import STATUS_TEXT, Source, Status, ToolResult

#: 通过环境变量切换的离线场景（供测试与降级演练使用）
SCENARIOS = ("clean", "no_match", "interface_error", "abstract_only", "insufficient")


class FixtureProvider:
    def __init__(self, cfg: Config, scenario: str = "clean") -> None:
        self.cfg = cfg
        self.scenario = scenario or "clean"
        self.dir: Path = cfg.fixtures_dir
        self._cases: list[dict[str, Any]] = []
        self._statutes: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        cases = self.dir / "cases.json"
        statutes = self.dir / "statutes.json"
        if cases.exists():
            self._cases = json.loads(cases.read_text(encoding="utf-8")).get("cases", [])
        if statutes.exists():
            self._statutes = json.loads(statutes.read_text(encoding="utf-8")).get("statutes", [])

    # ------------------------------------------------------------ cases
    def search_cases(self, **args: Any) -> ToolResult:
        if self.scenario == "interface_error":
            return ToolResult(
                tool="search_cases",
                status=Status.INTERFACE_ERROR,
                detail="检索接口调用失败，本次未获得可核验依据。这不等于「无相关案例」。可点击重试。",
                error_kind="fixture_simulated",
                meta={"retryable": True},
            )
        if self.scenario == "no_match":
            return ToolResult(
                tool="search_cases",
                status=Status.NO_MATCH,
                detail="本次检索条件与数据源范围内未找到匹配。这不代表相关案例不存在。",
                error_kind="fixture_simulated",
            )

        applied, ignored = _echo_conditions(args, ("cause", "court", "level", "region", "since"))
        rows = self._filtered_case_rows(args)

        status = Status.ABSTRACT_ONLY if self.scenario == "abstract_only" else Status.OK
        if self.scenario == "insufficient" or app_count_filtered(args, rows):
            status = Status.INSUFFICIENT if self.scenario == "insufficient" else status

        sources = [_case_source(row, status) for row in rows]
        if not sources:
            return ToolResult(
                tool="search_cases",
                status=Status.NO_MATCH,
                detail="本次检索条件与数据源范围内未找到匹配。这不代表相关案例不存在。",
                meta={"applied_conditions": applied, "ignored_conditions": ignored},
            )
        result = ToolResult(
            tool="search_cases",
            status=status,
            sources=sources,
            detail=(
                f"（离线夹具）返回 {len(sources)} 条候选案例；数据为合成样例。"
                if status is Status.OK
                else f"（离线夹具）{STATUS_TEXT[status]}"
            ),
            meta={
                "applied_conditions": applied,
                "ignored_conditions": ignored,
                "fixture": True,
                "max_chars": int(self.cfg.get("limits.max_tool_result_chars", 4000)),
            },
        )
        return result

    def _filtered_case_rows(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        rows = list(self._cases)
        cause = str(args.get("cause") or "").strip()
        if cause:
            rows = [r for r in rows if cause in str(r.get("cause", ""))]
        level = str(args.get("level") or "").strip()
        if level and "中级" in level:
            rows = [r for r in rows if "中级" in str(r.get("level", ""))]
        region = str(args.get("region") or "").strip()
        if region:
            rows = [r for r in rows if region in str(r.get("region", ""))]
        since = str(args.get("since") or "").strip()
        if since:
            rows = [r for r in rows if str(r.get("decided_on", "")) >= since]
        limit = int(self.cfg.get("limits.max_candidates", 30))
        return rows[:limit]

    # ------------------------------------------------------------ statutes
    def search_statutes(self, **args: Any) -> ToolResult:
        if self.scenario == "interface_error":
            return ToolResult(
                tool="search_statutes",
                status=Status.INTERFACE_ERROR,
                detail="检索接口调用失败，本次未获得可核验依据。这不等于「无相关案例」。可点击重试。",
                error_kind="fixture_simulated",
                meta={"retryable": True},
            )
        if self.scenario == "no_match":
            return ToolResult(
                tool="search_statutes",
                status=Status.NO_MATCH,
                detail="本次检索条件与数据源范围内未找到匹配。",
                error_kind="fixture_simulated",
            )
        applied, ignored = _echo_conditions(args, ("query", "applicable_at"))
        status = Status.ABSTRACT_ONLY if self.scenario == "abstract_only" else Status.OK
        sources = [_statute_source(row, status) for row in self._statutes]
        return ToolResult(
            tool="search_statutes",
            status=status,
            sources=sources,
            detail=(
                f"（离线夹具）返回 {len(sources)} 条法条；数据为合成样例。"
                if status is Status.OK
                else f"（离线夹具）{STATUS_TEXT[status]}"
            ),
            meta={
                "applied_conditions": applied,
                "ignored_conditions": ignored,
                "fixture": True,
                "max_chars": int(self.cfg.get("limits.max_tool_result_chars", 4000)),
            },
        )


def _echo_conditions(args: dict[str, Any], known: tuple[str, ...]) -> tuple[list[str], list[str]]:
    applied = [key for key in known if str(args.get(key) or "").strip()]
    ignored = [
        f"{key}={value}（已忽略，本阶段数据源不支持）"
        for key, value in args.items()
        if key not in known and str(value or "").strip()
    ]
    return applied, ignored


def app_count_filtered(args: dict[str, Any], rows: list[dict[str, Any]]) -> bool:
    """条件与结果数量明显不匹配时标记为结果不足（供 PRD Case 4 使用）。"""
    return bool(args.get("since")) and len(rows) < 2


def _case_source(row: dict[str, Any], status: Status) -> Source:
    return Source(
        source_id=str(row["source_id"]),
        kind="case",
        title=str(row.get("title", "")),
        identifier=str(row.get("identifier", "")),
        quote="" if status is Status.ABSTRACT_ONLY else str(row.get("quote", "")),
        effective_status="现行有效",
        court=row.get("court"),
        level=row.get("level"),
        region=row.get("region"),
        decided_on=row.get("decided_on"),
        uri=row.get("uri"),
        origin="fixture",
        synthetic=True,
        status=status,
    )


def _statute_source(row: dict[str, Any], status: Status) -> Source:
    return Source(
        source_id=str(row["source_id"]),
        kind="statute",
        title=str(row.get("title", "")),
        identifier=str(row.get("identifier", "")),
        quote="" if status is Status.ABSTRACT_ONLY else str(row.get("quote", "")),
        effective_status=str(row.get("effective_status", "现行有效")),
        applicable_from=row.get("applicable_from"),
        applicable_to=row.get("applicable_to"),
        uri=row.get("uri"),
        origin="fixture",
        synthetic=True,
        status=status,
    )
