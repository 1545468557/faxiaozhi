#!/bin/bash
# ============================================================
# 法小智 · 双击启动（macOS）
#
# 双击本文件 → 自动打开终端并启动三个服务 → 就绪后自动打开浏览器。
# 等价于手工执行：./start-local.sh
#
#  后端  8010 → http://127.0.0.1:8010/    单页验收界面 + 旧 API
#  v3    8011 → http://127.0.0.1:8011/    新版法律问答后端（首页/咨询页用）
#  前端  5174 → http://localhost:5174/    正式前端（用这个）
#
# 停止：在终端窗口按 Ctrl-C
# ============================================================
cd "$(dirname "$0")" || exit 1

# 服务就绪后自动打开浏览器（前端只监听 IPv6，必须用 localhost）
(
  for _ in $(seq 1 90); do
    if curl -s -o /dev/null --noproxy '*' --max-time 2 http://localhost:5174/; then
      open "http://localhost:5174/"
      break
    fi
    sleep 1
  done
) &

exec ./start-local.sh
