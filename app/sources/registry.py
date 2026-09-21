"""来源注册表：按 `config.yaml: sources.<kind>.providers` 组装来源链。

规则：
- 顺序即优先级；未知来源名只告警不崩溃（配置写错不得拖垮主流程）；
- `pkulaw_mcp` 在离线/未配置地址时跳过；
- 权威来源可用时**不使用夹具**（防合成数据冒充真实检索）；
- 本地库只做命中复用，未命中必然继续走权威来源。
"""

from __future__ import annotations

import logging
import os
from typing import Any

from ..config import Config
from ..library import Library
from .base import SourceRouter
from .local_library import LocalLibraryProvider
from .mcp_source import FixtureSourceProvider, McpSourceProvider

LOG = logging.getLogger("faxiaozhi.sources")


def _scenario(session: Any) -> str:
    return str(getattr(session, "scenario", None) or os.environ.get("OFFLINE_SCENARIO", "clean"))


def _forced_offline(session: Any) -> bool:
    if os.environ.get("FAXIAOZHI_OFFLINE") == "1":
        return True
    if getattr(session, "force_offline", False):
        return True
    return _scenario(session) != "clean"


def build_providers(cfg: Config, kind: str, session: Any = None, library: Library | None = None):
    names = list(cfg.get(f"sources.{kind}.providers", []) or [])
    offline = _forced_offline(session)
    providers = []
    for name in names:
        if name == "local_library":
            provider = LocalLibraryProvider(cfg, kind, library=library)
            if provider.library.enabled:
                providers.append(provider)
        elif name == "pkulaw_mcp":
            if offline:
                continue
            provider = McpSourceProvider(cfg, kind)
            if provider.endpoints:
                providers.append(provider)
        elif name == "fixtures":
            providers.append(FixtureSourceProvider(cfg, kind, scenario=_scenario(session)))
        else:
            LOG.warning("未知来源 %s（kind=%s），已忽略；请检查 config.yaml: sources", name, kind)
    if any(p.authoritative for p in providers):
        providers = [p for p in providers if p.name != "fixtures"]
    return providers


def build_router(
    cfg: Config, kind: str, session: Any = None, library: Library | None = None
) -> SourceRouter:
    lib = library if library is not None else Library(cfg)
    return SourceRouter(cfg, kind, build_providers(cfg, kind, session, lib), session=session, library=lib)
