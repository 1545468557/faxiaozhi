"""本地法规库检索（迭代 3）：只读查询 + 口话化检索规则。

三条口径（写死，界面与接口都照这个）：
1. **默认只出「现行有效」**——本库有 686 条非现行有效，默认混入会让用户引用废止条文；
2. **法规名分级匹配**：全名 > 去「中华人民共和国」前缀 > 包含（实测「民法典」会命中 10 个法规，
   其中 9 个是"关于适用《民法典》的司法解释"，不做分级就会把司法解释排到民法典前面）；
3. **条号归一化**：`577` / `577条` / `第五百七十七条` 都能对上；保留 `第X条之一`（修正案新增条文，
   否则与真正的第X条撞名）。
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "statutes.sqlite3"

CN_DIGITS = "零一二三四五六七八九"
ARTICLE_HINT = re.compile(r"^\s*(?:第)?\s*([0-9一二三四五六七八九十百千零〇两]+)\s*条?\s*(之[一二三四五六七八九十]+)?\s*$")
STATUTE_COLS = "s.bbbs, s.title, s.category, s.organ, s.status_code, s.status_text, s.publish_date, s.effective_date"

CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def int_to_cn(number: int) -> str:
    """1234 → 一千二百三十四（用于把阿拉伯数字条号转成库里的中文条号）。"""
    if number <= 0 or number > 9999:
        return ""
    units = ["", "十", "百", "千"]
    digits = [int(ch) for ch in str(number)]
    out: list[str] = []
    size = len(digits)
    for index, digit in enumerate(digits):
        unit = units[size - index - 1]
        if digit == 0:
            if out and out[-1] != "零" and index != size - 1:
                out.append("零")
            continue
        if not (digit == 1 and unit == "十" and index == 0):
            out.append(CN_DIGITS[digit])
        out.append(unit)
    return "".join(out).rstrip("零")


def normalize_article_no(raw: str) -> str:
    """把用户写的条号归一成库里的形态：'577'→'第五百七十七条'；'第十七条之一'原样保留。"""
    text = (raw or "").strip().replace(" ", "")
    match = re.match(r"^(?:第)?([0-9一二三四五六七八九十百千零〇两]+)条?(之[一二三四五六七八九十]+)?$", text)
    if not match:
        return ""
    number, suffix = match.group(1), match.group(2) or ""
    cn = int_to_cn(int(number)) if number.isdigit() else number
    if not cn:
        return ""
    return f"第{cn}条{suffix}"


def parse_query(user_input: str) -> tuple[str, str]:
    """把「民法典 第五百七十七条」拆成 (法规名, 条号)。只给关键词时条号为空。"""
    text = unicodedata.normalize("NFKC", user_input or "").strip()
    text = text.replace("《", "").replace("》", "")
    # 法规名紧接阿拉伯数字也视为条号，不要求用户手动加空格。
    compact = re.fullmatch(r"(.+?)[\s，,、]*(第?\s*[0-9]+\s*条?(?:\s*之[一二三四五六七八九十]+)?)", text)
    if compact:
        article = normalize_article_no(compact.group(2))
        if article:
            return compact.group(1).strip(), article
    if not text:
        return "", ""
    tokens = [token for token in re.split(r"[\s，,、]+", text) if token]
    name_parts: list[str] = []
    article = ""
    for token in tokens:
        normalized = normalize_article_no(token)
        if normalized and not article:
            article = normalized
        else:
            name_parts.append(token)
    # 「民法典第五百七十七条」这种没空格的写法
    if not article:
        match = re.search(r"(第[0-9一二三四五六七八九十百千零〇两]+条(?:之[一二三四五六七八九十]+)?)", text)
        if match:
            article = normalize_article_no(match.group(1))
            name_parts = [text[: match.start()].strip()] if text[: match.start()].strip() else name_parts
    return "".join(name_parts).strip(), article


def cjk_space(text: str) -> str:
    return CJK_RE.sub(lambda m: f" {m.group(0)} ", text)


def fts_query(keyword: str) -> str:
    parts = [part for part in re.split(r"[\s，,、]+", keyword) if part]
    return " AND ".join(f'"{cjk_space(part)}"' for part in parts)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def name_rank(title: str, name: str) -> int:
    """法规名匹配分级：0 全名 → 1 去前缀全名 → 2 前缀一致 → 3 包含 → 9 不匹配。"""
    if not name:
        return 9
    if title == name:
        return 0
    if title == f"中华人民共和国{name}":
        return 1
    if title.startswith(name):
        return 2
    if name in title:
        return 3
    return 9


def data_as_of(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT value FROM meta WHERE key='imported_at'").fetchone()
    return row["value"] if row else "未知"


#: 法规层级排序权重（越小越优先）：让"法律"优先于"通知/批复"这类短文件
TIER = {"法律": 0, "行政法规": 1, "宪法": 0, "监察法规": 1, "司法解释": 2}


def keyword_search(conn: sqlite3.Connection, keyword: str, *, include_invalid: bool, limit: int, name: str = "") -> list[dict]:
    """关键词检索：FTS5（bm25）定位 → 回表取原始正文 → **再排序**。

    排序规则（2026-09-21 按实测修正）：
    1. **同一部法规只出一条**——否则「押金」会把《就业促进法》占满整屏；
    2. 真条文优先于「（题注）/（全文）」这类整篇兜底行；
    3. 按法规层级：法律 > 行政法规 > 司法解释 > 通知/批复；
    4. 同层级内用 FTS5 的 bm25 相关性。
    """
    status_filter = "" if include_invalid else " AND s.status_code = 'valid'"
    hits = conn.execute(
        f"""SELECT f.bbbs, f.no_raw, rank AS score FROM articles_fts f JOIN statutes s ON s.bbbs = f.bbbs
            WHERE articles_fts MATCH ?{status_filter} ORDER BY rank LIMIT ?""",
        (fts_query(keyword), 400),
    ).fetchall()
    items: list[dict] = []
    seen_key: set[tuple[str, str]] = set()
    per_statute: dict[str, int] = {}
    for hit in hits:
        key = (hit["bbbs"], hit["no_raw"])
        if key in seen_key:
            continue
        seen_key.add(key)
        # 同一部法规最多出 1 条（后面若不足 limit 再回补第二条）
        if per_statute.get(hit["bbbs"], 0) >= 1:
            continue
        per_statute[hit["bbbs"]] = per_statute.get(hit["bbbs"], 0) + 1
        row = conn.execute(
            """SELECT s.bbbs, s.title, s.category, s.organ, s.status_code, s.status_text, s.publish_date, s.effective_date,
                      a.no, a.text
               FROM articles a JOIN statutes s ON s.bbbs = a.bbbs
               WHERE a.bbbs = ? AND a.no = ? LIMIT 1""",
            key,
        ).fetchone()
        if row is not None:
            body = row["no"] not in ("（题注）", "（全文）")
            items.append(
                dict(row)
                | {
                    "rank": name_rank(row["title"], name),
                    "tier": TIER.get(row["category"] or "", 3),
                    "is_article": body,
                    "score": float(hit["score"] or 0),
                }
            )
    items.sort(key=lambda item: (item["tier"], 0 if item["is_article"] else 1, item["score"]))
    return items[:limit]


def search(user_input: str, *, include_invalid: bool = False, limit: int = 10) -> dict:
    """总入口。

    规则（2026-09-21 按产品经理反馈修正）：
    - **带条号** → 精准查（"民法典 577"）；
    - **不带条号** → 一律先做**全文关键词检索**，只有"该输入恰好就是某部法规的名字"时才把这部法放在最前面。
      旧逻辑把"合同"这种普通词当成法规名，结果只列标题含"合同"的那部法（劳动合同法）的前几条——
      这是错的：用户搜"合同"要的是**所有提到合同的条文**。
    """
    name, article = parse_query(user_input)
    conn = _connect()
    try:
        items: list[dict] = []
        if article and name:
            status_filter = "" if include_invalid else " AND s.status_code = 'valid'"
            rows = conn.execute(
                f"""SELECT s.bbbs, s.title, s.category, s.organ, s.status_code, s.status_text, s.publish_date, s.effective_date,
                           a.no, a.text
                    FROM articles a JOIN statutes s ON s.bbbs = a.bbbs
                    WHERE s.title LIKE ?{status_filter}
                    ORDER BY s.title""",
                (f"%{name}%",),
            ).fetchall()
            best_rank = min((name_rank(row["title"], name) for row in rows), default=9)
            scored = []
            for row in rows:
                if row["no"] != article:
                    continue
                rank = name_rank(row["title"], name)
                if rank == 9 or (best_rank <= 1 and rank != best_rank):
                    continue
                scored.append((rank, row))
            scored.sort(key=lambda item: (item[0], 0 if item[1]["status_code"] == "valid" else 1, len(item[1]["title"])))
            items = [dict(row) | {"rank": rank} for rank, row in scored[:limit]]

        if not items and not article:
            keyword = user_input.strip()
            items = keyword_search(conn, keyword, include_invalid=include_invalid, limit=limit, name=name)
            # 输入恰好就是某部法规的名字（如"劳动合同法"）→ 把这部法排在最前面，便于直接看全文
            strong = conn.execute(
                """SELECT bbbs, title FROM statutes
                   WHERE title = ? OR title = ? LIMIT 1""",
                (name, f"中华人民共和国{name}"),
            ).fetchone()
            if strong is not None:
                head = conn.execute(
                    """SELECT s.bbbs, s.title, s.category, s.organ, s.status_code, s.status_text, s.publish_date, s.effective_date,
                              a.no, a.text
                       FROM articles a JOIN statutes s ON s.bbbs = a.bbbs
                       WHERE s.bbbs = ? AND a.no NOT LIKE '%题注%' ORDER BY a.seq LIMIT 1""",
                    (strong["bbbs"],),
                ).fetchone()
                if head is not None:
                    entry = dict(head) | {"rank": 0}
                    items = [entry] + [item for item in items if item["bbbs"] != strong["bbbs"]][: max(0, limit - 1)]

        return {
            "input": user_input,
            "parsed": {"name": name, "article": article, "keyword": user_input.strip()},
            "data_as_of": data_as_of(conn),
            "total": len(items),
            "items": items,
        }
    finally:
        conn.close()


def open_statute(bbbs: str) -> dict | None:
    """取一份法规的元数据 + 全部条文（按 seq 顺序）。找不到返回 None。"""
    conn = _connect()
    try:
        head = conn.execute(
            """SELECT bbbs, title, category, organ, status_code, status_text, publish_date, effective_date,
                      article_count, char_len, source_file
               FROM statutes WHERE bbbs = ?""",
            (bbbs,),
        ).fetchone()
        if head is None:
            return None
        articles = [
            {"no": row["no"], "text": row["text"]}
            for row in conn.execute("SELECT no, text FROM articles WHERE bbbs = ? ORDER BY seq", (bbbs,))
        ]
        return dict(head) | {"articles": articles, "data_as_of": data_as_of(conn)}
    finally:
        conn.close()


def status_summary() -> dict:
    """库状态：规模与状态分布（界面用来标注"默认只出有效条文"的依据）。"""
    conn = _connect()
    try:
        by_status = {
            row["status_text"]: row["n"]
            for row in conn.execute(
                """SELECT status_text, COUNT(*) AS n FROM statutes
                   GROUP BY status_text
                   ORDER BY n DESC"""
            )
        }
        return {
            "statutes": conn.execute("SELECT COUNT(*) FROM statutes").fetchone()[0],
            "articles": conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0],
            "by_status": by_status,
            "data_as_of": data_as_of(conn),
        }
    finally:
        conn.close()
