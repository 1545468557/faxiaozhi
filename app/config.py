"""配置：.env（密钥）+ config.yaml（策略）。密钥永不打印、永不出现在日志/前端。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path) -> None:
    """极简 .env 解析：不覆盖已有环境变量，不打印任何值。"""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


def _dig(data: dict, dotted: str, default: Any = None) -> Any:
    cur: Any = data
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


class Config:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or ROOT)
        load_dotenv(self.root / ".env")
        cfg_path = self.root / "config.yaml"
        self.raw: dict = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    # ---- 通用读取 ----
    def get(self, dotted: str, default: Any = None) -> Any:
        return _dig(self.raw, dotted, default)

    # ---- 密钥与地址（只从环境变量读）----
    def env(self, name: str) -> str:
        return (os.environ.get(name) or "").strip()

    @property
    def model_api_key(self) -> str:
        return self.env("MODEL_API_KEY")

    @property
    def model_base_url(self) -> str:
        return self.env("MODEL_BASE_URL") or "https://api.deepseek.com"

    @property
    def model_id(self) -> str:
        return self.env("MODEL_ID") or "deepseek-chat"

    @property
    def model_provider(self) -> str:
        """auto | stub | deepseek。auto：有 Key 用 deepseek，无 Key 用 stub。"""
        forced = self.env("MODEL_PROVIDER").lower() or "auto"
        if forced in {"stub", "deepseek"}:
            return forced
        return "deepseek" if self.model_api_key else "stub"

    @property
    def mcp_token(self) -> str:
        return self.env("PKULAW_MCP_TOKEN")

    def mcp_endpoints(self) -> dict[str, list[str]]:
        """逻辑工具名 -> 地址列表（已过滤掉未配置的）。"""
        table = self.get("mcp.endpoints", {}) or {}
        out: dict[str, list[str]] = {}
        for logical, env_names in table.items():
            out[logical] = [u for u in (self.env(n) for n in (env_names or [])) if u]
        return out

    def all_mcp_urls(self) -> list[tuple[str, str]]:
        """所有已配置的 MCP 地址（含尚未映射到逻辑工具的编号槽位）。

        返回 [(环境变量名, 地址)]，按名称里的数字排序，便于探针逐个识别服务。
        """
        items: list[tuple[str, str]] = []
        for name in os.environ:
            if not name.startswith("PKULAW_URL_"):
                continue
            value = self.env(name)
            if value:
                items.append((name, value))
        def sort_key(item: tuple[str, str]) -> tuple[int, str]:
            tail = item[0].rsplit("_", 1)[-1]
            return (int(tail), item[0]) if tail.isdigit() else (10_000, item[0])
        return sorted(set(items), key=sort_key)

    @property
    def mcp_configured(self) -> bool:
        return any(self.mcp_endpoints().values()) or bool(self.all_mcp_urls())

    # ---- 路径 ----
    def path(self, dotted: str, default: str) -> Path:
        p = Path(self.get(dotted, default) or default)
        return p if p.is_absolute() else self.root / p

    @property
    def runs_dir(self) -> Path:
        d = self.path("journal.dir", "runs")
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def exports_dir(self) -> Path:
        d = self.path("export.dir", "exports")
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def fixtures_dir(self) -> Path:
        return self.path("fixtures.dir", "fixtures")

    @property
    def metrics_path(self) -> Path:
        p = self.path("metrics.path", "data/metrics.jsonl")
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    # ---- 自检（不泄露值）----
    def self_check(self) -> dict[str, Any]:
        mcp = self.mcp_endpoints()
        return {
            "model": {
                "provider": self.model_provider,
                "is_stub": self.model_provider == "stub",
                "model_id": self.model_id if self.model_provider != "stub" else "stub",
                "key_present": bool(self.model_api_key),
            },
            "mcp": {
                "configured": bool(mcp),
                "token_present": bool(self.mcp_token),
                "endpoints": {k: len(v) for k, v in mcp.items()},
                "available": None,  # 由探针/运行时填充
            },
            "policy": {
                "allow_synthetic": bool(self.get("policy.allow_synthetic", False)),
                "require_effective_status": bool(self.get("policy.require_effective_status", True)),
                "require_quote_match": bool(self.get("policy.require_quote_match", True)),
                "require_user_material_review": bool(
                    self.get("policy.require_user_material_review", True)
                ),
                "min_quote_length": int(self.get("policy.min_quote_length", 8)),
            },
        }


_config: Config | None = None


def get_config(refresh: bool = False) -> Config:
    global _config
    if _config is None or refresh:
        _config = Config()
    return _config
