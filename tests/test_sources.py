# ruff: noqa: E402, I001
"""说明：本文件需要在设置好环境后再导入 app 模块，故显式关闭导入位置/排序规则。

多源可插拔（2-3 SR 组）：优先级、短路、全部失败走降级矩阵、上限、权威性、离线回落、命中复用。
全部离线，不产生真实调用。
"""

from __future__ import annotations

import json

import pytest

from app.config import get_config
from app.library import ZONE_CITABLE, Library, build_entry_id, entry_from_source
from app.models import Source, Status, ToolResult
from app.sources import SourceProvider, SourceRouter, build_providers
from app.sources.local_library import LocalLibraryProvider

CASE_ID = "（2023）示例民终1001号"
CASE_TEXT = "本院认为，出卖人交付的货物存在质量瑕疵，买受人可以主张减少价款。"


@pytest.fixture(autouse=True)
def _restore_cfg():
    cfg = get_config()
    snapshot = json.loads(json.dumps(cfg.raw.get("sources", {})))
    library_snapshot = json.loads(json.dumps(cfg.raw.get("library", {})))
    yield
    cfg.raw["sources"] = snapshot
    cfg.raw["library"] = library_snapshot


class FakeProvider(SourceProvider):
    def __init__(self, name, kind="case", status=Status.OK, sources=None,
                 authoritative=False, boom=False, handled=True):
        self.name = name
        self.kind = kind
        self.authoritative = authoritative
        self.status = status
        self.sources = sources or []
        self.boom = boom
        self.handled = handled
        self.calls = 0
        self.fetch_calls = 0

    def search(self, **args):
        self.calls += 1
        if self.boom:
            raise RuntimeError("provider exploded")
        if not self.handled:
            return None
        return ToolResult(tool="search_cases", status=self.status, sources=list(self.sources),
                          detail="fake", meta={})

    def fetch(self, identifier, **args):
        self.fetch_calls += 1
        return self.sources[0] if self.sources else None


def _source(identifier=CASE_ID, text=CASE_TEXT, origin="mcp") -> Source:
    return Source(source_id="mcp_1", kind="case", identifier=identifier, title="t",
                  quote=text, origin=origin, status=Status.OK)


def _router(providers, kind="case", session=None) -> SourceRouter:
    return SourceRouter(get_config(), kind, providers, session=session, library=Library(get_config()))


# ==================================================================== 优先级与短路


def test_sr_priority_and_short_circuit():
    first = FakeProvider("first", status=Status.INTERFACE_ERROR)
    second = FakeProvider("second", sources=[_source()])
    third = FakeProvider("third", sources=[_source()])
    result = _router([first, second, third]).search(expression="x")
    assert result.status is Status.OK
    assert second.calls == 1
    assert third.calls == 0, "第一个成功来源之后的来源不得再调用（省配额）"


def test_sr_local_miss_continues_to_authoritative():
    """本地未命中必须继续走权威来源——不做本地优先检索，避免候选池偏向历史。"""
    local = LocalLibraryProvider(get_config(), "case")
    remote = FakeProvider("pkulaw_mcp", sources=[_source()], authoritative=True)
    result = _router([local, remote]).search(expression="新检索式")
    assert result.status is Status.OK
    assert remote.calls == 1


def test_sr_all_fail_uses_same_degradation_copy():
    from app.sources.mcp_source import FixtureSourceProvider

    fixture = FixtureSourceProvider(get_config(), "case", scenario="interface_error")
    result = _router([fixture]).search(expression="x")
    assert result.status is Status.INTERFACE_ERROR
    assert "未获得可核验依据" in result.detail and "不等于" in result.detail
    assert result.meta.get("retryable") is True, "降级文案与可重试标记沿用 2-2 矩阵"


def test_sr_no_provider_yields_canonical_copy():
    result = _router([]).search(expression="x")
    assert result.status is Status.INTERFACE_ERROR
    assert "未获得可核验依据" in result.detail and "不等于" in result.detail


def test_sr_last_failure_kind_is_preserved():
    a = FakeProvider("a", status=Status.INTERFACE_ERROR)
    b = FakeProvider("b", status=Status.PARSE_ERROR)
    result = _router([a, b]).search(expression="x")
    assert result.status is Status.PARSE_ERROR, "具体失败类型应保留，不被一律吞成接口失败"


def test_sr_max_provider_calls_limits_attempts():
    cfg = get_config()
    cfg.raw.setdefault("sources", {}).setdefault("merge", {})["max_provider_calls"] = 2
    providers = [FakeProvider(f"p{i}", status=Status.INTERFACE_ERROR) for i in range(4)]
    _router(providers).search(expression="x")
    assert sum(p.calls for p in providers) == 2, "超过上限必须停止调用"


def test_sr_provider_exception_does_not_break_router():
    boom = FakeProvider("boom", boom=True)
    ok = FakeProvider("ok", sources=[_source()])
    result = _router([boom, ok]).search(expression="x")
    assert result.status is Status.OK and ok.calls == 1


# ==================================================================== 配置驱动


def test_sr_unknown_provider_is_ignored_not_fatal(monkeypatch):
    cfg = get_config()
    cfg.raw.setdefault("sources", {}).setdefault("case", {})["providers"] = ["nonsense", "fixtures"]
    providers = build_providers(cfg, "case")
    assert [p.name for p in providers] == ["fixtures"], "未知来源只告警、不影响可用来源"


def test_sr_authoritative_removes_fixtures(monkeypatch):
    monkeypatch.setenv("FAXIAOZHI_OFFLINE", "0")
    monkeypatch.setenv("PKULAW_URL_CASE_SEMANTIC", "https://endpoint.invalid/mcp")
    cfg = get_config()
    cfg.raw.setdefault("sources", {}).setdefault("case", {})["providers"] = [
        "local_library", "pkulaw_mcp", "fixtures",
    ]
    providers = build_providers(cfg, "case")
    names = [p.name for p in providers]
    assert "pkulaw_mcp" in names
    assert "fixtures" not in names, "有权威来源时不得用合成夹具冒充真实检索"


def test_sr_offline_falls_back_to_fixtures():
    cfg = get_config()          # conftest 默认 FAXIAOZHI_OFFLINE=1 且未配置地址
    cfg.raw.setdefault("sources", {}).setdefault("case", {})["providers"] = [
        "local_library", "pkulaw_mcp", "fixtures",
    ]
    providers = build_providers(cfg, "case")
    names = [p.name for p in providers]
    assert "pkulaw_mcp" not in names
    assert "fixtures" in names


# ==================================================================== 命中复用与沉淀


def _seed_local_query(library: Library) -> None:
    library.upsert(entry_from_source(_source(), origin="mcp", zone=ZONE_CITABLE))
    library.put_query("search_cases", {"expression": "质量瑕疵"}, [build_entry_id("case", CASE_ID)])


def test_sr_local_query_hit_short_circuits_and_marks_local():
    cfg = get_config()
    library = Library(cfg)
    _seed_local_query(library)
    local = LocalLibraryProvider(cfg, "case", library=library)
    remote = FakeProvider("pkulaw_mcp", sources=[_source()], authoritative=True)
    router = SourceRouter(cfg, "case", [local, remote], library=library)
    result = router.search(expression="质量瑕疵")
    assert result.status is Status.OK
    assert remote.calls == 0, "命中本地缓存不得再调法宝"
    assert result.meta.get("local_hit") is True
    assert result.sources[0].local_hit is True
    assert result.sources[0].origin == "local"
    assert result.sources[0].corroboration == "single_local"


def test_sr_mcp_success_is_persisted_and_reusable():
    cfg = get_config()
    library = Library(cfg)
    remote = FakeProvider("pkulaw_mcp", sources=[_source()], authoritative=True)
    router = SourceRouter(cfg, "case", [remote], library=library)
    assert router.search(expression="质量瑕疵").status is Status.OK
    assert library.get("case", CASE_ID) is not None, "法宝成功结果应沉淀"
    assert library.get("case", CASE_ID).zone != ZONE_CITABLE, "未过门禁前只能在线索区"

    local = LocalLibraryProvider(cfg, "case", library=library)
    again = SourceRouter(cfg, "case", [local, remote], library=library)
    second = again.search(expression="质量瑕疵")
    assert second.sources[0].local_hit is True
    assert remote.calls == 1, "第二次应命中本地，不再调用法宝"


def test_sr_fixture_results_are_not_persisted():
    cfg = get_config()
    library = Library(cfg)
    fixture = FakeProvider("fixtures", sources=[_source(origin="fixture")])
    SourceRouter(cfg, "case", [fixture], library=library).search(expression="x")
    assert library.all_entries() == [], "夹具数据绝不入库"


def test_sr_write_disabled_does_not_persist():
    cfg = get_config()
    cfg.raw["library"]["write_on_success"] = False
    library = Library(cfg)
    remote = FakeProvider("pkulaw_mcp", sources=[_source()], authoritative=True)
    SourceRouter(cfg, "case", [remote], library=library).search(expression="x")
    assert library.all_entries() == []


# ==================================================================== 按标识取原文


def test_sr_fetch_prefers_local_library():
    cfg = get_config()
    library = Library(cfg)
    _seed_local_query(library)
    local = LocalLibraryProvider(cfg, "case", library=library)
    remote = FakeProvider("pkulaw_mcp", sources=[_source()], authoritative=True)
    router = SourceRouter(cfg, "case", [local, remote], library=library)
    source, provider_name = router.fetch(CASE_ID)
    assert source is not None and provider_name == "local_library"
    assert source.origin == "local" and remote.fetch_calls == 0


def test_sr_fetch_returns_none_when_unknown():
    router = _router([LocalLibraryProvider(get_config(), "case")])
    source, name = router.fetch("（1999）不存在的案号")
    assert source is None and name == ""
