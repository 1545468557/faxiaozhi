"""会话材料登记表（阶段 2-4）。

职责：给「用户自带材料」这条主路径提供确定性的登记与判定——
**角色（案例 / 法条）、标识（案号 / 法规名+条号）、核验状态、版本（superseded）与定位索引**。

红线（C-12 / D7，本文件不得违反）：
- 本模块**不持有原文**。原文只在 `session.materials`（会话内存）与依据池的 `Source.quote` 里；
  登记表只记计数、标识与定位信息（行号 / 偏移 / 字数），因此可以安全地进快照与 journal。
- 用户材料**永不落盘、永不入本地依据库**（`Library.upsert` 会拒绝 `origin=user`）。

设计口径：所有判定都由**确定性代码**完成（正则 + 计数），不交给模型——
「材料角色/标识/版本」若由模型决定，一旦判错就会污染矩阵与引用。
"""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any

ROLE_CASE = "case"
ROLE_STATUTE = "statute"
#: 2-6：合同材料（合同审查分支的输入）
ROLE_CONTRACT = "contract"
ROLES = (ROLE_CASE, ROLE_STATUTE, ROLE_CONTRACT)

IDENT_FROM_TEXT = "text"
IDENT_FROM_FILENAME = "filename"

#: 案号：（2023）苏01民终1234号 / (2023)京0105民初123号 / 2023）苏01民终1234号
CASE_NO = re.compile(r"[（(]\s*\d{4}\s*[）)]\s*[^\s，。；、：:）)]{0,25}?第?\s*\d+\s*号")
#: 法规名 + 条号：《中华人民共和国民法典》第五百七十七条
LAW_ARTICLE = re.compile(
    r"《[^》\n]{2,40}》\s*第[一二三四五六七八九十百零两\d]+条(?:之[一二三四五六七八九十])?"
)
#: 只有条号：第一百零七条
ARTICLE_ONLY = re.compile(r"第[一二三四五六七八九十百零两\d]+条(?:之[一二三四五六七八九十])?")
#: 明显是裁判文书 / 合同的标题特征
JUDGMENT_HINT = re.compile(r"(判决书|裁定书|调解书|民事判决|刑事判决|裁判文书)")
CONTRACT_HINT = re.compile(r"(合同|协议|条款)")
#: 2-6：合同当事人称谓（用于把「标题含合同/协议」的文本识别成合同材料）
CONTRACT_PARTY = re.compile(
    r"(甲方|乙方|出租方|承租方|出租人|承租人|买方|卖方|供方|需方|发包人|承包人|委托人|受托人)"
)


@dataclass
class MaterialRecord:
    """一条已登记的用户材料（**不含原文**）。"""

    material_id: str
    filename: str
    role: str = ROLE_CASE
    #: default（按配置默认值）/ detected（正文自动识别）/ manual（上传时指定）
    role_source: str = "default"
    identifier: str = ""
    identifier_source: str = IDENT_FROM_FILENAME
    #: 正文里没识别到案号 / 法规名+条号（界面与报告必须如实标注，不许假装规范）
    identifier_missing: bool = False
    chars: int = 0
    verified: bool = False
    version: int = 1
    superseded: bool = False
    superseded_by: str | None = None
    status: str = "pending_review"          # pending_review | verified | superseded
    fail_reason: str = ""
    source_id: str = ""
    locator_index: dict[str, Any] = field(default_factory=dict)
    uploaded_at: str = ""

    # ------------------------------------------------------------ 派生
    @property
    def role_label(self) -> str:
        if self.role == ROLE_CASE:
            return "案例"
        if self.role == ROLE_CONTRACT:
            return "合同"
        return "法条"

    @property
    def version_label(self) -> str:
        if self.superseded:
            return f"第 {self.version} 版（已被新版取代，本次不采用）"
        return f"第 {self.version} 版" + ("（本次采用）" if self.version > 1 else "")

    def brief(self) -> dict[str, Any]:
        data = asdict(self)
        data["role_label"] = self.role_label
        data["version_label"] = self.version_label
        data["needs_review"] = not self.verified and not self.superseded
        if self.identifier_missing:
            data["identifier_note"] = "未识别到案号，已改用文件名作为引用标识"
        return data


@dataclass
class SupersedeOutcome:
    """一次登记的结果：本条 + 被本条取代的旧版本。"""

    record: MaterialRecord
    superseded: list[MaterialRecord] = field(default_factory=list)

    @property
    def versions(self) -> int:
        return 1 + len(self.superseded)


class MaterialRegistry:
    """会话级材料登记表。**只在内存**，会话结束即释放。"""

    def __init__(self) -> None:
        self.records: dict[str, MaterialRecord] = {}

    # ------------------------------------------------------------ 登记
    def register(self, record: MaterialRecord) -> SupersedeOutcome:
        """登记一条材料；同标识的新版本会**取代**旧版本（旧版标 superseded）。

        同标识 = 角色相同 + 标识规范化后相同（大小写/空白不敏感）。
        """
        superseded: list[MaterialRecord] = []
        key = _ident_key(record.role, record.identifier)
        if key:
            for old in list(self.records.values()):
                if old.superseded:
                    continue
                if _ident_key(old.role, old.identifier) == key:
                    old.superseded = True
                    old.superseded_by = record.material_id
                    old.status = "superseded"
                    old.verified = False
                    superseded.append(old)
        version = 1 + max(
            (old.version for old in self.records.values() if _ident_key(old.role, old.identifier) == key),
            default=0,
        )
        record.version = version
        record.status = "pending_review"
        self.records[record.material_id] = record
        return SupersedeOutcome(record=record, superseded=superseded)

    # ------------------------------------------------------------ 查询
    def get(self, material_id: str) -> MaterialRecord | None:
        return self.records.get(material_id)

    def by_source(self, source_id: str) -> MaterialRecord | None:
        for record in self.records.values():
            if record.source_id == source_id:
                return record
        return None

    def active(self) -> list[MaterialRecord]:
        return [r for r in self.records.values() if not r.superseded]

    def active_case_materials(self) -> list[MaterialRecord]:
        return [r for r in self.active() if r.role == ROLE_CASE]

    def superseded(self) -> list[MaterialRecord]:
        return [r for r in self.records.values() if r.superseded]

    def versions_of(self, identifier: str, role: str | None = None) -> list[MaterialRecord]:
        key = _ident_key(role or ROLE_CASE, identifier)
        return sorted(
            (r for r in self.records.values() if _ident_key(r.role, r.identifier) == key),
            key=lambda r: r.version,
        )

    # ------------------------------------------------------------ 状态
    def mark_verified(self, source_id: str) -> MaterialRecord | None:
        record = self.by_source(source_id)
        if record is None or record.superseded:
            return None
        record.verified = True
        record.status = "verified"
        return record

    def mark_rejected(self, record: MaterialRecord, reason: str) -> None:
        record.status = "rejected"
        record.fail_reason = reason
        self.records[record.material_id] = record

    # ------------------------------------------------------------ 释放
    def release(self) -> None:
        """会话结束：清空登记表（与材料原文一起释放）。"""
        self.records.clear()

    # ------------------------------------------------------------ 快照
    def snapshot(self) -> list[dict[str, Any]]:
        ordered = sorted(self.records.values(), key=lambda r: (r.filename, r.version))
        return [r.brief() for r in ordered]


def _ident_key(role: str, identifier: str) -> str:
    from .models import normalize_text

    text = normalize_text(identifier or "")
    return f"{role}:{text}" if text else ""


# ---------------------------------------------------------------- 判定（纯函数）


def detect_role(
    text: str,
    filename: str = "",
    requested: str = "",
    default: str = ROLE_CASE,
) -> tuple[str, str]:
    """判定材料角色。返回 (role, role_source)。

    优先级：上传时指定（manual）> 正文特征（detected）> 配置默认值（default）。
    正文特征：有案号或裁判文书标题 → 案例；无案号但有「第…条」/《…》→ 法条。
    """
    if requested in ROLES:
        return requested, "manual"
    body = text or ""
    if CASE_NO.search(body) or JUDGMENT_HINT.search(body):
        return ROLE_CASE, "detected"
    # 2-6：合同优先于法条判定——合同里也写出「第一条/第二条」，若先走法条分支会把合同误判成法条材料。
    # 判据：标题带「合同/协议」且正文出现当事人称谓（甲方/乙方/出租方…）。
    if CONTRACT_HINT.search(body[:200]) and CONTRACT_PARTY.search(body):
        return ROLE_CONTRACT, "detected"
    if LAW_ARTICLE.search(body) or ARTICLE_ONLY.search(body):
        return ROLE_STATUTE, "detected"
    if CONTRACT_HINT.search(body) and not CASE_NO.search(body):
        # 合同/协议但没有当事人称谓：当普通证据材料处理（按案例材料）
        return ROLE_CASE, "detected"
    return (default if default in ROLES else ROLE_CASE), "default"


def extract_identifier(text: str, filename: str, role: str) -> tuple[str, str, bool]:
    """抽取引用标识。返回 (identifier, identifier_source, missing)。

    - 案例：优先案号；抽不到用文件名，并标 missing=True（界面/报告必须如实标注）。
    - 法条：优先「《法规名》第 X 条」；只有条号时用条号；再不行用文件名。
    """
    body = text or ""
    if role == ROLE_CONTRACT:
        # 合同没有法定「案号」：文件名就是它的自然标识（不算缺失，不篡改）
        return filename, IDENT_FROM_FILENAME, False
    if role == ROLE_STATUTE:
        match = LAW_ARTICLE.search(body)
        if match:
            return _squash(match.group(0)), IDENT_FROM_TEXT, False
        match = ARTICLE_ONLY.search(body)
        if match:
            return _squash(match.group(0)), IDENT_FROM_TEXT, False
        return filename, IDENT_FROM_FILENAME, True

    match = CASE_NO.search(body)
    if match:
        return _squash(match.group(0)), IDENT_FROM_TEXT, False
    return filename, IDENT_FROM_FILENAME, True


def locate(text: str, identifier: str) -> dict[str, Any]:
    """定位索引：**只用位置与结构信息，不含原文**（可安全进 messages / 快照）。

    返回行号、字符偏移、全文行数与字数——人工复核时能按行找到材料内容。
    """
    body = text or ""
    lines = body.splitlines() or [""]
    offset = body.find(identifier) if identifier else -1
    if offset < 0:
        offset = 0
    line = body.count("\n", 0, max(offset, 0)) + 1
    return {
        "chars": len(body),
        "lines": len(lines),
        "identifier_offset": offset,
        "identifier_line": line,
        "anchor": f"第 {line} 行 / 共 {len(lines)} 行",
    }


def locator_summary(record: MaterialRecord) -> str:
    """给 messages 用的摘要：**只有结构信息，没有原文**。"""
    loc = record.locator_index or {}
    return (
        f"{record.filename}（{record.role_label}，{record.chars} 字，"
        f"{loc.get('lines', '?')} 行，标识位于{loc.get('anchor', '未知')}）"
    )


#: 双源比对的最小正文长度（规范化后）。短于此不做判定：既不冒充“一致”，也不贸然判“冲突”
MIN_CORROBORATION_CHARS = 40


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def mark_material_corroboration(source_pool: dict[str, Any]) -> int:
    """用户案例材料 × 法宝（或本地库）同案号逐字比对，写入印证状态。返回冲突数。

    判定口径（**实测修正**，见《阶段闸门是否表》）：

    - 规范化后**一方包含另一方** → `dual`：同一份原文的不同截取。
      用户材料常常是“案号 + 法院 + 日期 + 裁判理由 + 说明行”（从法宝导出时加的行），
      而法宝本次只返回裁判理由段。若用“全文相等”判定，会把**同一份文书**误判为冲突，
      既给出事实错误的提示（“原文不一致”），又会白白挡住导出。
      包含关系只说明“多/少了外围行”，不构成矛盾。
    - 互不包含（含正文中间改一个字） → `conflict`（门禁 R7 拦住，需人工裁决）。
    - 任一侧规范化后不足 `MIN_CORROBORATION_CHARS` 字 → **不做判定**（保持 not_applicable）。
    - 没找到同案号 → 不动（保持 not_applicable，不作任何暗示）。
    """
    from .models import normalize_text

    peers: dict[str, list[Any]] = {}
    for source in source_pool.values():
        if source.is_user_material or source.kind != "case":
            continue
        key = normalize_text(source.identifier or "")
        if key:
            peers.setdefault(key, []).append(source)

    conflicts = 0
    for source in source_pool.values():
        if not source.is_user_material or source.kind != "case":
            continue
        key = normalize_text(source.identifier or "")
        matches = [p for p in peers.get(key, []) if (p.quote or "").strip()]
        if not matches or not (source.quote or "").strip():
            continue
        own = normalize_text(source.quote)
        if len(own) < MIN_CORROBORATION_CHARS:
            continue
        verdict: str | None = None
        for peer in matches:
            other = normalize_text(peer.quote)
            if len(other) < MIN_CORROBORATION_CHARS:
                continue
            if own in other or other in own:
                verdict = "dual"                     # 同一份原文（相差的只是外围行/截断）
                break
            verdict = "conflict"
        if verdict is None:
            continue
        source.corroboration = verdict
        if verdict == "conflict":
            conflicts += 1
    return conflicts


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


__all__ = [
    "IDENT_FROM_FILENAME",
    "IDENT_FROM_TEXT",
    "MIN_CORROBORATION_CHARS",
    "ROLE_CASE",
    "ROLE_CONTRACT",
    "ROLE_STATUTE",
    "ROLES",
    "MaterialRecord",
    "MaterialRegistry",
    "SupersedeOutcome",
    "detect_role",
    "extract_identifier",
    "locate",
    "locator_summary",
    "mark_material_corroboration",
    "now",
]