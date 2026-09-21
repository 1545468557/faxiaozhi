"""知识加载（PRD 3.7 / s07）：只把目录放进系统提示词，正文按需展开。

铁律：**知识包里不放法条原文**。法条只能通过工具从 MCP 取，
这样「模型凭记忆补写原文」在结构上就不可能发生（PRD 2.1）。
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import Config


class KnowledgeBase:
    def __init__(self, cfg: Config) -> None:
        self.dir: Path = cfg.root / "knowledge"
        self._catalog: list[dict[str, str]] = []
        self._cache: dict[str, str] = {}
        self._load_catalog()

    def _load_catalog(self) -> None:
        path = self.dir / "catalog.json"
        if not path.exists():
            self._catalog = []
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        self._catalog = data.get("catalog", []) if isinstance(data, dict) else data

    def render_catalog(self) -> str:
        if not self._catalog:
            return ""
        lines = [f"- {item['name']}：{item.get('one_liner', '')}" for item in self._catalog]
        return "可用领域口径（需要时按名称展开，不含法条原文）：\n" + "\n".join(lines)

    def load(self, name: str) -> str:
        if name in self._cache:
            return self._cache[name]
        path = self.dir / f"{name}.md"
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        self._cache[name] = text
        return text
