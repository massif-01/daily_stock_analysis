# -*- coding: utf-8 -*-
"""Shared stock-code extraction helpers for agent text inputs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List


# Common English words (2-5 uppercase letters) that should NOT be treated as
# US stock tickers.  This set is checked by _extract_stock_code() and should
# be kept at module level to avoid re-creating it on every call.
_COMMON_WORDS: set[str] = {
    # Pronouns / articles / prepositions / conjunctions
    "AM", "AS", "AT", "BE", "BY", "DO", "GO", "HE", "IF", "IN",
    "IS", "IT", "ME", "MY", "NO", "OF", "ON", "OR", "SO", "TO",
    "UP", "US", "WE",
    "THE", "AND", "FOR", "ARE", "BUT", "NOT", "YOU", "ALL",
    "CAN", "HAD", "HER", "WAS", "ONE", "OUR", "OUT", "HAS",
    "HIS", "HOW", "ITS", "LET", "MAY", "NEW", "NOW", "OLD",
    "SEE", "WAY", "WHO", "DID", "GET", "HIM", "USE", "SAY",
    "SHE", "TOO", "ANY", "WITH", "FROM", "THAT", "THAN",
    "THIS", "WHAT", "WHEN", "WILL", "JUST", "ALSO",
    "BEEN", "EACH", "HAVE", "MUCH", "ONLY", "OVER",
    "SOME", "SUCH", "THEM", "THEN", "THEY", "VERY",
    "WERE", "YOUR", "ABOUT", "AFTER", "COULD", "EVERY",
    "OTHER", "THEIR", "THERE", "THESE", "THOSE", "WHICH",
    "WOULD", "BEING", "STILL", "WHERE",
    # Finance/analysis jargon that looks like tickers
    "BUY", "SELL", "HOLD", "LONG", "PUT", "CALL",
    "ETF", "IPO", "RSI", "EPS", "PEG", "ROE", "ROA",
    "USA", "USD", "CNY", "HKD", "EUR", "GBP",
    "STOCK", "TRADE", "PRICE", "INDEX", "FUND",
    "HIGH", "LOW", "OPEN", "CLOSE", "STOP", "LOSS",
    "TREND", "BULL", "BEAR", "RISK", "CASH", "BOND",
    "MACD", "VWAP", "BOLL",
    "TTM", "LTM", "NTM", "FWD", "YOY", "QOQ", "YTD",
    "EBIT", "EBITDA", "DCF", "CAGR", "FCF", "NAV", "AUM",
    "PE", "PB",
    # Greetings / filler words that often appear in chat messages
    "HELLO", "PLEASE", "THANKS", "CHECK", "LOOK", "THINK",
    "MAYBE", "GUESS", "TELL", "SHOW", "WHAT", "WHATS",
    "WHY", "WHEN", "HOWDY", "HEY", "HI",
}

_LOWERCASE_TICKER_HINTS = re.compile(
    r"分析|看看|查一?下|研究|诊断|走势|趋势|股价|股票|个股",
)


@dataclass(frozen=True)
class StockCodeMention:
    """A stock-code candidate plus its source span in the input text."""

    code: str
    raw: str
    start: int
    end: int


def _is_denied_ticker_candidate(candidate: str) -> bool:
    """Return whether a text token should not be auto-treated as a ticker."""
    return (candidate or "").strip().upper() in _COMMON_WORDS


def _extract_stock_code(text: str) -> str:
    """Best-effort stock code extraction from free text."""
    codes = extract_stock_codes(text, limit=1)
    return codes[0] if codes else ""


def extract_stock_codes(text: str, *, limit: int | None = None) -> List[str]:
    """Return explicit stock-code candidates from free text in encounter order."""
    return [mention.code for mention in iter_stock_code_mentions(text, limit=limit)]


def iter_stock_code_mentions(text: str, *, limit: int | None = None) -> List[StockCodeMention]:
    """Return explicit stock-code candidates with source spans."""
    if not text:
        return []

    found: List[StockCodeMention] = []
    seen: set[str] = set()

    def add(candidate: str, start: int, end: int) -> None:
        if not candidate:
            return
        normalized = candidate.upper()
        if normalized in seen:
            return
        seen.add(normalized)
        found.append(StockCodeMention(code=normalized, raw=candidate, start=start, end=end))

    # A-share / BSE 6-digit codes.
    for match in re.finditer(r'(?<!\d)((?:[03648]\d{5}|92\d{4}))(?!\d)', text):
        add(match.group(1), match.start(1), match.end(1))
        if limit is not None and len(found) >= limit:
            return found

    # HK — same lookaround approach.
    for match in re.finditer(r'(?<![a-zA-Z])(hk\d{5})(?!\d)', text, re.IGNORECASE):
        add(match.group(1), match.start(1), match.end(1))
        if limit is not None and len(found) >= limit:
            return found

    # US ticker — require 2+ uppercase letters bounded by non-alpha chars.
    for match in re.finditer(r'(?<![a-zA-Z])([A-Z]{2,5}(?:\.[A-Z]{1,2})?)(?![a-zA-Z])', text):
        candidate = match.group(1)
        if not _is_denied_ticker_candidate(candidate):
            add(candidate, match.start(1), match.end(1))
            if limit is not None and len(found) >= limit:
                return found

    stripped = (text or "").strip()
    bare_match = re.fullmatch(r'([A-Za-z]{2,5}(?:\.[A-Za-z]{1,2})?)', stripped)
    if bare_match:
        candidate = bare_match.group(1).upper()
        if not _is_denied_ticker_candidate(candidate):
            start = text.find(bare_match.group(1))
            add(candidate, max(start, 0), max(start, 0) + len(bare_match.group(1)))
            if limit is not None and len(found) >= limit:
                return found

    if not _LOWERCASE_TICKER_HINTS.search(stripped):
        return found

    for match in re.finditer(r'(?<![a-zA-Z])([A-Za-z]{2,5}(?:\.[A-Za-z]{1,2})?)(?![a-zA-Z])', text):
        raw_candidate = match.group(1)
        candidate = raw_candidate.upper()
        if _is_denied_ticker_candidate(candidate):
            continue
        add(candidate, match.start(1), match.end(1))
        if limit is not None and len(found) >= limit:
            return found

    return found


def canonicalize_stock_code(value: object) -> str:
    """Return the canonical stock-code form used for scope comparisons."""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        from data_provider.base import canonical_stock_code, normalize_stock_code

        return canonical_stock_code(normalize_stock_code(text))
    except Exception:
        return text.upper()
