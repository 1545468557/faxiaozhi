"""样本锁：三态转换、残留检测、分母与分布由代码计算。"""

from __future__ import annotations

from app.gates.sample import SampleLock, build_distribution, build_matrix, sources_for_sample
from app.models import Source


def lock() -> SampleLock:
    sample = SampleLock()
    sample.set_candidates(["a", "b", "c"])
    return sample


def test_candidates_dedup_and_order():
    sample = SampleLock()
    sample.set_candidates(["b", "a", "b", "c"])
    assert sample.candidates == ["b", "a", "c"]


def test_confirm_moves_excluded_out_of_confirmed():
    sample = lock()
    sample.confirm(["a", "b", "c"], ["c"])
    assert sample.confirmed == ["a", "b"]
    assert sample.excluded == ["c"]
    assert sample.locked


def test_confirm_ignores_ids_not_in_candidates():
    sample = lock()
    sample.confirm(["a", "zzz"], [])
    assert sample.confirmed == ["a"]


def test_exclude_removes_from_confirmed():
    sample = lock()
    sample.confirm(["a", "b"], [])
    sample.exclude("a")
    assert "a" not in sample.confirmed
    assert "a" in sample.excluded


def test_reinclude_removes_from_excluded():
    sample = lock()
    sample.confirm(["a", "b"], ["c"])
    sample.reinclude("c")
    assert "c" not in sample.excluded
    assert "c" in sample.confirmed


def test_residues_detects_excluded_citation():
    sample = lock()
    sample.confirm(["a", "b"], ["c"])
    assert sample.residues(["a", "c"]) == ["c"]
    assert sample.residues(["a", "b"]) == []


def test_matrix_only_contains_confirmed():
    cases = [{"case_id": "a"}, {"case_id": "b"}, {"case_id": "c"}]
    rows = build_matrix(cases, {"a", "c"})
    assert [r["case_id"] for r in rows] == ["a", "c"]


def test_matrix_is_sorted_by_identifier():
    cases = [{"case_id": "a", "identifier": "（2024）2号"}, {"case_id": "b", "identifier": "（2023）1号"}]
    rows = build_matrix(cases, {"a", "b"})
    assert [r["identifier"] for r in rows] == ["（2023）1号", "（2024）2号"]


def test_matrix_joins_list_fields():
    rows = build_matrix([{"case_id": "a", "issues": ["x", "y"]}], {"a"})
    assert rows[0]["issues"] == "x；y"


def test_distribution_denominator_excludes_excluded():
    cases = [{"case_id": "a", "stance": "support"}, {"case_id": "b", "stance": "oppose"}]
    dist = build_distribution(cases)
    assert dist["denominator"] == 2
    assert dist["counts"]["support"] == 1
    assert "本次确认样本 2 篇" in dist["text"]
    assert "不代表全国裁判比例" in dist["text"]


def test_distribution_always_carries_scope_statement():
    dist = build_distribution([])
    assert dist["denominator"] == 0
    assert "未检出相反观点不代表不存在相反裁判" in dist["text"]


def test_distribution_unknown_stance_folds_into_unknown():
    dist = build_distribution([{"case_id": "a", "stance": "weird"}])
    assert dist["counts"]["unknown"] == 1


def test_sources_for_sample_skips_missing():
    pool = {"a": Source(source_id="a"), "b": Source(source_id="b")}
    assert [s.source_id for s in sources_for_sample(pool, ["a", "zz"])] == ["a"]
