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
