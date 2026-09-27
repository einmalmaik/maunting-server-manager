"""Alles, was das Panel zu einem Konto gespeichert hat, als ein Paket.

Welche Zeilen dazugehoeren, steht in keiner Liste, sondern im Schema selbst:

1. Jede Tabelle mit Fremdschluessel auf ``users.id`` liefert die Zeilen, in
   denen einer dieser Schluessel auf das Konto zeigt.
2. Eine Tabelle **ohne** eigenen Fremdschluessel auf ``users`` liefert die
   Zeilen, die auf eine schon exportierte Zeile zeigen (``ai_messages`` ueber
   ``ai_conversations``). Eine Tabelle mit eigenem Benutzerbezug wird nur ueber
   diesen gewaehlt; so kommen Team-Notizen anderer Mitglieder nie mit, nur weil
   das Team dem Konto gehoert.

Eine neue Tabelle mit Fremdschluessel ist damit ohne Zutun dabei. Was davon
nicht erfasst wird, steht in ``OHNE_KONTOBEZUG``; ``test_datenexport_abdeckung``
wird rot, sobald eine Tabelle in keine der beiden Gruppen faellt oder eine
Spalte nach Geheimnis aussieht und nirgends eingeordnet ist.

Verschluesselt: ``DisText`` erkennt der Dienst am Typ, Spalten mit
zeilengebundener AAD stehen in ``ZEILEN_AAD``. Beides wird gebuendelt
entschluesselt. Was nur ein Geraet oeffnen kann (``sv-note-v1:``, Messenger,
Tresor), bleibt hier Chiffrat; den Klartext legt der Client dazu.
"""

from __future__ import annotations

import base64
import logging
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy import Table, Text, or_, select, type_coerce
from sqlalchemy.orm import Session

from database import Base
from models.dis_text import DisText, gebuendelt_entschluesseln

logger = logging.getLogger(__name__)

FORMAT = "msm-datenexport-v1"

#: Tabellen, die nie exportiert werden, mit Grund. Ohne Benutzerbezug oder
#: bewusst blind (E2EE ohne Kontozuordnung), oder Panelbetrieb, bei dem das
#: Konto nur Dienstkonto ist.
OHNE_KONTOBEZUG: dict[str, str] = {
    "ai_providers": "Panel-Einstellung",
    "backups": "Server-Daten",
    "change_events": "Server-Daten",
    "direct_chats": "blind, dem Server ist kein Konto bekannt",
    "e2ee_blind_envelopes": "blind, dem Server ist kein Konto bekannt",
    "e2ee_blind_mailboxes": "blind, dem Server ist kein Konto bekannt",
    "e2ee_mailbox_push": "blind, dem Server ist kein Konto bekannt",
    "email_verifications": "kurzlebiger Bestaetigungscode, nur als Hash",
    "guardian_incident_deliveries": "Server-Daten",
    "hoster_integrations": "Panel-Integration, das Konto ist nur ihr Dienstkonto",
    "hoster_products": "Panel-Integration",
    "incidents": "Server-Daten",
    "mods": "Server-Daten",
    "node_enrollments": "Panel-Betrieb",
    "nodes": "Panel-Betrieb",
    "oauth_providers": "Panel-Einstellung",
    "panel_backups": "Panel-Betrieb",
    "panel_settings": "Panel-Einstellung",
    "postgres_databases": "Server-Daten",
    "postgres_grants": "Server-Daten",
    "postgres_instances": "Server-Daten",
    "postgres_users": "Server-Daten",
    "role_ai_limits": "Panel-Einstellung",
    "role_permissions": "Panel-Einstellung",
    "roles": "Panel-Einstellung",
    "server_ports": "Server-Daten",
    "servers": "Server-Daten",
    "singra_webhook_events": "Panel-Betrieb",
    "vault_blind_buckets": "blind, der Tresor liegt nur verschluesselt vor",
    "vault_entries": "blind, der Tresor liegt nur verschluesselt vor",
    "webhook_deliveries": "Server-Daten",
    "webhook_subscriptions": "Server-Daten",
}

#: Spalten, die nicht mitgehen. Die Gruende stehen im Paket.
AUSGESCHLOSSEN: dict[str, str] = {
    "users.email": "Hash der E-Mail-Adresse (die Adresse steht in email_encrypted)",
    "users.email_hash": "Hash der E-Mail-Adresse",
    "users.password_hash": "Passwort-Hash",
    "users.two_factor_secret_encrypted": "2FA-Geheimnis",
    "users.password_reset_token": "Hash eines Ruecksetzlinks",
    "backup_codes.code_hash": "Hash eines Backup-Codes",
    "refresh_tokens.token_hash": "Hash eines Sitzungstokens",
    "refresh_tokens.family": "Sitzungskennung",
    "jwt_blacklist.jti": "Kennung eines gesperrten Tokens",
    "login_challenges.token_hash": "Hash eines Anmeldeschritts",
    "login_challenges.payload_json": "Interner Zustand eines Anmeldeschritts",
    "device_pairings.code_hash": "Hash eines Kopplungscodes",
    "device_pairings.family": "Sitzungskennung",
    "device_pairings.verlauf_blob": "Fuer ein Geraet versiegelter Verlauf",
    "push_subscriptions.endpoint": "Zustelladresse des Push-Dienstes",
    "push_subscriptions.p256dh": "Schluessel des Push-Dienstes",
    "push_subscriptions.auth": "Schluessel des Push-Dienstes",
    "push_subscriptions.auth_family": "Sitzungskennung",
    "user_e2ee_devices.auth_family": "Sitzungskennung",
    "user_passkeys.credential_id": "Schluesselmaterial des Passkeys",
    "user_passkeys.public_key": "Schluesselmaterial des Passkeys",
    "operation_tasks.idempotency_key_hash": "Interner Hash",
    "operation_tasks.request_hash": "Interner Hash",
    "ai_action_proposals.confirmation_token_hash": "Hash eines Bestaetigungstokens",
    "ai_action_approvals.token_hash": "Hash eines Bestaetigungstokens",
    "ai_memory_entries.key_index": "Suchindex (HMAC) zum Namen",
    "ai_memory_entries.embedding_bytes": "Suchvektor, aus dem Eintrag berechnet",
    "ai_skills.embedding_json": "Suchvektor, aus dem Eintrag berechnet",
    "chat_media.ciphertext_blob": "Ende-zu-Ende verschluesselt, der Schluessel steht nur in der Nachricht",
    "hoster_identities.external_subject_hash": "Hash der Kundenkennung beim Hoster",
    "hoster_handoffs.token_hash": "Hash eines Uebergabetokens",
    "hoster_webhook_deliveries.payload_hash": "Interner Hash",
}

#: Spalten, deren Name nach Geheimnis klingt, die aber keins sind.
UNBEDENKLICH: dict[str, str] = {
    "users.has_password": "Ja/Nein",
    "users.password_reset_expires": "Zeitpunkt",
    "user_credentials.secret_hint": "Maske, verraet das Geheimnis nicht",
    "user_e2ee_devices.public_key_jwk": "oeffentlicher Schluessel",
    "user_e2ee_devices.signing_public_key_jwk": "oeffentlicher Schluessel",
    "user_e2ee_devices.approval_signature": "Unterschrift der Freigabe",
    "vault_user_settings.kdf_salt": "Salz der Tresor-Ableitung, ohne Master-Passwort wertlos",
    "chat_group_configs.blob": "Gruppenzustand, mit dem Gruppenschluessel verschluesselt",
    "server_credential_bindings.credential_id": "Verweis auf user_credentials",
    "desktop_jobs.device_family": "Welches Geraet den Auftrag bekam",
    "ai_memory_entries.embedding_model": "Name des Modells",
    "ai_skills.embedding_model": "Name des Modells",
}

#: Verschluesselte Text-Spalten mit AAD je Zeile. Die AAD steht ueberall so,
#: wie der jeweilige Dienst sie schreibt.
ZEILEN_AAD: dict[str, Callable[[dict], str]] = {
    "users.email_encrypted": lambda z: "msm:user:email",
    "oauth_user_links.email_at_link_encrypted": lambda z: "msm:oauth:link:email",
    "oauth_user_links.username_at_link_encrypted": lambda z: "msm:oauth:link:username",
    "notes.title": lambda z: f"msm:note:{z['user_id']}:{z['note_uid']}",
    "notes.content": lambda z: f"msm:note:{z['user_id']}:{z['note_uid']}",
    "calendar_events.title": lambda z: f"msm:cal:{z['user_id']}:{z['event_uid']}",
    "calendar_events.description": lambda z: f"msm:cal:{z['user_id']}:{z['event_uid']}",
    "calendar_events.location": lambda z: f"msm:cal:{z['user_id']}:{z['event_uid']}",
    "calendar_events.recurrence": lambda z: f"msm:cal:{z['user_id']}:{z['event_uid']}",
    "ai_memory_entries.value_encrypted": lambda z: (
        f"msm:ai:memory:{z['scope_identity']}:{z['id']}"
        if int(z.get("aad_version") or 1) >= 2 else f"msm:ai:memory:{z['id']}"
    ),
    "ai_memory_entries.key_encrypted": lambda z: f"msm:ai:memory:key:{z['scope_identity']}:{z['id']}",
    "ai_attachments.content_encrypted": lambda z: f"msm:ai:attachment:{z['id']}:content",
    "ai_attachments.extracted_text_encrypted": lambda z: f"msm:ai:attachment:{z['id']}:text",
    "ai_action_proposals.payload_encrypted": lambda z: f"msm:ai:action-proposal:v1:{z['id']}",
    "desktop_jobs.payload_encrypted": lambda z: f"msm:desktop_job:{z['id']}",
    "desktop_jobs.result_encrypted": lambda z: f"msm:desktop_job:{z['id']}",
    "vault_hints.hint": lambda z: f"msm:vault:hint:{z['user_id']}",
    "user_credentials.secret_encrypted": lambda z: f"msm:credential:{z['id']}:secret",
    "user_mailboxes.credentials_encrypted": lambda z: f"msm:user_mailbox:{z['user_id']}",
    "user_calendars.credentials_encrypted": lambda z: f"msm:user_calendar:{z['user_id']}",
}

#: Zugangsdaten, die der Nutzer selbst hinterlegt hat. Sie gehen nur mit, wenn
#: er sich mit Passwort oder zweitem Faktor ausgewiesen hat.
GEHEIMNISSE = frozenset({
    "user_credentials.secret_encrypted",
    "user_mailboxes.credentials_encrypted",
    "user_calendars.credentials_encrypted",
})


def _benutzer_schluessel(tabelle: Table) -> list:
    return [
        spalte for spalte in tabelle.columns
        if any(fk.column.table.name == "users" for fk in spalte.foreign_keys)
    ]


def zeilenwahl(benutzer_id: int) -> dict[str, Any]:
    """Je exportierter Tabelle die WHERE-Bedingung, in Schemareihenfolge.

    Eltern stehen vor Kindern (``sorted_tables``); ein Kind verweist per
    Unterabfrage auf die Bedingung seines Elternteils.
    """
    bedingungen: dict[str, Any] = {}
    for tabelle in Base.metadata.sorted_tables:
        if tabelle.name in OHNE_KONTOBEZUG:
            continue
        if tabelle.name == "users":
            bedingungen["users"] = tabelle.c.id == benutzer_id
            continue
        eigene = _benutzer_schluessel(tabelle)
        if eigene:
            bedingungen[tabelle.name] = or_(*(spalte == benutzer_id for spalte in eigene))
            continue
        ueber_eltern = [
            fk.parent.in_(select(fk.column).where(bedingungen[fk.column.table.name]))
            for fk in tabelle.foreign_keys
            if fk.column.table.name in bedingungen and fk.column.table.name != tabelle.name
        ]
        if ueber_eltern:
            bedingungen[tabelle.name] = or_(*ueber_eltern)
    return bedingungen


def _wert(wert: Any) -> Any:
    if isinstance(wert, datetime):
        if wert.tzinfo is None:
            wert = wert.replace(tzinfo=timezone.utc)
        return wert.isoformat()
    if isinstance(wert, date):
        return wert.isoformat()
    if isinstance(wert, Decimal):
        return str(wert)
    if isinstance(wert, (bytes, bytearray, memoryview)):
        return base64.b64encode(bytes(wert)).decode("ascii")
    return wert


def _rohe_zeilen(db: Session, tabelle: Table, bedingung) -> list[dict]:
    spalten = [
        type_coerce(spalte, Text).label(spalte.name) if isinstance(spalte.type, DisText) else spalte
        for spalte in tabelle.columns
        if f"{tabelle.name}.{spalte.name}" not in AUSGESCHLOSSEN
    ]
    return [dict(zeile._mapping) for zeile in db.execute(select(*spalten).where(bedingung))]


def _aad_je_spalte(tabelle: Table, mit_geheimnissen: bool) -> dict[str, Callable[[dict], str]]:
    aad: dict[str, Callable[[dict], str]] = {}
    for spalte in tabelle.columns:
        name = f"{tabelle.name}.{spalte.name}"
        if name in AUSGESCHLOSSEN or (name in GEHEIMNISSE and not mit_geheimnissen):
            continue
        if isinstance(spalte.type, DisText):
            aad[spalte.name] = lambda z, feste=spalte.type.aad: feste
        elif name in ZEILEN_AAD:
            aad[spalte.name] = ZEILEN_AAD[name]
    return aad


def _entschluesseln(zeilen: list[dict], aad: dict[str, Callable[[dict], str]]) -> int:
    """Ersetzt Chiffrat durch Klartext, gebuendelt. Gibt die Zahl unlesbarer Werte zurueck."""
    from services.dis_client import DisClient

    offen: list[tuple[dict, str, tuple[str, str]]] = []
    for zeile in zeilen:
        for spalte, aad_von in aad.items():
            wert = zeile.get(spalte)
            if isinstance(wert, str) and DisClient.ist_verschluesselt(wert):
                offen.append((zeile, spalte, (wert, aad_von(zeile))))
    klartexte = gebuendelt_entschluesseln([paar for _, _, paar in offen])
    unlesbar = 0
    for zeile, spalte, paar in offen:
        klartext = klartexte.get(paar)
        if klartext is None:
            try:
                klartext = DisClient.decrypt(*paar)
            except Exception:  # noqa: BLE001 - ein Wert haelt den Rest nicht auf
                logger.warning("Datenexport: ein Wert in %s nicht lesbar.", spalte)
                unlesbar += 1
        zeile[spalte] = klartext
    return unlesbar


def _avatar(url: str | None) -> dict | None:
    from services.bild_upload import DATEINAME_MUSTER, bilder_verzeichnis

    dateiname = (url or "").split("/")[-1]
    if not dateiname or not DATEINAME_MUSTER.match(dateiname):
        return None
    pfad = os.path.join(bilder_verzeichnis(), dateiname)
    if not os.path.isfile(pfad):
        return None
    with open(pfad, "rb") as datei:
        inhalt = datei.read()
    return {"pfad": f"dateien/avatar/{dateiname}", "base64": base64.b64encode(inhalt).decode("ascii")}


def _anhang_als_datei(zeile: dict) -> dict | None:
    """Der Inhalt eines KI-Anhangs geht als Datei statt als Base64 in der Tabelle."""
    inhalt = zeile.pop("content_encrypted", None)
    if not inhalt:
        return None
    name = str(zeile.get("original_name") or "anhang").replace("/", "_").replace("\\", "_")
    pfad = f"dateien/ki-anhaenge/{zeile['id']}-{name}"
    zeile["datei"] = pfad
    return {"pfad": pfad, "base64": inhalt}


def exportieren(db: Session, benutzer_id: int, *, mit_geheimnissen: bool) -> dict[str, Any]:
    tabellen: dict[str, list[dict]] = {}
    dateien: list[dict] = []
    unlesbar = 0
    for name, bedingung in zeilenwahl(benutzer_id).items():
        tabelle = Base.metadata.tables[name]
        zeilen = _rohe_zeilen(db, tabelle, bedingung)
        if not zeilen:
            continue
        unlesbar += _entschluesseln(zeilen, _aad_je_spalte(tabelle, mit_geheimnissen))
        if not mit_geheimnissen:
            for spalte in tabelle.columns:
                if f"{name}.{spalte.name}" in GEHEIMNISSE:
                    for zeile in zeilen:
                        zeile[spalte.name] = None
        if name == "ai_attachments":
            dateien.extend(d for d in map(_anhang_als_datei, zeilen) if d)
        if name == "users":
            avatar = _avatar(zeilen[0].get("avatar_url"))
            if avatar:
                dateien.append(avatar)
        tabellen[name] = [{k: _wert(v) for k, v in zeile.items()} for zeile in zeilen]

    return {
        "manifest": {
            "format": FORMAT,
            "erstellt_am": datetime.now(timezone.utc).isoformat(),
            "konto_id": benutzer_id,
            "zugangsdaten_enthalten": mit_geheimnissen,
            "unlesbare_werte": unlesbar,
            "zeilen_je_tabelle": {name: len(zeilen) for name, zeilen in tabellen.items()},
            "nicht_enthalten": {
                "spalten": AUSGESCHLOSSEN,
                "tabellen": OHNE_KONTOBEZUG,
            },
        },
        "tabellen": tabellen,
        "dateien": dateien,
    }
