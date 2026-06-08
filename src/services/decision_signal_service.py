# -*- coding: utf-8 -*-
"""Service layer for persisted DecisionSignal assets."""

from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any, Dict, List, Optional, get_args

from data_provider.base import canonical_stock_code, normalize_stock_code
from src.core.trading_calendar import MarketPhase
from src.repositories.decision_signal_repo import DecisionSignalRepository
from src.repositories.portfolio_repo import PortfolioRepository
from src.report_language import normalize_report_language
from src.schemas.decision_action import DecisionAction, localize_action_label
from src.services.portfolio_service import VALID_MARKETS
from src.storage import DatabaseManager, DecisionSignalRecord
from src.utils.sanitize import sanitize_decision_signal_payload, sanitize_decision_signal_text


SOURCE_TYPES = frozenset({"analysis", "agent", "alert", "market_review", "manual"})
SIGNAL_STATUSES = frozenset({"active", "expired", "invalidated", "closed", "archived"})
PLAN_QUALITIES = frozenset({"complete", "partial", "minimal", "unknown"})
HORIZONS = frozenset({"intraday", "1d", "3d", "5d", "10d", "swing", "long"})
MARKET_PHASES = frozenset(phase.value for phase in MarketPhase)
DECISION_ACTIONS = frozenset(get_args(DecisionAction))


class DecisionSignalNotFoundError(ValueError):
    """Raised when a requested decision signal does not exist."""


class DecisionSignalService:
    """Business logic for DecisionSignal storage, querying, and serialization."""

    def __init__(
        self,
        repo: Optional[DecisionSignalRepository] = None,
        portfolio_repo: Optional[PortfolioRepository] = None,
        db_manager: Optional[DatabaseManager] = None,
    ):
        self.repo = repo or DecisionSignalRepository(db_manager)
        self.portfolio_repo = portfolio_repo or PortfolioRepository(db_manager)

    def create_signal(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        fields = self._normalize_payload(payload)
        row, created = self.repo.create_if_absent(fields)
        return {"item": self._serialize(row), "created": created}

    def get_signal(self, signal_id: int) -> Dict[str, Any]:
        row = self.repo.get(signal_id)
        if row is None:
            raise DecisionSignalNotFoundError(f"Decision signal not found: {signal_id}")
        return self._serialize(row)

    def list_signals(
        self,
        *,
        stock_code: Optional[str] = None,
        market: Optional[str] = None,
        action: Optional[str] = None,
        market_phase: Optional[str] = None,
        source_type: Optional[str] = None,
        trigger_source: Optional[str] = None,
        status: Optional[str] = None,
        created_from: Optional[Any] = None,
        created_to: Optional[Any] = None,
        expires_from: Optional[Any] = None,
        expires_to: Optional[Any] = None,
        holding_only: bool = False,
        account_id: Optional[int] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        safe_page = max(1, int(page))
        safe_page_size = max(1, min(int(page_size), 100))
        market_norm = self._normalize_optional_market(market)
        action_norm = self._normalize_optional_action(action)
        market_phase_norm = self._normalize_optional_enum(market_phase, MARKET_PHASES, "market_phase")
        source_type_norm = self._normalize_optional_enum(source_type, SOURCE_TYPES, "source_type")
        status_norm = self._normalize_optional_enum(status, SIGNAL_STATUSES, "status")
        trigger_source_norm = self._normalize_optional_trigger_source(trigger_source)
        stock_codes = self._stock_filter_codes(stock_code)

        if holding_only:
            held_codes = self._cached_holding_codes(account_id=account_id)
            if stock_codes:
                stock_codes = [code for code in stock_codes if code in held_codes]
            else:
                stock_codes = sorted(held_codes)
            if not stock_codes:
                return {"items": [], "total": 0, "page": safe_page, "page_size": safe_page_size}

        rows, total = self.repo.list(
            stock_codes=stock_codes,
            market=market_norm,
            action=action_norm,
            market_phase=market_phase_norm,
            source_type=source_type_norm,
            trigger_source=trigger_source_norm,
            status=status_norm,
            created_from=self._parse_datetime(created_from),
            created_to=self._parse_datetime(created_to),
            expires_from=self._parse_datetime(expires_from),
            expires_to=self._parse_datetime(expires_to),
            page=safe_page,
            page_size=safe_page_size,
        )
        return {
            "items": [self._serialize(row) for row in rows],
            "total": total,
            "page": safe_page,
            "page_size": safe_page_size,
        }

    def get_latest_active(
        self,
        *,
        stock_code: str,
        market: Optional[str] = None,
        limit: int = 1,
    ) -> Dict[str, Any]:
        rows = self.repo.get_latest_active(
            stock_codes=self._stock_filter_codes(stock_code) or [self._normalize_stock_code(stock_code)],
            market=self._normalize_optional_market(market),
            limit=limit,
        )
        return {
            "items": [self._serialize(row) for row in rows],
            "total": len(rows),
            "page": 1,
            "page_size": max(1, min(int(limit), 100)),
        }

    def update_status(
        self,
        signal_id: int,
        *,
        status: str,
        metadata: Optional[Any] = None,
        replace_metadata: bool = False,
    ) -> Dict[str, Any]:
        status_norm = self._normalize_enum(status, SIGNAL_STATUSES, "status")
        metadata_json = self._json_dumps(metadata) if replace_metadata else None
        row = self.repo.update_status(
            signal_id,
            status=status_norm,
            metadata_json=metadata_json,
            replace_metadata=replace_metadata,
        )
        if row is None:
            raise DecisionSignalNotFoundError(f"Decision signal not found: {signal_id}")
        return self._serialize(row)

    def _normalize_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        stock_code = self._normalize_stock_code(payload.get("stock_code"))
        market = self._normalize_market(payload.get("market"))
        action = self._normalize_action(payload.get("action"))
        report_language = normalize_report_language(payload.get("report_language"))
        action_label = self._optional_text(payload.get("action_label"), "action_label", max_length=32)
        if not action_label:
            action_label = localize_action_label(action, report_language)

        confidence = self._optional_float(payload.get("confidence"), "confidence")
        if confidence is not None and not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
        score = self._optional_int(payload.get("score"), "score")
        if score is not None and not 0 <= score <= 100:
            raise ValueError("score must be between 0 and 100")

        fields: Dict[str, Any] = {
            "stock_code": stock_code,
            "stock_name": self._optional_text(payload.get("stock_name"), "stock_name", max_length=64),
            "market": market,
            "source_type": self._normalize_enum(payload.get("source_type"), SOURCE_TYPES, "source_type"),
            "source_agent": self._optional_text(payload.get("source_agent"), "source_agent", max_length=64),
            "source_report_id": self._optional_int(payload.get("source_report_id"), "source_report_id"),
            "trace_id": self._optional_text(payload.get("trace_id"), "trace_id", max_length=64),
            "market_phase": self._normalize_optional_enum(payload.get("market_phase"), MARKET_PHASES, "market_phase"),
            "trigger_source": self._normalize_trigger_source(payload.get("trigger_source")),
            "action": action,
            "action_label": action_label,
            "confidence": confidence,
            "score": score,
            "horizon": self._normalize_optional_enum(payload.get("horizon"), HORIZONS, "horizon"),
            "entry_low": self._optional_price_float(payload.get("entry_low"), "entry_low"),
            "entry_high": self._optional_price_float(payload.get("entry_high"), "entry_high"),
            "stop_loss": self._optional_price_float(payload.get("stop_loss"), "stop_loss"),
            "target_price": self._optional_price_float(payload.get("target_price"), "target_price"),
            "invalidation": self._optional_signal_text(payload.get("invalidation")),
            "watch_conditions": self._optional_signal_text(payload.get("watch_conditions")),
            "reason": self._optional_signal_text(payload.get("reason")),
            "risk_summary": self._optional_signal_text(payload.get("risk_summary")),
            "catalyst_summary": self._optional_signal_text(payload.get("catalyst_summary")),
            "evidence_json": self._json_dumps(payload.get("evidence")),
            "data_quality_summary_json": self._json_dumps(payload.get("data_quality_summary")),
            "status": self._normalize_optional_enum(payload.get("status"), SIGNAL_STATUSES, "status") or "active",
            "expires_at": self._parse_datetime(payload.get("expires_at")),
            "metadata_json": self._json_dumps(payload.get("metadata")),
        }
        self._validate_entry_range(fields)
        fields["plan_quality"] = self._normalize_plan_quality(
            payload.get("plan_quality"),
            fields=fields,
        )
        return fields

    def _normalize_plan_quality(self, value: Any, *, fields: Dict[str, Any]) -> str:
        if value is not None:
            return self._normalize_enum(value, PLAN_QUALITIES, "plan_quality")
        has_action_or_reason = bool(fields.get("action") or fields.get("reason"))
        if not has_action_or_reason:
            return "unknown"
        slots = 0
        if fields.get("entry_low") is not None or fields.get("entry_high") is not None:
            slots += 1
        for key in ("stop_loss", "target_price", "invalidation", "watch_conditions"):
            if fields.get(key) not in (None, ""):
                slots += 1
        if slots >= 4:
            return "complete"
        if slots >= 2:
            return "partial"
        return "minimal"

    def _cached_holding_codes(self, *, account_id: Optional[int]) -> set[str]:
        symbols = self.portfolio_repo.list_cached_position_symbols(account_id=account_id)
        return {
            self._normalize_stock_code(symbol)
            for symbol in symbols
            if str(symbol or "").strip()
        }

    @classmethod
    def _stock_filter_codes(cls, stock_code: Optional[str]) -> Optional[List[str]]:
        if not stock_code:
            return None
        return [cls._normalize_stock_code(stock_code)]

    @staticmethod
    def _normalize_stock_code(value: Any) -> str:
        code = canonical_stock_code(normalize_stock_code(str(value or "").strip()))
        if not code:
            raise ValueError("stock_code is required")
        return code

    @staticmethod
    def _normalize_market(value: Any) -> str:
        market = str(value or "").strip().lower()
        if market not in VALID_MARKETS:
            raise ValueError("market must be one of cn, hk, us")
        return market

    @classmethod
    def _normalize_optional_market(cls, value: Any) -> Optional[str]:
        if value in (None, ""):
            return None
        return cls._normalize_market(value)

    @staticmethod
    def _normalize_action(value: Any) -> str:
        action = str(value or "").strip().lower()
        if not action or action not in DECISION_ACTIONS:
            raise ValueError("action must be one of buy/add/hold/reduce/sell/watch/avoid/alert")
        return action

    @classmethod
    def _normalize_optional_action(cls, value: Any) -> Optional[str]:
        if value in (None, ""):
            return None
        return cls._normalize_action(value)

    @staticmethod
    def _normalize_enum(value: Any, allowed: frozenset[str], field_name: str) -> str:
        text = str(value or "").strip()
        if text not in allowed:
            allowed_text = ", ".join(sorted(allowed))
            raise ValueError(f"{field_name} must be one of {allowed_text}")
        return text

    @classmethod
    def _normalize_optional_enum(
        cls,
        value: Any,
        allowed: frozenset[str],
        field_name: str,
    ) -> Optional[str]:
        if value in (None, ""):
            return None
        return cls._normalize_enum(value, allowed, field_name)

    @staticmethod
    def _normalize_trigger_source(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("trigger_source is required")
        if len(text) > 64:
            raise ValueError("trigger_source must be at most 64 characters")
        return text

    @classmethod
    def _normalize_optional_trigger_source(cls, value: Any) -> Optional[str]:
        if value in (None, ""):
            return None
        return cls._normalize_trigger_source(value)

    @staticmethod
    def _optional_text(value: Any, field_name: str, *, max_length: int) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        if len(text) > max_length:
            raise ValueError(f"{field_name} must be at most {max_length} characters")
        return text

    @staticmethod
    def _optional_signal_text(value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, (dict, list)):
            return json.dumps(sanitize_decision_signal_payload(value), ensure_ascii=False, sort_keys=True)
        text = sanitize_decision_signal_text(value)
        return text or None

    @staticmethod
    def _optional_float(value: Any, field_name: str) -> Optional[float]:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name} must be a number") from exc

    @classmethod
    def _optional_price_float(cls, value: Any, field_name: str) -> Optional[float]:
        number = cls._optional_float(value, field_name)
        if number is None:
            return None
        if not math.isfinite(number) or number <= 0:
            raise ValueError(f"{field_name} must be a finite positive number")
        return number

    @staticmethod
    def _validate_entry_range(fields: Dict[str, Any]) -> None:
        entry_low = fields.get("entry_low")
        entry_high = fields.get("entry_high")
        if entry_low is not None and entry_high is not None and entry_low > entry_high:
            raise ValueError("entry_low must be less than or equal to entry_high")

    @staticmethod
    def _optional_int(value: Any, field_name: str) -> Optional[int]:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name} must be an integer") from exc

    @staticmethod
    def _parse_datetime(value: Any) -> Optional[datetime]:
        if value in (None, ""):
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                return datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(f"invalid datetime value: {value}") from exc
        raise ValueError(f"invalid datetime value: {value}")

    @staticmethod
    def _json_dumps(value: Any) -> Optional[str]:
        if value in (None, ""):
            return None
        sanitized = sanitize_decision_signal_payload(value)
        return json.dumps(sanitized, ensure_ascii=False, sort_keys=True, default=str)

    @staticmethod
    def _json_loads(value: Optional[str]) -> Any:
        if not value:
            return None
        try:
            return json.loads(value)
        except Exception:
            return None

    def _serialize(self, row: DecisionSignalRecord) -> Dict[str, Any]:
        return {
            "id": row.id,
            "stock_code": row.stock_code,
            "stock_name": row.stock_name,
            "market": row.market,
            "source_type": row.source_type,
            "source_agent": row.source_agent,
            "source_report_id": row.source_report_id,
            "trace_id": row.trace_id,
            "market_phase": row.market_phase,
            "trigger_source": row.trigger_source,
            "action": row.action,
            "action_label": row.action_label,
            "confidence": row.confidence,
            "score": row.score,
            "horizon": row.horizon,
            "entry_low": row.entry_low,
            "entry_high": row.entry_high,
            "stop_loss": row.stop_loss,
            "target_price": row.target_price,
            "invalidation": row.invalidation,
            "watch_conditions": row.watch_conditions,
            "reason": row.reason,
            "risk_summary": row.risk_summary,
            "catalyst_summary": row.catalyst_summary,
            "evidence": self._json_loads(row.evidence_json),
            "data_quality_summary": self._json_loads(row.data_quality_summary_json),
            "plan_quality": row.plan_quality,
            "status": row.status,
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "metadata": self._json_loads(row.metadata_json),
        }
