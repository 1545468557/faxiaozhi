#!/usr/bin/env python
"""北大法宝 MCP 联通性探针 + field_map 校准（阶段 2-1 第一步）。

用法（在项目目录下运行）：
    uv run python scripts/probe_mcp.py            # 只探测：拉 tools/list，不产生检索调用
    uv run python scripts/probe_mcp.py --call     # 额外各调 1 次法条检索与案例检索（会产生费用）

安全：只从 .env 读密钥；输出里**一律不打印密钥**，地址按 mask_url 脱敏。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_config  # noqa: E402
from app.tools.mcp import McpClient, McpProvider, mask_url  # noqa: E402

SECRET = re.compile(r"(sk-[A-Za-z0-9]{6,}|Bearer\s+[A-Za-z0-9\-]{6,}|[0-9a-f]{8}-[0-9a-f\-]{20,})")

#: 工具名/描述 → 服务名 的猜测规则（探针用，不参与业务逻辑）
GUESS_RULES: tuple[tuple[str, str], ...] = (
    ("法条", "检索法律法规"),
    ("fatiao", "精准查找法条"),
    ("law", "法律法规检索"),
    ("case", "检索司法案例"),
    ("案号", "案号识别与溯源"),
    ("溯源", "识别与溯源"),
    ("link", "法宝超链"),
    ("recognition", "法条识别与溯源"),
    ("agg", "法律智能检索"),
    ("hallucination", "修正生成幻觉"),
)


def _guess_service(names: list[str], tools: list[dict[str, Any]]) -> str:
    haystack = (" ".join(names) + " " + " ".join(str(t.get("description") or "") for t in tools)).lower()
    hits = [label for key, label in GUESS_RULES if key in haystack]
    return "、".join(dict.fromkeys(hits)) or "未识别（需人工看工具名）"


def scrub(text: str, limit: int = 400) -> str:
    return SECRET.sub("«已隐藏»", (text or "")[:limit])


def _percentile(values: list[int], percent: int) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, min(len(ordered), round(percent / 100 * len(ordered) + 0.5)))
    return ordered[rank - 1]


def run_stress(cfg, logical: str, count: int, delay: float) -> dict[str, Any]:
    """M4：受控次数的连续调用，测耗时分布、失败率与限流表现（只调检索，不调模型）。"""
    endpoints = cfg.mcp_endpoints().get(logical) or []
    if not endpoints:
        print(f"⚠ 没有为 {logical} 配置可用地址，无法做稳定性实测。")
        return {"ok": False, "reason": "no_endpoint"}
    kind = "statute" if logical == "search_statutes" else "case"
    if logical == "search_statutes":
        sample_args: dict[str, Any] = {"query": "合同解除 根本违约"}
    else:
        sample_args = {"expression": "设备质量瑕疵 减少价款", "limit": 3}
    samples: list[dict[str, Any]] = []
    print("=" * 78)
    print(f"M4 稳定性实测（{logical}，共 {count} 次，间隔 {delay}s；只调检索、不调模型）")
    print("=" * 78)
    for index in range(1, count + 1):
        provider = McpProvider(cfg, logical, kind, endpoints)
        started = time.perf_counter()
        result = provider.call(**sample_args)
        elapsed = result.elapsed_ms or int((time.perf_counter() - started) * 1000)
        samples.append(
            {
                "n": index,
                "elapsed_ms": elapsed,
                "status": result.status.value,
                "error_kind": result.error_kind,
                "sources": len(result.sources),
            }
        )
        flag = "✅" if result.status.value == "ok" else "⚠"
        print(
            f"  {flag} 第 {index:>2}/{count} 次：{elapsed:>5} ms｜{result.status.value}"
            f"｜来源 {len(result.sources)} 条"
        )
        if index < count and delay > 0:
            time.sleep(delay)
    elapsed_values = [s["elapsed_ms"] for s in samples]
    failures = [s for s in samples if s["status"] != "ok"]
    limited = [
        s for s in failures if s["error_kind"] in {"http_429", "http_503"} or s["error_kind"] == "circuit_open"
    ]
    summary = {
        "logical": logical,
        "attempts": count,
        "ok": count - len(failures),
        "failures": len(failures),
        "failure_rate": round(len(failures) / count, 4),
        "latency_ms": {
            "min": min(elapsed_values),
            "p50": _percentile(elapsed_values, 50),
            "p95": _percentile(elapsed_values, 95),
            "max": max(elapsed_values),
        },
        "by_error_kind": {},
        "rate_limited": bool(limited),
        "samples": samples,
    }
    for item in failures:
        key = str(item["error_kind"])
        summary["by_error_kind"][key] = summary["by_error_kind"].get(key, 0) + 1
    print()
    print(json.dumps({k: v for k, v in summary.items() if k != "samples"}, ensure_ascii=False, indent=2))
    print()
    print("⚠ 提示：本次为单机单次观测（样本量小），不代表线上表现；如出现限流请立即停止，不要硬冲。")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--call", action="store_true", help="额外发起真实检索调用（产生费用）")
    parser.add_argument("--stress", type=int, default=0, help="M4：连续调用 N 次测稳定性（产生费用）")
    parser.add_argument("--stress-delay", type=float, default=1.0, help="连续调用之间的间隔秒数")
    parser.add_argument(
        "--stress-tool",
        default="search_cases",
        choices=["search_cases", "search_statutes"],
        help="稳定性实测使用的逻辑工具",
    )
    parser.add_argument("--max-stress", type=int, default=50, help="单次实测的调用次数上限（防止误操作）")
    args = parser.parse_args()

    cfg = get_config()
    endpoints = cfg.mcp_endpoints()
    print("=" * 78)
    print("法小智 · 北大法宝 MCP 探针")
    print("=" * 78)
    print(f"配置的地址数：{sum(len(v) for v in endpoints.values())}")
    print(f"鉴权 Key：{'已配置' if cfg.mcp_token else '未配置'}（值已隐藏）")
    print()

    all_urls = [(n, u) for n, u in cfg.all_mcp_urls() if n.rsplit('_', 1)[-1].isdigit()]
    if not all_urls and not any(endpoints.values()):
        print("⚠ 没有读到任何地址。请检查 .env 中的 PKULAW_URL_* 是否已填写。")
        return 2

    # ---------------------------------------------------------------- 逐地址识别
    if all_urls:
        print("=" * 78)
        print("逐个地址识别（tools/list，不产生检索费用）")
        print("=" * 78)
        for env_name, url in all_urls:
            client = McpClient(cfg, url)
            started = time.perf_counter()
            try:
                tools = client.list_tools()
                elapsed = int((time.perf_counter() - started) * 1000)
                names = [str(t.get("name", "?")) for t in tools]
                print(f"✅ {env_name}  {mask_url(url)}")
                print(f"   {len(tools)} 个工具，{elapsed} ms")
                for tool in tools:
                    desc = (tool.get("description") or "").replace("\n", " ")[:90]
                    print(f"     - {tool.get('name')}｜{desc}")
                guessed = _guess_service(names, tools)
                print(f"   ⇒ 猜测服务：{guessed}")
            except Exception as exc:
                print(f"❌ {env_name}  {mask_url(url)}")
                print(f"   失败：{type(exc).__name__}｜{scrub(str(exc))}")
                print("   建议：若为 SSE 传输，请把该地址换成 /mcp 或 /sse 结尾的形态再试")
            print()
        if not endpoints:
            return 0

    print("（地址已按服务名归位；继续按逻辑工具映射探测）")

    # ---------------------------------------------------------------- tools/list
    results: dict[str, list[dict[str, Any]]] = {}
    for logical, urls in endpoints.items():
        for url in urls:
            started = time.perf_counter()
            client = McpClient(cfg, url)
            try:
                tools = client.list_tools()
                elapsed = int((time.perf_counter() - started) * 1000)
                results[logical] = tools
                print(f"✅ [{logical}] {mask_url(url)}")
                print(f"   tools/list 成功，{len(tools)} 个工具，耗时 {elapsed} ms")
                for tool in tools:
                    name = tool.get("name", "?")
                    desc = (tool.get("description") or "").replace("\n", " ")[:70]
                    schema = tool.get("inputSchema") or tool.get("input_schema") or {}
                    required = ",".join(schema.get("required", []) or [])
                    print(f"     - {name}｜{desc}")
                    if required:
                        print(f"       必填参数：{required}")
            except Exception as exc:
                print(f"❌ [{logical}] {mask_url(url)}")
                print(f"   失败：{type(exc).__name__}｜{scrub(str(exc))}")
            print()

    # ---------------------------------------------------------------- 真实调用
    if args.call:
        print("=" * 78)
        print("真实检索调用（各 1 次，用于校准 field_map）")
        print("=" * 78)
        for logical, sample_args in (
            ("search_cases", {"expression": "设备质量瑕疵 解除合同", "limit": 3}),
            ("search_statutes", {"query": "合同解除 根本违约"}),
        ):
            tools = results.get(logical) or []
            if not tools:
                print(f"⏭ [{logical}] 无可用端点，跳过")
                continue
            url = cfg.mcp_endpoints()[logical][0]
            tool_name = str(tools[0].get("name") or logical)
            client = McpClient(cfg, url)
            started = time.perf_counter()
            try:
                if isinstance(sample_args.get("limit"), int):
                    sample_args["limit"] = min(sample_args["limit"], 3)
                raw = client.call_tool(tool_name, sample_args)
                elapsed = int((time.perf_counter() - started) * 1000)
                print(f"✅ [{logical}] 工具 {tool_name} 调用成功，耗时 {elapsed} ms")
                print(f"   返回体顶层键：{sorted(raw.keys()) if isinstance(raw, dict) else type(raw).__name__}")
                print(f"   内容预览：{scrub(json.dumps(raw, ensure_ascii=False), 600)}")
            except Exception as exc:
                print(f"❌ [{logical}] 调用失败：{type(exc).__name__}｜{scrub(str(exc))}")
            print()

    # ---------------------------------------------------------------- 校准建议
    print("=" * 78)
    print("给 field_map 的校准建议（写入 config.yaml: mcp.field_map）")
    print("=" * 78)
    for logical, tools in results.items():
        names = [t.get("name") for t in tools]
        print(f"- {logical}：可用工具 {names}")
        if len(names) == 1:
            print(f"  建议：mcp.tool_names.{logical} = {names[0]}")
    print()
    print("提示：tools/list 不产生检索费用；上面的实数调用各 1 次，请留意配额。")

    # ---------------------------------------------------------------- M4 稳定性
    if args.stress:
        if args.stress > args.max_stress:
            print(f"✖ 实测次数 {args.stress} 超过上限 {args.max_stress}，已拒绡（防止误操作）。")
            return 2
        run_stress(cfg, args.stress_tool, args.stress, args.stress_delay)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
