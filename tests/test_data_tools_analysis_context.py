# -*- coding: utf-8 -*-
"""Contract tests for get_analysis_context tool code normalization behavior."""

import os
import sys
import unittest
from dataclasses import dataclass
from datetime import date, timedelta
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.agent.tools.data_tools import _handle_get_analysis_context


@dataclass
class _Bar:
    code: str
    date: date


class _DummyDB:
    def __init__(self):
        self._rows = {}

    def seed(self, code: str, count: int, *, end_date: date) -> None:
        bucket = self._rows.setdefault(code, [])
        bucket.clear()
        for idx in range(count):
            current_date = end_date - timedelta(days=count - idx - 1)
            bucket.append(_Bar(code=code, date=current_date))

    def get_latest_data(self, code: str, days: int = 2):
        rows = list(self._rows.get(code, []))
        return list(reversed(rows[-days:]))

    def get_analysis_context(self, code: str):
        rows = self._rows.get(code, [])
        if not rows:
            return None
        latest = rows[-1]
        previous = rows[-2] if len(rows) > 1 else None
        context = {
            "code": code,
            "date": latest.date.isoformat(),
            "today": {"date": latest.date.isoformat()},
        }
        if previous is not None:
            context["yesterday"] = {"date": previous.date.isoformat()}
        return context


class TestGetAnalysisContextTool(unittest.TestCase):
    def test_prefixed_input_resolves_canonical_bucket(self) -> None:
        db = _DummyDB()
        db.seed("600519", 2, end_date=date(2026, 4, 16))

        with patch("src.agent.tools.data_tools._get_db", return_value=db), patch(
            "src.services.stock_history_cache.get_db", return_value=db
        ):
            result = _handle_get_analysis_context("SH600519")

        self.assertEqual(result["code"], "600519")
        self.assertEqual(result["today"]["date"], "2026-04-16")

    def test_legacy_bucket_fallback_still_works(self) -> None:
        db = _DummyDB()
        db.seed("SH600519", 2, end_date=date(2026, 4, 16))

        with patch("src.agent.tools.data_tools._get_db", return_value=db), patch(
            "src.services.stock_history_cache.get_db", return_value=db
        ):
            result = _handle_get_analysis_context("SH600519")

        self.assertEqual(result["code"], "SH600519")
        self.assertEqual(result["today"]["date"], "2026-04-16")


if __name__ == "__main__":
    unittest.main()
