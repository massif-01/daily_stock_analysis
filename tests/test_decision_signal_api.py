# -*- coding: utf-8 -*-
"""API tests for DecisionSignal P1."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    sys.modules["litellm"] = MagicMock()

import src.auth as auth
from api.app import create_app
from src.config import Config
from src.storage import DatabaseManager, DecisionSignalRecord, PortfolioAccount, PortfolioPosition


def _reset_auth_globals() -> None:
    auth._auth_enabled = None
    auth._session_secret = None
    auth._password_hash_salt = None
    auth._password_hash_stored = None
    auth._rate_limit = {}


@pytest.fixture()
def client_and_db(tmp_path):
    old_env_file = os.environ.get("ENV_FILE")
    old_database_path = os.environ.get("DATABASE_PATH")
    env_path = tmp_path / ".env"
    db_path = tmp_path / "decision_signal_api.db"
    static_dir = tmp_path / "empty-static"
    static_dir.mkdir()
    env_path.write_text(
        "\n".join(
            [
                "STOCK_LIST=600519",
                "GEMINI_API_KEY=test",
                "ADMIN_AUTH_ENABLED=false",
                f"DATABASE_PATH={db_path}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    os.environ["ENV_FILE"] = str(env_path)
    os.environ["DATABASE_PATH"] = str(db_path)
    _reset_auth_globals()
    Config.reset_instance()
    DatabaseManager.reset_instance()
    app = create_app(static_dir=Path(static_dir))
    client = TestClient(app)
    db = DatabaseManager.get_instance()
    try:
        yield client, db
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        _reset_auth_globals()
        if old_env_file is None:
            os.environ.pop("ENV_FILE", None)
        else:
            os.environ["ENV_FILE"] = old_env_file
        if old_database_path is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = old_database_path


def _payload(**overrides):
    payload = {
        "stock_code": "SH600519",
        "stock_name": "贵州茅台",
        "market": "cn",
        "source_type": "analysis",
        "source_agent": "api-test",
        "source_report_id": 3001,
        "trace_id": "trace-3001",
        "market_phase": "intraday",
        "trigger_source": "api",
        "action": "buy",
        "confidence": 0.75,
        "score": 80,
        "horizon": "3d",
        "entry_low": 1680,
        "stop_loss": 1600,
        "reason": "突破平台",
        "evidence": {"source": "unit-test"},
        "metadata": {"task_id": "task-3001", "alert_trigger_id": "alert-1"},
    }
    payload.update(overrides)
    return payload


def test_create_duplicate_list_detail_latest_and_status_update(client_and_db) -> None:
    client, _db = client_and_db

    created_resp = client.post("/api/v1/decision-signals", json=_payload())
    assert created_resp.status_code == 200, created_resp.text
    created = created_resp.json()
    assert created["created"] is True
    signal_id = created["item"]["id"]
    assert created["item"]["stock_code"] == "600519"
    assert created["item"]["plan_quality"] == "partial"

    duplicate_resp = client.post(
        "/api/v1/decision-signals",
        json=_payload(reason="重复报告里不同文案不应覆盖旧信号"),
    )
    assert duplicate_resp.status_code == 200, duplicate_resp.text
    duplicate = duplicate_resp.json()
    assert duplicate["created"] is False
    assert duplicate["item"]["id"] == signal_id
    assert duplicate["item"]["reason"] == "突破平台"

    list_resp = client.get(
        "/api/v1/decision-signals",
        params={
            "market": "cn",
            "stock_code": "600519.SH",
            "action": "buy",
            "market_phase": "intraday",
            "source_type": "analysis",
            "trigger_source": "api",
            "status": "active",
        },
    )
    assert list_resp.status_code == 200, list_resp.text
    listed = list_resp.json()
    assert listed["total"] == 1
    assert listed["items"][0]["id"] == signal_id

    detail_resp = client.get(f"/api/v1/decision-signals/{signal_id}")
    assert detail_resp.status_code == 200, detail_resp.text
    assert detail_resp.json()["id"] == signal_id

    latest_resp = client.get("/api/v1/decision-signals/latest/600519", params={"limit": 1})
    assert latest_resp.status_code == 200, latest_resp.text
    assert latest_resp.json()["items"][0]["id"] == signal_id

    patch_resp = client.patch(
        f"/api/v1/decision-signals/{signal_id}/status",
        json={"status": "closed", "metadata": {"closed_by": "api-test"}},
    )
    assert patch_resp.status_code == 200, patch_resp.text
    assert patch_resp.json()["status"] == "closed"
    assert patch_resp.json()["metadata"]["closed_by"] == "api-test"
    assert "task_id" not in patch_resp.json()["metadata"]

    clear_metadata_resp = client.patch(
        f"/api/v1/decision-signals/{signal_id}/status",
        json={"status": "archived", "metadata": None},
    )
    assert clear_metadata_resp.status_code == 200, clear_metadata_resp.text
    assert clear_metadata_resp.json()["status"] == "archived"
    assert clear_metadata_resp.json()["metadata"] is None

    status_only_resp = client.patch(
        f"/api/v1/decision-signals/{signal_id}/status",
        json={"status": "active"},
    )
    assert status_only_resp.status_code == 200, status_only_resp.text
    assert status_only_resp.json()["status"] == "active"
    assert status_only_resp.json()["metadata"] is None

    invalid_status_resp = client.patch(
        f"/api/v1/decision-signals/{signal_id}/status",
        json={"status": "bad_status"},
    )
    assert invalid_status_resp.status_code == 422
    assert invalid_status_resp.json()["error"] == "validation_error"

    missing_resp = client.get("/api/v1/decision-signals/999999")
    assert missing_resp.status_code == 404


def test_status_update_sanitizes_metadata_before_response_and_persistence(client_and_db) -> None:
    client, db = client_and_db

    created_resp = client.post(
        "/api/v1/decision-signals",
        json=_payload(source_report_id=3051, trace_id="trace-3051"),
    )
    assert created_resp.status_code == 200, created_resp.text
    signal_id = created_resp.json()["item"]["id"]

    patch_resp = client.patch(
        f"/api/v1/decision-signals/{signal_id}/status",
        json={
            "status": "closed",
            "metadata": {
                "source_url": "https://news.example.com/article?id=1",
                "webhook": "https://hooks.slack.com/services/T000/B000/abcdef",
                "feishu": "https://open.feishu.cn/open-apis/bot/v2/hook/abcdef",
                "userinfo": "https://user:pass@example.com/path",
                "fragment": "https://news.example.com/cb#access_token=abc",
                "note": "Bearer abcdef0123456789",
            },
        },
    )
    assert patch_resp.status_code == 200, patch_resp.text
    response_blob = str(patch_resp.json()["metadata"])
    assert "https://news.example.com/article?id=1" in response_blob
    assert "[REDACTED_URL]" in response_blob
    assert "hooks.slack.com" not in response_blob
    assert "open.feishu.cn" not in response_blob
    assert "user:pass" not in response_blob
    assert "access_token=abc" not in response_blob
    assert "abcdef0123456789" not in response_blob

    with db.session_scope() as session:
        row = session.query(DecisionSignalRecord).filter_by(id=signal_id).one()
        stored_blob = str(row.metadata_json)
    assert "https://news.example.com/article?id=1" in stored_blob
    assert "[REDACTED_URL]" in stored_blob
    assert "hooks.slack.com" not in stored_blob
    assert "open.feishu.cn" not in stored_blob
    assert "user:pass" not in stored_blob
    assert "access_token=abc" not in stored_blob
    assert "abcdef0123456789" not in stored_blob


def test_detail_endpoint_lazily_expires_active_signal(client_and_db) -> None:
    client, _db = client_and_db
    created_resp = client.post(
        "/api/v1/decision-signals",
        json=_payload(
            source_report_id=3101,
            trace_id="trace-3101",
            expires_at=(datetime.now() - timedelta(minutes=5)).isoformat(),
        ),
    )
    assert created_resp.status_code == 200, created_resp.text
    signal_id = created_resp.json()["item"]["id"]

    detail_resp = client.get(f"/api/v1/decision-signals/{signal_id}")
    assert detail_resp.status_code == 200, detail_resp.text
    assert detail_resp.json()["status"] == "expired"

    latest_resp = client.get("/api/v1/decision-signals/latest/600519")
    assert latest_resp.status_code == 200, latest_resp.text
    assert latest_resp.json()["total"] == 0


def test_holding_only_uses_cached_positions_and_stock_code_variants(client_and_db) -> None:
    client, db = client_and_db
    stock_resp = client.post(
        "/api/v1/decision-signals",
        json=_payload(source_report_id=3201, trace_id="trace-3201", stock_code="600519.SH"),
    )
    assert stock_resp.status_code == 200, stock_resp.text
    other_resp = client.post(
        "/api/v1/decision-signals",
        json=_payload(
            source_report_id=3202,
            trace_id="trace-3202",
            stock_code="AAPL",
            stock_name="Apple",
            market="us",
        ),
    )
    assert other_resp.status_code == 200, other_resp.text

    with db.session_scope() as session:
        account = PortfolioAccount(name="Test account", market="cn", base_currency="CNY")
        session.add(account)
        session.flush()
        account_id = account.id
        session.add(
            PortfolioPosition(
                account_id=account_id,
                cost_method="fifo",
                symbol="SH600519",
                market="cn",
                currency="CNY",
                quantity=100,
                avg_cost=1600,
                total_cost=160000,
            )
        )
        session.add(
            PortfolioPosition(
                account_id=account_id,
                cost_method="fifo",
                symbol="AAPL",
                market="us",
                currency="USD",
                quantity=0,
            )
        )
        session.add(
            PortfolioPosition(
                account_id=account_id,
                cost_method="avg",
                symbol="AAPL",
                market="us",
                currency="USD",
                quantity=5,
                avg_cost=180,
                total_cost=900,
            )
        )

    with patch(
        "src.services.portfolio_service.PortfolioService.get_portfolio_snapshot",
        side_effect=AssertionError("holding_only must not replay portfolio snapshots"),
    ):
        holding_resp = client.get(
            "/api/v1/decision-signals",
            params={"holding_only": "true", "account_id": account_id},
        )

    assert holding_resp.status_code == 200, holding_resp.text
    payload = holding_resp.json()
    assert payload["total"] == 2
    assert {item["stock_code"] for item in payload["items"]} == {"600519", "AAPL"}

    variant_resp = client.get("/api/v1/decision-signals", params={"stock_code": "SH600519"})
    assert variant_resp.status_code == 200, variant_resp.text
    assert variant_resp.json()["total"] == 1

    with db.session_scope() as session:
        empty_account = PortfolioAccount(name="Empty account", market="cn", base_currency="CNY")
        session.add(empty_account)
        session.flush()
        empty_account_id = empty_account.id

    empty_resp = client.get(
        "/api/v1/decision-signals",
        params={"holding_only": "true", "account_id": empty_account_id},
    )
    assert empty_resp.status_code == 200, empty_resp.text
    assert empty_resp.json()["total"] == 0
    assert empty_resp.json()["items"] == []


def test_query_validation_error_envelope(client_and_db) -> None:
    client, _db = client_and_db
    resp = client.get("/api/v1/decision-signals", params={"action": "panic"})
    assert resp.status_code == 400
    assert resp.json()["error"] == "validation_error"

    page_size_resp = client.get("/api/v1/decision-signals", params={"page_size": 0})
    assert page_size_resp.status_code == 422
    assert page_size_resp.json()["error"] == "validation_error"


def test_internal_errors_do_not_reflect_exception_details(client_and_db) -> None:
    client, _db = client_and_db

    with patch("api.v1.endpoints.decision_signals.DecisionSignalService") as service_cls:
        service_cls.return_value.list_signals.side_effect = RuntimeError(
            "secret-token /private/tmp/internal-path"
        )
        resp = client.get("/api/v1/decision-signals")

    assert resp.status_code == 500
    payload = resp.json()
    assert payload["error"] == "internal_error"
    assert payload["message"] == "List decision signals failed"
    assert "secret-token" not in str(payload)
    assert "internal-path" not in str(payload)


def test_create_schema_and_service_validation_errors(client_and_db) -> None:
    client, _db = client_and_db

    schema_invalid_cases = [
        {"entry_low": -1},
        {"entry_high": 0},
        {"stop_loss": "nan"},
        {"target_price": "inf"},
        {"trace_id": "x" * 65},
    ]
    for overrides in schema_invalid_cases:
        resp = client.post("/api/v1/decision-signals", json=_payload(**overrides))
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"] == "validation_error"

    range_resp = client.post(
        "/api/v1/decision-signals",
        json=_payload(source_report_id=3301, trace_id="trace-range", entry_low=1700, entry_high=1600),
    )
    assert range_resp.status_code == 400, range_resp.text
    assert range_resp.json()["error"] == "validation_error"


def test_dedup_distinguishes_horizon_and_market_phase(client_and_db) -> None:
    client, _db = client_and_db

    first_resp = client.post(
        "/api/v1/decision-signals",
        json=_payload(source_report_id=3401, trace_id="trace-3401", horizon="1d", market_phase="intraday"),
    )
    assert first_resp.status_code == 200, first_resp.text
    first = first_resp.json()
    assert first["created"] is True

    duplicate_resp = client.post(
        "/api/v1/decision-signals",
        json=_payload(source_report_id=3401, trace_id="trace-3401", horizon="1d", market_phase="intraday"),
    )
    assert duplicate_resp.status_code == 200, duplicate_resp.text
    duplicate = duplicate_resp.json()
    assert duplicate["created"] is False
    assert duplicate["item"]["id"] == first["item"]["id"]

    horizon_resp = client.post(
        "/api/v1/decision-signals",
        json=_payload(source_report_id=3401, trace_id="trace-3401", horizon="10d", market_phase="intraday"),
    )
    assert horizon_resp.status_code == 200, horizon_resp.text
    assert horizon_resp.json()["created"] is True
    assert horizon_resp.json()["item"]["id"] != first["item"]["id"]

    phase_resp = client.post(
        "/api/v1/decision-signals",
        json=_payload(source_report_id=3401, trace_id="trace-3401", horizon="1d", market_phase="premarket"),
    )
    assert phase_resp.status_code == 200, phase_resp.text
    assert phase_resp.json()["created"] is True
    assert phase_resp.json()["item"]["id"] != first["item"]["id"]

    list_resp = client.get(
        "/api/v1/decision-signals",
        params={"stock_code": "600519", "source_type": "analysis", "trigger_source": "api"},
    )
    assert list_resp.status_code == 200, list_resp.text
    assert list_resp.json()["total"] == 3

    latest_resp = client.get("/api/v1/decision-signals/latest/600519", params={"limit": 3})
    assert latest_resp.status_code == 200, latest_resp.text
    assert latest_resp.json()["total"] == 3
