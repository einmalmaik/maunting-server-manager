"""Secret-minimierte API-Vertraege fuer AI-Provider."""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, Field, SecretStr

from services.ai_voice_session import STIMMEN


# Obergrenze fuer den gepflegten Preis: 1.000 USD je eine Million Tokens, in
# Microunits (1 US-Cent = 10.000). Das teuerste Modell bei OpenRouter liegt bei
# rund 75 USD — die Grenze faengt also Tippfehler ab, ohne einer echten
# Preisliste im Weg zu stehen.
MAX_TOKEN_PRICE_MICRO_USD = 1_000_000_000


def _stimme_lesen(wert: object) -> str | None:
    """Nachsichtig lesen, streng speichern — und Unbekanntes gar nicht erst.

    Nachsichtig in zwei Punkten, und beide sind keine Falscheingabe: Ein
    Auswahlfeld ohne Wahl schickt ``""`` und nicht ``null`` — das ist der Weg,
    auf dem „nichts hinterlegt" hier ankommt. Und in der Oberflaeche steht die
    Stimme gross („Alloy"), waehrend die API sie klein verlangt; wer den Wert
    von Hand setzt, tippt ab, was er sieht. Ein 422 dafuer waere eine Belehrung
    ohne Anlass.

    Streng beim Unbekannten: eine erfundene Stimme faellt sonst erst beim
    Verbinden auf, und zwar der Gegenstelle. Der Betreiber saehe einen
    Anbieterfehler in einem Sprachgespraech statt einer Fehlermeldung an dem
    Feld, in das er gerade getippt hat.

    Ein leerer Wert wird zu ``None`` und nicht zu ``""``: in der Spalte heisst
    ``None`` „nichts hinterlegt" und loest auf die Standardstimme auf — ein
    leerer String haette dort keine Bedeutung und ginge als Stimme an OpenAI.
    """
    if wert is None:
        return None
    name = str(wert).strip().lower()
    if not name:
        return None
    if name not in STIMMEN:
        raise ValueError(
            "Unbekannte Stimme. Waehlbar sind: " + ", ".join(sorted(STIMMEN))
        )
    return name


#: Eine der acht Realtime-Stimmen, oder ``None`` fuer „nichts hinterlegt". Die
#: Liste steht in `services.ai_voice_session` und **nur** dort: sie gehoert zum
#: Protokoll der Gegenstelle, nicht zu diesem Vertrag. Eine Kopie hier waere die
#: zweite Wahrheit, die beim naechsten Wechsel des Anbieters still veraltet.
Stimme = Annotated[str | None, BeforeValidator(_stimme_lesen)]


class AiProviderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    # Schluessel aus `services/ai_provider_registry.ANBIETER`. Die Adresse
    # dahinter gehoert dem Programm — hier steht nur noch, *welcher* Anbieter.
    provider_kind: str = Field(min_length=1, max_length=32)
    default_model: str = Field(min_length=1, max_length=256)
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
    operator_api_key: SecretStr | None = Field(default=None, min_length=1, max_length=4096)


class AiProviderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    provider_kind: str | None = Field(default=None, min_length=1, max_length=32)
    default_model: str | None = Field(default=None, min_length=1, max_length=256)
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
    default_model: str
    #: Bewusst ohne die Pruefung aus `Stimme`: gelesen wird hier, was in der
    #: Zeile steht. Nimmt OpenAI eines Tages eine Stimme aus dem Programm, soll
    #: der Betreiber sie in der Liste sehen und aendern koennen — eine 500 beim
    #: blossen Anzeigen waere die schlechteste Art, ihm das mitzuteilen.
    #: ``None`` heisst „nichts hinterlegt", nicht „alloy".
    default_voice: str | None = None
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
    #: ``chat_completions`` oder ``realtime``. Steht hier, weil das Formular
    #: sonst zwei Zugaenge anbietet, die verschiedene Dinge tun, und nichts
    #: verraet welchen wofuer. Ein Realtime-Zugang taucht in der Chatauswahl
    #: spaeter gar nicht auf — das soll der Betreiber beim Anlegen wissen und
    #: nicht danach suchen muessen.
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
