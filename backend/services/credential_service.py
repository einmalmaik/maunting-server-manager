"""Zugangsdaten auf Panel-, Benutzer- und Serverebene.

Auflösungsreihenfolge für einen Server
--------------------------------------
1. **Server-Bindung** — das vom Betreiber oder Eigentümer ausdrücklich diesem
   Server zugewiesene Benutzer-Credential.
2. **Umgebungsvariable** — der bestehende ENV-Vorrang bleibt unverändert.
3. **Panel-Zugang** — der bisherige panelweite Wert, sofern der Betreiber den
   zentralen Fallback nicht abgeschaltet hat.

Warum die Bindung ganz oben steht: sie ist die spezifischste Aussage, die es
gibt. Stünde ENV davor, wäre eine bewusst gesetzte Kundenzuordnung im
Hoster-Betrieb wirkungslos. Ohne Bindung verhält sich alles exakt wie bisher —
ein Self-Hosted-Betrieb merkt von dieser Schicht nichts.

Sicherheitsgrenzen
------------------
- Geheimnisse liegen ausschließlich DIS-verschlüsselt mit objektgebundener AAD
  vor. Es gibt keinen Lesepfad, der Klartext an eine API-Antwort gibt.
- Ein Benutzer sieht und bindet nur seine eigenen Credentials. Wer fremde
  Server verwalten darf, kann deren Bindung setzen — aber nur auf Credentials,
  die ihm selbst gehören.
- Der zentrale Fallback ist eine Betreiberentscheidung. Ist er aus, läuft ein
  Server ohne Bindung bewusst in einen verständlichen Fehler statt still den
  Betreiberzugang zu verwenden.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Literal

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import (
    CREDENTIAL_KINDS,
    KIND_GITHUB_TOKEN,
    KIND_STEAM_ACCOUNT,
    ServerCredentialBinding,
    UserCredential,
    User,
)
from services.auth_service import AuthService
from services.dis_client import DisDecryptionError
from services.panel_settings_service import PanelSettingsService


PANEL_FALLBACK_SETTING = "credentials.allow_panel_fallback"
MAX_SECRET_LENGTH = 4096
# Untergrenze fuer ein Geheimnis. Sie steht hier nicht aus Passwortstrenge,
# sondern weil `_hint` darunter nichts mehr verdecken kann: bei genau vier
# Zeichen waeren "die letzten vier" der vollstaendige Klartext. Beide real
# vorgesehenen Arten liegen ohnehin darueber (Steam erzwingt acht Zeichen, ein
# GitHub-PAT ist deutlich laenger) — die Grenze trifft nur Fehleingaben.
MIN_SECRET_LENGTH = 8
MAX_USERNAME_LENGTH = 256
MAX_CREDENTIALS_PER_USER = 20
_LABEL_RE = re.compile(r"^[\w .\-]{1,64}$", re.UNICODE)

CredentialSource = Literal["server", "env", "panel"]


class CredentialError(ValueError):
    """Ungültige Credential-Eingabe; wird am API-Rand zu einem 422."""


@dataclass(frozen=True)
class ResolvedCredential:
    """Ein einsatzbereites Geheimnis samt Herkunft.

    `secret` ist Klartext und darf ausschließlich an den ausführenden Prozess
    (SteamCMD, git) weitergegeben werden — niemals in Logs, Antworten oder
    Audit-Details.
    """

    kind: str
    secret: str
    username: str | None
    source: CredentialSource


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aad(credential_id: int) -> str:
    return f"msm:credential:{credential_id}:secret"


def _hint(secret: str) -> str:
    """Ein Hinweis, der das Geheimnis nicht verraet.

    Die alte Bedingung `len(secret) >= 4` war genau falsch herum: der erste
    Fall, in dem die Maskierung greifen sollte, war der erste, in dem sie
    nichts mehr maskierte. Das wiegt hier schwerer als bei einer reinen
    Selbstauskunft, weil dieser Hinweis ueber die Serverbindung die
    Besitzergrenze verlaesst.

    Deshalb dieselbe Untergrenze wie im uebrigen Panel
    (`routers/panel_settings._mask_secret`): alles bis acht Zeichen wird
    vollstaendig maskiert. Die Maske ist bewusst laengenunabhaengig, damit sie
    nicht ihrerseits verraet, wie kurz das Geheimnis ist.
    """
    if len(secret) <= MIN_SECRET_LENGTH:
        return "****"
    return f"...{secret[-4:]}"


def panel_fallback_allowed() -> bool:
    """Darf ein Server ohne eigene Bindung den panelweiten Zugang verwenden?

    Default `true`: bestehende Self-Hosted-Installationen verhalten sich nach
    dem Update unverändert. Ein Hoster schaltet das bewusst ab.
    """
    return PanelSettingsService.get(PANEL_FALLBACK_SETTING, "true") != "false"


def set_panel_fallback_allowed(allowed: bool) -> None:
    PanelSettingsService.set(PANEL_FALLBACK_SETTING, "true" if allowed else "false")


def _validate_kind(kind: str) -> str:
    if kind not in CREDENTIAL_KINDS:
        raise CredentialError("Unbekannte Credential-Art")
    return kind


def _validate_label(label: str) -> str:
    value = (label or "").strip()
    if not value or not _LABEL_RE.fullmatch(value):
        raise CredentialError(
            "Bezeichnung darf nur Buchstaben, Ziffern, Leerzeichen, Punkt und Bindestrich enthalten"
        )
    return value


# ── Benutzer-Tresor ────────────────────────────────────────────────────────


def list_user_credentials(
    db: Session, user_id: int, kind: str | None = None
) -> list[UserCredential]:
    query = db.query(UserCredential).filter(UserCredential.user_id == user_id)
    if kind is not None:
        query = query.filter(UserCredential.kind == _validate_kind(kind))
    return query.order_by(UserCredential.kind, UserCredential.label).all()


def upsert_user_credential(
    db: Session,
    *,
    user_id: int,
    kind: str,
    label: str,
    secret: str,
    username: str | None = None,
) -> UserCredential:
    """Legt ein Credential an oder ersetzt sein Geheimnis.

    Ein vorhandener Eintrag mit derselben Bezeichnung wird überschrieben — das
    ist der Rotationsfall. Die ID bleibt dabei erhalten, damit bestehende
    Server-Bindungen weiter gelten.
    """
    kind = _validate_kind(kind)
    label = _validate_label(label)
    value = (secret or "").strip()
    if len(value) > MAX_SECRET_LENGTH:
        raise CredentialError("Geheimnis ist zu lang")
    if len(value) < MIN_SECRET_LENGTH:
        # Bewusst erst nach dem strip() geprueft: acht Leerzeichen kaemen sonst
        # durch jede Laengenpruefung im Schema und landeten als leeres
        # Geheimnis in der Datenbank.
        raise CredentialError("Geheimnis muss mindestens acht Zeichen haben")
    name = (username or "").strip() or None
    if kind == KIND_STEAM_ACCOUNT:
        if not name:
            raise CredentialError("Für einen Steam-Account wird ein Benutzername benötigt")
        if len(name) > MAX_USERNAME_LENGTH:
            raise CredentialError("Benutzername ist zu lang")
    else:
        # Ein GitHub-PAT hat keinen Benutzernamen; ein mitgeschickter Wert wäre
        # nur eine stille Fehlbedienung.
        name = None

    existing = (
        db.query(UserCredential)
        .filter(
            UserCredential.user_id == user_id,
            UserCredential.kind == kind,
            UserCredential.label == label,
        )
        .first()
    )
    if existing is None:
        count = db.query(UserCredential).filter(UserCredential.user_id == user_id).count()
        if count >= MAX_CREDENTIALS_PER_USER:
            raise CredentialError("Es sind bereits zu viele Zugangsdaten hinterlegt")
        credential = UserCredential(
            user_id=user_id, kind=kind, label=label, username=name, secret_encrypted=""
        )
        db.add(credential)
        # Die AAD bindet den Ciphertext an genau diese Zeile, deshalb wird die
        # ID vor dem Verschlüsseln benötigt.
        db.flush()
    else:
        credential = existing
        credential.username = name

    credential.secret_encrypted = AuthService.encrypt_secret(value, aad=_aad(credential.id))
    credential.secret_hint = _hint(value)
    credential.updated_at = _now()
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise CredentialError("Bezeichnung wurde parallel vergeben") from exc
    return credential


def delete_user_credential(db: Session, *, user_id: int, credential_id: int) -> None:
    """Löscht ein eigenes Credential, sofern kein Server es noch verwendet."""
    credential = (
        db.query(UserCredential)
        .filter(UserCredential.id == credential_id, UserCredential.user_id == user_id)
        .first()
    )
    if credential is None:
        raise HTTPException(status_code=404, detail="Zugangsdaten nicht gefunden")
    bound = (
        db.query(ServerCredentialBinding)
        .filter(ServerCredentialBinding.credential_id == credential.id)
        .count()
    )
    if bound:
        # Stilles Löschen würde die betroffenen Server beim nächsten Install
        # unbemerkt auf den Panel-Zugang zurückfallen lassen.
        raise HTTPException(
            status_code=409,
            detail="Zugangsdaten werden noch von einem Server verwendet",
        )
    db.delete(credential)
    db.flush()


# ── Server-Bindung ─────────────────────────────────────────────────────────


def get_binding(db: Session, server_id: int, kind: str) -> ServerCredentialBinding | None:
    return (
        db.query(ServerCredentialBinding)
        .filter(
            ServerCredentialBinding.server_id == server_id,
            ServerCredentialBinding.kind == _validate_kind(kind),
        )
        .first()
    )


def set_binding(
    db: Session,
    *,
    server_id: int,
    kind: str,
    credential_id: int | None,
    actor: User,
) -> ServerCredentialBinding | None:
    """Bindet ein eigenes Credential an einen Server oder löst die Bindung.

    `credential_id=None` entfernt die Bindung; der Server fällt dann wieder auf
    die Panel-Ebene zurück, sofern der Betreiber das erlaubt.

    Die Rechteprüfung auf den Server liegt beim Aufrufer. Hier wird die zweite
    Hälfte durchgesetzt: gebunden werden darf nur ein Credential, das dem
    Handelnden selbst gehört — sonst könnte jemand mit Serverrechten fremde
    Zugangsdaten in Betrieb nehmen.
    """
    kind = _validate_kind(kind)
    existing = get_binding(db, server_id, kind)
    if credential_id is None:
        if existing is not None:
            db.delete(existing)
            db.flush()
        return None

    credential = (
        db.query(UserCredential)
        .filter(UserCredential.id == credential_id, UserCredential.user_id == actor.id)
        .first()
    )
    if credential is None:
        raise HTTPException(status_code=404, detail="Zugangsdaten nicht gefunden")
    if credential.kind != kind:
        raise CredentialError("Zugangsdaten passen nicht zur angeforderten Art")

    if existing is None:
        existing = ServerCredentialBinding(
            server_id=server_id, kind=kind, credential_id=credential.id
        )
        db.add(existing)
    else:
        existing.credential_id = credential.id
        existing.updated_at = _now()
    db.flush()
    return existing


# ── Auflösung für den Ausführungspfad ──────────────────────────────────────


def _from_binding(db: Session, server_id: int, kind: str) -> ResolvedCredential | None:
    binding = get_binding(db, server_id, kind)
    if binding is None:
        return None
    credential = binding.credential
    if credential is None:
        return None
    try:
        secret = AuthService.decrypt_secret(
            credential.secret_encrypted, aad=_aad(credential.id)
        )
    except DisDecryptionError as exc:
        # Bewusst kein stiller Rückfall auf den Panel-Zugang: der Server würde
        # sonst unbemerkt mit fremden Zugangsdaten laufen.
        raise HTTPException(
            status_code=503,
            detail=(
                "Die diesem Server zugewiesenen Zugangsdaten können nicht entschlüsselt "
                "werden. Bitte neu hinterlegen."
            ),
        ) from exc
    return ResolvedCredential(
        kind=kind, secret=secret, username=credential.username, source="server"
    )


def resolve_for_server(db: Session, server_id: int, kind: str) -> ResolvedCredential | None:
    """Liefert das für diesen Server gültige Geheimnis oder ``None``."""
    kind = _validate_kind(kind)
    bound = _from_binding(db, server_id, kind)
    if bound is not None:
        return bound
    return resolve_panel_default(kind)


def resolve_panel_default(kind: str) -> ResolvedCredential | None:
    """Der bisherige panelweite Zugang (ENV vor Panel-Einstellung)."""
    kind = _validate_kind(kind)
    if not panel_fallback_allowed():
        return None
    if kind == KIND_GITHUB_TOKEN:
        from services.github_token_service import current_source, resolve_token

        token = resolve_token()
        if not token:
            return None
        source: CredentialSource = "env" if current_source() == "env" else "panel"
        return ResolvedCredential(kind=kind, secret=token, username=None, source=source)

    from services.steam_account_service import SteamAccountService

    if not SteamAccountService.is_configured():
        return None
    try:
        password = SteamAccountService.get_decrypted_password()
    except RuntimeError:
        # Der Account ist hinterlegt, aber nicht mehr entschluesselbar (typisch
        # nach einer SECRET_KEY-Rotation). Das ist fachlich "kein verwendbarer
        # Zugang": der Aufrufer meldet das dem Benutzer, statt hier mit einem
        # rohen Fehler aus einem Installations-Thread zu fliegen.
        import logging

        logging.getLogger(__name__).warning(
            "Panelweiter Steam-Account ist nicht entschluesselbar und wird ignoriert"
        )
        return None
    return ResolvedCredential(
        kind=kind,
        secret=password,
        username=SteamAccountService.get_username(),
        source="panel",
    )


def describe_for_server(
    db: Session,
    server_id: int,
    kind: str,
    *,
    viewer_id: int | None = None,
    viewer_may_manage: bool = False,
) -> dict:
    """Statusauskunft für die Serveroberfläche — ohne fremde Kontodaten.

    "Secret-frei" war als Zusage zu schwach. Bezeichnung, Login-Name und der
    Hinweis auf die letzten Zeichen gehoeren dem Benutzer, dessen Credential
    gebunden ist, und das ist im Hoster-Betrieb regelmaessig ein anderer als
    der Betrachter: binden darf nur, wer ``server.credentials.manage`` haelt,
    lesen schon, wer ``server.view`` haelt. Ein Kunde bekam so den Login-Namen
    und vier Zeichen des Passworts eines fremden Kontos zu sehen.

    Darum zwei Ebenen: **dass** und **woher** ein Zugang kommt, sieht jeder
    Leser — sonst koennte er einen fehlenden Zugang nicht von einem gesetzten
    unterscheiden und wuesste bei einem gescheiterten Install nicht, woran es
    liegt. **Welcher** Zugang es ist, sieht nur, wem er gehoert oder wer ihn
    ohnehin umsetzen darf.

    Die Vorgaben sind absichtlich die restriktiven: ein Aufrufer, der die
    Frage nicht beantwortet, bekommt die knappe Auskunft.

    Der panelweite Benutzername faellt ganz heraus. Er ist eine
    Betreibereinstellung und liegt sonst hinter ``panel.settings.read``
    (routers/panel_settings.py) — ueber diese Route war er fuer jeden mit
    Leserecht auf irgendeinen Server abrufbar.
    """
    kind = _validate_kind(kind)
    binding = get_binding(db, server_id, kind)
    if binding is not None and binding.credential is not None:
        credential = binding.credential
        may_identify = viewer_may_manage or (
            viewer_id is not None and credential.user_id == viewer_id
        )
        return {
            "kind": kind,
            "source": "server",
            "configured": True,
            "credential_id": credential.id if may_identify else None,
            "label": credential.label if may_identify else None,
            "username": credential.username if may_identify else None,
            "hint": credential.secret_hint if may_identify else None,
        }
    panel = resolve_panel_default(kind)
    return {
        "kind": kind,
        "source": panel.source if panel else "none",
        "configured": panel is not None,
        "credential_id": None,
        "label": None,
        "username": None,
        "hint": None,
    }


def required_kinds_for_server(db: Session, server_id: int) -> list[str]:
    """Welche Zugangsdaten dieser Server laut seinem Blueprint überhaupt braucht.

    Ohne diese Auskunft müsste die Oberfläche jedem Server alle Arten anbieten —
    und der Benutzer wüsste nicht, was er tatsächlich hinterlegen muss.
    """
    from blueprints.schema import BlueprintSourceType
    from games import get_plugin
    from models import Server

    server = db.query(Server).filter(Server.id == server_id).first()
    if server is None:
        return []
    plugin = get_plugin(server.game_type)
    blueprint = plugin.get_blueprint() if plugin else None
    if blueprint is None:
        return []
    source = blueprint.source
    if source.type == BlueprintSourceType.GITHUB:
        return [KIND_GITHUB_TOKEN]
    if source.type == BlueprintSourceType.STEAM and getattr(
        source.steam, "requiresLogin", False
    ):
        return [KIND_STEAM_ACCOUNT]
    return []
