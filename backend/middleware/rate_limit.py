from slowapi import Limiter
from slowapi.util import get_remote_address

from config import settings

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["100/minute"],
    storage_uri=settings.redis_url or None,
    in_memory_fallback_enabled=True,
)
