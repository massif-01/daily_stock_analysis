# -*- coding: utf-8 -*-
"""Decision signal repository for Issue #1390 P1."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, desc, func, select

from src.storage import DatabaseManager, DecisionSignalRecord


class DecisionSignalRepository:
    """DB access layer for persisted AI decision signals."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def create(self, fields: Dict[str, Any]) -> DecisionSignalRecord:
        with self.db.get_session() as session:
            row = DecisionSignalRecord(**fields)
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def create_if_absent(self, fields: Dict[str, Any]) -> Tuple[DecisionSignalRecord, bool]:
        with self.db.get_session() as session:
            existing = self._find_existing_in_session(session=session, fields=fields)
            if existing is not None:
                return existing, False
            row = DecisionSignalRecord(**fields)
            session.add(row)
            session.commit()
            session.refresh(row)
            return row, True

    def get(self, signal_id: int) -> Optional[DecisionSignalRecord]:
        self.expire_due_signals()
        with self.db.get_session() as session:
            return session.execute(
                select(DecisionSignalRecord).where(DecisionSignalRecord.id == signal_id).limit(1)
            ).scalar_one_or_none()

    def list(
        self,
        *,
        stock_codes: Optional[List[str]] = None,
        market: Optional[str] = None,
        action: Optional[str] = None,
        market_phase: Optional[str] = None,
        source_type: Optional[str] = None,
        trigger_source: Optional[str] = None,
        status: Optional[str] = None,
        created_from: Optional[datetime] = None,
        created_to: Optional[datetime] = None,
        expires_from: Optional[datetime] = None,
        expires_to: Optional[datetime] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[DecisionSignalRecord], int]:
        self.expire_due_signals()
        conditions = self._build_conditions(
            stock_codes=stock_codes,
            market=market,
            action=action,
            market_phase=market_phase,
            source_type=source_type,
            trigger_source=trigger_source,
            status=status,
            created_from=created_from,
            created_to=created_to,
            expires_from=expires_from,
            expires_to=expires_to,
        )
        where_clause = and_(*conditions) if conditions else True
        safe_page = max(1, int(page))
        safe_page_size = max(1, min(int(page_size), 100))
        offset = (safe_page - 1) * safe_page_size

        with self.db.get_session() as session:
            total = session.execute(
                select(func.count(DecisionSignalRecord.id))
                .select_from(DecisionSignalRecord)
                .where(where_clause)
            ).scalar() or 0
            rows = session.execute(
                select(DecisionSignalRecord)
                .where(where_clause)
                .order_by(desc(DecisionSignalRecord.created_at), desc(DecisionSignalRecord.id))
                .offset(offset)
                .limit(safe_page_size)
            ).scalars().all()
            return list(rows), int(total)

    def get_latest_active(
        self,
        *,
        stock_codes: List[str],
        market: Optional[str] = None,
        limit: int = 1,
    ) -> List[DecisionSignalRecord]:
        self.expire_due_signals()
        safe_limit = max(1, min(int(limit), 100))
        conditions = [
            DecisionSignalRecord.status == "active",
            DecisionSignalRecord.stock_code.in_(stock_codes),
        ]
        if market:
            conditions.append(DecisionSignalRecord.market == market)
        with self.db.get_session() as session:
            rows = session.execute(
                select(DecisionSignalRecord)
                .where(and_(*conditions))
                .order_by(desc(DecisionSignalRecord.created_at), desc(DecisionSignalRecord.id))
                .limit(safe_limit)
            ).scalars().all()
            return list(rows)

    def update_status(
        self,
        signal_id: int,
        *,
        status: str,
        metadata_json: Optional[str] = None,
        replace_metadata: bool = False,
    ) -> Optional[DecisionSignalRecord]:
        with self.db.get_session() as session:
            row = session.execute(
                select(DecisionSignalRecord).where(DecisionSignalRecord.id == signal_id).limit(1)
            ).scalar_one_or_none()
            if row is None:
                return None
            row.status = status
            if replace_metadata:
                row.metadata_json = metadata_json
            row.updated_at = datetime.now()
            session.commit()
            session.refresh(row)
            return row

    def expire_due_signals(self, now: Optional[datetime] = None) -> int:
        now_value = now or datetime.now()
        with self.db.get_session() as session:
            rows = session.execute(
                select(DecisionSignalRecord).where(
                    DecisionSignalRecord.status == "active",
                    DecisionSignalRecord.expires_at.is_not(None),
                    DecisionSignalRecord.expires_at <= now_value,
                )
            ).scalars().all()
            for row in rows:
                row.status = "expired"
                row.updated_at = now_value
            session.commit()
            return len(rows)

    @staticmethod
    def _find_existing_in_session(*, session: Any, fields: Dict[str, Any]) -> Optional[DecisionSignalRecord]:
        source_report_id = fields.get("source_report_id")
        trace_id = fields.get("trace_id")
        stock_code = fields.get("stock_code")
        action = fields.get("action")
        horizon = fields.get("horizon")
        market_phase = fields.get("market_phase")
        if source_report_id is not None:
            conditions = [
                DecisionSignalRecord.source_report_id == source_report_id,
                DecisionSignalRecord.stock_code == stock_code,
                DecisionSignalRecord.action == action,
                DecisionSignalRecord.horizon == horizon,
                DecisionSignalRecord.market_phase == market_phase,
            ]
        elif trace_id:
            conditions = [
                DecisionSignalRecord.trace_id == trace_id,
                DecisionSignalRecord.stock_code == stock_code,
                DecisionSignalRecord.action == action,
                DecisionSignalRecord.horizon == horizon,
                DecisionSignalRecord.market_phase == market_phase,
            ]
        else:
            return None
        return session.execute(
            select(DecisionSignalRecord)
            .where(and_(*conditions))
            .order_by(DecisionSignalRecord.id.asc())
            .limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def _build_conditions(
        *,
        stock_codes: Optional[List[str]],
        market: Optional[str],
        action: Optional[str],
        market_phase: Optional[str],
        source_type: Optional[str],
        trigger_source: Optional[str],
        status: Optional[str],
        created_from: Optional[datetime],
        created_to: Optional[datetime],
        expires_from: Optional[datetime],
        expires_to: Optional[datetime],
    ) -> List[Any]:
        conditions: List[Any] = []
        if stock_codes:
            conditions.append(DecisionSignalRecord.stock_code.in_(stock_codes))
        if market:
            conditions.append(DecisionSignalRecord.market == market)
        if action:
            conditions.append(DecisionSignalRecord.action == action)
        if market_phase:
            conditions.append(DecisionSignalRecord.market_phase == market_phase)
        if source_type:
            conditions.append(DecisionSignalRecord.source_type == source_type)
        if trigger_source:
            conditions.append(DecisionSignalRecord.trigger_source == trigger_source)
        if status:
            conditions.append(DecisionSignalRecord.status == status)
        if created_from:
            conditions.append(DecisionSignalRecord.created_at >= created_from)
        if created_to:
            conditions.append(DecisionSignalRecord.created_at <= created_to)
        if expires_from:
            conditions.append(DecisionSignalRecord.expires_at >= expires_from)
        if expires_to:
            conditions.append(DecisionSignalRecord.expires_at <= expires_to)
        return conditions
