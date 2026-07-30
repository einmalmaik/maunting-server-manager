"""Globales API-Rate-Limit (slowapi Limiter).

Das Default-Limit pro IP kommt dynamisch aus panel_settings
(rate_limit_global), Default 100/minute. Auth-spezifische Limits
liegen separat in main.auth_rate_limit. Enrollment- und Webhook-Limits
bleiben fest in den jeweiligen Routern.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

from config import settings
from services.rate_limit_settings import dynamic_global_limit_provider

# default_limits als Callable: slowapi wertet den String pro Request neu aus,
# sodass gespeicherte Admin-Werte ohne Restart greifen. Bei Lesefehler/ungültig
# liefert dynamic_global_limit_provider den Default 100 (fail-closed).
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[dynamic_global_limit_provider],
    storage_uri=settings.redis_url or None,
    in_memory_fallback_enabled=True,
)
