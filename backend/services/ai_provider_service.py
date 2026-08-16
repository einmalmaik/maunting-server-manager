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

from models import AiProvider, AiRun, User
import re

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


def spricht(provider: AiProvider, protokoll: str) -> bool:
    """Spricht der Anbieter dieses Zugangs das verlangte Protokoll?

    Steht hier und nicht in den Routern, aus demselben Grund wie `base_url()`
    direkt darüber: welche API hinter einem Zugang steckt, ist eine Eigenschaft
    des Anbieters und keine der Zeile. Ein Router, der dafür `provider_kind`
    auswertet, hätte die Registry ein zweites Mal im Kopf.

    Gebraucht wird das an jeder Stelle, die einen Zugang **entgegennimmt**: der
    Chat verlangt ``CHAT``, die Stimme ``TTS``. Ohne die Prüfung liefe ein
    Stimmzugang im Chat gegen ``https://api.elevenlabs.io/v1/chat/completions``
    — eine Adresse, die es nicht gibt, mit einem Schlüssel, der in den falschen
    Kopf geschrieben würde. Der Benutzer sähe einen Anbieterfehler statt einer
    Erklärung.
    """
    return ai_provider_registry.spricht(provider.provider_kind, protokoll)


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


#: Woraus eine Stimm-Kennung bestehen darf — dieselbe Menge wie in
#: `schemas.ai_provider`, und aus demselben Grund: der Wert wird in einen
#: **URL-Pfad** eingesetzt (``/v1/text-to-speech/{voice}/stream-input``). Ein
#: ``/`` darin wäre ein anderer Endpunkt, ein ``?`` ein angehängter Parameter.
_STIMME_MUSTER = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _assert_stimme(stimme: str | None) -> str | None:
    """Nur eine Stimm-Kennung von zulässiger Form — oder gar keine.

    Die verbindliche Prüfung, obwohl `schemas.ai_provider.Stimme` dasselbe schon
    tut. Der Vertrag dort verschafft dem Betreiber eine 422 mit Feldbezug statt
    einer Meldung ohne Ort; hier steht sie, weil nicht jeder Schreibweg durch
    ihn führt — ein Seed, ein Test, ein späterer Importweg schreiben direkt in
    diese Funktion. Es ist damit **eine Sicherheitsprüfung an zwei Stellen**
    und keine doppelte Kosmetik: die eine sichert das Formular, die andere die
    Funktion.

    Geprüft wird die Form und nicht die Existenz. Anders als bei den acht
    Stimmen, die hier bis zum 16.08.2026 standen, gibt es keine Liste: die
    Kennungen gehören dem Konto des Betreibers, und MSM kennt sie nicht. Ob es
    die Stimme wirklich gibt, sagt der Testknopf.

    Ausdrücklich **nicht** kleingeschrieben: ``21m00Tcm4TlvDq8ikWAM`` ist
    gross- und kleinempfindlich, und ein ``.lower()`` hätte hier jede zweite
    Kennung unbrauchbar gemacht.

    ``None`` bleibt ``None``. Eine Standardstimme gibt es nicht und soll es
    nicht geben — ohne hinterlegte Stimme gibt es keinen Sprachmodus, statt
    einer geratenen Stimme auf der Rechnung des Betreibers.
    """
    if stimme is None:
        return None
    kennung = stimme.strip()
    if not kennung:
        return None
    if not _STIMME_MUSTER.match(kennung):
        raise AiProviderConfigurationError(
            "Ungültige Stimm-Kennung. Erlaubt sind Buchstaben, Ziffern, "
            "Bindestrich und Unterstrich (höchstens 64 Zeichen)."
        )
    return kennung


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
    # Optional: ohne Stimme gibt es über diesen Zugang keinen Sprachmodus. Eine
    # Standardstimme wird bewusst **nicht** eingetragen — warum, steht an der
    # Spalte in `models/ai_provider.py`.
    default_voice: str | None = None,
    # Optional: das hörende Modell eines Chatzugangs. Ohne es gibt es über
    # diesen Zugang ebenfalls keinen Sprachmodus.
    transcription_model: str | None = None,
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
        default_voice=_assert_stimme(default_voice),
        transcription_model=(transcription_model or "").strip() or None,
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
    # Wie beim Preis darüber entscheidet die **Anwesenheit** des Schlüssels und
    # nicht sein Wert: `values` kommt aus `model_dump(exclude_unset=True)`, ein
    # nicht mitgeschicktes Feld fehlt also ganz. Ein ausdrückliches ``null``
    # landet dagegen hier und leert die Spalte — über den Zugang lässt sich
    # danach nicht mehr sprechen. Nicht bei `name`/`default_model` oben, denn
    # die dürfen nicht leer werden; diese beiden Spalten schon, und leer heißt
    # hier etwas.
    if "default_voice" in values:
        provider.default_voice = _assert_stimme(values["default_voice"])
    if "transcription_model" in values:
        rohwert = values["transcription_model"]
        provider.transcription_model = (rohwert or "").strip() or None
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


def anbieter_ohne_auswahl(db: Session, user: User) -> AiProvider | None:
    """Welcher Anbieter, wenn niemand einen aussucht?

    Im Chat kommt er aus der Anfrage. In einem Lauf, den die Uhr oder die
    Guardian-Engine ausloest, gibt es keine — und `AiProvider` hat kein
    `is_default`. Eines zu erfinden waere eine neue Einstellung fuer ein
    Problem, das die Daten schon beantworten: der Anbieter, den dieser Benutzer
    zuletzt tatsaechlich benutzt hat. Das ist keine Vermutung ueber seine
    Vorlieben, sondern seine juengste Entscheidung.

    Gibt es keinen solchen, der einzige aktivierte. Gibt es mehrere und keinen
    juengsten, wird **nicht** geraten — dann laeuft nichts, und der Benutzer
    erfaehrt beim naechsten Chat davon. Ein zufaellig gewaehltes Modell koennte
    teurer sein, als er wollte.

    Steht hier und nicht bei der Guardian-Engine, seit es den zweiten Ausloeser
    ohne Zuschauer gibt. Zwei Kopien dieser Regel waeren zwei Antworten auf
    dieselbe Frage — und die zweite haette irgendwann geraten, wo die erste
    schweigt.
    """
    letzter = (
        db.query(AiRun)
        .filter(AiRun.user_id == user.id, AiRun.provider_id.isnot(None))
        .order_by(AiRun.created_at.desc())
        .first()
    )
    if letzter is not None:
        anbieter = db.get(AiProvider, letzter.provider_id)
        if anbieter is not None and anbieter.enabled:
            return anbieter

    aktive = db.query(AiProvider).filter(AiProvider.enabled.is_(True)).order_by(AiProvider.id).all()
    return aktive[0] if len(aktive) == 1 else None


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
