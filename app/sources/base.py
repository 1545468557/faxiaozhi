"""来源编排原语（阶段 2-3）。

设计原则：
- 每个来源实现同一个调用面（`search` / `fetch`），**新来源不改业务代码**；
- 顺序即优先级，由 `config.yaml: sources.<kind>.providers` 决定；
- **不做本地优先检索**：本地库只按「同一检索式」或「同一标识」命中复用，未命中必然继续走权威来源；
- 全部失败时仍走 2-2 固化的降级矩阵（状态与文案不重设计）。
"""

from __future__ import annotations

import logging
from typing import Any

from ..config import Config
from ..library import Library, entry_from_source, entry_to_source
from ..models import Source, Status, ToolResult

LOG = logging.getLogger("faxiaozhi.sources")


class SourceProvider:
    """来源接口。`search` 返回 None 表示"我不处理这类查询"（交给下一个来源）。"""

    name: str = "base"
    kind: str = "case"
    authoritative: bool = False

    def search(self, **args: Any) -> ToolResult | None:  # pragma: no cover - 接口
        return None

    def fetch(self, identifier: str, **args: Any) -> Source | None:  # pragma: no cover - 接口
        return None


class SourceRouter:
    """按优先级串行调用来源，合并/去重、标注印证、沉淀本地依据库。"""

    def __init__(
        self,
        cfg: Config,
        kind: str,
        providers: list[SourceProvider],
        session: Any = None,
        library: Library | None = None,
    ) -> None:
        self.cfg = cfg
        self.kind = kind
        self.providers = providers
        self.session = session
        self.library = library if library is not None else Library(cfg)
        self.max_calls = int(cfg.get("sources.merge.max_provider_calls", 3))
        self.local_hit_reuse = bool(cfg.get("sources.merge.local_hit_reuse", True))
        self.cache_ttl = int(cfg.get("sources.merge.search_cache_ttl_seconds", 86400))
        self.write_on_success = bool(cfg.get("library.write_on_success", True))
        self.logical = "search_cases" if kind == "case" else "search_statutes"

    # ------------------------------------------------------------ 查询
    def search(self, **args: Any) -> ToolResult:
        last: ToolResult | None = None
        calls = 0
        for provider in self.providers:
            if provider.name == "local_library" and not self.local_hit_reuse:
                continue
            if calls >= self.max_calls:
                LOG.info("来源调用次数达到上限 %s，提前停止", self.max_calls)
                break
            calls += 1
            try:
                result = provider.search(**args)
            except Exception as exc:                     # 单个来源异常不得拖垮整体
                LOG.warning("来源 %s 调用异常：%s", provider.name, type(exc).__name__)
                continue
            if result is None:
                continue
            if result.status in (Status.OK, Status.ABSTRACT_ONLY, Status.NO_MATCH, Status.INSUFFICIENT):
                self._annotate(result)
                self._persist(provider, args, result)
                return result
            last = result                                  # 失败：继续尝试下一个来源
        if last is not None:
            return last
        return ToolResult(
            tool=self.logical,
            status=Status.INTERFACE_ERROR,
            detail="检索接口调用失败，本次未获得可核验依据。这不等于「无相关案例」。可点击重试。",
            error_kind="unavailable",
            meta={"retryable": True, "transport": "router",
                  "providers": [p.name for p in self.providers]},
        )

    def fetch(self, identifier: str, **args: Any) -> tuple[Source | None, str]:
        for provider in self.providers:
            try:
                source = provider.fetch(identifier, **args)
            except Exception:                              # 兜底
                continue
            if source is not None:
                source.corroboration = self.library.corroborate(source)
                return source, provider.name
        return None, ""

    # ------------------------------------------------------------ 标注与沉淀
    def _annotate(self, result: ToolResult) -> None:
        for source in result.sources:
            if source.origin == "local":
                source.local_hit = True
            source.corroboration = self.library.corroborate(source)
        counts = {"dual": 0, "single_mcp": 0, "single_local": 0, "conflict": 0, "not_applicable": 0}
        for source in result.sources:
            counts[source.corroboration] = counts.get(source.corroboration, 0) + 1
        result.meta.setdefault("corroboration", counts)
        result.meta.setdefault("local_hit", counts["single_local"])

    def _persist(self, provider: SourceProvider, args: dict[str, Any], result: ToolResult) -> None:
        """只有真实权威来源（法宝）的结果才沉淀；夹具与本地命中不回写。"""
        if not self.write_on_success or provider.name != "pkulaw_mcp" or not result.sources:
            return
        run_id = getattr(self.session, "run_id", None)
        entry_ids: list[str] = []
        for source in result.sources:
            entry = entry_from_source(source, origin="mcp", run_id=run_id)
            action = self.library.upsert(entry)
            if action not in {"skipped_disabled", "refused_non_public_origin", "refused_secret_like"}:
                entry_ids.append(entry.entry_id)
        if entry_ids:
            self.library.put_query(self.logical, args, sorted(set(entry_ids)))
            topic = str(getattr(self.session, "topic", "") or "")
            if topic:
                # 主题级回退：只在实时检索完全失败时使用（见 research.py）
                self.library.put_topic(topic, getattr(self.session, "conditions", {}) or {}, entry_ids)


def annotate_result_sources(result: ToolResult, library: Library) -> None:
    """给来源打印证标签（供不经过 router 的路径复用，如工具直连）。"""
    for source in result.sources:
        source.corroboration = library.corroborate(source)


def local_source_from_entry(entry, source_id: str | None = None) -> Source:
    source = entry_to_source(entry, source_id=source_id)
    source.local_hit = True
    return source
