"""本地依据库来源（2-3）：只做「同一检索式命中复用」与「按标识取原文」，不做全文检索。"""

from __future__ import annotations

from typing import Any

from ..config import Config
from ..library import Library, entry_to_source
from ..models import STATUS_TEXT, Status, ToolResult
from .base import SourceProvider


class LocalLibraryProvider(SourceProvider):
    name = "local_library"
    authoritative = False

    def __init__(self, cfg: Config, kind: str, library: Library | None = None) -> None:
        self.cfg = cfg
        self.kind = kind
        self.library = library if library is not None else Library(cfg)
        self.logical = "search_cases" if kind == "case" else "search_statutes"
        self.ttl = int(cfg.get("sources.merge.search_cache_ttl_seconds", 86400))

    # ------------------------------------------------------------ 检索式命中
    def search(self, **args: Any) -> ToolResult | None:
        if not self.library.enabled:
            return None
        entries = self.library.query_hit(self.logical, args, self.ttl)
        if not entries:
            return None
        sources = []
        for entry in entries:
            source = entry_to_source(entry)
            source.local_hit = True
            sources.append(source)
        if not sources:
            return None
        status = (
            Status.ABSTRACT_ONLY
            if all(s.status is Status.ABSTRACT_ONLY for s in sources)
            else Status.OK
        )
        return ToolResult(
            tool=self.logical,
            status=status,
            sources=sources,
            detail=(
                f"（本地依据库命中）返回 {len(sources)} 条；"
                f"内容来自此前真实检索（法宝）的本地留存，法宝未参与本次调取。"
            ),
            meta={
                "provider": "local_library",
                "local_hit": True,
                "library": True,
                "max_chars": int(self.cfg.get("limits.max_tool_result_chars", 4000)),
            },
        )

    # ------------------------------------------------------------ 按标识取原文
    def fetch(self, identifier: str, **args: Any) -> Any:
        if not self.library.enabled or not identifier:
            return None
        entry = self.library.get(self.kind, identifier)
        if entry is None:
            return None
        source = entry_to_source(entry)
        source.local_hit = True
        if not source.quote:
            source.status = Status.ABSTRACT_ONLY
            _ = STATUS_TEXT[source.status]
        return source
