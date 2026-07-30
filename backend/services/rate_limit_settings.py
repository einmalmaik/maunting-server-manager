"""Gemeinsame Regeln für konfigurierbare Panel-Rate-Limits.

Auth- und globales API-Limit werden als Key-Value in panel_settings
gespeichert (rate_limit_auth, rate_limit_global). Limiters und der
Settings-Router teilen sich dieselben Defaults und erlaubten Bereiche,
damit UI, Persistenz und Durchsetzung nie auseinanderlaufen.

Warum ein reines Hilfsmodul: Validierung und Fallback ohne DB-I/O
bleiben unit-testbar und können von middleware, main und Router
importiert werden, ohne zyklische Abhängigkeiten.
"""

from __future__ import annotations

from typing import Any

# Persistenz-Keys (panel_settings.key)
KEY_AUTH = "rate_limit_auth"
KEY_GLOBAL = "rate_limit_global"

# Dokumentierte Defaults — bei fehlendem/ungültigem Wert immer diese nutzen
DEFAULT_AUTH = 10
DEFAULT_GLOBAL = 100

# Server-seitig erzwungene erlaubte Bereiche
AUTH_MIN = 3
AUTH_MAX = 50
GLOBAL_MIN = 50
GLOBAL_MAX = 1000


def _to_int(value: Any, field_name: str) -> int:
    """Wandelt einen Eingabewert in int um; lehnt Bool und Nicht-Ganzzahlen ab.

    Warum: bool ist in Python eine int-Unterklasse (True==1); ohne Abweisung
    würden True/False fälschlich als 1/0 akzeptiert. Floats wie 10.5
    werden abgelehnt, weil Rate-Limits nur ganze Anfragen pro Minute sind.
    """
    if value is None:
        raise ValueError(f"{field_name} darf nicht leer sein")
    if isinstance(value, bool):
        raise ValueError(f"{field_name} muss eine ganze Zahl sein")
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"{field_name} muss eine ganze Zahl sein")
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        raw = value.strip()
        if raw == "":
            raise ValueError(f"{field_name} darf nicht leer sein")
        # Keine Floats in Strings (\"10.5\") und keine führenden Plus-Zeichen nötig
        if raw.startswith("-"):
            # Negative Limits sind sinnlos und werden über den Range abgelehnt;
            # hier erst parsen, damit die Range-Meldung greift.
            pass
        try:
            # int() akzeptiert \"10\" und \"-1\"; lehnt \"10.5\" und \"abc\" ab
            if "." in raw or "e" in raw.lower():
                raise ValueError(f"{field_name} muss eine ganze Zahl sein")
            return int(raw, 10)
        except ValueError as exc:
            raise ValueError(f"{field_name} muss eine ganze Zahl sein") from exc
    raise ValueError(f"{field_name} muss eine ganze Zahl sein")


def validate_auth_limit(value: Any) -> int:
    """Prüft Login/Auth-Limit für Writes; liefert den gültigen Integer.

    Erlaubter Bereich: 3–50 Anfragen pro Minute pro IP.
    Wirft ValueError mit verständlicher deutscher Meldung bei Fehlern.
    """
    n = _to_int(value, KEY_AUTH)
    if n < AUTH_MIN or n > AUTH_MAX:
        raise ValueError(
            f"{KEY_AUTH} muss zwischen {AUTH_MIN} und {AUTH_MAX} liegen"
        )
    return n


def validate_global_limit(value: Any) -> int:
    """Prüft globales API-Limit für Writes; liefert den gültigen Integer.

    Erlaubter Bereich: 50–1000 Anfragen pro Minute pro IP.
    Wirft ValueError mit verständlicher deutscher Meldung bei Fehlern.
    """
    n = _to_int(value, KEY_GLOBAL)
    if n < GLOBAL_MIN or n > GLOBAL_MAX:
        raise ValueError(
            f"{KEY_GLOBAL} muss zwischen {GLOBAL_MIN} und {GLOBAL_MAX} liegen"
        )
    return n


def resolve_auth_limit(raw: Any) -> int:
    """Liefert ein sicheres Auth-Limit für die Laufzeit-Durchsetzung.

    Leere, fehlende oder ungültige Werte fallen auf DEFAULT_AUTH (10)
    zurück — nie unlimitiert, nie außerhalb des erlaubten Bereichs.
    """
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        return DEFAULT_AUTH
    try:
        return validate_auth_limit(raw)
    except ValueError:
        return DEFAULT_AUTH


def resolve_global_limit(raw: Any) -> int:
    """Liefert ein sicheres globales API-Limit für die Laufzeit-Durchsetzung.

    Leere, fehlende oder ungültige Werte fallen auf DEFAULT_GLOBAL (100)
    zurück — nie unlimitiert, nie außerhalb des erlaubten Bereichs.
    """
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        return DEFAULT_GLOBAL
    try:
        return validate_global_limit(raw)
    except ValueError:
        return DEFAULT_GLOBAL


def auth_limit_string(raw: Any = None) -> str:
    """slowapi/limits-kompatibler String für das Auth-Limit (z. B. '10/minute')."""
    return f"{resolve_auth_limit(raw)}/minute"


def global_limit_string(raw: Any = None) -> str:
    """slowapi/limits-kompatibler String für das globale Limit."""
    return f"{resolve_global_limit(raw)}/minute"


def current_auth_limit_from_settings() -> int:
    """Liest rate_limit_auth aus dem PanelSettings-Cache und resolved sicher.

    Separat von resolve_*, damit Limiters nur diesen Einstieg nutzen und
    DB-Import hier gekapselt bleibt (vermeidet enge Kopplung im pure helper).
    """
    from services.panel_settings_service import PanelSettingsService

    return resolve_auth_limit(PanelSettingsService.get(KEY_AUTH, ""))


def current_global_limit_from_settings() -> int:
    """Liest rate_limit_global aus dem PanelSettings-Cache und resolved sicher."""
    from services.panel_settings_service import PanelSettingsService

    return resolve_global_limit(PanelSettingsService.get(KEY_GLOBAL, ""))


def dynamic_global_limit_provider() -> str:
    """Callable für slowapi default_limits — wird pro Request neu ausgewertet.

    Warum Callable statt fester String: Admin-Änderungen greifen ohne
    Prozess-Neustart, sobald der PanelSettings-Cache den neuen Wert hat.
    """
    return f"{current_global_limit_from_settings()}/minute"
