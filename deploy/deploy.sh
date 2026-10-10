#!/usr/bin/env bash
#
# 法小智 · 一键上线
#
# 服务器到手后，只需要两个值：
#
#   deploy/deploy.sh --server ubuntu@1.2.3.4 --site-host faxiaozhi.example.com
#   deploy/deploy.sh --server ubuntu@1.2.3.4 --site-host 1.2.3.4.sslip.io    # 没域名时
#
# 本机彩排（不碰服务器，在本仓库跑**同一套步骤**，用来提前暴露脚本本身的问题）：
#
#   deploy/deploy.sh --local --skip-caddy
#
# 依序做这些事：
#   生成 .env.production → 打法规库快照 → 装 Docker/rsync → 传代码+配置+快照
#   → 构建镜像 → 起 backend/v3/frontend → 容器内跑预检 → **预检通过才起 Caddy**
#
# 三个刻意的设计：
#
# 1. **预检是闸门，不是提示。** Caddy 是唯一把端口暴露到公网的容器（80/443）。
#    在预检变绿之前不起它，等于「配置不全的站点根本没有公网入口」——
#    而不是「起来了但坏了」，后者会被搜索引擎和第一批用户看到。
#
# 2. **绝不复述密钥。** 所有回显只打键名和「已注入」。脚本中途把 `.env.production`
#    交给 scp，路径也只在 `--verbose` 下打印。
#
# 3. **可重复执行。** rsync 增量传、`compose up -d` 幂等；
#    `data/ runs/ exports/ .env.production` 都在 rsync 排除表里，
#    配合 `--delete` 也不会被删掉 —— 重跑一次不会丢账号和访客额度。
#
# 退出码：0 成功；10 参数/前置检查不过；20 远端依赖装不上；30 构建失败；
#         40 服务起不来；50 **预检没过**（此时 Caddy 未启动，站点未对公网暴露）。
#
set -euo pipefail

# --------------------------------------------------------------------- 基础

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SELF_DIR/.." && pwd)"
COMPOSE_FILE="deploy/compose.yaml"
# 快照是构建产物（165 MB），刻意**不放在仓库里**：
# 一来它不该进 `git status` 的视野，二来某些受限环境（沙箱/只读挂载）
# 会让 SQLite 在建 journal 时直接报 `disk I/O error`，而临时目录是稳的。
SNAPSHOT_DIR="${FXZ_SNAPSHOT_DIR:-${TMPDIR:-/tmp}/faxiaozhi-deploy}"
SNAPSHOT="$SNAPSHOT_DIR/statutes.sqlite3"

MODE="ssh"
SERVER=""
SITE_HOST=""
SSH_KEY=""
SSH_PORT="22"
REMOTE_DIR="faxiaozhi"
SKIP_BUILD=0
SKIP_CADDY=0
SKIP_SNAPSHOT=0
FRESH_SNAPSHOT=0
VERBOSE=0
ASSUME_YES=0

#: 已实测可用的镜像源（2026-10-10）。服务器直连 Docker Hub 通常不通，
#: 不给 daemon 配镜像源的话，构建会卡在 `load metadata` 直到超时。
REGISTRY_MIRRORS=("https://docker.m.daocloud.io" "https://docker.1ms.run" "https://docker.xuanyuan.me")

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '\033[1;33m    ⚠️  %s\033[0m\n' "$*" >&2; }
die()  { printf '\n\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit "${2:-10}"; }

usage() {
  sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  cat <<'TXT'

可选项：
  --key <私钥路径>      ssh/scp 用的私钥
  --port <端口>         ssh 端口，默认 22
  --dir <目录名>        服务器上的目录名（$HOME 下），默认 faxiaozhi
  --local              本机彩排：不 ssh，直接在本仓库执行同样的步骤
  --skip-build         不重新构建镜像（代码没动时省时间）
  --skip-caddy         不起 Caddy（本机彩排必用；也用于「先内部验一遍」）
  --skip-snapshot      不传法规库快照（线上已经有了、或本次没动法规库时用）
  --fresh-snapshot     重新打法规库快照（默认复用已有的，165MB 那个）
  --verbose            打印更多细节（仍然不打印密钥）
  -y, --yes            不交互确认
TXT
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --server)    SERVER="${2:?}"; shift 2 ;;
    --site-host) SITE_HOST="${2:?}"; shift 2 ;;
    --key)       SSH_KEY="${2:?}"; shift 2 ;;
    --port)      SSH_PORT="${2:?}"; shift 2 ;;
    --dir)       REMOTE_DIR="${2:?}"; shift 2 ;;
    --local)     MODE="local"; shift ;;
    --skip-build)     SKIP_BUILD=1; shift ;;
    --skip-caddy)     SKIP_CADDY=1; shift ;;
    --skip-snapshot)  SKIP_SNAPSHOT=1; shift ;;
    --fresh-snapshot) FRESH_SNAPSHOT=1; shift ;;
    --verbose)   VERBOSE=1; shift ;;
    -y|--yes)    ASSUME_YES=1; shift ;;
    -h|--help)   usage; exit 0 ;;
    *) die "不认识的参数：$1（--help 看用法）" ;;
  esac
done

# --------------------------------------------------------------------- 目标和执行封装

if [[ "$MODE" == local ]]; then
  SITE_HOST="${SITE_HOST:-localhost}"
  # 本机彩排时「目标目录」就是本仓库，且不传代码（rsync 自己拷自己会死循环）。
  TARGET_DIR="$REPO_ROOT"
  SSH=()
else
  [[ -n "$SERVER" ]] || die "缺 --server（或加 --local 做本机彩排）"
  if [[ -z "$SITE_HOST" ]]; then
    DETECTED_IP="${SERVER#*@}"
    SITE_HOST="${DETECTED_IP}.sslip.io"
    warn "没给 --site-host，按公网 IP 推成 ${SITE_HOST}（先确认这个域名能解析到服务器）"
  fi
  SSH=(ssh -p "$SSH_PORT" -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new)
  SCP=(scp -P "$SSH_PORT" -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new)
  RSYNC_SSH="ssh -p $SSH_PORT -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new"
  if [[ -n "$SSH_KEY" ]]; then
    SSH+=(-i "$SSH_KEY"); SCP+=(-i "$SSH_KEY"); RSYNC_SSH+=" -i $SSH_KEY"
  fi
  SSH+=("$SERVER")
  # 目标目录在**本地**就解析成绝对路径（先问一次远端 $HOME）。
  # 这样两套模式里每一段远端脚本都只写 `cd "$1"`，
  # 不会出现「本机彩排把 $HOME 又拼了一遍」那种只在一种模式下炸的错。
  TARGET_DIR=""
fi

# 在「服务器」上执行 stdin 里的脚本；位置参数原样透传给远端 bash。
# 本地模式走同一个函数 —— 所以 `--local` 彩排跑的确实是上线时的同一段逻辑。
run_remote() {
  if [[ "$MODE" == local ]]; then
    bash -s -- "$@"
  else
    "${SSH[@]}" bash -s -- "$@"
  fi
}

# --------------------------------------------------------------------- 0. 本机前置检查

step "0/9  本机前置检查"
[[ -f "$REPO_ROOT/$COMPOSE_FILE" ]] || die "找不到 ${COMPOSE_FILE}（脚本要放在仓库的 deploy/ 下）"
[[ -f "$REPO_ROOT/.env" ]] || die "找不到仓库根目录的 .env —— 模型密钥和北大法宝 Token 都从它取"
[[ -f "$REPO_ROOT/data/statutes.sqlite3" ]] || die "找不到 data/statutes.sqlite3 —— 线上法规库要靠它"
command -v docker >/dev/null 2>&1 || die "本机没有 docker（构建镜像要用它）"
info "仓库：$REPO_ROOT"
info "模式：$([[ $MODE == local ]] && echo '本机彩排' || echo "上传到 $SERVER")"
if [[ "$MODE" == ssh ]]; then
  REMOTE_HOME="$("${SSH[@]}" 'printf %s "$HOME"' 2>/dev/null | tr -d '\r')"
  [[ -n "$REMOTE_HOME" ]] || die "连不上 $SERVER 或取不到它的 \$HOME（先确认 ssh $SERVER 能进）"
  TARGET_DIR="$REMOTE_HOME/$REMOTE_DIR"
  info "连通性：ok（远端 \$HOME 为 ${REMOTE_HOME}）"
  info "目标目录：$TARGET_DIR"
fi

# --------------------------------------------------------------------- 1. 生成线上配置

step "1/9  生成 .env.production"
PY_BIN="python3"
[[ -x "$REPO_ROOT/.venv/bin/python" ]] && PY_BIN="$REPO_ROOT/.venv/bin/python"
( cd "$REPO_ROOT" && "$PY_BIN" deploy/render_env.py --site-host "$SITE_HOST" )
info "（这份文件含真实密钥，已被 .gitignore 的 .env* 排除，不会进仓库）"

# --------------------------------------------------------------------- 2. 法规库快照

step "2/9  准备法规库快照"
mkdir -p "$SNAPSHOT_DIR"
if [[ -f "$SNAPSHOT" && $FRESH_SNAPSHOT -eq 0 ]]; then
  info "复用已有快照 $(du -h "$SNAPSHOT" | cut -f1)（要重打就加 --fresh-snapshot）"
else
  rm -f "$SNAPSHOT"
  ( cd "$REPO_ROOT" && "$PY_BIN" scripts/snapshot_statutes.py "$SNAPSHOT" )
fi
info "位置：${SNAPSHOT}（在仓库外，不会被误提交）"

# --------------------------------------------------------------------- 3. 远端依赖

if [[ "$MODE" == ssh ]]; then
  step "3/9  远端依赖（Docker / rsync / 目录）"
  run_remote "$TARGET_DIR" "${REGISTRY_MIRRORS[@]}" <<'EOS'
set -euo pipefail
DIR="$1"; shift
MIRRORS=("$@")
# 先建目录（第一次跑时它还不存在），后面的步骤才 cd 得进去。
mkdir -p "$DIR"/data "$DIR"/runs "$DIR"/exports

# --- 提权方式 ---------------------------------------------------------
# root 直接干；非 root 走「免密 sudo」（-n：拿不到密码就立刻失败，不要挂在这儿等人输）。
# ⚠️ 阿里云 RHEL 系最小镜像上常常 **根本没装 sudo**，所以不能无脑写 sudo。
if [[ "$(id -u)" -eq 0 ]]; then
  SUDO=""
elif sudo -n true 2>/dev/null; then
  SUDO="sudo -n"
else
  echo "    ✗ 当前不是 root，也拿不到免密 sudo。请用 root 登录，或配好 NOPASSWD 再来。" >&2
  exit 20
fi

# --- 包管理器 ---------------------------------------------------------
# Debian 系 apt-get；RHEL 系（Alibaba Cloud Linux / CentOS / Alinux）是 dnf 或 yum。
# 这里实测台机器是 Alibaba Cloud Linux 4 —— 没有 apt-get，硬写 apt 会在第一步就炸。
if command -v apt-get >/dev/null 2>&1; then
  PKG=apt
elif command -v dnf >/dev/null 2>&1; then
  PKG=dnf
elif command -v yum >/dev/null 2>&1; then
  PKG=yum
else
  PKG=none
fi
echo "    远端：$(id -un)@$(hostname)  包管理器=$PKG"

pkg_install() {
  case "$PKG" in
    apt) $SUDO apt-get update -qq && $SUDO apt-get install -y -qq "$@" ;;
    dnf) $SUDO dnf install -y -q "$@" ;;
    yum) $SUDO yum install -y -q "$@" ;;
    *)   return 1 ;;
  esac
}

if ! command -v rsync >/dev/null 2>&1; then
  echo "    装 rsync"
  pkg_install rsync || { echo "    ✗ rsync 装不上（${PKG}）。请手动装好再来。" >&2; exit 20; }
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "    装 Docker（官方脚本）"
  if ! curl -fsSL --connect-timeout 20 https://get.docker.com -o /tmp/fxz-get-docker.sh; then
    echo "    ✗ 拉不到 get.docker.com。按发行版手动装（下面这份是 RHEL 系 + 阿里云源）：" >&2
    echo "      sudo dnf install -y dnf-plugins-core" >&2
    echo "      sudo dnf config-manager --add-repo https://mirrors.aliyun.com/docker-ce/linux/centos/docker-ce.repo" >&2
    echo "      sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin" >&2
    echo "      sudo systemctl enable --now docker" >&2
    exit 20
  fi
  $SUDO sh /tmp/fxz-get-docker.sh
fi

# 决定用不用 sudo：装了 Docker 之后当前会话通常还没进 docker 组。
if docker info >/dev/null 2>&1; then
  DOCKER="docker"
elif $SUDO docker info >/dev/null 2>&1; then
  DOCKER="$SUDO docker"
  echo "    当前账号还没进 docker 组，本次用 sudo 跑（想免 sudo：sudo usermod -aG docker \$USER 后重新登录）"
else
  echo "    ✗ Docker 装了但连不上守护进程，且拿不到提权。请重新登录一次再跑本脚本。" >&2
  exit 20
fi

# compose 子命令：新版是插件（docker compose），老版是 docker-compose。
if ! $DOCKER compose version >/dev/null 2>&1 && ! command -v docker-compose >/dev/null 2>&1; then
  echo "    ✗ 没有 docker compose 插件。RHEL 系装：sudo dnf install -y docker-compose-plugin" >&2
  exit 20
fi

# 镜像源：直连 Docker Hub 在境内基本不通，构建会卡在 load metadata 直到超时。
# ⚠️ 这里**必须合并、不能覆盖**。实测这台机器上 daemon.json 已经存在，
#    里面是 OpenClaw 在用的 default-address-pools —— 整份覆盖会改掉别人的容器网段，
#    属于「为了自己上线，把别人网络弄坏」。所以只加 registry-mirrors 这一个键。
MIRROR_JSON="$(printf '%s\n' "${MIRRORS[@]}" | sed 's/.*/"&"/' | paste -sd, -)"
NEED_RESTART=0
if command -v python3 >/dev/null 2>&1; then
  $SUDO test -f /etc/docker/daemon.json \
    && $SUDO cp -n /etc/docker/daemon.json "/etc/docker/daemon.json.bak.$(date +%s)" 2>/dev/null || true
  # ⚠️ 不能写成 `python3 ... || NEED_RESTART=1` 再取 $? —— 那样 $? 永远是 0。
  #    必须让 `|| rc=$?` 直接接住 python 的退出码（0=已改 / 3=无需改 / 4=保留了原有）。
  rc=0
  python3 - "$MIRROR_JSON" <<'PY' || rc=$?
import json, os, sys
p = "/etc/docker/daemon.json"
want = json.loads("[" + sys.argv[1] + "]")
d = json.load(open(p)) if os.path.exists(p) else {}
cur = d.get("registry-mirrors") or []
if cur == want:
    print("    daemon.json 的 registry-mirrors 已是最新，不动它")
    sys.exit(3)
if cur:
    print(f"    ⚠️ 已有 registry-mirrors={cur}（与本脚本推荐的不同）—— 保留它，不覆盖")
    sys.exit(4)
d["registry-mirrors"] = want
with open(p, "w") as f:
    json.dump(d, f, indent=4, ensure_ascii=False)
    f.write("\n")
print(f"    registry-mirrors: (无) -> {want}")
print(f"    其余键保留：{sorted(k for k in d if k != 'registry-mirrors')}")
PY
  case "$rc" in
    0) NEED_RESTART=1 ;;
    3|4) : ;;
    *)
      echo "    ✗ 合并 daemon.json 失败（退出码 ${rc}）。构建若卡在 load metadata，请手动加：" >&2
      echo "      { \"registry-mirrors\": [${MIRRORS[*]}] }" >&2
      ;;
  esac
elif ! $SUDO test -f /etc/docker/daemon.json; then
  echo "    写 /etc/docker/daemon.json 配镜像源（本机没有 python3，也没有 daemon.json，直接新建）"
  printf '{"registry-mirrors":[%s]}\n' "$MIRROR_JSON" | $SUDO tee /etc/docker/daemon.json >/dev/null
  NEED_RESTART=1
else
  echo "    ⚠️ 没有 python3，且 daemon.json 已存在 —— 不敢拿 sed 改 JSON。请手动加 registry-mirrors：" >&2
  echo "      { \"registry-mirrors\": [${MIRRORS[*]}] }" >&2
fi

if [[ $NEED_RESTART -eq 1 ]]; then
  echo "    重启 docker 让镜像源生效"
  $SUDO systemctl restart docker
  $SUDO systemctl enable docker >/dev/null 2>&1 || true
  # 重启后等守护进程回来，否则下一步 docker info 会假失败。
  for _ in $(seq 1 30); do $DOCKER info >/dev/null 2>&1 && break; sleep 1; done
fi

# 收尾复核：镜像源到底有没有生效，让失败看得见。
# ⚠️ 只取 `Registry Mirrors:` 后面那几行 —— 早先写成 `/Registry Mirrors/,/^$/p`，
#    因为 docker info 那段后面没有真正的空行，把 `LiveRestoreEnabled:` 也吞了进来，
#    打印出来像是「镜像源 = LiveRestoreEnabled:false」，属会误导人的输出。
MIRRORS_LIVE="$($DOCKER info 2>/dev/null | sed -n '/Registry Mirrors:/,+4p' | tail -n +2 | grep -o 'https\?://[^ ]*' | paste -sd' ' -)"
if [[ -n "$MIRRORS_LIVE" ]]; then
  echo "    ✓ 生效的镜像源：$(printf '%s' "$MIRRORS_LIVE" | paste -sd' ' -)"
else
  echo "    ⚠️ docker info 里没有 Registry Mirrors —— 构建可能要从 Docker Hub 直连，慢或超时"
fi
EOS
else
  step "3/9  远端依赖（本机彩排，跳过安装步骤）"
  mkdir -p "$REPO_ROOT/data" "$REPO_ROOT/runs" "$REPO_ROOT/exports"
fi

# --------------------------------------------------------------------- 4/5. 传代码与数据

#: 快照分几片并发传。实测这条链路（本机 → 阿里云上海）**单流只有 ~25 KB/s**，
#: 158MB 要传 105 分钟；4 路并发能到 ~122 KB/s。压缩再省 2.9x。
SNAPSHOT_CHUNKS="${FXZ_SNAPSHOT_CHUNKS:-4}"

md5_of() {
  if command -v md5 >/dev/null 2>&1; then md5 -q "$1"
  else md5sum "$1" | cut -d' ' -f1; fi
}

# 传法规库快照。
# 为什么不用「一条 scp 传完」：2026-10-10 在真机上实测，单流 25 KB/s，
# 158MB 要 105 分钟 —— 这不是理论值，是卡在那里量出来的。
# 改成三段式：本机 gzip（158M→55M）→ 切成 N 片并发 scp（4 路 ≈5x）
# → 远端按 md5 校验后再原子替换。实测 8 分钟传完，且**校验不过就不动线上库**。
upload_snapshot() {
  local tmpu gz md5_local size_raw size_gz n rc=0
  size_raw="$(du -h "$SNAPSHOT" | cut -f1)"
  tmpu="$(mktemp -d "${TMPDIR:-/tmp}/fxz-upload.XXXXXX")"
  gzip -c "$SNAPSHOT" > "$tmpu/snap.gz"
  md5_local="$(md5_of "$tmpu/snap.gz")"
  split -n "$SNAPSHOT_CHUNKS" -d "$tmpu/snap.gz" "$tmpu/part-"
  n="$(find "$tmpu" -name 'part-*' | wc -l | tr -d ' ')"
  size_gz="$(du -h "$tmpu/snap.gz" | cut -f1)"
  info "法规库快照 ${size_raw} → 压缩 ${size_gz}，切 ${n} 片并发上传"

  REMOTE_TMP="/tmp/fxz-upload.$$"
  "${SSH[@]}" "rm -rf '$REMOTE_TMP' && mkdir -p '$REMOTE_TMP'" || die "远端建不了临时目录" 20

  local pids=() f
  for f in "$tmpu"/part-*; do
    "${SCP[@]}" -q "$f" "$SERVER:$REMOTE_TMP/" &
    pids+=("$!")
  done
  for p in "${pids[@]}"; do wait "$p" || rc=1; done
  if [[ $rc -ne 0 ]]; then
    rm -rf "$tmpu"
    "${SSH[@]}" "rm -rf '$REMOTE_TMP'" || true
    die "分片上传失败（有分片没传成功）" 20
  fi

  # 远端：拼 → 验 md5 → 解压 → 原子替换。四步都过才动线上那个文件。
  "${SSH[@]}" "REMOTE_TMP=$REMOTE_TMP" "M=$md5_local" bash -s -- "$TARGET_DIR" <<'EOS' || die "远端拼接/校验失败 —— 线上法规库保持原样未替换" 20
set -euo pipefail
DIR="$1"
cd "$DIR/data"
cat "$REMOTE_TMP"/part-* > "$REMOTE_TMP/snap.gz"
GOT="$(md5sum "$REMOTE_TMP/snap.gz" | cut -d' ' -f1)"
if [ "$GOT" != "$M" ]; then
  echo "    ✗ 分片拼接后 md5 不符：期望 ${M}，实得 ${GOT} —— 不替换线上法规库" >&2
  exit 1
fi
rm -f "$REMOTE_TMP"/part-*
gunzip -c "$REMOTE_TMP/snap.gz" > statutes.sqlite3.new
mv -f statutes.sqlite3.new statutes.sqlite3
rm -rf "$REMOTE_TMP"
echo "    快照已就位：$(du -h statutes.sqlite3 | cut -f1)（md5 校验通过）"
EOS

  rm -rf "$tmpu"
}

if [[ "$MODE" == ssh ]]; then

# --------------------------------------------------------------------- 4/5. 传代码与数据

  step "4/9  传代码（rsync 增量）"
  # 排除表里同时放 data/runs/exports/.env.production ——
  # 配合 --delete 它们只是「不被同步」，**不会**被远端删掉。
  rsync -az --delete --human-readable \
    -e "$RSYNC_SSH" \
    --exclude '.git/' \
    --exclude '.venv/' \
    --exclude '**/node_modules/' \
    --exclude '**/.next/' \
    --exclude '**/.vinext/' \
    --exclude '**/dist/' \
    --exclude '**/__pycache__/' \
    --exclude '**/.pytest_cache/' \
    --exclude '**/.ruff_cache/' \
    --exclude '.env*' \
    --exclude 'data/' \
    --exclude 'runs/' \
    --exclude 'exports/' \
    --exclude 'logs/' \
    --exclude 'previews/' \
    --exclude '*.sqlite3*' \
    --exclude '*.log' \
    "$REPO_ROOT/" "$SERVER:$TARGET_DIR/"

  step "5/9  传配置与法规库快照"
  "${SCP[@]}" "$REPO_ROOT/.env.production" "$SERVER:$TARGET_DIR/.env.production"
  info ".env.production 已上传（含密钥，只走 scp，不进仓库）"
  # 线上已经有一份像样的法规库时默认不重传 —— 165MB 走这条链路不是便宜事。
  REMOTE_SNAP_BYTES="$("${SSH[@]}" "stat -c %s '$TARGET_DIR/data/statutes.sqlite3' 2>/dev/null || echo 0" 2>/dev/null | tr -dc '0-9')"
  REMOTE_SNAP_BYTES="${REMOTE_SNAP_BYTES:-0}"
  if [[ $SKIP_SNAPSHOT -eq 1 ]]; then
    info "按 --skip-snapshot 跳过法规库快照（线上那份原样不动）"
  elif [[ "$REMOTE_SNAP_BYTES" -gt 1000000 && $FRESH_SNAPSHOT -eq 0 ]]; then
    info "线上已有法规库快照（$(( REMOTE_SNAP_BYTES / 1024 / 1024 )) MB），跳过重传"
    info "（要强制重传就加 --fresh-snapshot）"
  else
    upload_snapshot
  fi
else
  step "4/9  传代码（本机彩排，跳过）"
  step "5/9  传配置与快照（本机彩排，直接用仓库里的 data/statutes.sqlite3）"
fi

# --------------------------------------------------------------------- 6. 构建

if [[ $SKIP_BUILD -eq 1 ]]; then
  step "6/9  构建镜像（--skip-build，跳过）"
else
  step "6/9  构建镜像（首次约 5–15 分钟；2 核 2G 的机器最容易在前端 OOM）"
  set +e
  run_remote "$TARGET_DIR" "$COMPOSE_FILE" <<'EOS'
set -euo pipefail
DIR="$1"; COMPOSE="$2"
cd "$DIR"
if docker info >/dev/null 2>&1; then DOCKER="docker"; else DOCKER="sudo -n docker"; fi
$DOCKER compose -f "$COMPOSE" build backend v3 frontend
EOS
  BUILD_RC=$?
  set -e
  if [[ $BUILD_RC -ne 0 ]]; then
    warn "构建失败（rc=${BUILD_RC}）。按报错关键词对号入座："
    warn "  'load metadata ... TLS handshake timeout' → Docker 拉不到基础镜像；给 daemon 配 registry-mirrors。"
    warn "  'ReadTimeoutError ... files.pythonhosted.org' → PyPI 不通；Dockerfile 已默认国内源，"
    warn "    若被覆盖过，重跑时加 --build-arg PIP_INDEX=https://mirrors.cloud.tencent.com/pypi/simple"
    warn "  'npm ERR! ... registry.npmjs.org' → npm 源不通；加 --build-arg NPM_REGISTRY=https://registry.npmmirror.com"
    warn "  'Killed' / 'signal: killed' → 内存不足（前端构建在 2G 机器上会 OOM）；加 2G swap 或换 4G。"
    exit 30
  fi
fi

# --------------------------------------------------------------------- 7. 起服务并等健康

step "7/9  起 backend / v3 / frontend 并等健康"
set +e
run_remote "$TARGET_DIR" "$COMPOSE_FILE" <<'EOS'
set -euo pipefail
DIR="$1"; COMPOSE="$2"
cd "$DIR"
if docker info >/dev/null 2>&1; then DOCKER="docker"; else DOCKER="sudo -n docker"; fi

$DOCKER compose -f "$COMPOSE" up -d backend v3 frontend

probe() {  # $1=容器名 $2=容器内地址
  $DOCKER compose -f "$COMPOSE" exec -T "$1" \
    .venv/bin/python -c "
import sys, urllib.request
try:
    print(urllib.request.urlopen('$2', timeout=3).status)
except Exception as exc:
    sys.exit(1)
" >/dev/null 2>&1
}

# ⚠️ 两个后端的探活路径**故意不一样**，别顺手「统一」成 /api/health：
#   · `/api/health` **只有 app_v3 有**（app_v3/main.py 里定义），
#     返回 `{"ok": true, "is_stub": ...}`，纯本地计算。
#   · app/server.py（8010）**根本没有 /api/health** —— 2026-10-10 在真机上打过去
#     是连片的 404；而探活只认 200，于是服务明明起着却被判成「没起来」（rc=40）。
#   · 8010 上最便宜的公开端点（且在 guard.PUBLIC_EXACT 白名单里）是 /api/bootstrap，
#     它只调 cfg.self_check()，**纯读配置、不联网**。
#   所以：8010 → /api/bootstrap；8011 → /api/health。两边都是「不联网、免登录、必 200」。
for i in $(seq 1 40); do
  if probe backend http://127.0.0.1:8010/api/bootstrap && probe v3 http://127.0.0.1:8011/api/health; then
    echo "    两个后端都活了（第 ${i} 次探测）"
    break
  fi
  if [ "$i" = 40 ]; then
    echo "    ✗ 等了 120 秒后端仍未就绪" >&2
    $DOCKER compose -f "$COMPOSE" logs --tail=40 backend v3 >&2
    exit 40
  fi
  sleep 3
done

$DOCKER compose -f "$COMPOSE" ps
EOS
HEALTH_RC=$?
set -e
[[ $HEALTH_RC -eq 0 ]] || die "服务没起来（rc=${HEALTH_RC}）" 40

# --------------------------------------------------------------------- 8. 预检闸门

step "8/9  容器内跑预检（这是能不能开公网入口的唯一判据）"
# 别写 `mktemp -t fxz-preflight`：BSD/macOS 认，GNU/Linux 不认
# （报 `too few X's in template`）。本脚本通常在本机跑，但部署脚本不该只在一种系统上能跑。
PREFLIGHT_LOG="$(mktemp "${TMPDIR:-/tmp}/fxz-preflight.XXXXXX")"
set +e
run_remote "$TARGET_DIR" "$COMPOSE_FILE" 2>&1 <<'EOS' | tee "$PREFLIGHT_LOG"
set -euo pipefail
DIR="$1"; COMPOSE="$2"
cd "$DIR"
if docker info >/dev/null 2>&1; then DOCKER="docker"; else DOCKER="sudo -n docker"; fi
$DOCKER compose -f "$COMPOSE" exec -T backend .venv/bin/python scripts/preflight_public.py
EOS
PREFLIGHT_RC=${PIPESTATUS[0]}
set -e

if [[ $PREFLIGHT_RC -ne 0 ]]; then
  warn "预检未通过（rc=${PREFLIGHT_RC}）—— **Caddy 不会启动**，所以站点还没有公网入口。"
  warn "上面 JSON 里 checks 为 false 的每一项都要修；改完 .env.production 重跑本脚本即可。"
  warn "对应关系："
  warn "  login_gate_enabled / isolated_guest_mode / https_only_cookie → AUTH_* 三项"
  warn "  guest_secret_present   → GUEST_COOKIE_SECRET 至少 32 字符"
  warn "  model_key_present      → MODEL_API_KEY"
  warn "  pkulaw_*_present/configured → PKULAW_MCP_TOKEN 与 PKULAW_URL_*"
  warn "  statutes_db_present/nonempty   → data/statutes.sqlite3 没传上去或为空"
  warn ""
  warn "短信通道**不进 checks**：AUTH_GUEST_MODE=1 时发码接口整个返回 404，"
  warn "短信用不到，所以只出现在 warnings 里。切到登录模式（AUTH_GUEST_MODE=0）后"
  warn "它会立刻变回硬拦 —— 那时候 SMS_PROVIDER=mock 等于「拿到日志就能登录成任意手机号」。"
  exit 50
fi
info "预检全绿 ✓"

# --------------------------------------------------------------------- 9. Caddy + 验收

if [[ $SKIP_CADDY -eq 1 ]]; then
  step "9/9  起 Caddy（--skip-caddy，跳过）"
  warn "站点还没有公网入口。要开入口：去掉 --skip-caddy 重跑。"
else
  step "9/9  起 Caddy（申请证书 + 开 80/443）"
  set +e
  run_remote "$TARGET_DIR" "$COMPOSE_FILE" "$SITE_HOST" <<'EOS'
set -euo pipefail
DIR="$1"; COMPOSE="$2"; HOST="$3"
cd "$DIR"
if docker info >/dev/null 2>&1; then DOCKER="docker"; else DOCKER="sudo -n docker"; fi
$DOCKER compose -f "$COMPOSE" up -d caddy
sleep 5
$DOCKER compose -f "$COMPOSE" logs --tail=25 caddy
echo
echo "    Caddy 报错最多的一种：域名解析不到这台机器 / 80 端口没开 → 证书签不下来。"
echo "    内地服务器另有一层：域名没备案 → 80/443 直接被拦，表现为证书一直 pending。"
EOS
  CADDY_RC=$?
  set -e
  [[ $CADDY_RC -eq 0 ]] || warn "Caddy 启动命令返回 ${CADDY_RC}，看上面的日志。"

  if [[ "$MODE" == ssh ]]; then
    step "验收（对着公网地址跑）"
    set +e
    bash "$SELF_DIR/verify_deploy.sh" "https://$SITE_HOST" --server "$SERVER"
    VERIFY_RC=$?
    set -e
    [[ $VERIFY_RC -eq 0 ]] && info "验收通过 ✓" || warn "验收有未通过项，见上面输出。"
  fi
fi

# --------------------------------------------------------------------- 收尾

rm -f "$PREFLIGHT_LOG"
bold ""
bold "上线流程结束。"
if [[ "$MODE" == local ]]; then
  bold "这是本机彩排 —— 服务器上还没跑过。"
fi
cat <<TXT

日常运维（在 $TARGET_DIR 目录下）：
  看日志     docker compose -f $COMPOSE_FILE logs -f backend
  改配置     vim .env.production && docker compose -f $COMPOSE_FILE up -d backend v3 frontend
  重跑预检   docker compose -f $COMPOSE_FILE exec -T backend .venv/bin/python scripts/preflight_public.py
  备份账号库 docker compose -f $COMPOSE_FILE exec -T backend .venv/bin/python -c "import sqlite3;s=sqlite3.connect('data/auth.sqlite3');d=sqlite3.connect('/tmp/b.sqlite3');s.backup(d)"
TXT
