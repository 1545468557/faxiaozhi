#!/usr/bin/env python
"""把**合法提供**的法规 / 判例文件导入本地依据库（阶段 2-3）。

⚠ 本脚本**不做任何抓取**：输入必须是产品经理合法提供或合法下载后的本地文件。

用法（在项目目录下运行）：

    uv run python scripts/import_library.py --file statutes.json            # 只报告（dry-run）
    uv run python scripts/import_library.py --file statutes.json --apply     # 真正写入
    uv run python scripts/import_library.py --dir ./corpus --apply --citable # 目录批量导入并标为可引用
    uv run python scripts/import_library.py --file x.json --apply --backup   # 写入前备份

支持的输入格式（JSON，UTF-8）：

    {"entries": [{"content_type": "statute", "identifier": "《民法典》第577条",
                  "title": "民法典", "text": "……", "effective_status": "现行有效",
                  "applicable_from": "2021-01-01"}]}

    # 或直接是一个数组；content_type 省略时按 identifier 猜（含"第…条"视为 statute，否则 case）
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from app.config import get_config  # noqa: E402
from app.library import Library, LibraryEntry, build_entry_id  # noqa: E402

MAX_FILE_MB = 50


def _guess_type(identifier: str) -> str:
    import re

    return "statute" if re.search(r"第[一二三四五六七八九十百零〇\d]+条", identifier or "") else "case"


def load_items(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    if isinstance(data, dict):
        items = data.get("entries") or data.get("items") or data.get("data") or []
    elif isinstance(data, list):
        items = data
    else:
        items = []
    return [item for item in items if isinstance(item, dict)]


def to_entry(item: dict[str, Any], origin: str, zone: str) -> LibraryEntry:
    identifier = str(item.get("identifier") or item.get("案号") or item.get("标题") or "").strip()
    content_type = str(item.get("content_type") or _guess_type(identifier))
    meta = {
        "court": item.get("court"),
        "level": item.get("level"),
        "region": item.get("region"),
        "decided_on": item.get("decided_on") or item.get("裁判日期"),
        "effective_status": item.get("effective_status") or item.get("时效性"),
        "applicable_from": item.get("applicable_from"),
        "applicable_to": item.get("applicable_to"),
        "uri": item.get("uri"),
    }
    now = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())
    return LibraryEntry(
        entry_id=build_entry_id(content_type, identifier),
        content_type=content_type,
        identifier=identifier,
        title=str(item.get("title") or item.get("法规名称") or ""),
        text=str(item.get("text") or item.get("quote") or item.get("原文") or ""),
        meta={k: v for k, v in meta.items() if v not in (None, "")},
        zone=zone,
        origin=origin,
        fetched_at=now,
        imported_at=now,
        last_used_at=now,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", action="append", default=[], help="JSON 文件（可重复）")
    parser.add_argument("--dir", default=None, help="JSON 文件目录（批量）")
    parser.add_argument("--apply", action="store_true", help="真正写入；缺省只报告（dry-run）")
    parser.add_argument("--citable", action="store_true", help="直接标为可引用（依据库）；缺省进线索库")
    parser.add_argument("--backup", action="store_true", help="写入前备份整个依据库")
    parser.add_argument("--origin", default="imported", choices=["imported"])
    args = parser.parse_args()

    paths = [Path(p) for p in args.file]
    if args.dir:
        paths += sorted(Path(args.dir).glob("*.json"))
    if not paths:
        print("✖ 没有输入文件。用 --file 或 --dir 指定（本脚本不抓取任何站点）。")
        return 2

    cfg = get_config()
    library = Library(cfg)
    zone = "citable" if args.citable else "clue"

    if args.apply and args.backup and library.dir.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        archive = shutil.make_archive(str(library.dir) + f"-backup-{stamp}", "gztar", library.dir)
        print(f"已备份：{archive}")

    print(f"依据库目录：{library.dir}")
    print(f"写入模式：{'应用' if args.apply else 'dry-run（只报告）'}｜分区：{zone}")
    print()

    totals = {"added": 0, "touched": 0, "versioned": 0, "skipped": 0, "rejected": 0}
    for path in paths:
        size_mb = path.stat().st_size / 1024 / 1024
        if size_mb > MAX_FILE_MB:
            print(f"✖ 跳过 {path.name}：{size_mb:.1f} MB 超过 {MAX_FILE_MB} MB 上限")
            totals["rejected"] += 1
            continue
        try:
            items = load_items(path)
        except json.JSONDecodeError as exc:
            print(f"✖ 跳过 {path.name}：不是合法 JSON（{exc.msg}）")
            totals["rejected"] += 1
            continue
        print(f"· {path.name}：{len(items)} 条")
        for item in items:
            entry = to_entry(item, origin=args.origin, zone=zone)
            if not entry.identifier or not entry.text:
                totals["skipped"] += 1
                continue
            if not args.apply:
                totals["added"] += 1
                continue
            action = library.upsert(entry)
            if action.startswith("refused") or action.startswith("skipped"):
                totals["rejected"] += 1
                print(f"  ⚠ 拒收 {entry.identifier}：{action}")
            else:
                totals[action] = totals.get(action, 0) + 1
    print()
    print("汇总：", json.dumps(totals, ensure_ascii=False))
    if not args.apply:
        print("提示：以上为 dry-run，未写入任何内容；确认后加 --apply。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
