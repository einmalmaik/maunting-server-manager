"""Sektionsbewusstes Setzen von INI-Werten als reine Textfunktion.

**Warum es diese Funktion gibt.** Bis hierher hat die KI Konfigurationswerte
ueber Textersetzung geaendert: Suchtext finden, Ersatztext einsetzen. Fuer eine
Formatdatei ist das das falsche Werkzeug, und zwar messbar. Am 18.08.2026 hat
ein ausgefuehrter Patch auf einem ASA-Server einen **zweiten**
``[ServerSettings]``-Block ans Dateiende gehaengt; ARK liest nur den ersten. Die
Werte waren richtig, die Wirkung war null, und der Diff sah korrekt aus. Am
selben Server scheitern mehrzeilige Suchtexte ausserdem deterministisch an den
CRLF-Zeilenenden der echten Datei, weil ein Modell in ``\\n`` denkt.

Beide Fehler sind mit Sektion und Schluessel als Argument nicht mehr moeglich.

**Warum nicht ``games/ini_utils.set_ini_value``.** Das dortige Verfahren ist
dasselbe, arbeitet aber auf Pfaden (``open``/``write``) und umgeht damit
``safe_path``, die Rechtepruefung und den Versionsspeicher. Die KI muss durch
``write_server_text``. Also dieselbe Zeilenlogik, aber ``str -> str``.

**Warum kein ``configparser``.** Steht schon in ``ini_utils.py`` begruendet:
UE-INIs erlauben Duplikat-Keys und tragen Kommentare, die ``configparser``
zerstoert. Keine neue Dependency.
"""
from __future__ import annotations

from services.ai_action_errors import AiActionValidationError
from services.ai_redaction import redact_sensitive_text

#: Laengengrenzen. Sie halten einen einzelnen Eintrag klein genug, dass er eine
#: Zeile bleibt — die Struktur der Datei haengt daran.
MAX_SEKTION_CHARS = 128
MAX_SCHLUESSEL_CHARS = 128
MAX_WERT_CHARS = 512


def _pruefe_baustein(name: str, wert: str, grenze: int, verbotene: str) -> None:
    """Weist alles ab, was mehr als einen Zeileninhalt erzeugen koennte.

    Die Grenze ist keine Formalie. Ein Wert, der ein Zeilenende enthaelt,
    schreibt eine zweite Zeile — und die kann eine Sektionsueberschrift sein.
    Damit waere das Werkzeug eine Struktur-Injektion in die Datei, die es
    eigentlich sauber halten soll.
    """
    if not isinstance(wert, str) or not wert.strip():
        raise AiActionValidationError(f"{name} fehlt")
    if len(wert) > grenze:
        raise AiActionValidationError(f"{name} ist zu lang (max. {grenze} Zeichen)")
    for zeichen in verbotene:
        if zeichen in wert:
            raise AiActionValidationError(
                f"{name} enthaelt ein unzulaessiges Zeichen und wird abgewiesen"
            )


def _zeilenende(inhalt: str) -> str:
    """Das vorherrschende Zeilenende der Datei, nicht das des Modells.

    Gemischte Zeilenenden sind schlimmer als die falschen: manche Spiele lesen
    ab der ersten abweichenden Zeile nichts mehr. Deshalb richtet sich die
    neue Zeile immer nach dem, was schon dasteht.
    """
    return "\r\n" if "\r\n" in inhalt else "\n"


def _ist_sektionszeile(zeile: str) -> bool:
    nackt = zeile.strip()
    return nackt.startswith("[") and nackt.endswith("]")


def _gleiche_sektion(zeile: str, sektion: str) -> bool:
    """Sektionsvergleich ohne Ruecksicht auf Gross-/Kleinschreibung.

    ASA schreibt ``[/script/shootergame.shootergamemode]`` klein, die Wikis und
    damit das Modellwissen schreiben es gross. Ohne diese Toleranz legt die KI
    eine zweite Sektion an, die sich von der ersten nur in der Schreibweise
    unterscheidet — derselbe wirkungslose Doppelblock wie zuvor, nur schwerer
    zu erkennen.
    """
    return zeile.strip().casefold() == f"[{sektion}]".casefold()


def _ist_schluesselzeile(zeile: str, schluessel: str) -> bool:
    nackt = zeile.strip()
    if "=" not in nackt:
        return False
    return nackt.split("=", 1)[0].strip().casefold() == schluessel.casefold()


def ini_setzen(inhalt: str, sektion: str, schluessel: str, wert: str) -> str:
    """Setzt ``schluessel=wert`` in ``[sektion]`` und gibt den neuen Text zurueck.

    - Vorhandener Schluessel in der Sektion wird ueberschrieben (**erstes**
      Vorkommen; spaetere Duplikate bleiben stehen, weil UE-INIs sie
      absichtlich nutzen — dieselbe Regel wie in ``games/ini_utils.py``).
    - Fehlt der Schluessel, wird er am **Ende der Sektion** eingefuegt, nicht am
      Dateiende: sonst landet er in der naechsten Sektion und wirkt nicht.
    - Fehlt die Sektion, wird sie am Dateiende angelegt.
    - Das Zeilenende der Datei bleibt, wie es ist.

    Ist der Wert bereits gesetzt, kommt der Text unveraendert zurueck. Das ist
    kein Detail: die Durchsetzung beim Serverstart ruft diese Funktion bei jedem
    Start auf und darf die Datei nicht jedes Mal anfassen.
    """
    # Struktur-Injektion: Klammern in der Sektion, Trenner im Schluessel,
    # Zeilenenden ueberall.
    _pruefe_baustein("Die Sektion", sektion, MAX_SEKTION_CHARS, "[]\r\n")
    _pruefe_baustein("Der Schluessel", schluessel, MAX_SCHLUESSEL_CHARS, "=[]\r\n")
    if not isinstance(wert, str):
        raise AiActionValidationError("Der Wert fehlt")
    if len(wert) > MAX_WERT_CHARS:
        raise AiActionValidationError(f"Der Wert ist zu lang (max. {MAX_WERT_CHARS} Zeichen)")
    for zeichen in "\r\n":
        if zeichen in wert:
            raise AiActionValidationError(
                "Der Wert enthaelt einen Zeilenumbruch und wird abgewiesen"
            )

    # Dieselbe Geheimnisgrenze wie bei `apply_edits`. Ohne sie waere dieses
    # Werkzeug schlicht der Umweg um eine bestehende Invariante.
    zeile_neu = f"{schluessel}={wert}"
    if redact_sensitive_text(zeile_neu) != zeile_neu:
        raise AiActionValidationError(
            "Der Eintrag enthaelt moegliche Zugangsdaten und wird abgewiesen. "
            "Passwortfelder traegt der Benutzer selbst im Dateimanager ein."
        )

    ende = _zeilenende(inhalt)
    # `keepends=False` und spaeteres Zusammenfuegen wuerde ein fehlendes
    # Zeilenende am Dateiende stillschweigend ergaenzen. Das ist hier gewollt:
    # eine Datei ohne Schlusszeilenende bekommt eines, sobald wir sie anfassen.
    zeilen = inhalt.splitlines()

    ausgabe: list[str] = []
    in_sektion = False
    sektion_gefunden = False
    gesetzt = False
    einfuegemarke: int | None = None

    for zeile in zeilen:
        if _ist_sektionszeile(zeile):
            # Wir verlassen die Zielsektion, ohne den Schluessel gesehen zu
            # haben: hier gehoert er hin, nicht ans Dateiende.
            if in_sektion and not gesetzt and einfuegemarke is None:
                einfuegemarke = len(ausgabe)
            in_sektion = _gleiche_sektion(zeile, sektion)
            if in_sektion:
                sektion_gefunden = True
            ausgabe.append(zeile)
            continue

        if in_sektion and not gesetzt and _ist_schluesselzeile(zeile, schluessel):
            if zeile.strip() == zeile_neu:
                return inhalt  # schon so — die Datei bleibt unberuehrt
            ausgabe.append(zeile_neu)
            gesetzt = True
            continue

        ausgabe.append(zeile)

    if not gesetzt:
        if sektion_gefunden:
            if einfuegemarke is None:
                # Die Sektion lief bis zum Dateiende.
                einfuegemarke = len(ausgabe)
            # Leerzeilen am Sektionsende gehoeren hinter den neuen Eintrag,
            # sonst waechst bei jedem Setzen eine Luecke in die Datei.
            while einfuegemarke > 0 and not ausgabe[einfuegemarke - 1].strip():
                einfuegemarke -= 1
            ausgabe.insert(einfuegemarke, zeile_neu)
        else:
            if ausgabe and ausgabe[-1].strip():
                ausgabe.append("")
            ausgabe.append(f"[{sektion}]")
            ausgabe.append(zeile_neu)

    return ende.join(ausgabe) + ende
