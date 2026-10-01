# 法小智：单台轻量服务器公开部署

这套配置把前端、旧版 API、新版问答 API 和 HTTPS 反向代理放在同一台 Linux 服务器。只开放 80/443；两个 Python API 留在 Docker 内网。法规库、访客问答库、运行记录和导出文件保存在服务器磁盘，容器重建不会清空。

## 服务器与域名

- 建议先选 Ubuntu 24.04 x86_64、2 核 2 GB、至少 40 GB 系统盘。2 GB 是低成本试运行规格；构建前端时若内存不足，需要添加交换空间或升级到 4 GB。
- 若选择中国内地节点，公开网站需要先完成 ICP 备案。中国香港节点无需该备案，但内地访问质量可能波动。腾讯云官方[价格总览](https://cloud.tencent.com/document/product/1207/73452)与[备案说明](https://cloud.tencent.com/document/product/243/19630)供购买时核对。
- 有自己的域名时，将 A 记录指向服务器公网 IP。暂时没有域名时，可先用免费地址 `<公网IP>.sslip.io`（例如 `1.2.3.4.sslip.io`）做公开试运行；它会解析到嵌入的 IP，也可以由 Caddy 申请 HTTPS 证书，详见 [sslip.io 说明](https://sslip.io/)。此地址依赖第三方 DNS，长期使用建议换自有域名。
- 防火墙开放 80、443 和受限的 SSH 端口。公开环境必须使用 HTTPS，访客 Cookie 设置为 Secure。
- 按 [Docker 官方 Ubuntu 安装指南](https://docs.docker.com/engine/install/ubuntu/)安装 Docker Engine 和 Compose 插件。

## 第一次发布

把已审核的代码放到服务器，进入仓库根目录，然后：

```bash
cp deploy/production.env.example .env.production
chmod 600 .env.production
openssl rand -hex 32
```

把生成的随机值填入 `.env.production` 的 `GUEST_COOKIE_SECRET`，并填 `SITE_HOST`、`ALLOWED_HOSTS`、模型密钥和北大法宝 MCP 配置。两个 Host 填相同的正式域名，不带协议或路径。密钥只保留在服务器环境文件，不提交 Git。`AUTH_REQUIRE_LOGIN=1` 和 `AUTH_GUEST_MODE=1` 必须同时保留，这样网站无登录页且访客数据分开。

从原部署电脑制作法规库一致性快照：

```bash
uv run python scripts/snapshot_statutes.py /tmp/faxiaozhi-statutes.sqlite3
scp /tmp/faxiaozhi-statutes.sqlite3 USER@SERVER:~/faxiaozhi/data/statutes.sqlite3
```

把 `USER@SERVER:~/faxiaozhi` 换成实际 SSH 用户和仓库路径。**只传法规库快照**；原电脑的其他 `data/` 文件、合同、会话和导出文件不传。服务器端先执行 `mkdir -p data runs exports`。若仓库不在 `~/faxiaozhi`，相应修改目标路径。

在服务器仓库根目录依次执行：

```bash
docker compose -f deploy/compose.yaml up -d --build backend v3 frontend
docker compose -f deploy/compose.yaml exec backend .venv/bin/python scripts/preflight_public.py
docker compose -f deploy/compose.yaml up -d caddy
```

只有预检报告 `ok: true` 才执行最后一行。Caddy 会按域名申请 HTTPS 证书。首次访问要确认首页、法规检索、问答、合同审查及双访客数据隔离，并查看 `docker compose -f deploy/compose.yaml logs --tail=100` 排查失败。

## 更新和备份

发布新代码：`git pull` 后重新运行 `docker compose -f deploy/compose.yaml up -d --build backend v3 frontend`。更新期间 `data/`、`runs/`、`exports/` 仍在宿主机，但应定期备份；备份 SQLite 时使用 SQLite backup API，不直接复制正在写入的数据库文件。

本机现有每周法规核验任务只更新本机。服务器就绪后，要将核验通过的法规库快照同步到服务器，先上传为临时文件，再在同一文件系统内原子替换 `data/statutes.sqlite3`，最后验证线上搜索结果。此步骤未接入云端前，不应把“每周自动更新线上法规库”当作已完成。
