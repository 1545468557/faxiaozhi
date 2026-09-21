"""Word 模板 v1（2-2 W 组，5 条）：页脚复核提示与页码、模板版本与来源声明、
被拦截引用的原文绝不进文档、缺口章节保留并加粗、引用块三段式。"""

from __future__ import annotations

from pathlib import Path

from docx import Document

from app.models import Gap, GateReport, Rejection, Source
from app.render.docx import (
    REVIEW_NOTICE,
    SOURCE_DECLARATION,
    document_text,
    render_research_memo,
    template_version,
)
from app.session import Session

#: 被拦截引用的原文（绝不允许出现在交付文档里）
BLOCKED_QUOTE = "本院认为，涉案设备存在根本性质量瑕疵，买受人有权拒绝支付全部价款并主张三倍赔偿。"
PASSED_QUOTE = "本院认为，设备质量存在瑕疵的，买受人可以主张减少价款。"


def _session() -> Session:
    session = Session()
    session.topic = "买受人以设备质量存在瑕疵为由主张减少价款，法院一般如何认定？"
    session.conditions = {"地域": "江苏省"}
    session.sample.set_candidates(["s1", "s2"])
    session.sample.confirm(["s1"], ["s2"])
    session.source_pool["s1"] = Source(
        source_id="s1",
        kind="case",
        identifier="（2023）苏01民终1234号",
        quote=PASSED_QUOTE,
        uri="https://example.com/case/1234",
        court="江苏省南京市中级人民法院",
        origin="mcp",
    )
    session.matrix = [
        {"case_id": "s1", "identifier": "（2023）苏01民终1234号", "holding": "可主张减价"}
    ]
    session.distribution = {"text": "本次确认样本 1 篇中，支持 1 篇。", "denominator": 1}
    session.synthesis = {
        "conclusions": [
            {
                "text": "买受人可以主张减少价款。",
                "citations": [
                    {
                        "source_id": "s1",
                        "identifier": "（2023）苏01民终1234号",
                        "quote": PASSED_QUOTE,
                    }
                ],
            },
            # 未通过核验的结论：不得进入文档
            {"text": f"买受人可以主张三倍赔偿（依据：{BLOCKED_QUOTE}）", "citations": []},
        ]
    }
    session.structured_output = {"_passed_conclusions": ["买受人可以主张减少价款。"]}
    session.gate_report = GateReport(
        accepted=1,
        rejected=[
            Rejection(
                rule="R5",
                reason="引用原文过短（少于 8 字），不予核验",
                identifier="（2023）苏01民终9999号",
                quote=BLOCKED_QUOTE,
            )
        ],
        coverage=1.0,
    )
    session.gaps = [Gap(kind="citation", detail="有结论未提供引用，已降级为「依据缺口」")]
    session.limitations = ["本次仅 1 篇样本，不代表全国裁判总体情况。"]
    return session


def _render(tmp_path: Path) -> tuple[Session, Path, Document]:
    session = _session()
    target = tmp_path / "memo_v1.docx"
    render_research_memo(session, target)
    assert target.exists()
    return session, target, Document(str(target))


# ==================================================================== W34


def test_w34_footer_contains_human_review_notice_and_page_number(tmp_path):
    _session_obj, _path, doc = _render(tmp_path)
    footer = doc.sections[0].footer
    footer_text = "\n".join(p.text for p in footer.paragraphs)
    assert REVIEW_NOTICE in footer_text, "页脚必须含人工复核提示"
    # 页码是 PAGE 域代码，检查页脚 XML 里确实插入了域
    assert "PAGE" in footer._element.xml
    # 正文末尾同样保留一句，防止有人只看正文
    assert REVIEW_NOTICE in document_text(doc)


# ==================================================================== W35


def test_w35_document_declares_template_version_and_source(tmp_path):
    _session_obj, _path, doc = _render(tmp_path)
    text = document_text(doc)
    assert template_version() == "research_memo_v1"
    assert "模板版本：research_memo_v1" in text
    assert "来源声明" in text
    assert SOURCE_DECLARATION in text


# ==================================================================== W36


def test_w36_blocked_quote_never_enters_document(tmp_path):
    _session_obj, _path, doc = _render(tmp_path)
    text = document_text(doc)
    assert BLOCKED_QUOTE not in text, "被拦截引用的原文片段绝不能进入交付文档"
    assert "R5" in text and "不予核验" in text, "拦截原因要保留，原文不能留"
    assert "三倍赔偿" not in text, "未通过核验的结论不得进入文档"


# ==================================================================== W37


def test_w37_gap_section_is_preserved_and_bold(tmp_path):
    _session_obj, _path, doc = _render(tmp_path)
    gap_paragraphs = [p for p in doc.paragraphs if "[citation]" in p.text]
    assert gap_paragraphs, "依据缺口章节必须保留，不能因为不好看就删掉"
    assert any(run.bold for run in gap_paragraphs[0].runs), "缺口条目必须加粗标注"
    rejected_paragraphs = [p for p in doc.paragraphs if p.text.startswith("R5：")]
    assert rejected_paragraphs and any(run.bold for run in rejected_paragraphs[0].runs)


# ==================================================================== W38


def test_w38_citation_block_is_three_part_and_uniform(tmp_path):
    _session_obj, _path, doc = _render(tmp_path)
    citations = [p.text for p in doc.paragraphs if p.text.startswith("引用：")]
    assert len(citations) == 1
    block = citations[0]
    assert "引用：（2023）苏01民终1234号" in block
    assert f"｜原文：「{PASSED_QUOTE}」" in block
    assert "｜链接：https://example.com/case/1234" in block
    assert "　" not in block[:1], "引用块不得以全角空格开头（v0 的旧格式）"
