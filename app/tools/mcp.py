"""北大法宝 MCP 客户端与适配层。

三件事全部**配置驱动**，接口变更只改 `config.yaml`，不改代码：
1. **工具名**：`mcp.tool_names.<逻辑名>`
2. **入参翻译**：`mcp.arg_map.<逻辑名>`（我方参数名 -> MCP 参数名）
3. **返回归一化**：`mcp.field_map`（清单路径候选 + 字段名候选）

连接失败一律映射为 `interface_error` —— **禁止回退到模型记忆作答**（PRD 3.8.2）。
"""

from __future__ import annotations

import itertools
import json
import logging
import re
import time
from typing import Any

import httpx

from ..config import Config
from ..models import Source, Status, ToolResult

LOG = logging.getLogger("faxiaozhi.mcp")

_JSONRPC_ID = itertools.count(1)


class McpError(RuntimeError):
    """带**可分类的失败原因**的 MCP 调用错误（2-2 新增）。

    分类的意义：用户在界面上看到的文案必须与实际原因对应，且
    `retryable` 决定「重试」按钮是否有意义（例如 Token 错就不该鼓励重试）。
    """

    def __init__(self, kind: str, message: str, retryable: bool = True) -> None:
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable


def _classify(exc: Exception) -> McpError:
    """把底层异常翻译成带类别的 McpError（顺序敏感：ConnectTimeout 是 Timeout 的子类）。"""
    if isinstance(exc, McpError):
        return exc
    if isinstance(exc, httpx.ConnectTimeout):
        return McpError("connect", "无法连接到检索服务（连接超时）")
    if isinstance(exc, httpx.ConnectError):
        return McpError("connect", "无法连接到检索服务（地址不可达）")
    if isinstance(exc, httpx.TimeoutException):
        return McpError("timeout", "检索服务响应超时")
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code in (401, 403):
            return McpError("auth", f"检索服务鉴权失败（HTTP {code}）", retryable=False)
        return McpError(f"http_{code}", f"检索服务返回 HTTP {code}", retryable=code >= 500)
    if isinstance(exc, json.JSONDecodeError):
        return McpError("non_json", "检索服务返回内容不是合法 JSON", retryable=False)
    return McpError(type(exc).__name__.lower(), f"检索服务调用失败：{type(exc).__name__}")

#: 裁判文书本身不适用「效力状态」概念，用固定值表示已核对（门禁 R6 放行）
CASE_EFFECTIVE_STATUS = "不适用（裁判文书）"


class CircuitBreaker:
    """按端点熔断：连续可重试失败达阈值 → 快速失败，`open_seconds` 后放行一次探测（半开）。

    2-2 新增。设计取舍（见《MCP 稳定性与配额基线》）：
    - **只统计「可重试」的瞬时故障**（connect / timeout / http_5xx / empty_response / rpc_error）。
      鉴权错（auth）与数据结构错（non_json）属于配置/数据问题，不计入熔断——
      否则用户改好密钥后还要白白等一个熔断窗口。
    - 半开状态允许一次探测请求；探测成功即闭合，失败则重新计时。
    - 状态与计数**只在进程内存**，重启即重置（与本产品会话态一致，不引入新依赖）。
    """

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.failures = 0
        self.opened_at: float | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("mcp.circuit_breaker.enabled", True))

    @property
    def threshold(self) -> int:
        return max(1, int(self.cfg.get("mcp.circuit_breaker.failure_threshold", 5)))

    @property
    def open_seconds(self) -> float:
        return max(0.0, float(self.cfg.get("mcp.circuit_breaker.open_seconds", 60)))

    def state(self) -> str:
        """closed（放行）/ open（快速失败）/ half_open（放行一次探测）。"""
        if not self.enabled or self.opened_at is None:
            return "closed"
        if time.monotonic() - self.opened_at >= self.open_seconds:
            return "half_open"
        return "open"

    def allow(self) -> bool:
        return self.state() != "open"

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = None

    def record_failure(self) -> None:
        """记录一次「可重试」失败（调用方负责只对瞬时故障调用）。"""
        if not self.enabled:
            return
        self.failures += 1
        if self.opened_at is not None or self.failures >= self.threshold:
            self.opened_at = time.monotonic()


#: 端点 -> 熔断器（进程内存；测试用 _reset_breakers() 清空）
_BREAKERS: dict[str, CircuitBreaker] = {}


def breaker_for(cfg: Config, endpoint: str) -> CircuitBreaker:
    key = endpoint or ""
    breaker = _BREAKERS.get(key)
    if breaker is None or breaker.cfg is not cfg:
        breaker = CircuitBreaker(cfg)
        _BREAKERS[key] = breaker
    return breaker


def _reset_breakers() -> None:
    _BREAKERS.clear()


def mask_url(url: str) -> str:
    """给日志/界面用的脱敏地址（去掉 query 与疑似密钥片段）。"""
    if not url:
        return ""
    head, _, tail = url.partition("?")
    masked = head + ("?…（已隐藏参数）" if tail else "")
    parts = masked.split("/")
    for index, part in enumerate(parts):
        if len(part) >= 24 and all(ch.isalnum() or ch in "-_" for ch in part):
            parts[index] = part[:6] + "…"
    return "/".join(parts)


class McpClient:
    def __init__(self, cfg: Config, endpoint: str) -> None:
        self.cfg = cfg
        self.endpoint = endpoint
        self.token = cfg.mcp_token
        self.timeout = int(cfg.get("mcp.timeout_seconds", 20))
        self.connect_timeout = int(cfg.get("mcp.connect_timeout_seconds", 5))
        self.max_retries = int(cfg.get("mcp.max_retries", 2))
        self.backoff_base = float(cfg.get("mcp.backoff_base_seconds", 0.5))
        self.backoff_max = float(cfg.get("mcp.backoff_max_seconds", 8))
        self._session_id: str | None = None

    def _timeout(self) -> httpx.Timeout:
        """连接阶段单独超时：地址不可达时快速失败，不把 20 秒耗在连不上上。

        2-2：超时值**每次读配置**，改 `config.yaml` 后无需重启即生效（可配、可关闭）。
        """
        return httpx.Timeout(
            int(self.cfg.get("mcp.timeout_seconds", self.timeout)),
            connect=int(self.cfg.get("mcp.connect_timeout_seconds", self.connect_timeout)),
        )

    def _backoff(self, attempt: int) -> float:
        return min(self.backoff_base * (2 ** (attempt - 1)), self.backoff_max)

    # ------------------------------------------------------------ 传输
    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.token:
            headers["Authorization"] = (
                self.token if self.token.lower().startswith("bearer ") else f"Bearer {self.token}"
            )
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        breaker = breaker_for(self.cfg, self.endpoint)
        if not breaker.allow():
            # 熔断打开：不发请求、不等待，直接快速失败（2-2 稳定性要求）
            raise McpError(
                "circuit_open",
                "检索服务连续失败，已临时熔断。稍后自动恢复，期间不再重复请求。",
                retryable=True,
            )
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": next(_JSONRPC_ID), "method": method}
        if params is not None:
            payload["params"] = params
        last: McpError | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                with httpx.Client(timeout=self._timeout()) as client:
                    response = client.post(self.endpoint, json=payload, headers=self._headers())
                    if response.status_code in (401, 403):
                        raise McpError(
                            "auth", f"检索服务鉴权失败（HTTP {response.status_code}）", retryable=False
                        )
                    response.raise_for_status()
                    if sid := response.headers.get("mcp-session-id"):
                        self._session_id = sid
                    body = _parse_body(response)
                    breaker.record_success()
                    return body
            except McpError as exc:
                last = exc
                LOG.warning("MCP 调用失败（%s），第 %s 次尝试", exc.kind, attempt)
                if not exc.retryable:
                    raise
                if attempt < self.max_retries:
                    time.sleep(self._backoff(attempt))
            except Exception as exc:
                last = _classify(exc)
                LOG.warning("MCP 调用失败（%s），第 %s 次尝试", last.kind, attempt)
                if not last.retryable:
                    raise last from exc
                if attempt < self.max_retries:
                    time.sleep(self._backoff(attempt))
        if last is not None and last.retryable:
            breaker.record_failure()
        raise last or McpError("unknown", "检索服务调用失败")

    def initialize(self) -> dict[str, Any]:
        result = self._rpc(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "faxiaozhi", "version": "0.1.0"},
            },
        )
        try:
            with httpx.Client(timeout=self._timeout()) as client:
                client.post(
                    self.endpoint,
                    json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                    headers=self._headers(),
                )
        except Exception:
            LOG.debug("initialized 通知发送失败（不影响后续）")
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        self.initialize()
        result = self._rpc("tools/list", {})
        return result.get("tools") or result.get("result", {}).get("tools") or []

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        return result.get("result", result)


def _parse_body(response: httpx.Response) -> dict[str, Any]:
    content_type = response.headers.get("content-type", "")
    text = response.text
    if not (text or "").strip():
        raise McpError("empty_response", "检索服务返回空响应")
    if "text/event-stream" in content_type:
        chunks = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
        for chunk in reversed(chunks):
            if chunk and chunk != "[DONE]":
                try:
                    return json.loads(chunk)
                except json.JSONDecodeError as exc:
                    raise McpError("non_json", "检索服务返回的流式内容不是合法 JSON", False) from exc
        raise McpError("empty_response", "检索服务流式响应中没有数据负载")
    try:
        body = json.loads(text)
    except json.JSONDecodeError as exc:
        raise McpError("non_json", "检索服务返回内容不是合法 JSON", False) from exc
    if isinstance(body, dict) and "error" in body:
        raise McpError("rpc_error", "检索服务返回错误", False)
    return body


# ------------------------------------------------------------------ 归一化


def field_map_get(payload: Any, dotted: str) -> Any:
    cur: Any = payload
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def unwrap_result(raw: Any) -> Any:
    """剥掉 MCP 工具返回外壳：`structuredContent` / `content[].text`（后者常是 JSON 字符串）。"""
    if not isinstance(raw, dict):
        return raw
    structured = raw.get("structuredContent")
    if isinstance(structured, (dict, list)):
        return structured
    content = raw.get("content")
    if isinstance(content, list):
        texts = [
            c.get("text")
            for c in content
            if isinstance(c, dict) and c.get("type") == "text" and c.get("text")
        ]
        joined = "\n".join(str(t) for t in texts)
        if joined:
            try:
                return json.loads(joined)
            except json.JSONDecodeError:
                return {"text": joined}
    return raw


_TOOL_CACHE: dict[str, list[str]] = {}


def resolve_tool_name(client: McpClient, cfg: Config, logical: str) -> str:
    """同一个服务在不同端点上的工具名不一样（聚合端点带命名空间前缀）。

    规则：配置名命中 > 去掉命名空间后缀同名 > 该端点唯一工具 > 失败。
    """
    configured = str(cfg.get(f"mcp.tool_names.{logical}", "") or logical)
    if client.endpoint not in _TOOL_CACHE:
        try:
            _TOOL_CACHE[client.endpoint] = [
                str(t.get("name", "")) for t in client.list_tools()
            ]
        except Exception:
            _TOOL_CACHE[client.endpoint] = []
    names = _TOOL_CACHE[client.endpoint]
    if not names:
        return configured
    if configured in names:
        return configured
    short = configured.rsplit(".", 1)[-1]
    for name in names:
        if name == short or name.rsplit(".", 1)[-1] == short:
            return name
    if len(names) == 1:
        return names[0]
    raise RuntimeError(f"端点 {mask_url(client.endpoint)} 上没有工具 {configured}；可用：{names}")


def _deep_find_list(payload: Any, depth: int = 3) -> list[Any] | None:
    """兜底：在返回体里向下找第一个「由字典组成的列表」。"""
    if depth < 0:
        return None
    if isinstance(payload, list):
        if payload and all(isinstance(x, dict) for x in payload):
            return payload
        for item in payload:
            found = _deep_find_list(item, depth - 1)
            if found:
                return found
        return None
    if isinstance(payload, dict):
        for key in ("list", "data", "records", "items", "results", "rows", "result"):
            if key in payload:
                found = _deep_find_list(payload[key], depth - 1)
                if found:
                    return found
        for value in payload.values():
            if isinstance(value, (dict, list)):
                found = _deep_find_list(value, depth - 1)
                if found:
                    return found
    return None


def find_list(payload: Any, cfg: Config) -> tuple[list[Any] | None, dict[str, Any]]:
    """按配置的候选路径找清单；找不到就回传顶层键名，便于校准。"""
    if isinstance(payload, list):
        return payload, {}
    if not isinstance(payload, dict):
        return None, {"raw_type": type(payload).__name__}

    for path in cfg.get("mcp.field_map.list_candidates", []) or []:
        value = field_map_get(payload, path)
        if isinstance(value, list):
            return value, {}
        if isinstance(value, dict):
            for sub in ("list", "data", "records", "items", "results", "rows"):
                if isinstance(value.get(sub), list):
                    return value[sub], {}
    found = _deep_find_list(payload)
    if found is not None:
        return found, {}
    return None, {"raw_keys": sorted(payload.keys())}


def pick_field(row: Any, kind: str, field: str, cfg: Config) -> Any:
    """按候选字段名逐个尝试，取到即用；数组按「、」连接（接口里大量使用数组字段）。"""
    if not isinstance(row, dict):
        return None
    for path in cfg.get(f"mcp.field_map.{kind}.{field}", []) or []:
        value = field_map_get(row, path) if "." in path else row.get(path)
        if isinstance(value, list):
            joined = "、".join(str(v) for v in value if str(v).strip())
            if joined:
                return joined
            continue
        if value not in (None, "", {}, 0):
            return value
    return None


_CLAUSE = re.compile(r"\s*(第[一二三四五六七八九十百零〇\d]+条(?:之[一二三四五六七八九十]+)?)")


def build_statute_identifier(title: str, quote: str, fallback: str) -> str:
    """法条引用标识 = 法规名 + 条号（条号从正文开头抽取）。"""
    match = _CLAUSE.match(quote or "")
    clause = match.group(1) if match else ""
    if title and clause:
        return f"{title} {clause}"
    return title or clause or fallback


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).strip()
    return text or None


def normalize_rows(rows: list[Any], cfg: Config, kind: str) -> list[Source]:
    sources: list[Source] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        identifier = _as_str(pick_field(row, kind, "identifier", cfg))
        title = _as_str(pick_field(row, kind, "title", cfg))
        quote = _as_str(pick_field(row, kind, "quote", cfg)) or ""
        if kind == "case":
            extra = [
                _as_str(pick_field(row, kind, key, cfg))
                for key in ("quote_extra",)
            ]
            # quote_extra 是字段列表，上面只取到第一个候选；这里把配置的候选逐个取出再合并
            extras = []
            for path in cfg.get("mcp.field_map.case.quote_extra", []) or []:
                value = row.get(path)
                if isinstance(value, str) and value.strip():
                    extras.append(value.strip())
            if extras:
                quote = "\n".join([quote] + extras).strip() if quote else "\n".join(extras)
            del extra
        if kind == "statute" and cfg.get("mcp.field_map.statute.identifier_clause_from_quote"):
            identifier = build_statute_identifier(title or "", quote, f"未识别标识 #{index + 1}")
        if not identifier:
            identifier = title or f"未识别标识 #{index + 1}"
        sources.append(
            Source(
                source_id=f"mcp_{kind}_{index + 1}",
                kind="case" if kind == "case" else "statute",
                title=title or "",
                identifier=identifier,
                quote=quote,
                effective_status=_as_str(pick_field(row, kind, "effective_status", cfg))
                or (CASE_EFFECTIVE_STATUS if kind == "case" else "unknown"),
                applicable_from=_as_str(pick_field(row, kind, "applicable_from", cfg)),
                applicable_to=_as_str(pick_field(row, kind, "applicable_to", cfg)),
                court=_as_str(pick_field(row, kind, "court", cfg)),
                level=_as_str(pick_field(row, kind, "level", cfg)),
                region=_as_str(pick_field(row, kind, "region", cfg)),
                decided_on=_as_str(pick_field(row, kind, "decided_on", cfg)),
                uri=_as_str(pick_field(row, kind, "uri", cfg)),
                origin="mcp",
                synthetic=False,
                status=Status.OK if quote.strip() else Status.ABSTRACT_ONLY,
            )
        )
    return sources


def translate_args(cfg: Config, logical: str, args: dict[str, Any]) -> dict[str, Any]:
    """我方参数名 -> MCP 真实参数名；未映射的参数一律丢弃（MCP 侧会拒未知参数）。"""
    mapping: dict[str, str] = cfg.get(f"mcp.arg_map.{logical}", {}) or {}
    out: dict[str, Any] = {}
    for key, value in (args or {}).items():
        if value in (None, "", [], {}):
            continue
        real = mapping.get(key)
        if real:
            out[real] = value

    if logical == "search_cases":
        cause = str(args.get("cause") or "").strip()
        if cause and "text" in out and cause not in str(out["text"]):
            out["text"] = f"{out['text']} {cause}".strip()

    max_size = int(cfg.get("mcp.limits.max_size", 20))
    if "size" in out:
        try:
            out["size"] = max(1, min(int(out["size"]), max_size))
        except (TypeError, ValueError):
            out.pop("size")
    if logical in ("search_cases", "search_statutes") and "size" not in out:
        out["size"] = int(cfg.get("mcp.limits.default_size", 10))
    return out


class McpProvider:
    """把若干 MCP 端点包装成与夹具体相同的调用面（按顺序尝试，末位通常是聚合地址兜底）。"""

    def __init__(self, cfg: Config, logical: str, kind: str, endpoints: list[str]) -> None:
        self.cfg = cfg
        self.logical = logical
        self.kind = kind
        self.endpoints = endpoints

    def call(self, **args: Any) -> ToolResult:
        real_args = translate_args(self.cfg, self.logical, args)
        last_kind = ""
        last_retryable = True
        last_keys: dict[str, Any] = {}
        applied = [k for k in (args or {}) if args.get(k) not in (None, "", [], {})]
        ignored = [k for k in (args or {}) if k not in applied]

        for endpoint in self.endpoints:
            client = McpClient(self.cfg, endpoint)
            tool_name = ""
            try:
                tool_name = resolve_tool_name(client, self.cfg, self.logical)
                raw = client.call_tool(tool_name, real_args)
                payload = unwrap_result(raw)
                rows, keys = find_list(payload, self.cfg)
                if rows is None:
                    last_keys = keys
                    last_kind, last_retryable = "schema", False
                    continue
                sources = normalize_rows(rows, self.cfg, self.kind)
                if not sources:
                    return ToolResult(
                        tool=self.logical,
                        status=Status.NO_MATCH,
                        detail="本次检索条件与数据源范围内未找到匹配。这不代表相关案例不存在。",
                        meta={"endpoint": mask_url(endpoint), "tool": tool_name},
                    )
                abstract = [s for s in sources if s.status is Status.ABSTRACT_ONLY]
                status = Status.ABSTRACT_ONLY if len(abstract) == len(sources) else Status.OK
                detail = f"真实检索返回 {len(sources)} 条；来源：北大法宝 MCP（{tool_name}）。"
                if status is Status.ABSTRACT_ONLY:
                    detail += "本次仅取得摘要、无全文，摘要可作线索但不能作为引用依据。"
                return ToolResult(
                    tool=self.logical,
                    status=status,
                    sources=sources,
                    detail=detail,
                    meta={
                        "endpoint": mask_url(endpoint),
                        "tool": tool_name,
                        "applied_conditions": applied,
                        "ignored_conditions": ignored,
                        "sent_args": sorted(real_args.keys()),
                        "max_chars": int(self.cfg.get("limits.max_tool_result_chars", 4000)),
                    },
                )
            except McpError as exc:
                last_kind, last_retryable = exc.kind, exc.retryable
                LOG.warning("端点不可用（%s / %s）：%s", mask_url(endpoint), last_kind, exc)
                continue
            except Exception as exc:
                classified = _classify(exc)
                last_kind, last_retryable = classified.kind, classified.retryable
                LOG.warning("端点不可用（%s / %s）：%s", mask_url(endpoint), last_kind, exc)
                continue

        if last_keys:
            return ToolResult(
                tool=self.logical,
                status=Status.PARSE_ERROR,
                detail="检索结果结构无法识别，请联系维护者校准字段映射。",
                error_kind="schema",
                meta={**last_keys, "endpoint": mask_url(self.endpoints[0])},
            )
        return ToolResult(
            tool=self.logical,
            status=Status.INTERFACE_ERROR,
            detail="检索接口调用失败，本次未获得可核验依据。这不等于「无相关案例」。可点击重试。",
            error_kind=last_kind or "unavailable",
            meta={
                "retryable": last_retryable,
                "transport": "mcp",
                "endpoints": [mask_url(e) for e in self.endpoints],
            },
        )
