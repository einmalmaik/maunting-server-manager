"""Gewuenschte Konfigurationswerte je Server — der Speicher.

**Das Problem in einem Satz:** Eine Aenderung an einer Konfigurationsdatei ist
nur so dauerhaft, wie der Prozess sie laesst, dem die Datei gehoert.

Gemessen auf Server 107 (15.–19.08.2026): ein ausgefuehrter Vorschlag stand vier
Tage spaeter nicht mehr in der Datei. ARK haelt seine Einstellungen im Speicher
und schreibt ``GameUserSettings.ini`` beim Autosave vollstaendig neu; alles, was
der laufende Prozess nicht kennt, verwirft er dabei. Fuer den Benutzer sah das
aus, als haette die KI ihre Arbeit nur behauptet.

**Warum nicht "Server muss gestoppt sein".** Weil das die Ursache verfehlt und
gleichzeitig zu viel verlangt. Zu viel: die Mehrheit der Spiele liest ihre
Konfiguration beim Start und schreibt sie nie zurueck — dort aendert man
gefahrlos im Betrieb und startet spaeter neu. Zu wenig: bei ARK ueberlebt der
Wert zwar den naechsten Neustart, aber nicht den uebernaechsten Autosave.

**Warum keine Liste "diese Spiele schreiben zurueck".** Weil eine Liste, die
gepflegt werden muss, still versagt. Ein vergessener Eintrag wirft keinen
Fehler; er laesst nur irgendwann wieder eine Aenderung verschwinden, und
niemand sieht den Zusammenhang. MSM soll ausserdem perspektivisch mehr als
Spielserver verwalten — was eine Aufzaehlung braucht, waechst nicht mit.

**Und warum es fuer jedes Dateiformat gilt.** Eine erste Fassung nahm nur
INI-artige Dateien an. Das war dieselbe Sorte Einschraenkung eine Ebene tiefer:
Spiele schreiben auch XML, JSON, YAML und Properties beim Start zurueck, und wer
die Endungsliste pflegen muss, vergisst sie. Deshalb entscheidet hier nicht das
Format, sondern der **Anker**:

* ``ini`` — Sektion und Schluessel. Der staerkere Anker, weil er unabhaengig
  vom aktuellen Wert trifft: egal was das Spiel hineingeschrieben hat, der
  Schluessel wird gefunden und gesetzt.
* ``text`` — ein exakter Textausschnitt, der genau einmal vorkommt, und der
  Text, der stattdessen dastehen soll. Funktioniert in **jedem** Textformat,
  weil er nichts ueber die Struktur annimmt.

Der Weg stattdessen: Der gewuenschte Wert wird hier gespeichert und vor jedem
Start in die Datei geschrieben (``games/base.py``). Das Verfahren ist dasselbe
wie bei ``guardian_overrides_json`` — eine Handvoll Skalare, die **nach** der
Blueprint-Ableitung darueberliegen. Die Blueprints selbst deklarieren dafuer
nichts: der Wunsch haengt am Server, nicht an der Vorlage.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from services.ai_action_errors import AiActionValidationError
from services.ai_ini_edit import (
    MAX_SCHLUESSEL_CHARS,
    MAX_SEKTION_CHARS,
    MAX_WERT_CHARS,
    ini_setzen,
)
from services.ai_redaction import redact_sensitive_text

#: Obergrenze je Server. Wie bei den Guardian-Stellschrauben eine Zahl und kein
#: Gefuehl: der Speicher wird bei **jedem** Start durchlaufen, also darf er
#: nicht unbemerkt wachsen.
MAX_WUENSCHE = 64

MAX_PFAD_CHARS = 256

#: Laenge eines Textankers. Grosszuegig genug fuer ein umschliessendes
#: XML-Element, klein genug, dass der Speicher nicht zur zweiten Kopie der
#: Datei wird.
MAX_ANKER_CHARS = 2000

ART_INI = "ini"
ART_TEXT = "text"


@dataclass(frozen=True)
class Wunsch:
    """Ein Wert, der nach jedem Start dastehen soll.

    Ein Diskriminator statt zweier Klassen, weil genau diese Form schon durch
    das Panel reist: ``runtime.configPatches`` eines Blueprints hat dieselbe
    Gestalt (``type``, ``section``, ``key``, ``regex``, ``value``). Ein Wunsch
    unterscheidet sich davon nur in der Reichweite — ein Server statt aller
    Server eines Spiels.
    """

    datei: str
    art: str = ART_INI
    sektion: str = ""
    schluessel: str = ""
    wert: str = ""
    finde: str = ""
    setze: str = ""

    def kennung(self) -> tuple[str, ...]:
        """Was denselben Wunsch ausmacht.

        Bei INI schreibweisenunabhaengig, weil ASA seine Sektionen klein
        schreibt und die Dokumentation gross. Ohne das entstuenden zwei
        Wuensche fuer denselben Wert, und beim Start gewaenne der zufaellig
        letzte.

        Bei Text ist der Anker die Kennung — derselbe Ausschnitt kann nur
        einmal ein Ziel haben.
        """
        if self.art == ART_TEXT:
            return (self.datei.casefold(), ART_TEXT, self.finde)
        return (
            self.datei.casefold(),
            ART_INI,
            self.sektion.casefold(),
            self.schluessel.casefold(),
        )


def _pruefe_pfad(datei: str) -> str:
    """Dieselbe Grenze wie ``server_file_access_service.safe_path``.

    Hier ohne Dateisystemzugriff, weil der Wunsch gespeichert wird, bevor
    feststeht, auf welchem Host er einmal angewandt wird. Die Anwendung selbst
    laeuft spaeter erneut durch die echte Pfadpruefung — das hier ist die
    fruehe Absage, damit ein unmoeglicher Wunsch gar nicht erst in der
    Datenbank landet.

    **Keine Endungspruefung.** Welches Format eine Datei hat, entscheidet
    nicht, ob ihr Inhalt dauerhaft sein darf — das entscheidet der Anker.
    """
    if not isinstance(datei, str) or not datei.strip():
        raise AiActionValidationError("Der Dateipfad fehlt")
    datei = datei.strip()
    if len(datei) > MAX_PFAD_CHARS:
        raise AiActionValidationError("Der Dateipfad ist zu lang")
    if datei.startswith(("/", "\\")) or ".." in Path(datei).parts:
        raise AiActionValidationError("Ungueltiger relativer Dateipfad")
    if "\x00" in datei:
        raise AiActionValidationError("Ungueltiger relativer Dateipfad")
    return datei


def _pruefe_geheimnis(probe: str) -> None:
    """Dieselbe Grenze wie bei ``apply_edits`` und ``ini_setzen``.

    Ohne sie waere der Wunschspeicher der Umweg um eine bestehende Invariante:
    ein Passwort, das ``propose_config_patch`` abweist, laege hier dauerhaft in
    der Datenbank und wuerde bei jedem Start neu geschrieben.
    """
    if redact_sensitive_text(probe) != probe:
        raise AiActionValidationError(
            "Der Eintrag enthaelt moegliche Zugangsdaten und wird abgewiesen. "
            "Passwortfelder traegt der Benutzer selbst im Dateimanager ein."
        )


def _pruefe_ini(sektion: str, schluessel: str, wert: str) -> tuple[str, str, str]:
    for name, roh, grenze, verboten in (
        ("Die Sektion", sektion, MAX_SEKTION_CHARS, "[]\r\n"),
        ("Der Schluessel", schluessel, MAX_SCHLUESSEL_CHARS, "=[]\r\n"),
    ):
        if not isinstance(roh, str) or not roh.strip():
            raise AiActionValidationError(f"{name} fehlt")
        if len(roh) > grenze:
            raise AiActionValidationError(f"{name} ist zu lang")
        if any(z in roh for z in verboten):
            raise AiActionValidationError(f"{name} enthaelt ein unzulaessiges Zeichen")
    if not isinstance(wert, str):
        raise AiActionValidationError("Der Wert fehlt")
    if len(wert) > MAX_WERT_CHARS:
        raise AiActionValidationError("Der Wert ist zu lang")
    if any(z in wert for z in "\r\n"):
        raise AiActionValidationError("Der Wert enthaelt einen Zeilenumbruch")
    _pruefe_geheimnis(f"{schluessel}={wert}")
    return sektion.strip(), schluessel.strip(), wert


def _pruefe_text(finde: str, setze: str) -> tuple[str, str]:
    """Anker und Ziel eines Textwunsches.

    Der Anker darf leer werden wollen (``setze=""`` loescht die Stelle), der
    Anker selbst nie: ein leerer Suchtext kommt unendlich oft vor.
    """
    if not isinstance(finde, str) or not finde:
        raise AiActionValidationError("Der Suchtext fehlt")
    if not isinstance(setze, str):
        raise AiActionValidationError("Der Ersatztext fehlt")
    for name, roh in (("Der Suchtext", finde), ("Der Ersatztext", setze)):
        if len(roh) > MAX_ANKER_CHARS:
            raise AiActionValidationError(f"{name} ist zu lang (max. {MAX_ANKER_CHARS})")
        if "\x00" in roh:
            raise AiActionValidationError(f"{name} enthaelt ein unzulaessiges Zeichen")
    _pruefe_geheimnis(finde)
    _pruefe_geheimnis(setze)
    return finde, setze


def lese(roh: str | None) -> list[Wunsch]:
    """Liest den Speicher. Unlesbares gilt als leer.

    Bewusst ohne Ausnahme: diese Funktion laeuft im Startpfad, und ein defekter
    Zusatzwunsch ist kein Grund, dem Benutzer den Server zu verweigern.
    """
    if not roh:
        return []
    try:
        daten = json.loads(roh)
    except (ValueError, TypeError):
        return []
    if not isinstance(daten, list):
        return []

    ergebnis: list[Wunsch] = []
    for eintrag in daten[:MAX_WUENSCHE]:
        if not isinstance(eintrag, dict):
            continue
        datei = eintrag.get("datei")
        if not isinstance(datei, str) or not datei:
            continue
        # Ohne `art` ist es ein Eintrag aus der ersten Fassung: die kannte nur
        # INI. Kein Migrationsschritt noetig, kein Datenverlust.
        art = eintrag.get("art", ART_INI)
        if art == ART_TEXT:
            finde = eintrag.get("finde")
            setze = eintrag.get("setze")
            if not isinstance(finde, str) or not finde:
                continue
            if not isinstance(setze, str):
                continue
            ergebnis.append(Wunsch(datei=datei, art=ART_TEXT, finde=finde, setze=setze))
            continue
        sektion = eintrag.get("sektion")
        schluessel = eintrag.get("schluessel")
        wert = eintrag.get("wert")
        if not isinstance(sektion, str) or not sektion:
            continue
        if not isinstance(schluessel, str) or not schluessel:
            continue
        if not isinstance(wert, str):
            continue
        ergebnis.append(Wunsch(
            datei=datei, art=ART_INI, sektion=sektion, schluessel=schluessel, wert=wert
        ))
    return ergebnis


def _schreibe(wuensche: list[Wunsch]) -> str:
    daten: list[dict] = []
    for w in wuensche:
        if w.art == ART_TEXT:
            daten.append({"datei": w.datei, "art": ART_TEXT, "finde": w.finde, "setze": w.setze})
        else:
            daten.append({
                "datei": w.datei, "art": ART_INI,
                "sektion": w.sektion, "schluessel": w.schluessel, "wert": w.wert,
            })
    return json.dumps(daten, ensure_ascii=False)


def _einfuegen(vorhanden: list[Wunsch], neu: Wunsch) -> None:
    """Ersetzt einen gleichen Wunsch, sonst anhaengen — mit Ablaufkette.

    Die Kette ist der Grund, warum das nicht bloss ein `append` ist: aendert
    jemand denselben Wert zweimal (``1.0`` → ``2.0``, spaeter ``2.0`` → ``3.0``),
    haetten beide Textwuensche verschiedene Anker und stuenden nebeneinander.
    Der zweite loest den ersten ab — sein Anker ist genau dessen Ziel.
    """
    if neu.art == ART_TEXT:
        vorhanden[:] = [
            w for w in vorhanden
            if not (w.art == ART_TEXT and w.datei == neu.datei and w.setze == neu.finde)
        ]
    for i, alt in enumerate(vorhanden):
        if alt.kennung() == neu.kennung():
            vorhanden[i] = neu
            return
    if len(vorhanden) >= MAX_WUENSCHE:
        raise AiActionValidationError(
            f"Hoechstens {MAX_WUENSCHE} dauerhafte Werte je Server. "
            "Entferne einen bestehenden, bevor du einen neuen anlegst."
        )
    vorhanden.append(neu)


def setze(
    roh: str | None,
    *,
    datei: str,
    eintraege: list[tuple[str, str, str]],
) -> str:
    """Legt INI-Wuensche an oder aktualisiert sie (Sektion, Schluessel, Wert)."""
    datei = _pruefe_pfad(datei)
    if not eintraege:
        raise AiActionValidationError("Es fehlt mindestens ein Eintrag")

    vorhanden = lese(roh)
    for sektion, schluessel, wert in eintraege:
        sektion, schluessel, wert = _pruefe_ini(sektion, schluessel, wert)
        _einfuegen(vorhanden, Wunsch(
            datei=datei, art=ART_INI, sektion=sektion, schluessel=schluessel, wert=wert
        ))
    return _schreibe(vorhanden)


def setze_text(
    roh: str | None,
    *,
    datei: str,
    ersetzungen: list[tuple[str, str]],
) -> str:
    """Legt Textwuensche an oder aktualisiert sie (Suchtext, Ersatztext).

    Der Weg fuer jedes Format, das keine INI ist — XML, JSON, YAML, Properties,
    Lua, was auch immer. Der Anker macht keine Annahme ueber die Struktur.
    """
    datei = _pruefe_pfad(datei)
    if not ersetzungen:
        raise AiActionValidationError("Es fehlt mindestens eine Ersetzung")

    vorhanden = lese(roh)
    for finde, setze_text_ in ersetzungen:
        finde, setze_text_ = _pruefe_text(finde, setze_text_)
        _einfuegen(vorhanden, Wunsch(
            datei=datei, art=ART_TEXT, finde=finde, setze=setze_text_
        ))
    return _schreibe(vorhanden)


def entferne(roh: str | None, *, datei: str, sektion: str = "", schluessel: str = "",
             finde: str = "") -> str:
    """Nimmt einen Wunsch heraus. Unbekannt zu sein ist kein Fehler."""
    if finde:
        ziel = Wunsch(datei=datei, art=ART_TEXT, finde=finde).kennung()
    else:
        ziel = Wunsch(
            datei=datei, art=ART_INI, sektion=sektion, schluessel=schluessel
        ).kennung()
    return _schreibe([w for w in lese(roh) if w.kennung() != ziel])


def nach_datei(wuensche: list[Wunsch]) -> dict[str, list[Wunsch]]:
    """Gruppiert fuer den Durchsetzer: eine Datei einmal lesen und einmal schreiben."""
    gruppen: dict[str, list[Wunsch]] = {}
    for w in wuensche:
        gruppen.setdefault(w.datei, []).append(w)
    return gruppen


def als_patches(wuensche: list[Wunsch]) -> list[dict[str, str | None]]:
    """Die Wuensche im Format der Blueprint-Config-Patches.

    Auf einer entfernten Node liegen die Dateien nicht dort, wo das Panel
    laeuft. ``wuensche_durchsetzen`` schreibt lokal und waere dort ein
    **stiller** Fehlschlag — kein Fehler, keine Datei, und der Benutzer merkt
    erst Wochen spaeter, dass sein Wert nie ankam.

    Statt dafuer einen zweiten Uebertragungsweg zu bauen, reisen die Wuensche
    im vorhandenen ``prepare_runtime``-Kanal, den der Agent seit jeher
    anwendet: INI-Wuensche als ``ini``-Patch, Textwuensche als ``regex``-Patch.

    **Der Regex kommt nie vom Modell.** ``re.escape`` macht aus dem exakten
    Suchtext ein literales Muster — damit kann keine Zeichenklasse, kein
    Quantor und damit auch kein ReDoS entstehen. Im Ersatztext werden
    Rueckwaertsverweise entschaerft (``\\1``, ``\\g<name>``), indem jeder
    Backslash verdoppelt wird; sonst koennte ein Wert wie ``C:\\1`` unterwegs
    zu etwas anderem werden.

    Bekannter Unterschied zum lokalen Weg: ``re.sub`` im Agenten ersetzt
    **alle** Vorkommen, der lokale Durchsetzer nur ein eindeutiges. Der
    Suchtext war beim Anlegen eindeutig; wird er es spaeter nicht mehr, faellt
    die entfernte Node strenger aus als die lokale.
    """
    patches: list[dict[str, str | None]] = []
    for w in wuensche:
        if w.art == ART_TEXT:
            patches.append({
                "type": "regex",
                "file": w.datei,
                "section": None,
                "key": None,
                "regex": re.escape(w.finde),
                "value": w.setze.replace("\\", "\\\\"),
            })
        else:
            patches.append({
                "type": "ini",
                "file": w.datei,
                "section": w.sektion,
                "key": w.schluessel,
                "regex": None,
                "value": w.wert,
            })
    return patches


def _oeffne_sicher(basis: Path, relativ: str) -> Path:
    """Loest den Zielpfad auf und weist alles ausserhalb der Basis ab.

    Der Durchsetzer laeuft im Startpfad des Panels, also mit dessen Rechten —
    und die Dateien im Serververzeichnis gehoeren einem fremden Benutzer, der
    sie zwischen zwei Starts veraendern kann. Ein Symlink dort waere sonst ein
    Schreibrecht auf jede Datei, die das Panel anfassen darf.

    ``resolve()`` folgt Symlinks auf dem ganzen Weg, nicht nur am letzten
    Glied; deshalb faengt der anschliessende ``relative_to`` auch ein
    verlinktes Verzeichnis mitten im Pfad.
    """
    if relativ.startswith(("/", "\\")) or ".." in Path(relativ).parts:
        raise ValueError("Ungueltiger relativer Dateipfad")
    basis_echt = basis.resolve(strict=False)
    ziel = (basis_echt / relativ).resolve(strict=False)
    ziel.relative_to(basis_echt)
    return ziel


def _text_anwenden(inhalt: str, wunsch: Wunsch) -> tuple[str, str | None]:
    """Setzt einen Textwunsch durch. Gibt Inhalt und ggf. eine Meldung zurueck.

    Die Reihenfolge der Pruefungen ist die Zusage:

    1. Steht das Ziel schon da, ist nichts zu tun — das ist die Definition von
       "erfuellt" und macht den Durchsetzer idempotent.
    2. Sonst muss der Anker **genau einmal** vorkommen. Mehrfach heisst
       abgewiesen, nicht "die erste Stelle": bei ``value="1"`` in einer
       XML-Konfiguration traefe das Raten neunundneunzig unbeteiligte Stellen.
    3. Gar nicht vorhanden heisst: das Spiel hat die Stelle anders
       umgeschrieben, als wir sie kannten. Dann wird gemeldet statt geraten.
    """
    if wunsch.setze and wunsch.setze in inhalt:
        return inhalt, None
    treffer = inhalt.count(wunsch.finde)
    if treffer == 1:
        return inhalt.replace(wunsch.finde, wunsch.setze, 1), None
    if treffer == 0:
        return inhalt, (
            f"Dauerhafter Wert in {wunsch.datei} nicht gesetzt: die bekannte "
            "Stelle steht so nicht mehr in der Datei"
        )
    return inhalt, (
        f"Dauerhafter Wert in {wunsch.datei} nicht gesetzt: die Stelle kommt "
        f"{treffer}-mal vor und ist nicht eindeutig"
    )


def wuensche_durchsetzen(server) -> None:
    """Schreibt die gewuenschten Werte in die Dateien. Laeuft vor jedem Start.

    Das ist die Stelle, an der aus einer Absicht eine Eigenschaft wird: ob das
    Spiel seine Konfiguration zurueckschreibt, muss danach niemand mehr wissen.

    **Wirft nicht.** Diese Funktion laeuft im Startpfad; eine Ausnahme hier
    wuerde dem Benutzer den Server verweigern, weil ein Zusatzwunsch nicht
    passt. Fehlgeschlagene Wuensche werden uebersprungen, die uebrigen
    geschrieben. Sichtbar wird das im Konsolenlog des Servers, nicht als
    Startfehler.
    """
    roh = getattr(server, "config_wishes_json", None)
    if not roh:
        return
    # Auf einer entfernten Node liegen die Dateien nicht hier. Dort reisen die
    # Wuensche als Patches im `prepare_runtime`-Kanal (siehe `als_patches`);
    # lokal zu schreiben wuerde nur ein leeres Verzeichnis auf dem Panel-Host
    # anlegen und den echten Fehlschlag verdecken.
    node = getattr(server, "node", None)
    if node is not None and not getattr(node, "is_local", False):
        return
    alle = lese(roh)
    if not alle:
        return

    basis = Path(server.install_dir)
    for relativ, gruppe in nach_datei(alle).items():
        try:
            ziel = _oeffne_sicher(basis, relativ)
        except (ValueError, OSError):
            _melde(server, f"Dauerhafter Wert uebersprungen: {relativ} liegt ausserhalb des Servers")
            continue
        try:
            if ziel.exists():
                # `newline=""` ist tragend, nicht kosmetisch: ohne das uebersetzt
                # Python CRLF beim **Lesen** still nach LF, und der Durchsetzer
                # wuerde die Datei bei jedem Start von Windows- auf
                # Unix-Zeilenenden umschreiben. Manche Spiele lesen ab der
                # ersten abweichenden Zeile nichts mehr.
                # (`Path.read_text` kennt `newline` erst ab Python 3.13.)
                with open(ziel, "r", encoding="utf-8", errors="replace", newline="") as datei:
                    inhalt = datei.read()
            else:
                inhalt = ""
        except OSError as fehler:
            _melde(server, f"Dauerhafter Wert uebersprungen: {relativ} nicht lesbar ({fehler.strerror})")
            continue

        neu = inhalt
        for wunsch in gruppe:
            if wunsch.art == ART_TEXT:
                neu, meldung = _text_anwenden(neu, wunsch)
                if meldung:
                    _melde(server, meldung)
                continue
            try:
                neu = ini_setzen(neu, wunsch.sektion, wunsch.schluessel, wunsch.wert)
            except AiActionValidationError as fehler:
                _melde(server, f"Dauerhafter Wert uebersprungen: {fehler}")

        if neu == inhalt:
            # Nichts zu tun. Bewusst kein Schreibvorgang: sonst bewegt jeder
            # Start die mtime der Datei, und genau daran haengt die Diagnose,
            # mit der dieser ganze Fehler gefunden wurde.
            continue

        try:
            ziel.parent.mkdir(parents=True, exist_ok=True)
            ziel.write_text(neu, encoding="utf-8", newline="")
        except OSError as fehler:
            _melde(server, f"Dauerhafter Wert nicht geschrieben: {relativ} ({fehler.strerror})")


def _melde(server, text: str) -> None:
    """Schreibt in das Konsolenlog des Servers, ohne den Start zu gefaehrden.

    Der Import liegt in der Funktion, weil ``games.base`` diesen Modul
    importiert — andersherum entstuende ein Zyklus.
    """
    try:
        from games.base import _append_console_log

        _append_console_log(server.id, f"[MSM] {text}\n")
    except Exception:
        pass
