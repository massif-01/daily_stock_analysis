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
        ("暂不建仓", "avoid"),
        ("先不布局", "avoid"),
        ("不建议加仓", "hold"),
        ("不建议卖出", "hold"),
        ("无需卖出", "hold"),
        ("不要卖出", "hold"),
        ("暂不卖出", "hold"),
        ("无需减仓", "hold"),
        ("不建议清仓", "hold"),
    ],
)
def test_normalize_decision_action_handles_negated_trade_actions(value: str, expected: str) -> None:
    assert normalize_decision_action(value) == expected


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
