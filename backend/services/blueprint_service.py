"""Blueprints schreiben — die eine Implementierung.

Herausgeloest aus `routers/blueprints.py`, wo das Speichern im Endpunkt stand.
Dasselbe Muster wie bei `server_deletion_service` und
`backup_restore_service`: der Router ist nur noch der HTTP-Rand.

Der Anlass kommt aus dem Betrieb. Ein Benutzer bat die KI, die Minecraft-Version
seines Servers zu aendern. Sie konnte die Version nicht einmal *lesen* — und
haette sie auch nicht aendern koennen, denn:

- Die Spielversion ist kein eigenes Feld. Sie steckt bei Minecraft in
  ``runtime.env["VERSION"]`` des Blueprints, bei Steam-Titeln in
  ``source.steam.branch``, sonst im Image-Tag.
- Blueprints gelten fuer **alle** Server desselben ``game_type``.
- Native Blueprints sind schreibgeschuetzt, und ein Community-Blueprint mit
  nativer ID wird abgewiesen.

Die Spielversion eines bestehenden Servers war damit ueberhaupt nicht aenderbar
— auch nicht von Hand. `minecraft_vanilla.blueprint.json` beschreibt den
Handgriff in Zeile 8 als Nutzeranleitung, die so nicht durchfuehrbar war.

`derive_from` schliesst die Luecke: aus einem Blueprint (nativ oder community)
entsteht ein neuer mit eigener ID und geaenderten Werten. Der Ausgangs-Blueprint
bleibt unangetastet, und andere Server, die ihn nutzen, merken nichts.

Blueprints sind JSON-Dateien, keine Datenbanktabelle (`blueprints/__init__.py`:
"KISS: keine DB-Tabelle, kein Pickle, kein YAML"). Diese Schicht aendert daran
nichts — sie buendelt nur, was vorher im Router lag.
"""

from __future__ import annotations

import copy
import json
import logging
import re
from typing import Any

from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from blueprints.registry import (
    BlueprintSourceOrigin,
    community_blueprint_path,
    ensure_community_dir,
    get_registry,
    reload_registry,
)
from blueprints.schema import BlueprintValidationError, load_blueprint_dict
from models import Server


logger = logging.getLogger(__name__)

# Derselbe Zwang wie beim Dateinamen in der Registry. Er steht hier noch einmal,
# weil ein abgeleiteter Blueprint seine ID nicht aus einer Datei bekommt,
# sondern von einem Menschen oder einem Modell — und dann erst zu einem
# Dateinamen wird.
ID_MUSTER = re.compile(r"^[a-z0-9_]{1,64}$")

# Welche Felder `derive_from` aendern darf. Bewusst eng: eine Ableitung soll die
# Version, das Image oder eine Umgebungsvariable anpassen, nicht die Portrollen
# oder die Installationsquelle umbauen. Wer das braucht, laedt einen ganzen
# Blueprint hoch — dann sieht ein Mensch das vollstaendige Ergebnis, statt einer
# Liste von Einzelaenderungen zustimmen zu muessen, deren Zusammenwirken er
# nicht ueberblickt.
#
# `runtime.startup` steht hier, seit die KI auch eine falsche Startzeile
# korrigieren koennen soll — sie ist bei GitHub-Quellen der haeufigste Grund,
# warum ein Server ueberhaupt nicht hochkommt. `runtime.startupProfiles` steht
# bewusst **nicht** hier: das ist eine Liste mit Bedingungen, und eine
# Punktpfad-Aenderung an einer Liste ist keine, die ein Mensch auf einer
# Bestaetigungskarte ueberblickt. Wo ein Blueprint Profile fuehrt, weist
# `derived_payload` eine Aenderung an `runtime.startup` deshalb ab, statt sie
# wirkungslos durchzureichen — siehe dort.
AENDERBARE_PFADE = (
    "meta.name",
    "meta.description",
    "runtime.image",
    "runtime.env",
    "runtime.startup",
)


def blueprint_view(blueprint_id: str) -> dict[str, Any]:
    """Ein Blueprint als JSON, mitsamt seiner Herkunft.

    ``origin`` ist mehr als eine Angabe zur Vollstaendigkeit: an ihr haengt, ob
    die KI zu dieser Software im Netz nachschlagen darf. Nativ ausgelieferte
    Blueprints beschreiben oeffentlich dokumentierte Spiele; was ein Benutzer
    selbst importiert hat, kann sein eigener Discord-Bot sein.
    """
    eintrag = get_registry().get(blueprint_id)
    if eintrag is None:
        raise HTTPException(status_code=404, detail="Blueprint nicht gefunden")
    return {
        "id": blueprint_id,
        "origin": eintrag.origin.value,
        "editable": eintrag.origin != BlueprintSourceOrigin.NATIVE,
        "blueprint": eintrag.blueprint.model_dump(mode="json", by_alias=True),
    }


def save_community_blueprint(raw: dict[str, Any]) -> str:
    """Prueft und speichert einen Community-Blueprint. Gibt die ID zurueck.

    Ueberschreiben eines vorhandenen Community-Blueprints ist erlaubt — das ist
    der einzige Weg, einen zu bearbeiten. Native IDs sind hart geschuetzt: die
    Registry laedt native zuerst und verwirft eine Kollision ohnehin, aber ein
    stillschweigend wirkungsloser Import waere schlimmer als eine Absage.
    """
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="Body muss ein JSON-Objekt sein.")
    try:
        blueprint = load_blueprint_dict(raw)
    except BlueprintValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Blueprint-Validierung fehlgeschlagen",
                "errors": exc.errors,
            },
        ) from exc

    vorhanden = get_registry().get(blueprint.meta.id)
    if vorhanden is not None and vorhanden.origin == BlueprintSourceOrigin.NATIVE:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Blueprint-ID '{blueprint.meta.id}' kollidiert mit einer "
                "nativen Blueprint und darf nicht ueberschrieben werden."
            ),
        )

    ensure_community_dir()
    try:
        ziel = community_blueprint_path(blueprint.meta.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    nutzlast = blueprint.model_dump(mode="json", by_alias=True)
    try:
        ziel.write_text(
            json.dumps(nutzlast, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        logger.error("Konnte Blueprint %s nicht schreiben: %s", ziel.name, exc)
        raise HTTPException(
            status_code=500, detail="Blueprint konnte nicht gespeichert werden."
        ) from exc

    reload_registry()
    return blueprint.meta.id


def _server_anzahl(db: Session, blueprint_id: str) -> int:
    """Wie viele Server auf diesem Blueprint liegen.

    Die eine Zaehlung fuer beide Faelle, in denen ein Blueprint unter einem
    laufenden Server weggezogen wuerde: das Loeschen und das Ueberschreiben
    einer bestehenden Kennung. Zweimal geschrieben waeren es zweimal die Chance,
    dass die eine Stelle spaeter etwas anderes zaehlt als die andere.

    Ein Datenbankfehler wird hier zu einem 503 und **nicht** zu einem stillen
    "vermutlich benutzt es niemand". Wer die Verwendung nicht pruefen kann,
    fasst den Blueprint nicht an; das Gegenteil waere ein Fallback, der eine
    Schutzpruefung ueberspringt, sobald der Pool klemmt.
    """
    try:
        return db.query(Server).filter(Server.game_type == blueprint_id).count()
    except SQLAlchemyError as exc:
        logger.error(
            "Konnte Server-Nutzung fuer Blueprint %s nicht pruefen: %s",
            blueprint_id,
            exc,
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "Die Verwendung dieses Blueprints laesst sich gerade nicht "
                "pruefen (Datenbank nicht erreichbar). Aus Sicherheitsgruenden "
                "wird nichts geaendert — bitte spaeter erneut versuchen."
            ),
        ) from exc


def delete_community_blueprint(blueprint_id: str, db: Session) -> None:
    """Loescht einen Community-Blueprint. Native und benutzte sind geschuetzt.

    ``db`` ist Pflicht und wird vom Aufrufer gestellt — dem Router aus seinem
    Request, dem Vorschlagsdienst aus dem Lauf, der die Bestaetigung ausfuehrt.
    Eine eigene Verbindung daneben aufzumachen waere bequemer und faengt sich
    genau den Fehler ein, den diese Pruefung verhindern soll: sie antwortete auf
    einen anderen Stand als den, auf dem der Aufrufer gerade arbeitet.

    Geloescht wird per ``unlink``, und es gibt keinen Schnappschuss davon. Der
    einzige Weg zurueck ist die Datei, die jemand vorher exportiert hat —
    deshalb steht die Zaehlung **vor** dem Loeschen und nicht als Warnung
    daneben.
    """
    eintrag = get_registry().get(blueprint_id)
    if eintrag is None:
        raise HTTPException(status_code=404, detail="Blueprint nicht gefunden")
    if eintrag.origin == BlueprintSourceOrigin.NATIVE:
        raise HTTPException(
            status_code=400,
            detail="Native Blueprints sind read-only und koennen nicht geloescht werden.",
        )

    anzahl = _server_anzahl(db, blueprint_id)
    if anzahl > 0:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Blueprint wird noch von {anzahl} Server(n) verwendet und kann "
                "nicht geloescht werden."
            ),
        )

    try:
        ziel = community_blueprint_path(blueprint_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        ziel.unlink(missing_ok=True)
    except OSError as exc:
        logger.error("Konnte Blueprint %s nicht loeschen: %s", ziel.name, exc)
        raise HTTPException(
            status_code=500, detail="Blueprint konnte nicht geloescht werden."
        ) from exc
    reload_registry()


def _setze(ziel: dict, pfad: str, wert: Any) -> None:
    teile = pfad.split(".")
    knoten = ziel
    for teil in teile[:-1]:
        if not isinstance(knoten.get(teil), dict):
            knoten[teil] = {}
        knoten = knoten[teil]
    knoten[teile[-1]] = wert


def derived_payload(
    source_id: str, *, new_id: str, changes: dict[str, Any], db: Session
) -> dict[str, Any]:
    """Baut die Nutzlast eines abgeleiteten Blueprints — ohne zu speichern.

    Getrennt vom Speichern, weil ein KI-Vorschlag beides zu verschiedenen
    Zeitpunkten braucht: beim Vorschlagen, um dem Menschen zu zeigen, was
    herauskommt, und nach der Bestaetigung, um es zu schreiben. Zweimal bauen
    waere zweimal die Chance auf ein anderes Ergebnis.

    ``changes`` sind Punktpfade auf ``AENDERBARE_PFADE``. ``runtime.env`` wird
    **gemischt**, nicht ersetzt: wer die Version aendert, will nicht alle
    anderen Umgebungsvariablen verlieren.

    ``db`` ist Pflicht, aus demselben Grund wie bei
    `delete_community_blueprint`: eine Ableitung ist nur dann harmlos, wenn sie
    wirklich eine **neue** Datei anlegt. Zeigt ``new_id`` auf einen bestehenden
    Community-Blueprint, ist es keine Ableitung mehr, sondern eine Aenderung an
    allen Servern, die darauf liegen — und die muss dieselbe Zaehlung sehen wie
    das Loeschen.
    """
    if not ID_MUSTER.match(new_id or ""):
        raise HTTPException(
            status_code=400,
            detail="Blueprint-ID darf nur a-z, 0-9 und _ enthalten (max. 64 Zeichen).",
        )
    quelle = get_registry().get(source_id)
    if quelle is None:
        raise HTTPException(status_code=404, detail="Vorlage nicht gefunden")

    # Der Ueberschreibschutz. Zuvor stand hier nur die Native-Abfrage, und die
    # sprang bei ``new_id == source_id`` gar nicht erst an — ausgerechnet der
    # Fall, in dem die Vorlage selbst ueberschrieben wird. Ein bestehender
    # Community-Blueprint mit Servern darauf ist keine Vorlage mehr, sondern
    # eine Betriebsgrundlage: seine `runtime.image`, `runtime.env` und seit
    # Kurzem auch seine Startzeile entscheiden, was diese Server beim naechsten
    # Start ausfuehren. Wer das aendern will, legt eine neue Kennung an.
    vorhanden = get_registry().get(new_id)
    if vorhanden is not None:
        if vorhanden.origin == BlueprintSourceOrigin.NATIVE:
            raise HTTPException(
                status_code=409,
                detail=f"Die ID '{new_id}' gehoert einem nativen Blueprint.",
            )
        anzahl = _server_anzahl(db, new_id)
        if anzahl > 0:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Die ID '{new_id}' gehoert einem Blueprint, auf dem "
                    f"{anzahl} Server liegen. Ihn zu ueberschreiben aenderte "
                    "diese Server mit. Waehle eine neue ID."
                ),
            )

    nutzlast = copy.deepcopy(quelle.blueprint.model_dump(mode="json", by_alias=True))
    _setze(nutzlast, "meta.id", new_id)

    for pfad, wert in (changes or {}).items():
        if pfad not in AENDERBARE_PFADE:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"'{pfad}' ist nicht aenderbar. Erlaubt: "
                    + ", ".join(AENDERBARE_PFADE)
                ),
            )
        if pfad == "runtime.startup" and quelle.blueprint.runtime.startupProfiles:
            # `resolve_startup_template` (blueprints/github_source.py) nimmt das
            # erste Profil, dessen `whenFile` im Installationsverzeichnis
            # existiert, und faellt nur auf `runtime.startup` zurueck, wenn
            # keines passt. Bei so einem Blueprint kann eine Korrektur an
            # `runtime.startup` also folgenlos bleiben — und zwar unbemerkt: der
            # Vorschlag sieht richtig aus, wird bestaetigt, und der Server
            # startet danach mit derselben Zeile wie vorher. Lieber eine Absage,
            # die sagt warum, als eine Zusage, die nicht haelt. Die Profile
            # selbst sind ueber `AENDERBARE_PFADE` nicht erreichbar; wer sie
            # aendern will, laedt den Blueprint als Ganzes hoch.
            raise HTTPException(
                status_code=400,
                detail=(
                    f"'{source_id}' waehlt seine Startzeile ueber "
                    "runtime.startupProfiles (nach vorhandener Datei). Eine "
                    "Aenderung an runtime.startup wirkt dort nur, wenn kein "
                    "Profil zutrifft, und bliebe sonst folgenlos. Die Profile "
                    "lassen sich nur beim Hochladen eines vollstaendigen "
                    "Blueprints aendern."
                ),
            )
        if pfad == "runtime.env":
            if not isinstance(wert, dict):
                raise HTTPException(
                    status_code=400, detail="runtime.env erwartet ein Objekt."
                )
            bestand = dict(nutzlast.get("runtime", {}).get("env") or {})
            # Mischen statt ersetzen: eine Versionsaenderung darf nicht die
            # uebrigen Umgebungsvariablen mitnehmen.
            bestand.update({str(k): str(v) for k, v in wert.items()})
            _setze(nutzlast, "runtime.env", bestand)
        else:
            _setze(nutzlast, pfad, wert)

    # Fruehe Validierung: ein Vorschlag, dessen Ergebnis das Schema verletzt,
    # soll gar nicht erst entstehen — sonst scheitert er erst nach der
    # Bestaetigung, und der Mensch hat einer Aenderung zugestimmt, die es nicht
    # gibt.
    try:
        load_blueprint_dict(nutzlast)
    except BlueprintValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Abgeleiteter Blueprint ist ungueltig",
                "errors": exc.errors,
            },
        ) from exc
    return nutzlast
