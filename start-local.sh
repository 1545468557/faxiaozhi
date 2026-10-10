#!/usr/bin/env bash
# ============================================================
# 法小智 · 本地一键启动（运行模式由 .env 决定）
#
#   后端  8010 → http://127.0.0.1:8010/      单页验收界面 + 旧 API
#   v3    8011 → http://127.0.0.1:8011/     新版问答后端（首页/咨询页用）
#   前端  5174 → http://localhost:5174/      正式前端（Next.js / vinext）
#
# 用法：  ./start-local.sh          启动全部
#         Ctrl-C                   全部停止
# ============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# ---- 1. 环境准备：pnpm 不在 PATH 时用 corepack 拉起 ----
if ! command -v pnpm >/dev/null 2>&1; then
  for d in "$HOME/.workbuddy/binaries/node/versions"/*/bin; do
    [ -x "$d/pnpm" ] && export PATH="$d:$PATH" && break
  done
fi
if ! command -v pnpm >/dev/null 2>&1; then
  command -v corepack >/dev/null 2>&1 || { echo "缺少 node/npm/corepack，请先装 Node.js >= 22.13（见 frontend/README.md）"; exit 1; }
  corepack enable pnpm
  corepack prepare pnpm@11.19.0 --activate
fi

# ---- 2. 依赖自检（缺了才装） ----
[ -d .venv ] || { echo "[准备] 安装后端依赖 …"; uv sync; }
[ -d frontend/node_modules ] || { echo "[准备] 安装前端依赖 …"; (cd frontend && pnpm install); }
[ -f .env ] || { echo "[准备] 由 .env.example 生成 .env（留空 = 离线模式）"; cp .env.example .env; }
[ -f frontend/.env.local ] || cp frontend/.env.example frontend/.env.local

mkdir -p logs

# ---- 3. 启动三个服务 ----
echo "[1/3] 后端 8010 …"
uv run python -m app.server > logs/backend-8010.log 2>&1 &
P1=$!

echo "[2/3] v3 后端 8011 …"
APP_PORT=8011 uv run python -m app_v3.main > logs/backend-8011.log 2>&1 &
P2=$!

echo "[3/3] 前端 5174 …"
( cd frontend && pnpm dev > ../logs/frontend-5174.log 2>&1 ) &
P3=$!

cleanup() {
  echo; echo "正在停止 …"
  kill "$P1" "$P2" "$P3" 2>/dev/null || true
  wait 2>/dev/null || true
  echo "已全部停止。"
}
trap cleanup INT TERM

# ---- 4. 就绪探测（前端只监听 IPv6，必须用 localhost） ----
wait_for() { # $1=url $2=描述
  for _ in $(seq 1 40); do
    curl -s -o /dev/null --noproxy '*' "$1" && { echo "  ✓ $2 就绪 → $1"; return 0; }
    sleep 1
  done
  echo "  ✗ $2 未就绪（看 logs/ 里的日志）"; return 1
}

echo; echo "等待服务就绪 …"
wait_for http://127.0.0.1:8010/      "后端单页界面"
wait_for http://127.0.0.1:8011/api/health "v3 问答后端"
wait_for http://localhost:5174/      "正式前端" || true

cat <<'EOF'

============================================================
 正式前端      http://localhost:5174/          ← 用这个
   · 法律问答  http://localhost:5174/consult
   · 类案研究  http://localhost:5174/research
   · 合同审查  http://localhost:5174/contract
   · 法规查找  http://localhost:5174/statutes
 后端单页界面  http://127.0.0.1:8010/
 接口文档      http://127.0.0.1:8010/docs

 注意：前端只监听 IPv6，请用 localhost，不要用 127.0.0.1:5174。
 日志：logs/{backend-8010,backend-8011,frontend-5174}.log
 停止：在本终端按 Ctrl-C
============================================================
EOF

wait
