#!/usr/bin/env python3
"""清理运行目录与指标文件中的测试残渣（2-2 新增）。

背景（实测发现的真实缺陷）：跑 pytest 会把脏数据写进真实 `runs/` 与
`data/metrics.jsonl`，导致北极星指标混入测试数据而不可信。

本脚本做三件事，**幂等**、可重复执行：
  1. 清理 `runs/` 里的过期锁文件（`*.lock`，默认超过 N 小时视为过期）；
  2. 把 `runs/` 里测试用例产生的残渣移入 `runs/archive-tests/`（不删除，可回溯）；
  3. 把 `data/metrics.jsonl` 里测试来源的行拆到 `data/metrics.test-archive.jsonl`
     （同样不删除），真实行原样保留。

用法（在工程目录下执行）：
    uv run python scripts/clean_runs.py                 # 只报告，不改动
    uv run python scripts/clean_runs.py --apply         # 实际执行
    uv run python scripts/clean_runs.py --apply --lock-hours 1
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT_DIR = HERE.parent                     # 用脚本位置反推工程根，不依赖当前工作目录
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))


#: 测试用例里硬编码的 run_id（见 tests/test_workflow.py），它们绝不可能是真实运行
TEST_RUN_IDS = {
    "testresume01",
    "materialcheck1",
    "materialcheck2",
    "versioncheck1",
    "ordercheck1",
}

TEST_SOURCE = "test"
OFFLINE_SOURCE = "offline"


def is_test_run(run_id: str) -> bool:
    return run_id in TEST_RUN_IDS or run_id.startswith("test") or run_id.endswith("check1")


def is_offline_run(runs_dir: Path, run_id: str) -> tuple[bool, str]:
    """读存档判断这次运行是不是「离线/stub」跑出来的（不是真实外部调用）。

    依据 `runs/<run_id>.json` 里的 provider / scenario：
    模型是 stub，或用了非 clean 的故障场景 → 属于离线运行，其产物不算真实成绩。
    """
    state = runs_dir / f"{run_id}.json"
    if not state.exists():
        # 存档已被搬进 archive-tests（说明此前判定为测试/离线运行）：延续同一判定，
        # 否则同一次运行的其余文件会因找不到存档而被漏掉（实测踩到过）。
        if (runs_dir / "archive-tests" / f"{run_id}.json").exists():
            return (True, "already_archived")
        return (False, "")
    try:
        data = json.loads(state.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return (False, "")
    if str(data.get("provider") or "") == "stub":
        return (True, "provider=stub")
    scenario = str(data.get("scenario") or "")
    if scenario and scenario != "clean":
        return (True, f"scenario={scenario}")
    # 兼容 2-2 之前的旧产物（存档里没有 provider 字段）：
    # 依据池里出现 synthetic=true，说明这次用的是合成夹具 —— 不是真实运行。
    sources = runs_dir / f"{run_id}.sources.jsonl"
    if sources.exists():
        try:
            head = sources.read_text(encoding="utf-8")[:20000]
        except OSError:
            head = ""
        if '"synthetic": true' in head:
            return (True, "synthetic_fixture")
    return (False, "")


def clean_locks(runs_dir: Path, lock_hours: float, apply: bool) -> list[str]:
    removed: list[str] = []
    now = time.time()
    for lock in sorted(runs_dir.glob("*.lock")):
        age_hours = (now - lock.stat().st_mtime) / 3600
        if age_hours < lock_hours:
            continue
        removed.append(f"{lock.name}（闲置 {age_hours:.1f} 小时）")
        if apply:
            lock.unlink(missing_ok=True)
    return removed


def archive_test_runs(runs_dir: Path, apply: bool) -> list[str]:
    """把「测试残渣」与「离线/stub 运行产物」移入 runs/archive-tests/（不删除）。"""
    moved: list[str] = []
    target = runs_dir / "archive-tests"
    # 关键：**先判定、后搬运**。若边搬边判，run 的 .json 存档先被搬走后，
    # 它同名的其它文件（journal/output/sources）就再也判不出来了（实测踩到过）。
    verdicts: dict[str, bool] = {}
    for path in sorted(runs_dir.glob("*")):
        if not path.is_file():
            continue
        run_id = path.name.split(".")[0]
        if run_id not in verdicts:
            offline, _why = is_offline_run(runs_dir, run_id)
            verdicts[run_id] = is_test_run(run_id) or offline
        if not verdicts[run_id]:
            continue
        moved.append(path.name)
    if apply:
        target.mkdir(parents=True, exist_ok=True)
        for name in moved:
            (runs_dir / name).rename(target / name)
    return moved


def split_metrics(
    metrics_path: Path, apply: bool, runs_dir: Path | None = None
) -> tuple[int, int]:
    """返回 (真实行数, 归档行数)。

    归档行 = source 为 test / offline，或 run_id 形如测试编号。
    **只看 source=real 的行才是真实成绩**（北极星指标）。
    """
    if not metrics_path.exists():
        return (0, 0)
    real_lines: list[str] = []
    test_lines: list[str] = []
    for line in metrics_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            real_lines.append(line)               # 坏行无法判断，保守留作真实行
            continue
        source = row.get("source")
        run_id = str(row.get("run_id") or "")
        offline_row = False
        if runs_dir is not None and run_id:
            offline_row, _why = is_offline_run(runs_dir, run_id)
        if (
            source in {TEST_SOURCE, OFFLINE_SOURCE}
            or is_test_run(run_id)
            or offline_row
        ):
            test_lines.append(line)
        else:
            real_lines.append(line)

    if apply and test_lines:
        archive = metrics_path.with_name("metrics.test-archive.jsonl")
        with archive.open("a", encoding="utf-8") as fh:
            for line in test_lines:
                fh.write(line + "\n")
        metrics_path.write_text("\n".join(real_lines) + ("\n" if real_lines else ""), encoding="utf-8")
    return (len(real_lines), len(test_lines))


def main() -> int:
    parser = argparse.ArgumentParser(description="清理测试残渣与过期锁（幂等）")
    parser.add_argument("--apply", action="store_true", help="实际执行（默认只报告）")
    parser.add_argument("--lock-hours", type=float, default=2.0, help="锁文件闲置多少小时算过期")
    args = parser.parse_args()

    from app.config import get_config

    cfg = get_config()
    runs_dir = cfg.runs_dir
    metrics_path = cfg.metrics_path
    mode = "执行" if args.apply else "报告（未改动，加 --apply 才改）"

    print(f"工程目录：{PROJECT_DIR}")
    print(f"模式    ：{mode}\n")

    locks = clean_locks(runs_dir, args.lock_hours, args.apply)
    print(f"[1/3] 过期锁文件（闲置 > {args.lock_hours} 小时）：{len(locks)} 个")
    for name in locks:
        print(f"      - {name}")

    moved = archive_test_runs(runs_dir, args.apply)
    print(f"\n[2/3] 测试残渣 + 离线运行产物：{len(moved)} 个（移入 runs/archive-tests/，不删除）")
    for name in moved[:20]:
        print(f"      - {name}")
    if len(moved) > 20:
        print(f"      … 另有 {len(moved) - 20} 个")

    real, test = split_metrics(metrics_path, args.apply, runs_dir)
    print(f"\n[3/3] 指标文件：真实 {real} 行 / 归档 {test} 行（测试 + 离线运行）")
    if test:
        print("      归档行将拆到 data/metrics.test-archive.jsonl（不删除）")

    print("\n完成。北极星指标请用 GET /api/metrics?source=real 取真实数据。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
