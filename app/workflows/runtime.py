"""编排原语 + journal + checkpoint（PRD 3.5，s16 扩展）。

journal 的原文边界（本阶段一处推荐裁决）：
- MCP 权威来源（公开数据）：原文随 journal 落盘，重启后仍可核验引用；
- **用户上传材料：只落指纹与长度，正文一律不落**（C-12 / D7）。
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from ..assemble import assemble_system_prompt
from ..config import Config
from ..errors import ApiError
from ..knowledge import KnowledgeBase
from ..llm import build_provider
from ..loop import agent_loop
from ..models import Claim, Status, ToolResult, content_key

LOG = logging.getLogger("faxiaozhi.runtime")
SCHEMA_VERSION = 1

#: 合同审查立场（2-6）。未确认不得进入下一步（PRD §5.2 / ADR-4）
STANCES: tuple[str, ...] = ("party_a", "party_b", "neutral")
STANCE_LABELS: dict[str, str] = {
    "party_a": "甲方（委托方 / 买方 / 出租方一侧）",
    "party_b": "乙方（受托方 / 卖方 / 承租方一侧）",
    "neutral": "中立第三方（同时看双方风险）",
}


class RunContext:
    def __init__(
        self,
        session: Any,
        cfg: Config,
        provider: Any,
        kb: KnowledgeBase,
        workflow: str = "legal-research",
        args: dict[str, Any] | None = None,
        run_id: str | None = None,
    ) -> None:
        self.session = session
        self.cfg = cfg
        self.provider = provider
        self.kb = kb
        self.workflow = workflow
        self.args = args or {}
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.phase_name = ""
        self.steps = 0
        self.cache: dict[str, Any] = {}
        self.usage = {"in": 0, "out": 0}
        self.started_at = time.time()
        self.first_response_ms: int | None = None

        self.dir: Path = cfg.runs_dir
        self.journal_path = self.dir / f"{self.run_id}.journal.jsonl"
        self.sources_path = self.dir / f"{self.run_id}.sources.jsonl"
        self.state_path = self.dir / f"{self.run_id}.json"
        self.output_path = self.dir / f"{self.run_id}.output.json"
        self.lock_path = self.dir / f"{self.run_id}.lock"
        self.lock_acquired = False
        self.state: dict[str, Any] = {}
        #: 本次运行内「阶段 -> journal key」的登记，用于失败时只作废失败步骤的存档
        self._journal_keys: list[tuple[str, str]] = []
        #: 2-4：本次运行是否存过被脱敏的用户材料文本（重启后材料不可恢复）
        self.material_redacted = False
        self._material_norms: list[str] | None = None

    # ------------------------------------------------------------ 生命周期
    def acquire_lock(self) -> None:
        if self.lock_path.exists():
            age = time.time() - self.lock_path.stat().st_mtime
            if age < 60:
                raise ApiError("run_expired", "该任务正在运行中，请稍候再试。")
            LOG.warning("发现过期的运行锁，已接管：%s", self.run_id)
        self.lock_path.write_text(str(os.getpid()), encoding="utf-8")
        self.lock_acquired = True

    def release_lock(self) -> None:
        if self.lock_acquired and self.lock_path.exists():
            try:
                self.lock_path.unlink()
            except OSError:
                LOG.debug("锁文件删除失败：%s", self.lock_path)

    def load(self) -> None:
        """从 journal 恢复：命中的步骤不重跑。"""
        if self.state_path.exists():
            try:
                self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
                if self.state.get("schema_version") != SCHEMA_VERSION:
                    raise ApiError("run_expired", "存档版本不匹配，请重新发起研究。")
            except json.JSONDecodeError:
                LOG.warning("状态文件损坏，忽略：%s", self.state_path)
                self.state = {}
        if self.journal_path.exists():
            for line in self.journal_path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not row.get("k"):
                    continue
                # 2-4：被脱敏的材料文本不进缓存（不得用“脱敏占位”冒充真实提炼结果）
                if _contains_redaction(row.get("value")):
                    self.material_redacted = True
                    continue
                self.cache[row["k"]] = row.get("value")
        if self.sources_path.exists():
            from ..models import Source

            for line in self.sources_path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                row["status"] = Status(row.get("status", "ok"))
                row.pop("quote_len", None)
                row.pop("quote_fp", None)
                row.pop("redacted", None)
                existing = self.session.source_pool.get(row["source_id"])
                if existing is not None and existing.is_user_material and existing.quote:
                    # 同会话重试时原文仍在内存；磁盘只有脱敏元数据，不得反向覆盖原文。
                    continue
                self.session.source_pool[row["source_id"]] = Source(**row)

    # ------------------------------------------------------------ 事件与状态
    def emit(self, event: str, data: dict[str, Any]) -> None:
        if self.first_response_ms is None:
            self.first_response_ms = int((time.time() - self.started_at) * 1000)
            self.session.first_response_ms = self.first_response_ms
        try:
            self.session.events.put_nowait({"event": event, "data": data})
        except Exception:                                   # 队列异常不影响主流程
            LOG.exception("事件入队失败：%s", event)

    def phase(self, name: str) -> None:
        self.phase_name = name
        self.session.phase = name
        index = _PHASES.index(name) + 1 if name in _PHASES else 0
        self.emit("phase", {"name": name, "index": index, "total": len(_PHASES)})
        self.save_state()

    def log(self, message: str) -> None:
        self.emit("progress", {"phase": self.phase_name, "detail": message})

    def count_step(self) -> None:
        self.steps += 1
        limit = int(self.cfg.get("limits.max_workflow_steps", 40))
        if self.steps > limit:
            raise ApiError("workflow_step_limit")

    def record_usage(self, usage: dict[str, int]) -> None:
        self.usage["in"] += int(usage.get("in", 0))
        self.usage["out"] += int(usage.get("out", 0))

    def on_tool_result(self, name: str, result: ToolResult) -> None:
        if result.status is Status.OK:
            return
        self.session.add_gap("evidence", f"{name}：{result.detail}")
        self.note_degradation(result)

    def note_degradation(self, result: ToolResult, step: str | None = None) -> None:
        """记录一次降级（2-2 新增）：界面据此显示原因，并决定是否出现「重试」。"""
        from ..models import STATUS_TEXT, DegradationRecord

        retryable = result.meta.get("retryable")
        if retryable is None:
            retryable = result.status is Status.INTERFACE_ERROR
        # 脱敏端点别名：优先取单值 endpoint，失败分支给的是 endpoints（已逐个脱敏），取第一个
        alias = result.meta.get("endpoint") or next(iter(result.meta.get("endpoints") or []), "")
        record = DegradationRecord(
            step=step or self.phase_name or "检索",
            tool=result.tool,
            status=result.status,
            error_kind=result.error_kind,
            retryable=bool(retryable),
            attempts=int(result.attempts or 1),
            occurred_at=_now(),
            endpoint_alias=str(alias or ""),
        )
        self.session.note_degradation(record)
        self.emit("degrade", {**record.event(), "status_text": STATUS_TEXT[result.status]})

    def system_prompt(self) -> str:
        return assemble_system_prompt(self.session, self.kb)

    # ------------------------------------------------------------ 原语
    async def agent(
        self,
        *,
        label: str,
        prompt: str,
        schema: dict[str, Any] | None = None,
        tools: list[Any] | None = None,
        temperature: float | None = None,
    ) -> Any:
        key = content_key("agent", label, prompt, schema)
        if key in self.cache:
            self.emit("progress", {"phase": self.phase_name, "detail": f"{label}：命中存档，不重跑"})
            return self.cache[key]
        # 先由 workflow 保存当前步骤与内存上下文，再初始化 SDK。
        # 初始化失败同样进入该步骤的 error / retry 流程；命中存档无需创建客户端。
        if self.provider is None:
            self.provider = build_provider(self.cfg, getattr(self.session, "scenario", "clean"))
        value = await agent_loop(
            self, label=label, prompt=prompt, schema=schema, tools=tools, temperature=temperature
        )
        self.cache[key] = value
        self.append_journal(key, "agent", label, value)
        return value

    async def checkpoint(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        key = content_key("checkpoint", kind)
        if key in self.cache:
            value = dict(self.cache[key] or {})
            self.log(f"确认点「{kind}」命中存档，直接继续")
        else:
            self.emit(
                "checkpoint_reached",
                {"kind": kind, "payload": payload, "resume_token": uuid.uuid4().hex[:8]},
            )
            self.session.status = "awaiting_checkpoint"
            self.session.checkpoint_kind = kind
            self.session.checkpoint_value = None
            self.session.checkpoint_waiter.clear()
            self.save_state()
            await self.session.checkpoint_waiter.wait()
            value = dict(self.session.checkpoint_value or {})
            self.cache[key] = value
            self.append_journal(key, "checkpoint", kind, value)
            self.session.status = "active"
            self.session.checkpoint_kind = None

        # 修复（2-6）：原先这里**无条件**执行样本锁逻辑，导致任何新的人工确认点
        # （如合同立场 stance_confirm）都会顺手把一个空样本锁上，污染 SampleLock
        # 与残留检测。现在按 kind 分派；未知 kind 一律拒绝（宁可报错，不默默跑错分支）。
        self.apply_checkpoint(kind, value)
        self.save_state()
        return value

    def apply_checkpoint(self, kind: str, value: dict[str, Any]) -> None:
        """把一次人工确认的结果写回会话（**按 kind 分派**）。"""
        if kind == "sample_confirm":
            confirmed = [str(x) for x in value.get("confirmed", [])]
            excluded = [str(x) for x in value.get("excluded", [])]
            self.session.sample.confirm(confirmed, excluded)
            self.log(
                f"样本已确认 {len(self.session.sample.confirmed)} 篇"
                f"（排除 {len(self.session.sample.excluded)} 篇）"
            )
            return
        if kind == "stance_confirm":
            stance = str(value.get("stance") or "").strip()
            if stance not in STANCES:
                raise ApiError(
                    "invalid_request", "立场只能是 party_a（甲方）/ party_b（乙方）/ neutral（其他）。"
                )
            self.session.contract["stance"] = stance
            self.session.contract["stanceSource"] = "user"
            self.log(f"审查立场已确认：{STANCE_LABELS[stance]}")
            return
        if kind == "conditions_confirm":
            self.session.conditions_confirmed = True
            self.log("要素与条件已确认")
            return
        raise ApiError("invalid_request", f"未知的确认点：{kind}")

    async def pipeline(
        self,
        items: list[str],
        fn: Callable[[str, int], Awaitable[Any]],
    ) -> list[Any]:
        """逐项串行执行；**单项失败不阻断全局**（记录缺口后继续）。"""
        out: list[Any] = []
        total = len(items)
        for index, item in enumerate(items):
            self.emit("progress", {"phase": self.phase_name, "done": index, "total": total})
            try:
                out.append(await fn(item, index))
            except ApiError as exc:
                self.session.add_gap("extraction", f"{item} 处理失败：{exc.message}")
                self.note_degradation(
                    ToolResult(
                        tool=f"{self.phase_name or 'extract'}:{item}",
                        status=Status.INSUFFICIENT,
                        detail=exc.message,
                        error_kind="step_failed",
                        meta={"retryable": True},
                    )
                )
                self.emit(
                    "tool",
                    {
                        "tool": f"{self.phase_name or 'extract'}:{item}",
                        "status": Status.INSUFFICIENT.value,
                        "detail": exc.message,
                        "sources_returned": 0,
                    },
                )
        self.emit("progress", {"phase": self.phase_name, "done": total, "total": total})
        return out

    # ------------------------------------------------------------ journal
    def material_norms(self) -> list[str]:
        """当前不得落盘的用户提供内容（规范化后）：用户材料正文 + 2-5 咨询的问题/补充原文。"""
        if self._material_norms is None:
            from ..models import normalize_text

            norms: list[str] = [
                normalize_text(s.quote)
                for s in self.session.source_pool.values()
                if s.is_user_material and (s.quote or "").strip()
            ]
            # 2-5：咨询的提问与补充回答同样属「仅当前会话临时处理」（C-12 / D7）
            for item in (getattr(self.session, "consult", {}) or {}).get("facts") or []:
                norm = normalize_text(str(item))
                if norm:
                    norms.append(norm)
            self._material_norms = [n for n in norms if n]
        return self._material_norms

    def redact_material_text(self, value: Any) -> Any:
        """把含用户材料原文的字符串换成“脱敏占位”（2-4 实测发现的缺陷）。

        背景：提炼与引用是拿材料原文算出来的，模型（尤其离线 stub）会把原文片段原样放进
        `facts / holding / basis / citations[].quote`。这些字段如果直接进 journal，
        就等于把用户材料写到了磁盘上（违反 C-12 / D7）。这里在**落盘前**统一拦截。
        """
        norms = self.material_norms()
        if not norms:
            return value
        return _redact(value, norms, self)

    def append_journal(self, key: str, kind: str, label: str, value: Any) -> None:
        value = self.redact_material_text(value)
        row = {"k": key, "kind": kind, "label": label, "value": value, "ts": _now()}
        with self.journal_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        self._journal_keys.append((self.phase_name, key))
        self.save_state()

    def invalidate_current_phase(self) -> list[str]:
        """作废**当前阶段**的存档，使失败的那一步在重试时真的重跑（2-2 实测发现的缺陷）。

        背景：检索失败时，工具层返回的是 `interface_error`（不是异常），
        于是「检索编排」这一步会被正常写进 journal。重试时它命中存档、
        原样返回失败结果——**重试等于没重试**。这里在标记失败前把该步骤的存档删掉。

        只作废当前阶段：更早成功的步骤仍从存档命中，**不重复调用模型、不重复计费**。
        """
        phase = self.phase_name
        keys = {key for name, key in self._journal_keys if name == phase}
        if not keys or not self.journal_path.exists():
            return []
        kept: list[str] = []
        for line in self.journal_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                kept.append(line)
                continue
            if row.get("k") in keys:
                continue
            kept.append(line)
        self.journal_path.write_text(
            "\n".join(kept) + ("\n" if kept else ""), encoding="utf-8"
        )
        for key in keys:
            self.cache.pop(key, None)
        self._journal_keys = [(name, key) for name, key in self._journal_keys if key not in keys]
        self.save_state()
        LOG.info("已作废阶段「%s」的存档 %s 条，重试将真正重跑该步骤", phase, len(keys))
        return sorted(keys)

    def snapshot_sources(self) -> None:
        """把依据池写盘（用户材料只落指纹与长度）。"""
        with self.sources_path.open("w", encoding="utf-8") as fh:
            for source in self.session.source_pool.values():
                fh.write(json.dumps(source.disk_safe(), ensure_ascii=False, default=str) + "\n")

    def save_state(self) -> None:
        args = self.args
        # 2-5：咨询问题里可能含当事人具体事实（C-12 / D7），问题原文不落盘，只留标记。
        if isinstance(args, dict) and args.get("consult") and args.get("question"):
            args = {**args, "question": "", "question_redacted": True}
        state = {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "session_id": self.session.id,
            "workflow": self.workflow,
            "args": args,
            "status": self.session.status,
            "phase": self.phase_name,
            "steps": self.steps,
            # 2-2：标记这次运行的运行方式，便于把「离线/夹具运行」的产物与真实运行分开
            "scenario": getattr(self.session, "scenario", "clean"),
            "provider": self.cfg.model_provider,
            "usage": self.usage,
            "created_at": self.state_created,
            "updated_at": _now(),
        }
        self.state_path.write_text(json.dumps(state, ensure_ascii=False, default=str), encoding="utf-8")

    @property
    def state_created(self) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(self.started_at))

    def finish(self, status: str, payload: dict[str, Any]) -> None:
        self.snapshot_sources()
        # 注意：任务结束 ≠ 会话结束。会话继续存活，界面可刷新、可导出、可续跑；
        # 只有用户主动关闭或 TTL 到期才会进入 closed。
        self.session.status = "active"
        self.session.total_ms = int((time.time() - self.started_at) * 1000)
        safe_payload = _safe_output(payload, material_norms=self.material_norms())
        self.output_path.write_text(
            json.dumps(safe_payload, ensure_ascii=False, default=str), encoding="utf-8"
        )
        self.save_state()


_PHASES = ["检索", "确认样本", "逐案提炼", "横向对比", "综合", "成稿"]

#: 判定“这段文字是不是用户材料原文”的窗口长度（规范化后）
_MATERIAL_WINDOW = 40
_MATERIAL_STRIDE = 10


def _is_redacted(value: Any) -> bool:
    return isinstance(value, dict) and value.get("redacted_material") is True


def _contains_redaction(value: Any) -> bool:
    """递归查找脱敏标记（取值可能嵌在 facts/basis/citations 里）。"""
    if _is_redacted(value):
        return True
    if isinstance(value, dict):
        return any(_contains_redaction(v) for v in value.values())
    if isinstance(value, list):
        return any(_contains_redaction(v) for v in value)
    return False


def _redact(value: Any, norms: list[str], ctx: Any) -> Any:
    """递归脱敏：字符串只要与任一用户材料原文有 ≥40 字的规范化公共片段，就整体替换。"""
    from ..models import normalize_text

    if isinstance(value, str):
        if len(value) < 20:
            return value
        norm = normalize_text(value)
        if len(norm) < 20:
            return value
        for material in norms:
            if norm in material:
                ctx.material_redacted = True
                return {
                    "redacted_material": True,
                    "len": len(value),
                    "note": "含用户上传材料原文，已按 C-12 脱敏（原文不落盘）",
                }
            # 反向：用户提供的文本（尤其是 2-5 咨询的短提问）出现在模型输出里，同样不得落盘
            if len(material) >= 12 and material in norm:
                ctx.material_redacted = True
                return {
                    "redacted_material": True,
                    "len": len(value),
                    "note": "含用户提供的原文，已按 C-12 脱敏（原文不落盘）",
                }
            limit = len(norm) - _MATERIAL_WINDOW
            for start in range(0, max(limit, 0) + 1, _MATERIAL_STRIDE):
                if norm[start : start + _MATERIAL_WINDOW] in material:
                    ctx.material_redacted = True
                    return {
                        "redacted_material": True,
                        "len": len(value),
                        "note": "含用户上传材料原文片段，已按 C-12 脱敏（原文不落盘）",
                    }
        return value
    if isinstance(value, dict):
        if value.get("kind") == "user_material" or value.get("origin") == "user":
            value = dict(value)
            if value.get("quote"):
                value["quote"] = ""
                value["quote_len"] = len(str(value.get("quote_len") or ""))
                value["redacted"] = True
        out = {}
        for key, item in value.items():
            # 标识与编号本身不含正文，保留可读性（否则矩阵/引用会看不懂）
            if key in {"case_id", "source_id", "identifier", "title", "status", "rule"}:
                out[key] = item
            else:
                out[key] = _redact(item, norms, ctx)
        return out
    if isinstance(value, list):
        return [_redact(item, norms, ctx) for item in value]
    return value


def _safe_output(payload: dict[str, Any], material_norms: list[str] | None = None) -> dict[str, Any]:
    """输出存档同样遵守用户材料原文不外落。"""
    import copy

    def scrub(value: Any) -> Any:
        if isinstance(value, Claim):
            return value.__dict__
        if isinstance(value, dict):
            if value.get("kind") == "user_material" or value.get("origin") == "user":
                value = copy.deepcopy(value)
                value["quote"] = ""
                value["redacted"] = True
            return {k: scrub(v) for k, v in value.items()}
        if isinstance(value, list):
            return [scrub(v) for v in value]
        return value

    scrubbed = scrub(payload)
    if material_norms:
        from ..models import normalize_text

        class _Mark:
            material_redacted = False

        mark = _Mark()

        def walk(node: Any) -> Any:
            if isinstance(node, str):
                if len(node) < 20:
                    return node
                norm = normalize_text(node)
                if len(norm) < 20:
                    return node
                for material in material_norms:
                    if norm in material:
                        mark.material_redacted = True
                        return {"redacted_material": True, "len": len(node)}
                    limit = len(norm) - _MATERIAL_WINDOW
                    for start in range(0, max(limit, 0) + 1, _MATERIAL_STRIDE):
                        if norm[start : start + _MATERIAL_WINDOW] in material:
                            mark.material_redacted = True
                            return {"redacted_material": True, "len": len(node)}
                return node
            if isinstance(node, dict):
                return {
                    k: (v if k in {"case_id", "source_id", "identifier", "title"} else walk(v))
                    for k, v in node.items()
                }
            if isinstance(node, list):
                return [walk(v) for v in node]
            return node

        scrubbed = walk(scrubbed)
    return scrubbed


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())
