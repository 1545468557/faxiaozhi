#!/usr/bin/env python
"""用**真实库数据**生成「法规查找」的静态预览页（便于产品经理先看效果，不动产品）。

输出：~/Desktop/法小智-法规查找-预览.html（自包含，双击即可打开）
用法：cd projects/法小智agent开发文档 && uv run python scripts/preview_statutes.py
"""

from __future__ import annotations

import html
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.statutes import search  # noqa: E402

OUT = pathlib.Path.home() / "Desktop" / "法小智-法规查找-预览.html"

DEMOS = [
    ("民法典 577", "精准查（阿拉伯数字也认）"),
    ("民法典 第五百七十七条", "精准查（中文条号）"),
    ("刑法 第一百二十条之一", "修正案新增条文（第X条之一）"),
    ("消费者权益保护法 第二十六条", "同名撞车时按法规名分级排序"),
    ("押金 退还", "关键词检索（默认只出有效条文）"),
    ("格式条款 无效", "关键词检索"),
]

TEMPLATE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>法小智 · 法规查找（预览）</title>
<style>
 :root {{ --ink:#173c3a; --teal:#136b61; --line:#dce7e2; --surface:#f5f8f6; --muted:#5f7a73; --faint:#93a6a0; }}
 * {{ box-sizing:border-box; }}
 body {{ margin:0; background:var(--surface); color:var(--ink); font:15px/1.7 Inter,"PingFang SC","Microsoft YaHei",sans-serif; }}
 .wrap {{ max-width:1000px; margin:0 auto; padding:28px 20px 60px; }}
 .draft {{ display:flex; gap:8px; padding:10px 14px; margin-bottom:18px; border:1px dashed #c8a155; border-radius:10px; background:#fdf8ec; color:#7a5a12; font-size:12.5px; }}
 h1 {{ font-size:23px; margin:0 0 6px; }}
 .meta {{ color:var(--muted); font-size:13px; margin-bottom:6px; }}
 .notice {{ color:var(--faint); font-size:12.5px; margin-bottom:20px; }}
 .demo {{ margin-bottom:26px; }}
 .query {{ display:flex; align-items:center; gap:10px; padding:11px 14px; border:1px solid var(--line); border-radius:11px 11px 0 0; background:#fff; }}
 .query b {{ font-weight:500; }}
 .query span {{ color:var(--faint); font-size:12px; }}
 .hit {{ padding:13px 15px; border:1px solid var(--line); border-top:0; background:#fff; }}
 .hit:last-child {{ border-radius:0 0 11px 11px; }}
 .row {{ display:flex; flex-wrap:wrap; align-items:center; gap:8px; margin-bottom:5px; }}
 .title {{ font-weight:500; }}
 .badge {{ padding:2px 8px; border-radius:999px; font-size:11px; background:#e9f6f2; color:#17695f; }}
 .badge.warn {{ background:#fdf3e4; color:#8a5a12; }}
 .badge.bad {{ background:#fbeceb; color:#b03a2e; }}
 .where {{ color:var(--muted); font-size:12px; }}
 .body {{ margin:6px 0 8px; font-size:13.5px; line-height:1.95; white-space:pre-wrap; }}
 .copy {{ padding:5px 11px; border:1px solid var(--line); border-radius:8px; background:#fff; color:var(--muted); font-size:12px; cursor:pointer; }}
 .copy:hover {{ border-color:#a9cec4; background:#fbfdfc; }}
 .empty {{ color:var(--warn); }}
 footer {{ margin-top:26px; color:var(--faint); font-size:12px; line-height:1.9; }}
</style></head><body><div class="wrap">
<p class="draft">这是<strong>预览</strong>：数据是真实的，界面样式与正式页面一致；正式页面还没动手做。</p>
<h1>法规查找</h1>
<p class="meta">本地法规库：<b>{statutes}</b> 篇 · <b>{articles}</b> 条条文</p>
<p class="notice">默认只出「现行有效」的条文（库里另有 686 条已修改/已废止/失效，需勾选"包含旧版本"才出现，并会标红）。</p>
{blocks}

</div>
<script>
document.querySelectorAll(".copy").forEach(function (b) {{
  b.addEventListener("click", function () {{
    var card = b.closest(".hit");
    var text = card.getAttribute("data-cite");
    navigator.clipboard.writeText(text).then(function () {{ b.textContent = "已复制"; setTimeout(function () {{ b.textContent = "复制引用"; }}, 1200); }});
  }});
}});
</script></body></html>"""


def badge(item: dict) -> str:
    code = item["status_code"]
    cls = "badge" if code == "valid" else ("badge bad" if code in ("repealed", "invalid") else "badge warn")
    return f'<span class="{cls}">{html.escape(item["status_text"])}</span>'


def render_block(query: str, note: str, result: dict) -> str:
    parts = [f'<div class="demo"><div class="query"><b>{html.escape(query)}</b><span>· {html.escape(note)}｜命中 {result["total"]} 条</span></div>']
    if not result["items"]:
        parts.append('<div class="hit">没有命中。库里没有的条文会如实说明"本库未收录"，不会编。</div></div>')
        return "".join(parts)
    for item in result["items"]:
        cite = f'《{item["title"]}》{item["no"]}'
        where = " · ".join(x for x in [item["organ"], item["publish_date"] and f'公布 {item["publish_date"]}', item["effective_date"] and f'施行 {item["effective_date"]}'] if x)
        parts.append(
            f'<div class="hit" data-cite="{html.escape(cite)}">'
            f'<div class="row"><span class="title">《{html.escape(item["title"])}》{html.escape(item["no"])}</span>{badge(item)}'
            f'<span class="where">{html.escape(where)}</span></div>'
            f'<div class="body">{html.escape(item["text"][:400])}</div>'
            f'<button class="copy">复制引用</button></div>'
        )
    return "".join(parts) + "</div>"


def main() -> None:
    from app.statutes import DB_PATH, _connect, data_as_of

    conn = _connect()
    statutes = conn.execute("SELECT COUNT(*) FROM statutes").fetchone()[0]
    articles = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    as_of = data_as_of(conn)
    conn.close()

    blocks = "".join(render_block(q, note, search(q, limit=3)) for q, note in DEMOS)
    OUT.write_text(
        TEMPLATE.format(statutes=statutes, articles=articles, as_of=as_of[:10], blocks=blocks),
        encoding="utf-8",
    )
    print(f"预览已生成：{OUT}")
    print(f"库大小：{DB_PATH.stat().st_size / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    main()
