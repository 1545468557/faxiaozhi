# 法小智：单台轻量服务器公开部署

这套配置把前端、旧版 API、新版问答 API 和 HTTPS 反向代理放在同一台 Linux 服务器。只开放 80/443；两个 Python API 留在 Docker 内网。法规库、访客问答库、运行记录和导出文件保存在服务器磁盘，容器重建不会清空。

## 服务器与域名

- 建议先选 Ubuntu 24.04 x86_64、2 核 2 GB、至少 40 GB 系统盘。2 GB 是低成本试运行规格；构建前端时若内存不足，需要添加交换空间或升级到 4 GB。
- 若选择中国内地节点，公开网站需要先完成 ICP 备案。中国香港节点无需该备案，但内地访问质量可能波动。腾讯云官方[价格总览](https://cloud.tencent.com/document/product/1207/73452)与[备案说明](https://cloud.tencent.com/document/product/243/19630)供购买时核对。
- 有自己的域名时，将 A 记录指向服务器公网 IP。暂时没有域名时，可先用免费地址 `<公网IP>.sslip.io`（例如 `1.2.3.4.sslip.io`）做公开试运行；它会解析到嵌入的 IP，也可以由 Caddy 申请 HTTPS 证书，详见 [sslip.io 说明](https://sslip.io/)。此地址依赖第三方 DNS，长期使用建议换自有域名。
- 防火墙开放 80、443 和受限的 SSH 端口。公开环境必须使用 HTTPS，访客 Cookie 设置为 Secure。
- **8010、8011、3001 绝不对外开放**。它们只在 Docker 内网通信；放出去等于后端绕过 Caddy 和全部门禁裸奔。`verify_deploy.sh --server` 会实测这三个端口从公网不可达。
- Docker 由 `deploy/deploy.sh` 自动安装（走官方脚本，拉不到时会打印阿里云镜像源的手工步骤）。

## 第一次发布

**服务器和域名到手后，只需要两个值。**

在**你本机**（不是服务器上）执行：

```bash
deploy/deploy.sh --server ubuntu@<公网IP> --site-host <域名>
# 还没有域名时，用 sslip.io 的免费地址先跑通：
deploy/deploy.sh --server ubuntu@<公网IP> --site-host <公网IP>.sslip.io
```

这一条命令按顺序做完全部事情：

| 步 | 做什么 |
| --- | --- |
| 1 | `deploy/render_env.py` 从本机 `.env` 生成 `.env.production`（模型密钥 + 北大法宝 Token + **全部 14 个** `PKULAW_URL_*` + 随机 `GUEST_COOKIE_SECRET`） |
| 2 | 用 SQLite backup API 打法规库一致性快照 |
| 3 | 服务器上装 Docker / rsync，配 registry-mirrors，建 `data runs exports` |
| 4–5 | rsync 增量传代码；scp 传配置与快照（快照先传 `.new` 再原子改名，避免线上读到半个库） |
| 6 | `docker compose build backend v3 frontend` |
| 7 | 起 backend / v3 / frontend，轮询容器内 `/api/health` 直到两个后端都活 |
| 8 | **容器内跑 `preflight_public.py`，这是闸门** |
| 9 | 预检全绿才 `up -d caddy`（申证书、开 80/443），然后对着公网地址跑一遍 `verify_deploy.sh` |

想在本机先把脚本本身验一遍（不碰服务器、不起 Caddy）：

```bash
deploy/deploy.sh --local --skip-caddy
```

其它开关见 `deploy/deploy.sh --help`。

### 为什么 Caddy 必须最后起

Caddy 是**唯一**把端口暴露到公网的容器。在预检变绿之前不起它，
结果就是「配置不全的站点根本没有公网入口」，而不是「起来了但坏了」——
后者会被搜索引擎和第一批用户看到。预检没过时脚本以退出码 50 结束，并逐条列出
`checks` 里为 `false` 的项对应哪个环境变量。

### 构建为什么默认走国内源

`deploy/Dockerfile.backend` 与 `Dockerfile.frontend` 各留了一个可覆盖的 build-arg：

- `PIP_INDEX`（默认 `https://mirrors.cloud.tencent.com/pypi/simple`）
- `NPM_REGISTRY`（默认 `https://registry.npmmirror.com`）

不是「快一点」的问题，是**不通**：境内直连 `files.pythonhosted.org` 和
`registry.npmjs.org` 会读超时，而报错时构建已经跑到中间层，栈里看不出是网络问题。
要改回官方源：`docker compose -f deploy/compose.yaml build --build-arg PIP_INDEX=https://pypi.org/simple backend v3`。

### 手工走一遍（排错用）

脚本每一步都能单独重跑，顺序不能颠倒：

```bash
python3 deploy/render_env.py --site-host <域名>
uv run python scripts/snapshot_statutes.py /tmp/faxiaozhi-statutes.sqlite3
# 传代码 / 传 .env.production / 传快照到 data/statutes.sqlite3
docker compose -f deploy/compose.yaml build backend v3 frontend
docker compose -f deploy/compose.yaml up -d backend v3 frontend     # ⚠️ 先不起 caddy
docker compose -f deploy/compose.yaml exec -T backend .venv/bin/python scripts/preflight_public.py
docker compose -f deploy/compose.yaml up -d caddy                  # 只有 ok:true 才执行
deploy/verify_deploy.sh https://<域名> --server ubuntu@<公网IP>
```

**只传法规库快照**；本机 `data/` 下的 `auth.sqlite3`、`v3.sqlite3`、`sms-outbox.jsonl`、
`metrics.jsonl` 以及 `runs/`、`exports/` 一律不传（脚本的 rsync 排除表已经这么设了）。
`frontend/.next` 也不传，服务器上重建。

## 更新和备份

发布新代码：`git pull` 后重新运行 `docker compose -f deploy/compose.yaml up -d --build backend v3 frontend`。更新期间 `data/`、`runs/`、`exports/` 仍在宿主机，但应定期备份；备份 SQLite 时使用 SQLite backup API，不直接复制正在写入的数据库文件。

本机现有每周法规核验任务只更新本机。服务器就绪后，要将核验通过的法规库快照同步到服务器，先上传为临时文件，再在同一文件系统内原子替换 `data/statutes.sqlite3`，最后验证线上搜索结果。此步骤未接入云端前，不应把“每周自动更新线上法规库”当作已完成。
