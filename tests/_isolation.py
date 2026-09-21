"""测试隔离哨兵（2-2 新增）。

背景（实测发现的真实缺陷）：跑 pytest 会把脏数据写进**真实的** `runs/` 与
`data/metrics.jsonl`，导致 `GET /api/metrics` 报出的北极星指标混入测试数据而不可信。

这里把"目录快照 + 差异断言"做成纯函数，既给 conftest 用，也能被测试直接调用
（用于自证：故意造一个污染，断言哨兵一定会报错）。
"""

from __future__ import annotations

import json
from pathlib import Path

#: 真实数据目录里，属于"运行产物"的扩展名（用于比对，不误伤 .gitkeep 等）
WATCH_SUFFIXES = (".json", ".jsonl")


def snapshot(directory: Path) -> dict[str, int]:
    """记录目录下受关注文件的 (相对名 -> 字节数)。目录不存在记为空。"""
    out: dict[str, int] = {}
    if not directory.exists():
        return out
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix not in WATCH_SUFFIXES:
            continue
        out[str(path.relative_to(directory))] = path.stat().st_size
    return out


def snapshot_lines(path: Path) -> int:
    """记录指标文件的行数（文件不存在记 0）。"""
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as fh:
        return sum(1 for _ in fh)


def diff(before: dict[str, int] | int, after: dict[str, int] | int, label: str) -> list[str]:
    """返回人类可读的差异清单；无差异返回空列表。"""
    if isinstance(before, int) and isinstance(after, int):
        if after == before:
            return []
        return [f"{label}：行数从 {before} 变成 {after}（新增 {after - before} 行）"]

    assert isinstance(before, dict) and isinstance(after, dict)
    problems: list[str] = []
    for name in sorted(set(after) - set(before)):
        problems.append(f"{label}：新增文件 {name}（{after[name]} 字节）")
    for name in sorted(set(before) - set(after)):
        problems.append(f"{label}：文件消失 {name}")
    for name in sorted(set(before) & set(after)):
        if before[name] != after[name]:
            problems.append(f"{label}：文件被改写 {name}（{before[name]} → {after[name]} 字节）")
    return problems


def assert_unchanged(before: dict[str, int] | int, after: dict[str, int] | int, label: str) -> None:
    """差异非空即失败。这是哨兵的核心断言。"""
    problems = diff(before, after, label)
    if problems:
        raise AssertionError("检测到测试污染真实数据目录：\n  - " + "\n  - ".join(problems))


def read_metrics(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows
