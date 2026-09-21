# ruff: noqa: E402, I001
# 说明：本文件必须在设置 sys.path / 环境变量之后再导入 app 与测试辅助模块，
# 因此这里显式关闭「导入位置」与「导入排序」两条规则（有意为之）。
"""测试前置。

1. 强制离线（stub 模型 + 无 MCP），避免任何真实外呼。
2. **目录隔离 + 污染哨兵（2-2 新增）**：把所有写入型目录（runs / exports / metrics）
   重定向到临时目录，并在真实数据目录上放哨兵。背景：实测发现跑 pytest 会把脏数据
   写进真实 `runs/` 与 `data/metrics.jsonl`，导致北极星指标不可信。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

os.environ["MODEL_PROVIDER"] = "stub"
os.environ["MODEL_API_KEY"] = ""
os.environ["OFFLINE_SCENARIO"] = "clean"
os.environ["FAXIAOZHI_OFFLINE"] = "1"
# 2-2 新增：标记指标事件来源为 test，使北极星指标可只取真实数据（防测试污染）
os.environ["FAXIAOZHI_TEST_MODE"] = "1"
for _name in (
    "PKULAW_MCP_TOKEN",
    "PKULAW_URL_STATUTE_SEMANTIC",
    "PKULAW_URL_STATUTE_KEYWORD",
    "PKULAW_URL_CASE_SEMANTIC",
    "PKULAW_URL_CASE_KEYWORD",
    "PKULAW_URL_STATUTE_EXACT",
    "PKULAW_URL_STATUTE_TRACE",
    "PKULAW_URL_CASE_TRACE",
    "PKULAW_URL_ANTI_HALLUCINATION",
    "PKULAW_URL_STATUTE_HISTORY",
    "PKULAW_URL_HYPERLINK",
    "PKULAW_URL_SMART_SEARCH",
):
    os.environ[_name] = ""
# 具名槽位可能随时新增，这里统一清空，确保测试永不外呼
for _name in [k for k in os.environ if k.startswith("PKULAW_URL_")]:
    os.environ[_name] = ""

import pytest  # noqa: E402

from app.config import get_config  # noqa: E402
from app.knowledge import KnowledgeBase  # noqa: E402
from app.loop import install_default_hooks  # noqa: E402
from app.session import Session  # noqa: E402
from app.workflows import launch  # noqa: E402

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import _isolation as isolation  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _isolate_write_dirs(tmp_path_factory):
    """写入型目录全部改到临时目录；真实目录上装哨兵，测试结束比对是否被污染。"""
    import app.observability as obs_module

    root = tmp_path_factory.mktemp("faxiaozhi-isolated")
    cfg = get_config()

    # 先记录真实目录的基线（注意：这些属性会 mkdir，但不会新增文件）
    real_runs = cfg.runs_dir
    real_exports = cfg.exports_dir
    real_metrics = cfg.metrics_path
    # 2-3：本地依据库也要隔离（语料目录 data/library/）
    from pathlib import Path as _Path

    real_library = _Path(cfg.get("library.dir", "data/library"))
    if not real_library.is_absolute():
        real_library = cfg.root / real_library
    baseline = {
        "runs": isolation.snapshot(real_runs),
        "exports": isolation.snapshot(real_exports),
        "metrics": isolation.snapshot_lines(real_metrics),
        "library": isolation.snapshot(real_library),
    }

    # 重定向写入型目录（fixtures / knowledge 是只读数据源，必须保留真实路径）
    cfg.raw.setdefault("journal", {})["dir"] = str(root / "runs")
    cfg.raw.setdefault("export", {})["dir"] = str(root / "exports")
    cfg.raw.setdefault("metrics", {})["path"] = str(root / "metrics.jsonl")
    cfg.raw.setdefault("library", {})["dir"] = str(root / "library")
    cfg.raw["library"]["enabled"] = True

    # 重置可观测性与依据库单例，使其采用隔离后的路径
    obs_module._obs = None
    obs_module.get_observability(cfg)
    import app.library as library_module

    library_module._lib = None

    # 把隔离后的路径暴露给测试
    pytest.isolated_root = root  # type: ignore[attr-defined]

    yield

    isolation.assert_unchanged(
        baseline["runs"], isolation.snapshot(real_runs), "真实 runs/（测试不得写入）"
    )
    isolation.assert_unchanged(
        baseline["exports"], isolation.snapshot(real_exports), "真实 exports/（测试不得写入）"
    )
    isolation.assert_unchanged(
        baseline["metrics"],
        isolation.snapshot_lines(real_metrics),
        "真实 data/metrics.jsonl（测试不得写入）",
    )
    isolation.assert_unchanged(
        baseline["library"],
        isolation.snapshot(real_library),
        "真实 data/library/（测试不得写入）",
    )


@pytest.fixture(autouse=True)
def _isolate_library_per_test(tmp_path):
    """每条用例一个独立的依据库目录，避免语料/检索缓存跨用例串扰。"""
    cfg = get_config()
    cfg.raw.setdefault("library", {})
    cfg.raw["library"]["dir"] = str(tmp_path / "library")
    cfg.raw["library"]["enabled"] = True
    import app.library as library_module

    library_module._lib = None
    yield
    library_module._lib = None


@pytest.fixture(autouse=True)
def _install_hooks():
    install_default_hooks()
    yield


@pytest.fixture(autouse=True)
def _reset_policy():
    cfg = get_config()
    cfg.raw.setdefault("policy", {})
    cfg.raw["policy"]["allow_synthetic"] = False
    yield
    cfg.raw["policy"]["allow_synthetic"] = False


@pytest.fixture(autouse=True)
def _reset_circuit_breakers():
    """熔断器/依据库单例是进程内的：不逐用例重置的话，前一用例的失败/语料会把后一用例污染
    （实测发现的测试串扰）。这里前后各清一次，保持用例相互独立。"""
    from app.tools import mcp as mcp_module

    mcp_module._reset_breakers()
    yield
    mcp_module._reset_breakers()
    import app.library as library_module

    library_module._lib = None


def set_policy(**kwargs):
    get_config().raw.setdefault("policy", {}).update(kwargs)


async def run_research(
    topic: str = "设备质量存在瑕疵时，买受人能否主张解除合同？",
    conditions: dict | None = None,
    confirmed: list[str] | None = None,
    scenario: str = "clean",
    allow_synthetic: bool = False,
    sample_size: int | None = None,
    timeout: float = 20.0,
    run_id: str | None = None,
    session: Session | None = None,
) -> Session:
    """跑完整条类案研究链路，到确认点自动提交样本。"""
    set_policy(allow_synthetic=allow_synthetic)
    cfg = get_config()
    session = session or Session()
    kb = KnowledgeBase(cfg)
    args = {"topic": topic, "conditions": conditions or {}}
    task = asyncio.create_task(
        launch(session, cfg, kb, args, run_id=run_id, scenario=scenario)
    )
    for _ in range(800):
        if session.checkpoint_kind is not None or task.done():
            break
        await asyncio.sleep(0.005)

    if session.checkpoint_kind is not None:
        ids = [str(c["source_id"]) for c in session.candidates]
        chosen = list(confirmed) if confirmed is not None else (ids[:sample_size] if sample_size else ids)
        excluded = [i for i in ids if i not in chosen]
        session.checkpoint_value = {"confirmed": chosen, "excluded": excluded}
        session.checkpoint_waiter.set()

    await asyncio.wait_for(task, timeout=timeout)
    return session
