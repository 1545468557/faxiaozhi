"""可观测性：日志 / 指标 / 轨迹 / 审计，**全部脱敏，绝不写原文**（PRD 6.3）。"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .config import Config

#: 允许写入指标/审计/轨迹的字段白名单（其余一律丢弃）
ALLOWED_FIELDS = {
    "ts",
    "session_id",
    "run_id",
    "branch",
    "purpose",
    "event",
    "phase",
    "tool",
    "status",
    "error_kind",
    "attempts",
    "elapsed_ms",
    "sources_returned",
    "gate_accepted",
    "gate_rejected",
    "guard_hits",
    "first_response_ms",
    "total_ms",
    "chars",
    "source_id",
    "rule",
    "gate",
    "reason",
    "count",
    "label",
    "ok",
    "kind",
    "demo_mode",
    "coverage",
    "token_in",
    "token_out",
    # ---- 2-2 新增：数据来源分离与降级记录（均为脱敏字段）----
    "source",
    "step",
    "failed_step",
    "resumed_from",
    "skipped_steps",
    "retryable",
    "endpoint_alias",
    "attempt",
    # ---- 2-3 新增：来源印证与本地复用（均为脱敏字段）----
    "corr_dual",
    "corr_single_mcp",
    "corr_single_local",
    "corr_conflict",
    "local_hits",
    "sources_total",
}

#: 事件来源：real（真实运行）/ test（自动化测试）/ unknown（2-2 之前的历史数据）
SOURCE_REAL = "real"
SOURCE_TEST = "test"
SOURCE_UNKNOWN = "unknown"
#: 离线 / stub 运行：既不是真实运行，也不是自动化测试（2-2 实测补充）
#: 必须与 real 分开，否则「用夹具跑出来的成绩」会污染北极星指标。
SOURCE_OFFLINE = "offline"


def scrub(payload: dict[str, Any]) -> dict[str, Any]:
    """按白名单裁剪；任何疑似正文的值一律不写。"""
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if key not in ALLOWED_FIELDS:
            continue
        if isinstance(value, str) and len(value) > 200:
            value = value[:200]
        out[key] = value
    return out


class Observability:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.metrics_path: Path = cfg.metrics_path
        self.retention_days = int(cfg.get("metrics.retention_days", 30))
        #: 用户材料指纹（只存指纹与长度，用于 audit_flush 自检）
        self._material_fps: set[str] = set()
        #: 2-2 新增：事件来源。测试进程由 conftest 设 FAXIAOZHI_TEST_MODE=1 标记为 test，
        #: 保证北极星指标不被测试数据污染（实测发现的历史缺陷）。
        self.source = _source_for(cfg)

    # ------------------------------------------------------------ 材料指纹自检
    def register_material(self, fingerprint_value: str) -> None:
        if fingerprint_value:
            self._material_fps.add(fingerprint_value)

    def contains_material(self, payload: Any) -> bool:
        text = json.dumps(payload, ensure_ascii=False, default=str)
        if not text or not self._material_fps:
            return False
        from .models import normalize_text

        normalized = normalize_text(text)
        return any(fp in _hash_candidates(normalized) for fp in self._material_fps)

    # ------------------------------------------------------------ 写入
    def metric(self, **fields: Any) -> dict[str, Any]:
        record = scrub({"ts": _now(), "source": self.source, **fields})
        self._append(self.metrics_path, record)
        return record

    def audit(self, **fields: Any) -> dict[str, Any]:
        record = scrub({"ts": _now(), "source": self.source, **fields})
        if self.contains_material(record):       # audit_flush 前的指纹自检
            record = {"ts": _now(), "event": "audit_dropped", "reason": "material_fingerprint"}
        self._append(self.metrics_path, record)
        return record

    def trajectory(self, **fields: Any) -> None:
        record = scrub({"ts": _now(), "source": self.source, "event": "trajectory", **fields})
        if not self.contains_material(record):
            self._append(self.metrics_path, record)

    @staticmethod
    def _append(path: Path, record: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------ 读取
    def summary(self, source: str | None = None) -> dict[str, Any]:
        """source=None 取全部（兼容旧调用）；source=real / test 只取对应来源。

        2-2 新增来源维度：实测发现测试会把脏数据写进真实指标文件，
        导致「引用可溯源率」等北极星指标不可信。真实成绩必须用 source=real 取。
        """
        total = accepted = rejected = 0
        by_status: dict[str, int] = {}
        by_event: dict[str, int] = {}
        by_source: dict[str, int] = {}
        latencies: list[int] = []
        corr = {"dual": 0, "single_mcp": 0, "single_local": 0, "conflict": 0}
        local_hits = sources_total = 0
        if self.metrics_path.exists():
            for line in self.metrics_path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                row_source = row.get("source") or SOURCE_UNKNOWN
                by_source[row_source] = by_source.get(row_source, 0) + 1
                if source is not None and row_source != source:
                    continue
                accepted += int(row.get("gate_accepted") or 0)
                rejected += int(row.get("gate_rejected") or 0)
                if row.get("status"):
                    by_status[row["status"]] = by_status.get(row["status"], 0) + 1
                if row.get("event"):
                    by_event[row["event"]] = by_event.get(row["event"], 0) + 1
                # 2-2：耗时基线（p50/p95）——供《MCP 稳定性与配额基线》使用，
                # 同样按 source 过滤，绝不让测试数据混进真实基线。
                if row.get("elapsed_ms") is not None:
                    try:
                        latencies.append(int(row["elapsed_ms"]))
                    except (TypeError, ValueError):
                        pass
                # 2-3：双源印证与本地复用（只按 source 过滤后的行累加）
                corr["dual"] += int(row.get("corr_dual") or 0)
                corr["single_mcp"] += int(row.get("corr_single_mcp") or 0)
                corr["single_local"] += int(row.get("corr_single_local") or 0)
                corr["conflict"] += int(row.get("corr_conflict") or 0)
                local_hits += int(row.get("local_hits") or 0)
                sources_total += int(row.get("sources_total") or 0)
                total += 1
        denom = accepted + rejected
        corr_denom = corr["dual"] + corr["single_mcp"] + corr["single_local"]
        return {
            "events": total,
            "source_filter": source,
            "by_source": by_source,
            "citations": {
                "accepted": accepted,
                "rejected": rejected,
                "traceable_rate": round(accepted / denom, 4) if denom else None,
            },
            "by_status": by_status,
            "by_event": by_event,
            "latency_ms": {
                "count": len(latencies),
                "p50": _percentile(latencies, 50),
                "p95": _percentile(latencies, 95),
                "max": max(latencies) if latencies else None,
            },
            "corroboration": {
                **corr,
                "rate": round(corr["dual"] / corr_denom, 4) if corr_denom else None,
                "sources_total": sources_total,
            },
            "library_hit_rate": (
                round(local_hits / sources_total, 4) if sources_total else None
            ),
        }


def _hash_candidates(text: str) -> set[str]:
    import hashlib

    if len(text) < 20:
        return set()
    return {hashlib.sha256(text.encode("utf-8")).hexdigest()}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def _percentile(values: list[int], percent: int) -> int | None:
    """最近秩法（nearest-rank）百分位，样本量小时也稳定、可解释。"""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, min(len(ordered), round(percent / 100 * len(ordered) + 0.5)))
    return ordered[rank - 1]


def _source_for(cfg: Config) -> str:
    """事件来源判定（2-2）：test > offline（stub 模型）> real。

    real 的含义是「真的调用了外部模型与检索接口」。用夹具/stub 跑出来的结果
    不算真实成绩，必须单独归类，否则北极星指标会被自己骗。
    """
    if _test_mode():
        return SOURCE_TEST
    try:
        if cfg.model_provider == "stub":
            return SOURCE_OFFLINE
    except Exception:                                          # 配置异常不阻断写入
        pass
    return SOURCE_REAL


def _test_mode() -> bool:
    return os.environ.get("FAXIAOZHI_TEST_MODE", "").strip() not in {"", "0", "false"}


_obs: Observability | None = None


def get_observability(cfg: Config | None = None) -> Observability:
    global _obs
    if _obs is None or cfg is not None:
        from .config import get_config

        _obs = Observability(cfg or get_config())
    return _obs
