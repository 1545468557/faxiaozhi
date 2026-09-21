"""文件安全（2-2 F 组）：假文件、超大文件、路径穿越、zip 炸弹。

对应 2-1 遗留未验项 D2 / D3（当时靠人工上传验证，未执行）。
本组全部离线可跑，不需要网络与密钥。
"""

from __future__ import annotations

import io
import zipfile

from app.config import get_config
from app.models import Status
from app.tools.document import detect_kind, parse_bytes
from app.tools.legal import safe_filename

MAGIC = b"PK\x03\x04"


def _make_docx(text: str = "设备质量异议与检验期间的法律适用研究。" * 8) -> bytes:
    """造一个最小但真实的 docx（zip 容器）。"""
    from docx import Document

    doc = Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_f29_exe_renamed_to_docx_is_rejected():
    """D2：exe 改名成 .docx → 必须被拒（不只看扩展名）。"""
    fake = b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 200      # Windows PE 头
    result = parse_bytes(fake, "伪装材料.docx")
    assert result.status == Status.PARSE_ERROR
    assert result.error_kind == "type_mismatch"
    assert result.sources == []


def test_f30_real_docx_passes():
    result = parse_bytes(_make_docx(), "合同.txt".replace(".txt", ".docx"))
    assert result.status == Status.OK
    assert result.sources and result.sources[0].is_user_material
    assert result.sources[0].user_verified is False          # 必须由用户点「本人已核验」


def test_f31_oversize_file_rejected_without_reading_body():
    """D3：超过上限必须被拒，且文案明确指向大小。"""
    cfg = get_config()
    limit_mb = int(cfg.get("limits.max_upload_mb", 20))
    oversize = MAGIC + b"\x00" * ((limit_mb + 1) * 1024 * 1024)
    result = parse_bytes(oversize, "超大材料.docx")
    assert result.status == Status.PARSE_ERROR
    assert result.error_kind == "file_too_large"
    assert str(limit_mb) in result.detail


def test_f32_path_traversal_filename_is_sanitized():
    """文件名含路径穿越 → 安全化，绝不带目录分隔符与小圆点。"""
    for evil in ("../../etc/passwd", "..\\..\\windows\\system32\\cmd", "/etc/hosts", "a/../../b"):
        safe = safe_filename(evil, "material")
        assert "/" not in safe
        assert "\\" not in safe
        assert ".." not in safe


def test_f33_zip_bomb_is_rejected_before_extraction():
    """高压缩比 zip（伪 docx）→ 在解压前就被拒。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", b"\x00" * (8 * 1024 * 1024))   # 8MB 全零 → 极高压缩比
    bomb = buf.getvalue()
    assert detect_kind(bomb, "bomb.docx")[0] == "docx"                  # 类型本身能识别
    result = parse_bytes(bomb, "bomb.docx")
    assert result.status == Status.PARSE_ERROR
    assert result.error_kind == "zip_bomb_suspected"


def test_f34_normal_docx_is_not_blocked_by_zip_check():
    """反向：正常 docx 不能被误伤（压缩比防护不能误拒真文件）。"""
    result = parse_bytes(_make_docx(), "正常材料.docx")
    assert result.status == Status.OK
