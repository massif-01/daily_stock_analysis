# -*- coding: utf-8 -*-
"""Session stock-scope guard for agent tool calls."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from src.agent.llm_adapter import ToolCall
from src.agent.stock_text import (
    _is_denied_ticker_candidate,
    canonicalize_stock_code,
    extract_stock_codes,
)
from src.agent.tools.registry import ToolRegistry


_EXPLICIT_STOCK_SCOPE_INTENT = re.compile(
    r"换成|切换到?|改成|改为|分析|研究|看看|看一下|查一?下|查询|比较|对比|\bvs\b",
    re.IGNORECASE,
)
_COMPARE_CONNECTOR = re.compile(r"和|跟|与|\bvs\b", re.IGNORECASE)


@dataclass
class StockScope:
    """Structured current-stock contract for one agent loop."""

    active_stock_code: str
    active_stock_name: str = ""
    user_message: str = ""
    allowed_stock_codes: set[str] = field(default_factory=set)

    @classmethod
    def from_context(cls, context: Optional[Dict[str, Any]], user_message: str) -> Optional["StockScope"]:
        active_code = canonicalize_stock_code((context or {}).get("stock_code", ""))
        if not active_code:
            return None
        allowed = _extract_allowed_stock_codes(user_message or "", active_code)
        return cls(
            active_stock_code=active_code,
            active_stock_name=str((context or {}).get("stock_name") or "").strip(),
            user_message=user_message or "",
            allowed_stock_codes=allowed,
        )


@dataclass
class StockScopeDecision:
    """Guard decision for one tool call."""

    action: str
    reason: str
    original_stock_code: str = ""
    effective_stock_code: str = ""
    conflict_result: Optional[Dict[str, Any]] = None

    def log_fields(self) -> Dict[str, Any]:
        fields = {
            "stock_scope_action": self.action,
            "stock_scope_reason": self.reason,
        }
        if self.original_stock_code:
            fields["original_stock_code"] = self.original_stock_code
        if self.effective_stock_code:
            fields["effective_stock_code"] = self.effective_stock_code
        return fields


def guard_tool_calls(
    tool_calls: List[ToolCall],
    *,
    stock_scope: Optional[StockScope],
    tool_registry: ToolRegistry,
) -> Tuple[List[ToolCall], Dict[str, StockScopeDecision]]:
    """Return guarded tool calls plus per-call stock-scope decisions."""
    if not stock_scope:
        return tool_calls, {}

    guarded: List[ToolCall] = []
    decisions: Dict[str, StockScopeDecision] = {}
    for tool_call in tool_calls:
        effective_call, decision = guard_tool_call(
            tool_call,
            stock_scope=stock_scope,
            tool_registry=tool_registry,
        )
        guarded.append(effective_call)
        if decision:
            decisions[effective_call.id] = decision
    return guarded, decisions


def guard_tool_call(
    tool_call: ToolCall,
    *,
    stock_scope: StockScope,
    tool_registry: ToolRegistry,
) -> Tuple[ToolCall, Optional[StockScopeDecision]]:
    """Guard one tool call and return the effective call plus decision metadata."""
    effective_args = copy.deepcopy(tool_call.arguments or {})
    effective_call = ToolCall(
        id=tool_call.id,
        name=tool_call.name,
        arguments=effective_args,
        thought_signature=tool_call.thought_signature,
        provider_specific_fields=copy.deepcopy(tool_call.provider_specific_fields or {}),
    )

    tool_def = _get_tool_definition(tool_registry, tool_call.name)
    if not _tool_has_parameter(tool_def, "stock_code"):
        return effective_call, None

    active_code = canonicalize_stock_code(stock_scope.active_stock_code)
    original_stock_code = str((tool_call.arguments or {}).get("stock_code") or "").strip()
    if not original_stock_code:
        if active_code:
            effective_args["stock_code"] = active_code
            if stock_scope.active_stock_name and _tool_has_parameter(tool_def, "stock_name"):
                effective_args["stock_name"] = stock_scope.active_stock_name
            return effective_call, StockScopeDecision(
                action="rewrite",
                reason="missing_tool_stock_code",
                original_stock_code="",
                effective_stock_code=active_code,
            )
        return effective_call, StockScopeDecision(
            action="allow",
            reason="missing_tool_stock_code",
            original_stock_code="",
            effective_stock_code="",
        )

    requested_code = canonicalize_stock_code(original_stock_code)

    if not active_code:
        return effective_call, StockScopeDecision(
            action="allow",
            reason="no_active_stock",
            original_stock_code=original_stock_code,
            effective_stock_code=requested_code,
        )

    if requested_code == active_code:
        return effective_call, StockScopeDecision(
            action="allow",
            reason="matches_active_stock",
            original_stock_code=original_stock_code,
            effective_stock_code=requested_code,
        )

    if requested_code in stock_scope.allowed_stock_codes:
        return effective_call, StockScopeDecision(
            action="allow",
            reason="explicit_user_stock",
            original_stock_code=original_stock_code,
            effective_stock_code=requested_code,
        )

    if _is_denied_ticker_candidate(original_stock_code):
        effective_args["stock_code"] = active_code
        if stock_scope.active_stock_name and _tool_has_parameter(tool_def, "stock_name"):
            effective_args["stock_name"] = stock_scope.active_stock_name
        return effective_call, StockScopeDecision(
            action="rewrite",
            reason="denied_ticker_candidate",
            original_stock_code=original_stock_code,
            effective_stock_code=active_code,
        )

    conflict_result = build_stock_scope_conflict(
        requested_stock_code=requested_code or original_stock_code,
        active_stock_code=active_code,
        reason="tool_stock_code_not_in_active_scope",
        tool_name=tool_call.name,
    )
    return effective_call, StockScopeDecision(
        action="block",
        reason="tool_stock_code_not_in_active_scope",
        original_stock_code=original_stock_code,
        effective_stock_code=requested_code,
        conflict_result=conflict_result,
    )


def build_stock_scope_conflict(
    *,
    requested_stock_code: str,
    active_stock_code: str,
    reason: str,
    tool_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the fixed JSON payload returned for blocked stock-scope conflicts."""
    payload: Dict[str, Any] = {
        "error": "stock_scope_conflict",
        "requested_stock_code": requested_stock_code,
        "active_stock_code": active_stock_code,
        "reason": reason,
    }
    if tool_name:
        payload["tool_name"] = tool_name
    return payload


def _extract_allowed_stock_codes(user_message: str, active_code: str) -> set[str]:
    codes = {
        canonicalize_stock_code(code)
        for code in extract_stock_codes(user_message or "")
        if canonicalize_stock_code(code)
    }
    if not codes:
        return set()

    stripped_code = canonicalize_stock_code((user_message or "").strip())
    if stripped_code in codes:
        return codes

    if _EXPLICIT_STOCK_SCOPE_INTENT.search(user_message or ""):
        return codes

    if len(codes) >= 2 and _COMPARE_CONNECTOR.search(user_message or ""):
        return codes

    return {code for code in codes if code == active_code}


def _get_tool_definition(tool_registry: ToolRegistry, tool_name: str):
    tool_def = tool_registry.get(tool_name)
    if tool_def is None and ":" in tool_name:
        tool_def = tool_registry.get(tool_name.split(":", 1)[-1])
    return tool_def


def _tool_has_parameter(tool_def: Any, parameter_name: str) -> bool:
    if tool_def is None:
        return False
    return any(param.name == parameter_name for param in tool_def.parameters)
