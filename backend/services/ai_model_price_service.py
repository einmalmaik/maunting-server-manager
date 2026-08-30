"""Preisauflösung für die drei textbasierten Modellrollen.

OpenRouter veröffentlicht Tokenpreise im öffentlichen Modellkatalog. Für
OpenAI und Azure wird derselbe Katalog nur für OpenAI-Modellkennungen befragt;
Azure-Deployments mit eigenen Namen bleiben absichtlich unangetastet. Ein
manuell gepflegter Preis hat immer Vorrang.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import threading
import time
from typing import Any

import httpx


_URL = "https://openrouter.ai/api/v1/models"
_TTL_SECONDS = 3600
_lock = threading.Lock()
_cache: tuple[float, dict[str, tuple[int, int]]] | None = None


def _micro_usd_per_million(value: object) -> int | None:
    """Wandelt OpenRouters USD-pro-Token-Angabe in die MSM-Speichereinheit."""
    if not isinstance(value, str):
        return None
    try:
        amount = Decimal(value)
    except InvalidOperation:
        return None
    if amount < 0:
        return None
    # USD/Token → Cent-Microunits/Mio.-Tokens
    return int((amount * Decimal("1000000000000")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _catalog() -> dict[str, tuple[int, int, int | None]]:
    global _cache
    with _lock:
        if _cache is not None and _cache[0] > time.monotonic():
            return _cache[1]  # type: ignore[return-value]
    try:
        response = httpx.get(_URL, timeout=2.0, headers={"Accept": "application/json"})
        response.raise_for_status()
        entries = response.json().get("data", [])
    except (httpx.HTTPError, ValueError, AttributeError):
        return {}
    prices: dict[str, tuple[int, int, int | None]] = {}
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
                continue
            pricing = entry.get("pricing")
            if not isinstance(pricing, dict):
                continue
            input_price = _micro_usd_per_million(pricing.get("prompt"))
            output_price = _micro_usd_per_million(pricing.get("completion"))
            cache_price = _micro_usd_per_million(pricing.get("input_cache_read") or pricing.get("cached"))
            if input_price is not None and output_price is not None:
                prices[entry["id"]] = (input_price, output_price, cache_price)
    with _lock:
        _cache = (time.monotonic() + _TTL_SECONDS, prices)  # type: ignore[assignment]
    return prices


def fill_missing_role_prices(provider_kind: str, values: dict[str, Any]) -> None:
    """Füllt leere Preise aus dem öffentlichen Katalog, ohne Saves zu blockieren.

    Kein Katalogtreffer und Netzwerkfehler sind regulär: manuelle Felder und
    der bestehende Rückfallpreis bleiben unverändert.
    """
    catalog = _catalog()
    for role, model_field in (
        ("standard", "default_model"),
        ("worker", "worker_model"),
        ("ethics", "ethics_model"),
    ):
        input_field = f"{role}_input_price_micro_usd_per_million"
        output_field = f"{role}_output_price_micro_usd_per_million"
        cache_field = f"{role}_cache_price_micro_usd_per_million"
        if values.get(input_field) is not None and values.get(output_field) is not None and values.get(cache_field) is not None:
            continue
        model = values.get(model_field)
        if not isinstance(model, str) or not model.strip():
            continue
        model_id = model.strip()
        if provider_kind in {"openai", "azure_openai"} and not model_id.startswith("openai/"):
            model_id = f"openai/{model_id}"
        price = catalog.get(model_id)
        if price is None:
            continue
        if values.get(input_field) is None:
            values[input_field] = price[0]
        if values.get(output_field) is None:
            values[output_field] = price[1]
        if values.get(cache_field) is None and len(price) > 2 and price[2] is not None:
            values[cache_field] = price[2]
