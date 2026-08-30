"""Provider-Konfiguration: Anbieter, Modell, Schlüssel — ohne Secret-Leaks.

**Hier stand einmal ein SSRF-Apparat.** Rund 150 Zeilen: Adressauflösung,
Politik gegen Link-Local- und Metadata-Ziele, IP-Pinning gegen DNS-Rebinding
und eine zweite Prüfung unmittelbar vor jedem Request. Alles davon war nötig,
solange der Betreiber eine beliebige Basis-URL eintragen konnte — dann ist die
Zieladresse eine Eingabe, und eine Eingabe, die das Panel zu einem HTTP-Aufruf
bewegt, ist eine Angriffsfläche.

Seit der Betreiber einen Anbieter aus `ai_provider_registry` **auswählt**,
stammt die Adresse aus dem Programm. Der Apparat ist ersatzlos entfallen;
`assert_provider_destination` und `validate_provider_base_url` gibt es nicht
mehr.

Das ist der eigentliche Gewinn des Umbaus und nicht nur ein Nebeneffekt: eine
Schutzmaßnahme, die man löschen kann, weil die Gefahr weg ist, schlägt jede,
die man pflegen muss.

**Seit Azure stimmt der Satz „es gibt gar keine Eingabe mehr" nicht mehr, und
er steht deshalb hier nicht mehr.** Bei Azure ist jede Ressource ihr eigener
Host; ohne dessen Namen ist der Anbieter nicht erreichbar. Was der Betreiber
beiträgt, ist aber ein **einzelnes DNS-Label** und keine Adresse: Schema,
Suffix und Pfad stehen als Vorlage in der Anbieterdatei, und `_assert_ressource`
prüft das Label mit ``re.fullmatch`` gegen die Form eines Labels — kein Punkt,
kein Schrägstrich, kein Doppelpunkt, kein Zeilenumbruch am Ende.

Die Prüfung steht **hier** und nicht nur im Schema, aus demselben Grund wie bei
der Stimme weiter unten: nicht jeder Schreibweg führt durch ein Formular. Und
sie kommt in `base_url()` ein zweites Mal zum Tragen — eine Zeile, die auf
einem anderen Weg in die Datenbank gelangt ist, darf nicht bis zum HTTP-Aufruf
durchkommen.

Was damit **nicht** ausgeschlossen ist, steht ausführlich an
`ai_provider_registry.basis.Anbieter.ressource_noetig`: Azure Private Link
lenkt einen gültigen Namen innerhalb eines VNet auf eine private Adresse. Eine
Suffixprüfung beweist über das Ziel nichts; wer das ausschließen will, braucht
wieder eine Prüfung nach der Namensauflösung.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from models import AiProvider, AiRun, User
import re

from services import ai_provider_registry, ai_model_price_service
from services.ai_reasoning import RANGFOLGE
from services.dis_client import DisClient


class AiProviderConfigurationError(ValueError):
    """Die Provider-Konfiguration ist nicht schlüssig."""


def base_url(provider: AiProvider) -> str:
    """Die Adresse dieses Providers — aus der Registry, nicht aus der Zeile.

    Eine eigene Funktion statt einer Spalte, damit es genau **eine** Wahrheit
    gibt. Als gespeicherter Wert wäre sie eine Kopie, die nach einer Änderung
    an der Registry still veraltet: der Anbieter zieht auf einen neuen Pfad um,
    das Programm weiß es, die Datenbank nicht.

    Bei einem Anbieter mit ``ressource_noetig`` ist der Eintrag eine **Vorlage**
    und die Zeile steuert genau ein DNS-Label bei. Geprüft wird es hier
    **erneut**, obwohl `create_provider` und `update_provider` es schon getan
    haben: diese Funktion ist die letzte Station vor dem HTTP-Aufruf, und eine
    Zeile aus einem Seed, einem Import oder einer Zukunftsversion soll nicht
    ungeprüft in einen Hostnamen geraten. Ein ungeprüfter Wert in
    ``httpx.URL`` wirft nämlich **nicht** — er ergibt einen prozentkodierten
    Host und stirbt erst als DNS-Fehler, an dem niemand die Ursache abliest.

    ``AiProviderConfigurationError``, nicht ``KeyError``: anders als ein
    unbekannter Anbieter ist ein fehlender Ressourcenname etwas, das der
    Betreiber selbst beheben kann — und die Router übersetzen diese Klasse
    bereits in eine 400 mit Text.
    """
    spec = ai_provider_registry.anbieter(provider.provider_kind)
    if not spec.ressource_noetig:
        return spec.base_url
    return spec.base_url.format(
        ressource=_assert_ressource(provider.azure_resource_name, pflicht=True)
    )


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


def fuer_chat(provider: AiProvider) -> bool:
    return bool(
        provider.enabled
        and spricht(provider, ai_provider_registry.CHAT)
        and bool(getattr(provider, "standard_enabled", True))
        and bool((provider.default_model or "").strip())
    )


def fuer_worker(provider: AiProvider) -> bool:
    return bool(
        provider.enabled
        and spricht(provider, ai_provider_registry.CHAT)
        and bool(getattr(provider, "worker_enabled", False))
        and (provider.worker_model or "").strip()
    )


def fuer_transcription(provider: AiProvider) -> bool:
    return bool(
        provider.enabled
        and spricht(provider, ai_provider_registry.CHAT)
        and bool(getattr(provider, "transcription_enabled", False))
        and (provider.transcription_model or "").strip()
        and bool(ai_provider_registry.anbieter(provider.provider_kind).gehoer_wege)
    )


def fuer_ethics(provider: AiProvider) -> bool:
    return bool(
        provider.enabled
        and spricht(provider, ai_provider_registry.CHAT)
        and bool(getattr(provider, "ethics_enabled", False))
        and (provider.ethics_model or "").strip()
        and (provider.ethics_mode or "auto").strip().lower() != "off"
    )


def _hint(secret: str) -> str:
    return "********" + secret[-4:] if len(secret) >= 4 else "********"


def _operator_aad(provider_id: int) -> str:
    return f"msm:ai:provider:{provider_id}:operator-key"


def _assert_kind(kind: str) -> str:
    normalized = (kind or "").strip()
    if not ai_provider_registry.bekannt(normalized):
        raise AiProviderConfigurationError("Unbekannter KI-Anbieter")
    return normalized


def _assert_key_passt(kind: str, api_key: str | None) -> str | None:
    """Nimmt den Schlüssel entgegen, säubert ihn und prüft sein Präfix.

    **Die Ränder fallen weg.** Ein Schlüssel wird aus einem Portal kopiert, und
    dabei kommt regelmässig ein Leerzeichen oder ein Zeilenumbruch mit. Jedes
    andere Feld hier wird längst getrimmt; dieses war die Ausnahme, und die
    Folge war die unverständlichste aller Fehlermeldungen: der Anbieter
    antwortet mit einem 401, das den Schlüssel für falsch erklärt, obwohl der
    richtige dransteht. Bei Azure wäre es sogar schlimmer als ein 401 — ein
    Zeilenumbruch in einem Kopfzeilenwert ist gar keine gültige Anfrage mehr.

    Nachsichtig lesen, streng speichern: die Ränder sind ein Tippfehler und
    kein Angriff, also werden sie entfernt statt abgewiesen. Was **innen**
    steht, bleibt unangetastet — ein Schlüssel gehört dem Anbieter, und MSM hat
    keine Meinung über seine Form.

    Die Präfixprüfung ist bewusst nur eine Plausibilitätsprüfung: ein Schlüssel
    mit falschem Präfix ist mit Sicherheit falsch, einer mit richtigem damit
    noch lange nicht gültig. Sie erspart dem Betreiber den Umweg über eine
    Fehlermeldung des Anbieters — der echte Testaufruf bleibt der Beweis.

    Rückgabe ist der zu speichernde Schlüssel, oder ``None`` für „keiner
    dabei". Ein Schlüssel, der nur aus Leerraum bestand, ist keiner.
    """
    schluessel = (api_key or "").strip()
    if not schluessel:
        return None
    praefix = ai_provider_registry.anbieter(kind).key_prefix
    if praefix and not schluessel.startswith(praefix):
        raise AiProviderConfigurationError(
            f"Der Schlüssel dieses Anbieters beginnt mit „{praefix}“"
        )
    return schluessel


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


#: Woraus ein Azure-Ressourcenname bestehen darf — die Form eines **DNS-Labels**
#: und nicht die eines Azure-Namens, und der Unterschied ist Absicht: der Wert
#: wird zum ersten Teil eines Hostnamens, nicht zu einem Bezeichner in einem
#: Portal.
#:
#: * Anfang und Ende alphanumerisch. Azure verbietet den abschliessenden
#:   Bindestrich ausdrücklich; der führende wäre zusätzlich ein
#:   Argument-Präfix, sobald der Wert je in eine Kommandozeile geriete.
#: * Mindestens 2 Zeichen, weil Azure kürzere Namen nicht vergibt. Ein
#:   einzelnes Zeichen wäre ein gültiges DNS-Label und trotzdem mit Sicherheit
#:   kein Ressourcenname — dieselbe Art Plausibilitätsprüfung wie beim
#:   Schlüsselpräfix: sie erspart den Umweg über eine Fehlermeldung des
#:   Anbieters, sie ersetzt sie nicht.
#: * Höchstens 63 Zeichen. Azure erlaubt 64, ein DNS-Label nicht
#:   (RFC 1035 §2.3.4) — es gilt die engere Grenze, denn hierhin geht der Wert.
#: * Kein Punkt, kein Schrägstrich, kein Doppelpunkt: alles drei wären ein
#:   anderes Ziel und nicht ein anderer Name.
#:
#: Geprüft wird mit ``fullmatch`` und **nicht** mit ``match``. Pythons ``$``
#: passt auch vor einem abschliessenden Zeilenumbruch; ``"meineressource\n"``
#: käme sonst durch und stünde anschliessend in einem Hostnamen.
_RESSOURCE_MUSTER = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,61}[A-Za-z0-9]")

#: Reserviert für Punycode. Passiert das Muster oben mühelos, wird aber von
#: IDNA-Bibliotheken uneinheitlich behandelt — ein Name, dessen Auflösung von
#: der Bibliothek abhängt, gehört nicht in einen Hostnamen.
_PUNYCODE_PRAEFIX = "xn--"


def _assert_ressource(name: str | None, *, pflicht: bool = False) -> str | None:
    """Ein Azure-Ressourcenname von zulässiger Form — oder gar keiner.

    Nachsichtig lesen, streng speichern, wie bei `_assert_stimme` darüber: ein
    leeres Formularfeld heisst „nichts hinterlegt", Rand-Leerzeichen aus einem
    Copy-and-paste fallen weg. Alles andere ist streng, denn der Wert wird zu
    einem Hostnamen und nicht zu einer Beschriftung.

    Ausdrücklich **nicht** kleingeschrieben. DNS ist zwar unempfindlich, aber
    der Wert steht auch in der Oberfläche, und ein stillschweigend verändertes
    ``Mein-AI-Hub`` sähe nach einem Fehler aus. Für die Auflösung macht es
    keinen Unterschied.

    ``pflicht=True`` verlangt einen Namen. Getrennt vom Formatfehler, weil es
    zwei verschiedene Auskünfte sind: „du hast nichts eingetragen" und „das ist
    kein Name". Wer beides in eine Meldung wirft, lässt den Betreiber raten.
    """
    kennung = (name or "").strip()
    if not kennung:
        if pflicht:
            raise AiProviderConfigurationError(
                "Dieser Anbieter braucht den Namen deiner Azure-Ressource"
            )
        return None
    if not _RESSOURCE_MUSTER.fullmatch(kennung) or kennung.lower().startswith(
        _PUNYCODE_PRAEFIX
    ):
        raise AiProviderConfigurationError(
            "Ungültiger Azure-Ressourcenname. Erlaubt sind Buchstaben, Ziffern "
            "und Bindestriche (2 bis 63 Zeichen, Bindestrich nicht am Rand). "
            "Trage nur den Namen ein, keine vollständige Adresse."
        )
    return kennung


def _assert_worker_rolle(
    worker_model: str | None,
    worker_reasoning_effort: str | None,
    default_model: str | None,
) -> tuple[str | None, str | None]:
    """Die Worker-Rolle eines Zugangs — schlüssig oder gar nicht.

    Drei Regeln, alle drei gegen tote Konfiguration:

    * Die Denkstufe muss ein Wort aus `ai_reasoning.RANGFOLGE` sein. Ein
      erfundenes Wort fiele sonst erst zur Laufzeit auf — als 400 des
      Anbieters bei jedem Worker-Segment, ohne Bezug zu dem Feld, in das der
      Betreiber getippt hat.
    * Eine Denkstufe ohne Worker-Modell gibt es nicht: die Stufe gehört zum
      Arbeitsmodell, und ob die Worker-Rolle konfiguriert ist, entscheidet
      allein ``worker_model`` — zwei halbe Schalter wären zwei Auslegungen.
    * Ein Worker-Modell ohne Standardmodell gibt es nicht: Worker erben den
      Zugang des Gehirns, ein Zugang ohne ``default_model`` trägt kein
      Gespräch und damit auch keinen `worker_start`. Die Zeile sähe
      konfiguriert aus und täte nie etwas.

    Nachsichtig gelesen (Rand-Leerzeichen, leer heißt „nichts hinterlegt"),
    streng gespeichert — dasselbe Muster wie bei der Stimme darüber.
    """
    modell = (worker_model or "").strip() or None
    stufe = (worker_reasoning_effort or "").strip() or None
    if stufe is not None and stufe not in RANGFOLGE:
        raise AiProviderConfigurationError(
            "Unbekannte Worker-Denkstufe. Zulässig sind: " + ", ".join(RANGFOLGE)
        )
    if stufe is not None and modell is None:
        raise AiProviderConfigurationError(
            "Eine Worker-Denkstufe braucht ein Worker-Modell"
        )
    if modell is not None and not (default_model or "").strip():
        raise AiProviderConfigurationError(
            "Ein Worker-Modell braucht ein Standardmodell am selben Zugang"
        )
    return modell, stufe


ETHICS_MODI = ("off", "auto", "always", "critical")

REALTIME_STIMMEN = frozenset(
    {"alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse", "marin", "cedar"}
)
REALTIME_SPRACHEN = frozenset({"auto", "de", "en"})
REALTIME_VAD = frozenset({"auto", "low", "medium", "high"})
REALTIME_2_REASONING = frozenset({"low", "medium", "high"})
REALTIME_PREISFELDER = (
    "realtime_text_input_price_micro_usd_per_million",
    "realtime_text_output_price_micro_usd_per_million",
    "realtime_audio_input_price_micro_usd_per_million",
    "realtime_audio_output_price_micro_usd_per_million",
)


def _assert_realtime_werte(provider: AiProvider) -> None:
    spec = ai_provider_registry.anbieter(provider.provider_kind) if ai_provider_registry.bekannt(provider.provider_kind) else None
    if spec is None or not getattr(spec, "realtime_tauglich", False):
        raise AiProviderConfigurationError("Realtime-Sprachmodus wird von diesem Anbieter nicht unterstützt")
    if not provider.enabled:
        raise AiProviderConfigurationError("Der Realtime-Zugang muss aktiviert sein")
    if not provider.operator_api_key_encrypted:
        raise AiProviderConfigurationError("Der Realtime-Zugang braucht einen API-Schlüssel")
    realtime_model = (provider.realtime_model or "").strip()
    if not realtime_model:
        raise AiProviderConfigurationError("Wähle ein Realtime-Modell aus")
    if "realtime" not in realtime_model.lower():
        raise AiProviderConfigurationError("Das gewählte Modell ist kein OpenAI-Realtime-Modell")
    _assert_realtime_reasoning(realtime_model, provider.realtime_reasoning_effort)
    if provider.realtime_voice not in REALTIME_STIMMEN:
        raise AiProviderConfigurationError("Wähle eine eingebaute OpenAI-Stimme aus")
    if provider.realtime_language not in REALTIME_SPRACHEN:
        raise AiProviderConfigurationError("Unbekannte Realtime-Antwortsprache")
    if provider.realtime_vad_eagerness not in REALTIME_VAD:
        raise AiProviderConfigurationError("Unbekannte Realtime-VAD-Empfindlichkeit")


def _realtime_ist_zwei(modell: str | None) -> bool:
    """Nur die explizite Realtime-2-Reihe erhält eine Denkstufe."""
    return "realtime-2" in (modell or "").strip().lower()


def _assert_realtime_reasoning(modell: str | None, effort: str | None) -> str | None:
    wert = (effort or "").strip().lower() or None
    if wert is None:
        return None
    if not _realtime_ist_zwei(modell):
        raise AiProviderConfigurationError(
            "Eine Realtime-Denkstufe ist nur für die OpenAI-Realtime-2-Reihe verfügbar"
        )
    if wert not in REALTIME_2_REASONING:
        raise AiProviderConfigurationError("Unbekannte Realtime-Denkstufe")
    return wert


def realtime_zugang(db: Session) -> AiProvider | None:
    """Der eine betriebsbereite panelweite Realtime-Zugang, falls aktiviert."""
    provider = db.query(AiProvider).filter(AiProvider.realtime_default.is_(True)).first()
    if provider is None:
        return None
    _assert_realtime_werte(provider)
    return provider


def _realtime_felder_setzen(provider: AiProvider, values: dict) -> None:
    for feld in (
        "realtime_model",
        "realtime_voice",
        "realtime_reasoning_effort",
        "realtime_language",
        "realtime_vad_eagerness",
        *REALTIME_PREISFELDER,
    ):
        if feld not in values or values[feld] is None and feld in {"realtime_language", "realtime_vad_eagerness"}:
            continue
        wert = values[feld]
        if feld in {"realtime_model", "realtime_voice", "realtime_reasoning_effort"}:
            wert = (wert or "").strip() or None
        elif feld in {"realtime_language", "realtime_vad_eagerness"}:
            wert = str(wert).strip().lower()
        setattr(provider, feld, wert)
    if provider.realtime_voice is not None and provider.realtime_voice not in REALTIME_STIMMEN:
        raise AiProviderConfigurationError("Unbekannte OpenAI-Realtime-Stimme")
    provider.realtime_reasoning_effort = _assert_realtime_reasoning(
        provider.realtime_model, provider.realtime_reasoning_effort
    )
    if provider.realtime_language not in REALTIME_SPRACHEN:
        raise AiProviderConfigurationError("Unbekannte Realtime-Antwortsprache")
    if provider.realtime_vad_eagerness not in REALTIME_VAD:
        raise AiProviderConfigurationError("Unbekannte Realtime-VAD-Empfindlichkeit")


def _realtime_auswahl_anwenden(db: Session, provider: AiProvider, *, verlangt: bool | None) -> None:
    if verlangt is True:
        _assert_realtime_werte(provider)
        db.query(AiProvider).filter(
            AiProvider.id != provider.id,
            AiProvider.realtime_default.is_(True),
        ).update({AiProvider.realtime_default: False, AiProvider.realtime_enabled: False}, synchronize_session=False)
        provider.realtime_default = True
        provider.realtime_enabled = True
        return
    if verlangt is False:
        provider.realtime_default = False
        provider.realtime_enabled = False
        return
    if provider.realtime_default:
        try:
            _assert_realtime_werte(provider)
        except AiProviderConfigurationError:
            provider.realtime_default = False
            provider.realtime_enabled = False


def _assert_ethics_rolle(
    ethics_model: str | None,
    ethics_reasoning_effort: str | None,
    ethics_mode: str | None,
    default_model: str | None,
) -> tuple[str | None, str | None, str]:
    """Die Ethics-Engine-Konfiguration eines Zugangs — schlüssig oder gar nicht.

    * Der Modus muss in ETHICS_MODI liegen (Standard: 'auto').
    * Die Denkstufe muss ein Wort aus `ai_reasoning.RANGFOLGE` sein, falls angegeben.
    * Eine Denkstufe ohne Ethics-Modell gibt es nicht.
    * Ein Ethics-Modell braucht ein Standardmodell am selben Zugang.
    """
    modell = (ethics_model or "").strip() or None
    stufe = (ethics_reasoning_effort or "").strip() or None
    modus = (ethics_mode or "auto").strip().lower()
    if modus not in ETHICS_MODI:
        raise AiProviderConfigurationError(
            f"Unbekannter Ethik-Modus '{modus}'. Zulässig sind: {', '.join(ETHICS_MODI)}"
        )
    if stufe is not None and stufe not in RANGFOLGE:
        raise AiProviderConfigurationError(
            "Unbekannte Ethik-Denkstufe. Zulässig sind: " + ", ".join(RANGFOLGE)
        )
    if stufe is not None and modell is None:
        raise AiProviderConfigurationError(
            "Eine Ethik-Denkstufe braucht ein Ethik-Modell"
        )
    if modell is not None and not (default_model or "").strip():
        raise AiProviderConfigurationError(
            "Ein Ethik-Modell braucht ein Standardmodell am selben Zugang"
        )
    return modell, stufe, modus


def create_provider(
    db: Session,
    *,
    name: str,
    provider_kind: str,
    default_model: str | None = None,
    enabled: bool,
    requires_api_key: bool,
    operator_api_key: str | None,
    # Optional: ohne Preis bleiben die Kosten bei null (siehe estimate_cost_microunits).
    token_price_micro_usd_per_million: int | None = None,
    standard_input_price_micro_usd_per_million: int | None = None,
    standard_output_price_micro_usd_per_million: int | None = None,
    standard_cache_price_micro_usd_per_million: int | None = None,
    worker_input_price_micro_usd_per_million: int | None = None,
    worker_output_price_micro_usd_per_million: int | None = None,
    worker_cache_price_micro_usd_per_million: int | None = None,
    ethics_input_price_micro_usd_per_million: int | None = None,
    ethics_output_price_micro_usd_per_million: int | None = None,
    ethics_cache_price_micro_usd_per_million: int | None = None,
    standard_enabled: bool | None = None,
    worker_enabled: bool | None = None,
    ethics_enabled: bool | None = None,
    transcription_enabled: bool | None = None,
    realtime_enabled: bool | None = None,
    # Optional: ohne Stimme gibt es über diesen Zugang keinen Sprachmodus. Eine
    # Standardstimme wird bewusst **nicht** eingetragen — warum, steht an der
    # Spalte in `models/ai_provider.py`.
    default_voice: str | None = None,
    # Optional: das hörende Modell eines Chatzugangs. Ohne es gibt es über
    # diesen Zugang ebenfalls keinen Sprachmodus.
    transcription_model: str | None = None,
    realtime_default: bool = False,
    realtime_model: str | None = None,
    realtime_voice: str | None = None,
    realtime_reasoning_effort: str | None = None,
    realtime_language: str = "auto",
    realtime_vad_eagerness: str = "auto",
    realtime_text_input_price_micro_usd_per_million: int | None = None,
    realtime_text_output_price_micro_usd_per_million: int | None = None,
    realtime_audio_input_price_micro_usd_per_million: int | None = None,
    realtime_audio_output_price_micro_usd_per_million: int | None = None,
    # Optional: die Worker-Rolle dieses Zugangs (docs/agentic-framework.md,
    # Abschnitt 5). Ohne Worker-Modell gilt der heutige Ein-Modell-Betrieb.
    # Zählt bewusst **nicht** als Funktion im Sinne der Prüfung unten: Worker
    # erben den Zugang des Gehirns, allein trüge das Modell nichts.
    worker_model: str | None = None,
    worker_reasoning_effort: str | None = None,
    # Optional: die Ethics-Engine-Rolle dieses Zugangs. Reflektiert und beraet
    # das Gehirn vor kritischen Handlungen.
    ethics_model: str | None = None,
    ethics_reasoning_effort: str | None = None,
    ethics_mode: str = "auto",
    # Optional: der Name der Azure-Ressource dieses Zugangs. Nur Anbieter mit
    # ``ressource_noetig`` brauchen ihn; bei allen anderen bleibt er leer und
    # unbeachtet. Er wird trotzdem **nicht** gegen `provider_kind` geprüft —
    # dieselbe Begründung wie bei der Stimme: der Anbieter lässt sich später
    # ändern, und ein stillschweigend gelöschter Name wäre ärgerlicher als ein
    # ungenutzter.
    azure_resource_name: str | None = None,
) -> AiProvider:
    if not (name or "").strip():
        raise AiProviderConfigurationError("Provider-Name darf nicht leer sein")
    modell = (default_model or "").strip() or None
    gehoer = (transcription_model or "").strip() or None
    stimme = _assert_stimme(default_voice)
    if not (modell or gehoer or stimme or (realtime_model or "").strip()):
        raise AiProviderConfigurationError(
            "Mindestens eine Funktion (Standardmodell, Modell für Gesprochenes oder Stimme) muss hinterlegt sein"
        )
    arbeitsmodell, arbeitsstufe = _assert_worker_rolle(
        worker_model, worker_reasoning_effort, modell
    )
    ethikmodell, ethikstufe, ethikmodus = _assert_ethics_rolle(
        ethics_model, ethics_reasoning_effort, ethics_mode, modell
    )
    kind = _assert_kind(provider_kind)
    automatic_prices: dict[str, object] = {
        "default_model": modell,
        "worker_model": arbeitsmodell,
        "ethics_model": ethikmodell,
        "standard_input_price_micro_usd_per_million": standard_input_price_micro_usd_per_million,
        "standard_output_price_micro_usd_per_million": standard_output_price_micro_usd_per_million,
        "standard_cache_price_micro_usd_per_million": standard_cache_price_micro_usd_per_million,
        "worker_input_price_micro_usd_per_million": worker_input_price_micro_usd_per_million,
        "worker_output_price_micro_usd_per_million": worker_output_price_micro_usd_per_million,
        "worker_cache_price_micro_usd_per_million": worker_cache_price_micro_usd_per_million,
        "ethics_input_price_micro_usd_per_million": ethics_input_price_micro_usd_per_million,
        "ethics_output_price_micro_usd_per_million": ethics_output_price_micro_usd_per_million,
        "ethics_cache_price_micro_usd_per_million": ethics_cache_price_micro_usd_per_million,
    }
    ai_model_price_service.fill_missing_role_prices(kind, automatic_prices)
    schluessel = _assert_key_passt(kind, operator_api_key)
    ressource = _assert_ressource(
        azure_resource_name,
        # Beim Anlegen gleich verlangt, statt eine Zeile zuzulassen, die
        # niemand benutzen kann: ohne Namen gibt es für diesen Anbieter keine
        # Adresse, und der Fehler fiele erst beim ersten Chat auf.
        pflicht=ai_provider_registry.anbieter(kind).ressource_noetig,
    )
    provider = AiProvider(
        name=name.strip(),
        provider_kind=kind,
        default_model=modell,
        enabled=enabled,
        requires_api_key=requires_api_key,
        token_price_micro_usd_per_million=token_price_micro_usd_per_million,
        standard_input_price_micro_usd_per_million=automatic_prices["standard_input_price_micro_usd_per_million"],
        standard_output_price_micro_usd_per_million=automatic_prices["standard_output_price_micro_usd_per_million"],
        standard_cache_price_micro_usd_per_million=automatic_prices["standard_cache_price_micro_usd_per_million"],
        worker_input_price_micro_usd_per_million=automatic_prices["worker_input_price_micro_usd_per_million"],
        worker_output_price_micro_usd_per_million=automatic_prices["worker_output_price_micro_usd_per_million"],
        worker_cache_price_micro_usd_per_million=automatic_prices["worker_cache_price_micro_usd_per_million"],
        ethics_input_price_micro_usd_per_million=automatic_prices["ethics_input_price_micro_usd_per_million"],
        ethics_output_price_micro_usd_per_million=automatic_prices["ethics_output_price_micro_usd_per_million"],
        ethics_cache_price_micro_usd_per_million=automatic_prices["ethics_cache_price_micro_usd_per_million"],
        standard_enabled=bool(modell) if standard_enabled is None else bool(standard_enabled and modell),
        worker_enabled=bool(arbeitsmodell) if worker_enabled is None else bool(worker_enabled and arbeitsmodell),
        ethics_enabled=bool(ethikmodell) if ethics_enabled is None else bool(ethics_enabled and ethikmodell),
        transcription_enabled=bool(gehoer) if transcription_enabled is None else bool(transcription_enabled and gehoer),
        realtime_enabled=bool((realtime_model or "").strip()) if realtime_enabled is None else bool(realtime_enabled and (realtime_model or "").strip()),
        default_voice=stimme,
        transcription_model=gehoer,
        realtime_default=False,
        realtime_model=(realtime_model or "").strip() or None,
        realtime_voice=(realtime_voice or "").strip() or None,
        realtime_reasoning_effort=(realtime_reasoning_effort or "").strip().lower() or None,
        realtime_language=(realtime_language or "auto").strip().lower(),
        realtime_vad_eagerness=(realtime_vad_eagerness or "auto").strip().lower(),
        realtime_text_input_price_micro_usd_per_million=realtime_text_input_price_micro_usd_per_million,
        realtime_text_output_price_micro_usd_per_million=realtime_text_output_price_micro_usd_per_million,
        realtime_audio_input_price_micro_usd_per_million=realtime_audio_input_price_micro_usd_per_million,
        realtime_audio_output_price_micro_usd_per_million=realtime_audio_output_price_micro_usd_per_million,
        worker_model=arbeitsmodell,
        worker_reasoning_effort=arbeitsstufe,
        ethics_model=ethikmodell,
        ethics_reasoning_effort=ethikstufe,
        ethics_mode=ethikmodus,
        azure_resource_name=ressource,
    )
    db.add(provider)
    db.flush()
    if schluessel:
        provider.operator_api_key_encrypted = DisClient.encrypt(
            schluessel, aad=_operator_aad(provider.id)
        )
        provider.operator_api_key_hint = _hint(schluessel)
    _realtime_felder_setzen(provider, {
        "realtime_model": realtime_model,
        "realtime_voice": realtime_voice,
        "realtime_reasoning_effort": realtime_reasoning_effort,
        "realtime_language": realtime_language,
        "realtime_vad_eagerness": realtime_vad_eagerness,
        "realtime_text_input_price_micro_usd_per_million": realtime_text_input_price_micro_usd_per_million,
        "realtime_text_output_price_micro_usd_per_million": realtime_text_output_price_micro_usd_per_million,
        "realtime_audio_input_price_micro_usd_per_million": realtime_audio_input_price_micro_usd_per_million,
        "realtime_audio_output_price_micro_usd_per_million": realtime_audio_output_price_micro_usd_per_million,
    })
    _realtime_auswahl_anwenden(db, provider, verlangt=realtime_default)
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
    if "name" in values and not str(values["name"] or "").strip():
        raise AiProviderConfigurationError("Provider-Name darf nicht leer sein")
    # Der gespeicherte Schluessel gehoert zu **einer** Gegenstelle. Wechselt
    # die, muss er weg: sonst ginge er beim naechsten Test oder Chat an eine
    # Partei, fuer die er nie ausgestellt wurde. Ein im selben Aufruf
    # mitgeschickter neuer Schluessel wird unten regulaer gespeichert.
    #
    # Zwei Wege fuehren zu einer anderen Gegenstelle, und beide zaehlen:
    schluessel_verfaellt = False
    if "provider_kind" in values and values["provider_kind"] != provider.provider_kind:
        # Ein anderer Anbieter. Die Praefixpruefung beim Anlegen verhindert
        # genau diesen Fall; ohne die Zeile hier waere sie beim Wechsel
        # wirkungslos.
        provider.provider_kind = _assert_kind(values["provider_kind"])
        schluessel_verfaellt = True
    neue_ressource = (
        _assert_ressource(values["azure_resource_name"])
        if "azure_resource_name" in values
        else provider.azure_resource_name
    )
    if neue_ressource != provider.azure_resource_name:
        # **Dieselbe Invariante, zweite Lücke.** Bei Azure ist ein Schluessel an
        # genau eine Ressource gebunden, und der Namensraum ist global und frei
        # belegbar. Bliebe der Schluessel beim Wechsel stehen, schickte ein
        # Tippfehler im Ressourcennamen den Betreiberschluessel an eine fremde
        # Azure-Ressource — bei gleichbleibendem `provider_kind`, an dem die
        # Regel darueber nicht anschlaegt.
        schluessel_verfaellt = True
    provider.azure_resource_name = neue_ressource
    if schluessel_verfaellt:
        provider.operator_api_key_encrypted = None
        provider.operator_api_key_hint = None
    schluessel = _assert_key_passt(provider.provider_kind, operator_api_key)
    new_name = values["name"].strip() if "name" in values else provider.name
    new_default_model = (values["default_model"] or "").strip() or None if "default_model" in values else provider.default_model
    new_default_voice = _assert_stimme(values["default_voice"]) if "default_voice" in values else provider.default_voice
    new_transcription_model = (values["transcription_model"] or "").strip() or None if "transcription_model" in values else provider.transcription_model
    new_realtime_model = (values.get("realtime_model") or "").strip() or None if "realtime_model" in values else provider.realtime_model
    if not (new_default_model or new_transcription_model or new_default_voice or new_realtime_model):
        raise AiProviderConfigurationError(
            "Mindestens eine Funktion (Standardmodell, Modell für Gesprochenes oder Stimme) muss hinterlegt sein"
        )
    # Gegen den **Zielzustand** geprüft, nicht gegen die Eingabe allein: auch
    # wer nur das Standardmodell leert, darf kein verwaistes Worker-Modell
    # zurücklassen.
    new_worker_model, new_worker_effort = _assert_worker_rolle(
        values["worker_model"] if "worker_model" in values else provider.worker_model,
        values["worker_reasoning_effort"]
        if "worker_reasoning_effort" in values
        else provider.worker_reasoning_effort,
        new_default_model,
    )
    new_ethics_model, new_ethics_effort, new_ethics_mode = _assert_ethics_rolle(
        values["ethics_model"] if "ethics_model" in values else provider.ethics_model,
        values["ethics_reasoning_effort"]
        if "ethics_reasoning_effort" in values
        else provider.ethics_reasoning_effort,
        values["ethics_mode"] if "ethics_mode" in values else provider.ethics_mode,
        new_default_model,
    )
    price_values: dict[str, object] = {
        "default_model": new_default_model,
        "worker_model": new_worker_model,
        "ethics_model": new_ethics_model,
        **{
            field: values[field] if field in values else getattr(provider, field)
            for field in (
                "standard_input_price_micro_usd_per_million",
                "standard_output_price_micro_usd_per_million",
                "standard_cache_price_micro_usd_per_million",
                "worker_input_price_micro_usd_per_million",
                "worker_output_price_micro_usd_per_million",
                "worker_cache_price_micro_usd_per_million",
                "ethics_input_price_micro_usd_per_million",
                "ethics_output_price_micro_usd_per_million",
                "ethics_cache_price_micro_usd_per_million",
            )
        },
    }
    ai_model_price_service.fill_missing_role_prices(provider.provider_kind, price_values)
    provider.name = new_name
    provider.default_model = new_default_model
    provider.default_voice = new_default_voice
    provider.transcription_model = new_transcription_model
    _realtime_felder_setzen(provider, values)
    provider.worker_model = new_worker_model
    provider.worker_reasoning_effort = new_worker_effort
    provider.ethics_model = new_ethics_model
    provider.ethics_reasoning_effort = new_ethics_effort
    provider.ethics_mode = new_ethics_mode
    for flag, model in (
        ("standard_enabled", new_default_model),
        ("worker_enabled", new_worker_model),
        ("ethics_enabled", new_ethics_model),
        ("transcription_enabled", new_transcription_model),
    ):
        if flag in values:
            setattr(provider, flag, bool(values[flag] and model))
        elif model is None:
            setattr(provider, flag, False)
    if "realtime_enabled" in values and values["realtime_enabled"] is not None:
        if values["realtime_enabled"]:
            _assert_realtime_werte(provider)
            provider.realtime_enabled = bool(new_realtime_model)
            if not provider.realtime_enabled:
                raise AiProviderConfigurationError("Realtime-Modell fehlt, Rolle kann nicht aktiviert werden")
        else:
            provider.realtime_enabled = False
    elif new_realtime_model is None:
        provider.realtime_enabled = False
    for field in ("enabled", "requires_api_key"):
        # ``null`` heisst bei einer NOT-NULL-Spalte nicht „aus", sondern
        # „nichts gesagt" — es wird wie ein fehlendes Feld behandelt, statt als
        # `IntegrityError` mit irrefuehrender 409-Meldung zu enden.
        if field in values and values[field] is not None:
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
    # Und dieselbe Regel für die Adresse: ein aktivierter Azure-Zugang ohne
    # Ressourcennamen hat keine. Ohne diese Prüfung sähe die Zeile
    # betriebsbereit aus, stünde in der Providerauswahl des Chats und liefe
    # beim ersten Absenden in einen Fehler aus `base_url()` — weit entfernt von
    # dem Feld, das leer geblieben ist.
    if provider.enabled and ai_provider_registry.bekannt(provider.provider_kind):
        _assert_ressource(
            provider.azure_resource_name,
            pflicht=ai_provider_registry.anbieter(
                provider.provider_kind
            ).ressource_noetig,
        )
    if "token_price_micro_usd_per_million" in values:
        provider.token_price_micro_usd_per_million = values[
            "token_price_micro_usd_per_million"
        ]
    for field in (
        "standard_input_price_micro_usd_per_million",
        "standard_output_price_micro_usd_per_million",
        "standard_cache_price_micro_usd_per_million",
        "worker_input_price_micro_usd_per_million",
        "worker_output_price_micro_usd_per_million",
        "worker_cache_price_micro_usd_per_million",
        "ethics_input_price_micro_usd_per_million",
        "ethics_output_price_micro_usd_per_million",
        "ethics_cache_price_micro_usd_per_million",
    ):
        if field in values or price_values[field] != getattr(provider, field):
            setattr(provider, field, price_values[field])
    if clear_operator_api_key:
        provider.operator_api_key_encrypted = None
        provider.operator_api_key_hint = None
    elif schluessel:
        provider.operator_api_key_encrypted = DisClient.encrypt(
            schluessel, aad=_operator_aad(provider.id)
        )
        provider.operator_api_key_hint = _hint(schluessel)
    _realtime_auswahl_anwenden(
        db,
        provider,
        verlangt=values.get("realtime_default") if "realtime_default" in values else None,
    )
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


def katalogschluessel(kind: str) -> str | None:
    """Ein Betreiberschlüssel, mit dem sich der Modellkatalog dieses Anbieters lesen lässt.

    Eingehängt in `ai_model_catalog.schluesselquelle_setzen` und **nur** dort
    gerufen. Der Grund für diesen Umweg steht dort ausführlich; kurz: nur die
    Provider-Einstellungsseite hat den Schlüssel zur Hand, alle übrigen Leser
    des Katalogs müssten ihn je Anfrage neu entschlüsseln.

    Nach ``kind`` und nicht nach Zugang, weil die Frage keinen Zugang kennt:
    sie fällt in einer Hintergrundaufgabe, die nur den Anbieter kennt. Genommen
    wird der erste, der überhaupt eine Antwort verspricht — der mit der
    kleinsten Kennung, stabil und nachvollziehbar statt geraten.

    Hier stand einmal, das sei auch inhaltlich gleichgültig, weil zwei
    Betreiberschlüssel dieselbe Liste lieferten. Das gilt für OpenRouters offen
    liegenden Katalog und **nicht** für einen hinter einem Schlüssel: der
    antwortet mit dem, was dieses Konto sehen darf. Die Antwort von hier ist
    deshalb nicht „die Liste des Anbieters", sondern „die Liste *irgendeines*
    seiner Zugänge" — welcher, hält `ai_model_catalog` am Eintrag fest, und ein
    Leser mit einem anderen Schlüssel bekommt sie nicht zu sehen.

    Nur **aktivierte** Zugänge. Ein abgeschalteter Anbieter soll nicht im
    Hintergrund weiter abgefragt werden; abschalten ist auch eine Aussage über
    ausgehende Verbindungen.

    Öffnet eine **eigene** Datenbanksitzung. Der Aufrufer ist eine
    Hintergrundaufgabe ohne Anfrage und damit ohne ``Depends(get_db)``, und die
    Sitzung wird sofort wieder geschlossen: die Funktion läuft in einem Thread
    des Threadpools, und eine dort hängengebliebene Sitzung gehörte danach
    niemandem mehr.
    """
    from database import SessionLocal

    db = SessionLocal()
    try:
        zugang = (
            db.query(AiProvider)
            .filter(
                AiProvider.provider_kind == kind,
                AiProvider.enabled.is_(True),
                AiProvider.operator_api_key_encrypted.isnot(None),
            )
            .order_by(AiProvider.id)
            .first()
        )
        if zugang is None:
            return None
        return resolve_api_key(db, zugang, 0)
    finally:
        db.close()


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
        if anbieter is not None and anbieter.enabled and fuer_chat(anbieter):
            return anbieter

    # Gezaehlt werden nur **Chat**zugaenge mit konfiguriertem Standardmodell.
    # Ein reiner STT- oder TTS-Zugang kann einen Hintergrundlauf nicht tragen.
    aktive = [
        zugang
        for zugang in db.query(AiProvider)
        .filter(AiProvider.enabled.is_(True))
        .order_by(AiProvider.id)
        .all()
        if fuer_chat(zugang)
    ]
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
