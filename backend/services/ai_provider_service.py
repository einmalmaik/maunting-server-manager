"""Provider-Konfiguration: Anbieter, Modell, Schlüssel — ohne Secret-Leaks.

**Hier stand einmal ein SSRF-Apparat.** Rund 150 Zeilen: Adressauflösung,
Politik gegen Link-Local- und Metadata-Ziele, IP-Pinning gegen DNS-Rebinding
und eine zweite Prüfung unmittelbar vor jedem Request. Alles davon war nötig,
solange der Betreiber eine beliebige Basis-URL eintragen konnte — dann ist die
Zieladresse eine Eingabe, und eine Eingabe, die das Panel zu einem HTTP-Aufruf
bewegt, ist eine Angriffsfläche.

Seit der Betreiber einen Anbieter aus `ai_provider_registry` **auswählt**,
stammt die Adresse aus dem Programm. Es gibt keine Eingabe mehr, die auf ein
internes Netz zeigen könnte, und damit nichts mehr zu verteidigen. Der Apparat
ist ersatzlos entfallen; `assert_provider_destination` und
`validate_provider_base_url` gibt es nicht mehr.

Das ist der eigentliche Gewinn des Umbaus und nicht nur ein Nebeneffekt: eine
Schutzmaßnahme, die man löschen kann, weil die Gefahr weg ist, schlägt jede,
die man pflegen muss.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from models import AiProvider
from services import ai_provider_registry
from services.dis_client import DisClient


class AiProviderConfigurationError(ValueError):
    """Die Provider-Konfiguration ist nicht schlüssig."""


def base_url(provider: AiProvider) -> str:
    """Die Adresse dieses Providers — aus der Registry, nicht aus der Zeile.

    Eine eigene Funktion statt einer Spalte, damit es genau **eine** Wahrheit
    gibt. Als gespeicherter Wert wäre sie eine Kopie, die nach einer Änderung
    an der Registry still veraltet: der Anbieter zieht auf einen neuen Pfad um,
    das Programm weiß es, die Datenbank nicht.
    """
    return ai_provider_registry.anbieter(provider.provider_kind).base_url


def _hint(secret: str) -> str:
    return "********" + secret[-4:] if len(secret) >= 4 else "********"


def _operator_aad(provider_id: int) -> str:
    return f"msm:ai:provider:{provider_id}:operator-key"


def _assert_kind(kind: str) -> str:
    normalized = (kind or "").strip()
    if not ai_provider_registry.bekannt(normalized):
        raise AiProviderConfigurationError("Unbekannter KI-Anbieter")
    return normalized


def _assert_key_passt(kind: str, api_key: str | None) -> None:
    """Prüft den Schlüssel gegen das Präfix des Anbieters, falls er eines hat.

    Bewusst nur eine Plausibilitätsprüfung: ein Schlüssel mit falschem Präfix
    ist mit Sicherheit falsch, einer mit richtigem damit noch lange nicht
    gültig. Sie erspart dem Betreiber den Umweg über eine Fehlermeldung des
    Anbieters — der echte Testaufruf bleibt der Beweis.
    """
    if not api_key:
        return
    praefix = ai_provider_registry.anbieter(kind).key_prefix
    if praefix and not api_key.startswith(praefix):
        raise AiProviderConfigurationError(
            f"Der Schlüssel dieses Anbieters beginnt mit „{praefix}“"
        )


def create_provider(
    db: Session,
    *,
    name: str,
    provider_kind: str,
    default_model: str,
    enabled: bool,
    requires_api_key: bool,
    operator_api_key: str | None,
    # Optional: ohne Preis bleiben die Kosten bei null (siehe estimate_cost_microunits).
    token_price_micro_usd_per_million: int | None = None,
) -> AiProvider:
    if not name.strip() or not default_model.strip():
        raise AiProviderConfigurationError("Provider-Name und Modell dürfen nicht leer sein")
    kind = _assert_kind(provider_kind)
    _assert_key_passt(kind, operator_api_key)
    provider = AiProvider(
        name=name.strip(),
        provider_kind=kind,
        default_model=default_model.strip(),
        enabled=enabled,
        requires_api_key=requires_api_key,
        token_price_micro_usd_per_million=token_price_micro_usd_per_million,
    )
    db.add(provider)
    db.flush()
    if operator_api_key:
        provider.operator_api_key_encrypted = DisClient.encrypt(
            operator_api_key, aad=_operator_aad(provider.id)
        )
        provider.operator_api_key_hint = _hint(operator_api_key)
    db.flush()
    return provider


def update_provider(
    db: Session,
    provider: AiProvider,
    *,
    values: dict,
    operator_api_key: str | None,
    clear_operator_api_key: bool,
) -> AiProvider:
    if any(field in values and not str(values[field]).strip() for field in ("name", "default_model")):
        raise AiProviderConfigurationError("Provider-Name und Modell dürfen nicht leer sein")
    if "provider_kind" in values:
        provider.provider_kind = _assert_kind(values["provider_kind"])
    _assert_key_passt(provider.provider_kind, operator_api_key)
    for field in ("name", "default_model"):
        if field in values:
            setattr(provider, field, values[field].strip())
    for field in ("enabled", "requires_api_key"):
        if field in values:
            setattr(provider, field, values[field])
    # Ein Zugang, dessen Anbieter MSM nicht kennt, darf nicht aktiv werden.
    # Der Fall entsteht durch die Migration 20260811_01: sie parkt alles, was
    # nicht zu einem unterstützten Anbieter gehörte, mit leerem Schlüssel. Ohne
    # diese Prüfung genügte ein Haken bei „aktiv“, und der nächste Chat liefe
    # in einen `KeyError` aus `base_url()` — ein 500 statt einer Erklärung.
    if provider.enabled and not ai_provider_registry.bekannt(provider.provider_kind):
        raise AiProviderConfigurationError(
            "Für diesen Zugang ist kein unterstützter Anbieter hinterlegt. "
            "Wähle einen Anbieter aus, bevor du ihn aktivierst."
        )
    if "token_price_micro_usd_per_million" in values:
        provider.token_price_micro_usd_per_million = values[
            "token_price_micro_usd_per_million"
        ]
    if clear_operator_api_key:
        provider.operator_api_key_encrypted = None
        provider.operator_api_key_hint = None
    elif operator_api_key:
        provider.operator_api_key_encrypted = DisClient.encrypt(
            operator_api_key, aad=_operator_aad(provider.id)
        )
        provider.operator_api_key_hint = _hint(operator_api_key)
    db.flush()
    return provider


def resolve_api_key(db: Session, provider: AiProvider, user_id: int) -> str | None:
    """Der Schlüssel des Betreibers, im Klartext nur an den Adapter.

    Hier stand einmal BYOK: ein Benutzerschlüssel wurde **vor** dem des
    Betreibers genommen. Das ist entfallen — der Betreiber stellt Schlüssel,
    Modell und Anbieter. Ein eigener Nutzerschlüssel wäre ein zweiter
    Abrechnungspfad neben dem, den der Betreiber kalkuliert hat.

    ``db`` und ``user_id`` bleiben in der Signatur, obwohl sie nicht mehr
    gebraucht werden: alle Aufrufer reichen sie durch, und sie wegzunehmen wäre
    eine Änderung an jedem Aufrufpfad für einen kosmetischen Gewinn. Sollte
    der Betreiber je Schlüssel je Rolle oder je Team einführen, sind sie
    genau die Angaben, die es dafür braucht.
    """
    del db, user_id
    if provider.operator_api_key_encrypted:
        return DisClient.decrypt(
            provider.operator_api_key_encrypted, aad=_operator_aad(provider.id)
        )
    return None


def estimate_cost_microunits(provider: AiProvider, tokens: int) -> int:
    """Rechnet Tokens mit dem gepflegten Preis in Kosten um — die Rückfallebene.

    Gebucht wird normalerweise, was der Anbieter meldet: OpenRouter schickt in
    der letzten Zeile jedes Streams den Betrag, der dem Konto tatsächlich
    belastet wurde, und der ist genauer als jede Nachrechnung. Diese Funktion
    greift nur, wenn er schweigt — und für die Reservierung *vor* dem Aufruf,
    wo es die echte Zahl naturgemäß noch nicht gibt.

    Sie bleibt dabei eine grobe Näherung, und das ist keine Nachlässigkeit,
    sondern die Grenze der Eingabe: **ein** Preis auf **alle** Tokens. Eingabe
    und Ausgabe kosten bei den meisten Modellen unterschiedlich viel, oft um
    das Fünffache, und eine Eingabe aus dem Zwischenspeicher nochmal rund ein
    Zehntel. Wer ein genaues Ergebnis braucht, braucht die Anbieterzahl — die
    Oberfläche markiert Zeilen aus dieser Funktion deshalb als geschätzt.

    Ohne gepflegten Preis bleibt das Ergebnis null; MSM erfindet keinen Preis.
    Die Rechnung läuft ganzzahlig: Preis wie Ergebnis stehen in Microunits
    (1 US-Cent = 10.000), der Preis gilt je eine Million Tokens.
    """
    price = provider.token_price_micro_usd_per_million
    if not price or tokens <= 0:
        return 0
    return (int(tokens) * int(price)) // 1_000_000
