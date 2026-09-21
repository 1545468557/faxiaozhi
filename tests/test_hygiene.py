# ruff: noqa: E402, I001
# 说明：本文件必须在设置 sys.path / 环境变量之后再导入 app 与测试辅助模块，
# 因此这里显式关闭「导入位置」与「导入排序」两条规则（有意为之）。
"""数据与测试卫生（2-2 H 组）。

对应实测发现的真实缺陷：
① 跑 pytest 会把脏数据写进**真实的** `runs/` 与 `data/metrics.jsonl`；
② `runs/` 里的过期锁文件不会被清理；
③ 因此北极星指标（引用可溯源率等）混入测试数据、不可信。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

import _isolation as isolation
from app.config import get_config

PROJECT_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import clean_runs  # noqa: E402


# ------------------------------------------------------------------ H23 / H24


def test_h23_write_dirs_are_redirected_away_from_real_project():
    """写入型目录必须全部指向临时目录，绝不落在真实工程目录里。"""
    cfg = get_config()
    real_root = cfg.root
    for path in (cfg.runs_dir, cfg.exports_dir, cfg.metrics_path):
        assert real_root not in path.parents, f"{path} 仍在真实工程目录内"


def test_h24_running_the_agent_does_not_touch_real_data_dirs():
    """真实数据目录在写入动作前后必须零变化（哨兵的核心用途）。"""
    cfg = get_config()
    real_runs = cfg.root / "runs"
    real_metrics = cfg.root / "data" / "metrics.jsonl"
    before_runs = isolation.snapshot(real_runs)
    before_metrics = isolation.snapshot_lines(real_metrics)

    # 真的写一次（走隔离后的路径）
    from app.observability import get_observability

    obs = get_observability(cfg)
    obs.metric(event="hygiene_probe", run_id="hygiene_probe_01")
    probe = cfg.runs_dir / "hygiene_probe_01.journal.jsonl"
    probe.write_text('{"kind": "probe"}\n', encoding="utf-8")

    assert probe.exists(), "写入未落到隔离目录，测试隔离失效"
    after_runs = isolation.snapshot(real_runs)
    after_metrics = isolation.snapshot_lines(real_metrics)
    isolation.assert_unchanged(before_runs, after_runs, "真实 runs/")
    isolation.assert_unchanged(before_metrics, after_metrics, "真实 data/metrics.jsonl")


# ------------------------------------------------------------------ H25


def test_h25_sentinel_self_proof_catches_pollution(tmp_path):
    """哨兵自证：故意制造污染，必须被抓到（否则哨兵形同虚设）。"""
    watched = tmp_path / "runs"
    watched.mkdir()
    before = isolation.snapshot(watched)

    (watched / "ordercheck1.journal.jsonl").write_text('{"kind": "agent"}\n', encoding="utf-8")

    with pytest.raises(AssertionError) as exc:
        isolation.assert_unchanged(before, isolation.snapshot(watched), "真实 runs/")
    assert "ordercheck1.journal.jsonl" in str(exc.value)

    # 指标文件的行数哨兵同样要能抓到
    metrics = tmp_path / "metrics.jsonl"
    metrics.write_text('{"event": "a"}\n', encoding="utf-8")
    lines_before = isolation.snapshot_lines(metrics)
    with metrics.open("a", encoding="utf-8") as fh:
        fh.write('{"event": "b"}\n')
    with pytest.raises(AssertionError):
        isolation.assert_unchanged(lines_before, isolation.snapshot_lines(metrics), "真实指标文件")


def test_h25b_sentinel_passes_when_nothing_changed(tmp_path):
    """反向：没有任何改动时哨兵不得误报。"""
    watched = tmp_path / "runs"
    watched.mkdir()
    (watched / "keep.json").write_text("{}", encoding="utf-8")
    before = isolation.snapshot(watched)
    isolation.assert_unchanged(before, isolation.snapshot(watched), "真实 runs/")
    assert isolation.diff(before, isolation.snapshot(watched), "真实 runs/") == []


# ------------------------------------------------------------------ H26


def test_h26_expired_lock_is_cleaned_and_idempotent(tmp_path):
    """过期锁可被清理；重复执行无副作用（幂等）。"""
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    stale = runs_dir / "3fde79fc19c1.lock"
    stale.write_text("12345", encoding="utf-8")
    old = time.time() - 3 * 3600
    import os

    os.utime(stale, (old, old))
    fresh = runs_dir / "fresh.lock"
    fresh.write_text("12345", encoding="utf-8")

    first = clean_runs.clean_locks(runs_dir, lock_hours=2.0, apply=True)
    assert any("3fde79fc19c1.lock" in name for name in first)
    assert not stale.exists()
    assert fresh.exists(), "未过期的锁不得被清理"

    # 幂等：再跑一次不再有可清理项，且不报错
    second = clean_runs.clean_locks(runs_dir, lock_hours=2.0, apply=True)
    assert second == []
    assert fresh.exists()


def test_h26b_dry_run_does_not_modify_anything(tmp_path):
    """默认只报告：不加 --apply 时不许改动磁盘。"""
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    stale = runs_dir / "old.lock"
    stale.write_text("1", encoding="utf-8")
    import os

    old = time.time() - 5 * 3600
    os.utime(stale, (old, old))

    reported = clean_runs.clean_locks(runs_dir, lock_hours=2.0, apply=False)
    assert reported, "应报告待清理项"
    assert stale.exists(), "dry-run 不得真删"


# ------------------------------------------------------------------ H27


def test_h27_metrics_can_be_filtered_by_source(tmp_path):
    """指标可按来源分离：北极星指标只取真实数据，不被测试数据污染。"""
    from app.observability import SOURCE_REAL, SOURCE_TEST, Observability

    cfg = get_config()
    obs = Observability(cfg)
    obs.metrics_path = tmp_path / "metrics.jsonl"
    obs.source = SOURCE_REAL
    obs.metric(event="gate", gate_accepted=9, gate_rejected=0)
    obs.source = SOURCE_TEST
    obs.metric(event="gate", gate_accepted=90, gate_rejected=10)

    everything = obs.summary()
    assert everything["by_source"] == {SOURCE_REAL: 1, SOURCE_TEST: 1}

    only_real = obs.summary(source=SOURCE_REAL)
    assert only_real["events"] == 1
    assert only_real["citations"]["accepted"] == 9
    assert only_real["citations"]["traceable_rate"] == 1.0

    only_test = obs.summary(source=SOURCE_TEST)
    assert only_test["events"] == 1
    assert only_test["citations"]["accepted"] == 90
    assert only_test["citations"]["traceable_rate"] == 0.9


def test_h27b_test_mode_marks_events_as_test():
    """本测试进程必须把事件标为 test（否则隔离形同虚设）。"""
    from app.observability import SOURCE_TEST, get_observability

    assert get_observability().source == SOURCE_TEST


# ------------------------------------------------------------------ H28


def test_h28_test_run_residue_is_archived_not_deleted(tmp_path):
    """历史测试残渣移入 archive-tests，不删除（可回溯）。"""
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    for name in ("ordercheck1.json", "testresume01.json", "6caf7b4f71d5.json"):
        (runs_dir / name).write_text("{}", encoding="utf-8")

    moved = clean_runs.archive_test_runs(runs_dir, apply=True)
    assert "ordercheck1.json" in moved
    assert "testresume01.json" in moved
    assert "6caf7b4f71d5.json" not in moved, "真实 run_id 不得被当测试残渣搬走"
    assert (runs_dir / "archive-tests" / "ordercheck1.json").exists()
    assert not (runs_dir / "ordercheck1.json").exists()
    assert (runs_dir / "6caf7b4f71d5.json").exists()


def test_h28b_metrics_split_keeps_real_lines(tmp_path):
    """指标拆分：测试行进归档，真实行原样保留。"""
    metrics = tmp_path / "metrics.jsonl"
    real_row = '{"event": "gate", "run_id": "6caf7b4f71d5", "gate_accepted": 9, "source": "real"}'
    test_row = '{"event": "gate", "run_id": "ordercheck1", "gate_accepted": 1, "source": "test"}'
    legacy_row = '{"event": "gate", "run_id": "fceedb791969", "gate_accepted": 3}'
    metrics.write_text("\n".join([real_row, test_row, legacy_row]) + "\n", encoding="utf-8")

    real, test = clean_runs.split_metrics(metrics, apply=True)
    assert (real, test) == (2, 1)
    kept = isolation.read_metrics(metrics)
    assert {row["run_id"] for row in kept} == {"6caf7b4f71d5", "fceedb791969"}
    archived = isolation.read_metrics(tmp_path / "metrics.test-archive.jsonl")
    assert [row["run_id"] for row in archived] == ["ordercheck1"]


# ------------------------------------------------------------------ H29 离线运行识别


def test_h29_offline_run_is_detected_by_state_file(tmp_path):
    """标注了 provider=stub / 故障场景的运行，必须被判为「离线运行」（不算真实成绩）。"""
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    (runs_dir / "stubrun0001.json").write_text(
        json.dumps({"provider": "stub", "scenario": "clean"}), encoding="utf-8"
    )
    (runs_dir / "faultrun0001.json").write_text(
        json.dumps({"provider": "deepseek", "scenario": "interface_error"}), encoding="utf-8"
    )
    (runs_dir / "realrun0001.json").write_text(
        json.dumps({"provider": "deepseek", "scenario": "clean"}), encoding="utf-8"
    )

    assert clean_runs.is_offline_run(runs_dir, "stubrun0001")[0] is True
    assert clean_runs.is_offline_run(runs_dir, "faultrun0001")[0] is True
    assert clean_runs.is_offline_run(runs_dir, "realrun0001")[0] is False


def test_h29b_legacy_offline_run_detected_by_synthetic_fixture(tmp_path):
    """2-2 之前的旧产物没有 provider 字段：依据池里出现 synthetic 即判为离线运行。"""
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    (runs_dir / "oldrun0001.json").write_text(json.dumps({}), encoding="utf-8")
    (runs_dir / "oldrun0001.sources.jsonl").write_text(
        '{"source_id": "case_1", "synthetic": true}\n', encoding="utf-8"
    )
    (runs_dir / "really0001.json").write_text(json.dumps({}), encoding="utf-8")
    (runs_dir / "really0001.sources.jsonl").write_text(
        '{"source_id": "mcp_case_1", "synthetic": false}\n', encoding="utf-8"
    )

    assert clean_runs.is_offline_run(runs_dir, "oldrun0001")[0] is True
    assert clean_runs.is_offline_run(runs_dir, "really0001")[0] is False


def test_h29c_archive_moves_all_sibling_files_then_is_idempotent(tmp_path):
    """回归：判定必须先于搬运。

    实测缺陷：边搬边判时，run 的 `.json` 存档被先搬走后，同名 journal/output/sources
    就再也判不出来，导致每次只搬一半（864 个文件只搬走 432 个）。
    """
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    run_id = "offlinerun01"
    (runs_dir / f"{run_id}.json").write_text(
        json.dumps({"provider": "stub"}), encoding="utf-8"
    )
    for suffix in ("journal.jsonl", "output.json", "sources.jsonl"):
        (runs_dir / f"{run_id}.{suffix}").write_text("{}", encoding="utf-8")

    moved = clean_runs.archive_test_runs(runs_dir, apply=True)
    assert len(moved) == 4, f"四个文件应一次全部搬走，实际 {moved}"
    assert not list(runs_dir.glob(f"{run_id}.*")), "不应残留同名文件"

    # 幂等：再跑一次不应有可搬运项
    assert clean_runs.archive_test_runs(runs_dir, apply=True) == []


def test_h29d_offline_metrics_rows_are_archived(tmp_path):
    """离线运行的指标行不得留在真实统计里（否则北极星指标被夹具成绩稀释）。"""
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    (runs_dir / "offlinerow01.json").write_text(
        json.dumps({"provider": "stub"}), encoding="utf-8"
    )
    metrics = tmp_path / "metrics.jsonl"
    metrics.write_text(
        "\n".join(
            [
                '{"event": "gate", "run_id": "offlinerow01", "gate_accepted": 1, "source": "real"}',
                '{"event": "gate", "run_id": "realtrade01", "gate_accepted": 9, "source": "real"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (runs_dir / "realtrade01.json").write_text(
        json.dumps({"provider": "deepseek", "scenario": "clean"}), encoding="utf-8"
    )

    real, archived = clean_runs.split_metrics(metrics, apply=True, runs_dir=runs_dir)
    assert (real, archived) == (1, 1)
    kept = isolation.read_metrics(metrics)
    assert [row["run_id"] for row in kept] == ["realtrade01"]
