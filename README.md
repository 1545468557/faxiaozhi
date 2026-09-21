# 法小智 · Faxiaozhi

法律 AI 工作台：法律问答、类案检索与研究、合同审查、法规查找。

本仓库为当前开发版本的独立开源快照，包含前后端源码、离线测试和交互设计预览。尚非完成生产验收的托管产品。AI 输出供研究与辅助工作使用，实际业务须核对原文与适用情况。

## 技术栈

- Python 3.12+ / FastAPI / Pydantic / SQLite
- React 19 / TypeScript / vinext / Vite / Tailwind CSS
- 可配置模型服务与北大法宝 MCP；凭据仅由后端读取

## 本地运行

需要 Python 3.12+、uv、Node.js 22.13+ 和 package.json 指定版本的 pnpm。

```sh
git clone https://github.com/1545468557/faxiaozhi.git
cd faxiaozhi
uv sync
cp .env.example .env
uv run python -m app.server
```

后端默认 `http://127.0.0.1:8010`，API 文档 `/docs`。未填写模型密钥时使用离线 stub；离线内容不是实际检索或法律解答。

另开终端启动前端：

```sh
cd frontend
pnpm install --frozen-lockfile
cp .env.example .env.local
pnpm dev
```

访问 `http://localhost:5174`。首次使用在仓库根目录生成自己的邀请码：

```sh
uv run python -c "from app.auth import core; print(core.generate_invite('local setup')['code'])"
```

在登录页选择邀请码注册。请勿公开邀请码、密码或生成的账号数据库。

首页新版法律问答使用 `app_v3/`（8011）；类案检索、合同审查与账号功能使用 `app/`（8010）。完整体验还需另开终端，在仓库根目录运行：

```sh
APP_PORT=8011 uv run python -m app_v3.main
```

`frontend/.env.local` 可设置 `V3_BACKEND_BASE_URL=http://127.0.0.1:8011`，该地址也是默认值。旧 `/consult` 页面仍使用 8010。两个后端并存是当前过渡状态，见 `app_v3/README.md`。

## 配置真实服务

在根目录 `.env` 配置 `MODEL_API_KEY`、`MODEL_BASE_URL` 和 `MODEL_ID`。北大法宝使用 `.env.example` 中具名 `PKULAW_URL_*` 槽位，映射见 `config.yaml`。真实模型和检索调用可能收费，需自行获得服务权限。

不附带法规原始库、供应商付费数据或真实材料；因此本地法规查找初始可能为空。已有合法来源的数据可用 `scripts/import_statutes.py` 或 `scripts/import_library.py` 导入，先阅读脚本参数和输入格式。不要把生成的数据库提交到 Git。

## 全站设计预览

```sh
python3 -m http.server 5190 --bind 127.0.0.1 --directory previews/site-refresh
```

访问 `http://127.0.0.1:5190/#ask`：蓝白配色、导航动效，以及问答、研究、合同审查和报告等示例流程。它是独立静态预览，尚未全量合并进正式界面；不调用模型、不上传合同。报告及案例内容均为示例，部分交互仅展示反馈。

另外保留 `previews/contract-design/` 和 `previews/research-design/` 两份专项预览。

## 验证

```sh
uv run pytest
cd frontend
pnpm test
pnpm typecheck
pnpm lint
pnpm build
```

后端 pytest 配置强制使用离线模型。`smoke_real.py` 和名称含 real 的脚本用于真实外部调用，运行前检查配置及费用。

## 目录

| 目录 | 内容 |
| --- | --- |
| app/ | 主后端、引用核验、工作流、账号与导出 |
| frontend/ | 正式前端与 BFF |
| tests/、frontend/tests/ | 自动化测试 |
| fixtures/ | 标注为虚构的离线测试数据 |
| knowledge/ | 分析框架与依据库格式说明 |
| scripts/ | 导入、诊断及验证脚本 |
| previews/ | 独立界面预览 |
| app_v3/ | 实验后端 |

## 当前限制

- 主后端部分工作会话仍存于内存，重启可能丢失；账号持久化不代表所有工作流已经持久化。
- 原文缺失、引用未核验和来源冲突可能阻止报告导出。
- 开源发布不等于网站上线；生产部署仍需单独验证数据隔离、备份、权限和运行稳定性。
- 部分工程脚本仍针对原有开发目录，使用前需检查路径。

## 许可证

项目自有源码使用 [MIT](LICENSE)。第三方依赖和随附代码保留其原有许可，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。MIT 不授予第三方数据、商标或服务账号的使用权。
