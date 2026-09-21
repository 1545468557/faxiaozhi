"""北大法宝 MCP 来源（2-3）：权威来源，复用 2-2 的超时/退避/熔断。"""

from __future__ import annotations

from typing import Any

from ..config import Config
from ..models import ToolResult
from .base import SourceProvider


class McpSourceProvider(SourceProvider):
    name = "pkulaw_mcp"
    authoritative = True

    def __init__(self, cfg: Config, kind: str) -> None:
        self.cfg = cfg
        self.kind = kind
        self.logical = "search_cases" if kind == "case" else "search_statutes"
        self.endpoints = cfg.mcp_endpoints().get(self.logical) or []

    def search(self, **args: Any) -> ToolResult | None:
        if not self.endpoints:
            return None
        from ..tools.mcp import McpProvider

        return McpProvider(self.cfg, self.logical, self.kind, self.endpoints).call(**args)


class FixtureSourceProvider(SourceProvider):
    """离线夹具来源：只在没有权威来源可用时兜底（synthetic=true，默认被门禁 R2 拦）。"""

    name = "fixtures"
    authoritative = False

    def __init__(self, cfg: Config, kind: str, scenario: str = "clean") -> None:
        self.cfg = cfg
        self.kind = kind
        self.scenario = scenario or "clean"
        self.logical = "search_cases" if kind == "case" else "search_statutes"

    def search(self, **args: Any) -> ToolResult | None:
        from ..tools.fixtures import FixtureProvider

        provider = FixtureProvider(self.cfg, scenario=self.scenario)
        if self.kind == "case":
            return provider.search_cases(**args)
        return provider.search_statutes(**args)
