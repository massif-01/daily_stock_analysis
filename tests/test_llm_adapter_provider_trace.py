# -*- coding: utf-8 -*-
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    sys.modules["litellm"] = MagicMock()

from src.agent.llm_adapter import LLMToolAdapter  # noqa: E402


def test_convert_messages_preserves_reasoning_blocks_and_provider_specific_fields() -> None:
    adapter = LLMToolAdapter.__new__(LLMToolAdapter)
    messages = [
        {
            "role": "assistant",
            "content": "checking",
            "_trace_provider": "anthropic",
            "_trace_model": "anthropic/claude-test",
            "provider_blocks": [
                {"type": "thinking", "thinking": "opaque"},
                {"type": "redacted_thinking", "data": "redacted"},
                {"type": "text", "text": "checking"},
            ],
            "reasoning_content": "reasoning",
            "tool_calls": [
                {
                    "id": "call_1",
                    "name": "echo",
                    "arguments": {"message": "hello"},
                    "thought_signature": "sig-1",
                    "provider_specific_fields": {"thought_signature": "sig-1", "extra": "keep"},
                }
            ],
        }
    ]

    converted = adapter._convert_messages(messages)

    assert converted[0]["role"] == "assistant"
    assert converted[0]["content"][0]["type"] == "thinking"
    assert converted[0]["reasoning_content"] == "reasoning"
    assert converted[0]["tool_calls"][0]["provider_specific_fields"] == {
        "thought_signature": "sig-1",
        "extra": "keep",
    }
    assert "_trace_provider" not in converted[0]


def test_parse_litellm_response_extracts_claude_blocks_and_tool_provider_fields() -> None:
    adapter = LLMToolAdapter.__new__(LLMToolAdapter)
    blocks = [
        {"type": "thinking", "thinking": "opaque"},
        {"type": "redacted_thinking", "data": "hidden"},
        {"type": "text", "text": "Need data"},
    ]
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=blocks,
                    reasoning_content=None,
                    tool_calls=[
                        SimpleNamespace(
                            id="call_1",
                            function=SimpleNamespace(
                                name="echo",
                                arguments='{"message": "hello"}',
                                provider_specific_fields=None,
                            ),
                            provider_specific_fields={"thought_signature": "sig-1", "extra": "keep"},
                        )
                    ],
                )
            )
        ],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=2, total_tokens=3),
    )

    parsed = adapter._parse_litellm_response(response, "anthropic/claude-test")

    assert parsed.content == "Need data"
    assert parsed.provider_blocks == blocks
    assert parsed.provider == "anthropic"
    assert parsed.model == "anthropic/claude-test"
    assert parsed.tool_calls[0].thought_signature == "sig-1"
    assert parsed.tool_calls[0].provider_specific_fields == {
        "thought_signature": "sig-1",
        "extra": "keep",
    }
