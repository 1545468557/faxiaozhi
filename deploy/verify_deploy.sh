#!/usr/bin/env bash
#
# 法小智 · 上线后验收
#
#   deploy/verify_deploy.sh https://faxiaozhi.example.com
#   deploy/verify_deploy.sh https://faxiaozhi.example.com --server ubuntu@1.2.3.4
#   deploy/verify_deploy.sh https://faxiaozhi.example.com --sms 13800138000     # 会真发短信
#
# 只读，不改动远端任何状态（`--sms` 那一步会发一条真短信）。
#
# 这里断言的都是**部署后最容易悄悄坏掉、而且不报错**的东西：
#   · 模型掉回 stub（MODEL_API_KEY 没传上去）→ 界面能开、答不了
#   · 访客隔离没生效（GUEST_COOKIE_SECRET 没配）→ 所有人共用一个桶
#   · 后台对匿名开放 → 不是「能不能用」问题，是数据泄露
#   · 8010/8011 被防火墙漏出去 → 后端裸奔，绕过 Caddy 与全部门禁
#
# 退出码：0 全部通过（可以含警告）；1 有硬失败。
#
set -uo pipefail

BASE="${1:-}"
shift || true
SERVER=""
SMS_PHONE=""
NO_PROXY=0

usage() { sed -n '2,16p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --server)   SERVER="${2:?}"; shift 2 ;;
    --sms)      SMS_PHONE="${2:?}"; shift 2 ;;
    --no-proxy) NO_PROXY=1; shift ;;   # 本机彩排时对 localhost 用它（沙箱里回环也走代理）
    -h|--help)  usage; exit 0 ;;
    *) printf '不认识的参数：%s\n' "$1" >&2; exit 2 ;;
  esac
done

[[ -n "$BASE" ]] || { usage; exit 2; }
BASE="${BASE%/}"
HOST="${BASE#*://}"; HOST="${HOST%%/*}"

PASS=0; FAIL=0; WARN=0

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SELF_DIR/.." && pwd)"
PY="$REPO_ROOT/.venv/bin/python"
[[ -x "$PY" ]] || PY="python3"

CURL=(curl -sS -m 15)
[[ $NO_PROXY -eq 1 ]] && CURL+=(--noproxy '*')

ok()   { PASS=$((PASS+1)); printf '  \033[1;32m✓\033[0m %s\n' "$1"; }
bad()  { FAIL=$((FAIL+1)); printf '  \033[1;31m✗\033[0m %s\n' "$1"; [[ -n "${2:-}" ]] && printf '      %s\n' "$2"; }
meh()  { WARN=$((WARN+1)); printf '  \033[1;33m!\033[0m %s\n' "$1"; [[ -n "${2:-}" ]] && printf '      %s\n' "$2"; }

#: 返回三位状态码 —— 连不上时返回 000。
#  ⚠️ 别写成 `... -w '%{http_code}' ... || echo 000`：
#     curl **连接失败时既会打印 000、又会以非零码退出**，`||` 于是再补一个 000，
#     结果是 "000000"；而脚本里所有 `== 000` 的判断就此**全部失效**。
#     2026-10-10 实测后果：把「8010/8011/3001 端口已关」误判成「后端裸奔」，
#     连我随后加的负对照守卫也被它带偏（对照端口也返回 000000）。
#     教训：**字符串 bug 会伪装成安全问题**，先怀疑取值，再怀疑攻击面。
#     这里改成「取回后校验格式」，任何不是三位数字的一律归零。
http_code() {
  local out
  out="$("${CURL[@]}" -o /dev/null -w '%{http_code}' "$@" 2>/dev/null)" || true
  [[ "$out" =~ ^[0-9]{3}$ ]] || out="000"
  printf '%s' "$out"
}
#: 返回 "状态码<换行>响应体"（注意：码在**最后一行**）
http_body() {
  local out
  out="$("${CURL[@]}" -w '\n%{http_code}' "$@" 2>/dev/null)" || true
  local code="${out##*$'\n'}"
  [[ "$code" =~ ^[0-9]{3}$ ]] || code="000"
  printf '%s\n%s' "${out%$'\n'*}" "$code"
}

json_field() { # $1=json $2=python 表达式（变量名 d）
  printf '%s' "$1" | "$PY" -c "
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print('<不是 JSON>'); raise SystemExit
try:
    print($2)
except Exception as exc:
    print(f'<取不到: {exc}>')
" 2>/dev/null || printf '<解析失败>'
}

printf '\n\033[1m验收目标：%s\033[0m\n\n' "$BASE"

# ------------------------------------------------------------------ 1. TLS + 首页
printf '\033[1m1. 站点可达性\033[0m\n'
if "${CURL[@]}" -o /dev/null "$BASE/" 2>/dev/null; then
  code="$(http_code "$BASE/")"
  [[ "$code" == 200 ]] && ok "首页 200（TLS 证书链同时校验通过）" \
                       || bad "首页返回 $code" "不是 200；看 Caddy 日志：docker compose logs caddy"
else
  bad "连不上 $BASE" "可能原因：域名没解析到服务器 / 80·443 没放行 / 内地未备案 / Caddy 没起"
fi

# ------------------------------------------------------------------ 2. v3 探活（模型是不是掉回 stub）
printf '\033[1m2. 后端探活与模型\033[0m\n'
if [[ "$(http_code "$BASE/api/bff/v3/health")" == 200 ]]; then
  body="$(http_body "$BASE/api/bff/v3/health")"
  json="${body%$'\n'*}"
  ok_flag="$(json_field "$json" "d.get('ok')")"
  stub="$(json_field "$json" "d.get('is_stub')")"
  [[ "$ok_flag" == True ]] && ok "v3 /api/health → ok" || bad "v3 探活 ok=$ok_flag"
  if [[ "$stub" == True ]]; then
    bad "模型掉回 stub —— 线上答不了问题，只会返回占位文本" \
        "MODEL_API_KEY 没进 .env.production，或容器没重启：docker compose up -d backend v3"
  else
    ok "模型不是 stub（is_stub=false）"
  fi
else
  bad "/api/bff/v3/health 返回 $(http_code "$BASE/api/bff/v3/health")" "v3 容器或 BFF 转发有问题"
fi

# ------------------------------------------------------------------ 3. 首页后端能力 + 访客隔离
printf '\033[1m3. 能力声明与访客隔离\033[0m\n'
# ⚠️ 不要写 `mktemp -t fxz-hdr`：那是 BSD/macOS 的写法。
# macOS 的 mktemp -t 会自己补随机串，GNU（Linux）的**不行** ——
# 直接报 `mktemp: too few X's in template 'fxz-hdr'`，然后脚本继续往下跑、
# 拿着一个空路径去 -D 写响应头，于是一路报「000」「没看到 Cookie」，
# 看起来像站点坏了，其实是临时文件根本没建出来（2026-10-10 在服务器上实测）。
# 用两边都认的显式模板。
RESP_HEADERS="$(mktemp "${TMPDIR:-/tmp}/fxz-hdr.XXXXXX")"
code="$(http_code "$BASE/api/bff/bootstrap" -D "$RESP_HEADERS")"
if [[ "$code" == 200 ]]; then
  body="$(http_body "$BASE/api/bff/bootstrap")"
  json="${body%$'\n'*}"
  name="$(json_field "$json" "d.get('agent',{}).get('name')")"
  [[ "$name" == *法小智* ]] && ok "能力声明正常（agent.name=${name}）" || meh "agent.name=${name}（预期含「法小智」）"
elif [[ "$code" == 403 ]]; then
  bad "被前端来源白名单拒了（403）" "ALLOWED_HOSTS 没写成 $HOST —— 改 .env.production 后重启 frontend"
else
  bad "/api/bff/bootstrap 返回 $code"
fi

if grep -qi 'set-cookie: *fzx_guest=' "$RESP_HEADERS" 2>/dev/null; then
  ok "访客 Cookie 已下发（AUTH_GUEST_MODE 与 GUEST_COOKIE_SECRET 生效）"
else
  # 把实际收到的响应头打出来再下结论 —— 否则「没看到」可能只是 grep 又空手而归。
  bad "没看到 fzx_guest Cookie" \
      "AUTH_GUEST_MODE 没开，或 GUEST_COOKIE_SECRET 短于 32 字符（后者会让两个后端直接起不来）"
  printf '      实际响应头：\n'
  sed 's/^/        /' "$RESP_HEADERS" | head -20
fi
rm -f "$RESP_HEADERS"

# ------------------------------------------------------------------ 4. 后台不可匿名
printf '\033[1m4. 后台入口\033[0m\n'
code="$(http_code "$BASE/api/bff/admin/overview")"
case "$code" in
  401|403) ok "匿名访问后台被拒（${code}）" ;;
  200)     bad "匿名能读后台总览（200）—— 这是数据泄露，立刻停机检查" ;;
  *)       meh "后台返回 ${code}（预期 401/403）" ;;
esac

# ------------------------------------------------------------------ 5. 裸端口
printf '\033[1m5. 后端裸端口（这一条最容易漏，也最要紧）\033[0m\n'
if [[ -z "$SERVER" ]]; then
  meh "跳过（没给 --server，取不到公网 IP）" "补上 --server ubuntu@<IP> 再跑一次"
else
  IP="${SERVER#*@}"
  # ⚠️ 这一步**必须在另一台机器上跑**，而且不能靠「我在不在本机」来判断：
  #    阿里云 ECS 是 NAT 的，公网 IP 根本不在网卡上（`hostname -I` 只有内网地址），
  #    所以 2026-10-10 第一版守卫没拦住，三条全报「裸奔」。
  #    改用一个**肯定没人监听的端口做对照**（负对照）：链路若存在中间盒
  #    （云边缘的未备案拦截层会对任何端口应答），对照端口也会应答 ——
  #    此时本节的结论一律不可信，直接跳过并说明，而不是给出假失败。
  CONTROL_PORT=47131
  CONTROL="$(http_code -m 5 "http://$IP:$CONTROL_PORT/")"
  if [[ "$CONTROL" != 000 ]]; then
    meh "跳过（对照端口 ${CONTROL_PORT} 也应答了 ${CONTROL}）" \
        "链路上有东西对**任何**端口都应答（云边缘拦截层／或本机自连绕回），本节会全部测成假阳性。请在另一台机器上重跑这一步。"
  else
    for port in 8010 8011 3001; do
      if [[ "$(http_code -m 5 "http://$IP:$port/api/health")" == 000 ]]; then
        ok "$port 从公网不可达（正确）"
      else
        bad "$IP:$port 从公网能连上 —— 后端绕过 Caddy 与门禁裸奔" \
            "云安全组 / ufw 只放行 22、80、443；8010/8011/3001 一律不许对外"
      fi
    done
  fi
fi

# ------------------------------------------------------------------ 6. 响应头
printf '\033[1m6. 响应头\033[0m\n'
SRV="$("${CURL[@]}" -D - -o /dev/null "$BASE/" 2>/dev/null | tr -d '\r' | grep -i '^server:' || true)"
if printf '%s' "$SRV" | grep -qi uvicorn; then
  meh "Server 头暴露了后端（${SRV}）" "不影响功能，但等于告诉扫描器「后面是 uvicorn」"
elif [[ -n "$SRV" ]]; then
  ok "Server 头：$SRV"
else
  meh "没有 Server 头"
fi

# ------------------------------------------------------------------ 7. 短信通道（可选，会真发）
if [[ -n "$SMS_PHONE" ]]; then
  printf '\033[1m7. 短信通道（会真发一条）\033[0m\n'
  body="$(http_body -X POST -H 'content-type: application/json' \
          -d "{\"phone\":\"$SMS_PHONE\",\"purpose\":\"login\"}" \
          "$BASE/api/bff/auth/code")"
  json="${body%$'\n'*}"
  code="${body##*$'\n'}"
  if [[ "$code" == 404 ]]; then
    meh "登录接口 404 —— 当前是 AUTH_GUEST_MODE=1（访客免登录）" \
        "guest 模式下 /api/auth/* 全部下线，短信通道暂时用不到；要测就先关 guest 模式"
  elif [[ "$code" == 200 ]]; then
    ch="$(json_field "$json" "d.get('channel')")"
    mk="$(json_field "$json" "d.get('mock')")"
    if [[ "$mk" == True || "$ch" == mock ]]; then
      bad "通道仍是 mock —— 验证码写进了服务端日志，拿到日志就能登录成任意手机号" \
          "把 SMS_PROVIDER 改成 pnvs 并补齐密钥"
    else
      ok "通道 ${ch}，mock=false（去 $SMS_PHONE 上确认收到）"
    fi
  elif [[ "$code" == 502 ]]; then
    bad "发码失败 502 sms_unavailable" "$(json_field "$json" "d.get('error',{}).get('message','')")"
  else
    meh "发码返回 ${code}：$json"
  fi
fi

# ------------------------------------------------------------------ 小结
printf '\n\033[1m小结：%d 通过 / %d 失败 / %d 警告\033[0m\n\n' "$PASS" "$FAIL" "$WARN"
[[ $FAIL -eq 0 ]] || exit 1
exit 0
