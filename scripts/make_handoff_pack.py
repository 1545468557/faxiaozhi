#!/usr/bin/env python
"""生成《法小智 · 交接包》（可重生，随时可重跑）。

目标：把**接手的人**需要的东西打成一个目录（并可压成 zip），让对方直接开工：
源码（后端 + 前端）、全部阶段文档、法规库文件、配置模板、启动与验收说明。

**安全红线**（脚本会自动检查，命中就报错退出）：
- 不含任何密钥：`.env*`（除 `.env.example`）、账号库、邀请码、导出文件、运行数据；
- 打包前扫描 `sk-`、`Bearer `、`token=`、常见 Key 名，命中即失败。

用法：
    cd projects/法小智agent开发文档
    uv run python scripts/make_handoff_pack.py                # 只生成本地库（含 158MB 法规库）
    uv run python scripts/make_handoff_pack.py --no-db        # 不带法规库（对方自己跑导入脚本重建）
    uv run python scripts/make_handoff_pack.py --zip          # 额外压成 zip
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]        # projects/法小智agent开发文档
ROOT = PROJECT.parents[1]                            # 仓库根
STAMP = time.strftime("%Y-%m-%d")
OUT = Path.home() / "Desktop" / f"法小智-交接包-{STAMP}"

#: 进包的目录/文件（相对 PROJECT 或 ROOT）
BACKEND = ["app", "app_v3", "tests", "scripts", "knowledge", "fixtures",
           "config.yaml", "pyproject.toml", "uv.lock", "README.md", ".env.example"]
FRONTEND = ["app", "components", "lib", "tests", "scripts", "package.json", "pnpm-lock.yaml",
            "tsconfig.json", "vitest.config.ts", "next.config.ts", "next.config.mjs",
            "postcss.config.mjs", "eslint.config.mjs", "components.json", "README.md", ".env.example"]
DOCS = ROOT / "docs" / "阶段文档" / "法小智agent开发文档"
PRD = ROOT / "docs" / "PRD" / "法小智 PRD"
STATE = ROOT / "docs" / "项目状态" / "法小智agent开发文档.md"
EVIDENCE = ROOT / "docs" / "evidence" / "法小智agent开发文档"
STORE = PROJECT / "data" / "statutes.sqlite3"

SKIP_DIRS = {".venv", "node_modules", ".next", "dist", ".wrangler", "__pycache__", ".pytest_cache",
             ".ruff_cache", ".vinext", ".git", "runs", "exports", "data", "archive-tests"}
SKIP_FILES = {".DS_Store", "tsconfig.tsbuildinfo"}
SECRET_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9]{16,}"), "疑似 API Key（sk-…）"),
    (re.compile(r"Bearer\s+[A-Za-z0-9._-]{20,}"), "疑似 Bearer Token"),
    # 只把"看起来像真值"的算命中：变量名后面接 16 位以上的字母数字串，且不含占位符标记
    (re.compile(r"PKULAW_MCP_TOKEN\s*[=:]\s*[A-Za-z0-9._-]{16,}"), ".env 里的法宝 Token 真值"),
    (re.compile(r"MODEL_API_KEY\s*[=:]\s*sk-[A-Za-z0-9]{16,}"), ".env 里的模型 Key 真值"),
]

README = """# 法小智 · 交接包（{stamp}）

> 接手后再读这一页就够了。**包里没有任何密钥**（Key 必须由账号所有人单独、点对点提供）。

## 一、这个包是什么

面向律师/法务、也向普通人开放的法律 AI 助手。**真实模式**下用 DeepSeek 做推理、北大法宝做法规检索。
本次交接包含：**后端源码 + 前端源码 + 全部阶段文档 + 法规库文件 + 配置模板 + 验收说明**。

## 二、要跑真实结果，需要两把钥匙（包里没有，需另行获取）

| 变量 | 是什么 | 去哪里拿 | 费用量级 |
| --- | --- | --- | --- |
| `MODEL_API_KEY` | DeepSeek 的 API Key | DeepSeek 开放平台自己注册 | 按 token；本项目一次类案研究/合同审查约 **¥0.1–0.3** |
| `PKULAW_MCP_TOKEN` | 北大法宝 MCP 的鉴权 Token（12 个端点共用） | 北大法宝账号（需开通，按次计费） | 按调用次数；一次研究约 20–40 次调用 |

拿到后：

```bash
cp .env.example .env      # 只填上面两项 + 法宝各端点地址（模板里有说明）
```

> **不要**把 `.env` 提交、发群、贴聊天。给对方的 Key 建议**单独申请一把**，用完可吊销。
> **真实合同与案件材料不要送进第三方模型**（本项目开发期只用合成/公开样例）。

## 三、启动（四个服务）

```bash
cd projects/法小智agent开发文档      # 后端
.venv/bin/python -m app.server                                  # ① 8010：类案检索 / 合同审查 / 法规查找 / 导出
APP_PORT=8011 .venv/bin/python -m app_v3.main                   # ② 8011：首页一句话问答（也走真实模型）

cd frontend && node ./node_modules/vinext/dist/cli.js dev --port 5174   # ③ 前端
```

- 本机**没有全局 pnpm**：用 `./node_modules/.bin/vitest run`、`tsc --noEmit`、`eslint`、`vinext build`。
- 前端 `.env.local` 只有一行 `BACKEND_BASE_URL=http://127.0.0.1:8010`（**永不含密钥**）。
- 首次启动会用到 `data/auth.sqlite3` 账号库；**第一个邀请码**用下面这条命令生成（不要用别人的）：
  ```bash
  .venv/bin/python -c "import sys;sys.path.insert(0,'.');from app.auth import core;print(core.generate_invite('first admin')['code'])"
  ```
  然后打开 `/login` → 「用邀请码注册」→ 你就是管理员。

## 四、法规库（法规查找用，158 MB）

包里的 `04-法规库/statutes.sqlite3` 直接放到 `data/statutes.sqlite3` 即可（**不含密钥、不含个人信息**，
只有公开法律法规条文：2476 篇 / 约 8.9 万条）。
若包里没带（`--no-db` 生成），可用《法律法规数据库》源目录重建：
```bash
.venv/bin/python scripts/import_statutes.py
```

## 五、不想花任何密钥也能改界面（可选）

```bash
MODEL_PROVIDER=stub APP_PORT=8012 FAXIAOZHI_OFFLINE=1 .venv/bin/python -m app.server
```
界面、交互、错误文案、导出、法规查找全都能验；只有"回答内容"是示例。**法规查找不受影响**（纯本地库）。

## 六、改完必须跑

```bash
cd frontend
./node_modules/.bin/vitest run        # 前端单测（当前 219 条，2 条是既有失败）
./node_modules/.bin/tsc --noEmit
./node_modules/.bin/eslint . --ignore-pattern dist --ignore-pattern .next
./node_modules/.bin/vinext build
cd .. && .venv/bin/python -m pytest tests -q && .venv/bin/ruff check app tests
```

## 七、先看这两份

1. `01-文档/前端交接说明（2026-09-21）.md` —— 目录结构、三条硬约定、**10 个已知坑**、接口速查。
2. `01-文档/迭代2技术开发文档.md` + `迭代3技术开发文档.md` —— 账号与法规查找的设计口径。

## 八、磁盘内容一览

- `01-文档/`：全部阶段文档 + PRD + 项目状态
- `02-后端源码/`：`app/`（老后端）· `app_v3/`（新问答后端）· `tests/` · `scripts/` · `config.yaml` · `.env.example`
- `03-前端源码/`：`app/` · `components/` · `lib/` · `tests/` · `scripts/`
- `04-法规库/`：`statutes.sqlite3`（可放 `data/`）
- `05-验收证据/`：各阶段的截图与"照着点"清单（**只含文字与图片，无运行数据**）
- `SHA256SUMS.txt`：逐文件校验，可用 `shasum -a 256 -c SHA256SUMS.txt` 验完整性
"""


def copy_tree(src: Path, dst: Path) -> int:
    if not src.exists():
        return 0
    count = 0
    for path in src.rglob("*"):
        if path.is_dir():
            if path.name in SKIP_DIRS:
                continue
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.name in SKIP_FILES or path.name.startswith(".env"):
            continue
        target = dst / path.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        count += 1
    return count


def scan_secrets(root: Path) -> list[str]:
    hits: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        if path.suffix.lower() in {".sqlite3", ".png", ".jpg", ".zip", ".docx", ".pdf"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for pattern, label in SECRET_PATTERNS:
            if pattern.search(text):
                hits.append(f"{path.relative_to(root)}：{label}")
    return hits


def write_sums(root: Path) -> int:
    lines = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "SHA256SUMS.txt":
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.relative_to(root)}")
    (root / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-db", action="store_true", help="不带法规库（对方自己重建）")
    parser.add_argument("--zip", action="store_true", help="额外压成 zip")
    args = parser.parse_args()

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    counts = {
        "01-文档": copy_tree(DOCS, OUT / "01-文档"),
        "02-后端源码": sum(copy_tree(PROJECT / item, OUT / "02-后端源码" / item) for item in BACKEND),
        "03-前端源码": sum(copy_tree(PROJECT / "frontend" / item, OUT / "03-前端源码" / item) for item in FRONTEND),
        "05-验收证据": copy_tree(EVIDENCE, OUT / "05-验收证据"),
    }
    if STATE.exists():
        (OUT / "01-文档").mkdir(exist_ok=True)
        shutil.copy2(STATE, OUT / "01-文档" / "项目状态-法小智.md")
    copy_tree(PRD, OUT / "01-文档" / "PRD")
    if not args.no_db and STORE.exists():
        (OUT / "04-法规库").mkdir(exist_ok=True)
        shutil.copy2(STORE, OUT / "04-法规库" / STORE.name)
        counts["04-法规库"] = 1

    (OUT / "00-先读我.md").write_text(README.format(stamp=STAMP), encoding="utf-8")

    hits = scan_secrets(OUT)
    if hits:
        print("❌ 扫描到疑似密钥，已停止：", file=sys.stderr)
        for line in hits:
            print("   -", line, file=sys.stderr)
        shutil.rmtree(OUT)
        raise SystemExit(1)

    files = write_sums(OUT)
    print("✅ 交接包已生成：", OUT)
    for name, count in counts.items():
        print(f"   {name}: {count} 个文件")
    print(f"   合计 {files} 个文件 · 大小 {sum(p.stat().st_size for p in OUT.rglob('*') if p.is_file()) / 1024 / 1024:.1f} MB")
    print("   密钥扫描：0 命中")

    if args.zip:
        archive = shutil.make_archive(str(OUT), "zip", OUT)
        print("📦 zip：", archive, f"{os.path.getsize(archive) / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    main()
