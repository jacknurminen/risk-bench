from unittest.mock import MagicMock

import anthropic
import pytest

from risk_bench.agents.anthropic import _parse_response, _to_anthropic_message, _with_usage
from risk_bench.agents.base import Message, Response
from risk_bench.runner.checkpoint import decode, encode


@pytest.mark.parametrize("model,supports_effort", [("claude-haiku-5-5", True), ("claude-haiku-4-5", False)])
def test_haiku_sends_effort_only_for_supported_generation(monkeypatch, model, supports_effort):
    from risk_bench.agents.anthropic import AnthropicAgent
    from risk_bench.agents.base import ReasoningEffort

    client = MagicMock()
    client.messages.stream.return_value.__enter__.return_value.get_final_message.return_value = (
        anthropic.types.Message(
            id="msg-1",
            type="message",
            role="assistant",
            model=model,
            content=[],
            stop_reason="end_turn",
            usage=anthropic.types.Usage(input_tokens=1, output_tokens=1),
        )
    )
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: client)
    agent = AnthropicAgent(model, "unused", 1024, ReasoningEffort.HIGH)
    agent.respond([Message(role="user", content="Hello")], [])
    sent = client.messages.stream.call_args.kwargs
    assert sent["model"] == model
    assert sent["output_config"] == ({"effort": "high"} if supports_effort else anthropic.omit)


@pytest.mark.parametrize("thinking_only", [False, True])
def test_unusable_anthropic_response_keeps_diagnostics_in_trace(monkeypatch, tmp_path, thinking_only):
    import json

    from risk_bench.agents.anthropic import AnthropicAgent
    from risk_bench.agents.base import ReasoningEffort
    from risk_bench.agents.tracing import TraceWriter, TracingAgent

    blocks = (
        [anthropic.types.ThinkingBlock(type="thinking", thinking="Planning", signature="sig")] if thinking_only else []
    )
    client = MagicMock()
    stream = client.messages.stream.return_value.__enter__.return_value
    stream.request_id = "req-123"
    stream.get_final_message.return_value = anthropic.types.Message(
        id="msg-123",
        type="message",
        role="assistant",
        model="claude-opus-5-5",
        content=blocks,
        stop_reason="max_tokens" if thinking_only else "end_turn",
        usage=anthropic.types.Usage(input_tokens=200, output_tokens=8192 if thinking_only else 0),
    )
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: client)
    writer = TraceWriter(path=tmp_path / "trace.jsonl", append=False)
    agent = TracingAgent(
        agent=AnthropicAgent("claude-opus-5-5", "unused", 8192, ReasoningEffort.LOW),
        model="claude-opus-5-5",
        caller="alice",
        writer=writer,
    )
    response = agent.respond([Message(role="user", content="Choose an action")], [])
    writer.close()
    record = json.loads(writer.path.read_text())

    assert response.text is None and not response.tool_calls
    assert record["output_tokens"] == (8192 if thinking_only else 0)
    assert record["response"]["stop_reason"] == ("max_tokens" if thinking_only else "end_turn")
    assert record["response"]["provider_metadata"] == {
        "provider": "anthropic",
        "request_id": "req-123",
        "response_id": "msg-123",
        "model": "claude-opus-5-5",
        "stop_sequence": None,
        "max_tokens": 8192,
        "reasoning_effort": "low",
        "content": [block.model_dump(mode="json", exclude_none=True) for block in blocks],
    }


def test_usage_normalizes_cache_traffic_into_total_input():
    # Anthropic's input_tokens EXCLUDES cache traffic; the cached shares come back separately
    usage = anthropic.types.Usage(
        input_tokens=200, output_tokens=50, cache_creation_input_tokens=1_000, cache_read_input_tokens=8_000
    )

    response = _with_usage(parsed=Response(text="ok"), usage=usage)

    assert response.input_tokens == 9_200  # normalized to the call's total prompt tokens
    assert response.cache_write_tokens == 1_000 and response.cache_read_tokens == 8_000
    assert response.output_tokens == 50


def test_usage_without_cache_fields_reports_no_cached_shares():
    usage = anthropic.types.Usage(
        input_tokens=300, output_tokens=20, cache_creation_input_tokens=None, cache_read_input_tokens=None
    )

    response = _with_usage(parsed=Response(text="ok"), usage=usage)

    assert response.input_tokens == 300
    assert response.cache_write_tokens == 0 and response.cache_read_tokens == 0


def test_thinking_and_tool_block_order_survive_retry_checkpoint_roundtrip():
    blocks = [
        anthropic.types.ThinkingBlock(type="thinking", thinking="", signature="signed-first"),
        anthropic.types.TextBlock(type="text", text="A move."),
        anthropic.types.ToolUseBlock(type="tool_use", id="call-1", name="attack", input={"dice": 4}),
        anthropic.types.ThinkingBlock(type="thinking", thinking="", signature="signed-second"),
        anthropic.types.ToolUseBlock(type="tool_use", id="call-2", name="send_message", input={"text": "Hello"}),
    ]
    response = _parse_response(blocks)
    message = Message(role="assistant", content=response.text, tool_calls=response.tool_calls)
    restored = decode(encode(message))

    assert response.text == "A move."
    assert [call.id for call in response.tool_calls] == ["call-1", "call-2"]
    assert _to_anthropic_message(restored) == {
        "role": "assistant",
        "content": [block.model_dump(mode="json", exclude_none=True) for block in blocks],
    }
