"""In-Memory-Store fuer Klartext-Webhook-Secrets.

Bewusst klein, threadsicher, nur Prozess-Lifetime. Hintergrund:

- Wir duerfen KEINEN symmetrisch-verschluesselten Secret-Storage bauen
  (Security.md §4 verbietet eigene Krypto).
- Wir brauchen das Klartext-Secret NUR beim Versand, also Prozess-Lifetime
  ist ausreichend — nach Restart rotiert der User entweder manuell oder
  akzeptiert, dass alte Subscriptions erst nach Re-Setup wieder feuern.
- Diese Datei wird vom Outbound-Service UND vom Router (enable/rotate)
  importiert. Es gibt bewusst KEINE Persistenz.

Persistente Speicherung waere ueber Pterodactyl-Wings-Pattern
(env-only) oder Vault moeglich. Out-of-scope fuer Phase 1.
"""
from __future__ import annotations

import threading
from typing import Dict


_LOCK = threading.Lock()
_STORE: Dict[int, str] = {}


def put(subscription_id: int, secret: str) -> None:
    if not secret:
        return
    with _LOCK:
        _STORE[subscription_id] = secret


def get(subscription_id: int) -> str | None:
    with _LOCK:
        return _STORE.get(subscription_id)


def delete(subscription_id: int) -> None:
    with _LOCK:
        _STORE.pop(subscription_id, None)


def reset_for_tests() -> None:
    with _LOCK:
        _STORE.clear()
