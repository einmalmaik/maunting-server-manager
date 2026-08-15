"""Die panelseitige Shop-Anbindung, lesbar fuer das Modell.

Zwei Werkzeuge, zwei verschiedene Aufgaben.

`read_hoster_setup` ist die **Bestandsaufnahme**: welche Integrationen es gibt,
welche Slugs vergeben sind, welche Benutzer als Dienstbenutzer taugen, welche
Rollen der Akteur vergeben darf und welche Produkte schon zugeordnet sind. Ohne
sie muesste das Modell die Argumente seiner Vorschlaege raten — und Raten ist
genau das, was hier nicht passieren darf.

Sie liefert bewusst **alles in einem Aufruf**. Werkzeugergebnisse fliessen nur
aus dem juengsten Lauf in den Folgekontext zurueck; eine Einrichtung ueber
mehrere Nachrichten haenge sonst am Kurzzeitgedaechtnis eines einzelnen Zuges.
Jeder Schritt muss aus dem beobachtbaren Panelzustand wieder auffindbar sein.

`hoster_integration_guide` ist der **woertliche Einbindungsblock** fuer den
Shop-Entwickler. Er ist keine zweite Dokumentation: jeder Wert darin kommt aus
demselben Code, den die API durchsetzt — die Pfade aus den Routern, die
Zustaende aus `hoster_service_lifecycle`, die Headernamen aus
`hoster_integration_service` und `hoster_webhook_service`, die Produktkennungen
aus der Datenbank. Wo es nichts aus Code zu holen gibt (die Bedeutung der
`status_code`-Werte), zeigt der Block auf den Abschnitt der Doku, statt eine
Liste zu erfinden.

**Keine Geheimnisse.** Weder `api_key_hash` noch `webhook_secret_encrypted`
noch ein entschluesselter Wert verlassen dieses Modul. Was hier
zurueckkommt, landet in `ai_tool_results`, in `ai_runs.state_json` und in der
Anfrage an den Modellanbieter — drei Orte im Klartext. Der Hinweis
(`api_key_hint`, Form `...6qS`) reicht, um zu erkennen, *welcher* Schluessel
gemeint ist, und genuegt nicht, um ihn zu benutzen.
"""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from models import HosterIntegration, HosterProduct, HosterService, Role, User
from services import permission_service, role_service
from services.ai_action_errors import AiActionValidationError

MAX_INTEGRATIONEN = 25
MAX_PRODUKTE = 60
MAX_DIENSTBENUTZER = 40
MAX_ROLLEN = 40


def _rollen_fuer_akteur(db: Session, akteur: User) -> list[dict]:
    """Nur die Rollen, die dieser Mensch selbst vergeben duerfte.

    Dieselben drei Regeln wie `_ensure_actor_may_grant_role` im Router: Rolle
    existiert, `admin` nur fuer den Owner, jeder Rechteschluessel der Rolle muss
    beim Akteur selbst vorhanden sein. Die Liste hier ist kein Ersatz fuer die
    Pruefung beim Schreiben — sie sorgt nur dafuer, dass das Modell nicht
    vorschlaegt, was ohnehin abgewiesen wuerde.
    """
    from services.permission_catalog import SYSTEM_ROLE_ADMIN

    # Die eigenen Rechte des Akteurs einmal holen statt zweimal je geprüftem
    # Schlüssel. `has_global_permission` fragt für einen Nicht-Owner genau
    # diese Menge ab, nur jedes Mal neu.
    akteur_keys = (
        set()
        if akteur.is_owner
        else set(role_service.effective_user_role_permission_keys(db, akteur))
    )

    eintraege: list[dict] = []
    for rolle in role_service.list_roles(db)[:MAX_ROLLEN]:
        if rolle.is_system and rolle.name == SYSTEM_ROLE_ADMIN and not akteur.is_owner:
            continue
        keys = role_service.role_permission_keys(db, rolle.id)
        if not akteur.is_owner and not set(keys) <= akteur_keys:
            continue
        eintraege.append({
            "role_id": rolle.id,
            "name": rolle.name,
            "is_system": bool(rolle.is_system),
            "permission_count": len(keys),
            "ai_limits": _ai_limits(db, rolle),
        })
    return eintraege


def _ai_limits(db: Session, rolle: Role) -> dict | None:
    """Das KI-Kontingent einer Rolle — der Grund, warum ein Produkt ueberhaupt
    eine Rolle traegt."""
    from services import ai_limit_service

    row = ai_limit_service.get_role_limit(db, rolle.id)
    if row is None:
        # Ausdruecklich `None` statt Nullen: bei den Kontingenten heisst keine
        # Konfiguration in `resolve_effective_limits` **unbegrenzt**, nicht
        # "null Tokens".
        return None
    # Ein Feld dieser Liste liest sich anders als seine Nachbarn, und das Modell
    # sieht es ihm nicht an: bei `max_memory_entries` heisst `null` nicht
    # unbegrenzt, sondern "nichts hinterlegt" — durchgesetzt wird dann
    # `MAX_SYSTEM_SCOPE_ENTRIES`, siehe `resolve_scope_memory_limit`. Fuer den
    # Fall darueber gilt dasselbe: auch die Rolle ganz ohne Zeile hat beim
    # Vorrat keine offene Grenze, sondern dieselbe Systemgrenze.
    #
    # Das ist hier keine Feinheit, sondern der Lesepfad, ueber den das Modell
    # bestehende Tarifrollen ueberhaupt erst kennenlernt: auf "Was enthaelt der
    # Enterprise-Tarif?" antwortete es nach der Lesart der Nachbarfelder
    # "Gedaechtnis: unbegrenzt", und der Betreiber verkaufte danach genau das —
    # waehrend der Kunde denselben Vorrat bekommt wie im Gratistarif. Der
    # Vorbehalt steht deshalb in der Werkzeugbeschreibung von
    # `read_hoster_setup` (`ai_action_service`), woertlich wie beim
    # Schreib-Werkzeug `propose_ai_tarif_role`. Kaeme ein weiteres Feld dieser
    # Art dazu, waere hier wieder nichts zu aendern und dort schon.
    return {feld: getattr(row, feld) for feld in ai_limit_service.LIMIT_FIELDS}


def _dienstbenutzer(db: Session) -> list[dict]:
    """Benutzer, die `require_service_user` durchlassen wuerde.

    Die drei Bedingungen stehen dort und werden hier gespiegelt: aktiv, kein
    Owner, `servers.create`. Ein Modell, das den Dienstbenutzer raet, baut eine
    Integration, die bei der ersten Bestellung scheitert.
    """
    kandidaten = (
        db.query(User)
        .filter(User.is_active.is_(True), User.is_owner.is_(False))
        .order_by(User.id)
        .limit(200)
        .all()
    )
    # Mengenweise fragen, nicht je Kandidat: einzeln waren das 1 + 2n
    # Abfragen, also 401 an der Obergrenze von 200.
    berechtigt = permission_service.benutzer_mit_recht(db, kandidaten, "servers.create")
    treffer = [
        {"user_id": u.id, "username": u.username}
        for u in kandidaten
        if u.id in berechtigt
    ]
    return treffer[:MAX_DIENSTBENUTZER]


def _produkte(db: Session, integration_id: int) -> list[dict]:
    rows = (
        db.query(HosterProduct)
        .filter(HosterProduct.integration_id == integration_id)
        .order_by(HosterProduct.external_product_key)
        .limit(MAX_PRODUKTE)
        .all()
    )
    return [{
        "external_product_key": p.external_product_key,
        "game_type": p.game_type,
        "ram_limit_mb": p.ram_limit_mb,
        "cpu_limit_percent": p.cpu_limit_percent,
        "disk_limit_gb": p.disk_limit_gb,
        "node_id": p.node_id,
        "backup_interval_hours": p.backup_interval_hours,
        "role_id": p.role_id,
        "enabled": bool(p.enabled),
    } for p in rows]


def setup_uebersicht(db: Session, *, user: User) -> dict:
    """Der ganze panelseitige Zustand der Shop-Anbindung in einem Aufruf."""
    if not permission_service.has_global_permission(db, user, "panel.hoster.read"):
        raise AiActionValidationError(
            "Einsicht in die Hoster-Anbindung ist fuer diesen Benutzer nicht erlaubt"
        )

    integrationen = (
        db.query(HosterIntegration)
        .order_by(HosterIntegration.id)
        .limit(MAX_INTEGRATIONEN)
        .all()
    )
    namen = {
        u.id: u.username
        for u in db.query(User).filter(
            User.id.in_([i.service_user_id for i in integrationen] or [0])
        ).all()
    }

    # Zwei Zahlen je Integration, in SQL gezählt. Die Vertragstabelle wächst
    # mit dem Geschaeft — sie war die einzige ungedeckelte Abfrage dieser
    # Funktion, und ihr ganzes Ergebnis diente nur diesen beiden Summen.
    integration_ids = [i.id for i in integrationen] or [0]
    vertraege_gesamt = dict(
        db.query(HosterService.integration_id, func.count())
        .filter(HosterService.integration_id.in_(integration_ids))
        .group_by(HosterService.integration_id)
        .all()
    )
    vertraege_aktiv = dict(
        db.query(HosterService.integration_id, func.count())
        .filter(
            HosterService.integration_id.in_(integration_ids),
            HosterService.desired_state == "active",
            HosterService.status.in_(("provisioning", "ready")),
        )
        .group_by(HosterService.integration_id)
        .all()
    )

    eintraege = []
    for i in integrationen:
        eintraege.append({
            "integration_id": i.id,
            "name": i.name,
            "slug": i.slug,
            "enabled": bool(i.enabled),
            "service_user_id": i.service_user_id,
            # Mit Namen, nicht nur mit ID: eine Zahl kann das Modell dem
            # Benutzer nicht erklaeren, und der Mensch bestaetigt am Ende
            # anhand dessen, was er liest.
            "service_user": namen.get(i.service_user_id),
            "webhook_url": i.webhook_url,
            "webhook_secret_configured": bool(i.webhook_secret_encrypted),
            "terminate_grace_days": i.terminate_grace_days,
            # Nur der Hinweis. Der Schluessel selbst existiert im Panel nur als
            # Hash und wird beim Anlegen genau einmal angezeigt.
            "api_key_hint": i.api_key_hint,
            "products": _produkte(db, i.id),
            "contract_count": vertraege_gesamt.get(i.id, 0),
            "active_contract_count": vertraege_aktiv.get(i.id, 0),
        })

    ergebnis: dict = {
        "integrations": eintraege,
        "count": len(eintraege),
        "used_slugs": sorted(i.slug for i in integrationen),
    }

    # Rollen- und Benutzerliste haengen an anderen Rechten. Fehlt eines,
    # bleibt das Feld nicht einfach weg: "withheld" sagt dem Modell, dass es
    # eine Liste gibt, die es nicht sehen darf — sonst haelt es die Abwesenheit
    # fuer "es gibt keine Rollen" und schlaegt vor, welche anzulegen.
    if permission_service.has_global_permission(db, user, "users.read"):
        ergebnis["service_user_candidates"] = _dienstbenutzer(db)
    else:
        ergebnis["service_user_candidates"] = "withheld"

    if permission_service.has_global_permission(db, user, "roles.manage"):
        ergebnis["grantable_roles"] = _rollen_fuer_akteur(db, user)
    else:
        ergebnis["grantable_roles"] = "withheld"

    return ergebnis


# ── Der woertliche Einbindungsblock ──────────────────────────────────────

def _endpunkte() -> list[str]:
    """Methode und Pfad aus den Routern selbst.

    Dieselbe Technik wie `test_hoster_api_docs_contract._routes`. Eine
    abgeschriebene Liste waere genau die zweite Doku, die niemand pflegen will —
    und die erste, die falsch wird.
    """
    from routers import hoster_api

    gefunden: set[tuple[str, str]] = set()
    for router in (hoster_api.router, hoster_api.redeem_router):
        for route in router.routes:
            pfad = getattr(route, "path", None)
            methoden = getattr(route, "methods", None)
            if not pfad or not methoden:
                continue
            for methode in methoden:
                if methode in {"HEAD", "OPTIONS"}:
                    continue
                gefunden.add((methode, pfad))
    return [f"{m} {p}" for m, p in sorted(gefunden, key=lambda x: (x[1], x[0]))]


def integration_guide(db: Session, *, user: User, integration_id: int) -> dict:
    """Baut den Block, den der Betreiber unveraendert in seinen Shop traegt."""
    from config import settings
    from services import hoster_integration_service, hoster_webhook_service
    from services.hoster_service_lifecycle import DESIRED_STATES, SERVICE_STATUSES

    if not permission_service.has_global_permission(db, user, "panel.hoster.read"):
        raise AiActionValidationError(
            "Einsicht in die Hoster-Anbindung ist fuer diesen Benutzer nicht erlaubt"
        )
    integration = (
        db.query(HosterIntegration).filter(HosterIntegration.id == integration_id).first()
    )
    if integration is None:
        raise AiActionValidationError(f"Integration {integration_id} gibt es nicht")

    basis = (settings.panel_url or "").rstrip("/")
    produkte = [p["external_product_key"] for p in _produkte(db, integration.id)]

    return {
        "untrusted": True,
        # Die Marke ist keine Kosmetik: die Werkzeugbeschreibung verpflichtet
        # das Modell, diesen Block unveraendert weiterzugeben. Umformuliert
        # waere er wieder Modellprosa und damit genau das, was er ersetzt.
        "verbatim": True,
        "integration": {
            "name": integration.name,
            "slug": integration.slug,
            "enabled": bool(integration.enabled),
            "terminate_grace_days": integration.terminate_grace_days,
            "webhook_url": integration.webhook_url,
            "webhook_secret_configured": bool(integration.webhook_secret_encrypted),
        },
        "base_url": basis or None,
        "auth_header": hoster_integration_service.API_KEY_HEADER,
        "endpoints": _endpunkte(),
        "desired_states": sorted(DESIRED_STATES),
        "service_statuses": list(SERVICE_STATUSES),
        "webhook_events": [f"service.{status}" for status in SERVICE_STATUSES],
        "webhook_headers": {
            "signature": hoster_webhook_service.SIGNATURE_HEADER,
            "timestamp": hoster_webhook_service.TIMESTAMP_HEADER,
            "event": hoster_webhook_service.EVENT_HEADER,
        },
        "webhook_retries": {
            "max_attempts": hoster_webhook_service.MAX_ATTEMPTS,
            "backoff_seconds": list(hoster_webhook_service.RETRY_BACKOFF_SECONDS),
        },
        "product_keys": produkte,
        # Die Bedeutung der `status_code`-Werte steht als Tabelle in der Doku
        # und in keiner Konstante. Der Block zeigt darauf, statt eine Liste zu
        # erfinden, die morgen unvollstaendig ist.
        "status_codes_documented_at": {
            "page": "hoster-api",
            "section": "fehler-und-statuscodes",
        },
        "notes": [
            "Der API-Key gehoert in den Header, nie in die URL.",
            "Ein Vertrag wird ueber die Produktkennung des Shops angelegt; "
            "MSM-interne IDs muss der Shop nie kennen.",
            "Ohne Webhook-Ziel **und** Webhook-Secret stellt MSM nichts zu.",
        ],
    }
