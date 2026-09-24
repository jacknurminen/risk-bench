import anthropic

from risk_bench.agents.anthropic import _parse_response, _to_anthropic_message, _with_usage
from risk_bench.agents.base import Message, Response
from risk_bench.runner.checkpoint import decode, encode


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
