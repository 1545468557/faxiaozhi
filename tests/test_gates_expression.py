"""表达边界：五类拦截 + 配置写错不崩。"""

from __future__ import annotations

import pytest

from app.gates.expression import ExpressionGuard

CLEAN = "本次确认样本 3 篇中，支持 2 篇、相反 1 篇。该分布仅描述本次确认样本。"


@pytest.mark.parametrize(
    ("category", "text"),
    [
        ("总体化推断", "总体而言该条款被支持。"),
        ("总体化推断", "全国各级法院对此类案件的比例逐年上升。"),
        ("总体化推断", "绝大多数法院都采取这一立场。"),
        ("伪指标", "该类案件的胜诉率为 78%。"),
        ("伪指标", "本次统计的支持率明显偏高。"),
        ("无来源百分比", "高达 90% 的案件得到支持。"),
        ("确定性承诺", "可以确定该条款无效。"),
        ("确定性承诺", "法院一定会支持我方主张。"),
        ("存在性断言", "该案例不存在。"),
        ("存在性断言", "检索后没有相关判例。"),
    ],
)
def test_five_categories_are_caught(category, text):
    hits = ExpressionGuard().scan(text)
    assert hits, f"未拦截：{text}"
    assert category in {h["category"] for h in hits}


def test_clean_text_has_no_hits():
    assert ExpressionGuard().scan(CLEAN) == []


def test_bad_extra_regex_is_ignored_without_crash():
    guard = ExpressionGuard(extra_patterns=["([unclosed"])
    assert guard.scan("正常内容") == []


def test_extra_regex_is_applied():
    guard = ExpressionGuard(extra_patterns=[r"绝对没问题"])
    assert guard.scan("这个结论绝对没问题")[0]["category"] == "自定义"


def test_rewrite_hint_mentions_denominator():
    hint = ExpressionGuard.rewrite_hint([{"category": "伪指标", "text": "胜诉率"}], denominator=3)
    assert "本次样本 N 篇中 n 篇" in hint
    assert "3 篇" in hint
