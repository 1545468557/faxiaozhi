"""核心数据模型：状态是一等公民（PRD 附录 A 第 1 条）。"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

# ---------------------------------------------------------------- 状态枚举


class Status(StrEnum):
    OK = "ok"
    NO_MATCH = "no_match"
    INSUFFICIENT = "insufficient"
    ABSTRACT_ONLY = "abstract_only"
    PARSE_ERROR = "parse_error"
    INTERFACE_ERROR = "interface_error"


#: 用户可见文案（六值必须互不相同，C-04 / C-05）
STATUS_TEXT: dict[Status, str] = {
    Status.OK: "已取得完整原文",
    Status.NO_MATCH: "本次检索条件与数据源范围内未找到匹配。这不代表相关案例不存在。",
    Status.INSUFFICIENT: "结果不足以支撑综合结论，仅提供单案要素。",
    Status.ABSTRACT_ONLY: "本次仅取得摘要，无裁判原文。摘要可作线索，不能作为引用依据。",
    Status.PARSE_ERROR: "返回内容无法识别，未能完成本次处理。请替换格式，或稍后重试；若反复出现请联系维护者校准。",
    Status.INTERFACE_ERROR: (
        "检索接口调用失败，本次未获得可核验依据。这不等于「无相关案例」。可点击重试。"
    ),
}


#: 来源与印证文案（2-3）：由代码标注，模型无权写
CORROBORATION_TEXT: dict[str, str] = {
    "dual": "双源一致（法宝 + 本地依据库）",
    "single_mcp": "单源（法宝）",
    "single_local": "单源（本地依据库，法宝未参与）",
    "conflict": "来源冲突（法宝与本地依据库原文不一致）",
    "not_applicable": "不适用",
}

#: 2-4：用户材料与法宝同案号比对后的文案（值仍复用 dual / conflict，文案按来源区分）
CORROBORATION_TEXT_USER: dict[str, str] = {
    "dual": "双源一致（用户材料 + 法宝）",
    "conflict": "来源冲突（用户材料与法宝对同一案号的原文不一致）",
    "single_mcp": "单源（法宝）",
    "not_applicable": "未做双源比对（本次无同案号法宝来源）",
}

#: 来源标签（矩阵「来源」列、界面、导出报告共用，由代码决定）
ORIGIN_TEXT: dict[str, str] = {
    "user": "用户材料",
    "mcp": "法宝",
    "local": "本地依据库",
    "fixture": "演示夹具",
}


class RunStatus(StrEnum):
    RUNNING = "running"
    SUSPENDED = "suspended"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


# ---------------------------------------------------------------- 文本规范化


_PUNCT = re.compile(r"[\s，。、；：！？（）〔〕《》“”‘’\"'.,;:!?()\[\]<>·—\-_/\\|…⋯]+")


def normalize_text(text: str) -> str:
    """NFKC 全角半角归一 + 去标点空白 + casefold（PRD 4.1.2 R5 的实现基础）。"""
    out = unicodedata.normalize("NFKC", text or "")
    out = _PUNCT.sub("", out)
    return out.casefold()


def fingerprint(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def content_key(*parts: Any) -> str:
    """内容哈希调用键（不能用计数器：pipeline 完成顺序不确定）。"""
    basis = "|".join(_stable(p) for p in parts)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _stable(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


# ---------------------------------------------------------------- 来源与工具结果


@dataclass
class Source:
    source_id: str
    kind: Literal["statute", "case", "user_material"] = "case"
    title: str = ""
    identifier: str = ""            # 法规名+条款号，或案号
    quote: str = ""                 # 权威原文
    effective_status: str = "unknown"   # 现行有效 / 已修订 / 已废止 / unknown
    applicable_from: str | None = None
    applicable_to: str | None = None
    court: str | None = None
    level: str | None = None
    region: str | None = None
    decided_on: str | None = None
    uri: str | None = None          # 法宝超链
    origin: str = "fixture"         # mcp | fixture | user
    synthetic: bool = False         # 夹具数据（C-13）
    user_verified: bool = False     # 用户材料的人工核验标记（门禁 R3）
    status: Status = Status.OK
    # ---- 2-3 新增：来源与印证（全部由代码判定）----
    corroboration: str = "not_applicable"   # dual / single_mcp / single_local / conflict / not_applicable
    local_hit: bool = False                 # 本次是否由本地依据库命中复用
    # ---- 2-4 新增：材料主路径 ----
    supplement: bool = False                # 本条来自「补充检索」（不改变用户材料的主体地位）
    superseded: bool = False                # 已被同标识的新版本取代（不参与候选与引用）
    manual_override: bool = False           # 冲突经人工裁决「以我上传的材料为准」后放行
    identifier_missing: bool = False        # 正文未识别到案号/法规名+条号，用了文件名
    locator: dict[str, Any] = field(default_factory=dict)   # 定位索引（不含原文）

    @property
    def corroboration_text(self) -> str:
        if self.is_user_material:
            return CORROBORATION_TEXT_USER.get(self.corroboration, self.corroboration)
        return CORROBORATION_TEXT.get(self.corroboration, self.corroboration)

    @property
    def origin_text(self) -> str:
        """来源标签：补充检索单独标注，用户材料优先显示"""
        if self.is_user_material:
            return ORIGIN_TEXT["user"]
        base = ORIGIN_TEXT.get(self.origin, self.origin)
        return f"补充来源（{base}）" if self.supplement else base

    @property
    def is_user_material(self) -> bool:
        return self.kind == "user_material" or self.origin == "user"

    @property
    def text_fingerprint(self) -> str:
        return fingerprint(self.quote or "")

    def disk_safe(self) -> dict[str, Any]:
        """落 journal 的安全视图：用户材料不留正文（D7 / C-12）。"""
        data: dict[str, Any] = dict(self.__dict__)
        data["status"] = self.status.value
        if self.is_user_material:
            data["quote"] = ""
            data["quote_len"] = len(self.quote or "")
            data["quote_fp"] = self.text_fingerprint
            data["redacted"] = True
        return data

    def brief(self) -> dict[str, Any]:
        """给界面用的摘要（用户材料不回显原文）。"""
        d: dict[str, Any] = {
            "source_id": self.source_id,
            "kind": self.kind,
            "title": self.title,
            "identifier": self.identifier,
            "status": self.status.value,
            "status_text": STATUS_TEXT[self.status],
            "origin": self.origin,
            "synthetic": self.synthetic,
            "effective_status": self.effective_status,
            "applicable_from": self.applicable_from,
            "applicable_to": self.applicable_to,
            "uri": self.uri,
            "user_verified": self.user_verified,
            "corroboration": self.corroboration,
            "corroboration_text": self.corroboration_text,
            "local_hit": self.local_hit,
            "origin_text": self.origin_text,
            "supplement": self.supplement,
            "superseded": self.superseded,
            "manual_override": self.manual_override,
            "identifier_missing": self.identifier_missing,
            "locator": self.locator,
        }
        if self.is_user_material:
            d["quote"] = ""
            d["quote_len"] = len(self.quote or "")
            d["note"] = "用户材料原文仅在当前会话内临时处理，不落盘、不回显全文"
        else:
            d["quote"] = self.quote
        for k in ("court", "level", "region", "decided_on"):
            if getattr(self, k):
                d[k] = getattr(self, k)
        return d


@dataclass
class ToolResult:
    tool: str
    status: Status
    sources: list[Source] = field(default_factory=list)
    detail: str = ""
    attempts: int = 1
    elapsed_ms: int = 0
    error_kind: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def event(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "status": self.status.value,
            "status_text": STATUS_TEXT[self.status],
            "sources_returned": len(self.sources),
            "attempts": self.attempts,
            "elapsed_ms": self.elapsed_ms,
            "detail": self.detail,
            "error_kind": self.error_kind,
        }

    def to_model_json(self) -> str:
        """给模型看的紧凑结果（含 source_id，便于其引用）。"""
        import json

        payload = {
            "tool": self.tool,
            "status": self.status.value,
            "status_text": STATUS_TEXT[self.status],
            "detail": self.detail,
            "sources": [
                {
                    "source_id": s.source_id,
                    "kind": s.kind,
                    "identifier": s.identifier,
                    "title": s.title,
                    "status": s.status.value,
                    "effective_status": s.effective_status,
                    "applicable_from": s.applicable_from,
                    "applicable_to": s.applicable_to,
                    "court": s.court,
                    "decided_on": s.decided_on,
                    "origin": s.origin,
                    "user_verified": s.user_verified,
                }
                for s in self.sources
            ],
        }
        text = json.dumps(payload, ensure_ascii=False)
        limit = int(self.meta.get("max_chars", 4000))
        if len(text) > limit:
            text = text[:limit] + "…（已截断，完整内容见依据池）"
        return text


# ---------------------------------------------------------------- 引用与门禁


@dataclass
class Citation:
    source_id: str = ""
    identifier: str = ""
    quote: str = ""


@dataclass
class Claim:
    text: str
    citations: list[Citation] = field(default_factory=list)
    is_conclusion: bool = True


@dataclass
class Rejection:
    rule: str
    reason: str
    identifier: str = ""
    quote: str = ""


@dataclass
class GateReport:
    accepted: int = 0
    rejected: list[Rejection] = field(default_factory=list)
    guard_hits: list[str] = field(default_factory=list)
    demo_mode: bool = False
    coverage: float = 1.0
    gaps: list[str] = field(default_factory=list)
    #: 被降级（未通过核验）的结论原文。**界面必须用它过滤结论**，否则会把
    #: 未通过核验的结论当成结论展示（红线 4）。只暴露已经生成的文本，无副作用。
    degraded_texts: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return bool(self.rejected) or bool(self.guard_hits)

    def event(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "rejected": len(self.rejected),
            "guard_hits": len(self.guard_hits),
            "coverage": round(self.coverage, 4),
            "demo_mode": self.demo_mode,
            "details": [r.__dict__ for r in self.rejected],
            "gaps": self.gaps,
            "degraded_texts": self.degraded_texts,
        }


@dataclass
class Gap:
    kind: str            # citation | sample | expression | evidence
    detail: str


@dataclass
class CheckpointState:
    kind: str
    value: dict[str, Any] | None = None
    reached: bool = False


# ---------------------------------------------------------------- 降级记录（2-2 新增）


@dataclass
class DegradationRecord:
    """一次「不顺利」的记录：哪一步、哪个工具、什么原因、能不能重试。

    用途有三：① 界面展示（用户知道发生了什么）；② 《降级矩阵表》的数据来源；
    ③ 决定是否出现「重试」按钮。**只记录脱敏字段**，真实地址与 Token 永不入内。
    """

    step: str = ""
    tool: str = ""
    status: Status = Status.OK
    error_kind: str | None = None
    retryable: bool = False
    attempts: int = 1
    occurred_at: str = ""
    endpoint_alias: str = ""

    def event(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "tool": self.tool,
            "status": self.status.value,
            "status_text": STATUS_TEXT[self.status],
            "error_kind": self.error_kind,
            "retryable": self.retryable,
            "attempts": self.attempts,
            "occurred_at": self.occurred_at,
            "endpoint_alias": self.endpoint_alias,
        }
