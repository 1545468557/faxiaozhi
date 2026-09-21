"""来源框架（阶段 2-3）：多源可插拔 + 本地依据库命中复用 + 双源印证。"""

from __future__ import annotations

from .base import SourceProvider, SourceRouter, annotate_result_sources
from .local_library import LocalLibraryProvider
from .mcp_source import FixtureSourceProvider, McpSourceProvider
from .registry import build_providers, build_router

__all__ = [
    "FixtureSourceProvider",
    "LocalLibraryProvider",
    "McpSourceProvider",
    "SourceProvider",
    "SourceRouter",
    "annotate_result_sources",
    "build_providers",
    "build_router",
]
