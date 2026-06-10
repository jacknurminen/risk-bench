"""Benchmark model tiers per provider: lo / mid / hi mapped to concrete model identifiers.

Tiers pick comparable price-capability points across providers so cross-provider
matchups are meaningful. Price comments are input/output USD per million tokens,
checked 2026-06-10 against the providers' pricing pages:
https://platform.claude.com/docs/en/about-claude/pricing
https://ai.google.dev/gemini-api/docs/pricing
https://developers.openai.com/api/docs/pricing
"""

from enum import StrEnum


class AnthropicModel(StrEnum):
    LO = "claude-haiku-4-5"  # $1.00 / $5.00
    MID = "claude-sonnet-4-6"  # $3.00 / $15.00
    HI = "claude-opus-4-8"  # $5.00 / $25.00


class GoogleModel(StrEnum):
    LO = "gemini-3.1-flash-lite"  # $0.25 / $1.50
    MID = "gemini-3.5-flash"  # $1.50 / $9.00
    HI = "gemini-3.1-pro-preview"  # $2.00 / $12.00 (<=200k-token prompts; $4.00 / $18.00 above)


class OpenAIModel(StrEnum):
    LO = "gpt-5.4-mini"  # $0.75 / $4.50
    MID = "gpt-5.4"  # $2.50 / $15.00
    HI = "gpt-5.5"  # $5.00 / $30.00
