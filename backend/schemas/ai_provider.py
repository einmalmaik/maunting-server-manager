"""Secret-minimierte API-Vertraege fuer AI-Provider."""

import re
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, Field, SecretStr


# Obergrenze fuer den gepflegten Preis: 1.000 USD je eine Million Tokens, in
# Microunits (1 US-Cent = 10.000). Das teuerste Modell bei OpenRouter liegt bei
# rund 75 USD — die Grenze faengt also Tippfehler ab, ohne einer echten
# Preisliste im Weg zu stehen.
MAX_TOKEN_PRICE_MICRO_USD = 1_000_000_000


#: Woraus eine Stimm-Kennung bestehen darf. Bewusst eng, und zwar nicht aus
#: Ordnungsliebe: der Wert wird in einen **URL-Pfad** eingesetzt
#: (``/v1/text-to-speech/{voice}/stream-input``). Ein ``/`` darin waere ein
#: anderer Endpunkt, ein ``?`` ein angehaengter Parameter, ein ``..`` ein Schritt
#: nach oben. Die bekannten Kennungen sind zwanzig Zeichen aus Buchstaben und
#: Ziffern; diese Menge laesst zusaetzlich ``-`` und ``_`` zu und damit alles,
#: was ElevenLabs plausibel je vergeben wird, aber nichts, was den Pfad verlaesst.
_STIMME_MUSTER = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _stimme_lesen(wert: object) -> str | None:
    """Nachsichtig lesen, streng speichern.

    Nachsichtig beim Leeren: ein Formularfeld ohne Eingabe schickt ``""`` und
    nicht ``null`` — das ist der Weg, auf dem „nichts hinterlegt" hier ankommt.
    Ein 422 dafuer waere eine Belehrung ohne Anlass.

    Nachsichtig auch bei Rand-Leerzeichen: eine Stimm-Kennung wird kopiert und
    eingefuegt, und beim Kopieren kommt regelmaessig ein Leerzeichen mit. Es
    stillschweigend zu entfernen ist richtig; es stehenzulassen ergaebe ein 404
    bei der Gegenstelle, das nach einem falschen Schluessel aussieht.

    Streng bei allem anderen. Anders als bei den acht Stimmen davor gibt es hier
    **keine Liste**, gegen die sich pruefen liesse: die Kennungen gehoeren dem
    Konto des Betreibers, und MSM kennt sie nicht. Geprueft wird deshalb die
    Form — und die Form ist hier eine Sicherheitsfrage und keine Kosmetik, weil
    der Wert in einen Pfad geht.

    Ausdruecklich **nicht** kleingeschrieben, anders als die Stimmen zuvor:
    ``21m00Tcm4TlvDq8ikWAM`` ist gross- und kleinempfindlich. Ein ``.lower()``
    hier haette jede zweite Kennung unbrauchbar gemacht.
    """
    if wert is None:
        return None
    kennung = str(wert).strip()
    if not kennung:
        return None
    if not _STIMME_MUSTER.match(kennung):
        raise ValueError(
            "Ungueltige Stimm-Kennung. Erlaubt sind Buchstaben, Ziffern, "
            "Bindestrich und Unterstrich (hoechstens 64 Zeichen)."
        )
    return kennung


def _modell_lesen(wert: object) -> str | None:
    """Wie `_stimme_lesen`, aber fuer eine Modellkennung.

    Eigene Funktion und kein gemeinsamer Validator: eine Modellkennung darf
    ``/`` enthalten (``google/gemini-2.5-flash``) und eine Stimm-Kennung darf es
    ausdruecklich nicht. Dieselbe Pruefung fuer beide waere entweder zu eng fuer
    das Modell oder zu weit fuer die Stimme — und „zu weit fuer die Stimme"
    heisst hier: ein Pfad, der woandershin zeigt.

    Geprueft wird nur auf leer. Welche Modelle es gibt, sagt der Katalog, und
    der wird beim Speichern nicht befragt: der Betreiber soll ein Modell
    eintragen koennen, das der Katalog gerade nicht fuehrt, ohne dass die
    Einstellungsseite ihn belehrt.
    """
    if wert is None:
        return None
    kennung = str(wert).strip()
    return kennung or None


#: Eine Stimm-Kennung aus dem Konto des Betreibers, oder ``None`` fuer „nichts
#: hinterlegt". Ohne sie gibt es keinen Sprachmodus — nie eine geratene Stimme,
#: denn sie stuende in seiner Abrechnung.
Stimme = Annotated[str | None, BeforeValidator(_stimme_lesen)]

#: Eine Modellkennung, oder ``None`` fuer „nichts hinterlegt".
Modellkennung = Annotated[str | None, BeforeValidator(_modell_lesen)]


class AiProviderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    # Schluessel aus `services/ai_provider_registry.ANBIETER`. Die Adresse
    # dahinter gehoert dem Programm — hier steht nur noch, *welcher* Anbieter.
    provider_kind: str = Field(min_length=1, max_length=32)
    default_model: Modellkennung = Field(default=None, max_length=256)
    enabled: bool = True
    requires_api_key: bool = True
    # Rueckfallpreis je eine Million Tokens, in US-Cent-Microunits (1 Cent =
    # 10.000). Gebraucht wird er nur, wenn der Anbieter selbst keine Kosten
    # meldet; ``None`` heisst dann: keine belastbare Preisquelle, Kosten werden
    # mit null verbucht. Die Oberflaeche nimmt hier eine Dezimalzahl in der
    # Anzeigewaehrung entgegen und rechnet um — die Schnittstelle sieht nur die
    # ganze Zahl.
    token_price_micro_usd_per_million: int | None = Field(
        default=None, ge=0, le=MAX_TOKEN_PRICE_MICRO_USD
    )
    # Nur fuer einen Sprachzugang von Bedeutung; bei einem Chatzugang bleibt das
    # Feld schlicht leer. Es wird trotzdem nicht gegen `provider_kind` geprueft:
    # der Anbieter laesst sich spaeter aendern, und eine hinterlegte Stimme, die
    # dabei stillschweigend haette geloescht werden muessen, waere aergerlicher
    # als eine, die ungenutzt dasteht.
    default_voice: Stimme = None
    # Das Gegenstueck zur Stimme, und zwar am **anderen** Zugang: hier steht das
    # hoerende Modell des Chatzugangs. Auch dieses Feld wird nicht gegen
    # `provider_kind` geprueft, aus demselben Grund wie die Stimme darueber.
    transcription_model: Modellkennung = Field(default=None, max_length=256)
    operator_api_key: SecretStr | None = Field(default=None, min_length=1, max_length=4096)


class AiProviderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    provider_kind: str | None = Field(default=None, min_length=1, max_length=32)
    default_model: Modellkennung = Field(default=None, max_length=256)
    enabled: bool | None = None
    requires_api_key: bool | None = None
    # Siehe `AiProviderCreate`: Rueckfallpreis in US-Cent-Microunits je Million.
    token_price_micro_usd_per_million: int | None = Field(
        default=None, ge=0, le=MAX_TOKEN_PRICE_MICRO_USD
    )
    # Wie beim Preis eine Zeile darueber gibt es hier zwei verschiedene Dinge,
    # die beide wie ``None`` aussehen: „nicht mitgeschickt" laesst die Stimme
    # stehen, ein ausdrueckliches ``null`` loescht sie. Auseinander haelt die
    # beiden nicht dieser Vertrag, sondern `model_dump(exclude_unset=True)` im
    # Router — deshalb braucht dieses Feld kein eigenes ``clear_``-Flag, anders
    # als der Schluessel darunter, den die Antwort nie zurueckgibt.
    default_voice: Stimme = None
    transcription_model: Modellkennung = Field(default=None, max_length=256)
    operator_api_key: SecretStr | None = Field(default=None, min_length=1, max_length=4096)
    clear_operator_api_key: bool = False


class AiProviderResponse(BaseModel):
    id: int
    name: str
    provider_kind: str
    # Aus der Registry abgeleitet, nicht gespeichert. Steht in der Antwort,
    # damit die Oberfläche zeigen kann, wohin die Anfragen gehen. ``None``
    # bei einer geparkten Zeile, deren Anbieter diese Version nicht kennt —
    # sie soll trotzdem in der Liste erscheinen.
    base_url: str | None = None
    default_model: str | None = None
    #: Bewusst ohne die Pruefung aus `Stimme`: gelesen wird hier, was in der
    #: Zeile steht. Loescht der Betreiber eine Stimme in seinem Konto, soll er
    #: sie in der Liste sehen und aendern koennen — eine 500 beim blossen
    #: Anzeigen waere die schlechteste Art, ihm das mitzuteilen. Und genau das
    #: waere passiert, als am 2026-08-16 aus ``alloy`` eine Kennung wurde: jede
    #: Bestandszeile haette die Einstellungsseite zerlegt.
    #: ``None`` heisst „nichts hinterlegt".
    default_voice: str | None = None
    #: Wie `default_voice` ungeprueft gelesen, aus demselben Grund.
    transcription_model: str | None = None
    enabled: bool
    requires_api_key: bool
    operator_key_configured: bool
    operator_key_hint: str | None
    token_price_micro_usd_per_million: int | None
    updated_at: datetime


class AiProviderKindResponse(BaseModel):
    """Ein von MSM unterstuetzter Anbieter — die Auswahl im Einrichtungsformular."""

    kind: str
    label: str
    base_url: str
    key_url: str
    key_prefix: str | None
    #: ``chat_completions`` oder ``tts``. Steht hier, weil das Formular sonst
    #: zwei Zugaenge anbietet, die verschiedene Dinge tun, und nichts verraet
    #: welchen wofuer. Ein Stimmzugang taucht in der Modellauswahl des Chats
    #: spaeter gar nicht auf — das soll der Betreiber beim Anlegen wissen und
    #: nicht danach suchen muessen. Umgekehrt entscheidet dieses Feld auch,
    #: welche Zusatzfelder das Formular zeigt: die Stimme beim einen, das
    #: hoerende Modell beim anderen.
    protokoll: str
    #: Ob der Modellkatalog dieses Anbieters den Betreiberschluessel braucht.
    #: Die Oberflaeche erklaert damit die leere Modelliste, statt sie als
    #: Stoerung aussehen zu lassen: erst Schluessel speichern, dann Modell
    #: waehlen.
    katalog_braucht_schluessel: bool


class AiCatalogModelResponse(BaseModel):
    """Ein Modell aus dem Katalog des Anbieters, mit seinen Denkfaehigkeiten.

    ``efforts`` sind die Stufen, die dieses Modell **wirklich** kennt — direkt
    aus dem Katalog. Eine leere Liste heisst nicht "denkt nicht", sondern "kennt
    keine Stufen": gemessen koennen 145 der 272 denkenden Modelle bei OpenRouter
    nur an oder aus.

    ``recommended`` ist die einzige Angabe hier, die **nicht** vom Anbieter
    stammt, sondern von MSM: das Modell, mit dem das Panel erprobt ist. Es
    steht am Modell und nicht neben der Liste, damit die Oberflaeche es dort
    zeigen kann, wo gewaehlt wird — und damit es verschwindet, wenn der Katalog
    diese Kennung nicht mehr fuehrt.
    """

    model_id: str
    name: str
    reasoning: bool
    efforts: list[str]
    default_effort: str | None
    mandatory: bool
    recommended: bool = False


class AiProviderAvailableResponse(BaseModel):
    """Ein Provider, wie ihn der Chat zur Auswahl anbietet.

    Kein ``user_key_configured`` mehr: seit der Betreiber die Schluessel stellt,
    haengt ``available`` allein an seinem Schluessel, und der Benutzer hat
    nichts, was er hier beitragen koennte.

    Die Denkangaben sind bereits **auf die Rolle dieses Benutzers geklemmt**:
    ``efforts`` enthaelt nur, was er auch waehlen darf. Damit muss die
    Oberflaeche keine Rechte auswerten — sie zeigt eine Liste an. Die
    verbindliche Klemmung passiert trotzdem erneut serverseitig beim Senden;
    diese hier ist Darstellung, nicht Durchsetzung.
    """

    id: int
    name: str
    default_model: str
    requires_api_key: bool
    operator_key_available: bool
    available: bool
    #: Ob bei diesem Modell ueberhaupt nachgedacht werden kann.
    reasoning: bool = False
    #: Die waehlbaren Stufen, aufsteigend. Leer = nur an/aus.
    efforts: list[str] = []
    #: Ob "aus" eine gueltige Wahl ist. Bei 82 der 402 Modelle nicht.
    can_disable: bool = True
    #: Was gilt, wenn der Benutzer nichts waehlt.
    default_effort: str | None = None


class AiProviderTestResponse(BaseModel):
    """Ergebnis eines echten Testaufrufs gegen den konfigurierten Endpunkt."""

    ok: bool
    # Stabiler MSM-Code, z. B. AI_PROVIDER_AUTH_FAILED. Uebersetzbar.
    code: str | None = None
    # Gekuerzte, redigierte Klartextmeldung des Anbieters. Sie sagt, *was*
    # fehlt ("No endpoints found for openrouter-free") — genau das, was ein
    # uebersetzter Code allein nicht leisten kann.
    detail: str | None = None
