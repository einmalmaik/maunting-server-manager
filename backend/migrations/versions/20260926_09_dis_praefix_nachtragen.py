"""DIS-Praefix fuer den Altbestand ausserhalb der KI-Tabellen.

Seit 20260926 schreibt der Sidecar jeden Wert als ``msm-dis-v1:`` + Base64.
``20260926_08`` hat den Praefix fuer die KI-Spalten nachgetragen; hier folgt
der Rest: E-Mail-Adressen, 2FA-Geheimnisse, Node-Token, Passwoerter,
Webhook-Geheimnisse und die verschluesselten Panel-Einstellungen.

Der Umschlag ist nur der Praefix vor demselben Base64, der Sidecar liest
beide Formen. Deshalb braucht es keinen Sidecar, und ``downgrade`` nimmt den
Praefix einfach wieder ab.

Nur Spalten, die ausschliesslich DIS-Chiffrat tragen. Nicht angefasst werden:

- Werte, die mit ``gAAAAA`` beginnen: Fernet aus der Zeit vor DIS. Die
  erkennt ``scripts/migrate_to_dis.py`` genau an diesem Anfang.
- ``calendar_events`` und ``notes``: dort liegen E2EE-Werte (``sv-cal-v1:``,
  ``sv-note-v1:``), DIS-Werte und alter Klartext nebeneinander, und nur der
  Dienst kann sie unterscheiden.
- In ``panel_settings`` jeder Schluessel, der nicht in ``PANEL_SCHLUESSEL``
  steht. Klartext-Altbestand liegt dort immer unter einem eigenen
  ``*_LEGACY``-Schluessel, nie unter dem verschluesselten.
- Werte, die mit Praefix nicht mehr in ihre Spalte passten. Sie bekommen ihn
  beim naechsten Speichern.

Revision ID: 20260926_09
Revises: 20260926_08
Create Date: 2026-09-26
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260926_09"
down_revision: Union[str, None] = "20260926_08"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PRAEFIX = "msm-dis-v1:"

NUR_DIS = (
    ("users", "email_encrypted"),
    ("users", "two_factor_secret_encrypted"),
    ("oauth_providers", "client_secret_encrypted"),
    ("oauth_user_links", "email_at_link_encrypted"),
    ("oauth_user_links", "username_at_link_encrypted"),
    ("nodes", "auth_token_enc"),
    ("node_enrollments", "auth_token_enc"),
    ("hoster_integrations", "webhook_secret_encrypted"),
    ("postgres_instances", "admin_password_encrypted"),
    ("postgres_databases", "owner_password_encrypted"),
    ("postgres_users", "password_encrypted"),
    ("user_calendars", "credentials_encrypted"),
    ("user_mailboxes", "credentials_encrypted"),
    ("user_credentials", "secret_encrypted"),
    ("webhook_subscriptions", "secret_encrypted"),
    ("vault_hints", "hint"),
    ("desktop_jobs", "payload_encrypted"),
    ("desktop_jobs", "result_encrypted"),
)

#: Die Schluessel in `panel_settings`, deren Wert DIS-Chiffrat ist. Als
#: Literale, nicht aus den Diensten importiert: eine Migration muss auch
#: laufen, wenn sich der Code laengst weitergedreht hat.
PANEL_SCHLUESSEL = (
    "smtp_password_encrypted",
    "resend_api_key_encrypted",
    "captcha_secret_key_encrypted",
    "ai_maptiler_browser_key_encrypted",
    "ai_tomtom_traffic_key_encrypted",
    "ai_satellite_credentials_encrypted",
    "ai_web_search_api_key_encrypted",
    "backup.s3_access_key_encrypted",
    "backup.s3_secret_key_encrypted",
    "backup.password_encrypted",
    "cloudflare_api_token_enc",
    "curseforge_api_key_enc",
    "github_clone_token_enc",
    "livekit_api_secret_encrypted",
    "managed_postgres.admin_password_encrypted",
    "singra_webhook_secret_enc",
    "singra_widget_install_id_enc",
    "steam_account_password_enc",
    "steam_web_api_key_enc",
    "webpush_vapid_private_encrypted",
)


def _spaltenlaengen(tabelle: str) -> dict[str, int | None]:
    return {
        s["name"]: getattr(s["type"], "length", None)
        for s in sa.inspect(op.get_bind()).get_columns(tabelle)
    }


def _ziele() -> list[tuple[str, str, str, dict]]:
    """(Tabelle, Spalte, zusaetzliche Bedingung, Parameter) fuer alles Vorhandene."""
    tabellen = set(sa.inspect(op.get_bind()).get_table_names())
    ziele = []
    for tabelle, spalte in NUR_DIS:
        if tabelle not in tabellen:
            continue
        laengen = _spaltenlaengen(tabelle)
        if spalte in laengen:
            ziele.append((tabelle, spalte, laengen[spalte], "", {}))
    if "panel_settings" in tabellen:
        laenge = _spaltenlaengen("panel_settings").get("value")
        schluessel = ", ".join(f":s{i}" for i in range(len(PANEL_SCHLUESSEL)))
        ziele.append((
            "panel_settings", "value", laenge, f" AND key IN ({schluessel})",
            {f"s{i}": wert for i, wert in enumerate(PANEL_SCHLUESSEL)},
        ))
    return ziele


def upgrade() -> None:
    conn = op.get_bind()
    for tabelle, spalte, laenge, extra, parameter in _ziele():
        passt = f" AND length({spalte}) <= {laenge - len(PRAEFIX)}" if laenge else ""
        conn.execute(
            sa.text(
                f"UPDATE {tabelle} SET {spalte} = :praefix || {spalte} "
                f"WHERE {spalte} IS NOT NULL AND {spalte} <> '' "
                f"AND {spalte} NOT LIKE 'msm-dis-%' AND {spalte} NOT LIKE 'gAAAAA%'"
                f"{passt}{extra}"
            ),
            {"praefix": PRAEFIX, **parameter},
        )


def downgrade() -> None:
    # Der aeltere Sidecar kennt den Praefix nicht; ohne ihn ist es dasselbe
    # Chiffrat. Das gilt auch fuer Werte, die der neue Code geschrieben hat.
    conn = op.get_bind()
    for tabelle, spalte, _laenge, extra, parameter in _ziele():
        conn.execute(
            sa.text(
                f"UPDATE {tabelle} SET {spalte} = substr({spalte}, {len(PRAEFIX) + 1}) "
                f"WHERE {spalte} LIKE '{PRAEFIX}%'{extra}"
            ),
            parameter,
        )
