# -*- coding: utf-8 -*-
"""Repository tests for DecisionSignal P1."""

from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest
from sqlalchemy import inspect

from src.config import Config
from src.repositories.decision_signal_repo import DecisionSignalRepository
from src.storage import Base, DatabaseManager, DecisionSignalRecord


@pytest.fixture()
def isolated_db(tmp_path):
    old_database_path = os.environ.get("DATABASE_PATH")
    db_path = tmp_path / "decision_signal_repo.db"
    os.environ["DATABASE_PATH"] = str(db_path)
    Config.reset_instance()
    DatabaseManager.reset_instance()
    db = DatabaseManager.get_instance()
    try:
        yield db
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        if old_database_path is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = old_database_path


def _fields(**overrides):
    fields = {
        "stock_code": "600519",
        "stock_name": "贵州茅台",
        "market": "cn",
        "source_type": "analysis",
        "source_agent": "test-agent",
        "source_report_id": 1001,
        "trace_id": "trace-1001",
        "market_phase": "intraday",
        "trigger_source": "api",
        "action": "buy",
        "action_label": "买入",
        "confidence": 0.8,
        "score": 88,
        "horizon": "3d",
        "entry_low": 1680.0,
        "entry_high": 1700.0,
        "stop_loss": 1600.0,
        "target_price": 1850.0,
        "invalidation": "跌破 1600",
        "watch_conditions": "量能继续放大",
        "reason": "趋势增强",
        "risk_summary": "波动加大",
        "catalyst_summary": "业绩披露",
        "evidence_json": '{"items":[]}',
        "data_quality_summary_json": '{"level":"good"}',
        "plan_quality": "complete",
        "status": "active",
        "metadata_json": '{"task_id":"task-1"}',
    }
    fields.update(overrides)
    return fields


def test_create_if_absent_deduplicates_report_and_trace_keys(isolated_db) -> None:
    repo = DecisionSignalRepository(isolated_db)

    row1, created1 = repo.create_if_absent(_fields())
    row2, created2 = repo.create_if_absent(_fields(reason="new reason"))
    assert created1 is True
    assert created2 is False
    assert row2.id == row1.id
    assert row2.reason == "趋势增强"

    different_horizon, horizon_created = repo.create_if_absent(_fields(horizon="10d", target_price=1900))
    assert horizon_created is True
    assert different_horizon.id != row1.id

    different_phase, phase_created = repo.create_if_absent(_fields(market_phase="premarket", target_price=1800))
    assert phase_created is True
    assert different_phase.id != row1.id

    trace_row1, trace_created1 = repo.create_if_absent(
        _fields(source_report_id=None, trace_id="trace-only", stock_code="000001")
    )
    trace_row2, trace_created2 = repo.create_if_absent(
        _fields(source_report_id=None, trace_id="trace-only", stock_code="000001", reason="ignored")
    )
    assert trace_created1 is True
    assert trace_created2 is False
    assert trace_row2.id == trace_row1.id

    trace_horizon_row, trace_horizon_created = repo.create_if_absent(
        _fields(
            source_report_id=None,
            trace_id="trace-only",
            stock_code="000001",
            horizon="10d",
        )
    )
    assert trace_horizon_created is True
    assert trace_horizon_row.id != trace_row1.id

    none_dim_row1, none_dim_created1 = repo.create_if_absent(
        _fields(source_report_id=1002, trace_id="trace-none-dim", horizon=None, market_phase=None)
    )
    none_dim_row2, none_dim_created2 = repo.create_if_absent(
        _fields(source_report_id=1002, trace_id="trace-none-dim", horizon=None, market_phase=None)
    )
    assert none_dim_created1 is True
    assert none_dim_created2 is False
    assert none_dim_row2.id == none_dim_row1.id

    no_key_row1, no_key_created1 = repo.create_if_absent(
        _fields(source_report_id=None, trace_id=None, stock_code="000002")
    )
    no_key_row2, no_key_created2 = repo.create_if_absent(
        _fields(source_report_id=None, trace_id=None, stock_code="000002")
    )
    assert no_key_created1 is True
    assert no_key_created2 is True
    assert no_key_row2.id != no_key_row1.id


def test_list_latest_status_update_and_lazy_expire(isolated_db) -> None:
    repo = DecisionSignalRepository(isolated_db)
    old_row = repo.create(_fields(source_report_id=2001, trace_id="trace-2001", action="watch"))
    new_row = repo.create(_fields(source_report_id=2002, trace_id="trace-2002", action="buy"))
    expired_row = repo.create(
        _fields(
            source_report_id=2003,
            trace_id="trace-2003",
            action="alert",
            expires_at=datetime.now() - timedelta(minutes=1),
        )
    )

    with isolated_db.session_scope() as session:
        session.query(DecisionSignalRecord).filter_by(id=old_row.id).update(
            {"created_at": datetime.now() - timedelta(days=1)}
        )

    rows, total = repo.list(stock_codes=["600519"], action="buy", page=1, page_size=10)
    assert total == 1
    assert rows[0].id == new_row.id

    latest = repo.get_latest_active(stock_codes=["600519"], limit=2)
    assert [row.id for row in latest] == [new_row.id, old_row.id]
    assert repo.get(expired_row.id).status == "expired"
    assert repo.expire_due_signals() == 0

    latest_after_expire = repo.get_latest_active(stock_codes=["600519"], limit=2)
    assert [row.id for row in latest_after_expire] == [new_row.id, old_row.id]

    updated = repo.update_status(
        new_row.id,
        status="closed",
        metadata_json='{"closed_by":"test"}',
        replace_metadata=True,
    )
    assert updated.status == "closed"
    assert updated.metadata_json == '{"closed_by":"test"}'
    assert repo.update_status(999999, status="closed") is None


def test_create_all_is_idempotent_and_indexes_exist(isolated_db) -> None:
    Base.metadata.create_all(isolated_db._engine)
    Base.metadata.create_all(isolated_db._engine)

    index_names = {
        item["name"]
        for item in inspect(isolated_db._engine).get_indexes("decision_signals")
    }
    assert "ix_decision_signal_stock_status_time" in index_names
    assert "ix_decision_signal_market_status_time" in index_names
    assert "ix_decision_signal_report_stock_action_horizon_phase" in index_names
    assert "ix_decision_signal_trace_stock_action_horizon_phase" in index_names
