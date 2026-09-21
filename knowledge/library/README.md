# 本地依据库（local evidence library）· 法小智

> 阶段 2-3 新增。**语料不提交仓库**（`data/` 已被 `.gitignore` 忽略），本目录只放格式与说明。

## 一、这是什么

把**通过合法通道检索到的公开法条与判例原文**跨会话保存下来，用于两件事：

1. **命中复用**：同一检索式 / 同一标识（案号、法规名+条号）再次出现时，直接用本地副本（省法宝配额、法宝抖动时仍可用）；
2. **逐字核验与双源印证**：作为引用核验基准；与法宝实时结果比对，冲突即拦。

**它不做全文检索，也不替代检索。** 候选池始终来自实时检索（避免偏向历史数据），本地库只在"同一查询 / 同一标识"时命中。

## 二、目录与格式

```
data/library/                     # 语料（gitignored，不在仓库里）
├── index.json                    # 检索式 → 条目 的索引（丢失可重建）
├── .lock                         # 写锁（过期 60s 可接管）
├── cases/<slug>.json
└── statutes/<slug>.json
```

单个条目文件：

```json
{
  "schema_version": 1,
  "entry": {
    "entry_id": "case:2023示例民终1001号",
    "content_type": "case",            // case | statute | history
    "identifier": "（2023）示例民终1001号",
    "title": "……",
    "text": "本院认为……",             // 逐字原文（核验基准）
    "meta": {"court": "……", "decided_on": "2023-04-18", "effective_status": "现行有效"},
    "zone": "citable",                 // citable（依据库，可引用）| clue（线索库，不可引用）
    "origin": "mcp",                   // mcp（法宝自动沉淀）| imported（产品经理提供）
    "fetched_at": "2026-09-18T10:00:00+0800",
    "source_run_id": "smokeresume001",
    "content_hash": "……",
    "last_used_at": "2026-09-18T10:00:00+0800",
    "schema_version": 1
  },
  "revisions": []                      // 同标识内容变化时保留旧版本
}
```

`slug` 由 `entry_id` 生成：可读片段 + 内容哈希前 8 位。

## 三、三分区（不可混）

| 分区 | 收什么 | 能被引用吗 |
| --- | --- | --- |
| `citable`（依据库） | 通过引用门禁、有逐字原文的案例与法条 | ✅ |
| `clue`（线索库） | 仅摘要（`abstract_only`）、以及**尚未通过门禁**的检索内容 | ❌ 永不作为引用 |
| 不入库 | 失败返回、用户上传材料、夹具数据、疑似含密钥的内容 | ❌ |

**用户上传材料绝不入库**（隐私红线，有测试断言）；库中不含密钥与真实地址。

## 四、合法来源（不做爬虫）

1. **法宝自动沉淀**：真实检索成功且通过门禁的内容（`library.write_on_success`，可关）。
2. **合法导入**：产品经理提供的法规/判例文件 → `scripts/import_library.py`。

**不主动爬取任何站点**（国家法律法规数据库 robots 明禁自动化；案例库 403；裁判文书网登录+反爬，均已实测）。
对外/商用前需与法宝确认"客户端本地缓存"的合同条款。

## 五、容量与清理

- 默认上限：**5 万条 / 2 GB**（`config.yaml: library.max_entries` / `max_bytes`）。
- 超限按**最久未使用（LRU）**淘汰；索引在淘汰后自动清理失效项，也可 `rebuild`。
- 单条原文上限 `max_entry_chars`（默认 20 万字符），超长拒收。

## 六、常用操作

```bash
# 查看状态（不打印正文）
curl -s 'http://127.0.0.1:8010/api/library'

# 导入（默认 dry-run，只报告不写入）
uv run python scripts/import_library.py --file /path/to/statutes.json
uv run python scripts/import_library.py --file /path/to/statutes.json --apply

# 备份后再导入
uv run python scripts/import_library.py --file /path/to/statutes.json --apply --backup
```
