"""文档解析（PRD 3.2.2 的 parse_document）：DOCX / PDF / TXT，失败即降级。

安全：校验扩展名、**真实类型**（文件头）、大小、安全文件名。
隐私：**不落盘**，原文只进会话内存（C-12）。
"""

from __future__ import annotations

import io
from typing import Any

from ..config import get_config
from ..materials import (
    IDENT_FROM_FILENAME,
    ROLE_CASE,
    ROLE_CONTRACT,
    ROLE_STATUTE,
    ROLES,
    detect_role,
    extract_identifier,
    locate,
)
from ..models import Source, Status, ToolResult
from ..session import Session

PARSE_DOCUMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "filename": {"type": "string"},
        "material_id": {"type": "string", "description": "已上传材料的编号（由服务端分配）"},
        "role": {
            "type": "string",
            "enum": list(ROLES),
            "description": (
                "材料角色：case 案例 / statute 法条 / contract 合同；不填则自动推断（默认案例）"
            ),
        },
    },
    "required": ["filename"],
    "additionalProperties": False,
}

_MAGIC = (
    (b"%PDF-", "pdf"),
    (b"PK\x03\x04", "zip"),
    (b"\xd0\xcf\x11\xe0", "ole"),
)


def detect_kind(data: bytes, filename: str) -> tuple[str | None, str | None]:
    """返回 (真实类型, 错误说明)。"""
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    for magic, kind in _MAGIC:
        if data.startswith(magic):
            if kind == "zip":
                return ("docx", None) if suffix == "docx" else (None, "type_mismatch")
            if kind == "ole":
                return (None, "type_mismatch")
            return ("pdf", None) if suffix == "pdf" else (None, "type_mismatch")
    # 纯文本：可解码且无控制字符
    if suffix in {"txt", "md"}:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            return None, "type_mismatch"
        return ("text", None)
    return None, "type_mismatch"


def parse_bytes(
    data: bytes,
    filename: str,
    *,
    requested_role: str = "",
    material_id: str = "",
) -> ToolResult:
    """解析并判定材料（2-4）。

    角色与标识均由**确定性代码**判定（角色可被上传时的 `requested_role` 覆盖）：
    - 案例材料：`kind=case`、`effective_status=不适用（裁判文书）` → 核验后可引用；
    - 法条材料：`kind=statute`、`effective_status=unknown` → 未经核验只能作线索（R6 拦）。
    """
    cfg = get_config()
    limit = int(cfg.get("limits.max_upload_mb", 20)) * 1024 * 1024
    if len(data) > limit:
        return ToolResult(
            tool="parse_document",
            status=Status.PARSE_ERROR,
            detail="文件超出大小限制（20MB）。",
            error_kind="file_too_large",
        )

    kind, error = detect_kind(data, filename)
    if error or kind is None:
        return ToolResult(
            tool="parse_document",
            status=Status.PARSE_ERROR,
            detail="文件真实类型与扩展名不符，已拒绝。",
            error_kind="type_mismatch",
        )

    # 2-2 新增：轻量 zip 炸弹防护（底线 6）——解压前先看压缩比，不先解压
    if kind == "docx" and bool(cfg.get("files.block_zip_bomb", True)):
        max_ratio = float(cfg.get("files.max_uncompressed_ratio", 200))
        if not _zip_ratio_ok(data, max_ratio):
            return ToolResult(
                tool="parse_document",
                status=Status.PARSE_ERROR,
                detail="文件解压后体积异常（远超合理压缩比），已拒绝处理。",
                error_kind="zip_bomb_suspected",
            )

    try:
        if kind == "docx":
            text = _read_docx(data)
        elif kind == "pdf":
            text = _read_pdf(data)
        else:
            text = data.decode("utf-8", errors="replace")
    except Exception as exc:                      # 解析失败不产出风险清单
        return ToolResult(
            tool="parse_document",
            status=Status.PARSE_ERROR,
            detail="文件解析失败，未能完成处理。请替换格式或补充文本。",
            error_kind=f"parse:{type(exc).__name__}",
        )

    text = (text or "").strip()
    if len(text) < 50:
        return ToolResult(
            tool="parse_document",
            status=Status.PARSE_ERROR,
            detail="文件解析失败或内容过少（可能是扫描件）。请替换格式或补充文本。",
            error_kind="empty_text",
        )

    # 2-4：角色与标识判定（纯代码）；超长按配置截断并如实标记
    max_chars = int(cfg.get("materials.max_material_chars", 200000))
    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars]
    default_role = str(cfg.get("materials.role_default", "case"))
    allow_detect = bool(cfg.get("materials.auto_detect_role", True))
    role, role_source = detect_role(
        text if allow_detect else "",
        filename,
        requested=(requested_role if requested_role in ROLES else ""),
        default=default_role,
    )
    safe = _safe(filename)
    identifier, identifier_source, identifier_missing = extract_identifier(text, safe, role)
    locator = locate(text, identifier)

    source = Source(
        source_id=(f"user_{material_id}" if material_id else f"user_{abs(hash((safe, len(text)))) % 10**8:08d}"),
        kind=role,
        title=f"用户上传材料（{role}）：{safe}",
        identifier=identifier,
        quote=text,
        # 案例（裁判文书）没有「效力状态」概念；法条材料未经核验一律 unknown（R6 拦）
        # 2-6：合同不是「法律依据」，只是审查对象 —— 刻意不在 R6 白名单内，不得被当作法条引用
        effective_status=(
            "不适用（裁判文书）"
            if role == ROLE_CASE
            else ("不适用（合同文本）" if role == ROLE_CONTRACT else "unknown")
        ),
        origin="user",
        synthetic=False,
        user_verified=False,
        identifier_missing=identifier_missing,
        locator=locator,
        status=Status.OK,
    )
    detail = (
        f"已解析 {safe}（{len(text)} 字，{role_label(role)}）。"
        "内容仅在本会话临时处理，不会保存；作为引用依据前需要你点选「本人已核验」。"
    )
    if identifier_missing:
        detail += "正文未识别到案号/法规名+条号，已改用文件名作为引用标识（报告会如实标注）。"
    if truncated:
        detail += f"材料过长，已按 {max_chars} 字上限截断。"
    if role == ROLE_STATUTE:
        detail += "法条材料的效力状态未经核验，本次只能作为线索，不能作为引用依据。"
    return ToolResult(
        tool="parse_document",
        status=Status.OK,
        sources=[source],
        detail=detail,
        meta={
            "needs_review": True,
            "chars": len(text),
            "filename": safe,
            "role": role,
            "role_label": role_label(role),
            "role_source": role_source,
            "identifier": identifier,
            "identifier_source": identifier_source,
            "identifier_missing": identifier_missing,
            "locator_index": locator,
            "truncated": truncated,
        },
    )


def role_label(role: str) -> str:
    if role == ROLE_CASE:
        return "案例材料"
    if role == ROLE_CONTRACT:
        return "合同材料"
    return "法条材料"


def parse_document(session: Session, **args: Any) -> ToolResult:
    filename = str(args.get("filename", ""))
    material_id = str(args.get("material_id", ""))
    requested_role = str(args.get("role") or "")
    payload = session.materials.get(material_id) if hasattr(session, "materials") else None
    if payload is None:
        return ToolResult(
            tool="parse_document",
            status=Status.PARSE_ERROR,
            detail="材料不存在或已释放（内容仅在本会话临时处理）。",
            error_kind="missing_material",
        )
    data, real_name = payload
    result = parse_bytes(
        data,
        real_name or filename,
        requested_role=requested_role,
        material_id=material_id,
    )
    if result.status is Status.OK and result.sources:
        _register_material(session, result, material_id, real_name or filename)
    return result


def _register_material(
    session: Session, result: ToolResult, material_id: str, filename: str
) -> None:
    """把解析结果登记进会话材料表（2-4）：版本、核验状态、定位索引。

    同标识的新版本会取代旧版本（旧版 superseded，退出依据池）。
    """
    from ..materials import MaterialRecord, now

    source = result.sources[0]
    meta = result.meta
    record = MaterialRecord(
        material_id=material_id or source.source_id,
        filename=str(meta.get("filename") or filename),
        role=str(meta.get("role") or ROLE_CASE),
        role_source=str(meta.get("role_source") or "default"),
        identifier=str(meta.get("identifier") or source.identifier),
        identifier_source=str(meta.get("identifier_source") or IDENT_FROM_FILENAME),
        identifier_missing=bool(meta.get("identifier_missing")),
        chars=int(meta.get("chars") or len(source.quote or "")),
        source_id=source.source_id,
        locator_index=dict(meta.get("locator_index") or {}),
        uploaded_at=now(),
    )
    superseded = session.register_material(record, source)
    if superseded:
        result.meta["superseded"] = [r.brief() for r in superseded]
        result.detail += (
            f"检测到 {len(superseded) + 1} 个版本（同一标识），本次采用最新上传；"
            "旧版本已标记为 superseded，不参与对比与引用。"
        )


def _safe(name: str) -> str:
    from .legal import safe_filename

    return safe_filename(name, "material")


def _zip_ratio_ok(data: bytes, max_ratio: float) -> bool:
    """只读 zip 目录，不解压：解压后 / 压缩后 大于阈值即视为可疑。"""
    import zipfile

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = zf.infolist()
            total_uncompressed = sum(int(i.file_size) for i in infos)
            total_compressed = sum(max(int(i.compress_size), 1) for i in infos)
    except Exception:
        return False
    if total_compressed <= 0:
        return False
    return (total_uncompressed / total_compressed) <= max_ratio


def _read_docx(data: bytes) -> str:
    from docx import Document

    document = Document(io.BytesIO(data))
    parts = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.append("\t".join(cell.text for cell in row.cells))
    return "\n".join(parts)


def _read_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)
