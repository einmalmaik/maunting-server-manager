"""Die Zieladresse stammt aus dem Programm, nicht aus einer Eingabe.

**Diese Datei ersetzt `test_ai_provider_ssrf_rebinding.py`.** Dort standen fünf
Tests gegen DNS-Rebinding: `assert_provider_destination` hatte die geprüfte
Adresse aus einer *zweiten*, ungeprüften Namensauflösung zurückgegeben, sodass
der Adapter sich mit einer Adresse verbinden konnte, die nie gegen die
Sperrliste gehalten wurde.

Diese Tests sind nicht gestrichen, weil die Lücke unwichtig geworden wäre,
sondern weil es **die Funktion nicht mehr gibt**. Der Betreiber trägt keine
Basis-URL mehr ein; er wählt einen Anbieter aus `ai_provider_registry`. Damit
gibt es keine Eingabe, die zu einer Adresse wird — und ohne Eingabe kein SSRF.

Was hier steht, ist die Zusicherung, die an ihre Stelle tritt: **die Adresse
darf nie wieder aus Benutzerdaten stammen.** Führt jemand `base_url` als Spalte
oder Feld zurück, ohne die Prüfung mitzubringen, bricht der erste Test — und
zwar mit dem Hinweis, was damals daran hing.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from models import AiProvider
from services import ai_provider_registry, ai_provider_service


def test_a_provider_has_no_operator_supplied_address() -> None:
    """Kein Feld dieser Tabelle darf eine frei eintragbare Adresse tragen.

    Der Test greift bewusst am **Modell** an und nicht am Formular: ein Schema
    ließe sich umgehen, indem jemand direkt schreibt. Solange die Spalte fehlt,
    gibt es die Angriffsfläche nicht.
    """
    spalten = set(AiProvider.__table__.columns.keys())
    assert "base_url" not in spalten, (
        "base_url ist zurück. Mit einer frei eintragbaren Adresse braucht MSM "
        "wieder eine SSRF-Prüfung mit IP-Pinning — siehe die Historie von "
        "test_ai_provider_ssrf_rebinding.py."
    )
    assert "allow_private_network" not in spalten
    assert "provider_kind" in spalten


def test_the_address_comes_from_the_registry() -> None:
    provider = AiProvider(
        id=1, name="Test", provider_kind="openrouter",
        default_model="anthropic/claude-opus-5", enabled=True, requires_api_key=True,
    )
    assert ai_provider_service.base_url(provider) == "https://openrouter.ai/api/v1"


def test_an_unknown_kind_never_silently_becomes_an_address() -> None:
    """Ein Datenstand aus der Zukunft darf nicht bis zum HTTP-Aufruf durchkommen.

    Nach einem Downgrade kann eine Zeile einen Anbieter nennen, den diese
    Version nicht kennt. Eine leise Rückgabe von ``None`` oder ein Standardwert
    hieße, die Anfrage an *irgendwen* zu schicken — der Fehler gehört an die
    Stelle, an der er entsteht.
    """
    provider = AiProvider(
        id=2, name="Zukunft", provider_kind="anbieter-von-morgen",
        default_model="x", enabled=True, requires_api_key=True,
    )
    with pytest.raises(KeyError):
        ai_provider_service.base_url(provider)


def test_every_registry_entry_is_complete_and_addressable() -> None:
    """Jeder Eintrag muss benutzbar sein — sonst steht er nur da.

    Ohne Katalog gibt es keine Modellauswahl und damit keine Denkstufen; ein
    Anbieter ohne `catalog_url` wäre ein Rückschritt hinter genau das, wofür
    diese Registry gebaut wurde.
    """
    for spec in ai_provider_registry.alle():
        assert spec.base_url.startswith("https://"), spec.kind
        assert spec.catalog_url.startswith("https://"), spec.kind
        assert spec.key_url.startswith("https://"), spec.kind
        assert spec.label.strip()
        assert ai_provider_registry.anbieter(spec.kind) is spec


def test_creating_a_provider_rejects_an_unknown_kind(db: Session) -> None:
    with pytest.raises(ai_provider_service.AiProviderConfigurationError):
        ai_provider_service.create_provider(
            db, name="Falsch", provider_kind="gibt-es-nicht",
            default_model="x", enabled=True, requires_api_key=True,
            operator_api_key=None,
        )


def test_a_key_with_the_wrong_prefix_is_caught_before_the_provider_sees_it(
    db: Session,
) -> None:
    """Plausibilität, nicht Gültigkeit — aber sie erspart einen Umweg.

    Wer einen OpenAI-Schlüssel bei OpenRouter einträgt, bekam bisher erst beim
    Testaufruf eine Fehlermeldung des Anbieters. Das Präfix ist eine sichere
    Aussage in genau einer Richtung: falsches Präfix heißt sicher falsch,
    richtiges heißt noch gar nichts.
    """
    with pytest.raises(ai_provider_service.AiProviderConfigurationError):
        ai_provider_service.create_provider(
            db, name="Falscher Schluessel", provider_kind="openrouter",
            default_model="anthropic/claude-opus-5", enabled=True,
            requires_api_key=True, operator_api_key="sk-proj-abcdef",
        )

    provider = ai_provider_service.create_provider(
        db, name="Richtig", provider_kind="openrouter",
        default_model="anthropic/claude-opus-5", enabled=True,
        requires_api_key=True, operator_api_key="sk-or-v1-abcdef",
    )
    assert provider.provider_kind == "openrouter"
    assert provider.operator_api_key_hint == "********cdef"


def test_a_parked_provider_cannot_be_reactivated_by_a_mere_checkbox(
    db: Session,
) -> None:
    """Die Migration parkt fremde Zugänge mit leerem Anbieter — das muss halten.

    `20260811_01` schaltet jede Zeile ab, deren Adresse zu keinem
    unterstützten Anbieter gehörte, und leert ihren Schlüssel. Ohne diese
    Prüfung genügte ein Haken bei „aktiv“, und der nächste Chat liefe in einen
    `KeyError` aus `base_url()` — ein 500 statt einer Erklärung.
    """
    provider = ai_provider_service.create_provider(
        db, name="Geparkt", provider_kind="openrouter",
        default_model="x", enabled=False, requires_api_key=False,
        operator_api_key=None,
    )
    # So sieht die Zeile nach der Migration aus.
    provider.provider_kind = ""
    db.flush()

    with pytest.raises(ai_provider_service.AiProviderConfigurationError):
        ai_provider_service.update_provider(
            db, provider, values={"enabled": True},
            operator_api_key=None, clear_operator_api_key=False,
        )

    # Mit einem Anbieter geht es — das ist der vorgesehene Weg.
    ai_provider_service.update_provider(
        db, provider, values={"enabled": True, "provider_kind": "openrouter"},
        operator_api_key=None, clear_operator_api_key=False,
    )
    assert provider.enabled is True


def test_switching_the_kind_never_carries_the_old_key_along(db: Session) -> None:
    """Der gespeicherte Schluessel gehoert zum alten Anbieter.

    Ohne diese Regel genuegte ein Dropdown-Wechsel von OpenRouter auf OpenAI,
    und der naechste Test oder Chat schickte den ``sk-or-…``-Schluessel als
    ``Authorization: Bearer`` an ``api.openai.com`` — ein Geheimnis an eine
    Partei, fuer die es nie ausgestellt wurde. Die Praefixpruefung beim
    Anlegen verhindert genau das; sie darf beim Wechsel nicht wirkungslos sein.
    """
    provider = ai_provider_service.create_provider(
        db, name="Wechselt", provider_kind="openrouter",
        default_model="anthropic/claude-opus-5", enabled=True,
        requires_api_key=True, operator_api_key="sk-or-v1-abcdef",
    )
    assert provider.operator_api_key_encrypted is not None

    ai_provider_service.update_provider(
        db, provider, values={"provider_kind": "openai"},
        operator_api_key=None, clear_operator_api_key=False,
    )
    assert provider.provider_kind == "openai"
    assert provider.operator_api_key_encrypted is None
    assert provider.operator_api_key_hint is None

    # Ein im selben Aufruf mitgeschickter neuer Schluessel wird regulaer
    # gespeichert — und gegen den **neuen** Anbieter geprueft.
    ai_provider_service.update_provider(
        db, provider,
        values={"provider_kind": "openrouter"},
        operator_api_key="sk-or-v1-neu", clear_operator_api_key=False,
    )
    assert provider.operator_api_key_hint is not None

    # Derselbe Anbieter noch einmal ist kein Wechsel — der Schluessel bleibt.
    ai_provider_service.update_provider(
        db, provider, values={"provider_kind": "openrouter"},
        operator_api_key=None, clear_operator_api_key=False,
    )
    assert provider.operator_api_key_encrypted is not None


def test_an_explicit_null_is_an_explanation_not_a_500(db: Session) -> None:
    """`exclude_unset` laesst ein ausdrueckliches ``null`` durch.

    ``str(None)`` ist die wahre Zeichenkette ``"None"`` — die Leerpruefung
    schlug nicht an, und zwei Zeilen tiefer endete ``None.strip()`` als
    ``AttributeError`` im 500. Bei ``enabled`` setzte dasselbe ``null`` eine
    NOT-NULL-Spalte und endete als ``IntegrityError`` mit der irrefuehrenden
    Meldung „Provider-Name ist bereits vergeben".
    """
    provider = ai_provider_service.create_provider(
        db, name="Null", provider_kind="openrouter",
        default_model="anthropic/claude-opus-5", enabled=True,
        requires_api_key=False, operator_api_key=None,
    )

    for field in ("name", "default_model"):
        with pytest.raises(ai_provider_service.AiProviderConfigurationError):
            ai_provider_service.update_provider(
                db, provider, values={field: None},
                operator_api_key=None, clear_operator_api_key=False,
            )

    # ``null`` bei einer NOT-NULL-Spalte heisst „nichts gesagt", nicht „aus".
    ai_provider_service.update_provider(
        db, provider, values={"enabled": None, "requires_api_key": None},
        operator_api_key=None, clear_operator_api_key=False,
    )
    assert provider.enabled is True
    assert provider.requires_api_key is False


def test_the_recommendation_is_a_model_id_and_nothing_else() -> None:
    """Die Empfehlung ist eine Kennung, die der Katalog fuehren kann.

    Sie ist die einzige Meinung in dieser Datei — alles andere dort sind
    Tatsachen (Adresse, Katalogadresse, Schluesselpraefix). Meinungen veralten,
    Tatsachen nicht, und deshalb braucht genau diese eine Angabe eine Form, die
    ein Fehlgehen ueberlebt: eine Modellkennung, die die Oberflaeche gegen den
    Katalog abgleicht.

    Der Test prueft bewusst **nicht**, dass der Anbieter das Modell heute fuehrt.
    Das waere ein Netzabruf in der Testsuite und eine Zusage ueber einen fremden
    Dienst, die MSM nicht halten kann. Er prueft die Form — dass die Empfehlung
    ueberhaupt jemals auf einen Katalogeintrag passen kann.

    Gemessen wird dafuer am **Katalogleser des jeweiligen Anbieters** und nicht
    an einer Formregel in diesem Test. Der Leser ist die eine Stelle, die
    entscheidet, was aus einem Katalog ueberhaupt ein `Modell` wird; weist er
    die Empfehlung ab, kann sie im Betrieb nie erscheinen, egal wie sie aussieht.
    Eine zweite Formregel hier waere eine zweite Wahrheit — und sie war eine:
    frueher stand hier ein hartes ``"/" in empfehlung``, weil OpenRouter seine
    Kennungen als ``anbieter/modell`` fuehrt. Beim zweiten Anbieter schlug das
    fehl, obwohl ``eleven_flash_v2_5`` voellig richtig ist. ElevenLabs hat
    schlicht kein Praefix.
    """
    from services import ai_provider_registry
    from services.ai_model_catalog import _LESER

    # Wie ein Katalogeintrag dieses Anbieters mindestens aussieht. Je Anbieter
    # eine eigene Form, und das ist keine Umstaendlichkeit, sondern die Lage:
    # OpenRouter nennt die Kennung ``id``, ElevenLabs ``model_id`` und verlangt
    # zusaetzlich die Zusage, dass das Modell ueberhaupt vorlesen kann. Ein
    # gemeinsamer Probeeintrag wuerde von einem der beiden Leser verworfen — und
    # dieser Test bestuende dann aus einer Zusicherung, die immer haelt.
    probe = {
        "openrouter": lambda kennung: {"id": kennung},
        "elevenlabs": lambda kennung: {
            "model_id": kennung, "can_do_text_to_speech": True
        },
    }

    for kind, spec in ai_provider_registry.ANBIETER.items():
        if spec.empfehlung is None:
            continue
        assert spec.empfehlung == spec.empfehlung.strip(), kind
        assert kind in probe, (
            f"Neuer Anbieter {kind!r} ohne Probeeintrag — ohne ihn prueft diese "
            f"Zusage seine Empfehlung nicht mit."
        )
        assert _LESER[kind](probe[kind](spec.empfehlung)) is not None, (
            f"Der Katalogleser von {kind} wuerde die Empfehlung "
            f"{spec.empfehlung!r} verwerfen — sie kann dort nie erscheinen."
        )

    # Und die beiden Anbieter, die es heute gibt, haben auch wirklich eine.
    # Die OpenRouter-Kennung traegt zusaetzlich das anbietereigene Praefix; das
    # steht hier ausdruecklich bei OpenRouter und nicht in der Schleife oben.
    assert ai_provider_registry.ANBIETER["openrouter"].empfehlung == "openai/gpt-5.6-luna"
    assert "/" in ai_provider_registry.ANBIETER["openrouter"].empfehlung
    assert ai_provider_registry.ANBIETER["elevenlabs"].empfehlung == "eleven_flash_v2_5"
