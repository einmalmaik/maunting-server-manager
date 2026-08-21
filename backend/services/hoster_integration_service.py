"""Verwaltung angebundener Hoster: Integrationen, Produkte und Identitaeten.

Sicherheitsmodell
-----------------
- Der API-Key eines Hosters wird ausschliesslich als SHA-256-Hash gespeichert.
  Der Klartext wird genau einmal beim Anlegen bzw. Rotieren zurueckgegeben.
- Das Webhook-Secret liegt DIS-verschluesselt in der Datenbank. Es ueberlebt
  damit einen Panel-Neustart, ohne je im Klartext ausgelesen werden zu koennen.
- Ein externer Kunde wird ueber `(Integration, external_subject)` erkannt. Die
  E-Mail-Adresse allein verknuepft niemals einen bestehenden MSM-Account —
  sonst koennte ein Hoster durch Angabe einer fremden Adresse dessen Server
  uebernehmen.
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
import secrets

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import (
    HosterIdentity,
    HosterIntegration,
    HosterProduct,
    Role,
    User,
)
from models.hoster import hash_token
from services.auth_service import AuthService
from services.dis_client import DisClient
from services.permission_catalog import SYSTEM_ROLE_USER
from services.role_service import (
    get_role,
    get_role_by_name,
    role_permission_keys,
    set_user_roles,
)


API_KEY_HEADER = "X-MSM-Hoster-Key"
MAX_EXTERNAL_ID_LENGTH = 128
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_EXTERNAL_ID_RE = re.compile(r"^[A-Za-z0-9._:@-]+$")


class HosterConfigurationError(ValueError):
    """Die Hoster-Konfiguration ist ungueltig; wird am Rand zu einem 422."""


class HosterRoleEscalation(HosterConfigurationError):
    """Die Produktrolle traegt Rechte, die der Dienstbenutzer selbst nicht hat.

    Bewusst eine Unterklasse: der Admin-Router behandelt sie ohne Zutun weiter
    als 422, waehrend der Vertragspfad genau diesen Fall am Typ erkennt und mit
    einem eigenen, stabilen Fehlercode fehlschlaegt.
    """


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hint(secret: str) -> str:
    """Letzte vier Zeichen als Wiedererkennung. Nie der ganze Wert."""
    return f"...{secret[-4:]}" if len(secret) >= 4 else "****"


def _webhook_secret_aad(integration_id: int) -> str:
    return f"msm:hoster:integration:{integration_id}:webhook-secret"


def normalize_slug(value: str) -> str:
    """Prueft den technischen Kurznamen einer Integration."""
    slug = (value or "").strip().lower()
    if not slug or len(slug) > 64 or not _SLUG_RE.fullmatch(slug):
        raise HosterConfigurationError(
            "Slug darf nur Kleinbuchstaben, Ziffern und Bindestriche enthalten"
        )
    return slug


def normalize_external_id(value: str, *, label: str) -> str:
    """Validiert eine vom Shop gelieferte Kennung.

    Bewusst eng: die Kennung landet in eindeutigen Datenbankschluesseln und in
    Audit-Details. Steuerzeichen, Leerzeichen und ueberlange Werte werden
    abgewiesen, statt spaeter still gekuerzt zu werden.
    """
    cleaned = (value or "").strip()
    if (
        not cleaned
        or len(cleaned) > MAX_EXTERNAL_ID_LENGTH
        or not _EXTERNAL_ID_RE.fullmatch(cleaned)
    ):
        raise HosterConfigurationError(f"{label} ist leer, zu lang oder enthaelt ungueltige Zeichen")
    return cleaned


# ── Integrationen ──────────────────────────────────────────────────────────


def generate_api_key() -> str:
    """Erzeugt einen neuen Hoster-API-Key. Wird nur einmal ausgegeben."""
    return secrets.token_urlsafe(32)


def require_service_user(db: Session, service_user_id: int) -> User:
    """Prueft den Dienstbenutzer einer Integration.

    Der Benutzer muss aktiv sein und `servers.create` besitzen — sonst koennte
    eine Bestellung spaeter mitten im Ablauf an der Rechtepruefung scheitern und
    einen halb eingerichteten Vertrag hinterlassen. Ein Owner wird bewusst
    abgelehnt: ein Shop-Schluessel darf niemals Owner-Rechte tragen.
    """
    from services import permission_service

    user = db.query(User).filter(User.id == service_user_id, User.is_active.is_(True)).first()
    if user is None:
        raise HosterConfigurationError("Dienstbenutzer existiert nicht oder ist deaktiviert")
    if user.is_owner:
        raise HosterConfigurationError(
            "Der Owner-Account darf nicht als Dienstbenutzer verwendet werden"
        )
    if not permission_service.has_global_permission(db, user, "servers.create"):
        raise HosterConfigurationError("Dienstbenutzer benoetigt das Recht 'servers.create'")
    return user


def create_integration(
    db: Session,
    *,
    name: str,
    slug: str,
    enabled: bool,
    is_sandbox: bool = False,
    service_user_id: int,
    webhook_url: str | None,
    terminate_grace_days: int,
) -> tuple[HosterIntegration, str]:
    """Legt eine Integration an und gibt den einmaligen Klartext-API-Key zurueck."""
    clean_name = (name or "").strip()
    if not clean_name or len(clean_name) > 128:
        raise HosterConfigurationError("Name der Integration ist leer oder zu lang")
    if not 0 <= terminate_grace_days <= 365:
        raise HosterConfigurationError("Kuendigungsfrist muss zwischen 0 und 365 Tagen liegen")
    service_user = require_service_user(db, service_user_id)
    api_key = generate_api_key()
    integration = HosterIntegration(
        name=clean_name,
        slug=normalize_slug(slug),
        enabled=enabled,
        is_sandbox=is_sandbox,
        service_user_id=service_user.id,
        api_key_hash=hash_token(api_key),
        api_key_hint=_hint(api_key),
        webhook_url=validate_webhook_url(webhook_url),
        terminate_grace_days=terminate_grace_days,
    )
    db.add(integration)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HosterConfigurationError("Slug ist bereits vergeben") from exc
    return integration, api_key


def validate_webhook_url(value: str | None) -> str | None:
    """Erlaubt nur absolute HTTPS-Ziele ohne eingebettete Zugangsdaten."""
    url = (value or "").strip()
    if not url:
        return None
    if len(url) > 2048:
        raise HosterConfigurationError("Webhook-URL ist zu lang")
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise HosterConfigurationError("Webhook-URL muss eine absolute HTTPS-URL sein")
    if parsed.username or parsed.password:
        raise HosterConfigurationError("Webhook-URL darf keine Zugangsdaten enthalten")
    return url


def rotate_api_key(db: Session, integration: HosterIntegration) -> str:
    """Ersetzt den API-Key. Der alte Key ist danach sofort ungueltig."""
    api_key = generate_api_key()
    integration.api_key_hash = hash_token(api_key)
    integration.api_key_hint = _hint(api_key)
    integration.updated_at = _now()
    db.flush()
    return api_key


def set_webhook_secret(db: Session, integration: HosterIntegration) -> str:
    """Erzeugt ein neues Signatur-Secret und speichert es verschluesselt."""
    secret = secrets.token_urlsafe(32)
    integration.webhook_secret_encrypted = DisClient.encrypt(
        secret, aad=_webhook_secret_aad(integration.id)
    )
    integration.webhook_secret_hint = _hint(secret)
    integration.updated_at = _now()
    db.flush()
    return secret


def resolve_webhook_secret(integration: HosterIntegration) -> str | None:
    """Entschluesselt das Signatur-Secret ausschliesslich fuer den Versand."""
    if not integration.webhook_secret_encrypted:
        return None
    return DisClient.decrypt(
        integration.webhook_secret_encrypted, aad=_webhook_secret_aad(integration.id)
    )


def authenticate(db: Session, api_key: str | None) -> HosterIntegration:
    """Ordnet einen eingehenden API-Key genau einer aktiven Integration zu.

    Die Suche laeuft ueber den Hash, nicht ueber den Klartext. Damit gibt es
    weder einen Zeitunterschied zwischen "unbekannt" und "falsch" noch ein
    Klartext-Geheimnis in einem Index.
    """
    key = (api_key or "").strip()
    if not key or len(key) > 256:
        raise HTTPException(status_code=401, detail="Hoster-API-Key fehlt oder ist ungueltig")
    integration = (
        db.query(HosterIntegration)
        .filter(HosterIntegration.api_key_hash == hash_token(key))
        .first()
    )
    if integration is None or not integration.enabled:
        # Bewusst dieselbe Antwort fuer unbekannt und deaktiviert.
        raise HTTPException(status_code=401, detail="Hoster-API-Key fehlt oder ist ungueltig")
    return integration


# ── Produkte ───────────────────────────────────────────────────────────────


def ensure_role_is_delegatable(
    db: Session, *, integration: HosterIntegration, role_id: int | None
) -> Role | None:
    """Prueft, ob eine Integration die Produktrolle ueberhaupt vergeben darf.

    Eine Integration darf nie mehr vergeben, als ihr eigener Dienstbenutzer
    haelt. Ohne diese Schranke waere das Produktfeld ein Weg, sich ueber einen
    Shop-Kauf Rechte zu verschaffen: ein Produkt mit der Adminrolle wuerde jeden
    Kaeufer zum Admin machen, obwohl der Shop-Schluessel nur ein Vertragskonto
    ist. Inhaltlich dieselbe Regel wie `_ensure_no_global_escalation` in
    routers/admin.py — dort aber an den Router gebunden und als HTTPException.
    """
    if role_id is None:
        return None
    from services import permission_service

    role = get_role(db, role_id)
    if role is None:
        raise HosterConfigurationError("Zugeordnete Rolle existiert nicht")
    service_user = (
        db.query(User)
        .filter(User.id == integration.service_user_id, User.is_active.is_(True))
        .first()
    )
    if service_user is None:
        raise HosterConfigurationError(
            "Der Dienstbenutzer dieser Integration ist deaktiviert oder geloescht"
        )
    missing = sorted(
        key
        for key in role_permission_keys(db, role.id)
        if not permission_service.has_global_permission(db, service_user, key)
    )
    if missing:
        raise HosterRoleEscalation(
            "Der Dienstbenutzer dieser Integration besitzt die Rechte der "
            f"zugeordneten Rolle nicht selbst. Fehlend: {missing}"
        )
    return role


def ensure_actor_may_grant_role(db: Session, *, actor: User, role_id: int | None) -> None:
    """Verbietet, ueber ein Produkt eine Rolle zu hinterlegen, die der **Akteur**
    selbst nicht vergeben duerfte.

    `ensure_role_is_delegatable` beantwortet eine andere Frage: ob die
    *Integration* die Rolle vergeben darf. Sie prueft dazu gegen den
    Dienstbenutzer — und den waehlt genau der Akteur aus, der gerade schreibt.
    Als alleinige Schranke ist sie deshalb wertlos: wer `panel.hoster.write`
    hat, legt eine Integration mit einem privilegierten Dienstbenutzer an,
    haengt die `admin`-Rolle an ein Produkt, kauft mit dem frisch erhaltenen
    API-Key einen Vertrag und holt sich ueber einen Handoff eine Sitzung als der
    so erzeugte Admin-Kunde.

    Die drei Regeln sind woertlich die aus `_assign_roles` in routers/admin.py.
    Beide Pruefungen gelten zusammen: der Akteur muss die Rolle vergeben
    duerfen, *und* der Dienstbenutzer muss sie tragen.

    Sie steht hier im Dienst und nicht mehr nur im Router, weil es seit den
    KI-Werkzeugen einen **zweiten** Weg zu `upsert_product` gibt. Zwei Wege und
    eine Schranke, die nur an einem haengt, sind keine Schranke — der andere
    Weg waere schwaecher als der Panel-Knopf gewesen.
    """
    if role_id is None or actor.is_owner:
        return
    from services import permission_service
    from services.permission_catalog import SYSTEM_ROLE_ADMIN

    role = get_role(db, role_id)
    if role is None:
        raise HosterConfigurationError("Rolle nicht gefunden")
    if role.is_system and role.name == SYSTEM_ROLE_ADMIN:
        raise HosterRoleEscalation("Nur Owner kann die admin-Rolle zuweisen")
    missing = sorted(
        key
        for key in role_permission_keys(db, role.id)
        if not permission_service.has_global_permission(db, actor, key)
    )
    if missing:
        raise HosterRoleEscalation(
            "Du kannst nur Rollen zuordnen, deren Rechte du selbst besitzt. "
            f"Fehlend: {missing}"
        )


def upsert_product(
    db: Session,
    *,
    integration: HosterIntegration,
    external_product_key: str,
    game_type: str,
    ram_limit_mb: int | None,
    cpu_limit_percent: int | None,
    disk_limit_gb: int | None,
    node_id: int | None,
    backup_interval_hours: int | None,
    role_id: int | None,
    enabled: bool,
) -> HosterProduct:
    """Legt eine Produktzuordnung an oder aktualisiert sie.

    Der Blueprint wird sofort gegen die Registry geprueft. Ein Produkt, das auf
    einen unbekannten Blueprint zeigt, wuerde sonst erst bei der ersten echten
    Bestellung eines Kunden auffallen.

    Dasselbe gilt fuer die Rolle bei Buchung: sie wird geprueft, bevor die Zeile
    geschrieben wird. Eine gespeicherte, aber nicht delegierbare Rolle waere ein
    Produkt, das jede Bestellung erst mitten im Ablauf scheitern liesse.
    """
    from games import get_plugin

    key = normalize_external_id(external_product_key, label="Produktkennung")
    clean_game_type = (game_type or "").strip()
    if not clean_game_type or get_plugin(clean_game_type) is None:
        raise HosterConfigurationError("Unbekannter Blueprint bzw. Spieltyp")
    for value, label, maximum in (
        (ram_limit_mb, "RAM-Limit", 4_194_304),
        (cpu_limit_percent, "CPU-Limit", 10_000),
        (disk_limit_gb, "Disk-Limit", 1_048_576),
        (backup_interval_hours, "Backup-Intervall", 8_760),
    ):
        if value is not None and not 1 <= value <= maximum:
            raise HosterConfigurationError(f"{label} liegt ausserhalb des erlaubten Bereichs")
    if node_id is not None:
        from models import Node

        if db.query(Node).filter(Node.id == node_id).first() is None:
            raise HosterConfigurationError("Zugeordnete Node existiert nicht")
    ensure_role_is_delegatable(db, integration=integration, role_id=role_id)

    product = (
        db.query(HosterProduct)
        .filter(
            HosterProduct.integration_id == integration.id,
            HosterProduct.external_product_key == key,
        )
        .first()
    )
    if product is None:
        product = HosterProduct(integration_id=integration.id, external_product_key=key)
        db.add(product)
    product.game_type = clean_game_type
    product.ram_limit_mb = ram_limit_mb
    product.cpu_limit_percent = cpu_limit_percent
    product.disk_limit_gb = disk_limit_gb
    product.node_id = node_id
    product.backup_interval_hours = backup_interval_hours
    product.role_id = role_id
    product.enabled = enabled
    product.updated_at = _now()
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HosterConfigurationError("Produktkennung wurde parallel angelegt") from exc
    return product


def get_product(
    db: Session, integration: HosterIntegration, external_product_key: str
) -> HosterProduct:
    key = normalize_external_id(external_product_key, label="Produktkennung")
    product = (
        db.query(HosterProduct)
        .filter(
            HosterProduct.integration_id == integration.id,
            HosterProduct.external_product_key == key,
        )
        .first()
    )
    if product is None or not product.enabled:
        raise HosterConfigurationError("Produkt ist unbekannt oder deaktiviert")
    return product


# ── Externe Identitaeten ───────────────────────────────────────────────────


def _create_panel_user(db: Session, *, integration: HosterIntegration, email: str | None) -> User:
    """Legt einen technischen Panel-Benutzer fuer einen Hoster-Kunden an.

    Der Kunde bekommt kein zweites Passwort: er meldet sich ueber den Handoff
    oder ueber OIDC an. Das Passwortfeld wird deshalb mit einem zufaelligen,
    nirgends ausgegebenen Wert belegt — ein leeres Feld waere ein Loginpfad.
    """
    from services.oauth_service import _generate_unique_username

    username = _generate_unique_username(db, f"{integration.slug}-kunde")
    user = User(
        username=username,
        password_hash=AuthService.hash_password(secrets.token_urlsafe(32)),
        is_active=True,
        # Der Hoster hat den Kunden bereits authentifiziert. MSM verlangt
        # deshalb keine zweite E-Mail-Verifikation, bevor der Server nutzbar ist.
        email_verified=True,
    )
    # Die E-Mail wird nur uebernommen, wenn sie in MSM noch frei ist. Sonst
    # entstuende entweder ein Unique-Konflikt oder — schlimmer — der Eindruck,
    # ein bestehendes Konto sei uebernommen worden.
    if email:
        taken = db.query(User).filter(User.email_hash == User._email_hash(email)).first()
        if taken is None:
            user.email = email
    db.add(user)
    db.flush()
    default_role = get_role_by_name(db, SYSTEM_ROLE_USER)
    if default_role is not None:
        set_user_roles(db, user, [default_role.id])
    return user


def resolve_identity(
    db: Session,
    *,
    integration: HosterIntegration,
    external_subject: str,
    email: str | None = None,
) -> HosterIdentity:
    """Findet oder erzeugt die MSM-Identitaet zu einem externen Kunden.

    Anker ist ausschliesslich `(Integration, external_subject)`. Dadurch bleibt
    die Zuordnung stabil, wenn der Kunde seine E-Mail aendert, und zwei
    angebundene Hoster koennen dieselbe Adresse verwenden, ohne sich in die
    Quere zu kommen.
    """
    subject = normalize_external_id(external_subject, label="Kundenkennung")
    subject_hash = hash_token(f"{integration.id}:{subject}")
    identity = (
        db.query(HosterIdentity)
        .filter(
            HosterIdentity.integration_id == integration.id,
            HosterIdentity.external_subject_hash == subject_hash,
        )
        .first()
    )
    if identity is not None:
        identity.last_seen_at = _now()
        return identity

    user = _create_panel_user(db, integration=integration, email=email)
    identity = HosterIdentity(
        integration_id=integration.id,
        external_subject_hash=subject_hash,
        external_subject_hint=_hint(subject),
        user_id=user.id,
        last_seen_at=_now(),
    )
    db.add(identity)
    try:
        db.flush()
    except IntegrityError as exc:
        # Zwei parallele Bestellungen desselben Neukunden. Der Verlierer laedt
        # den Gewinner und verwendet dessen Benutzer weiter.
        db.rollback()
        winner = (
            db.query(HosterIdentity)
            .filter(
                HosterIdentity.integration_id == integration.id,
                HosterIdentity.external_subject_hash == subject_hash,
            )
            .first()
        )
        if winner is None:
            raise HosterConfigurationError("Kundenidentitaet konnte nicht angelegt werden") from exc
        return winner
    return identity
