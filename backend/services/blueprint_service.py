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

from blueprints.registry import (
    BlueprintSourceOrigin,
    community_blueprint_path,
    ensure_community_dir,
    get_registry,
    reload_registry,
)
from blueprints.schema import BlueprintValidationError, load_blueprint_dict


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
AENDERBARE_PFADE = (
    "meta.name",
    "meta.description",
    "runtime.image",
    "runtime.env",
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


def delete_community_blueprint(blueprint_id: str) -> None:
    """Loescht einen Community-Blueprint. Native IDs sind hart geschuetzt."""
    eintrag = get_registry().get(blueprint_id)
    if eintrag is None:
        raise HTTPException(status_code=404, detail="Blueprint nicht gefunden")
    if eintrag.origin == BlueprintSourceOrigin.NATIVE:
        raise HTTPException(
            status_code=400,
            detail="Native Blueprints sind read-only und koennen nicht geloescht werden.",
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
    source_id: str, *, new_id: str, changes: dict[str, Any]
) -> dict[str, Any]:
    """Baut die Nutzlast eines abgeleiteten Blueprints — ohne zu speichern.

    Getrennt vom Speichern, weil ein KI-Vorschlag beides zu verschiedenen
    Zeitpunkten braucht: beim Vorschlagen, um dem Menschen zu zeigen, was
    herauskommt, und nach der Bestaetigung, um es zu schreiben. Zweimal bauen
    waere zweimal die Chance auf ein anderes Ergebnis.

    ``changes`` sind Punktpfade auf ``AENDERBARE_PFADE``. ``runtime.env`` wird
    **gemischt**, nicht ersetzt: wer die Version aendert, will nicht alle
    anderen Umgebungsvariablen verlieren.
    """
    if not ID_MUSTER.match(new_id or ""):
        raise HTTPException(
            status_code=400,
            detail="Blueprint-ID darf nur a-z, 0-9 und _ enthalten (max. 64 Zeichen).",
        )
    quelle = get_registry().get(source_id)
    if quelle is None:
        raise HTTPException(status_code=404, detail="Vorlage nicht gefunden")
    if get_registry().get(new_id) is not None and new_id != source_id:
        vorhanden = get_registry().get(new_id)
        if vorhanden.origin == BlueprintSourceOrigin.NATIVE:
            raise HTTPException(
                status_code=409,
                detail=f"Die ID '{new_id}' gehoert einem nativen Blueprint.",
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
