from __future__ import annotations

from services import ai_model_price_service as prices


def test_missing_prices_are_taken_from_public_catalog_without_overwriting_manual_values(monkeypatch) -> None:
    monkeypatch.setattr(prices, "_catalog", lambda: {
        "openai/gpt-test": (1_250_000, 10_000_000),
    })
    values = {
        "default_model": "gpt-test",
        "worker_model": None,
        "ethics_model": None,
        "standard_input_price_micro_usd_per_million": None,
        "standard_output_price_micro_usd_per_million": 9_999,
        "worker_input_price_micro_usd_per_million": None,
        "worker_output_price_micro_usd_per_million": None,
        "ethics_input_price_micro_usd_per_million": None,
        "ethics_output_price_micro_usd_per_million": None,
    }

    prices.fill_missing_role_prices("openai", values)

    assert values["standard_input_price_micro_usd_per_million"] == 1_250_000
    assert values["standard_output_price_micro_usd_per_million"] == 9_999


def test_price_conversion_uses_the_same_unit_as_provider_costs() -> None:
    assert prices._micro_usd_per_million("0.000003") == 3_000_000
    assert prices._micro_usd_per_million("not-a-price") is None


def test_the_memory_role_gets_its_prices_like_the_others(monkeypatch) -> None:
    """Gedächtnis v2, Stufe 3: der vierte Platz, dieselbe Automatik."""
    monkeypatch.setattr(prices, "_catalog", lambda: {
        "openai/gpt-merk": (150_000, 600_000, 75_000),
    })
    values = {
        "default_model": None,
        "memory_model": " gpt-merk ",
        "memory_input_price_micro_usd_per_million": None,
        "memory_output_price_micro_usd_per_million": 1,
        "memory_cache_price_micro_usd_per_million": None,
    }

    prices.fill_missing_role_prices("openai", values)

    assert (
        values["memory_input_price_micro_usd_per_million"],
        values["memory_output_price_micro_usd_per_million"],
        values["memory_cache_price_micro_usd_per_million"],
    ) == (150_000, 1, 75_000)
