#!/usr/bin/env python
"""把《法律法规数据库》（产品经理放在桌面）导入本地法规库 `data/statutes.sqlite3`。

定位（迭代 3 第一步）：**只做数据侧**，不动任何现有功能、不新增任何接口。
产品经理确认数据无误后，再接「法规查找」的接口与界面。

数据来源与取得（如实记录，缺一不可）：
  - 源目录：`~/Desktop/法律法规数据库/`（`法律法规元数据清单.csv` + `全文/<分类>/<标题>_<日期>.docx`）
  - 元数据字段：法规ID(bbbs) / 标题 / 法规分类 / 制定机关 / 公布日期 / 施行日期 / 时效性状态
  - **效力状态逐条入库，检索默认只出「有效」**（本库含 已修改 410 / 已废止 275 / 失效 1，
    不默认过滤就会让用户引到废止条文）
  - 缺字段（如决定类没有施行日期）**如实标缺失**，不猜、不补

用法：
    cd projects/法小智agent开发文档
    uv run python scripts/import_statutes.py               # 导入（已存在则先清空重建）
    uv run python scripts/import_statutes.py --verify      # 只做抽样核对，不写库
"""

from __future__ import annotations

import argparse
import csv
import io
import pathlib
import re
import sqlite3
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = pathlib.Path.home() / "Desktop" / "法律法规数据库"
DB = ROOT / "data" / "statutes.sqlite3"

# **必须锚定在行首**：正文里「违反本法第二十一条规定」这类引用不能被当成新条起点
# （实测踩到：某法规被切成重复条号、内容错位）
# 两种形态都算条号起点（实测：只锚行首会丢约 1.7 万条——排版里条号常跟在小标题后）：
#   ① 行首（可带前导空白）；② 条号后紧跟空白/标点（这样「违反本法第二十一条规定」不算）
ARTICLE_RE = re.compile(
    r"(?m)^[\s　]*(第[一二三四五六七八九十百千零〇两]+条(?:之[一二三四五六七八九十]+)?)"
    r"|(第[一二三四五六七八九十百千零〇两]+条(?:之[一二三四五六七八九十]+)?)(?=[\s　，,、。：；（）(])"
)


def article_no(match: "re.Match[str]") -> str:
    return match.group(1) or match.group(2)
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def cjk_space(text: str) -> str:
    """把中日韩文字逐字用空格切开：SQLite 的 unicode61 分词器不会切中文，
    不这样做「押金 退还」这类查询在 FTS5 里永远搜不到（实测 0 条）。
    查询侧同样处理后再作为短语匹配。"""
    return CJK_RE.sub(lambda m: f" {m.group(0)} ", text)


def to_query(user_query: str) -> str:
    """把用户输入转成 FTS5 查询：中文逐字空格化后按短语 AND 起来。"""
    parts = [part.strip() for part in re.split(r"[\s，,、]+", user_query) if part.strip()]
    return " AND ".join(f'"{cjk_space(part)}"' for part in parts)
# 目录名与"法规分类"不是一一对应：决定/修正案/法律解释都归到「法律」目录
CATEGORY_DIR = {
    "宪法": "宪法",
    "法律": "法律",
    "法律解释": "法律",
    "修正案": "法律",
    "有关法律问题和重大问题的决定（部分）": "法律",
    "修改、废止的决定": "法律",
    "行政法规": "行政法规",
    "司法解释": "司法解释",
    "监察法规": "监察法规",
}
# 效力状态：默认只检索 VALID；其余一律标出来
STATUS = {"有效": ("valid", "现行有效"), "已修改": ("amended", "已被修改"), "已废止": ("repealed", "已废止"),
          "失效": ("invalid", "已失效"), "尚未生效": ("pending", "尚未生效"),
          "不适用(决议/决定类)": ("na", "决议/决定类，不标注时效性")}


def read_text(path: pathlib.Path) -> tuple[str, str]:
    """返回 (正文, 实际解析方式)。实测有些文件扩展名是 .docx 但内容其实是 PDF。"""
    sys.path.insert(0, str(ROOT))
    from app.tools.document import _read_docx, _read_pdf

    data = path.read_bytes()
    if data[:4] == b"%PDF":
        return _read_pdf(data), "pdf"
    return _read_docx(data), "docx"


def split_articles(text: str) -> list[tuple[str, str]]:
    """按「第X条」把正文切成条文。切不出条文的（决议/决定/批复）整篇作为一条。"""
    hits = list(ARTICLE_RE.finditer(text))
    if not hits:
        body = text.strip()
        return [("（全文）", body)] if body else []
    out: list[tuple[str, str]] = []
    head = text[: hits[0].start()].strip()
    if head:
        out.append(("（题注）", head))
    for index, hit in enumerate(hits):
        end = hits[index + 1].start() if index + 1 < len(hits) else len(text)
        body = text[hit.start() : end].strip()
        out.append((article_no(hit), body))
    return out


def file_index() -> tuple[dict[tuple[str, str], pathlib.Path], dict[str, list[pathlib.Path]]]:
    """文件名形如「标题_2021-03-12.docx」。

    **按 (标题, 日期) 精确匹配**——同名法规有多个版本（如某法 2008 版 / 2020 版），
    只按标题会把正文配错版本（实测踩到：索引 2022 vs 元数据 2476）。标题只作兜底。
    """
    exact: dict[tuple[str, str], pathlib.Path] = {}
    by_title: dict[str, list[pathlib.Path]] = {}
    for path in sorted(SRC.glob("全文/*/*.docx")):
        stem = path.stem
        title, _, date = stem.rpartition("_")
        title = (title or stem).strip()
        date = date.strip()
        if date and date != "无日期":
            exact[(title, date)] = path
        by_title.setdefault(title, []).append(path)
    return exact, by_title


def load_metadata() -> list[dict[str, str]]:
    csv_path = SRC / "法律法规元数据清单.csv"
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def build(conn: sqlite3.Connection, rows: list[dict[str, str]], files: tuple[dict[tuple[str, str], pathlib.Path], dict[str, list[pathlib.Path]]]) -> dict[str, int]:
    conn.executescript(
        """
        DROP TABLE IF EXISTS articles_fts;
        DROP TABLE IF EXISTS articles;
        DROP TABLE IF EXISTS statutes;
        DROP TABLE IF EXISTS meta;
        CREATE TABLE statutes (
            bbbs TEXT PRIMARY KEY, title TEXT NOT NULL, category TEXT, organ TEXT,
            publish_date TEXT, effective_date TEXT,
            status_code TEXT NOT NULL, status_text TEXT NOT NULL,
            source_file TEXT, char_len INTEGER, article_count INTEGER, imported_at TEXT
        );
        CREATE TABLE articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT, bbbs TEXT NOT NULL, seq INTEGER NOT NULL,
            no TEXT, text TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE articles_fts USING fts5(
            title, no, body, bbbs UNINDEXED, no_raw UNINDEXED, tokenize='unicode61'
        );
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        """
    )
    exact_index, by_title = files
    stats = {"statutes": 0, "articles": 0, "no_file": 0, "empty": 0, "by_date": 0, "by_title_only": 0, "ambiguous": 0, "via_pdf": 0}
    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    for row in rows:
        title = (row.get("标题") or "").strip()
        bbbs = (row.get("法规ID") or "").strip()
        if not title or not bbbs:
            continue
        raw_status = (row.get("时效性状态") or "").strip()
        code, text = STATUS.get(raw_status, ("unknown", raw_status or "效力状态缺失"))
        publish_date = (row.get("公布日期") or "").strip()
        path = exact_index.get((title, publish_date))
        if path is not None:
            stats["by_date"] += 1
        else:
            candidates = by_title.get(title) or []
            if len(candidates) > 1:
                stats["ambiguous"] += 1
            if candidates:
                path = candidates[-1]
                stats["by_title_only"] += 1
        body = ""
        if path is not None:
            try:
                body, how = read_text(path)
                if how == "pdf":
                    stats["via_pdf"] += 1
            except Exception as exc:  # 解析失败不阻断整体导入，如实计数
                body = ""
                print(f"  ! 解析失败：{title}（{exc}）", file=sys.stderr)
        else:
            stats["no_file"] += 1
        articles = split_articles(body)
        if not articles:
            stats["empty"] += 1
        conn.execute(
            "INSERT INTO statutes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (bbbs, title, row.get("法规分类", ""), row.get("制定机关", ""),
             row.get("公布日期", ""), row.get("施行日期", ""), code, text,
             str(path.relative_to(SRC)) if path else "", len(body), len(articles), now),
        )
        for seq, (no, art_text) in enumerate(articles, start=1):
            conn.execute("INSERT INTO articles (bbbs, seq, no, text) VALUES (?,?,?,?)", (bbbs, seq, no, art_text))
            conn.execute(
                "INSERT INTO articles_fts (title, no, body, bbbs, no_raw) VALUES (?,?,?,?,?)",
                (cjk_space(title), cjk_space(no), cjk_space(art_text), bbbs, no),
            )
            stats["articles"] += 1
        stats["statutes"] += 1
    conn.execute("INSERT INTO meta VALUES ('imported_at', ?)", (now,))
    conn.execute("INSERT INTO meta VALUES ('source_dir', ?)", (str(SRC),))
    conn.execute("INSERT INTO meta VALUES ('source_meta_file', '法律法规元数据清单.csv')", ())
    conn.execute("INSERT INTO meta VALUES ('default_filter', 'status_code = valid')", ())
    conn.commit()
    return stats


def verify(conn: sqlite3.Connection) -> None:
    print("\n【抽样核对】随机 5 篇：库里条文 vs 原文首句")
    rows = conn.execute(
        "SELECT s.bbbs, s.title, s.status_text, s.article_count FROM statutes s "
        "WHERE s.article_count > 3 AND s.status_code = 'valid' ORDER BY RANDOM() LIMIT 5"
    ).fetchall()
    for bbbs, title, status, count in rows:
        first = conn.execute("SELECT no, text FROM articles WHERE bbbs = ? AND seq = 2", (bbbs,)).fetchone()
        print(f"  《{title}》[{status}] {count} 条｜{first[0] if first else '—'}：{(first[1][:48] if first else '')}…")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true", help="只核对已有库，不重新导入")
    args = parser.parse_args()

    if not SRC.exists():
        raise SystemExit(f"找不到源目录：{SRC}")

    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB)
    if not args.verify:
        rows = load_metadata()
        files = file_index()
        exact_count, by_title = files
        print(f"元数据 {len(rows)} 条；全文文件 {sum(len(v) for v in by_title.values())} 个（可按日期精确匹配 {len(exact_count)} 个）")
        stats = build(conn, rows, files)
        print("导入完成：", stats)
    print("\n【库统计】")
    for label, sql in [
        ("法规总数", "SELECT COUNT(*) FROM statutes"),
        ("条文总数", "SELECT COUNT(*) FROM articles"),
        ("按状态", "SELECT status_text, COUNT(*) FROM statutes GROUP BY status_text ORDER BY 2 DESC"),
    ]:
        if label == "按状态":
            print(f"  {label}：", " · ".join(f"{r[0]} {r[1]}" for r in conn.execute(sql)))
        else:
            print(f"  {label}：{conn.execute(sql).fetchone()[0]}")
    print("  无正文的法规：%d 条" % conn.execute("SELECT COUNT(*) FROM statutes WHERE article_count = 0").fetchone()[0])
    print("  库大小：%.1f MB" % (DB.stat().st_size / 1024 / 1024))

    print("\n【检索样例】只搜有效条文：「押金 退还」")
    for title, no, body in conn.execute(
        """SELECT f.title, f.no, substr(f.body,1,60) FROM articles_fts f
           JOIN statutes s ON s.bbbs = f.bbbs
           WHERE articles_fts MATCH ? AND s.status_code = 'valid' LIMIT 5""",
        (to_query("押金 退还"),),
    ):
        print(f"  《{title}》{no}：{body}…")

    verify(conn)
    conn.close()


if __name__ == "__main__":
    main()
