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

**Der Weg stattdessen:** Der gewuenschte Wert wird hier gespeichert und vor
jedem Start in die Datei geschrieben (``games/base.py``). Ob das Spiel
zurueckschreibt, muss dann niemand wissen: beim naechsten Start steht der Wert
wieder da. Das Verfahren ist dasselbe wie bei ``guardian_overrides_json`` —
eine Handvoll Skalare, die **nach** der Blueprint-Ableitung darueberliegen.
"""
from __future__ import annotations

import json
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

#: Der Durchsetzer beim Start kann heute INI. Ein Wunsch fuer eine andere
#: Formatfamilie waere ein Versprechen, das beim Start still gebrochen wird —
#: deshalb wird er hier abgewiesen statt spaeter ignoriert.
INI_ENDUNGEN = (".ini", ".cfg", ".conf")


@dataclass(frozen=True)
class Wunsch:
    """Ein Wert, der nach jedem Start dastehen soll."""

    datei: str
    sektion: str
    schluessel: str
    wert: str

    def kennung(self) -> tuple[str, str, str]:
        """Was denselben Wunsch ausmacht.

        Schreibweisenunabhaengig, weil ASA seine Sektionen klein schreibt und
        die Dokumentation gross. Ohne das entstuenden zwei Wuensche fuer
        denselben Wert, und beim Start gewaenne der zufaellig letzte.
        """
        return (
            self.datei.casefold(),
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
    if not datei.lower().endswith(INI_ENDUNGEN):
        raise AiActionValidationError(
            "Dauerhafte Werte gibt es zurzeit nur fuer INI-artige Dateien "
            f"({', '.join(INI_ENDUNGEN)}). Andere Formate aenderst du direkt."
        )
    return datei


def _pruefe_eintrag(sektion: str, schluessel: str, wert: str) -> tuple[str, str, str]:
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

    # Ohne diese Grenze waere der Wunschspeicher der Umweg um eine bestehende
    # Invariante: ein Passwort, das `propose_config_patch` abweist, laege hier
    # dauerhaft in der Datenbank und wuerde bei jedem Start neu geschrieben.
    probe = f"{schluessel}={wert}"
    if redact_sensitive_text(probe) != probe:
        raise AiActionValidationError(
            "Der Eintrag enthaelt moegliche Zugangsdaten und wird abgewiesen. "
            "Passwortfelder traegt der Benutzer selbst im Dateimanager ein."
        )
    return sektion.strip(), schluessel.strip(), wert


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
        sektion = eintrag.get("sektion")
        schluessel = eintrag.get("schluessel")
        wert = eintrag.get("wert")
        if not isinstance(datei, str) or not datei:
            continue
        if not isinstance(sektion, str) or not sektion:
            continue
        if not isinstance(schluessel, str) or not schluessel:
            continue
        if not isinstance(wert, str):
            continue
        ergebnis.append(Wunsch(datei=datei, sektion=sektion, schluessel=schluessel, wert=wert))
    return ergebnis


def _schreibe(wuensche: list[Wunsch]) -> str:
    return json.dumps(
        [
            {"datei": w.datei, "sektion": w.sektion, "schluessel": w.schluessel, "wert": w.wert}
            for w in wuensche
        ],
        ensure_ascii=False,
    )


def setze(
    roh: str | None,
    *,
    datei: str,
    eintraege: list[tuple[str, str, str]],
) -> str:
    """Legt Wuensche an oder aktualisiert sie und gibt den neuen Speicher zurueck."""
    datei = _pruefe_pfad(datei)
    if not eintraege:
        raise AiActionValidationError("Es fehlt mindestens ein Eintrag")

    vorhanden = lese(roh)
    for sektion, schluessel, wert in eintraege:
        sektion, schluessel, wert = _pruefe_eintrag(sektion, schluessel, wert)
        neu = Wunsch(datei=datei, sektion=sektion, schluessel=schluessel, wert=wert)
        for i, alt in enumerate(vorhanden):
            if alt.kennung() == neu.kennung():
                vorhanden[i] = neu
                break
        else:
            if len(vorhanden) >= MAX_WUENSCHE:
                raise AiActionValidationError(
                    f"Hoechstens {MAX_WUENSCHE} dauerhafte Werte je Server. "
                    "Entferne einen bestehenden, bevor du einen neuen anlegst."
                )
            vorhanden.append(neu)
    return _schreibe(vorhanden)


def entferne(roh: str | None, *, datei: str, sektion: str, schluessel: str) -> str:
    """Nimmt einen Wunsch heraus. Unbekannt zu sein ist kein Fehler."""
    ziel = Wunsch(datei=datei, sektion=sektion, schluessel=schluessel, wert="").kennung()
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
    als das, was sie ohnehin sind: INI-Patches im vorhandenen
    ``prepare_runtime``-Kanal, den der Agent seit jeher anwendet. Ein Wunsch
    unterscheidet sich von einem Blueprint-Patch nur in der Reichweite — ein
    Server statt aller Server eines Spiels.
    """
    return [
        {
            "type": "ini",
            "file": w.datei,
            "section": w.sektion,
            "key": w.schluessel,
            "regex": None,
            "value": w.wert,
        }
        for w in wuensche
    ]


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
    # Wuensche als INI-Patches im `prepare_runtime`-Kanal (siehe `als_patches`);
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
        # Symlinks am Ziel selbst weist `resolve()` oben nicht ab — es folgt
        # ihnen ja gerade. Ein Link, der *innerhalb* der Basis bleibt, ist
        # harmlos; einer nach draussen ist schon abgefangen.
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
