# ruff: noqa: E402, I001
"""说明：本文件需要在设置好环境后再导入 app 模块，故显式关闭导入位置/排序规则。

稳定性与熔断（2-2 S 组，6 条）：并发不串号、退避符合配置、超时可配、熔断打开与恢复、
耗时基线（p50/p95）。全部离线可跑（假 httpx.Client），不产生真实调用与费用。
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from app.config import get_config
from app.models import Status
from app.observability import Observability, _percentile
from app.tools import mcp as mcp_module
from app.tools.mcp import McpClient, McpProvider, breaker_for


class _FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "", headers: dict | None = None) -> None:
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://endpoint.invalid")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("boom", request=request, response=response)


class _FakeClient:
    def __init__(self, behavior) -> None:
        self._behavior = behavior

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def post(self, url: str, json: dict | None = None, headers: dict | None = None):
        return self._behavior(json or {}, headers or {})


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    mcp_module._TOOL_CACHE.clear()
    mcp_module._reset_breakers()
    yield
    mcp_module._TOOL_CACHE.clear()
    mcp_module._reset_breakers()


@pytest.fixture
def inject_transport(monkeypatch):
    def install(behavior) -> None:
        monkeypatch.setattr(mcp_module.httpx, "Client", lambda **kwargs: _FakeClient(behavior))

    return install


@pytest.fixture
def cfg():
    return get_config()


def _provider() -> McpProvider:
    return McpProvider(get_config(), "search_cases", "case", ["https://endpoint.invalid/mcp"])


def _case_body(payload: dict) -> str:
    """按请求里的检索文本回一个案例清单，用于「响应是否与请求一一对应」的断言。"""
    arguments = (payload.get("params") or {}).get("arguments") or {}
    text = str(arguments.get("text") or "?")
    body = {
        "jsonrpc": "2.0",
        "id": payload.get("id"),
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "list": [
                                {
                                    "caseNumber": f"（2024）示例{text}号",
                                    "title": text,
                                    "identified": f"本院认为：{text}",
                                }
                            ]
                        },
                        ensure_ascii=False,
                    ),
                }
            ]
        },
    }
    return json.dumps(body, ensure_ascii=False)


# ==================================================================== S17 并发不串号


def test_s17_concurrent_calls_do_not_cross_responses(inject_transport):
    inject_transport(lambda payload, headers: _FakeResponse(200, _case_body(payload)))
    expressions = [f"并发议题{i}" for i in range(8)]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda e: _provider().call(expression=e, size=1), expressions))

    assert [r.status for r in results] == [Status.OK] * len(expressions)
    for expression, result in zip(expressions, results, strict=True):
        assert result.sources, f"{expression} 未取到结果"
        assert result.sources[0].title == expression, "并发调用发生了响应串号"


# ==================================================================== S18 退避


def test_s18_backoff_sequence_matches_config(inject_transport, cfg, monkeypatch):
    cfg.raw.setdefault("mcp", {})
    cfg.raw["mcp"]["max_retries"] = 4
    cfg.raw["mcp"]["backoff_base_seconds"] = 0.5
    cfg.raw["mcp"]["backoff_max_seconds"] = 8
    slept: list[float] = []
    monkeypatch.setattr(mcp_module.time, "sleep", lambda seconds: slept.append(seconds))

    def behavior(payload, headers):
        raise httpx.ConnectError("name or service not known")

    inject_transport(behavior)
    client = McpClient(cfg, "https://endpoint.invalid/mcp")
    with pytest.raises(mcp_module.McpError):
        client._rpc("tools/call", {"name": "x", "arguments": {}})

    assert slept == [0.5, 1.0, 2.0], f"退避序列与配置不符：{slept}"


def test_s18b_backoff_is_capped_by_max(inject_transport, cfg, monkeypatch):
    cfg.raw.setdefault("mcp", {})
    cfg.raw["mcp"]["max_retries"] = 5
    cfg.raw["mcp"]["backoff_base_seconds"] = 4
    cfg.raw["mcp"]["backoff_max_seconds"] = 8
    slept: list[float] = []
    monkeypatch.setattr(mcp_module.time, "sleep", lambda seconds: slept.append(seconds))

    inject_transport(lambda payload, headers: (_ for _ in ()).throw(httpx.ReadTimeout("slow")))
    with pytest.raises(mcp_module.McpError):
        McpClient(cfg, "https://endpoint.invalid/mcp")._rpc("tools/call", {})
    assert slept == [4, 8, 8, 8], f"退避未按上限封顶：{slept}"


# ==================================================================== S19 超时可配


def test_s19_timeouts_are_configurable(cfg):
    cfg.raw.setdefault("mcp", {})
    cfg.raw["mcp"]["timeout_seconds"] = 7
    cfg.raw["mcp"]["connect_timeout_seconds"] = 2
    client = McpClient(cfg, "https://endpoint.invalid/mcp")
    timeout = client._timeout()
    assert timeout.read == 7
    assert timeout.connect == 2


def test_s19b_config_changes_apply_without_restart(cfg):
    """同一客户端每次读超时配置：改 config.yaml 后无需重启进程（配置驱动）。"""
    cfg.raw.setdefault("mcp", {})
    client = McpClient(cfg, "https://endpoint.invalid/mcp")
    cfg.raw["mcp"]["timeout_seconds"] = 3
    cfg.raw["mcp"]["connect_timeout_seconds"] = 1
    timeout = client._timeout()
    assert timeout.read == 3
    assert timeout.connect == 1


# ==================================================================== S20/S21 熔断


def test_s20_breaker_opens_and_fails_fast(inject_transport, cfg, monkeypatch):
    cfg.raw.setdefault("mcp", {})
    cfg.raw["mcp"]["max_retries"] = 1
    cfg.raw["mcp"]["circuit_breaker"] = {"enabled": True, "failure_threshold": 3, "open_seconds": 60}
    calls = {"n": 0}

    def behavior(payload, headers):
        calls["n"] += 1
        raise httpx.ConnectError("name or service not known")

    inject_transport(behavior)
    for _ in range(3):
        _provider().call(expression="设备质量", size=1)
    assert calls["n"] == 3, "前三次应真的发起请求"
    assert breaker_for(cfg, "https://endpoint.invalid/mcp").state() == "open"

    result = _provider().call(expression="设备质量", size=1)
    assert result.status is Status.INTERFACE_ERROR
    assert result.error_kind == "circuit_open"
    assert result.meta["retryable"] is True
    assert calls["n"] == 3, "熔断打开后必须快速失败，不得再发请求"


def test_s20b_config_disables_breaker(inject_transport, cfg):
    cfg.raw.setdefault("mcp", {})
    cfg.raw["mcp"]["max_retries"] = 1
    cfg.raw["mcp"]["circuit_breaker"] = {"enabled": False, "failure_threshold": 1, "open_seconds": 60}
    inject_transport(lambda payload, headers: (_ for _ in ()).throw(httpx.ConnectError("down")))
    for _ in range(3):
        _provider().call(expression="设备质量", size=1)
    assert breaker_for(cfg, "https://endpoint.invalid/mcp").state() == "closed"


def test_s21_breaker_allows_one_probe_after_open_window(inject_transport, cfg, monkeypatch):
    cfg.raw.setdefault("mcp", {})
    cfg.raw["mcp"]["max_retries"] = 1
    cfg.raw["mcp"]["circuit_breaker"] = {"enabled": True, "failure_threshold": 2, "open_seconds": 60}
    clock = {"now": 1000.0}
    monkeypatch.setattr(mcp_module.time, "monotonic", lambda: clock["now"])
    state = {"fail": True}

    def behavior(payload, headers):
        if state["fail"]:
            raise httpx.ConnectError("down")
        return _FakeResponse(200, _case_body(payload))

    inject_transport(behavior)
    for _ in range(2):
        _provider().call(expression="设备质量", size=1)
    breaker = breaker_for(cfg, "https://endpoint.invalid/mcp")
    assert breaker.state() == "open"

    # 窗口内：仍然快速失败
    clock["now"] += 30
    assert _provider().call(expression="设备质量", size=1).error_kind == "circuit_open"

    # 窗口过后：放行一次探测；上游恢复则熔断闭合
    clock["now"] += 31
    state["fail"] = False
    assert breaker.state() == "half_open"
    result = _provider().call(expression="设备质量", size=1)
    assert result.status is Status.OK, "半开探测成功后应恢复正常"
    assert breaker.state() == "closed"


def test_s21b_probe_failure_reopens_breaker(inject_transport, cfg, monkeypatch):
    cfg.raw.setdefault("mcp", {})
    cfg.raw["mcp"]["max_retries"] = 1
    cfg.raw["mcp"]["circuit_breaker"] = {"enabled": True, "failure_threshold": 2, "open_seconds": 60}
    clock = {"now": 1000.0}
    monkeypatch.setattr(mcp_module.time, "monotonic", lambda: clock["now"])
    inject_transport(lambda payload, headers: (_ for _ in ()).throw(httpx.ConnectError("down")))
    for _ in range(2):
        _provider().call(expression="设备质量", size=1)
    breaker = breaker_for(cfg, "https://endpoint.invalid/mcp")

    clock["now"] += 61
    _provider().call(expression="设备质量", size=1)      # 半开探测，仍然失败
    assert breaker.state() == "open", "探测失败必须重新计时"


def test_s20c_non_retryable_errors_do_not_open_breaker(inject_transport, cfg):
    """鉴权错/结构错不属于瞬时故障：用户改好密钥后不该被熔断窗口挡住。"""
    cfg.raw.setdefault("mcp", {})
    cfg.raw["mcp"]["max_retries"] = 1
    cfg.raw["mcp"]["circuit_breaker"] = {"enabled": True, "failure_threshold": 2, "open_seconds": 60}
    inject_transport(lambda payload, headers: _FakeResponse(401, '{"error":"unauthorized"}'))
    for _ in range(4):
        result = _provider().call(expression="设备质量", size=1)
        assert result.error_kind == "auth"
    assert breaker_for(cfg, "https://endpoint.invalid/mcp").state() == "closed"


# ==================================================================== S22 耗时基线


class _MetricsCfg:
    def __init__(self, path) -> None:
        self.metrics_path = path

    def get(self, key, default=None):
        return 30 if key == "metrics.retention_days" else default


def _write_metrics(path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8"
    )


def test_s20d_failed_retrieval_reports_masked_endpoint_alias(inject_transport, cfg):
    """降级记录必须带**脱敏**端点别名（实测发现失败分支只带了 endpoints 列表，别名是空的）。"""
    from app.session import Session
    from app.workflows.runtime import RunContext

    cfg.raw.setdefault("mcp", {})
    cfg.raw["mcp"]["max_retries"] = 1
    inject_transport(lambda payload, headers: (_ for _ in ()).throw(httpx.ConnectError("down")))
    result = _provider().call(expression="设备质量", size=1)

    ctx = RunContext(session=Session(), cfg=cfg, provider=None, kb=None)
    ctx.note_degradation(result)
    record = ctx.session.degradations[0]
    assert record.endpoint_alias, "降级记录缺少脱敏端点别名"
    assert "endpoint.invalid" in record.endpoint_alias  # mask_url 会保留主机、去掉参数
    assert "Bearer" not in record.endpoint_alias


def test_s22_latency_percentiles_are_correct_and_source_filtered(tmp_path):
    path = tmp_path / "metrics.jsonl"
    _write_metrics(
        path,
        [
            {"ts": "t", "source": "real", "event": "tool_call", "elapsed_ms": 100},
            {"ts": "t", "source": "real", "event": "tool_call", "elapsed_ms": 200},
            {"ts": "t", "source": "real", "event": "tool_call", "elapsed_ms": 300},
            {"ts": "t", "source": "real", "event": "tool_call", "elapsed_ms": 400},
            {"ts": "t", "source": "test", "event": "tool_call", "elapsed_ms": 9999},
        ],
    )
    obs = Observability(_MetricsCfg(path))

    real = obs.summary(source="real")
    assert real["latency_ms"]["count"] == 4
    assert real["latency_ms"]["p50"] == 200
    assert real["latency_ms"]["p95"] == 400
    assert real["latency_ms"]["max"] == 400, "测试来源的耗时不得混入真实基线"

    only_test = obs.summary(source="test")
    assert only_test["latency_ms"]["max"] == 9999
    assert obs.summary()["latency_ms"]["count"] == 5


def test_s22b_percentile_helper_handles_edges():
    assert _percentile([], 50) is None
    assert _percentile([42], 95) == 42
    assert _percentile([1, 2, 3, 4], 50) == 2
    assert _percentile([1, 2, 3, 4], 95) == 4
