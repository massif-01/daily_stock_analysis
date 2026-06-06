# -*- coding: utf-8 -*-
"""Decision action taxonomy helpers for Issue #1390 P0.

This module is deliberately separate from ``src.agent.protocols``:
``DecisionAction`` is the new eight-state display taxonomy, while
``decision_type`` remains the existing buy/hold/sell statistics contract.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Literal, Optional, TypedDict, get_args

from src.report_language import normalize_report_language

DecisionAction = Literal["buy", "add", "hold", "reduce", "sell", "watch", "avoid", "alert"]
DecisionHorizon = Literal["intraday", "1d", "3d", "5d", "10d", "swing", "long"]
DecisionPlanQuality = Literal["complete", "partial", "minimal"]
DecisionSignalStatus = Literal["active", "expired", "invalidated", "closed", "archived"]


class DecisionActionFields(TypedDict):
    action: Optional[DecisionAction]
    action_label: Optional[str]


_ACTION_VALUES = set(get_args(DecisionAction))
_NON_STOCK_REPORT_TYPES = {"market_review"}

_ACTION_LABELS: Dict[str, Dict[str, str]] = {
    "buy": {"zh": "买入", "en": "Buy"},
    "add": {"zh": "加仓", "en": "Add"},
    "hold": {"zh": "持有", "en": "Hold"},
    "reduce": {"zh": "减仓", "en": "Reduce"},
    "sell": {"zh": "卖出", "en": "Sell"},
    "watch": {"zh": "观望", "en": "Watch"},
    "avoid": {"zh": "回避", "en": "Avoid"},
    "alert": {"zh": "预警", "en": "Alert"},
}

_EXPLICIT_ALIASES: Dict[str, DecisionAction] = {
    "strong_buy": "buy",
    "strong buy": "buy",
    "buy": "buy",
    "add": "add",
    "accumulate": "add",
    "hold": "hold",
    "reduce": "reduce",
    "trim": "reduce",
    "sell": "sell",
    "strong_sell": "sell",
    "strong sell": "sell",
    "watch": "watch",
    "wait": "watch",
    "avoid": "avoid",
    "alert": "alert",
}

_ACTION_PHRASES: Dict[DecisionAction, tuple[str, ...]] = {
    "avoid": (
        "不建议买入",
        "避免买入",
        "do not buy",
        "don't buy",
        "dont buy",
        "回避",
        "规避",
        "avoid",
    ),
    "alert": (
        "风险预警",
        "触发告警",
        "risk alert",
        "警惕",
        "alert",
    ),
    "buy": (
        "强烈买入",
        "strong_buy",
        "strong buy",
        "买入",
        "布局",
        "建仓",
        "buy",
    ),
    "add": (
        "加仓",
        "增持",
        "accumulate",
        "add",
    ),
    "hold": (
        "持有观察",
        "洗盘观察",
        "持有",
        "hold",
    ),
    "watch": (
        "观望",
        "等待",
        "wait",
        "watch",
    ),
    "reduce": (
        "减仓",
        "trim",
        "reduce",
    ),
    "sell": (
        "强烈卖出",
        "strong_sell",
        "strong sell",
        "卖出",
        "清仓",
        "sell",
    ),
}

_NEGATED_ACTION_PHRASES: Dict[DecisionAction, tuple[str, ...]] = {
    "avoid": (
        "暂不买入",
        "不要买入",
        "不宜买入",
        "先不买入",
        "暂不建仓",
        "不要建仓",
        "不宜建仓",
        "先不建仓",
        "暂不布局",
        "不要布局",
        "不宜布局",
        "先不布局",
        "not buy",
        "do not buy",
        "don't buy",
        "dont buy",
    ),
    "hold": (
        "不建议加仓",
        "无需加仓",
        "不要加仓",
        "不宜加仓",
        "暂不加仓",
        "不建议增持",
        "无需增持",
        "不要增持",
        "不宜增持",
        "暂不增持",
        "不建议卖出",
        "无需卖出",
        "不要卖出",
        "不宜卖出",
        "暂不卖出",
        "不建议减仓",
        "无需减仓",
        "不要减仓",
        "不宜减仓",
        "暂不减仓",
        "不建议清仓",
        "无需清仓",
        "不要清仓",
        "不宜清仓",
        "暂不清仓",
        "not sell",
        "do not sell",
        "don't sell",
        "dont sell",
    ),
}

_GUARD_ACTIONS: tuple[DecisionAction, ...] = ("avoid", "alert")


def _normalize_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", " ").replace("-", " ")


def _word_or_substring_match(text: str, phrase: str) -> bool:
    if not text or not phrase:
        return False
    normalized_phrase = _normalize_key(phrase)
    if re.search(r"[a-z]", normalized_phrase):
        return bool(re.search(rf"(?<![a-z0-9_]){re.escape(normalized_phrase)}(?![a-z0-9_])", text))
    return normalized_phrase in text


def _explicit_action(value: Any) -> Optional[DecisionAction]:
    normalized = _normalize_key(value)
    if not normalized:
        return None
    if normalized in _ACTION_VALUES:
        return normalized  # type: ignore[return-value]
    return _EXPLICIT_ALIASES.get(normalized)


def normalize_decision_action(value: Any) -> Optional[DecisionAction]:
    """Return a unique eight-state action for explicit values or clear text.

    Unknown or ambiguous human-readable advice returns ``None`` rather than
    defaulting to a neutral action.
    """

    explicit = _explicit_action(value)
    if explicit:
        return explicit

    text = _normalize_key(value)
    if not text:
        return None

    negated_matches: set[DecisionAction] = set()
    for action, phrases in _NEGATED_ACTION_PHRASES.items():
        if any(_word_or_substring_match(text, phrase) for phrase in phrases):
            negated_matches.add(action)
    if len(negated_matches) == 1:
        return next(iter(negated_matches))
    if len(negated_matches) > 1:
        return None

    guard_matches: set[DecisionAction] = set()
    for action in _GUARD_ACTIONS:
        if any(_word_or_substring_match(text, phrase) for phrase in _ACTION_PHRASES[action]):
            guard_matches.add(action)
    if len(guard_matches) == 1:
        return next(iter(guard_matches))
    if len(guard_matches) > 1:
        return None

    matches: set[DecisionAction] = set()
    for action, phrases in _ACTION_PHRASES.items():
        if action in _GUARD_ACTIONS:
            continue
        if any(_word_or_substring_match(text, phrase) for phrase in phrases):
            matches.add(action)

    if len(matches) == 1:
        return next(iter(matches))
    return None


def decision_type_from_action(action: Any) -> Optional[str]:
    """Bridge the eight-state action to the legacy buy/hold/sell contract."""

    normalized = _explicit_action(action)
    if normalized in {"buy", "add"}:
        return "buy"
    if normalized in {"sell", "reduce"}:
        return "sell"
    if normalized in {"hold", "watch", "avoid", "alert"}:
        return "hold"
    return None


def localize_action_label(action: Any, language: Optional[str] = "zh") -> Optional[str]:
    """Return a localized display label for a decision action."""

    normalized = _explicit_action(action)
    if not normalized:
        return None
    return _ACTION_LABELS[normalized][normalize_report_language(language)]


def build_action_fields(
    *,
    operation_advice: Any = None,
    explicit_action: Any = None,
    report_type: Any = None,
    report_language: Optional[str] = "zh",
) -> DecisionActionFields:
    """Build optional public action fields without mutating legacy contracts."""

    if str(report_type or "").strip().lower() in _NON_STOCK_REPORT_TYPES:
        return {"action": None, "action_label": None}

    action = normalize_decision_action(explicit_action)
    if action is None:
        advice_text = str(operation_advice or "").strip()
        if advice_text:
            action = normalize_decision_action(advice_text)

    return {
        "action": action,
        "action_label": localize_action_label(action, report_language) if action else None,
    }
