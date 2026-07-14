import anthropic

from risk_bench.agents.anthropic import _with_usage
from risk_bench.agents.base import Response


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
