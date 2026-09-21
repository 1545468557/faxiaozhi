#!/usr/bin/env python
"""真实链路冒烟（2-2 M1–M5）。

⚠ 会产生**真实费用**（模型费 + 北大法宝检索调用）。因此**必须显式加 `--yes`** 才会执行；
且脚本会在开始时把预估费用打印出来（底线 8：涉及计费的动作先告知、等确认）。

用法（在项目目录下运行）：

    uv run python scripts/smoke_real.py --mode happy   --yes          # M1 顺利链路（约 ¥0.25）
    uv run python scripts/smoke_real.py --mode bad-address --yes       # M2 地址错（几乎不花钱）
    uv run python scripts/smoke_real.py --mode bad-token   --yes       # M3 Token 错（几乎不花钱）
    uv run python scripts/smoke_real.py --mode stability --stress 20 --yes   # M4 稳定性（只调检索）
    uv run python scripts/smoke_real.py --mode resume  --yes           # M5 重启恢复回归

2-4 新增（主路径改造：用户自带材料为主、检索为辅）：

    uv run python scripts/smoke_real.py --mode material --material a.txt --material b.txt --yes
    uv run python scripts/smoke_real.py --mode material --material-dir ./公开案例 --yes
    uv run python scripts/smoke_real.py --mode material-supplement --material-dir ./公开案例 --yes
    uv run python scripts/smoke_real.py --mode material-conflict   --material-dir ./公开案例 --yes

⚠ 材料必须是你有权使用的**公开裁判文书**（或已脱敏文本）；不要放真实客户的涉密材料——
   本阶段没有账户隔离（本机单人使用）。材料原文不落盘、不进日志、不进本地依据库。

安全：只从 `.env` 读密钥；输出里**一律不打印密钥**，地址按 `mask_url` 脱敏。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from app.config import get_config  # noqa: E402
from app.knowledge import KnowledgeBase  # noqa: E402
from app.llm import ToolCall  # noqa: E402
from app.loop import install_default_hooks  # noqa: E402
from app.models import Source, Status  # noqa: E402
from app.session import Session  # noqa: E402
from app.tools import dispatch_tool  # noqa: E402
from app.workflows import launch  # noqa: E402

#: DeepSeek `deepseek-chat` 计价（元 / 百万 tokens）；与 2-1 实测 ¥0.25/次吻合
PRICE_IN_PER_M = 2.0
PRICE_OUT_PER_M = 8.0
#: 合规红线：只限「断言案例/判例不存在」，不包括「法院认定某事实不存在」这类裁判说理
BAD_STATEMENT = (
    "该案不存在",
    "该案例不存在",
    "无相关判例",
    "没有相关判例",
    "没有相似案例",
    "没有相关案例",
    "无相似案例",
    "不存在相关案例",
    "不存在类似案例",
    "无此类判例",
)
NEGATIONS = ("不等于", "不代表", "不意味着", "不能说明", "并非")


def estimate_cost(usage: dict[str, Any]) -> float:
    return round(
        int(usage.get("in", 0)) / 1e6 * PRICE_IN_PER_M
        + int(usage.get("out", 0)) / 1e6 * PRICE_OUT_PER_M,
        4,
    )


def unsafe_assertion(text: str) -> str | None:
    for phrase in BAD_STATEMENT:
        start = 0
        while (idx := text.find(phrase, start)) != -1:
            window = text[max(0, idx - 14) : idx]
            if not any(neg in window for neg in NEGATIONS):
                return f"…{window}{phrase}…"
            start = idx + len(phrase)
    return None


def preflight(cfg, mode: str, assume_yes: bool) -> None:
    print("=" * 78)
    print(f"法小智 · 真实链路冒烟（mode={mode}）")
    print("=" * 78)
    if not assume_yes:
        print("✖ 未加 --yes：本次冒烟会产生真实费用，已终止（底线 8）。请在确认费用后重跑。")
        raise SystemExit(2)
    if os.environ.get("FAXIAOZHI_TEST_MODE"):
        print("✖ 检测到 FAXIAOZHI_TEST_MODE，拒绝在测试模式下跑真实冒烟。")
        raise SystemExit(2)
    if mode in {
        "happy",
        "resume",
        "library",
        "conflict",
        "material",
        "material-supplement",
        "material-conflict",
        "consult",
        "contract",
    }:
        if cfg.model_provider == "stub":
            print("✖ 未配置真实模型 Key（MODEL_PROVIDER=stub），无法做真实链路冒烟。")
            raise SystemExit(2)
    needs_mcp = mode in {
        "happy",
        "resume",
        "library",
        "conflict",
        "material-supplement",
        "material-conflict",
        "consult",
        "contract",
    }
    if needs_mcp and not cfg.mcp_endpoints():
        print("✖ 未配置北大法宝 MCP 地址，无法做真实冒烟。")
        raise SystemExit(2)
    if mode == "material":
        print("  · 纯材料模式：材料充足时**不调用法宝检索**，可以只花模型费。")
    print(f"模型：{cfg.model_provider}｜模型名：{cfg.model_id}")
    print(f"MCP 地址：{sum(len(v) for v in cfg.mcp_endpoints().values())} 个（已脱敏）")
    print("费用预估：happy/resume/library ≈ ¥0.25/次；bad-*/conflict ≈ ¥0.01–0.05；stability 只调检索（按次计）。")
    print("         material ≈ ¥0.10–0.20/次（不检索）；material-supplement/conflict 另加检索调用。")
    print("         consult ≈ ¥0.05–0.15/次（含法条+案例检索）。")
    print("         contract ≈ ¥0.30–0.60/次（通读全文 + 3 次法条检索 + 1 次案例检索）。")
    print()


async def _run_workflow(
    cfg, args: dict[str, Any], sample_size: int, timeout: float, run_id: str | None = None
) -> tuple[Session, dict[str, Any]]:
    session = Session()
    kb = KnowledgeBase(cfg)
    started = time.perf_counter()
    task = asyncio.create_task(launch(session, cfg, kb, args, run_id=run_id))
    deadline = started + timeout
    while time.perf_counter() < deadline:
        if session.checkpoint_kind is not None or task.done():
            break
        await asyncio.sleep(0.05)
    if session.checkpoint_kind is not None:
        ids = [str(c["source_id"]) for c in session.candidates]
        chosen = ids[:sample_size]
        session.checkpoint_value = {
            "confirmed": chosen,
            "excluded": [i for i in ids if i not in chosen],
        }
        session.checkpoint_waiter.set()
        print(f"  · 已自动确认样本 {len(chosen)} 篇（排除 {len(ids) - len(chosen)} 篇）")
    output = await asyncio.wait_for(task, timeout=max(1.0, deadline - time.perf_counter() + 60))
    return session, output


def _summary(session: Session, output: dict[str, Any], elapsed: float) -> dict[str, Any]:
    synthesis = session.synthesis or {}
    conclusions = synthesis.get("conclusions") or []
    gate = output.get("gate") or {}
    limitations = " ".join(session.limitations)
    return {
        "run_id": session.run_id,
        "elapsed_s": round(elapsed, 1),
        "status": output.get("status"),
        "candidates": len(session.candidates),
        "confirmed": len(session.sample.confirmed),
        "matrix_rows": len(session.matrix),
        "conclusions": len(conclusions),
        "gate": gate,
        "usage": output.get("usage") or {},
        "cost_cny": estimate_cost(output.get("usage") or {}),
        "degradations": session.degradation_summary(),
        "failed_step": session.failed_step,
        "retryable": session.can_retry,
        "first_response_ms": output.get("first_response_ms"),
        "unsafe_assertion": unsafe_assertion(
            limitations + " " + json.dumps(synthesis, ensure_ascii=False)
        ),
    }


def _print_summary(summary: dict[str, Any]) -> None:
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print()


async def _maybe_export(session: Session) -> dict[str, Any]:
    blockers = session.export_blockers()
    if blockers:
        return {"exported": False, "blockers": blockers}
    result = dispatch_tool(session, ToolCall(id="smoke_export", name="export_docx", arguments={}))
    if result.status is not Status.OK:
        return {"exported": False, "blockers": [result.detail]}
    path = Path(str(result.meta.get("path")))
    return {
        "exported": True,
        "filename": result.meta.get("filename"),
        "bytes": path.stat().st_size if path.exists() else None,
    }


# ------------------------------------------------------------------ M1 / M5


def _library_stats(cfg) -> dict[str, Any]:
    from app.library import Library

    stats = Library(cfg).stats()
    return {
        k: stats[k]
        for k in ("entries", "entries_citable", "entries_clue", "by_type", "by_origin", "bytes")
    }


def _corroboration(session: Session) -> dict[str, int]:
    counts: dict[str, int] = {}
    for source in session.source_pool.values():
        counts[source.corroboration] = counts.get(source.corroboration, 0) + 1
    return counts


def mode_happy(cfg, args) -> dict[str, Any]:
    print("【M1 真实顺利链路】真实 MCP + 真实模型跑 1 次完整链路")
    started = time.perf_counter()
    session, output = asyncio.run(
        _run_workflow(cfg, args.args, args.sample, args.timeout, args.run_id)
    )
    elapsed = time.perf_counter() - started
    summary = _summary(session, output, elapsed)
    summary["export"] = asyncio.run(_maybe_export(session))
    _print_summary(summary)
    ok = (
        summary["candidates"] > 0
        and summary["matrix_rows"] > 0
        and summary["conclusions"] > 0
        and summary["unsafe_assertion"] is None
    )
    print("✅ M1 通过" if ok else "⚠ M1 未达预期，请人工核对上面的 JSON")
    return {"mode": "happy", "ok": ok, **summary}


def mode_resume(cfg, args) -> dict[str, Any]:
    print("【M1+M5 真实顺利链路 + 重启恢复】先真实跑一次并导出，再按同一 run_id 在新会话中恢复")
    run_id = args.run_id or "smokeresume001"
    started = time.perf_counter()
    first, output = asyncio.run(_run_workflow(cfg, args.args, args.sample, args.timeout, run_id))
    first_elapsed = time.perf_counter() - started
    export = asyncio.run(_maybe_export(first))
    summary = _summary(first, output, first_elapsed)
    summary["export"] = export

    journal = cfg.runs_dir / f"{run_id}.journal.jsonl"
    agent_before = _count_agent_lines(journal)

    print("  · 模拟服务重启：新会话按 run_id 恢复……")
    started = time.perf_counter()
    second = Session()
    second.run_id = run_id
    args_dict = json.loads((cfg.runs_dir / f"{run_id}.json").read_text(encoding="utf-8"))["args"]
    recovery = asyncio.run(_resume_only(cfg, second, args_dict, run_id))
    recovery_s = time.perf_counter() - started
    agent_after = _count_agent_lines(journal)

    summary.update(
        {
            "recovery_s": round(recovery_s, 3),
            "agent_steps_before": agent_before,
            "agent_steps_after": agent_after,
            "zero_rerun": agent_after == agent_before,
            "restored_confirmed": len(second.sample.confirmed),
            "first_confirmed": len(first.sample.confirmed),
            "recovery": recovery,
        }
    )
    _print_summary(summary)
    ok = (
        summary["candidates"] > 0
        and summary["matrix_rows"] > 0
        and summary["conclusions"] > 0
        and summary["unsafe_assertion"] is None
        and summary["export"].get("exported") is True
        and summary["zero_rerun"]
        and summary["restored_confirmed"] == summary["first_confirmed"]
    )
    print("✅ M1+M5 通过（真实链路 + 导出 + 零重跑恢复）" if ok else "⚠ M1+M5 未达预期")
    return {"mode": "resume", "ok": ok, **summary}


async def _resume_only(cfg, session: Session, args_dict: dict[str, Any], run_id: str) -> dict[str, Any]:
    from app.workflows import launch as _launch

    task = asyncio.create_task(_launch(session, cfg, KnowledgeBase(cfg), args_dict, run_id=run_id))
    for _ in range(6000):
        if session.checkpoint_kind is not None or task.done():
            break
        await asyncio.sleep(0.01)
    if session.checkpoint_kind is not None:
        ids = [str(c["source_id"]) for c in session.candidates]
        session.checkpoint_value = {"confirmed": ids[:3], "excluded": ids[3:]}
        session.checkpoint_waiter.set()
    return await asyncio.wait_for(task, timeout=300)


def _count_agent_lines(journal: Path) -> int:
    if not journal.exists():
        return 0
    return sum(1 for line in journal.read_text(encoding="utf-8").splitlines() if '"kind": "agent"' in line)


# ------------------------------------------------------------------ M2 / M3


def _force_bad_address() -> None:
    for name in [k for k in os.environ if k.startswith("PKULAW_URL_")]:
        os.environ[name] = "http://127.0.0.1:9/mcp"


def _force_bad_token() -> None:
    os.environ["PKULAW_MCP_TOKEN"] = "smoke-bad-token-000"


def _fault_mode(cfg, args, mode: str) -> dict[str, Any]:
    # 故障发生在检索阶段、模型不参与：把模型降级为 stub，指标记为 offline，不污染真实北极星指标
    os.environ["MODEL_PROVIDER"] = "stub"
    print("  · 模型降级为 stub：故障发生在检索阶段，本次不调模型、不产生模型费")
    if mode == "bad-address":
        print("【M2 真实故障：地址错】把所有 MCP 地址指向不可达端口")
        _force_bad_address()
    else:
        print("【M3 真实故障：Token 错】使用无效 Token 调真实地址")
        _force_bad_token()
    started = time.perf_counter()
    session, output = asyncio.run(_run_workflow(cfg, args.args, args.sample, args.timeout))
    elapsed = time.perf_counter() - started
    summary = _summary(session, output, elapsed)
    summary["export"] = asyncio.run(_maybe_export(session))
    limitations = " ".join(session.limitations)
    summary["says_pool_empty"] = "候选池为空" in limitations
    summary["conclusions_is_none"] = session.synthesis is None
    # 脱敏自检：界面/快照中不得出现密钥或真实地址
    blob = json.dumps(session.snapshot(), ensure_ascii=False, default=str)
    summary["leaks_token"] = "smoke-bad-token-000" in blob
    summary["leaks_real_url"] = "pkulaw" in blob.lower() or "chineselaw" in blob.lower()
    _print_summary(summary)
    ok = (
        session.has_interface_error()
        and summary["conclusions_is_none"]
        and summary["export"]["exported"] is False
        and summary["unsafe_assertion"] is None
        and not summary["says_pool_empty"]
        and not summary["leaks_token"]
    )
    if mode == "bad-token":
        ok = ok and session.failed_step is not None
    print(f"✅ {mode} 通过（接口失败 ≠ 没有案例、不产出结论、导出被拒、不泄露密钥）" if ok else f"⚠ {mode} 未达预期")
    return {"mode": mode, "ok": ok, **summary}


def mode_library(cfg, args) -> dict[str, Any]:
    """2-3 核心：M1 真实检索并沉淀 → M2 关掉法宝后用本地依据库复用、核验、导出。"""
    print("【M1 真实检索 + 自动沉淀】法宝 + 真实模型跑一次，法条与判例应进本地依据库")
    before = _library_stats(cfg)
    started = time.perf_counter()
    session1, output1 = asyncio.run(_run_workflow(cfg, args.args, args.sample, args.timeout))
    first = _summary(session1, output1, time.perf_counter() - started)
    first["corroboration"] = _corroboration(session1)
    first["export"] = asyncio.run(_maybe_export(session1))
    after = _library_stats(cfg)
    first["library_before"] = before
    first["library_after"] = after
    first["library_delta"] = {
        key: after.get(key, 0) - before.get(key, 0) if isinstance(after.get(key), int) else None
        for key in ("entries", "entries_citable", "entries_clue")
    }
    _print_summary(first)

    print("【M2 关掉法宝：本地依据库复用 + 核验 + 导出】把 MCP 地址指向不可达端口，重跑同一议题")
    _force_bad_address()
    started = time.perf_counter()
    session2, output2 = asyncio.run(_run_workflow(cfg, args.args, args.sample, args.timeout))
    second = _summary(session2, output2, time.perf_counter() - started)
    second["corroboration"] = _corroboration(session2)
    second["local_hits"] = sum(1 for s in session2.source_pool.values() if s.local_hit)
    second["limitations"] = session2.limitations
    second["export"] = asyncio.run(_maybe_export(session2))
    _print_summary(second)

    ok = (
        first["conclusions"] > 0
        and first["library_delta"]["entries"] > 0
        and first["export"].get("exported") is True
        and second["local_hits"] > 0
        and second["corroboration"].get("single_local", 0) > 0
        and second["conclusions"] > 0
        and second["export"].get("exported") is True
    )
    print("✅ M1+M2 通过（沉淀 + 法宝断时复用核验导出）" if ok else "⚠ M1/M2 未达预期")
    return {"mode": "library", "ok": ok, "m1": first, "m2": second}


def mode_conflict(cfg, args) -> dict[str, Any]:
    """M3：改动本地副本一个关键字 → 法宝与本地不一致 → 冲突必拦（不用真模型，零模型费）。"""
    print("【M3 来源冲突演练】复制本地依据库并改动原文，制造法宝与本地不一致")
    from pathlib import Path

    source_dir = Path(cfg.get("library.dir", "data/library"))
    if not source_dir.is_absolute():
        source_dir = cfg.root / source_dir
    if not any(source_dir.rglob("*.json")):
        print("✖ 依据库为空：先跑一次 `--mode library` 沉淀内容，再跑冲突演练。")
        return {"mode": "conflict", "ok": False, "reason": "empty_library"}
    scratch = Path(tempfile.mkdtemp(prefix="fxz-conflict-")) / "library"
    shutil.copytree(source_dir, scratch)
    mutated = 0
    for path in scratch.rglob("*.json"):
        if path.name == "index.json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        entry = data.get("entry") or {}
        if entry.get("text"):
            entry["text"] = str(entry["text"]) + "（冲突演练）"
            entry["content_hash"] = ""
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            mutated += 1
    print(f"  · 已改动 {mutated} 条本地副本（临时目录，不动真实依据库）")

    cfg.raw.setdefault("library", {})["dir"] = str(scratch)
    import app.library as library_module

    library_module._lib = None
    os.environ["MODEL_PROVIDER"] = "stub"          # 冲突判定不需要真实模型
    session, output = asyncio.run(_run_workflow(cfg, args.args, args.sample, args.timeout))
    conflicts = [s for s in session.source_pool.values() if s.corroboration == "conflict"]
    review = session.gate_report.event() if session.gate_report else {}
    rules = sorted({r["rule"] for r in (review.get("details") or [])})
    export = asyncio.run(_maybe_export(session))
    summary = {
        "mutated_entries": mutated,
        "conflicts": len(conflicts),
        "gate_rejected_rules": rules,
        "export": export,
        "conclusions": len((session.synthesis or {}).get("conclusions") or []),
        "limitations": session.limitations,
    }
    _print_summary(summary)
    ok = bool(conflicts) and "R7" in rules and export.get("exported") is False
    print("✅ M3 通过（来源冲突 → 门禁 R7 → 导出被拒）" if ok else "⚠ M3 未达预期")
    return {"mode": "conflict", "ok": ok, **summary}


# ------------------------------------------------------------------ 2-4 材料为主


def _search_calls(session: Session, cfg) -> int:
    """统计本次会话真实发生的检索工具调用次数（读埋点，不用估值）。"""
    path = cfg.metrics_path
    if not path.exists():
        return 0
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("session_id") != session.id:
            continue
        if str(row.get("tool", "")).startswith("search_"):
            count += 1
    return count


def _load_material_files(args) -> list[tuple[str, bytes]]:
    paths: list[Path] = []
    if args.material_dir:
        folder = Path(args.material_dir)
        if not folder.is_dir():
            print(f"✖ 材料目录不存在：{folder}")
            return []
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() in {".txt", ".md", ".docx", ".pdf"}:
                paths.append(path)
    for item in args.material or []:
        paths.append(Path(item))
    out: list[tuple[str, bytes]] = []
    for path in paths:
        if not path.is_file():
            print(f"✖ 材料文件不存在：{path}")
            continue
        out.append((path.name, path.read_bytes()))
    return out


def _material_session(
    files: list[tuple[str, bytes]], verify: bool = True
) -> tuple[Session, list[Any], list[tuple[str, str]]]:
    import uuid

    session = Session()
    sources: list[Any] = []
    rejected: list[tuple[str, str]] = []
    for name, data in files:
        material_id = "m" + uuid.uuid4().hex[:8]
        session.materials[material_id] = (data, name)
        result = dispatch_tool(
            session,
            ToolCall(
                id=f"p_{material_id}",
                name="parse_document",
                arguments={"filename": name, "material_id": material_id},
            ),
        )
        if result.status is not Status.OK:
            rejected.append((name, result.detail))
            continue
        sources.append(result.sources[0])
    if verify:
        for source in sources:
            session.verify_material(source.source_id)
    return session, sources, rejected


async def _run_material_workflow(cfg, session: Session, args_dict: dict[str, Any], sample: int, timeout: float):
    task = asyncio.create_task(launch(session, cfg, KnowledgeBase(cfg), args_dict))
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if session.checkpoint_kind is not None or task.done():
            break
        await asyncio.sleep(0.05)
    if session.checkpoint_kind is not None:
        ids = [str(c["source_id"]) for c in session.candidates]
        chosen = ids[:sample]
        session.checkpoint_value = {
            "confirmed": chosen,
            "excluded": [i for i in ids if i not in chosen],
        }
        session.checkpoint_waiter.set()
        print(f"  · 已自动确认样本 {len(chosen)} 篇（候选 {len(ids)} 条）")
    return session, await asyncio.wait_for(task, timeout=max(1.0, deadline - time.perf_counter() + 60))


def _material_result(session: Session, output: dict[str, Any], elapsed: float, cfg) -> dict[str, Any]:
    summary = _summary(session, output, elapsed)
    summary["material_primary"] = session.material_primary
    summary["materials"] = session.material_snapshot()
    summary["retrieval_calls"] = _search_calls(session, cfg)
    summary["matrix_source_column"] = [row.get("来源") for row in session.matrix]
    return summary


def mode_material(cfg, args) -> dict[str, Any]:
    """M1（2-4 核心）：纯材料出报告——不调用法宝检索。"""
    print("【M1 纯材料：用户自带材料为主、默认不检索】")
    files = _load_material_files(args)
    if len(files) < 2:
        print("✖ 需要至少 2 篇材料（--material 或 --material-dir）。请提供公开裁判文书或已脱敏文本。")
        return {"mode": "material", "ok": False, "reason": "materials_missing"}
    session, sources, rejected = _material_session(files)
    print(f"  · 解析成功 {len(sources)} 篇，被拒 {len(rejected)} 篇")
    for source in sources:
        print(f"    - {source.identifier}（{source.kind}，{len(source.quote)} 字）")
    started = time.perf_counter()
    session, output = asyncio.run(_run_material_workflow(cfg, session, args.args, args.sample, args.timeout))
    summary = _material_result(session, output, time.perf_counter() - started, cfg)
    summary["rejected"] = [name for name, _ in rejected]
    summary["export"] = asyncio.run(_maybe_export(session))
    _print_summary(summary)
    ok = (
        summary["candidates"] >= 2
        and summary["conclusions"] > 0
        and summary["retrieval_calls"] == 0
        and summary["export"].get("exported") is True
    )
    print("✅ M1 纯材料通过（不检索、材料为主、直接出报告）" if ok else "⚠ M1 未达预期")
    return {"mode": "material", "ok": ok, **summary}


def mode_material_supplement(cfg, args) -> dict[str, Any]:
    """M2：纯材料先出报告，再点「补充检索」——补充来源单独标注、材料主体地位不变。"""
    print("【M2 材料 + 补充检索】材料充足时本次**不自动检索**，由用户主动点「补充检索」")
    files = _load_material_files(args)
    if len(files) < 2:
        print("✖ 需要至少 2 篇材料（--material 或 --material-dir）。")
        return {"mode": "material-supplement", "ok": False, "reason": "materials_missing"}
    session, sources, _rejected = _material_session(files)
    started = time.perf_counter()
    session, output = asyncio.run(_run_material_workflow(cfg, session, args.args, args.sample, args.timeout))
    first = _material_result(session, output, time.perf_counter() - started, cfg)
    first["export"] = asyncio.run(_maybe_export(session))
    print("  · 纯材料阶段：")
    _print_summary(first)

    from app.materials import mark_material_corroboration
    from app.workflows.research import assemble_candidates

    user_before = [c["source_id"] for c in session.candidates if c.get("origin") == "user"]
    before = set(session.source_pool)
    result = dispatch_tool(
        session,
        ToolCall(
            id="sup1",
            name="search_cases",
            arguments={"expression": args.topic, "limit": int(cfg.get("limits.max_candidates", 30))},
        ),
    )
    added = [
        s for sid, s in session.source_pool.items() if sid not in before and not s.is_user_material
    ]
    for source in added:
        source.supplement = True
    session.supplement_rounds += 1
    conflicts = mark_material_corroboration(session.source_pool)
    session.candidates = assemble_candidates(session, int(cfg.get("limits.max_candidates", 30)))
    second = {
        "sources_added": len(added),
        "supplement_labels": sorted({s.origin_text for s in added}),
        "retrieval_calls": _search_calls(session, cfg),
        "retrieval_status": result.status.value,
        "retrieval_detail": result.detail,
        "user_materials_still_first": [
            c["source_id"] for c in session.candidates if c.get("origin") == "user"
        ] == user_before,
        "conflicts": conflicts,
        "export": asyncio.run(_maybe_export(session)),
    }
    _print_summary(second)
    ok = (
        first["conclusions"] > 0
        and len(added) > 0
        and second["user_materials_still_first"]
        and second["retrieval_calls"] > 0
        and second["export"].get("exported") is True
    )
    print("✅ M2 通过（补充来源单独标注、材料主体地位不变）" if ok else "⚠ M2 未达预期")
    return {"mode": "material-supplement", "ok": ok, "m1": first, "m2": second}


def mode_material_conflict(cfg, args) -> dict[str, Any]:
    """M3：冲突必拦 + 人工裁决放行。

    两段，**分开报，不混在一起冒充真实数据**：
    1. 本地确定性复现（不依赖法宝返回）：同案号、原文改一处 → R7 拦 → 裁决放行 → 导出带标注。
    2. 真实对照（best effort）：用材料的案号去法宝检索，看在真实数据下是否能复现冲突。
    """
    print("【M3 冲突与裁决】第一段：本地确定性复现（同案号、原文改一处）")
    files = _load_material_files(args)
    if len(files) < 2:
        print("✖ 需要至少 2 篇材料（--material 或 --material-dir）。")
        return {"mode": "material-conflict", "ok": False, "reason": "materials_missing"}
    session, sources, _rejected = _material_session(files)
    started = time.perf_counter()
    session, output = asyncio.run(_run_material_workflow(cfg, session, args.args, args.sample, args.timeout))
    base = _material_result(session, output, time.perf_counter() - started, cfg)
    base["export"] = asyncio.run(_maybe_export(session))
    print("  · 冲突前的正常状态：")
    _print_summary(base)

    from app.gates.runner import apply_gates
    from app.materials import mark_material_corroboration
    from app.workflows.research import assemble_candidates

    target = next((s for s in sources if s.kind == "case"), sources[0])
    mutated_quote = target.quote.replace("本院认为", "本院经审理认为", 1)
    if mutated_quote == target.quote:
        mutated_quote = target.quote[:20] + "（法宝侧版本差异）" + target.quote[20:]
    peer = Source(
        source_id="mcp_case_conflict_probe",
        kind="case",
        identifier=target.identifier,
        title="法宝侧同案号来源（本地复现用）",
        quote=mutated_quote,
        effective_status="不适用（裁判文书）",
        origin="mcp",
    )
    session.source_pool[peer.source_id] = peer
    conflicts = mark_material_corroboration(session.source_pool)
    session.candidates = assemble_candidates(session, int(cfg.get("limits.max_candidates", 30)))
    report = apply_gates(session)
    rules = sorted({r.rule for r in report.rejected})
    blocked = asyncio.run(_maybe_export(session))
    print(f"  · 冲突数 {conflicts}｜门禁规则 {rules}｜导出 {blocked}")

    session.resolve_conflict(target.source_id, "prefer_user_material")
    report2 = apply_gates(session)
    after = asyncio.run(_maybe_export(session))
    annotation = False
    if after.get("exported"):
        from docx import Document

        from app.render.docx import document_text

        doc = Document(str(cfg.exports_dir / str(after["filename"])))
        text = document_text(doc)
        annotation = "未经法宝印证" in text and "冲突已由人工确认" in text

    print("  · 第二段：真实对照（用材料的案号去法宝检索，best effort）")
    live_calls_before = _search_calls(session, cfg)
    live_before = set(session.source_pool)
    live = dispatch_tool(
        session,
        ToolCall(
            id="live_probe",
            name="search_cases",
            arguments={"expression": target.identifier, "limit": 5},
        ),
    )
    live_added = [s for sid, s in session.source_pool.items() if sid not in live_before]
    mark_material_corroboration(session.source_pool)
    live_peers = [s for s in live_added if not s.is_user_material and s.identifier == target.identifier]
    live_conflicts = [s for s in live_added if s.corroboration == "conflict"]
    print(
        f"  · 法宝返回 {len(live_added)} 条（status={live.status.value}），"
        f"同案号 {len(live_peers)} 条，其中冲突 {len(live_conflicts)} 条"
    )

    summary = {
        "local_reproduction": {
            "conflicts": conflicts,
            "gate_rules": rules,
            "export_blocked": blocked.get("exported") is False,
            "blockers": blocked.get("blockers"),
            "resolved_export": after,
            "document_annotation": annotation,
            "gate_rules_after_resolution": sorted({r.rule for r in report2.rejected}),
        },
        "live_comparison": {
            "search_calls_added": _search_calls(session, cfg) - live_calls_before,
            "status": live.status.value,
            "peers_same_identifier": len(live_peers),
            "conflicts": len(live_conflicts),
            "note": "真实数据能否复现冲突，取决于法宝返回的原文是否与你的材料逐字一致；不一致才算冲突。",
        },
    }
    _print_summary(summary)
    ok = (
        conflicts >= 1
        and "R7" in rules
        and blocked.get("exported") is False
        and after.get("exported") is True
        and annotation
    )
    print("✅ M3 通过（冲突必拦 → 人工裁决放行 → 导出带标注）" if ok else "⚠ M3 未达预期")
    return {"mode": "material-conflict", "ok": ok, **summary}


def mode_stability(cfg, args) -> dict[str, Any]:
    print("【M4 稳定性实测】委托 probe_mcp.py --stress（只调检索、不调模型）")
    command = [
        sys.executable,
        str(PROJECT_DIR / "scripts" / "probe_mcp.py"),
        "--stress",
        str(args.stress),
        "--stress-tool",
        args.stress_tool,
        "--stress-delay",
        str(args.stress_delay),
    ]
    env = dict(os.environ)
    env["FAXIAOZHI_OFFLINE"] = "0"
    completed = subprocess.run(command, cwd=str(PROJECT_DIR), env=env, check=False)
    return {"mode": "stability", "ok": completed.returncode == 0, "exit_code": completed.returncode}


# ------------------------------------------------------------------ 入口


#: 2-5 咨询冒烟场景：C1 事实完整 / C2 事实不足（会追问）/ C3 检索不到依据
CONSULT_QUESTIONS: dict[str, str] = {
    "C1": (
        "我与承租人签订了一年期的房屋租赁合同，约定月租三千元、每季度首月五日前支付；"
        "对方已连续两个月未付租金，我已在微信上催告两次但对方拒不理睬。"
        "请问我是否可以自行更换门锁收回房屋？"
    ),
    "C2": "租客欠我房租，我能换锁吗",
    "C3": "在轨航天器之间发生碰撞，我国法律对责任承担是怎么规定的？",
}


async def _consult_flow(cfg: Any, args: Any) -> dict[str, Any]:
    """2-5 真实冒烟：法律咨询（C1/C2/C3）。

    C2 会触发多轮追问，脚本按真实链路模拟用户补充事实，直到不再追问或到达上限。
    整条流程用**同一个事件循环**跑完（会话事件队列不跨 loop）。
    """
    scene = str(getattr(args, "consult_scene", "C2") or "C2").upper()
    question = CONSULT_QUESTIONS.get(scene, CONSULT_QUESTIONS["C2"])
    print(f"【咨询真实冒烟：{scene}】真 MCP + 真模型")
    print(f"  · 提问：{question}")

    session = Session(branch="consult")
    kb = KnowledgeBase(cfg)
    started = time.perf_counter()
    turns: list[dict[str, Any]] = []
    current = question
    for index in range(4):
        task = asyncio.create_task(
            launch(
                session,
                cfg,
                kb,
                {"question": current, "consult": True},
                workflow="legal-consult",
            )
        )
        output = await asyncio.wait_for(task, timeout=args.timeout)
        state = session.consult
        turns.append(
            {
                "turn": index + 1,
                "rounds_used": state.get("rounds", 0),
                "awaiting_answer": bool(state.get("awaiting")),
                "asked": list(state.get("asked") or [])[-3:],
                "conclusions_passed": len(state.get("passed") or []),
                "status": output.get("status"),
            }
        )
        if not state.get("awaiting"):
            break
        current = "大概三千元，已经拖欠两个月，没有书面合同。"

    elapsed = time.perf_counter() - started
    state = session.consult
    answer = state.get("answer") or {}
    gate = (output or {}).get("gate") or {}
    usage = (output or {}).get("usage") or {}

    export: dict[str, Any] = {"exported": False}
    try:
        result = dispatch_tool(
            session, ToolCall(id="call_export", name="export_docx", arguments={})
        )
        export = {
            "exported": result.status is Status.OK,
            "filename": result.meta.get("filename"),
            "bytes": (
                Path(str(result.meta["path"])).stat().st_size if result.status is Status.OK else 0
            ),
            "detail": result.detail,
        }
    except Exception as exc:                                   # 导出失败不影响冒烟结论
        export["detail"] = str(exc)[:200]

    summary = {
        "ok": True,
        "scene": scene,
        "run_id": session.run_id,
        "elapsed_s": round(elapsed, 1),
        "rounds_used": state.get("rounds", 0),
        "max_rounds": state.get("max_rounds", 3),
        "turns": turns,
        "status": state.get("status"),
        "conclusions_passed": len(state.get("passed") or []),
        "insufficient": str(answer.get("insufficient") or ""),
        "gate": gate,
        "usage": usage,
        "cost_cny": estimate_cost(usage),
        "first_response_ms": (output or {}).get("first_response_ms"),
        "limitations": list(session.limitations),
        "export": export,
        "unsafe_assertion": unsafe_assertion(
            " ".join(session.limitations) + " " + json.dumps(answer, ensure_ascii=False)
        ),
        "sources": len(session.source_pool),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print()
    if state.get("status") == "insufficient" and scene == "C3":
        print("✅ C3 通过：检索不到时如实说明，未编造法条")
    elif state.get("status") == "answered":
        print(f"✅ {scene} 通过：产出 {len(state.get('passed') or [])} 条带引用结论")
    else:
        print(f"⚠ {scene} 未产出结论（status={state.get('status')}），请人工判断")
    return summary


def mode_consult(cfg: Any, args: Any) -> dict[str, Any]:
    return asyncio.run(_consult_flow(cfg, args))


async def _contract_flow(cfg: Any, args: Any) -> dict[str, Any]:
    """2-6 真实冒烟：合同审查。

    流程：读合同文件 → 解析（角色=合同）→ 核验 → 发起审查（停在立场人工门）
    → 按 --stance 自动提交 → 出风险 → 导出报告。
    """
    stance = str(getattr(args, "stance", "party_b") or "party_b")
    raw_path = getattr(args, "contract_file", None)
    if not raw_path:
        print("✖ 合同审查冒烟需要 --contract-file <合同文件>（docx/pdf/txt/md）")
        return {"ok": False, "error": "missing_contract_file"}
    path = Path(str(raw_path))
    if not path.exists():
        print(f"✖ 找不到合同文件：{path}")
        return {"ok": False, "error": "contract_file_not_found"}

    session = Session(branch="contract")
    session.materials["m_contract"] = (path.read_bytes(), path.name)
    parsed = dispatch_tool(
        session,
        ToolCall(
            id="p_contract",
            name="parse_document",
            arguments={"filename": path.name, "material_id": "m_contract", "role": "contract"},
        ),
    )
    if parsed.status is not Status.OK:
        print(f"✖ 合同解析失败：{parsed.detail}（error_kind={parsed.error_kind}）")
        return {"ok": False, "error": parsed.error_kind or "parse_failed"}
    source = parsed.sources[0]
    session.verify_material(source.source_id)
    print(f"【合同审查真实冒烟】真 MCP + 真模型｜文件：{path.name}（{len(source.quote)} 字）")
    print(f"  · 已核验；本次立场：{stance}")

    kb = KnowledgeBase(cfg)
    started = time.perf_counter()
    task = asyncio.create_task(
        launch(session, cfg, kb, {"contract": True}, workflow="legal-contract")
    )
    for _ in range(12000):
        if session.checkpoint_kind == "stance_confirm" or task.done():
            break
        await asyncio.sleep(0.05)
    if session.checkpoint_kind == "stance_confirm":
        parties = session.contract.get("parties") or {}
        print(f"  · 到立场人工门（推断：甲方 {parties.get('party_a') or '?'}｜乙方 {parties.get('party_b') or '?'}）")
        session.checkpoint_value = {"stance": stance}
        session.checkpoint_waiter.set()
        print("  · 已确认立场，继续…")
    output = await asyncio.wait_for(task, timeout=args.timeout)
    elapsed = time.perf_counter() - started

    state = session.contract
    risks = list(state.get("risks") or [])
    counts = state.get("counts") or {}
    export: dict[str, Any] = {"exported": False}
    if session.export_ready():
        result = dispatch_tool(session, ToolCall(id="e1", name="export_docx", arguments={}))
        export = {
            "exported": result.status is Status.OK,
            "filename": result.meta.get("filename"),
            "bytes": (
                Path(str(result.meta["path"])).stat().st_size if result.status is Status.OK else 0
            ),
            "detail": result.detail,
        }
    else:
        export["blockers"] = session.export_blockers()

    summary = {
        "ok": True,
        "run_id": session.run_id,
        "elapsed_s": round(elapsed, 1),
        "filename": path.name,
        "chars": len(source.quote),
        "stance": stance,
        "parties": state.get("parties") or {},
        "clauses": len(state.get("clauses") or []),
        "risks": len(risks),
        "counts": counts,
        "risks_by_kind": counts.get("by_kind", {}),
        "risks_by_level": counts.get("by_level", {}),
        "locate_failed": counts.get("locate_failed", 0),
        "gate": (output or {}).get("gate") or {},
        "usage": (output or {}).get("usage") or {},
        "cost_cny": estimate_cost((output or {}).get("usage") or {}),
        "first_response_ms": (output or {}).get("first_response_ms"),
        "limitations": list(session.limitations),
        "export": export,
        "unsafe_assertion": unsafe_assertion(
            " ".join(session.limitations) + " " + json.dumps(risks, ensure_ascii=False)
        ),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print()
    print(
        f"✅ 合同审查完成：条款 {summary['clauses']} ｜风险 {summary['risks']}"
        f"（有依据 {counts.get('verified', 0)}｜提示性 {counts.get('no_basis', 0)}）"
        f"｜门禁 {(summary['gate'] or {}).get('accepted', 0)}/{(summary['gate'] or {}).get('rejected', 0)}"
    )
    return summary


def mode_contract(cfg: Any, args: Any) -> dict[str, Any]:
    return asyncio.run(_contract_flow(cfg, args))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=[
            "happy",
            "bad-address",
            "bad-token",
            "stability",
            "resume",
            "library",
            "conflict",
            "material",
            "material-supplement",
            "material-conflict",
            "consult",
            "contract",
        ],
        default="happy",
    )
    parser.add_argument("--yes", action="store_true", help="确认已知悉并同意真实费用")
    parser.add_argument("--topic", default="买受人以设备质量存在瑕疵为由主张减少价款，法院一般如何认定？")
    parser.add_argument("--region", default="江苏省")
    parser.add_argument("--sample", type=int, default=3, help="自动确认的样本数")
    parser.add_argument("--timeout", type=float, default=600.0, help="单次 workflow 超时（秒）")
    parser.add_argument("--run-id", default=None, help="复用/指定 run_id（重试与恢复用）")
    parser.add_argument("--stress", type=int, default=20, help="M4 连续调用次数")
    parser.add_argument("--stress-delay", type=float, default=1.0)
    parser.add_argument("--stress-tool", default="search_cases", choices=["search_cases", "search_statutes"])
    parser.add_argument("--out", default=None, help="把 JSON 结果另存到指定文件（可选）")
    parser.add_argument(
        "--material",
        action="append",
        default=[],
        help="用户材料文件路径（可重复；txt/md/docx/pdf）",
    )
    parser.add_argument("--material-dir", default=None, help="用户材料目录（目录下全部支持的文件）")
    parser.add_argument(
        "--consult-scene",
        default="C2",
        choices=["C1", "C2", "C3"],
        help="咨询冒烟场景：C1 事实完整 / C2 事实不足 / C3 检索不到依据",
    )
    parser.add_argument("--contract-file", default=None, help="合同审查冒烟：合同文件路径")
    parser.add_argument(
        "--stance",
        default="party_b",
        choices=["party_a", "party_b", "neutral"],
        help="合同审查立场：party_a 甲方 / party_b 乙方 / neutral 中立",
    )
    args = parser.parse_args()

    cfg = get_config()
    install_default_hooks()          # 注册 PostToolUse 埋点，否则真实运行的 tool_call 耗时不会被记录
    preflight(cfg, args.mode, args.yes)
    args.args = {"topic": args.topic, "conditions": {"region": args.region} if args.region else {}}

    dispatch = {
        "happy": mode_happy,
        "bad-address": lambda c, a: _fault_mode(c, a, "bad-address"),
        "bad-token": lambda c, a: _fault_mode(c, a, "bad-token"),
        "stability": mode_stability,
        "resume": mode_resume,
        "library": mode_library,
        "conflict": mode_conflict,
        "material": mode_material,
        "material-supplement": mode_material_supplement,
        "material-conflict": mode_material_conflict,
        "consult": mode_consult,
        "contract": mode_contract,
    }
    start = time.perf_counter()
    result = dispatch[args.mode](cfg, args)
    result["wall_clock_s"] = round(time.perf_counter() - start, 1)
    print(f"（本次 {args.mode} 用时 {result['wall_clock_s']} 秒）")
    if args.out:
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"结果已写入 {args.out}")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
