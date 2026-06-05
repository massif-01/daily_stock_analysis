# -*- coding: utf-8 -*-
"""Tests for session-level stock-scope guarding in the agent runner."""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    sys.modules["litellm"] = MagicMock()

from src.agent.llm_adapter import LLMResponse, ToolCall
from src.agent.runner import run_agent_loop
from src.agent.stock_scope import StockScope
from src.agent.tools.registry import ToolDefinition, ToolParameter, ToolRegistry


def _make_quote_registry(calls):
    registry = ToolRegistry()

    def _quote(stock_code):
        calls.append(stock_code)
        return {"code": stock_code}

    registry.register(
        ToolDefinition(
            name="get_realtime_quote",
            description="Get realtime quote",
            parameters=[
                ToolParameter(name="stock_code", type="string", description="Stock code"),
            ],
            handler=_quote,
        )
    )
    return registry


def _make_search_registry(calls):
    registry = ToolRegistry()

    def _search(stock_code, stock_name):
        calls.append((stock_code, stock_name))
        return {"code": stock_code, "name": stock_name}

    registry.register(
        ToolDefinition(
            name="search_stock_news",
            description="Search stock news",
            parameters=[
                ToolParameter(name="stock_code", type="string", description="Stock code"),
                ToolParameter(name="stock_name", type="string", description="Stock name"),
            ],
            handler=_search,
            category="search",
        )
    )
    return registry


class TestStockScopeGuard(unittest.TestCase):
    def test_denied_ticker_tool_arg_is_rewritten_to_active_stock_before_trace(self):
        calls = []
        registry = _make_quote_registry(calls)
        adapter = MagicMock()
        adapter.call_with_tools.side_effect = [
            LLMResponse(
                content="Checking.",
                tool_calls=[
                    ToolCall(id="q1", name="get_realtime_quote", arguments={"stock_code": "TTM"}),
                ],
                provider="openai",
            ),
            LLMResponse(content="done", tool_calls=[], provider="openai"),
        ]

        result = run_agent_loop(
            messages=[{"role": "user", "content": "如果不考虑 TTM 呢"}],
            tool_registry=registry,
            llm_adapter=adapter,
            max_steps=2,
            stock_scope=StockScope.from_context(
                {"stock_code": "600519", "stock_name": "示例股票"},
                "如果不考虑 TTM 呢",
            ),
        )

        self.assertTrue(result.success)
        self.assertEqual(calls, ["600519"])
        followup_messages = adapter.call_with_tools.call_args_list[1].args[0]
        assistant_msg = followup_messages[-2]
        self.assertEqual(assistant_msg["tool_calls"][0]["arguments"]["stock_code"], "600519")
        log = result.tool_calls_log[0]
        self.assertEqual(log["stock_scope_action"], "rewrite")
        self.assertEqual(log["original_stock_code"], "TTM")
        self.assertEqual(log["effective_stock_code"], "600519")
        self.assertEqual(log["tool_call_id"], "q1")

    def test_provider_tool_use_blocks_are_removed_after_stock_scope_guard(self):
        calls = []
        registry = _make_quote_registry(calls)
        adapter = MagicMock()
        adapter.call_with_tools.side_effect = [
            LLMResponse(
                content="Checking.",
                tool_calls=[
                    ToolCall(
                        id="q1",
                        name="get_realtime_quote",
                        arguments={"stock_code": "TTM"},
                        provider_specific_fields={"input": {"stock_code": "TTM"}, "meta": {"stable": True}},
                    ),
                ],
                provider_blocks=[
                    {"type": "thinking", "thinking": "Need quote."},
                    {
                        "type": "tool_use",
                        "id": "q1",
                        "name": "get_realtime_quote",
                        "input": {"stock_code": "TTM"},
                    },
                ],
                provider="anthropic",
            ),
            LLMResponse(content="done", tool_calls=[], provider="anthropic"),
        ]

        result = run_agent_loop(
            messages=[{"role": "user", "content": "如果不考虑 TTM 呢"}],
            tool_registry=registry,
            llm_adapter=adapter,
            max_steps=2,
            stock_scope=StockScope.from_context(
                {"stock_code": "600519", "stock_name": "示例股票"},
                "如果不考虑 TTM 呢",
            ),
        )

        self.assertTrue(result.success)
        assistant_msg = adapter.call_with_tools.call_args_list[1].args[0][-2]
        self.assertEqual(assistant_msg["tool_calls"][0]["arguments"]["stock_code"], "600519")
        self.assertNotIn("TTM", json.dumps(assistant_msg["tool_calls"], ensure_ascii=False))
        self.assertEqual(assistant_msg["tool_calls"][0]["provider_specific_fields"], {"meta": {"stable": True}})
        self.assertEqual(assistant_msg["provider_blocks"], [{"type": "thinking", "thinking": "Need quote."}])
        self.assertNotIn("TTM", json.dumps(assistant_msg["provider_blocks"], ensure_ascii=False))

    def test_unmentioned_valid_stock_is_blocked_with_fixed_conflict_schema(self):
        calls = []
        registry = _make_quote_registry(calls)
        adapter = MagicMock()
        adapter.call_with_tools.side_effect = [
            LLMResponse(
                content="Checking.",
                tool_calls=[
                    ToolCall(id="q1", name="get_realtime_quote", arguments={"stock_code": "AAPL"}),
                ],
                provider="openai",
            ),
            LLMResponse(content="done", tool_calls=[], provider="openai"),
        ]

        result = run_agent_loop(
            messages=[{"role": "user", "content": "如果不考虑 TTM 呢"}],
            tool_registry=registry,
            llm_adapter=adapter,
            max_steps=2,
            stock_scope=StockScope.from_context(
                {"stock_code": "600519", "stock_name": "示例股票"},
                "如果不考虑 TTM 呢",
            ),
        )

        self.assertTrue(result.success)
        self.assertEqual(calls, [])
        tool_msg = adapter.call_with_tools.call_args_list[1].args[0][-1]
        payload = json.loads(tool_msg["content"])
        self.assertEqual(payload["error"], "stock_scope_conflict")
        self.assertEqual(payload["requested_stock_code"], "AAPL")
        self.assertEqual(payload["active_stock_code"], "600519")
        self.assertEqual(payload["reason"], "tool_stock_code_not_in_active_scope")
        self.assertEqual(result.tool_calls_log[0]["stock_scope_action"], "block")

    def test_mentioned_stock_without_switch_or_compare_intent_is_blocked(self):
        calls = []
        registry = _make_quote_registry(calls)
        adapter = MagicMock()
        adapter.call_with_tools.side_effect = [
            LLMResponse(
                content="Checking.",
                tool_calls=[
                    ToolCall(id="q1", name="get_realtime_quote", arguments={"stock_code": "AAPL"}),
                ],
                provider="openai",
            ),
            LLMResponse(content="done", tool_calls=[], provider="openai"),
        ]

        result = run_agent_loop(
            messages=[{"role": "user", "content": "不要参考 AAPL 的趋势"}],
            tool_registry=registry,
            llm_adapter=adapter,
            max_steps=2,
            stock_scope=StockScope.from_context(
                {"stock_code": "600519", "stock_name": "示例股票"},
                "不要参考 AAPL 的趋势",
            ),
        )

        self.assertTrue(result.success)
        self.assertEqual(calls, [])
        tool_msg = adapter.call_with_tools.call_args_list[1].args[0][-1]
        payload = json.loads(tool_msg["content"])
        self.assertEqual(payload["error"], "stock_scope_conflict")
        self.assertEqual(payload["requested_stock_code"], "AAPL")
        self.assertEqual(payload["active_stock_code"], "600519")
        self.assertEqual(result.tool_calls_log[0]["stock_scope_action"], "block")

    def test_negated_reference_stock_is_blocked_even_with_active_stock_analysis_intent(self):
        calls = []
        registry = _make_quote_registry(calls)
        adapter = MagicMock()
        adapter.call_with_tools.side_effect = [
            LLMResponse(
                content="Checking.",
                tool_calls=[
                    ToolCall(id="q1", name="get_realtime_quote", arguments={"stock_code": "AAPL"}),
                ],
                provider="openai",
            ),
            LLMResponse(content="done", tool_calls=[], provider="openai"),
        ]

        result = run_agent_loop(
            messages=[{"role": "user", "content": "分析 600519 时不要参考 AAPL"}],
            tool_registry=registry,
            llm_adapter=adapter,
            max_steps=2,
            stock_scope=StockScope.from_context(
                {"stock_code": "600519", "stock_name": "示例股票"},
                "分析 600519 时不要参考 AAPL",
            ),
        )

        self.assertTrue(result.success)
        self.assertEqual(calls, [])
        tool_msg = adapter.call_with_tools.call_args_list[1].args[0][-1]
        payload = json.loads(tool_msg["content"])
        self.assertEqual(payload["error"], "stock_scope_conflict")
        self.assertEqual(payload["requested_stock_code"], "AAPL")
        self.assertEqual(payload["active_stock_code"], "600519")
        self.assertEqual(result.tool_calls_log[0]["stock_scope_action"], "block")

    def test_explicit_single_stock_switch_allows_requested_stock_code(self):
        calls = []
        registry = _make_quote_registry(calls)
        adapter = MagicMock()
        adapter.call_with_tools.side_effect = [
            LLMResponse(
                content="Switching.",
                tool_calls=[
                    ToolCall(id="q1", name="get_realtime_quote", arguments={"stock_code": "AAPL"}),
                ],
                provider="openai",
            ),
            LLMResponse(content="done", tool_calls=[], provider="openai"),
        ]

        result = run_agent_loop(
            messages=[{"role": "user", "content": "换成 AAPL 看看"}],
            tool_registry=registry,
            llm_adapter=adapter,
            max_steps=2,
            stock_scope=StockScope.from_context(
                {"stock_code": "600519", "stock_name": "示例股票"},
                "换成 AAPL 看看",
            ),
        )

        self.assertTrue(result.success)
        self.assertEqual(calls, ["AAPL"])
        self.assertEqual(result.tool_calls_log[0]["stock_scope_action"], "allow")
        self.assertEqual(result.tool_calls_log[0]["stock_scope_reason"], "explicit_user_stock")

    def test_us_market_suffix_matches_active_stock_scope(self):
        calls = []
        registry = _make_quote_registry(calls)
        adapter = MagicMock()
        adapter.call_with_tools.side_effect = [
            LLMResponse(
                content="Checking.",
                tool_calls=[
                    ToolCall(id="q1", name="get_realtime_quote", arguments={"stock_code": "AAPL.US"}),
                ],
                provider="openai",
            ),
            LLMResponse(content="done", tool_calls=[], provider="openai"),
        ]

        result = run_agent_loop(
            messages=[{"role": "user", "content": "继续看当前股票"}],
            tool_registry=registry,
            llm_adapter=adapter,
            max_steps=2,
            stock_scope=StockScope.from_context(
                {"stock_code": "AAPL", "stock_name": "Apple"},
                "继续看当前股票",
            ),
        )

        self.assertTrue(result.success)
        self.assertEqual(calls, ["AAPL"])
        self.assertEqual(result.tool_calls_log[0]["stock_scope_action"], "rewrite")
        self.assertEqual(result.tool_calls_log[0]["stock_scope_reason"], "matches_active_stock")
        self.assertEqual(result.tool_calls_log[0]["effective_stock_code"], "AAPL")

    def test_explicit_compare_allows_multiple_stock_codes(self):
        calls = []
        registry = _make_quote_registry(calls)
        adapter = MagicMock()
        adapter.call_with_tools.side_effect = [
            LLMResponse(
                content="Comparing.",
                tool_calls=[
                    ToolCall(id="q1", name="get_realtime_quote", arguments={"stock_code": "AAPL"}),
                ],
                provider="openai",
            ),
            LLMResponse(content="done", tool_calls=[], provider="openai"),
        ]

        result = run_agent_loop(
            messages=[{"role": "user", "content": "比较 600519 和 AAPL"}],
            tool_registry=registry,
            llm_adapter=adapter,
            max_steps=2,
            stock_scope=StockScope.from_context(
                {"stock_code": "600519", "stock_name": "示例股票"},
                "比较 600519 和 AAPL",
            ),
        )

        self.assertTrue(result.success)
        self.assertEqual(calls, ["AAPL"])
        self.assertEqual(result.tool_calls_log[0]["stock_scope_action"], "allow")
        self.assertEqual(result.tool_calls_log[0]["stock_scope_reason"], "explicit_user_stock")

    def test_explicit_compare_allows_stock_code_against_current_stock(self):
        calls = []
        registry = _make_quote_registry(calls)
        adapter = MagicMock()
        adapter.call_with_tools.side_effect = [
            LLMResponse(
                content="Comparing.",
                tool_calls=[
                    ToolCall(id="q1", name="get_realtime_quote", arguments={"stock_code": "AAPL"}),
                ],
                provider="openai",
            ),
            LLMResponse(content="done", tool_calls=[], provider="openai"),
        ]

        result = run_agent_loop(
            messages=[{"role": "user", "content": "比较 AAPL 和当前股票"}],
            tool_registry=registry,
            llm_adapter=adapter,
            max_steps=2,
            stock_scope=StockScope.from_context(
                {"stock_code": "600519", "stock_name": "示例股票"},
                "比较 AAPL 和当前股票",
            ),
        )

        self.assertTrue(result.success)
        self.assertEqual(calls, ["AAPL"])
        self.assertEqual(result.tool_calls_log[0]["stock_scope_action"], "allow")
        self.assertEqual(result.tool_calls_log[0]["stock_scope_reason"], "explicit_user_stock")

    def test_adjacent_compare_wording_allows_stock_code_against_current_stock(self):
        calls = []
        registry = _make_quote_registry(calls)
        adapter = MagicMock()
        adapter.call_with_tools.side_effect = [
            LLMResponse(
                content="Comparing.",
                tool_calls=[
                    ToolCall(id="q1", name="get_realtime_quote", arguments={"stock_code": "AAPL"}),
                ],
                provider="openai",
            ),
            LLMResponse(content="done", tool_calls=[], provider="openai"),
        ]

        result = run_agent_loop(
            messages=[{"role": "user", "content": "AAPL 和当前股票的差异在哪里"}],
            tool_registry=registry,
            llm_adapter=adapter,
            max_steps=2,
            stock_scope=StockScope.from_context(
                {"stock_code": "600519", "stock_name": "示例股票"},
                "AAPL 和当前股票的差异在哪里",
            ),
        )

        self.assertTrue(result.success)
        self.assertEqual(calls, ["AAPL"])
        self.assertEqual(result.tool_calls_log[0]["stock_scope_action"], "allow")
        self.assertEqual(result.tool_calls_log[0]["stock_scope_reason"], "explicit_user_stock")

    def test_current_stock_comparison_wording_allows_stock_code_against_current_stock(self):
        calls = []
        registry = _make_quote_registry(calls)
        adapter = MagicMock()
        adapter.call_with_tools.side_effect = [
            LLMResponse(
                content="Comparing.",
                tool_calls=[
                    ToolCall(id="q1", name="get_realtime_quote", arguments={"stock_code": "AAPL"}),
                ],
                provider="openai",
            ),
            LLMResponse(content="done", tool_calls=[], provider="openai"),
        ]

        result = run_agent_loop(
            messages=[{"role": "user", "content": "AAPL 和当前股票哪个更适合买"}],
            tool_registry=registry,
            llm_adapter=adapter,
            max_steps=2,
            stock_scope=StockScope.from_context(
                {"stock_code": "600519", "stock_name": "示例股票"},
                "AAPL 和当前股票哪个更适合买",
            ),
        )

        self.assertTrue(result.success)
        self.assertEqual(calls, ["AAPL"])
        self.assertEqual(result.tool_calls_log[0]["stock_scope_action"], "allow")
        self.assertEqual(result.tool_calls_log[0]["stock_scope_reason"], "explicit_user_stock")

    def test_context_allowed_stock_codes_allow_exact_name_comparison_targets(self):
        calls = []
        registry = _make_quote_registry(calls)
        adapter = MagicMock()
        adapter.call_with_tools.side_effect = [
            LLMResponse(
                content="Comparing.",
                tool_calls=[
                    ToolCall(id="q1", name="get_realtime_quote", arguments={"stock_code": "AAPL"}),
                ],
                provider="openai",
            ),
            LLMResponse(content="done", tool_calls=[], provider="openai"),
        ]

        result = run_agent_loop(
            messages=[{"role": "user", "content": "比较 Apple 和当前股票"}],
            tool_registry=registry,
            llm_adapter=adapter,
            max_steps=2,
            stock_scope=StockScope.from_context(
                {
                    "stock_code": "600519",
                    "stock_name": "示例股票",
                    "allowed_stocks": [{"stock_code": "AAPL", "stock_name": "Apple"}],
                },
                "比较 Apple 和当前股票",
            ),
        )

        self.assertTrue(result.success)
        self.assertEqual(calls, ["AAPL"])
        self.assertEqual(result.tool_calls_log[0]["stock_scope_action"], "allow")
        self.assertEqual(result.tool_calls_log[0]["stock_scope_reason"], "explicit_user_stock")

    def test_context_allowed_stock_codes_allow_current_stock_name_comparison_targets(self):
        calls = []
        registry = _make_quote_registry(calls)
        adapter = MagicMock()
        adapter.call_with_tools.side_effect = [
            LLMResponse(
                content="Comparing.",
                tool_calls=[
                    ToolCall(id="q1", name="get_realtime_quote", arguments={"stock_code": "AAPL"}),
                ],
                provider="openai",
            ),
            LLMResponse(content="done", tool_calls=[], provider="openai"),
        ]

        result = run_agent_loop(
            messages=[{"role": "user", "content": "Apple 和当前股票哪个更适合买"}],
            tool_registry=registry,
            llm_adapter=adapter,
            max_steps=2,
            stock_scope=StockScope.from_context(
                {
                    "stock_code": "600519",
                    "stock_name": "示例股票",
                    "allowed_stocks": [{"stock_code": "AAPL", "stock_name": "Apple"}],
                },
                "Apple 和当前股票哪个更适合买",
            ),
        )

        self.assertTrue(result.success)
        self.assertEqual(calls, ["AAPL"])
        self.assertEqual(result.tool_calls_log[0]["stock_scope_action"], "allow")
        self.assertEqual(result.tool_calls_log[0]["stock_scope_reason"], "explicit_user_stock")

    def test_context_allowed_stocks_are_ignored_when_not_mentioned_in_user_message(self):
        calls = []
        registry = _make_quote_registry(calls)
        adapter = MagicMock()
        adapter.call_with_tools.side_effect = [
            LLMResponse(
                content="Comparing.",
                tool_calls=[
                    ToolCall(id="q1", name="get_realtime_quote", arguments={"stock_code": "AAPL"}),
                ],
                provider="openai",
            ),
            LLMResponse(content="done", tool_calls=[], provider="openai"),
        ]

        result = run_agent_loop(
            messages=[{"role": "user", "content": "比较当前股票和宁德时代"}],
            tool_registry=registry,
            llm_adapter=adapter,
            max_steps=2,
            stock_scope=StockScope.from_context(
                {
                    "stock_code": "600519",
                    "stock_name": "示例股票",
                    "allowed_stocks": [{"stock_code": "AAPL", "stock_name": "Apple"}],
                },
                "比较当前股票和宁德时代",
            ),
        )

        self.assertTrue(result.success)
        self.assertEqual(calls, [])
        tool_msg = adapter.call_with_tools.call_args_list[1].args[0][-1]
        payload = json.loads(tool_msg["content"])
        self.assertEqual(payload["error"], "stock_scope_conflict")
        self.assertEqual(payload["requested_stock_code"], "AAPL")

    def test_context_allowed_stocks_are_ignored_for_negated_name_comparison(self):
        calls = []
        registry = _make_quote_registry(calls)
        adapter = MagicMock()
        adapter.call_with_tools.side_effect = [
            LLMResponse(
                content="Checking.",
                tool_calls=[
                    ToolCall(id="q1", name="get_realtime_quote", arguments={"stock_code": "AAPL"}),
                ],
                provider="openai",
            ),
            LLMResponse(content="done", tool_calls=[], provider="openai"),
        ]

        result = run_agent_loop(
            messages=[{"role": "user", "content": "不要和 Apple 比较，只看当前股票"}],
            tool_registry=registry,
            llm_adapter=adapter,
            max_steps=2,
            stock_scope=StockScope.from_context(
                {
                    "stock_code": "600519",
                    "stock_name": "示例股票",
                    "allowed_stocks": [{"stock_code": "AAPL", "stock_name": "Apple"}],
                },
                "不要和 Apple 比较，只看当前股票",
            ),
        )

        self.assertTrue(result.success)
        self.assertEqual(calls, [])
        tool_msg = adapter.call_with_tools.call_args_list[1].args[0][-1]
        payload = json.loads(tool_msg["content"])
        self.assertEqual(payload["error"], "stock_scope_conflict")
        self.assertEqual(payload["requested_stock_code"], "AAPL")

    def test_unrelated_negation_does_not_disable_compare_scope(self):
        calls = []
        registry = _make_quote_registry(calls)
        adapter = MagicMock()
        adapter.call_with_tools.side_effect = [
            LLMResponse(
                content="Comparing.",
                tool_calls=[
                    ToolCall(id="q1", name="get_realtime_quote", arguments={"stock_code": "AAPL"}),
                ],
                provider="openai",
            ),
            LLMResponse(content="done", tool_calls=[], provider="openai"),
        ]

        result = run_agent_loop(
            messages=[{"role": "user", "content": "比较 AAPL 和当前股票，不要考虑汇率"}],
            tool_registry=registry,
            llm_adapter=adapter,
            max_steps=2,
            stock_scope=StockScope.from_context(
                {"stock_code": "600519", "stock_name": "示例股票"},
                "比较 AAPL 和当前股票，不要考虑汇率",
            ),
        )

        self.assertTrue(result.success)
        self.assertEqual(calls, ["AAPL"])
        self.assertEqual(result.tool_calls_log[0]["stock_scope_action"], "allow")
        self.assertEqual(result.tool_calls_log[0]["stock_scope_reason"], "explicit_user_stock")

    def test_context_allowed_stock_codes_accept_exchange_suffixes_from_frontend_index(self):
        calls = []
        registry = _make_quote_registry(calls)
        adapter = MagicMock()
        adapter.call_with_tools.side_effect = [
            LLMResponse(
                content="Comparing.",
                tool_calls=[
                    ToolCall(id="q1", name="get_realtime_quote", arguments={"stock_code": "600519"}),
                ],
                provider="openai",
            ),
            LLMResponse(content="done", tool_calls=[], provider="openai"),
        ]

        result = run_agent_loop(
            messages=[{"role": "user", "content": "分析 贵州茅台 和当前股票哪个更适合买"}],
            tool_registry=registry,
            llm_adapter=adapter,
            max_steps=2,
            stock_scope=StockScope.from_context(
                {
                    "stock_code": "AAPL",
                    "stock_name": "Apple",
                    "allowed_stocks": [{"stock_code": "600519.SH", "stock_name": "贵州茅台"}],
                },
                "分析 贵州茅台 和当前股票哪个更适合买",
            ),
        )

        self.assertTrue(result.success)
        self.assertEqual(calls, ["600519"])
        self.assertEqual(result.tool_calls_log[0]["stock_scope_action"], "allow")
        self.assertEqual(result.tool_calls_log[0]["stock_scope_reason"], "explicit_user_stock")

    def test_allowed_stock_name_mapping_syncs_search_tool_name(self):
        calls = []
        registry = _make_search_registry(calls)
        adapter = MagicMock()
        adapter.call_with_tools.side_effect = [
            LLMResponse(
                content="Searching.",
                tool_calls=[
                    ToolCall(
                        id="s1",
                        name="search_stock_news",
                        arguments={"stock_code": "AAPL", "stock_name": "贵州茅台"},
                    ),
                ],
                provider="openai",
            ),
            LLMResponse(content="done", tool_calls=[], provider="openai"),
        ]

        result = run_agent_loop(
            messages=[{"role": "user", "content": "比较 Apple 和当前股票"}],
            tool_registry=registry,
            llm_adapter=adapter,
            max_steps=2,
            stock_scope=StockScope.from_context(
                {
                    "stock_code": "600519",
                    "stock_name": "贵州茅台",
                    "allowed_stock_codes": ["AAPL"],
                    "allowed_stocks": [{"stock_code": "AAPL", "stock_name": "Apple"}],
                },
                "比较 Apple 和当前股票",
            ),
        )

        self.assertTrue(result.success)
        self.assertEqual(calls, [("AAPL", "Apple")])
        followup_messages = adapter.call_with_tools.call_args_list[1].args[0]
        args = followup_messages[-2]["tool_calls"][0]["arguments"]
        self.assertEqual(args["stock_code"], "AAPL")
        self.assertEqual(args["stock_name"], "Apple")
        self.assertEqual(result.tool_calls_log[0]["stock_scope_action"], "rewrite")
        self.assertEqual(result.tool_calls_log[0]["stock_scope_reason"], "allowed_stock_name_sync")
        self.assertEqual(result.tool_calls_log[0]["original_stock_name"], "贵州茅台")
        self.assertEqual(result.tool_calls_log[0]["effective_stock_name"], "Apple")

    def test_allowed_stock_with_stale_active_name_is_blocked_without_name_mapping(self):
        calls = []
        registry = _make_search_registry(calls)
        adapter = MagicMock()
        adapter.call_with_tools.side_effect = [
            LLMResponse(
                content="Searching.",
                tool_calls=[
                    ToolCall(
                        id="s1",
                        name="search_stock_news",
                        arguments={"stock_code": "AAPL", "stock_name": "贵州茅台"},
                    ),
                ],
                provider="openai",
            ),
            LLMResponse(content="done", tool_calls=[], provider="openai"),
        ]

        result = run_agent_loop(
            messages=[{"role": "user", "content": "比较 Apple 和当前股票"}],
            tool_registry=registry,
            llm_adapter=adapter,
            max_steps=2,
            stock_scope=StockScope.from_context(
                {
                    "stock_code": "600519",
                    "stock_name": "贵州茅台",
                    "allowed_stock_codes": ["AAPL"],
                },
                "比较 AAPL 和当前股票",
            ),
        )

        self.assertTrue(result.success)
        self.assertEqual(calls, [])
        tool_msg = adapter.call_with_tools.call_args_list[1].args[0][-1]
        payload = json.loads(tool_msg["content"])
        self.assertEqual(payload["error"], "stock_scope_conflict")
        self.assertEqual(payload["reason"], "allowed_stock_name_missing")
        self.assertEqual(result.tool_calls_log[0]["stock_scope_action"], "block")

    def test_allowed_stock_with_any_name_is_blocked_without_name_mapping_for_search_tools(self):
        calls = []
        registry = _make_search_registry(calls)
        adapter = MagicMock()
        adapter.call_with_tools.side_effect = [
            LLMResponse(
                content="Searching.",
                tool_calls=[
                    ToolCall(
                        id="s1",
                        name="search_stock_news",
                        arguments={"stock_code": "AAPL", "stock_name": "贵州茅台股份"},
                    ),
                ],
                provider="openai",
            ),
            LLMResponse(content="done", tool_calls=[], provider="openai"),
        ]

        result = run_agent_loop(
            messages=[{"role": "user", "content": "比较 AAPL 和当前股票"}],
            tool_registry=registry,
            llm_adapter=adapter,
            max_steps=2,
            stock_scope=StockScope.from_context(
                {
                    "stock_code": "600519",
                    "stock_name": "贵州茅台",
                    "allowed_stock_codes": ["AAPL"],
                },
                "比较 AAPL 和当前股票",
            ),
        )

        self.assertTrue(result.success)
        self.assertEqual(calls, [])
        tool_msg = adapter.call_with_tools.call_args_list[1].args[0][-1]
        payload = json.loads(tool_msg["content"])
        self.assertEqual(payload["error"], "stock_scope_conflict")
        self.assertEqual(payload["reason"], "allowed_stock_name_missing")

    def test_context_allowed_stock_codes_are_honored_for_explicit_name_switch_intent(self):
        calls = []
        registry = _make_search_registry(calls)
        adapter = MagicMock()
        adapter.call_with_tools.side_effect = [
            LLMResponse(
                content="Switching.",
                tool_calls=[
                    ToolCall(
                        id="s1",
                        name="search_stock_news",
                        arguments={"stock_code": "AAPL", "stock_name": "贵州茅台"},
                    ),
                ],
                provider="openai",
            ),
            LLMResponse(content="done", tool_calls=[], provider="openai"),
        ]

        result = run_agent_loop(
            messages=[{"role": "user", "content": "换成 Apple 看看"}],
            tool_registry=registry,
            llm_adapter=adapter,
            max_steps=2,
            stock_scope=StockScope.from_context(
                {
                    "stock_code": "600519",
                    "stock_name": "贵州茅台",
                    "allowed_stocks": [{"stock_code": "AAPL", "stock_name": "Apple"}],
                },
                "换成 Apple 看看",
            ),
        )

        self.assertTrue(result.success)
        self.assertEqual(calls, [("AAPL", "Apple")])
        self.assertEqual(result.tool_calls_log[0]["stock_scope_action"], "rewrite")
        self.assertEqual(result.tool_calls_log[0]["stock_scope_reason"], "allowed_stock_name_sync")

    def test_context_allowed_stock_codes_are_ignored_without_switch_or_compare_intent(self):
        calls = []
        registry = _make_quote_registry(calls)
        adapter = MagicMock()
        adapter.call_with_tools.side_effect = [
            LLMResponse(
                content="Checking.",
                tool_calls=[
                    ToolCall(id="q1", name="get_realtime_quote", arguments={"stock_code": "AAPL"}),
                ],
                provider="openai",
            ),
            LLMResponse(content="done", tool_calls=[], provider="openai"),
        ]

        result = run_agent_loop(
            messages=[{"role": "user", "content": "继续分析趋势"}],
            tool_registry=registry,
            llm_adapter=adapter,
            max_steps=2,
            stock_scope=StockScope.from_context(
                {
                    "stock_code": "600519",
                    "stock_name": "示例股票",
                    "allowed_stock_codes": ["AAPL"],
                    "allowed_stocks": [{"stock_code": "AAPL", "stock_name": "Apple"}],
                },
                "继续分析趋势",
            ),
        )

        self.assertTrue(result.success)
        self.assertEqual(calls, [])
        tool_msg = adapter.call_with_tools.call_args_list[1].args[0][-1]
        payload = json.loads(tool_msg["content"])
        self.assertEqual(payload["error"], "stock_scope_conflict")
        self.assertEqual(payload["requested_stock_code"], "AAPL")

    def test_context_allowed_stock_codes_do_not_allow_denied_ticker_candidates(self):
        calls = []
        registry = _make_quote_registry(calls)
        adapter = MagicMock()
        adapter.call_with_tools.side_effect = [
            LLMResponse(
                content="Checking.",
                tool_calls=[
                    ToolCall(id="q1", name="get_realtime_quote", arguments={"stock_code": "TTM"}),
                ],
                provider="openai",
            ),
            LLMResponse(content="done", tool_calls=[], provider="openai"),
        ]

        result = run_agent_loop(
            messages=[{"role": "user", "content": "比较 TTM 和当前股票"}],
            tool_registry=registry,
            llm_adapter=adapter,
            max_steps=2,
            stock_scope=StockScope.from_context(
                {
                    "stock_code": "600519",
                    "stock_name": "示例股票",
                    "allowed_stock_codes": ["TTM"],
                },
                "比较 TTM 和当前股票",
            ),
        )

        self.assertTrue(result.success)
        self.assertEqual(calls, ["600519"])
        self.assertEqual(result.tool_calls_log[0]["stock_scope_action"], "rewrite")
        self.assertEqual(result.tool_calls_log[0]["stock_scope_reason"], "denied_ticker_candidate")

    def test_explicit_compare_allows_etf_codes_against_current_stock(self):
        calls = []
        registry = _make_quote_registry(calls)
        adapter = MagicMock()
        adapter.call_with_tools.side_effect = [
            LLMResponse(
                content="Comparing.",
                tool_calls=[
                    ToolCall(id="q1", name="get_realtime_quote", arguments={"stock_code": "510300"}),
                    ToolCall(id="q2", name="get_realtime_quote", arguments={"stock_code": "159915"}),
                ],
                provider="openai",
            ),
            LLMResponse(content="done", tool_calls=[], provider="openai"),
        ]

        result = run_agent_loop(
            messages=[{"role": "user", "content": "比较 510300、159915 和当前股票"}],
            tool_registry=registry,
            llm_adapter=adapter,
            max_steps=2,
            stock_scope=StockScope.from_context(
                {"stock_code": "600519", "stock_name": "示例股票"},
                "比较 510300、159915 和当前股票",
            ),
        )

        self.assertTrue(result.success)
        self.assertEqual(calls, ["510300", "159915"])
        self.assertEqual(result.tool_calls_log[0]["stock_scope_action"], "allow")
        self.assertEqual(result.tool_calls_log[1]["stock_scope_action"], "allow")

    def test_rewrite_keeps_search_stock_name_in_sync_when_active_name_exists(self):
        calls = []
        registry = ToolRegistry()

        def _search(stock_code, stock_name):
            calls.append((stock_code, stock_name))
            return {"code": stock_code, "name": stock_name}

        registry.register(
            ToolDefinition(
                name="search_stock_news",
                description="Search stock news",
                parameters=[
                    ToolParameter(name="stock_code", type="string", description="Stock code"),
                    ToolParameter(name="stock_name", type="string", description="Stock name"),
                ],
                handler=_search,
                category="search",
            )
        )
        adapter = MagicMock()
        adapter.call_with_tools.side_effect = [
            LLMResponse(
                content="Searching.",
                tool_calls=[
                    ToolCall(
                        id="s1",
                        name="search_stock_news",
                        arguments={"stock_code": "TTM", "stock_name": "TTM"},
                    ),
                ],
                provider="openai",
            ),
            LLMResponse(content="done", tool_calls=[], provider="openai"),
        ]

        result = run_agent_loop(
            messages=[{"role": "user", "content": "如果不考虑 TTM 呢"}],
            tool_registry=registry,
            llm_adapter=adapter,
            max_steps=2,
            stock_scope=StockScope.from_context(
                {"stock_code": "600519", "stock_name": "示例股票"},
                "如果不考虑 TTM 呢",
            ),
        )

        self.assertTrue(result.success)
        self.assertEqual(calls, [("600519", "示例股票")])
        followup_messages = adapter.call_with_tools.call_args_list[1].args[0]
        args = followup_messages[-2]["tool_calls"][0]["arguments"]
        self.assertEqual(args["stock_code"], "600519")
        self.assertEqual(args["stock_name"], "示例股票")

    def test_active_stock_code_syncs_stale_tool_stock_name(self):
        calls = []
        registry = ToolRegistry()

        def _search(stock_code, stock_name):
            calls.append((stock_code, stock_name))
            return {"code": stock_code, "name": stock_name}

        registry.register(
            ToolDefinition(
                name="search_stock_news",
                description="Search stock news",
                parameters=[
                    ToolParameter(name="stock_code", type="string", description="Stock code"),
                    ToolParameter(name="stock_name", type="string", description="Stock name"),
                ],
                handler=_search,
                category="search",
            )
        )
        adapter = MagicMock()
        adapter.call_with_tools.side_effect = [
            LLMResponse(
                content="Searching.",
                tool_calls=[
                    ToolCall(
                        id="s1",
                        name="search_stock_news",
                        arguments={"stock_code": "600519", "stock_name": "TTM"},
                    ),
                ],
                provider="openai",
            ),
            LLMResponse(content="done", tool_calls=[], provider="openai"),
        ]

        result = run_agent_loop(
            messages=[{"role": "user", "content": "继续看新闻"}],
            tool_registry=registry,
            llm_adapter=adapter,
            max_steps=2,
            stock_scope=StockScope.from_context(
                {"stock_code": "600519", "stock_name": "示例股票"},
                "继续看新闻",
            ),
        )

        self.assertTrue(result.success)
        self.assertEqual(calls, [("600519", "示例股票")])
        followup_messages = adapter.call_with_tools.call_args_list[1].args[0]
        args = followup_messages[-2]["tool_calls"][0]["arguments"]
        self.assertEqual(args["stock_code"], "600519")
        self.assertEqual(args["stock_name"], "示例股票")
        self.assertEqual(result.tool_calls_log[0]["stock_scope_action"], "allow")
        self.assertEqual(result.tool_calls_log[0]["stock_scope_reason"], "matches_active_stock")

    def test_missing_tool_stock_code_is_rewritten_to_active_stock(self):
        calls = []
        registry = _make_quote_registry(calls)
        adapter = MagicMock()
        adapter.call_with_tools.side_effect = [
            LLMResponse(
                content="Checking.",
                tool_calls=[
                    ToolCall(id="q1", name="get_realtime_quote", arguments={}),
                ],
                provider="openai",
            ),
            LLMResponse(content="done", tool_calls=[], provider="openai"),
        ]

        result = run_agent_loop(
            messages=[{"role": "user", "content": "继续看成交量"}],
            tool_registry=registry,
            llm_adapter=adapter,
            max_steps=2,
            stock_scope=StockScope.from_context(
                {"stock_code": "600519", "stock_name": "示例股票"},
                "继续看成交量",
            ),
        )

        self.assertTrue(result.success)
        self.assertEqual(calls, ["600519"])
        followup_messages = adapter.call_with_tools.call_args_list[1].args[0]
        assistant_msg = followup_messages[-2]
        self.assertEqual(assistant_msg["tool_calls"][0]["arguments"]["stock_code"], "600519")
        self.assertEqual(result.tool_calls_log[0]["stock_scope_action"], "rewrite")
        self.assertEqual(result.tool_calls_log[0]["stock_scope_reason"], "missing_tool_stock_code")
        self.assertEqual(result.tool_calls_log[0]["effective_stock_code"], "600519")

    def test_parallel_batch_uses_effective_args_and_blocks_conflicts_independently(self):
        calls = []
        registry = _make_quote_registry(calls)
        adapter = MagicMock()
        adapter.call_with_tools.side_effect = [
            LLMResponse(
                content="Checking batch.",
                tool_calls=[
                    ToolCall(id="q1", name="get_realtime_quote", arguments={"stock_code": "TTM"}),
                    ToolCall(id="q2", name="get_realtime_quote", arguments={"stock_code": "AAPL"}),
                ],
                provider="openai",
            ),
            LLMResponse(content="done", tool_calls=[], provider="openai"),
        ]

        result = run_agent_loop(
            messages=[{"role": "user", "content": "如果不考虑 TTM 呢"}],
            tool_registry=registry,
            llm_adapter=adapter,
            max_steps=2,
            stock_scope=StockScope.from_context(
                {"stock_code": "600519", "stock_name": "示例股票"},
                "如果不考虑 TTM 呢",
            ),
        )

        self.assertTrue(result.success)
        self.assertEqual(calls, ["600519"])
        followup_messages = adapter.call_with_tools.call_args_list[1].args[0]
        assistant_calls = followup_messages[-3]["tool_calls"]
        self.assertEqual(assistant_calls[0]["arguments"]["stock_code"], "600519")
        self.assertEqual(assistant_calls[1]["arguments"]["stock_code"], "AAPL")
        tool_payloads = [json.loads(msg["content"]) for msg in followup_messages[-2:]]
        self.assertTrue(any(payload.get("error") == "stock_scope_conflict" for payload in tool_payloads))
        self.assertEqual({entry["tool_call_id"] for entry in result.tool_calls_log}, {"q1", "q2"})


if __name__ == "__main__":
    unittest.main()
