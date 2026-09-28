"""Rechte anderer Benutzer aendern — Nutzlast und Ausfuehrung der vier Werkzeuge.

Jede Grenze kommt aus `rechtevergabe_service`, derselben Stelle, die die
Panel-Router fragen. Hier steht nur, was die KI zusaetzlich braucht:

* **Nachsichtig lesen, streng speichern.** `mode` versteht "add" wie "merge",
  `permissions` auch als Komma-Text. Unbekannte Rechte werden abgewiesen, nicht
  still verworfen — sonst stuende auf der Karte etwas anderes als gemeint.
* **Die Karte zeigt die Aenderung, nicht den Wunsch.** Hinzugefuegt und
  entzogen stehen getrennt da, aufgeloest aus dem Bestand. Wer bestaetigt,
  liest, was passiert, nicht was das Modell darueber schreibt.
* **`always_confirm`** entscheidet der Bestand: entzieht eine Vergabe an einen
  Benutzer etwas oder gibt sie ihm mehr als `UNCRITICAL_SERVER_PERMISSIONS`,
  fragt sie auch im autonomen Modus (`ai_tool_registry.verlangt_klick`). Eine
  Rolle ohne Traeger anzulegen oder zu aendern fragt dort nie, auch mit
  kritischen Rechten: sie wirkt erst, wenn sie jemandem zugewiesen wird, und
  diese Zuweisung fragt (Vorgabe des Betreibers vom 25.09.2026). Traegt sie
  schon jemand, ist die Aenderung eine Vergabe an diese Benutzer und fragt
  nach derselben Grenze wie oben. Sonst liesse sich eine harmlose Rolle
  autonom zuweisen und danach autonom um jedes Recht erweitern.
* **Der Stand beim Vorschlagen reist mit.** Zwischen Karte und Klick liegt ein
  Zeitfenster ohne Obergrenze. Hat jemand die Rechte darin im Panel geaendert,
  fuehrt der Klick nicht den alten Plan ueber den neuen Stand aus, sondern
  scheitert mit `AI_ACTION_REVISION_CONFLICT`.
"""
from __future__ import annotations

import asyncio
import logging
import re
import threading

from pydantic import ValidationError
from sqlalchemy.orm import Session

from models import Role, Server, User, UserRole
from schemas.role import RoleCreate, RoleUpdate
from services import rechtevergabe_service, role_service
from services.ai_action_errors import AiActionStateError, AiActionValidationError
from services.ai_proposals.base import _AusfuehrungsRahmen, _Ausgefuehrt
from services.ai_redaction import redact_sensitive_text
from services.ai_tools.user_tools import benutzername, kennung
from services.permission_catalog import (
    SERVER_KEYS,
    UNCRITICAL_SERVER_PERMISSIONS,
    is_known_key,
)
from services.permission_service import list_user_server_permission_keys

logger = logging.getLogger(__name__)

_MODI = {
    "merge": "merge", "add": "merge", "grant": "merge", "hinzufuegen": "merge",
    "replace": "replace", "set": "replace", "ersetzen": "replace",
    "remove": "remove", "revoke": "remove", "entziehen": "remove", "entfernen": "remove",
}


def _modus(roh: object) -> str:
    if roh is None or (isinstance(roh, str) and not roh.strip()):
        return "merge"
    modus = _MODI.get(str(roh).strip().lower())
    if modus is None:
        raise AiActionValidationError("mode ist merge, replace oder remove")
    return modus


def _liste(roh: object, name: str) -> list:
    """Eine Liste aus einem Werkzeugargument; `"a, b"` zaehlt wie `["a", "b"]`."""
    if roh is None:
        return []
    if isinstance(roh, str):
        return [teil for teil in re.split(r"[,\s]+", roh) if teil]
    if isinstance(roh, (list, tuple)):
        return list(roh)
    raise AiActionValidationError(f"'{name}' muss eine Liste sein")


def _rechteliste(roh: object, *, nur_server: bool) -> set[str]:
    schluessel = {str(eintrag).strip().lower() for eintrag in _liste(roh, "permissions")}
    schluessel.discard("")
    unbekannt = sorted(k for k in schluessel if not is_known_key(k))
    if unbekannt:
        raise AiActionValidationError(
            f"Unbekannte Rechte: {unbekannt}. Die gültigen Schlüssel liefert list_roles."
        )
    if nur_server:
        global_ = sorted(schluessel - SERVER_KEYS)
        if global_:
            raise AiActionValidationError(
                f"{global_} gelten global und hängen an Rollen, nicht an einem Server."
            )
    return schluessel


def _abgewiesen(fehler: rechtevergabe_service.RechteFehler) -> AiActionValidationError:
    return AiActionValidationError(fehler.detail)


def _bei_ausfuehrung(fehler: rechtevergabe_service.RechteFehler) -> AiActionStateError:
    # Eine Grenze, die beim Vorschlagen hielt und jetzt nicht mehr, heisst:
    # dem Benutzer fehlt inzwischen ein Recht, das die Aenderung braucht.
    if fehler.status_code == 403:
        return AiActionStateError("AI_ACTION_ACCESS_REVOKED")
    return AiActionStateError("AI_ACTION_EXECUTION_FAILED")


def _ziel(db: Session, user_id: int) -> User:
    ziel = db.get(User, user_id)
    if ziel is None:
        raise AiActionValidationError("Benutzer nicht gefunden")
    if ziel.is_owner:
        raise AiActionValidationError(
            "Das ist der Owner: er hat jedes Recht, und seine Rechte ändert niemand."
        )
    return ziel


def _unkritisch(schluessel: set[str]) -> bool:
    return schluessel <= UNCRITICAL_SERVER_PERMISSIONS


def _vorschau(preview: dict, *, entzogen: bool, vergeben: set[str]) -> dict:
    if entzogen or not _unkritisch(vergeben):
        preview["always_confirm"] = True
    return preview


# ── Serverrechte eines Benutzers ──────────────────────────────────────


def _user_server_permission_payload(
    db: Session, user: User, server: Server, rest: dict
) -> tuple[dict, dict]:
    if set(rest) - {"user_id", "permissions", "mode"}:
        raise AiActionValidationError("propose_user_server_permission hat ungueltige Argumente")
    ziel = _ziel(db, kennung(rest.get("user_id"), "user_id"))
    modus = _modus(rest.get("mode"))
    angefragt = _rechteliste(rest.get("permissions"), nur_server=True)
    if not angefragt and modus != "replace":
        raise AiActionValidationError("Es wurden keine Rechte genannt")

    vorher = set(list_user_server_permission_keys(db, ziel.id, server.id))
    if modus == "merge":
        nachher = vorher | angefragt
    elif modus == "replace":
        nachher = set(angefragt)
    else:
        nachher = vorher - angefragt
    # Ohne `server.view` sieht der Benutzer den Server gar nicht, und jedes
    # andere Recht darauf liefe ins Leere. Beim Vergeben kommt es deshalb mit;
    # beim Entziehen geht es nur zusammen mit allem anderen.
    if nachher and "server.view" not in nachher:
        if modus == "remove":
            raise AiActionValidationError(
                "server.view lässt sich nur zusammen mit allen anderen Rechten "
                "auf diesem Server entziehen"
            )
        nachher.add("server.view")
    if nachher == vorher:
        raise AiActionValidationError(
            f"Keine Änderung: {benutzername(ziel)} hat auf diesem Server bereits "
            f"genau {sorted(vorher) or 'keine Rechte'}."
        )
    try:
        rechtevergabe_service.check_server_permissions(
            db, user, ziel.id, server.id, sorted(nachher)
        )
    except rechtevergabe_service.RechteFehler as fehler:
        raise _abgewiesen(fehler) from None

    hinzu, weg = nachher - vorher, vorher - nachher
    payload = {"user_id": ziel.id, "permissions": sorted(nachher), "before": sorted(vorher)}
    preview = {
        "operation": "user_server_permission",
        "target_user": benutzername(ziel),
        "server_name": redact_sensitive_text(str(server.name or ""))[:128],
        "permissions_after": sorted(nachher),
        "permissions_added": sorted(hinzu),
        "permissions_removed": sorted(weg),
    }
    return payload, _vorschau(preview, entzogen=bool(weg), vergeben=hinzu)


def _benachrichtigen(ziel: User, server: Server, actor: User) -> None:
    """Dieselbe Mail wie beim ersten Zugang ueber das Panel.

    Der Versand ist asynchron, die Ausfuehrung eines Vorschlags nicht — also
    in einem eigenen Faden, und ein Fehler darin kostet die Mail, nicht die
    bereits gespeicherte Vergabe.
    """
    from services import EmailService

    if not EmailService.is_configured() or not ziel.email_notifications or not ziel.email:
        return
    werte = (ziel.email, ziel.username, server.name, actor.username)

    def _senden() -> None:
        try:
            asyncio.run(EmailService.send_user_added_to_server_notification(*werte))
        except Exception:  # noqa: BLE001 — die Vergabe steht, nur die Mail fehlt
            logger.warning("Zugangsmail nach KI-Vergabe nicht versendet", exc_info=True)

    threading.Thread(target=_senden, daemon=True).start()


def _ausfuehren_user_server_permission(db: Session, rahmen: _AusfuehrungsRahmen) -> _Ausgefuehrt:
    p = rahmen.payload
    user_id, server_id = int(p["user_id"]), int(rahmen.server_id or 0)
    jetzt = set(list_user_server_permission_keys(db, user_id, server_id))
    if jetzt != set(p.get("before") or []):
        raise AiActionStateError("AI_ACTION_REVISION_CONFLICT")
    try:
        gesetzt, erstmals, ziel, server = rechtevergabe_service.set_server_permissions(
            db, rahmen.active_user, user_id, server_id, list(p["permissions"])
        )
    except rechtevergabe_service.RechteFehler as fehler:
        raise _bei_ausfuehrung(fehler) from fehler
    if erstmals:
        _benachrichtigen(ziel, server, rahmen.active_user)
    return _Ausgefuehrt(result={
        "user_id": user_id,
        "username": benutzername(ziel),
        "server_id": server_id,
        "permissions": gesetzt,
        "added": sorted(set(gesetzt) - jetzt),
        "removed": sorted(jetzt - set(gesetzt)),
    })


# ── Rollen ────────────────────────────────────────────────────────────


def _zuweisungen(db: Session, role_id: int) -> int:
    neu = {uid for (uid,) in db.query(UserRole.user_id).filter(UserRole.role_id == role_id).all()}
    alt = {uid for (uid,) in db.query(User.id).filter(User.role_id == role_id).all()}
    return len(neu | alt)


def _eigene_rolle(db: Session, role_id: int) -> Role:
    rolle = role_service.get_role(db, role_id)
    if rolle is None:
        # Der Hinweis gehört in die Meldung: ein Modell, das eine erfundene
        # Kennung mitschickt, versucht es sonst mit der nächsten.
        raise AiActionValidationError(
            f"Eine Rolle mit role_id {role_id} gibt es nicht. Zum Anlegen role_id "
            "weglassen; vorhandene Rollen liefert list_roles."
        )
    if rolle.is_system:
        raise AiActionValidationError(
            f"'{rolle.name}' ist eine Systemrolle; die ändert die KI nicht."
        )
    return rolle


def _role_set_payload(db: Session, user: User, rest: dict) -> tuple[dict, dict]:
    if set(rest) - {"role_id", "name", "description", "permissions"}:
        raise AiActionValidationError("propose_role_set hat ungueltige Argumente")
    roh = rest.get("role_id")
    role_id = None if roh in (None, "", 0) else kennung(roh, "role_id")
    name = str(rest["name"]).strip() if rest.get("name") not in (None, "") else None
    beschreibung = (
        redact_sensitive_text(str(rest["description"]).strip())[:255]
        if rest.get("description") is not None
        else None
    )
    schluessel = (
        _rechteliste(rest["permissions"], nur_server=False)
        if rest.get("permissions") is not None
        else None
    )

    try:
        if role_id is None:
            if not name:
                raise AiActionValidationError("Eine neue Rolle braucht einen name")
            RoleCreate(name=name, description=beschreibung, permissions=sorted(schluessel or ()))
        else:
            RoleUpdate(
                name=name,
                description=beschreibung,
                permissions=sorted(schluessel) if schluessel is not None else None,
            )
    except ValidationError:
        raise AiActionValidationError(
            "Rollenname: 2 bis 64 Zeichen, nur Buchstaben, Ziffern, _ und -"
        ) from None

    try:
        if role_id is None:
            if role_service.get_role_by_name(db, name):
                raise AiActionValidationError(
                    f"Eine Rolle '{name}' gibt es schon; ändern geht mit ihrer role_id."
                )
            rechtevergabe_service.ensure_no_role_escalation(db, user, sorted(schluessel or ()))
            vorher: set[str] = set()
            nachher = set(schluessel or ())
            betroffen = 0
            payload = {
                "role_id": None,
                "name": name,
                "description": beschreibung,
                "permissions": sorted(nachher),
            }
        else:
            rolle = _eigene_rolle(db, role_id)
            if name is None and beschreibung is None and schluessel is None:
                raise AiActionValidationError("Es wurde nichts genannt, das geändert werden soll")
            if name is not None and name != rolle.name:
                andere = role_service.get_role_by_name(db, name)
                if andere is not None and andere.id != rolle.id:
                    raise AiActionValidationError("Name bereits vergeben")
            vorher = set(role_service.role_permission_keys(db, rolle.id))
            if schluessel is not None:
                rechtevergabe_service.ensure_no_role_escalation(db, user, sorted(vorher))
                rechtevergabe_service.ensure_no_role_escalation(db, user, sorted(schluessel))
            nachher = set(schluessel) if schluessel is not None else vorher
            betroffen = _zuweisungen(db, rolle.id)
            name = name or rolle.name
            payload = {
                "role_id": rolle.id,
                "name": name if name != rolle.name else None,
                "description": beschreibung,
                "permissions": sorted(schluessel) if schluessel is not None else None,
                "before": sorted(vorher),
            }
    except rechtevergabe_service.RechteFehler as fehler:
        raise _abgewiesen(fehler) from None

    hinzu, weg = nachher - vorher, vorher - nachher
    preview = {
        "operation": "role_set",
        "name": redact_sensitive_text(name)[:64],
        "role_permissions": sorted(nachher),
        "permissions_added": sorted(hinzu),
        "permissions_removed": sorted(weg),
        "role_users": betroffen,
    }
    # Siehe Modulkopf: eine Rolle ohne Traeger fragt nicht, eine vergebene
    # Rolle fragt wie eine Vergabe an ihre Traeger.
    if betroffen:
        _vorschau(preview, entzogen=bool(weg), vergeben=hinzu)
    return payload, preview


def _ausfuehren_role_set(db: Session, rahmen: _AusfuehrungsRahmen) -> _Ausgefuehrt:
    p = rahmen.payload
    actor = rahmen.active_user
    try:
        if p.get("role_id") is None:
            rolle = rechtevergabe_service.create_role(
                db, actor, str(p["name"]), p.get("description"), list(p.get("permissions") or [])
            )
        else:
            role_id = int(p["role_id"])
            rolle = role_service.get_role(db, role_id)
            if rolle is None or rolle.is_system:
                raise AiActionStateError("AI_ACTION_EXECUTION_FAILED")
            if set(role_service.role_permission_keys(db, role_id)) != set(p.get("before") or []):
                raise AiActionStateError("AI_ACTION_REVISION_CONFLICT")
            rolle = rechtevergabe_service.update_role(
                db, actor, role_id, p.get("name"), p.get("description"), p.get("permissions")
            )
    except rechtevergabe_service.RechteFehler as fehler:
        raise _bei_ausfuehrung(fehler) from fehler
    return _Ausgefuehrt(result={
        "role_id": rolle.id,
        "name": rolle.name,
        "permissions": role_service.role_permission_keys(db, rolle.id),
    })


def _user_roles_payload(db: Session, user: User, rest: dict) -> tuple[dict, dict]:
    if set(rest) - {"user_id", "role_ids", "mode"}:
        raise AiActionValidationError("propose_user_roles hat ungueltige Argumente")
    ziel = _ziel(db, kennung(rest.get("user_id"), "user_id"))
    modus = _modus(rest.get("mode"))
    angefragt = {kennung(eintrag, "role_ids") for eintrag in _liste(rest.get("role_ids"), "role_ids")}
    if not angefragt and modus != "replace":
        raise AiActionValidationError("Es wurden keine Rollen genannt")

    vorher = set(role_service.effective_user_role_ids(db, ziel))
    if modus == "merge":
        nachher = vorher | angefragt
    elif modus == "replace":
        nachher = set(angefragt)
    else:
        nachher = vorher - angefragt
    if nachher == vorher:
        raise AiActionValidationError(
            f"Keine Änderung: {benutzername(ziel)} hat genau diese Rollen bereits."
        )
    try:
        rechtevergabe_service.check_role_assignment(db, user, ziel.id, sorted(nachher))
    except rechtevergabe_service.RechteFehler as fehler:
        raise _abgewiesen(fehler) from None

    hinzu, weg = nachher - vorher, vorher - nachher
    namen = {
        rolle.id: redact_sensitive_text(str(rolle.name))
        for rolle in db.query(Role).filter(Role.id.in_(list(nachher | vorher))).all()
    }
    neue_rechte = {
        key for role_id in hinzu for key in role_service.role_permission_keys(db, role_id)
    }
    payload = {"user_id": ziel.id, "role_ids": sorted(nachher), "before": sorted(vorher)}
    preview = {
        "operation": "user_roles",
        "target_user": benutzername(ziel),
        "roles_after": [namen.get(r, str(r)) for r in sorted(nachher)],
        "roles_added": [namen.get(r, str(r)) for r in sorted(hinzu)],
        "roles_removed": [namen.get(r, str(r)) for r in sorted(weg)],
        "role_permissions": sorted(neue_rechte),
    }
    return payload, _vorschau(preview, entzogen=bool(weg), vergeben=neue_rechte)


def _ausfuehren_user_roles(db: Session, rahmen: _AusfuehrungsRahmen) -> _Ausgefuehrt:
    p = rahmen.payload
    user_id = int(p["user_id"])
    ziel = db.get(User, user_id)
    if ziel is None:
        raise AiActionStateError("AI_ACTION_EXECUTION_FAILED")
    if set(role_service.effective_user_role_ids(db, ziel)) != set(p.get("before") or []):
        raise AiActionStateError("AI_ACTION_REVISION_CONFLICT")
    try:
        ziel = rechtevergabe_service.assign_roles(
            db, rahmen.active_user, user_id, [int(r) for r in p["role_ids"]]
        )
    except rechtevergabe_service.RechteFehler as fehler:
        raise _bei_ausfuehrung(fehler) from fehler
    return _Ausgefuehrt(result={
        "user_id": user_id,
        "username": benutzername(ziel),
        "role_ids": role_service.effective_user_role_ids(db, ziel),
    })


def _role_delete_payload(db: Session, user: User, rest: dict) -> tuple[dict, dict]:
    if set(rest) - {"role_id"}:
        raise AiActionValidationError("propose_role_delete hat ungueltige Argumente")
    rolle = _eigene_rolle(db, kennung(rest.get("role_id"), "role_id"))
    anzahl = _zuweisungen(db, rolle.id)
    if anzahl:
        raise AiActionValidationError(
            f"Die Rolle '{rolle.name}' ist noch {anzahl} Benutzer(n) zugewiesen; "
            "erst dort entfernen (propose_user_roles)."
        )
    payload = {"role_id": rolle.id}
    preview = {
        "operation": "role_delete",
        "name": redact_sensitive_text(str(rolle.name))[:64],
        "role_permissions": role_service.role_permission_keys(db, rolle.id),
    }
    return payload, preview


def _ausfuehren_role_delete(db: Session, rahmen: _AusfuehrungsRahmen) -> _Ausgefuehrt:
    role_id = int(rahmen.payload["role_id"])
    rolle = role_service.get_role(db, role_id)
    if rolle is None or rolle.is_system:
        raise AiActionStateError("AI_ACTION_EXECUTION_FAILED")
    name = rolle.name
    try:
        rechtevergabe_service.delete_role(db, rahmen.active_user, role_id)
    except rechtevergabe_service.RechteFehler as fehler:
        raise _bei_ausfuehrung(fehler) from fehler
    return _Ausgefuehrt(result={"role_id": role_id, "name": name, "deleted": True})
