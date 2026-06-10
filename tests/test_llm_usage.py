# -*- coding: utf-8 -*-
"""Unit tests for LLM usage tracking (storage + analyzer helper)."""

import hashlib
import hmac as py_hmac
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.llm.usage import (
    attach_message_hmacs,
    build_message_hmacs,
    extract_usage_payload,
    normalize_litellm_usage,
    _reset_usage_hmac_secret_cache_for_tests,
)
from src.storage import DatabaseManager, LLMUsage, persist_llm_usage


def _fresh_db() -> DatabaseManager:
    """Return a DatabaseManager backed by a fresh in-memory SQLite database."""
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    return db


class TestRecordLLMUsage(unittest.TestCase):
    def setUp(self):
        self.db = _fresh_db()

    def tearDown(self):
        DatabaseManager.reset_instance()

    def test_record_single_row(self):
        self.db.record_llm_usage(
            call_type="analysis",
            model="gemini/gemini-2.5-flash",
            prompt_tokens=100,
            completion_tokens=200,
            total_tokens=300,
            stock_code="600519",
        )
        with self.db.session_scope() as session:
            rows = session.query(LLMUsage).all()
            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row.call_type, "analysis")
            self.assertEqual(row.model, "gemini/gemini-2.5-flash")
            self.assertEqual(row.stock_code, "600519")
            self.assertEqual(row.prompt_tokens, 100)
            self.assertEqual(row.completion_tokens, 200)
            self.assertEqual(row.total_tokens, 300)

    def test_record_without_stock_code(self):
        self.db.record_llm_usage(
            call_type="market_review",
            model="openai/gpt-4o",
            prompt_tokens=50,
            completion_tokens=150,
            total_tokens=200,
        )
        with self.db.session_scope() as session:
            rows = session.query(LLMUsage).all()
            self.assertEqual(len(rows), 1)
            self.assertIsNone(rows[0].stock_code)

    def test_record_multiple_rows(self):
        for i in range(5):
            self.db.record_llm_usage(
                call_type="agent",
                model="gemini/gemini-2.5-flash",
                prompt_tokens=10 * i,
                completion_tokens=20 * i,
                total_tokens=30 * i,
            )
        with self.db.session_scope() as session:
            count = session.query(LLMUsage).count()
            self.assertEqual(count, 5)


class TestLLMUsageNormalizer(unittest.TestCase):
    def test_openai_cached_tokens(self):
        usage = normalize_litellm_usage(
            {
                "prompt_tokens": 2000,
                "completion_tokens": 100,
                "total_tokens": 2100,
                "prompt_tokens_details": {"cached_tokens": 500},
            },
            model="openai/gpt-4o",
        )

        self.assertEqual(usage["prompt_tokens"], 2000)
        self.assertEqual(usage["normalized_cache_read_tokens"], 500)
        self.assertEqual(usage["provider_reported_cached_tokens"], 500)
        self.assertEqual(usage["provider_min_cache_tokens"], 1024)
        self.assertEqual(usage["cache_eligibility"], "eligible")
        self.assertEqual(usage["cache_observation"], "partial_hit")
        self.assertEqual(usage["normalized_cache_hit_ratio"], 0.25)

    def test_openai_below_threshold_does_not_fake_zero_hit(self):
        usage = normalize_litellm_usage(
            {
                "prompt_tokens": 100,
                "completion_tokens": 10,
                "total_tokens": 110,
                "prompt_tokens_details": {"cached_tokens": 0},
            },
            model="openai/gpt-4o",
        )

        self.assertEqual(usage["cache_eligibility"], "below_threshold")
        self.assertIsNone(usage["normalized_cache_eligible_input_tokens"])
        self.assertEqual(usage["cache_observation"], "unknown")

    def test_glm_cached_tokens_use_openai_shape(self):
        usage = normalize_litellm_usage(
            {
                "prompt_tokens": 1200,
                "completion_tokens": 80,
                "total_tokens": 1280,
                "prompt_tokens_details": {"cached_tokens": 1200},
            },
            model="zhipu/glm-4.5",
        )

        self.assertEqual(usage["normalized_cache_read_tokens"], 1200)
        self.assertEqual(usage["cache_capability"], "supported")
        self.assertEqual(usage["cache_observation"], "full_hit")

    def test_anthropic_cache_read_write_and_total_input(self):
        usage = normalize_litellm_usage(
            {
                "input_tokens": 100,
                "output_tokens": 30,
                "cache_read_input_tokens": 10,
                "cache_creation_input_tokens": 20,
            },
            model="anthropic/claude-3-5-sonnet",
        )

        self.assertEqual(usage["prompt_tokens"], 130)
        self.assertEqual(usage["completion_tokens"], 30)
        self.assertEqual(usage["total_tokens"], 160)
        self.assertEqual(usage["normalized_cache_read_tokens"], 10)
        self.assertEqual(usage["normalized_cache_write_tokens"], 20)
        self.assertEqual(usage["normalized_uncached_input_tokens"], 100)
        self.assertEqual(usage["cache_observation"], "read_and_write")

    def test_gemini_usage_metadata(self):
        payload = {
            "usage_metadata": {
                "prompt_token_count": 1000,
                "candidates_token_count": 50,
                "total_token_count": 1050,
                "cached_content_token_count": 32,
            }
        }

        usage = normalize_litellm_usage(
            extract_usage_payload(payload),
            model="gemini/gemini-2.5-flash",
        )

        self.assertEqual(usage["prompt_tokens"], 1000)
        self.assertEqual(usage["completion_tokens"], 50)
        self.assertEqual(usage["total_tokens"], 1050)
        self.assertEqual(usage["normalized_cache_read_tokens"], 32)
        self.assertEqual(usage["cache_observation"], "partial_hit")

    def test_deepseek_hit_miss_tokens(self):
        usage = normalize_litellm_usage(
            {
                "completion_tokens": 10,
                "prompt_cache_hit_tokens": 40,
                "prompt_cache_miss_tokens": 60,
            },
            model="deepseek/deepseek-chat",
        )

        self.assertEqual(usage["prompt_tokens"], 100)
        self.assertEqual(usage["total_tokens"], 110)
        self.assertEqual(usage["normalized_cache_read_tokens"], 40)
        self.assertEqual(usage["normalized_cache_miss_tokens"], 60)
        self.assertEqual(usage["normalized_uncached_input_tokens"], 60)

    def test_stepfun_top_level_cached_tokens(self):
        usage = normalize_litellm_usage(
            {
                "prompt_tokens": 900,
                "completion_tokens": 100,
                "total_tokens": 1000,
                "cached_tokens": 300,
            },
            model="stepfun/step-2",
        )

        self.assertEqual(usage["normalized_cache_read_tokens"], 300)
        self.assertEqual(usage["cache_capability"], "supported")

    def test_unknown_provider_keeps_cache_unknown(self):
        usage = normalize_litellm_usage(
            {
                "prompt_tokens": 1000,
                "completion_tokens": 100,
                "total_tokens": 1100,
            },
            model="gateway/custom-model",
            provider="gateway",
        )

        self.assertEqual(usage["prompt_tokens"], 1000)
        self.assertIsNone(usage["normalized_cache_read_tokens"])
        self.assertIsNone(usage["normalized_cache_miss_tokens"])
        self.assertEqual(usage["cache_capability"], "unknown")
        self.assertEqual(usage["cache_observation"], "unknown")

    def test_raw_usage_is_sanitized_and_size_limited(self):
        usage = normalize_litellm_usage(
            {
                "prompt_tokens": 1,
                "completion_tokens": 2,
                "total_tokens": 3,
                "api_key": "sk-secret",
                "headers": {"authorization": "Bearer secret"},
                "raw_prompt": "do not persist this prompt",
                "nested": {"raw_user_input": "do not persist this user input"},
                "large": "x" * 5000,
            },
            model="gateway/custom-model",
        )

        raw = usage["provider_usage_json"]
        self.assertIsNotNone(raw)
        self.assertLessEqual(len(raw.encode("utf-8")), 4096)
        self.assertNotIn("sk-secret", raw)
        self.assertNotIn("authorization", raw)
        self.assertNotIn("do not persist this prompt", raw)
        self.assertNotIn("do not persist this user input", raw)
        parsed = json.loads(raw)
        self.assertTrue(parsed["_truncated"])

    def test_raw_usage_sanitizes_forbidden_key_variants(self):
        usage = normalize_litellm_usage(
            {
                "prompt_tokens": 1,
                "apiKey": "sk-secret",
                "x-api-key": "sk-secondary",
                "requestBody": "raw request payload",
                "responseText": "raw model response",
                "webhook_url": "https://example.test/hook",
                "nested": {
                    "rawUserInput": "private user input",
                    "safe_count": 2,
                },
            },
            model="gateway/custom-model",
        )

        raw = usage["provider_usage_json"]
        self.assertIsNotNone(raw)
        self.assertNotIn("sk-secret", raw)
        self.assertNotIn("sk-secondary", raw)
        self.assertNotIn("raw request payload", raw)
        self.assertNotIn("raw model response", raw)
        self.assertNotIn("example.test/hook", raw)
        self.assertNotIn("private user input", raw)
        parsed = json.loads(raw)
        self.assertEqual(parsed["prompt_tokens"], 1)
        self.assertEqual(parsed["nested"]["safe_count"], 2)


class TestLLMUsageHMAC(unittest.TestCase):
    def tearDown(self):
        _reset_usage_hmac_secret_cache_for_tests()

    def test_hmac_sha256_is_used_without_raw_prompt_storage(self):
        messages = [
            {"role": "system", "content": "system policy"},
            {"role": "user", "content": "user prompt"},
        ]
        with patch.dict(
            os.environ,
            {
                "LLM_USAGE_HMAC_SECRET": "test-secret",
                "LLM_USAGE_HMAC_KEY_VERSION": "test-v1",
            },
            clear=False,
        ):
            _reset_usage_hmac_secret_cache_for_tests()
            fields = build_message_hmacs(messages, hash_scope="local_debug")

        expected_payload = json.dumps(
            messages,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        expected = py_hmac.new(
            b"test-secret",
            expected_payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        self.assertEqual(fields["messages_hmac"], expected)
        self.assertEqual(len(fields["messages_hmac"]), 64)
        self.assertEqual(fields["hmac_key_version"], "test-v1")
        self.assertEqual(fields["hmac_domain"], "prompt_message")
        self.assertEqual(fields["hash_scope"], "local_debug")
        self.assertNotIn("user prompt", json.dumps(fields))

    def test_missing_env_uses_generated_local_secret_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "stock_analysis.db"
            secret_path = Path(tmpdir) / ".llm_usage_hmac_secret"
            with patch.dict(os.environ, {"DATABASE_PATH": str(db_path)}, clear=True):
                _reset_usage_hmac_secret_cache_for_tests()
                fields = build_message_hmacs([{"role": "user", "content": "hello"}])

            self.assertTrue(secret_path.exists())
            self.assertEqual(secret_path.stat().st_size, 32)
            self.assertEqual(len(fields["messages_hmac"]), 64)
            self.assertEqual(fields["hmac_key_version"], "local-v1")

    def test_key_version_is_part_of_hash_comparability_tuple(self):
        messages = [{"role": "user", "content": "same message"}]
        with patch.dict(
            os.environ,
            {
                "LLM_USAGE_HMAC_SECRET": "same-secret",
                "LLM_USAGE_HMAC_KEY_VERSION": "v1",
            },
            clear=False,
        ):
            first = build_message_hmacs(messages)
        with patch.dict(
            os.environ,
            {
                "LLM_USAGE_HMAC_SECRET": "same-secret",
                "LLM_USAGE_HMAC_KEY_VERSION": "v2",
            },
            clear=False,
        ):
            second = build_message_hmacs(messages)

        self.assertEqual(first["messages_hmac"], second["messages_hmac"])
        self.assertNotEqual(
            (first["hmac_key_version"], first["messages_hmac"]),
            (second["hmac_key_version"], second["messages_hmac"]),
        )


class TestGetLLMUsageSummary(unittest.TestCase):
    def setUp(self):
        self.db = _fresh_db()
        now = datetime.now()
        yesterday = now - timedelta(days=1)

        # 3 analysis calls today
        for _ in range(3):
            row = LLMUsage(
                call_type="analysis",
                model="gemini/gemini-2.5-flash",
                prompt_tokens=100,
                completion_tokens=200,
                total_tokens=300,
                called_at=now,
            )
            with self.db.session_scope() as session:
                session.add(row)

        # 2 agent calls today
        for _ in range(2):
            row = LLMUsage(
                call_type="agent",
                model="openai/gpt-4o",
                prompt_tokens=50,
                completion_tokens=100,
                total_tokens=150,
                called_at=now,
            )
            with self.db.session_scope() as session:
                session.add(row)

        # 1 old call that should be excluded
        old_row = LLMUsage(
            call_type="analysis",
            model="gemini/gemini-2.5-flash",
            prompt_tokens=999,
            completion_tokens=999,
            total_tokens=999,
            called_at=yesterday,
        )
        with self.db.session_scope() as session:
            session.add(old_row)

    def tearDown(self):
        DatabaseManager.reset_instance()

    def _today_range(self):
        now = datetime.now()
        return now.replace(hour=0, minute=0, second=0, microsecond=0), now

    def test_total_calls_and_tokens(self):
        from_dt, to_dt = self._today_range()
        result = self.db.get_llm_usage_summary(from_dt, to_dt)
        self.assertEqual(result["total_calls"], 5)
        # 3*300 + 2*150 = 900 + 300 = 1200
        self.assertEqual(result["total_tokens"], 1200)

    def test_by_call_type(self):
        from_dt, to_dt = self._today_range()
        result = self.db.get_llm_usage_summary(from_dt, to_dt)
        by_type = {r["call_type"]: r for r in result["by_call_type"]}
        self.assertIn("analysis", by_type)
        self.assertIn("agent", by_type)
        self.assertEqual(by_type["analysis"]["calls"], 3)
        self.assertEqual(by_type["analysis"]["total_tokens"], 900)
        self.assertEqual(by_type["agent"]["calls"], 2)
        self.assertEqual(by_type["agent"]["total_tokens"], 300)

    def test_by_model(self):
        from_dt, to_dt = self._today_range()
        result = self.db.get_llm_usage_summary(from_dt, to_dt)
        by_model = {r["model"]: r for r in result["by_model"]}
        self.assertEqual(by_model["gemini/gemini-2.5-flash"]["calls"], 3)
        self.assertEqual(by_model["openai/gpt-4o"]["calls"], 2)

    def test_empty_range_returns_zeros(self):
        future = datetime(2099, 1, 1)
        result = self.db.get_llm_usage_summary(future, future)
        self.assertEqual(result["total_calls"], 0)
        self.assertEqual(result["total_tokens"], 0)
        self.assertEqual(result["by_call_type"], [])
        self.assertEqual(result["by_model"], [])


class TestPersistUsageHelper(unittest.TestCase):
    """Test that _persist_usage swallows exceptions and writes correctly."""

    def setUp(self):
        self.db = _fresh_db()

    def tearDown(self):
        DatabaseManager.reset_instance()

    def test_persist_usage_writes_row(self):
        persist_llm_usage(
            {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            "gemini/gemini-2.5-flash",
            call_type="analysis",
            stock_code="000001",
        )
        with self.db.session_scope() as session:
            rows = session.query(LLMUsage).all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].total_tokens, 30)

    def test_persist_usage_handles_empty_usage(self):
        # Should not raise even with an empty dict
        persist_llm_usage({}, "unknown", call_type="agent")
        with self.db.session_scope() as session:
            rows = session.query(LLMUsage).all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].total_tokens, 0)
            self.assertEqual(rows[0].cache_capability, "unknown")
            self.assertEqual(rows[0].cache_eligibility, "unknown")
            self.assertEqual(rows[0].cache_observation, "no_usage")

    def test_persist_usage_writes_new_telemetry_fields(self):
        usage = normalize_litellm_usage(
            {
                "prompt_tokens": 2000,
                "completion_tokens": 100,
                "total_tokens": 2100,
                "prompt_tokens_details": {"cached_tokens": 500},
            },
            model="openai/gpt-4o",
        )
        with patch.dict(
            os.environ,
            {
                "LLM_USAGE_HMAC_SECRET": "persist-secret",
                "LLM_USAGE_HMAC_KEY_VERSION": "persist-v1",
            },
            clear=False,
        ):
            usage = attach_message_hmacs(
                usage,
                [
                    {"role": "system", "content": "system prompt"},
                    {"role": "user", "content": "user prompt"},
                ],
            )

        persist_llm_usage(
            usage,
            "openai/gpt-4o",
            call_type="analysis",
            stock_code="000001",
        )

        with self.db.session_scope() as session:
            row = session.query(LLMUsage).one()
            self.assertEqual(row.prompt_tokens, 2000)
            self.assertEqual(row.normalized_prompt_tokens, 2000)
            self.assertEqual(row.normalized_cache_read_tokens, 500)
            self.assertEqual(row.cache_capability, "supported")
            self.assertEqual(row.cache_eligibility, "eligible")
            self.assertEqual(row.cache_observation, "partial_hit")
            self.assertEqual(row.hmac_key_version, "persist-v1")
            self.assertEqual(len(row.messages_hmac), 64)
            self.assertNotIn("system prompt", row.provider_usage_json)
            self.assertNotIn("user prompt", row.provider_usage_json)

    def test_persist_usage_never_raises(self):
        # Pass a deliberately bad db state by resetting the singleton
        DatabaseManager.reset_instance()
        # Should silently swallow the error, not raise
        try:
            persist_llm_usage({"total_tokens": 5}, "m", call_type="analysis")
        except Exception as exc:
            self.fail(f"persist_llm_usage raised unexpectedly: {exc}")


class TestLLMUsageMigration(unittest.TestCase):
    def tearDown(self):
        DatabaseManager.reset_instance()

    def test_existing_sqlite_table_gets_missing_columns_idempotently(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "legacy.sqlite"
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE llm_usage (
                        id INTEGER PRIMARY KEY,
                        call_type VARCHAR(64) NOT NULL,
                        model VARCHAR(128) NOT NULL,
                        stock_code VARCHAR(32),
                        prompt_tokens INTEGER NOT NULL DEFAULT 0,
                        completion_tokens INTEGER NOT NULL DEFAULT 0,
                        total_tokens INTEGER NOT NULL DEFAULT 0,
                        called_at DATETIME
                    )
                    """
                )
                conn.commit()

            DatabaseManager.reset_instance()
            db = DatabaseManager(db_url=f"sqlite:///{db_path}")
            db._ensure_llm_usage_telemetry_columns()

            with sqlite3.connect(db_path) as conn:
                columns = {
                    row[1]
                    for row in conn.execute("PRAGMA table_info(llm_usage)").fetchall()
                }

            self.assertIn("provider_usage_json", columns)
            self.assertIn("normalized_cache_read_tokens", columns)
            self.assertIn("messages_hmac", columns)


if __name__ == "__main__":
    unittest.main()
