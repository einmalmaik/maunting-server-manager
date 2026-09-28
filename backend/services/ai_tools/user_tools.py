"""Benutzer, Rollen und Rechte — was die KI darueber lesen darf.

Anlass (Betreiberplan vom 24.09.2026): unterwegs per Stimme "gib dem Kollegen,
der sich vorhin registriert hat, die normalen Rechte auf dem Minecraft-Server".
Dafuer muss die KI drei Dinge herausfinden, die sie bisher nicht sehen konnte:
wer gemeint ist, was er schon hat, und welche Rechte es ueberhaupt gibt.

**Was die Lesewerkzeuge nie herausgeben:** E-Mail-Adressen, Passwort- und
2FA-Felder, Tokens, Sitzungen. Sie werden gar nicht erst gelesen — die
Abfragen holen nur Kennung, Name, Status, Zeitpunkte und Rollen. Die letzte
Aktivitaet ist der juengste Anmeldetoken, nicht seine Geraeteangabe.

Die Rechte sind die der Panel-Routen (`ai_tool_registry`, Abschnitt "Benutzer
und Rollen, lesend"); geschrieben wird ueber `ai_proposals/user_proposals.py`.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

from sqlalchemy import func
from sqlalchemy.orm import Session

from models import RefreshToken, Role, RolePermission, Server, ServerPermission, User, UserRole
from services import permission_service, role_service
from services.ai_action_errors import AiActionValidationError
from services.ai_redaction import redact_sensitive_text
from services.ai_tools.base import (
    _RATIONALE_REQUIRED,
    _RATIONALE_SCHEMA,
    _SERVER_ID_SCHEMA,
    _function,
    _positive_int,
    _require_no_arguments,
)
from services.permission_catalog import (
    GLOBAL_PERMISSIONS,
    SERVER_PERMISSIONS,
    SYSTEM_ROLE_ADMIN,
    UNCRITICAL_SERVER_PERMISSIONS,
)

MAX_LISTED_USERS = 25
#: Wie viele Konten die ungefaehre Suche hoechstens vergleicht. Verglichen
#: wird in Python (`SequenceMatcher`), weil "heisst ungefaehr GamerXYZ" kein
#: `LIKE` ist; die Grenze haelt eine grosse Installation davon ab, dafuer
#: ihre ganze Benutzertabelle zu laden.
MAX_USER_CANDIDATES = 5_000
MAX_RECENT_HOURS = 24 * 90
#: Ab dieser Aehnlichkeit gilt ein Name als Treffer. Darunter liegen
#: Zufallsnaehen wie "max" zu "admin".
MIN_MATCH = 0.55
MAX_QUERY_CHARS = 64


def _user_tool_definitions() -> list[dict]:
    """Schemas der sieben Benutzer- und Rollenwerkzeuge.

    Bewusst karg: der Katalog geht in jeder Runde mit und stand beim Bau 83
    Zeichen unter seiner Grenze. Wann welches Werkzeug, was "unkritisch" heisst
    und warum Serverrechte vor Rollen gehen, steht in `ai_prompt.BENUTZER_UND_RECHTE`.
    """
    rechte = {
        "type": "array",
        "items": {"type": "string", "maxLength": 64},
        "maxItems": 64,
    }
    modus = {"type": "string", "enum": ["merge", "replace", "remove"]}
    return [
        _function(
            "list_users",
            "Sucht Benutzer ungefähr nach Name und/oder nach Registrierung "
            "bzw. Aktivität der letzten Stunden. Ohne E-Mail.",
            {
                "query": {"type": "string", "maxLength": MAX_QUERY_CHARS},
                "recent_hours": {"type": "integer", "minimum": 1, "maximum": MAX_RECENT_HOURS},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LISTED_USERS},
            },
            [],
        ),
        _function(
            "read_user_permissions",
            "Rollen, globale Rechte und Serverrechte eines Benutzers.",
            {"user_id": {"type": "integer", "minimum": 1}},
            ["user_id"],
        ),
        _function(
            "list_roles",
            "Alle Rollen mit Rechten und Nutzerzahl, dazu der Rechtekatalog.",
            {},
            [],
        ),
        _function(
            "propose_user_server_permission",
            "Serverrechte eines Benutzers vergeben, ändern oder entziehen.",
            {
                **_SERVER_ID_SCHEMA,
                "user_id": {"type": "integer", "minimum": 1},
                "permissions": rechte,
                "mode": modus,
                **_RATIONALE_SCHEMA,
            },
            ["server_id", "user_id", "permissions", *_RATIONALE_REQUIRED],
        ),
        _function(
            "propose_role_set",
            "Rolle erstellen bzw. anlegen oder ändern (mit role_id).",
            {
                "role_id": {"type": "integer", "minimum": 1},
                "name": {"type": "string", "maxLength": 64},
                "description": {"type": "string", "maxLength": 255},
                "permissions": rechte,
                **_RATIONALE_SCHEMA,
            },
            [*_RATIONALE_REQUIRED],
        ),
        _function(
            "propose_user_roles",
            "Einem Benutzer Rollen zuweisen oder entziehen.",
            {
                "user_id": {"type": "integer", "minimum": 1},
                "role_ids": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 1},
                    "maxItems": 32,
                },
                "mode": modus,
                **_RATIONALE_SCHEMA,
            },
            ["user_id", "role_ids", *_RATIONALE_REQUIRED],
        ),
        _function(
            "propose_role_delete",
            "Rolle löschen, die niemandem mehr zugewiesen ist.",
            {"role_id": {"type": "integer", "minimum": 1}, **_RATIONALE_SCHEMA},
            ["role_id", *_RATIONALE_REQUIRED],
        ),
    ]


# ── Kleine Helfer, auch vom Vorschlagspfad benutzt ────────────────────


def kennung(wert: object, name: str) -> int:
    """Eine Kennung aus einem Werkzeugargument — nachsichtig gelesen.

    `"42"` ist dieselbe Angabe wie `42`; `true`, `0` oder `"zwei"` nicht.
    """
    if isinstance(wert, str) and wert.strip().isdigit():
        wert = int(wert.strip())
    if isinstance(wert, bool) or not isinstance(wert, int) or wert < 1:
        raise AiActionValidationError(f"'{name}' fehlt oder ist keine gültige Kennung")
    return wert


def benutzername(user: User) -> str:
    # Frei gewaehlt und deshalb redigiert wie jeder andere freie Text.
    return redact_sensitive_text(str(user.username or ""))[:64]


def rollennamen(db: Session, user_ids: list[int]) -> dict[int, list[str]]:
    """Rollennamen je Benutzer, samt der alten `users.role_id`-Spalte."""
    if not user_ids:
        return {}
    namen: dict[int, set[str]] = {uid: set() for uid in user_ids}
    zeilen = (
        db.query(UserRole.user_id, Role.name)
        .join(Role, Role.id == UserRole.role_id)
        .filter(UserRole.user_id.in_(user_ids))
        .all()
    )
    alt = (
        db.query(User.id, Role.name)
        .join(Role, Role.id == User.role_id)
        .filter(User.id.in_(user_ids))
        .all()
    )
    for uid, name in [*zeilen, *alt]:
        namen.setdefault(uid, set()).add(redact_sensitive_text(str(name)))
    return {uid: sorted(werte) for uid, werte in namen.items()}


def _aware(zeitpunkt: datetime | None) -> datetime | None:
    if zeitpunkt is None:
        return None
    return zeitpunkt if zeitpunkt.tzinfo else zeitpunkt.replace(tzinfo=timezone.utc)


def _iso(zeitpunkt: datetime | None) -> str | None:
    zeitpunkt = _aware(zeitpunkt)
    return zeitpunkt.isoformat() if zeitpunkt else None


def _normiert(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", ascii_text.lower())


def aehnlichkeit(suche: str, name: str) -> float:
    """Wie gut ein Benutzername zu einer ungefaehren Angabe passt, 0 bis 1.

    Gesprochen kommt ein Name selten buchstabengetreu an: "Gamer X Y Z",
    "gamerxyz", "GamerXYZ_" meinen dasselbe Konto. Verglichen wird deshalb
    ohne Gross-/Kleinschreibung, Leer- und Sonderzeichen. Ein Teilstring
    schlaegt die reine Zeichenaehnlichkeit — "gamer" soll "xXGamerXx" finden,
    auch wenn die beiden als Ganzes wenig gemein haben.
    """
    s, n = _normiert(suche), _normiert(name)
    if not s or not n:
        return 0.0
    if s == n:
        return 1.0
    if s in n:
        return 0.8 + 0.15 * len(s) / len(n)
    if n in s:
        return 0.7
    return SequenceMatcher(None, s, n).ratio()


# ── Die drei Lesewerkzeuge ────────────────────────────────────────────


def _execute_list_users(db: Session, user: User, arguments: dict) -> dict:
    if not permission_service.has_global_permission(db, user, "users.read"):
        raise AiActionValidationError("Benutzerliste ist nicht erlaubt")
    if set(arguments) - {"query", "recent_hours", "limit"}:
        raise AiActionValidationError("list_users hat ungueltige Argumente")
    suche = str(arguments.get("query") or "").strip()[:MAX_QUERY_CHARS]
    stunden_roh = arguments.get("recent_hours")
    if isinstance(stunden_roh, str) and stunden_roh.strip().isdigit():
        stunden_roh = int(stunden_roh.strip())
    stunden = (
        _positive_int(stunden_roh, name="recent_hours", default=0, minimum=1, maximum=MAX_RECENT_HOURS)
        if stunden_roh not in (None, "", 0)
        else 0
    )
    limit_roh = arguments.get("limit")
    if isinstance(limit_roh, str) and limit_roh.strip().isdigit():
        limit_roh = int(limit_roh.strip())
    # Nachsichtig nach oben: "zeig alle" wird zum Deckel, nicht zum Fehler.
    if isinstance(limit_roh, int) and not isinstance(limit_roh, bool) and limit_roh > MAX_LISTED_USERS:
        limit_roh = MAX_LISTED_USERS
    limit = _positive_int(limit_roh, name="limit", default=10, minimum=1, maximum=MAX_LISTED_USERS)

    konten = (
        db.query(User.id, User.username, User.is_owner, User.is_active, User.created_at)
        .order_by(User.id.desc())
        .limit(MAX_USER_CANDIDATES)
        .all()
    )
    aktiv = dict(
        db.query(RefreshToken.user_id, func.max(RefreshToken.created_at))
        .group_by(RefreshToken.user_id)
        .all()
    )
    grenze = datetime.now(timezone.utc) - timedelta(hours=stunden) if stunden else None

    treffer: list[tuple[float, datetime, object]] = []
    for konto in konten:
        angelegt = _aware(konto.created_at)
        zuletzt = _aware(aktiv.get(konto.id))
        juengstes = max((z for z in (angelegt, zuletzt) if z is not None), default=None)
        if grenze is not None and (juengstes is None or juengstes < grenze):
            continue
        guete = aehnlichkeit(suche, str(konto.username or "")) if suche else 1.0
        if suche and guete < MIN_MATCH:
            continue
        treffer.append((guete, juengstes or datetime.min.replace(tzinfo=timezone.utc), konto))
    # Mit Suchtext zaehlt zuerst die Naehe zum Namen, sonst das Juengste.
    treffer.sort(key=lambda t: (t[0], t[1]), reverse=True)
    gewaehlt = treffer[:limit]
    rollen = rollennamen(db, [konto.id for _, _, konto in gewaehlt])
    eintraege = []
    for guete, _, konto in gewaehlt:
        eintrag = {
            "user_id": konto.id,
            "username": redact_sensitive_text(str(konto.username or ""))[:64],
            "is_owner": bool(konto.is_owner),
            "is_active": bool(konto.is_active),
            "created_at": _iso(konto.created_at),
            "last_active_at": _iso(aktiv.get(konto.id)),
            "roles": rollen.get(konto.id, []),
        }
        if suche:
            eintrag["match"] = round(guete, 2)
        eintraege.append(eintrag)
    return {
        "users": eintraege,
        "count": len(eintraege),
        "truncated": len(treffer) > len(gewaehlt),
    }


def _execute_read_user_permissions(db: Session, user: User, arguments: dict) -> dict:
    if not permission_service.has_global_permission(db, user, "users.permissions.manage"):
        raise AiActionValidationError("Rechteeinsicht ist nicht erlaubt")
    if set(arguments) - {"user_id"}:
        raise AiActionValidationError("read_user_permissions hat ungueltige Argumente")
    ziel = db.get(User, kennung(arguments.get("user_id"), "user_id"))
    if ziel is None:
        raise AiActionValidationError("Benutzer nicht gefunden")

    rollen = [
        {"role_id": rolle.id, "name": redact_sensitive_text(str(rolle.name))}
        for rolle in db.query(Role)
        .filter(Role.id.in_(role_service.effective_user_role_ids(db, ziel)))
        .order_by(Role.id)
        .all()
    ]
    zeilen = (
        db.query(ServerPermission.server_id, ServerPermission.permission_key)
        .filter(ServerPermission.user_id == ziel.id)
        .all()
    )
    je_server: dict[int, set[str]] = {}
    for server_id, schluessel in zeilen:
        je_server.setdefault(server_id, set()).add(schluessel)
    # Den Namen eines Servers bekommt nur, wer den Server selbst sieht — die
    # Kennung steht ohnehin in der Delegation, der Name ist eine Auskunft mehr.
    sichtbar = permission_service.list_visible_server_ids(db, user)
    namen = dict(
        db.query(Server.id, Server.name).filter(Server.id.in_(list(je_server))).all()
    ) if je_server else {}
    serverrechte = [
        {
            "server_id": server_id,
            "server_name": (
                redact_sensitive_text(str(namen.get(server_id) or ""))[:128]
                if sichtbar is None or server_id in sichtbar
                else None
            ),
            "permissions": sorted(schluessel),
            "only_uncritical": schluessel <= UNCRITICAL_SERVER_PERMISSIONS,
        }
        for server_id, schluessel in sorted(je_server.items())
    ]
    ergebnis = {
        "user_id": ziel.id,
        "username": benutzername(ziel),
        "is_owner": bool(ziel.is_owner),
        "is_active": bool(ziel.is_active),
        "roles": rollen,
        "global_permissions": role_service.effective_user_role_permission_keys(db, ziel),
        "server_permissions": serverrechte,
    }
    if ziel.is_owner:
        ergebnis["note"] = (
            "Owner hat jedes Recht; Rollen und Serverrechte lassen sich für ihn "
            "nicht vergeben."
        )
    return ergebnis


def _execute_list_roles(db: Session, user: User, arguments: dict) -> dict:
    if not any(
        permission_service.has_global_permission(db, user, recht)
        for recht in ("roles.manage", "users.permissions.manage")
    ):
        raise AiActionValidationError("Rolleneinsicht ist nicht erlaubt")
    _require_no_arguments("list_roles", arguments)

    zuweisungen: dict[int, set[int]] = {}
    for user_id, role_id in db.query(UserRole.user_id, UserRole.role_id).all():
        zuweisungen.setdefault(role_id, set()).add(user_id)
    for user_id, role_id in db.query(User.id, User.role_id).filter(User.role_id.isnot(None)).all():
        zuweisungen.setdefault(role_id, set()).add(user_id)
    schluessel: dict[int, list[str]] = {}
    for role_id, key in db.query(RolePermission.role_id, RolePermission.permission_key).all():
        schluessel.setdefault(role_id, []).append(key)

    rollen = []
    for rolle in role_service.list_roles(db):
        eintrag = {
            "role_id": rolle.id,
            "name": redact_sensitive_text(str(rolle.name)),
            "description": redact_sensitive_text(str(rolle.description or ""))[:255] or None,
            "is_system": bool(rolle.is_system),
            "user_count": len(zuweisungen.get(rolle.id, ())),
        }
        # Die admin-Rolle traegt jeden Schluessel des Katalogs; achtzig Namen
        # sagen dem Modell nichts, was "alle" nicht sagt.
        if rolle.is_system and rolle.name == SYSTEM_ROLE_ADMIN:
            eintrag["all_permissions"] = True
        else:
            eintrag["permissions"] = sorted(schluessel.get(rolle.id, []))
        rollen.append(eintrag)
    return {
        "roles": rollen,
        "uncritical_server_permissions": sorted(UNCRITICAL_SERVER_PERMISSIONS),
        "server_permissions": {p.key: p.label for p in SERVER_PERMISSIONS},
        "global_permissions": {p.key: p.label for p in GLOBAL_PERMISSIONS},
    }


def execute_user_read_tool(db: Session, *, user: User, tool_name: str, arguments: dict) -> dict:
    if tool_name == "list_users":
        return _execute_list_users(db, user, arguments)
    if tool_name == "read_user_permissions":
        return _execute_read_user_permissions(db, user, arguments)
    if tool_name == "list_roles":
        return _execute_list_roles(db, user, arguments)
    raise AiActionValidationError(f"Kein Handler fuer Werkzeug: {tool_name}")
