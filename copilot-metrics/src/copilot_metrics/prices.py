import tomllib
from pathlib import Path
from typing import Any

RATE_FIELDS = ("input", "output", "cache_read", "cache_write")
TOKENS_PER_RATE = 1_000_000
INCLUSIVE_INPUT_PROVIDERS = ("copilot", "codex")

Prices = dict[str, dict[str, float]]


def valid_rate(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and value >= 0


def validated_model(rates: object) -> dict[str, float]:
    if not isinstance(rates, dict) or not all(valid_rate(rates.get(field)) for field in RATE_FIELDS):
        raise ValueError("invalid_prices")
    return {field: float(rates[field]) for field in RATE_FIELDS}


def load_prices(path: Path) -> Prices:
    if not path.exists():
        return {}
    try:
        document: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise ValueError("invalid_prices") from error
    models = document.get("models", {})
    if not isinstance(models, dict):
        raise ValueError("invalid_prices")
    return {model: validated_model(rates) for model, rates in models.items()}


def estimate_cost(prices: Prices, *, provider: str, model: str, usage: dict[str, int | None]) -> float | None:
    rates = prices.get(model)
    fields = ("input_tokens", "output_tokens", "cached_input_tokens", "cache_creation_tokens")
    if rates is None or any(usage.get(field) is None for field in fields):
        return None
    input_tokens, output_tokens, cached, created = (int(usage[field] or 0) for field in fields)
    fresh = input_tokens - cached - created if provider in INCLUSIVE_INPUT_PROVIDERS else input_tokens
    return (
        fresh * rates["input"]
        + output_tokens * rates["output"]
        + cached * rates["cache_read"]
        + created * rates["cache_write"]
    ) / TOKENS_PER_RATE
