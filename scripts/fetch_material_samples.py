#!/usr/bin/env python
"""从北大法宝 MCP 取若干**公开**裁判文书，落成 .txt 材料样本（阶段 2-4 材料为主路径验收/冒烟用）。

为什么有这个脚本：
- 材料为主路径的验收需要"用户自带材料"。真实冒烟的材料必须是**公开裁判文书**（或已脱敏文本），
  而产品自身的合规红线是**不爬任何站点**（国家法律法规数据库 robots.txt 明确禁止自动化采集，
  人民法院案例库 403，裁判文书网需登录）。
- 所以这里只走**已配置的北大法宝 MCP**（与产品运行时同一条链路）。

⚠ 会产生**检索调用费用**（按次计），因此必须显式加 `--yes`。
   产物写到 `--out` 指定目录（默认仓库外的观测目录），**不写进仓库**。

用法：

    uv run python scripts/fetch_material_samples.py --count 3 --out ~/法小智-公开案例材料 --yes
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from app.config import get_config  # noqa: E402
from app.llm import ToolCall  # noqa: E402
from app.models import Status  # noqa: E402
from app.session import Session  # noqa: E402
from app.tools import dispatch_tool  # noqa: E402
from app.tools.legal import safe_filename  # noqa: E402

#: 单篇材料的最小字数（太短的裁判理由做不了对比矩阵）
MIN_CHARS = 200


def build_material(source) -> str:
    """把检索到的公开裁判文书组装成"用户材料"文本（案号放在正文里，便于标识抽取）。"""
    head = [source.identifier or "（未识别案号）"]
    meta = "｜".join(
        str(x) for x in (source.court, source.level, source.region, source.decided_on) if x
    )
    if meta:
        head.append(meta)
    head.append("")
    head.append(source.quote.strip())
    head.append("")
    head.append("（本材料取自北大法宝公开检索结果，仅用于产品验收与演示。）")
    return "\n".join(head)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--query",
        default="买受人以设备质量存在瑕疵为由主张减少价款，法院一般如何认定？",
        help="检索式（写成你要研究的议题最自然）",
    )
    parser.add_argument("--count", type=int, default=3, help="要落盘的材料篇数")
    parser.add_argument("--region", default="", help="地域筛选（可选，如 江苏省）")
    parser.add_argument("--out", required=True, help="材料输出目录（建议放仓库之外）")
    parser.add_argument("--yes", action="store_true", help="确认知悉检索调用会产生费用")
    args = parser.parse_args()

    if not args.yes:
        print("✖ 未加 --yes：本脚本会调用北大法宝检索（按次计费），已终止（底线 8）。")
        return 2
    cfg = get_config()
    if not cfg.mcp_endpoints():
        print("✖ 未配置北大法宝 MCP 地址，无法取材料。")
        return 2
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)

    arguments: dict[str, object] = {"expression": args.query, "limit": max(args.count * 4, 10)}
    if args.region:
        arguments["region"] = args.region
    session = Session()
    print(f"→ 走 MCP 检索（议题：{args.query[:40]}…）")
    result = dispatch_tool(
        session, ToolCall(id="fetch_materials", name="search_cases", arguments=arguments)
    )
    print(f"  状态 {result.status.value}｜返回 {len(result.sources)} 条｜{result.detail[:80]}")

    written: list[tuple[str, str, int]] = []
    for source in result.sources:
        if source.status is not Status.OK:
            continue
        if len((source.quote or "").strip()) < MIN_CHARS:
            continue
        text = build_material(source)
        name = safe_filename(str(source.identifier or source.source_id), "case")[:60] + ".txt"
        path = out / name
        path.write_text(text, encoding="utf-8")
        written.append((name, str(source.identifier), len(text)))
        if len(written) >= args.count:
            break

    print(f"\n已写入 {len(written)} 篇材料到：{out}")
    for name, identifier, chars in written:
        print(f"  - {name}｜{identifier}｜{chars} 字")
    if len(written) < args.count:
        print(
            "⚠ 可用的完整原文不足：法宝返回里有摘要级或过短结果。"
            "可以换检索式再跑一次，或用 --count 降低要求。"
        )
    return 0 if written else 1


if __name__ == "__main__":
    raise SystemExit(main())
