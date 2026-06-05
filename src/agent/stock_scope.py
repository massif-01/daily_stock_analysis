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
    StockCodeMention,
    canonicalize_stock_code,
    iter_stock_code_mentions,
)
from src.agent.tools.registry import ToolRegistry


_EXPLICIT_STOCK_SCOPE_INTENT = re.compile(
    r"换成|切换到?|改成|改为|分析|研究|看看|看一下|查一?下|查询|诊断|怎么看|怎么样|如何|走势|趋势",
    re.IGNORECASE,
)
_EXPLICIT_STOCK_SWITCH_INTENT = re.compile(r"换成|切换到?|改成|改为", re.IGNORECASE)
_COMPARE_INTENT = re.compile(r"比较|对比|相比|差异|差别|区别|不同|优劣|哪个更|谁更|\bvs\b", re.IGNORECASE)
_COMPARE_CONNECTOR = re.compile(r"和|跟|与|\bvs\b", re.IGNORECASE)
_CURRENT_STOCK_REFERENCE = re.compile(r"当前股票|当前标的|这只股票|这支股票|该股", re.IGNORECASE)
_NEGATED_COMPARE_INTENT = re.compile(
    r"(?:不要|别|无需|不用|不必|别再|排除|避免|忽略)\s*.{0,16}(?:比较|对比|相比)",
    re.IGNORECASE,
)
_PROVIDER_ARGUMENT_FIELD_NAMES = {"input", "arguments", "args", "parameters"}


@dataclass
class StockScope:
    """Structured current-stock contract for one agent loop."""

    active_stock_code: str
    active_stock_name: str = ""
    user_message: str = ""
    allowed_stock_codes: set[str] = field(default_factory=set)
    allowed_stock_names: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_context(cls, context: Optional[Dict[str, Any]], user_message: str) -> Optional["StockScope"]:
        active_code = canonicalize_stock_code((context or {}).get("stock_code", ""))
        if not active_code:
            return None
        allowed = _extract_allowed_stock_codes(user_message or "", active_code)
        context_allowed, context_names = _extract_context_allowed_stocks(
            context or {},
            active_code,
            user_message or "",
        )
        allowed.update(context_allowed)
        return cls(
            active_stock_code=active_code,
            active_stock_name=str((context or {}).get("stock_name") or "").strip(),
            user_message=user_message or "",
            allowed_stock_codes=allowed,
            allowed_stock_names=context_names,
        )


@dataclass
class StockScopeDecision:
    """Guard decision for one tool call."""

    action: str
    reason: str
    original_stock_code: str = ""
    effective_stock_code: str = ""
    original_stock_name: str = ""
    effective_stock_name: str = ""
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
        if self.original_stock_name:
            fields["original_stock_name"] = self.original_stock_name
        if self.effective_stock_name:
            fields["effective_stock_name"] = self.effective_stock_name
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
    effective_call.provider_specific_fields = _sanitize_provider_specific_fields(
        effective_call.provider_specific_fields
    )

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
        code_rewritten = original_stock_code != active_code
        effective_args["stock_code"] = active_code
        if stock_scope.active_stock_name and _tool_has_parameter(tool_def, "stock_name"):
            effective_args["stock_name"] = stock_scope.active_stock_name
        return effective_call, StockScopeDecision(
            action="rewrite" if code_rewritten else "allow",
            reason="matches_active_stock",
            original_stock_code=original_stock_code,
            effective_stock_code=requested_code,
        )

    if requested_code in stock_scope.allowed_stock_codes:
        code_rewritten = original_stock_code != requested_code
        effective_args["stock_code"] = requested_code
        original_stock_name = str(effective_args.get("stock_name") or "").strip()
        allowed_stock_name = stock_scope.allowed_stock_names.get(requested_code, "")
        if allowed_stock_name and _tool_has_parameter(tool_def, "stock_name"):
            effective_args["stock_name"] = allowed_stock_name
            if original_stock_name != allowed_stock_name or code_rewritten:
                return effective_call, StockScopeDecision(
                    action="rewrite",
                    reason="allowed_stock_name_sync" if original_stock_name != allowed_stock_name else "canonical_stock_code",
                    original_stock_code=original_stock_code,
                    effective_stock_code=requested_code,
                    original_stock_name=original_stock_name,
                    effective_stock_name=allowed_stock_name,
                )
        elif (
            original_stock_name
            and _tool_has_parameter(tool_def, "stock_name")
        ):
            conflict_result = build_stock_scope_conflict(
                requested_stock_code=requested_code,
                active_stock_code=active_code,
                reason="allowed_stock_name_missing",
                tool_name=tool_call.name,
            )
            return effective_call, StockScopeDecision(
                action="block",
                reason="allowed_stock_name_missing",
                original_stock_code=original_stock_code,
                effective_stock_code=requested_code,
                original_stock_name=original_stock_name,
                conflict_result=conflict_result,
            )
        return effective_call, StockScopeDecision(
            action="rewrite" if code_rewritten else "allow",
            reason="canonical_stock_code" if code_rewritten else "explicit_user_stock",
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
    mentions = [
        (mention, canonicalize_stock_code(mention.code))
        for mention in iter_stock_code_mentions(user_message or "")
    ]
    codes = {code for _, code in mentions if code}
    if not codes:
        return set()

    stripped_code = canonicalize_stock_code((user_message or "").strip())
    if stripped_code in codes:
        return codes

    allowed: set[str] = set()
    for mention, code in mentions:
        if not code:
            continue
        if code == active_code:
            allowed.add(code)
            continue
        if _is_stock_mention_allowed(user_message or "", mention, len(codes)):
            allowed.add(code)
    return allowed


def _extract_context_allowed_stocks(
    context: Dict[str, Any],
    active_code: str,
    user_message: str,
) -> Tuple[set[str], Dict[str, str]]:
    if not _has_context_allowed_intent(user_message or ""):
        return set(), {}
    allowed: set[str] = set()
    names: Dict[str, str] = {}
    message_allowed_codes = _extract_allowed_stock_codes(user_message or "", active_code)

    def add_allowed(raw_code: Any, raw_name: Any = "") -> None:
        code = canonicalize_stock_code(raw_code)
        if not code or code == active_code or _is_denied_ticker_candidate(code):
            return
        name = str(raw_name or "").strip()
        if code not in message_allowed_codes and not _is_name_mention_allowed(user_message or "", name):
            return
        allowed.add(code)
        if name:
            names[code] = name

    raw_codes = context.get("allowed_stock_codes")
    if isinstance(raw_codes, list):
        for raw_code in raw_codes:
            if isinstance(raw_code, dict):
                add_allowed(
                    raw_code.get("stock_code") or raw_code.get("code"),
                    raw_code.get("stock_name") or raw_code.get("name"),
                )
            else:
                add_allowed(raw_code)

    raw_stocks = context.get("allowed_stocks")
    if isinstance(raw_stocks, list):
        for raw_stock in raw_stocks:
            if isinstance(raw_stock, dict):
                add_allowed(
                    raw_stock.get("stock_code") or raw_stock.get("code"),
                    raw_stock.get("stock_name") or raw_stock.get("name"),
                )

    return allowed, names


def _is_stock_mention_allowed(user_message: str, mention: StockCodeMention, code_count: int) -> bool:
    if _has_negated_stock_scope(user_message, mention):
        return False

    if _has_compare_intent(user_message):
        return True

    if code_count >= 2 and _COMPARE_CONNECTOR.search(user_message) and _EXPLICIT_STOCK_SCOPE_INTENT.search(user_message):
        return True

    return bool(_EXPLICIT_STOCK_SCOPE_INTENT.search(_mention_window(user_message, mention)))


def _has_compare_intent(user_message: str) -> bool:
    if _NEGATED_COMPARE_INTENT.search(user_message):
        return False
    return bool(
        _COMPARE_INTENT.search(user_message)
        or (_CURRENT_STOCK_REFERENCE.search(user_message) and _COMPARE_CONNECTOR.search(user_message))
    )


def _has_context_allowed_intent(user_message: str) -> bool:
    return bool(_has_compare_intent(user_message) or _EXPLICIT_STOCK_SWITCH_INTENT.search(user_message))


def _is_name_mention_allowed(user_message: str, stock_name: str) -> bool:
    name = (stock_name or "").strip()
    if not name:
        return False
    flags = re.IGNORECASE if name.isascii() else 0
    for match in re.finditer(re.escape(name), user_message, flags):
        mention = StockCodeMention(code="", raw=name, start=match.start(), end=match.end())
        if not _has_negated_stock_scope(user_message, mention):
            return True
    return False


def _has_negated_stock_scope(user_message: str, mention: StockCodeMention) -> bool:
    left = user_message[max(0, mention.start - 12):mention.start]
    right = user_message[mention.end:min(len(user_message), mention.end + 8)]
    if re.search(r"(不要|别|无需|不用|不必|别再|排除|避免|忽略|不参考|不要参考)\s*.{0,8}$", left):
        return True
    if re.search(r"(not|without|exclude|ignore)\s*.{0,16}$", left, re.IGNORECASE):
        return True
    if re.search(r"^\s*(不用|不必|不要|别|排除|忽略)", right):
        return True
    return False


def _mention_window(user_message: str, mention: StockCodeMention) -> str:
    start = max(0, mention.start - 10)
    end = min(len(user_message), mention.end + 10)
    return user_message[start:end]


def _get_tool_definition(tool_registry: ToolRegistry, tool_name: str):
    tool_def = tool_registry.get(tool_name)
    if tool_def is None and ":" in tool_name:
        tool_def = tool_registry.get(tool_name.split(":", 1)[-1])
    return tool_def


def _tool_has_parameter(tool_def: Any, parameter_name: str) -> bool:
    if tool_def is None:
        return False
    return any(param.name == parameter_name for param in tool_def.parameters)


def _sanitize_provider_specific_fields(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: Dict[str, Any] = {}
        for key, item in value.items():
            if str(key).strip().lower() in _PROVIDER_ARGUMENT_FIELD_NAMES:
                continue
            cleaned = _sanitize_provider_specific_fields(item)
            if cleaned is not None:
                sanitized[key] = cleaned
        return sanitized
    if isinstance(value, list):
        return [
            cleaned
            for item in value
            if (cleaned := _sanitize_provider_specific_fields(item)) is not None
        ]
    return value
