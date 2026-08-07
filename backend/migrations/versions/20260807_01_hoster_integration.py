"""Add hoster integrations, products, external identities, services and handoffs.

Revision ID: 20260807_01
Revises: 20260801_06
Create Date: 2026-08-07

Phase 6 des v4-Plans. Alle Tabellen sind optional: ein Self-Hosted-Betrieb ohne
Hoster-Anbindung legt keine Zeilen an und verhaelt sich unveraendert.

Datenminimierung:
- Der externe Kundenbezeichner (`external_subject`) identifiziert eine
  natuerliche Person beim Hoster und wird deshalb — wie bereits bei
  `oauth_user_links` — ausschliesslich als SHA-256-Hash gespeichert.
- Die externe Service-ID ist dagegen eine Vertragskennung, kein Personenbezug.
  Sie bleibt im Klartext, weil Idempotenz, Statusabfragen und der Support sie
  brauchen.
- API-Keys und Webhook-Secrets liegen nie im Klartext: der API-Key nur als
  SHA-256-Hash, das Webhook-Secret DIS-verschluesselt.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260807_01"
down_revision: Union[str, None] = "20260801_06"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "hoster_integrations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        # Der vom Betreiber benannte Panel-Benutzer, in dessen Namen diese
        # Integration handelt. Damit kann ein Shop nie mehr als dieser Benutzer,
        # und die zentrale RBAC-Pruefung bleibt die einzige Grenze.
        sa.Column("service_user_id", sa.Integer(), nullable=False),
        # Nur der Hash. Der Klartext-Key wird einmalig beim Anlegen/Rotieren
        # zurueckgegeben und danach nie wieder.
        sa.Column("api_key_hash", sa.String(length=64), nullable=False),
        sa.Column("api_key_hint", sa.String(length=16), nullable=True),
        sa.Column("webhook_url", sa.String(length=2048), nullable=True),
        sa.Column("webhook_secret_encrypted", sa.Text(), nullable=True),
        sa.Column("webhook_secret_hint", sa.String(length=16), nullable=True),
        # Kuendigungsfrist: eine Kuendigung vernichtet nicht sofort alle Daten.
        sa.Column("terminate_grace_days", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "terminate_grace_days >= 0 AND terminate_grace_days <= 365",
            name="ck_hoster_integrations_grace",
        ),
        sa.ForeignKeyConstraint(["service_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_hoster_integrations_slug"),
        sa.UniqueConstraint("api_key_hash", name="uq_hoster_integrations_api_key"),
    )

    op.create_table(
        "hoster_products",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("integration_id", sa.Integer(), nullable=False),
        sa.Column("external_product_key", sa.String(length=128), nullable=False),
        sa.Column("game_type", sa.String(length=64), nullable=False),
        sa.Column("ram_limit_mb", sa.Integer(), nullable=True),
        sa.Column("cpu_limit_percent", sa.Integer(), nullable=True),
        sa.Column("disk_limit_gb", sa.Integer(), nullable=True),
        sa.Column("node_id", sa.Integer(), nullable=True),
        sa.Column("backup_interval_hours", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["integration_id"], ["hoster_integrations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["node_id"], ["nodes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "integration_id", "external_product_key", name="uq_hoster_products_key"
        ),
    )
    op.create_index("ix_hoster_products_integration_id", "hoster_products", ["integration_id"])

    op.create_table(
        "hoster_identities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("integration_id", sa.Integer(), nullable=False),
        sa.Column("external_subject_hash", sa.String(length=64), nullable=False),
        sa.Column("external_subject_hint", sa.String(length=16), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["integration_id"], ["hoster_integrations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # Die Zuordnung erfolgt ausschliesslich ueber (Integration, Subjekt).
        # Eine E-Mail-Adresse allein verknuepft bewusst keinen Account.
        sa.UniqueConstraint(
            "integration_id", "external_subject_hash", name="uq_hoster_identities_subject"
        ),
    )
    op.create_index("ix_hoster_identities_user_id", "hoster_identities", ["user_id"])

    op.create_table(
        "hoster_services",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("integration_id", sa.Integer(), nullable=False),
        sa.Column("external_service_id", sa.String(length=128), nullable=False),
        sa.Column("identity_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=True),
        sa.Column("server_id", sa.Integer(), nullable=True),
        sa.Column("desired_state", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("status_code", sa.String(length=64), nullable=True),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("correlation_id", sa.String(length=36), nullable=False),
        sa.Column("terminate_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "desired_state IN ('active', 'suspended', 'terminated')",
            name="ck_hoster_services_desired_state",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'provisioning', 'ready', 'suspended', "
            "'failed', 'terminating', 'terminated')",
            name="ck_hoster_services_status",
        ),
        sa.ForeignKeyConstraint(
            ["integration_id"], ["hoster_integrations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["identity_id"], ["hoster_identities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["hoster_products.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["server_id"], ["servers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        # Der Idempotenzanker: derselbe Auftrag erzeugt nie einen zweiten Server.
        sa.UniqueConstraint(
            "integration_id", "external_service_id", name="uq_hoster_services_external_id"
        ),
    )
    op.create_index("ix_hoster_services_integration_id", "hoster_services", ["integration_id"])
    op.create_index("ix_hoster_services_server_id", "hoster_services", ["server_id"])

    op.create_table(
        "hoster_handoffs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("integration_id", sa.Integer(), nullable=False),
        sa.Column("service_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        # Nur der Hash: der Klartext-Token existiert einzig im Link des Kunden
        # und taucht weder in Audit noch in Logs auf.
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("target_path", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["integration_id"], ["hoster_integrations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["service_id"], ["hoster_services.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_hoster_handoffs_token"),
    )
    op.create_index("ix_hoster_handoffs_user_id", "hoster_handoffs", ["user_id"])

    op.create_table(
        "hoster_webhook_deliveries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("integration_id", sa.Integer(), nullable=False),
        sa.Column("service_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        # Durable statt nur im Speicher: ein Panel-Neustart verliert keine
        # ausstehende Zustellung mehr.
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("response_code", sa.Integer(), nullable=True),
        sa.Column("error", sa.String(length=200), nullable=True),
        sa.Column("correlation_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'ok', 'failed')",
            name="ck_hoster_webhook_deliveries_status",
        ),
        sa.ForeignKeyConstraint(
            ["integration_id"], ["hoster_integrations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["service_id"], ["hoster_services.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_hoster_webhook_deliveries_pending",
        "hoster_webhook_deliveries",
        ["status", "next_attempt_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_hoster_webhook_deliveries_pending", table_name="hoster_webhook_deliveries")
    op.drop_table("hoster_webhook_deliveries")
    op.drop_index("ix_hoster_handoffs_user_id", table_name="hoster_handoffs")
    op.drop_table("hoster_handoffs")
    op.drop_index("ix_hoster_services_server_id", table_name="hoster_services")
    op.drop_index("ix_hoster_services_integration_id", table_name="hoster_services")
    op.drop_table("hoster_services")
    op.drop_index("ix_hoster_identities_user_id", table_name="hoster_identities")
    op.drop_table("hoster_identities")
    op.drop_index("ix_hoster_products_integration_id", table_name="hoster_products")
    op.drop_table("hoster_products")
    op.drop_table("hoster_integrations")
