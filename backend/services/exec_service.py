"""Exec-Service fuer den Exec-Tab (v1.4.7+).

Kapselt den Aufruf von ``docker_service.exec_in`` mit drei
Sicherheits-Invarianten:

1. **Kein Host-Exec**: Der Container-Name wird ausschliesslich aus
   ``container_name_for(server.id)`` gebildet -- kein User-Input fliesst
   in den Container-Namen.
2. **Kein Shell-Escape**: Das Befehls-Array wird als argv an
   ``container.exec_run`` weitergegeben. Wir bauen NIE einen
   ``["sh", "-c", userstring]`` zusammen.
3. **Audit-Log ohne Output**: Wir loggen server_id, user_id und argv --
   niemals stdout/stderr (kann sensible Daten enthalten).

Output-Truncation: stdout+stderr werden auf ``MAX_OUTPUT_BYTES`` (256 KiB)
gedeckelt. Laengere Outputs werden mit ``\\n...[truncated]`` markiert, so
weiss der User, dass abgeschnitten wurde.

KISS-Prinzipien:
- Kein neuer Manager / keine neue Klasse -- zwei Hilfsfunktionen +
  eine Wrapper-Funktion.
- Keine Caches, kein State. Jeder Aufruf geht direkt durch.
- Kein neues Logging-Framework -- der existierende ``logging``-Modul-
  Baum (``msm.audit.exec``) reicht; Konsumenten koennen den via
  Filter/Pipe einsammeln.
"""
from __future__ import annotations

import logging
from typing import Any

from games.base import container_name_for
from games import get_plugin
from services import docker_service

logger = logging.getLogger("msm.audit.exec")

# Hard-Cap fuer stdout+stderr pro Exec-Call. Grosszuegig gewaehlt (256 KiB)
# -- reicht fuer ``ls -laR``, ``ps aux``, ``docker compose ps`` etc. Bei
# wirklich grossen Outputs (z. B. ``find /``) muss der User den Log-Tab
# oder den normalen Konsolen-Stream benutzen.
MAX_OUTPUT_BYTES = 256 * 1024

# UTF-8-sicherer Truncation-Marker. Wird an den abgeschnittenen Output
# angehaengt, damit der User weiss, dass abgeschnitten wurde (im
# Gegensatz zu "der Befehl hat einfach nichts mehr ausgegeben").
_TRUNCATION_MARKER = "\n...[truncated]"


def load_blueprint_for_server(server) -> Any | None:
    """Laedt den Blueprint, mit dem ``server`` installiert wurde.

    Delegiert an ``get_plugin(server.game_type).get_blueprint()`` -- das ist
    der einzige existierende Pfad in MSM, einen Blueprint zu einem Server zu
    finden (Blueprints werden zur Laufzeit aus ``backend/blueprints/native``
    und ``blueprints/community/*.blueprint.json`` geladen und in der Registry
    indiziert).

    Returns ``None``, wenn kein Plugin gefunden wird (z. B. weil der Server
    zu einer ``game_type`` gehoert, die nicht mehr in der Registry ist --
    kaputter/alten Stand). Der Endpoint behandelt ``None`` als "Exec
    deaktiviert", so dass ein kaputter Server nie versehentlich Exec-Zugriff
    erlaubt.

    Hintergrund: Wir hatten keine Blueprint-Snapshot-Spalte im ``Server``-
    Model. Ein expliziter Snapshot waere die saubere Loesung (dann wuerde
    Exec auch funktionieren, wenn ein Community-Blueprint aus der Registry
    entfernt wurde), aber das waere ein groesserer Schema-Bruch -- fuer
    v1.4.7 reicht der Registry-Lookup. TODO v1.5: Server.blueprint_snapshot.
    """
    plugin = get_plugin(server.game_type)
    if plugin is None:
        return None
    return plugin.get_blueprint()


def _truncate_output(text: str, max_bytes: int = MAX_OUTPUT_BYTES) -> str:
    """Decktelt ``text`` auf ``max_bytes`` UTF-8-Bytes.

    Wichtiger Punkt: Wir schneiden NICHT mitten in einem Multibyte-UTF-8-
    Zeichen ab. Wenn der ``max_bytes``-Offset in der Mitte eines Mehrbyte-
    Zeichens landet, gehen wir rueckwaerts bis zur letzten gueltigen
    UTF-8-Boundary, sodass der Empfanger einen validen String bekommt.

    Edge-Cases:
    - text ist None oder leer: gibt "" zurueck.
    - text <= max_bytes: gibt text unveraendert zurueck.
    - text > max_bytes: schneidet ab, haengt Marker an.
    """
    if not text:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    truncated_bytes = encoded[:max_bytes]
    # An gueltiger UTF-8-Boundary ausrichten. ``decode(errors="ignore")``
    # schluckt genau die unvollstaendigen Multibyte-Sequenzen am Ende.
    head = truncated_bytes.decode("utf-8", errors="ignore")
    return head + _TRUNCATION_MARKER


def run_in_container(
    *,
    server_id: int,
    command: list[str],
    timeout: int,
    user_id: int | None = None,
    node: Any | None = None,
) -> dict[str, Any]:
    """Fuehrt ``command`` als argv im MSM-Container von ``server_id`` aus.

    Args:
        server_id: Primärschluessel des Servers in der MSM-DB. Wird ueber
            ``container_name_for`` in den Container-Namen aufgeloest --
            kein User-Input fliesst in den Container-Namen.
        command: argv-Liste (nicht String!). Wird 1:1 an
            ``docker_service.exec_in`` weitergereicht.
        timeout: Sekunden. Wird durch Blueprint ``execTimeoutSeconds``
            bestimmt (1..600).
        user_id: MSM-User-ID des Ausloesers. Fuer Audit-Log.

    Returns:
        Dict mit ``ok: bool``, ``stdout``, ``stderr`` (jeweils truncated).
        Bei ``ok=False`` zusaetzlich ``error`` (kurze Beschreibung).
    """
    container = container_name_for(server_id)

    # Audit-Log VOR dem exec -- so wissen wir auch bei Crashes, dass der
    # Befehl versucht wurde. Output wird NIE geloggt.
    logger.info(
        "exec attempt server=%d user=%s container=%s argc=%d timeout=%ds",
        server_id,
        user_id,
        container,
        len(command),
        timeout,
    )

    raw = docker_service.exec_in(container, command, timeout=timeout, node=node)

    if raw.get("ok"):
        logger.info(
            "exec ok server=%d user=%s argc=%d",
            server_id,
            user_id,
            len(command),
        )
        return {
            "ok": True,
            "stdout": _truncate_output(raw.get("stdout") or ""),
            "stderr": _truncate_output(raw.get("stderr") or ""),
        }

    # Fehlerpfad: error-Text kann interne Details enthalten (z. B.
    # "command not found: foo"), ist aber kein User-Secret. Wir geben
    # ihn gedeckelt (500 chars, wie docker_service.exec_in selbst schon)
    # zurueck. NICHT loggen -- koennte args enthalten, die in Logs nichts
    # zu suchen haben (z. B. Tokens als argv). Stattdessen nur "failed":
    logger.info(
        "exec failed server=%d user=%s argc=%d",
        server_id,
        user_id,
        len(command),
    )
    return {
        "ok": False,
        "stdout": _truncate_output(raw.get("stdout") or ""),
        "stderr": _truncate_output(raw.get("stderr") or ""),
        "error": raw.get("error") or "Exec fehlgeschlagen",
    }
