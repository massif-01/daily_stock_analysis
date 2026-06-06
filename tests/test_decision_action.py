# -*- coding: utf-8 -*-
"""Tests for Issue #1390 P0 decision action taxonomy helpers."""

import pytest

from src.schemas.decision_action import (
    build_action_fields,
    decision_type_from_action,
    localize_action_label,
    normalize_decision_action,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("strong_buy", "buy"),
        ("强烈买入", "buy"),
        ("买入", "buy"),
        ("布局", "buy"),
        ("建仓", "buy"),
        ("add", "add"),
        ("加仓", "add"),
        ("增持", "add"),
        ("accumulate", "add"),
        ("hold", "hold"),
        ("持有", "hold"),
        ("持有观察", "hold"),
        ("洗盘观察", "hold"),
        ("watch", "watch"),
        ("观望", "watch"),
        ("等待", "watch"),
        ("wait", "watch"),
        ("reduce", "reduce"),
        ("减仓", "reduce"),
        ("trim", "reduce"),
        ("sell", "sell"),
        ("卖出", "sell"),
        ("清仓", "sell"),
        ("strong_sell", "sell"),
        ("强烈卖出", "sell"),
        ("avoid", "avoid"),
        ("回避", "avoid"),
        ("规避", "avoid"),
        ("不建议买入", "avoid"),
        ("避免买入", "avoid"),
        ("do not buy", "avoid"),
        ("alert", "alert"),
        ("风险预警", "alert"),
        ("警惕", "alert"),
        ("触发告警", "alert"),
        ("risk alert", "alert"),
    ],
)
def test_normalize_decision_action_matrix(value: str, expected: str) -> None:
    assert normalize_decision_action(value) == expected


@pytest.mark.parametrize("value", ["", None, "观察", "等待突破后买入", "买入或卖出", "普通复盘说明"])
def test_normalize_decision_action_unknown_or_ambiguous_returns_none(value: str | None) -> None:
    assert normalize_decision_action(value) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("暂不买入", "avoid"),
        ("不要买入", "avoid"),
        ("不宜买入", "avoid"),
        ("先不买入", "avoid"),
        ("无需买入", "avoid"),
        ("无须买入", "avoid"),
        ("不建议建仓", "avoid"),
        ("暂不建仓", "avoid"),
        ("无需建仓", "avoid"),
        ("无须建仓", "avoid"),
        ("不建议布局", "avoid"),
        ("先不布局", "avoid"),
        ("无需布局", "avoid"),
        ("无须布局", "avoid"),
        ("no buy", "avoid"),
        ("no need to buy", "avoid"),
        ("need not buy", "avoid"),
        ("cannot buy", "avoid"),
        ("can't buy", "avoid"),
        ("不建议加仓", "hold"),
        ("无须加仓", "hold"),
        ("no add", "hold"),
        ("no need to add", "hold"),
        ("need not add", "hold"),
        ("cannot add", "hold"),
        ("no accumulate", "hold"),
        ("can't accumulate", "hold"),
        ("不建议卖出", "hold"),
        ("无需卖出", "hold"),
        ("无须卖出", "hold"),
        ("不要卖出", "hold"),
        ("暂不卖出", "hold"),
        ("no sell", "hold"),
        ("no need to sell", "hold"),
        ("cannot sell", "hold"),
        ("can't sell", "hold"),
        ("无需减仓", "hold"),
        ("无须减仓", "hold"),
        ("no reduce", "hold"),
        ("no need to reduce", "hold"),
        ("cannot reduce", "hold"),
        ("no trim", "hold"),
        ("can't trim", "hold"),
        ("不建议清仓", "hold"),
    ],
)
def test_normalize_decision_action_handles_negated_trade_actions(value: str, expected: str) -> None:
    assert normalize_decision_action(value) == expected


@pytest.mark.parametrize(
    "advice",
    [
        "无需买入，等待确认",
        "无须建仓，继续观察",
        "无需布局，等待突破",
        "no buy until breakout",
        "no need to buy before confirmation",
        "cannot buy before confirmation",
        "can't buy before confirmation",
    ],
)
def test_build_action_fields_prioritizes_negated_buy_advice_over_embedded_buy_phrase(advice: str) -> None:
    assert build_action_fields(operation_advice=advice) == {
        "action": "avoid",
        "action_label": "回避",
    }


@pytest.mark.parametrize(
    "advice",
    [
        "无须加仓，维持仓位",
        "无需卖出，继续持有",
        "无须减仓，等待确认",
        "no add before confirmation",
        "cannot add before confirmation",
        "no need to accumulate here",
        "can't accumulate here",
        "no sell before earnings",
        "cannot sell before earnings",
        "no need to reduce exposure",
        "can't reduce exposure",
        "no trim while trend holds",
        "cannot trim while trend holds",
    ],
)
def test_build_action_fields_prioritizes_negated_hold_advice_over_embedded_trade_phrase(advice: str) -> None:
    assert build_action_fields(operation_advice=advice) == {
        "action": "hold",
        "action_label": "持有",
    }


@pytest.mark.parametrize(
    ("action", "decision_type"),
    [
        ("buy", "buy"),
        ("add", "buy"),
        ("sell", "sell"),
        ("reduce", "sell"),
        ("hold", "hold"),
        ("watch", "hold"),
        ("avoid", "hold"),
        ("alert", "hold"),
        (None, None),
    ],
)
def test_decision_type_from_action_bridge(action: str | None, decision_type: str | None) -> None:
    assert decision_type_from_action(action) == decision_type


def test_localize_action_label_uses_report_language() -> None:
    assert localize_action_label("avoid", "zh") == "回避"
    assert localize_action_label("avoid", "en") == "Avoid"


def test_build_action_fields_respects_market_review_exclusion() -> None:
    fields = build_action_fields(
        operation_advice="买入",
        explicit_action="buy",
        report_type="market_review",
    )

    assert fields == {"action": None, "action_label": None}


def test_build_action_fields_prefers_explicit_action_over_advice() -> None:
    fields = build_action_fields(
        operation_advice="买入",
        explicit_action="watch",
        report_language="zh",
    )

    assert fields == {"action": "watch", "action_label": "观望"}


def test_build_action_fields_keeps_empty_action_without_advice_or_explicit_action() -> None:
    fields = build_action_fields(
        operation_advice=None,
        report_language="zh",
    )

    assert fields == {"action": None, "action_label": None}
