"""Die Zieladresse stammt aus dem Programm, nicht aus einer Eingabe.

**Diese Datei ersetzt `test_ai_provider_ssrf_rebinding.py`.** Dort standen fünf
Tests gegen DNS-Rebinding: `assert_provider_destination` hatte die geprüfte
Adresse aus einer *zweiten*, ungeprüften Namensauflösung zurückgegeben, sodass
der Adapter sich mit einer Adresse verbinden konnte, die nie gegen die
Sperrliste gehalten wurde.

Diese Tests sind nicht gestrichen, weil die Lücke unwichtig geworden wäre,
sondern weil es **die Funktion nicht mehr gibt**. Der Betreiber trägt keine
Basis-URL mehr ein; er wählt einen Anbieter aus `ai_provider_registry`.

Was hier steht, ist die Zusicherung, die an ihre Stelle tritt: **die Adresse
darf nie wieder aus Benutzerdaten stammen.** Führt jemand `base_url` als Spalte
oder Feld zurück, ohne die Prüfung mitzubringen, bricht der erste Test — und
zwar mit dem Hinweis, was damals daran hing.

**Seit Azure ist die Zusicherung enger gefasst, nicht schwächer.** Dort hat
jede Ressource ihren eigenen Host, und ohne dessen Namen ist der Anbieter nicht
erreichbar — es gibt also wieder eine Betreibereingabe im Hostnamen. Was sie
von der alten `base_url` unterscheidet, ist genau das, was die Tests hier
festhalten: es ist **ein DNS-Label und keine Adresse**. Schema, Suffix und Pfad
stehen als Vorlage im Programm, und das Label muss die Form eines Labels haben
— kein Schema, kein Punkt, kein Schrägstrich, kein Doppelpunkt, keine
Steuerzeichen, höchstens 63 Zeichen.

Wer diese Prüfung aufweicht, bricht `test_only_a_dns_label_ever_reaches_a_host`
— und wer eine Adresse ganz durchreichen will, bricht den ersten Test.
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
    # ``azure_resource_name`` ist die eine erlaubte Ausnahme, und sie ist auf
    # ein Label beschränkt — festgehalten in
    # `test_only_a_dns_label_ever_reaches_a_host`. Sie steht hier ausdrücklich
    # in der Liste, damit die nächste Spalte dieser Art nicht unbemerkt daneben
    # entsteht.
    assert spalten >= {"azure_resource_name"}


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

    ``catalog_url`` darf seit Azure ``None`` sein, und das ist eine bewusste
    Lockerung: dort heisst ein Modell so, wie der Betreiber sein Deployment
    genannt hat, und eine Liste dafür gibt es nicht. Ein Anbieter, der eine
    Katalogadresse **hat**, muss sie weiterhin über HTTPS führen — eine halbe
    Angabe wäre schlimmer als keine.

    Die Lücke ``{ressource}`` gehört ausschliesslich Anbietern, die sie auch
    angemeldet haben. Ohne diese Zusage entstünde der teuerste Fehler dieser
    Ecke: ``httpx.URL`` nimmt eine ungefüllte Vorlage klaglos an, kodiert die
    geschweiften Klammern in den Hostnamen und stirbt erst zur Laufzeit als
    DNS-Fehler — an einer Stelle, an der niemand die Ursache abliest.
    """
    for spec in ai_provider_registry.alle():
        assert spec.base_url.startswith("https://"), spec.kind
        if spec.catalog_url is not None:
            assert spec.catalog_url.startswith("https://"), spec.kind
        assert spec.key_url.startswith("https://"), spec.kind
        assert spec.label.strip()
        assert ai_provider_registry.anbieter(spec.kind) is spec
        # Vorlage und Marke gehören zusammen, in beide Richtungen.
        assert ("{ressource}" in spec.base_url) is spec.ressource_noetig, spec.kind
        if not spec.ressource_noetig:
            assert "{" not in spec.base_url, spec.kind
            assert "{" not in (spec.catalog_url or ""), spec.kind


def test_the_form_learns_about_hearing_from_the_same_field_the_voice_router_reads() -> None:
    """Was das Formular über Gehör sagt, entscheidet ``gehoer_wege``.

    ``routers/ai_voice.py`` überspringt jeden Zugang ohne ``gehoer_wege`` —
    gleich, was in seinem Transkriptmodell steht. Seit Azure gibt es
    Chatanbieter ohne Gehör, und ohne diese Ableitung verspräche die
    Oberfläche an zwei Stellen etwas, das der Sprachmodus nie einlöst.

    Der Test hängt bewusst am Router und nicht an der Registry: die Ableitung
    ist die Stelle, an der die beiden auseinanderlaufen könnten.
    """
    from routers.ai_providers import list_provider_kinds

    antwort = {eintrag.kind: eintrag for eintrag in list_provider_kinds(_=None)}  # type: ignore[arg-type]
    assert antwort, "die Liste der Anbieter darf nie leer sein"
    for spec in ai_provider_registry.alle():
        assert antwort[spec.kind].kann_hoeren is bool(spec.gehoer_wege), spec.kind
    # Und beide Seiten der Unterscheidung müssen es wirklich geben, sonst
    # prüfte die Schleife oben nur eine Hälfte.
    assert any(eintrag.kann_hoeren for eintrag in antwort.values())
    assert any(not eintrag.kann_hoeren for eintrag in antwort.values())


def test_only_a_dns_label_ever_reaches_a_host() -> None:
    """Der Betreiber steuert ein Label bei, nie eine Adresse.

    Das ist die Zusicherung, die seit Azure an die Stelle von „es gibt gar
    keine Eingabe" tritt. Geprüft wird der Service und nicht das Schema: ein
    Vertrag lässt sich umgehen, indem jemand direkt schreibt, und `base_url()`
    ist die letzte Station vor dem HTTP-Aufruf.

    Die Liste ist keine Sammlung von Kuriositäten. Jeder Eintrag ist ein Weg,
    aus einem Namensfeld ein anderes Ziel zu machen: ein Punkt wechselt die
    Domäne, ein Schrägstrich hängt einen Pfad an, ein Doppelpunkt einen Port,
    ein ``@`` verlegt den ganzen Host hinter eine Benutzerangabe, und ein
    Zeilenumbruch in der Mitte teilt die Anfrage.
    """
    provider = AiProvider(
        id=3, name="Azure", provider_kind="azure_openai",
        default_model="gpt-5.1", enabled=True, requires_api_key=True,
    )

    for boese in (
        "meine.ressource",          # andere Domäne
        "meine/ressource",          # Pfad
        "meine:8080",               # Port
        "boese@meine",              # Benutzerangabe vor dem Host
        "meine ressource",          # Leerzeichen
        "meine\nressource",         # Request-Splitting
        "meine\rressource",
        "meine_ressource",          # in einem Hostnamen nicht zulässig
        "-meine",                   # führender Bindestrich
        "meine-",                   # Azure verbietet ihn ausdrücklich
        "m",                        # zu kurz für einen Azure-Namen
        "xn--meine",                # Punycode, uneinheitlich aufgelöst
        "a" * 64,                   # ein DNS-Label darf höchstens 63 tragen
        "https://meine.openai.azure.com",
        "",
        None,
    ):
        provider.azure_resource_name = boese
        with pytest.raises(ai_provider_service.AiProviderConfigurationError):
            ai_provider_service.base_url(provider)

    # Und der gute Fall ergibt genau die Adresse aus der Anbieterdatei.
    provider.azure_resource_name = "mein-ai-hub"
    assert ai_provider_service.base_url(provider) == (
        "https://mein-ai-hub.services.ai.azure.com/openai/v1"
    )
    # Claude endet vor der Version: ``/v1/messages`` haengt der Adapter an, so
    # wie Azure die Adresse im Portal zeigt und Anthropics SDK sie erwartet.
    provider.provider_kind = "azure_anthropic"
    assert ai_provider_service.base_url(provider) == (
        "https://mein-ai-hub.services.ai.azure.com/anthropic"
    )

    # Ein abschliessender Zeilenumbruch ist kein Angriff, sondern ein
    # Copy-and-paste: er wird entfernt statt abgewiesen. Entscheidend ist, dass
    # er die Funktion nicht überlebt.
    provider.azure_resource_name = "mein-ai-hub\n"
    assert "\n" not in ai_provider_service.base_url(provider)


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
        leser = ai_provider_registry.katalog_leser(kind)
        assert leser(probe[kind](spec.empfehlung)) is not None, (
            f"Der Katalogleser von {kind} wuerde die Empfehlung "
            f"{spec.empfehlung!r} verwerfen — sie kann dort nie erscheinen."
        )

    # Und die beiden Anbieter, die es heute gibt, haben auch wirklich eine.
    # Die OpenRouter-Kennung traegt zusaetzlich das anbietereigene Praefix; das
    # steht hier ausdruecklich bei OpenRouter und nicht in der Schleife oben.
    assert ai_provider_registry.ANBIETER["openrouter"].empfehlung == "openai/gpt-5.6-luna"
    assert "/" in ai_provider_registry.ANBIETER["openrouter"].empfehlung
    assert ai_provider_registry.ANBIETER["elevenlabs"].empfehlung == "eleven_flash_v2_5"


def test_an_azure_access_cannot_exist_without_its_resource(db: Session) -> None:
    """Eine Zeile ohne Namen sähe eingerichtet aus und täte nie etwas.

    Der Fehler fiele sonst erst beim ersten Chat auf — weit entfernt von dem
    Feld, das leer geblieben ist.
    """
    with pytest.raises(ai_provider_service.AiProviderConfigurationError):
        ai_provider_service.create_provider(
            db, name="Azure ohne Namen", provider_kind="azure_openai",
            default_model="gpt-5.1", enabled=True, requires_api_key=True,
            operator_api_key=None,
        )

    provider = ai_provider_service.create_provider(
        db, name="Azure", provider_kind="azure_openai",
        default_model="gpt-5.1", enabled=True, requires_api_key=True,
        operator_api_key=None, azure_resource_name="mein-ai-hub",
    )
    assert provider.azure_resource_name == "mein-ai-hub"
    assert ai_provider_service.base_url(provider) == (
        "https://mein-ai-hub.services.ai.azure.com/openai/v1"
    )


def test_a_provider_that_needs_no_resource_ignores_the_field(db: Session) -> None:
    """Wie bei Stimme und Gehör: nicht gegen den Anbieter geprüft.

    Der Anbieter lässt sich später ändern, und ein stillschweigend gelöschter
    Wert wäre ärgerlicher als ein ungenutzter.
    """
    provider = ai_provider_service.create_provider(
        db, name="OpenRouter mit Namen", provider_kind="openrouter",
        default_model="anthropic/claude-opus-5", enabled=True,
        requires_api_key=False, operator_api_key=None,
        azure_resource_name="ungenutzt",
    )
    assert provider.azure_resource_name == "ungenutzt"
    assert ai_provider_service.base_url(provider) == "https://openrouter.ai/api/v1"


def test_switching_the_resource_never_carries_the_old_key_along(db: Session) -> None:
    """Dieselbe Invariante wie beim Anbieterwechsel, zweite Lücke.

    Bei Azure ist ein Schlüssel an genau **eine** Ressource gebunden, und der
    Namensraum ist global und frei belegbar. Bliebe er beim Wechsel stehen,
    schickte ein Tippfehler im Ressourcennamen den Betreiberschlüssel an eine
    fremde Azure-Ressource — bei gleichbleibendem `provider_kind`, an dem die
    ältere Regel nicht anschlägt.
    """
    provider = ai_provider_service.create_provider(
        db, name="Azure wechselt", provider_kind="azure_openai",
        default_model="gpt-5.1", enabled=True, requires_api_key=True,
        operator_api_key="azure-geheim", azure_resource_name="hub-a",
    )
    assert provider.operator_api_key_encrypted is not None

    ai_provider_service.update_provider(
        db, provider, values={"azure_resource_name": "hub-b"},
        operator_api_key=None, clear_operator_api_key=False,
    )
    assert provider.azure_resource_name == "hub-b"
    assert provider.operator_api_key_encrypted is None
    assert provider.operator_api_key_hint is None

    # Derselbe Name noch einmal ist kein Wechsel — der Schlüssel bleibt.
    ai_provider_service.update_provider(
        db, provider, values={"azure_resource_name": "hub-b"},
        operator_api_key="azure-neu", clear_operator_api_key=False,
    )
    assert provider.operator_api_key_encrypted is not None
    ai_provider_service.update_provider(
        db, provider, values={"azure_resource_name": "hub-b"},
        operator_api_key=None, clear_operator_api_key=False,
    )
    assert provider.operator_api_key_encrypted is not None

    # Und ein Feld, das gar nicht mitkam, ist erst recht kein Wechsel.
    ai_provider_service.update_provider(
        db, provider, values={"name": "Anders"},
        operator_api_key=None, clear_operator_api_key=False,
    )
    assert provider.operator_api_key_encrypted is not None


def test_an_azure_access_cannot_be_activated_without_its_resource(db: Session) -> None:
    """Sonst stünde die Zeile in der Providerauswahl des Chats und liefe leer."""
    provider = ai_provider_service.create_provider(
        db, name="Azure geparkt", provider_kind="azure_anthropic",
        default_model="claude-sonnet-5", enabled=False, requires_api_key=True,
        operator_api_key=None, azure_resource_name="hub-a",
    )
    ai_provider_service.update_provider(
        db, provider, values={"azure_resource_name": None},
        operator_api_key=None, clear_operator_api_key=False,
    )
    assert provider.azure_resource_name is None

    with pytest.raises(ai_provider_service.AiProviderConfigurationError):
        ai_provider_service.update_provider(
            db, provider, values={"enabled": True},
            operator_api_key=None, clear_operator_api_key=False,
        )

    # Mit Namen geht es — das ist der vorgesehene Weg.
    ai_provider_service.update_provider(
        db, provider,
        values={"enabled": True, "azure_resource_name": "hub-c"},
        operator_api_key=None, clear_operator_api_key=False,
    )
    assert provider.enabled is True
