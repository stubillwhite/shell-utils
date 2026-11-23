import pytest

from copilot_metrics.prices import estimate_cost, load_prices

PRICES_TOML = """
[models."claude-opus-5-5"]
input = 10.0
output = 50.0
cache_read = 1.0
cache_write = 12.5

[models."gpt-6-luna"]
input = 2.0
output = 8.0
cache_read = 0.5
cache_write = 2.0
"""

ONE_MILLION = 1_000_000


def tokens(*, input_tokens, output_tokens, cached=0, created=0):
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_input_tokens": cached,
        "cache_creation_tokens": created,
    }


@pytest.fixture
def prices(tmp_path):
    path = tmp_path / "prices.toml"
    path.write_text(PRICES_TOML)
    return load_prices(path)


def test_missing_price_file_means_nothing_is_priced_rather_than_an_error(tmp_path):
    assert load_prices(tmp_path / "absent.toml") == {}


@pytest.mark.parametrize(
    "body",
    [
        '[models."m"]\ninput = 1.0\noutput = 1.0\ncache_read = 1.0\n',
        '[models."m"]\ninput = -1.0\noutput = 1.0\ncache_read = 1.0\ncache_write = 1.0\n',
        '[models."m"]\ninput = "free"\noutput = 1.0\ncache_read = 1.0\ncache_write = 1.0\n',
    ],
)
def test_incomplete_or_invalid_rates_fail_loudly_instead_of_pricing_at_zero(tmp_path, body):
    path = tmp_path / "prices.toml"
    path.write_text(body)
    with pytest.raises(ValueError, match="invalid_prices"):
        load_prices(path)


def test_claude_input_excludes_cache_so_every_category_is_billed_separately(prices):
    cost = estimate_cost(
        prices,
        provider="claude",
        model="claude-opus-5-5",
        usage=tokens(input_tokens=ONE_MILLION, output_tokens=ONE_MILLION, cached=ONE_MILLION, created=ONE_MILLION),
    )
    assert cost == pytest.approx(10.0 + 50.0 + 1.0 + 12.5)


def test_copilot_input_includes_cache_so_cached_tokens_are_not_billed_twice(prices):
    cost = estimate_cost(
        prices,
        provider="copilot",
        model="gpt-6-luna",
        usage=tokens(input_tokens=ONE_MILLION, output_tokens=0, cached=600_000, created=100_000),
    )
    fresh = 300_000
    assert cost == pytest.approx(fresh * 2.0 / ONE_MILLION + 0.6 * 0.5 + 0.1 * 2.0)


def test_unpriced_model_is_unavailable_not_free(prices):
    assert (
        estimate_cost(prices, provider="claude", model="<synthetic>", usage=tokens(input_tokens=5, output_tokens=5))
        is None
    )


def test_any_unobserved_token_field_makes_the_estimate_unavailable(prices):
    usage = {**tokens(input_tokens=5, output_tokens=5), "cache_creation_tokens": None}
    assert estimate_cost(prices, provider="claude", model="claude-opus-5-5", usage=usage) is None
