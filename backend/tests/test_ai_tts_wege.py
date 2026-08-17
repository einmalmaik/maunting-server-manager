"""Der Sprachdienst ist austauschbar — und hier steht, woran man das merkt.

`test_ai_voice_wandler` prueft, wie ElevenLabs vorliest. Hier steht die Stufe
darueber: dass ihn ausser `ai_tts` **niemand** beim Namen kennt.

Bis zum 17.08.2026 war das anders. Beide Sprachrouter und `ai_voice_bridge`
importierten `ai_tts_elevenlabs` direkt, zusammen an acht Stellen — drei davon
blosse Typangaben, die Python nicht prueft. Ein Wechsel des Sprachdienstes hiess
damit: acht fremde Stellen finden, und die drei stillen zuletzt.

Diese Datei ist der Ersatz fuer das Suchen. Sie sichert vier Dinge zu:

* Kein Modul ausserhalb von `ai_tts` importiert einen Sprachdienst. Wer es
  wieder tut, bekommt hier den Grund zu lesen und nicht bloss ein rotes Feld.
* Jeder verkabelte Sprachdienst kann, was `ai_tts.Stimmweg` aufzaehlt — ein
  Protokoll, das niemand erfuellt, waere Papier.
* Ein Anbieter ohne Sprachdienst bekommt eine Meldung und keinen Absturz. Das
  ist der Fall „Datei geloescht, Eintrag stehengeblieben", und genau ihn haelt
  der spaete Import offen.
* Jeder Anbieter, der laut Registry spricht, hat auch jemanden, der spricht.
  Ein Eintrag ohne Dienst waere ein Sprachknopf ins Leere.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from services import ai_provider_registry, ai_tts
from services.openai_compatible_adapter import AiProviderRequestError

#: Die Wurzel des Backends — von ``tests/`` aus eine Ebene hoeher.
_BACKEND = Path(__file__).resolve().parent.parent

#: Ordner, in denen ein Import nichts ueber die Bauform aussagt.
#:
#: `tests/` steht hier, weil `test_ai_voice_wandler` das Vorlesen selbst prueft
#: und dafuer an das Modul heran muss; `migrations/` ist festgeschriebene
#: Vergangenheit, die niemand mehr anfasst. Alles andere wird durchsucht —
#: `scripts/` und `tools/` eingeschlossen, denn auch ein Wartungsskript, das
#: den Sprachdienst beim Namen nennt, kostet beim Ausbau eine fremde Datei.
_UEBERSPRUNGENE_ORDNER = {"tests", "migrations", "__pycache__", ".venv", "venv"}

#: Wer den Sprachdienst importieren darf. Genau der Controller, und sonst
#: niemand — was `_eigene_dateien` ergaenzt, ist keine Ausnahme, sondern die
#: Datei des Dienstes selbst.
_DARF_IMPORTIEREN = {"services/ai_tts.py"}


def _dateien() -> list[Path]:
    """Jede Python-Datei des Backends, ausser den uebersprungenen Ordnern.

    Vorher stand hier ``glob("services/*.py")`` und ``glob("routers/*.py")`` —
    zwei flache Muster. Das liess drei Luecken offen, und sie sind keine
    theoretischen: `services/ai_provider_registry` ist seit dem Umbau ein
    **Paket**, seine fuenf Dateien lagen damit ausserhalb der Suche; ebenso
    `middleware/`, `tools/` und `scripts/`. Ein Test, der eine Zusage fuer das
    ganze Backend macht und nur zwei Ordnerebenen liest, sagt „gruen" fuer
    Dateien, die er nie geoeffnet hat.
    """
    return sorted(
        datei
        for datei in _BACKEND.rglob("*.py")
        if not (
            _UEBERSPRUNGENE_ORDNER
            & set(datei.relative_to(_BACKEND).parts[:-1])
        )
    )


def _eigene_dateien() -> set[str]:
    """Die Datei eines Sprachdienstes selbst — mehr ist keine Ausnahme.

    Vorher stand hier ``stem.startswith("ai_tts_")``, und das ist etwas ganz
    anderes: es haette auch einen **zweiten** Sprachdienst freigestellt, der
    den ersten importiert, um sich dessen Textzerlegung zu leihen. Genau diese
    Verkabelung soll der Test verhindern — sie macht das Loeschen des einen zum
    Bruch des anderen. Gefragt wird darum die Verkabelungsstelle selbst, nicht
    ein Namensanfang: was in `ai_tts._stimmen()` steht, darf sich selbst.
    """
    return {
        Path(weg.__file__).resolve().relative_to(_BACKEND).as_posix()
        for weg in ai_tts._stimmen().values()
        if getattr(weg, "__file__", None)
    }


def _importierte_module(datei: Path) -> set[str]:
    """Welche Module diese Datei importiert — ueber den Syntaxbaum.

    Ueber `ast` und nicht ueber eine Textsuche, weil ein Kommentar, der einen
    Sprachdienst **erwaehnt**, keine Abhaengigkeit ist: `permission_catalog`
    nennt seinen Zeichendeckel als Quelle, und diesen Hinweis zu verbieten
    hiesse, eine Doku wegen einer Textsuche zu verschlechtern. Was zaehlt, ist
    der Import — er ist es, der beim Loeschen der Datei bricht.
    """
    namen: set[str] = set()
    baum = ast.parse(datei.read_text(encoding="utf-8"))
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Import):
            namen.update(teil.name for teil in knoten.names)
        elif isinstance(knoten, ast.ImportFrom):
            if knoten.module:
                namen.add(knoten.module)
            namen.update(f"{knoten.module or ''}.{teil.name}" for teil in knoten.names)
            namen.update(teil.name for teil in knoten.names)
    return namen


def test_ausser_dem_controller_importiert_niemand_einen_sprachdienst() -> None:
    dienste = set(ai_tts._stimmen())
    erlaubt = _DARF_IMPORTIEREN | _eigene_dateien()
    gefunden: list[str] = []
    gelesen = 0

    for datei in _dateien():
        pfad = datei.relative_to(_BACKEND).as_posix()
        if pfad in erlaubt:
            continue
        gelesen += 1
        importiert = _importierte_module(datei)
        for kind in dienste:
            if any(name.endswith(f"ai_tts_{kind}") for name in importiert):
                gefunden.append(f"{pfad} importiert ai_tts_{kind}")

    assert not gefunden, (
        "Ein Sprachdienst wird wieder direkt importiert:\n  "
        + "\n  ".join(gefunden)
        + "\nDamit kostet ein Wechsel des Anbieters wieder mehr als eine Zeile in "
        "ai_tts._stimmen(). Der Weg dorthin fuehrt ueber ai_tts.stimmweg(kind)."
    )
    # Ein Suchtest ohne Fund ist von einem Suchtest ohne Suche nicht zu
    # unterscheiden — ausser man zaehlt mit. Die Zahl ist bewusst grob: sie
    # soll ein leeres Muster fangen, nicht bei jeder neuen Datei umfallen.
    assert gelesen > 100, f"Nur {gelesen} Dateien gelesen — das Suchmuster greift nicht"


def test_der_waechter_sucht_auch_dort_wo_die_luecke_war() -> None:
    """Paketordner und Router muessen in der Suche liegen, nicht bloss daneben.

    Der Wortlaut der Zusage oben ist „kein Modul ausserhalb von `ai_tts`" — und
    zwischen dieser Zusage und dem Muster, das sie einloest, lag eine Weile ein
    Unterschied. Dieser Test steht zwischen beiden: er prueft die **Suche**,
    nicht das Ergebnis, und faellt darum auch dann um, wenn gerade niemand
    falsch importiert.
    """
    gefunden = {datei.relative_to(_BACKEND).as_posix() for datei in _dateien()}

    for pflicht in (
        "services/ai_tts.py",
        "services/ai_voice_bridge.py",
        "routers/ai_voice.py",
        # Der Umbau vom Modul zum Paket: fuenf Dateien, die das flache Muster
        # `services/*.py` schlicht nicht mehr traf.
        "services/ai_provider_registry/__init__.py",
        "services/ai_provider_registry/basis.py",
    ):
        assert pflicht in gefunden, f"{pflicht} liegt ausserhalb der Suche"

    assert not any(
        pfad.startswith("tests/") or pfad.startswith("migrations/")
        for pfad in gefunden
    ), "Uebersprungene Ordner werden doch gelesen"


def test_jeder_verkabelte_sprachdienst_kann_was_das_protokoll_verlangt() -> None:
    """Das Protokoll ist keine Absicht, sondern eine Bedingung.

    Geprueft wird auf Vorhandensein und nicht mit ``isinstance``: `Stimmweg` ist
    kein `runtime_checkable`, weil ein Laufzeit-Check dort ohnehin nur Namen
    zaehlen wuerde — und das tut dieser Test schon, mit einer Fehlermeldung, die
    den fehlenden Namen nennt.
    """
    verlangt = ("STIMME_MOEGLICH", "UNMOEGLICH_GRUND", "Stimme",
                "verbindungsadresse", "pruefen", "probe_fehlercode")

    for kind, weg in ai_tts._stimmen().items():
        for name in verlangt:
            assert hasattr(weg, name), (
                f"Der Sprachdienst von {kind!r} hat kein {name!r}. "
                f"Was ein Sprachdienst koennen muss, steht in ai_tts.Stimmweg."
            )
        assert isinstance(weg.UNMOEGLICH_GRUND, str) and weg.UNMOEGLICH_GRUND.strip()


def test_ein_anbieter_ohne_sprachdienst_bekommt_eine_meldung_und_keinen_absturz() -> None:
    """Der Fall „Datei geloescht, Eintrag stehengeblieben".

    Er trifft den, der sprechen will, und nicht den Start der Anwendung — das
    ist der ganze Zweck des spaeten Imports in `ai_tts._stimmen`. Ein
    ``AttributeError`` an dieser Stelle waere ein 500 statt einer Auskunft.
    """
    assert ai_tts.moeglich("openrouter") is False
    grund = ai_tts.unmoeglich("openrouter")
    assert grund and "Sprachdienst" in grund

    with pytest.raises(AiProviderRequestError) as fehler:
        ai_tts.stimmweg("openrouter")
    assert fehler.value.code == "AI_PROVIDER_TTS_UNSUPPORTED"


def test_jeder_sprechende_anbieter_hat_auch_jemanden_der_spricht() -> None:
    """Ein Anbieter mit ``protokoll="tts"`` und ohne Dienst waere ein Knopf ins Leere.

    Die Registry sagt, **dass** er spricht; `ai_tts` sagt, **wer**. Auseinander
    laufen die beiden genau dann, wenn jemand einen Anbieter eintraegt und die
    Verkabelung vergisst — und das faellt sonst erst dem auf, der ihn auswaehlt.
    """
    dienste = set(ai_tts._stimmen())
    for kind, spec in ai_provider_registry.ANBIETER.items():
        if spec.protokoll != ai_provider_registry.TTS:
            continue
        assert kind in dienste, (
            f"{spec.label} ist als Sprachanbieter eingetragen, aber in "
            f"ai_tts._stimmen() steht kein Dienst fuer ihn."
        )


def test_eine_fehlende_bibliothek_kostet_den_sprachmodus_und_nennt_den_grund(
    monkeypatch,
) -> None:
    """Der Betreiber soll eine Bibliothek nachinstallieren, keinen Schluessel suchen.

    Deshalb steht der Grund am Sprachdienst und nicht im Router: nur der Dienst
    weiss, was ihm fehlt. Der Router reicht den Satz weiter, ohne ihn zu kennen.
    """
    from services import ai_tts_elevenlabs

    monkeypatch.setattr(ai_tts_elevenlabs, "STIMME_MOEGLICH", False)

    assert ai_tts.moeglich("elevenlabs") is False
    assert ai_tts.unmoeglich("elevenlabs") == ai_tts_elevenlabs.UNMOEGLICH_GRUND
    # Der Weg selbst bleibt auffindbar — nicht laufen zu koennen ist etwas
    # anderes als nicht zu existieren, und nur das Zweite ist ein Fehler.
    assert ai_tts.stimmweg("elevenlabs") is ai_tts_elevenlabs
