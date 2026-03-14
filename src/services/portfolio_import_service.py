# -*- coding: utf-8 -*-
"""Portfolio CSV import service for PR2 broker adapters and dedup commit."""

from __future__ import annotations

import hashlib
import io
import logging
from datetime import date
from typing import Any, Dict, List, Optional

import pandas as pd

from data_provider.base import canonical_stock_code
from src.repositories.portfolio_repo import PortfolioRepository
from src.services.portfolio_service import PortfolioConflictError, PortfolioService

logger = logging.getLogger(__name__)

SUPPORTED_BROKERS = {"huatai", "citic", "cmb", "cmbchina", "zhaoshang", "zhongxin"}
BROKER_COLUMN_HINTS = {
    "huatai": {
        "trade_date": ("成交日期", "成交时间", "发生日期", "日期"),
        "symbol": ("证券代码", "股票代码", "代码"),
        "side": ("买卖标志", "买卖方向", "操作"),
        "quantity": ("成交数量", "数量", "成交股数"),
        "price": ("成交均价", "成交价格", "价格", "成交价", "均价"),
        "trade_uid": ("成交编号", "成交序号", "流水号"),
    },
    "citic": {
        "trade_date": ("发生日期", "成交日期", "日期"),
        "symbol": ("证券代码", "股票代码", "代码"),
        "side": ("买卖方向", "买卖标志", "业务名称"),
        "quantity": ("成交数量", "数量", "成交股数"),
        "price": ("成交价格", "成交均价", "价格", "成交价"),
        "trade_uid": ("合同编号", "成交编号", "委托编号"),
    },
    "cmb": {
        "trade_date": ("日期", "成交日期", "发生日期"),
        "symbol": ("证券代码", "股票代码", "代码"),
        "side": ("交易方向", "买卖方向", "买卖标志"),
        "quantity": ("成交股数", "成交数量", "数量"),
        "price": ("成交价", "成交价格", "成交均价", "均价"),
        "trade_uid": ("流水号", "成交编号", "成交序号"),
    },
}


class PortfolioImportService:
    """Parse broker CSV and commit normalized trade records with dedup."""

    def __init__(
        self,
        *,
        portfolio_service: Optional[PortfolioService] = None,
        repo: Optional[PortfolioRepository] = None,
    ):
        self.portfolio_service = portfolio_service or PortfolioService()
        self.repo = repo or PortfolioRepository()

    def parse_trade_csv(
        self,
        *,
        broker: str,
        content: bytes,
    ) -> Dict[str, Any]:
        broker_norm = self._normalize_broker(broker)
        df = self._read_csv(content)

        records: List[Dict[str, Any]] = []
        skipped = 0
        errors: List[str] = []

        for idx, row in df.iterrows():
            normalized = self._normalize_trade_row(row=row, broker=broker_norm)
            if normalized is None:
                skipped += 1
                continue
            try:
                normalized["dedup_hash"] = self._build_dedup_hash(normalized)
                records.append(normalized)
            except Exception as exc:  # pragma: no cover - defensive path
                skipped += 1
                errors.append(f"row={idx + 1}: {exc}")

        return {
            "broker": broker_norm,
            "record_count": len(records),
            "skipped_count": skipped,
            "error_count": len(errors),
            "records": records,
            "errors": errors[:20],
        }

    def commit_trade_records(
        self,
        *,
        account_id: int,
        broker: str,
        records: List[Dict[str, Any]],
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        self._normalize_broker(broker)

        inserted_count = 0
        duplicate_count = 0
        failed_count = 0
        errors: List[str] = []

        for i, record in enumerate(records):
            try:
                trade_uid = (record.get("trade_uid") or "").strip() or None
                dedup_hash = (record.get("dedup_hash") or "").strip()
                if not dedup_hash:
                    dedup_hash = self._build_dedup_hash(record)

                if trade_uid and self.repo.has_trade_uid(account_id, trade_uid):
                    duplicate_count += 1
                    continue
                dedup_hash_to_use: Optional[str] = dedup_hash or None
                if dedup_hash_to_use and self.repo.has_trade_dedup_hash(account_id, dedup_hash_to_use):
                    duplicate_count += 1
                    continue

                if dry_run:
                    inserted_count += 1
                    continue

                trade_date_value = record.get("trade_date")
                if isinstance(trade_date_value, date):
                    trade_date_obj = trade_date_value
                else:
                    trade_date_obj = date.fromisoformat(str(trade_date_value))

                self.portfolio_service.record_trade(
                    account_id=account_id,
                    symbol=str(record["symbol"]),
                    trade_date=trade_date_obj,
                    side=str(record["side"]),
                    quantity=float(record["quantity"]),
                    price=float(record["price"]),
                    fee=float(record.get("fee", 0.0) or 0.0),
                    tax=float(record.get("tax", 0.0) or 0.0),
                    market=record.get("market"),
                    currency=record.get("currency"),
                    trade_uid=trade_uid,
                    dedup_hash=dedup_hash_to_use,
                    note=(record.get("note") or "").strip() or f"csv_import:{broker}",
                )
                inserted_count += 1
            except PortfolioConflictError:
                duplicate_count += 1
            except Exception as exc:
                failed_count += 1
                errors.append(f"idx={i}: {exc}")

        return {
            "account_id": account_id,
            "record_count": len(records),
            "inserted_count": inserted_count,
            "duplicate_count": duplicate_count,
            "failed_count": failed_count,
            "dry_run": bool(dry_run),
            "errors": errors[:20],
        }

    @staticmethod
    def _normalize_broker(value: str) -> str:
        broker = (value or "").strip().lower()
        alias = {
            "zhaoshang": "cmb",
            "cmbchina": "cmb",
            "zhongxin": "citic",
        }
        broker = alias.get(broker, broker)
        if broker not in SUPPORTED_BROKERS:
            raise ValueError("broker must be one of: huatai, citic, cmb")
        if broker == "cmbchina":
            return "cmb"
        if broker == "zhaoshang":
            return "cmb"
        if broker == "zhongxin":
            return "citic"
        return broker

    @staticmethod
    def _read_csv(content: bytes) -> pd.DataFrame:
        for encoding in ("utf-8-sig", "gbk", "gb18030"):
            try:
                return pd.read_csv(io.BytesIO(content), encoding=encoding)
            except UnicodeDecodeError:
                continue
        return pd.read_csv(io.BytesIO(content))

    def _normalize_trade_row(self, *, row: Any, broker: str) -> Optional[Dict[str, Any]]:
        broker_hints = BROKER_COLUMN_HINTS.get(broker, {})

        trade_date_raw = self._pick(
            row,
            *(broker_hints.get("trade_date") or ()),
            "成交日期",
            "发生日期",
            "日期",
            "成交时间",
        )
        trade_date_obj = self._parse_date(trade_date_raw)
        if trade_date_obj is None:
            return None

        symbol_raw = self._pick(
            row,
            *(broker_hints.get("symbol") or ()),
            "证券代码",
            "股票代码",
            "代码",
        )
        symbol = canonical_stock_code(str(symbol_raw or "").strip())
        if not symbol:
            return None

        side_raw = self._pick(
            row,
            *(broker_hints.get("side") or ()),
            "买卖标志",
            "买卖方向",
            "交易方向",
            "业务名称",
            "操作",
        )
        side = self._normalize_side(side_raw)
        if side is None:
            return None

        quantity = self._parse_float(
            self._pick(row, *(broker_hints.get("quantity") or ()), "成交数量", "数量", "成交股数")
        )
        price = self._parse_float(
            self._pick(row, *(broker_hints.get("price") or ()), "成交均价", "成交价格", "价格", "成交价", "均价")
        )
        if quantity is None or quantity <= 0 or price is None or price <= 0:
            return None

        fee = 0.0
        for col in ("手续费", "佣金", "交易费", "规费", "过户费"):
            value = self._parse_float(self._pick(row, col))
            if value is not None:
                fee += value

        tax = 0.0
        for col in ("印花税", "税费", "其他税费"):
            value = self._parse_float(self._pick(row, col))
            if value is not None:
                tax += value

        trade_uid = self._pick(
            row,
            *(broker_hints.get("trade_uid") or ()),
            "成交编号",
            "成交序号",
            "合同编号",
            "委托编号",
            "流水号",
        )
        currency = self._pick(row, "币种", "货币")

        return {
            "trade_date": trade_date_obj,
            "symbol": symbol,
            "side": side,
            "quantity": float(quantity),
            "price": float(price),
            "fee": float(fee),
            "tax": float(tax),
            "trade_uid": (str(trade_uid).strip() if trade_uid is not None else None) or None,
            "currency": (str(currency).strip().upper() if currency is not None else None) or None,
        }

    @staticmethod
    def _pick(row: Any, *candidates: str) -> Any:
        for name in candidates:
            if name in row.index:
                value = row.get(name)
                if value is not None and str(value).strip() != "" and str(value).strip().lower() != "nan":
                    return value
        return None

    @staticmethod
    def _parse_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        text = str(value).strip().replace(",", "")
        if not text or text.lower() == "nan":
            return None
        try:
            return float(text)
        except ValueError:
            return None

    @staticmethod
    def _parse_date(value: Any) -> Optional[date]:
        if value is None:
            return None
        text = str(value).strip()
        if not text or text.lower() == "nan":
            return None
        parsed = pd.to_datetime(text, errors="coerce")
        if pd.isna(parsed):
            return None
        return parsed.date()

    @staticmethod
    def _normalize_side(value: Any) -> Optional[str]:
        text = str(value or "").strip().lower()
        if not text:
            return None
        compact = text.replace(" ", "")
        buy_exact = {"buy", "b", "买", "买入", "证券买入", "普通买入"}
        sell_exact = {"sell", "s", "卖", "卖出", "证券卖出", "普通卖出"}
        if compact in buy_exact:
            return "buy"
        if compact in sell_exact:
            return "sell"
        if "买入" in compact or compact.startswith("买"):
            return "buy"
        if "卖出" in compact or compact.startswith("卖"):
            return "sell"
        return None

    @staticmethod
    def _build_dedup_hash(record: Dict[str, Any]) -> str:
        payload = "|".join(
            [
                str(record.get("trade_date") or ""),
                str(record.get("symbol") or ""),
                str(record.get("side") or ""),
                f"{float(record.get('quantity', 0.0)):.8f}",
                f"{float(record.get('price', 0.0)):.8f}",
                f"{float(record.get('fee', 0.0)):.8f}",
                f"{float(record.get('tax', 0.0)):.8f}",
                str(record.get("currency") or ""),
            ]
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
