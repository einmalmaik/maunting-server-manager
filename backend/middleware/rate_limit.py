import os

from slowapi import Limiter
from slowapi.util import get_remote_address

_storage_uri = os.getenv("REDIS_URL", None)

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["100/minute"],
    storage_uri=_storage_uri,
    in_memory_fallback_enabled=True,
)
