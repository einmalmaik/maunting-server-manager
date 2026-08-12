"""Hoster-Anbindung: Integration, Produkte, externe Identitaeten und Services.

Diese Tabellen bilden nur die *Zuordnung* zwischen einem externen Shop und MSM
ab. Die eigentliche Servererstellung und der Lifecycle laufen unveraendert ueber
`server_provisioning_service` und `server_action_service` — es gibt bewusst
keinen zweiten Weg, einen Server anzulegen.

Secrets: API-Key nur als SHA-256-Hash, Webhook-Secret nur DIS-verschluesselt,
Handoff-Token nur als SHA-256-Hash.
"""

from datetime import datetime, timezone
import hashlib

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


def hash_token(value: str) -> str:
    """Einheitlicher SHA-256-Hex-Hash fuer API-Keys, Subjekte und Handoff-Token.

    Ein gemeinsamer Helper verhindert, dass an einzelnen Stellen versehentlich
    ein anderes (schwaecheres) Verfahren verwendet wird.
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class HosterIntegration(Base):
    """Ein angebundener Shop bzw. Hoster mit eigenem API-Key und Webhook-Ziel."""

    __tablename__ = "hoster_integrations"
    __table_args__ = (
        CheckConstraint(
            "terminate_grace_days >= 0 AND terminate_grace_days <= 365",
            name="ck_hoster_integrations_grace",
        ),
        UniqueConstraint("slug", name="uq_hoster_integrations_slug"),
        UniqueConstraint("api_key_hash", name="uq_hoster_integrations_api_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Der Panel-Benutzer, in dessen Namen diese Integration handelt. Ein Shop
    # kann damit nie mehr als dieser Benutzer darf; es gibt keinen namenlosen
    # Provisionierungspfad an RBAC vorbei.
    service_user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    api_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    api_key_hint: Mapped[str | None] = mapped_column(String(16), nullable=True)
    webhook_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    webhook_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    webhook_secret_hint: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Nach einer Kuendigung bleibt der Service so viele Tage erhalten, bevor er
    # geloescht werden darf. 0 bedeutet: sofort loeschbar.
    terminate_grace_days: Mapped[int] = mapped_column(Integer, default=7, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )


class HosterProduct(Base):
    """Abbildung eines Shopprodukts auf Blueprint und Ressourcenpaket."""

    __tablename__ = "hoster_products"
    __table_args__ = (
        UniqueConstraint("integration_id", "external_product_key", name="uq_hoster_products_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    integration_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("hoster_integrations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Die Produktkennung des Shops. Der Shop muss keine internen MSM-IDs kennen.
    external_product_key: Mapped[str] = mapped_column(String(128), nullable=False)
    game_type: Mapped[str] = mapped_column(String(64), nullable=False)
    ram_limit_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cpu_limit_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disk_limit_gb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Ohne feste Node waehlt die vorhandene Provisionierung die lokale Node.
    node_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("nodes.id", ondelete="SET NULL"), nullable=True
    )
    backup_interval_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Diese globale Rolle bekommt der Kunde zusaetzlich, solange sein Vertrag
    # aktiv ist. Ueber globale Rollen laufen unter anderem die KI-Kontingente
    # (`role_ai_limit.py`) — genau dafuer gibt es das Feld: ein groesseres
    # Produkt darf mehr KI, ohne dass jemand von Hand Rollen nachpflegt.
    role_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("roles.id", ondelete="SET NULL"), nullable=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )


class HosterIdentity(Base):
    """Feste Zuordnung eines externen Kunden zu genau einem MSM-Benutzer.

    Der Anker ist `(integration_id, external_subject_hash)`. Eine E-Mail-Adresse
    allein verknuepft bewusst keinen Account: sie ist beim Hoster aenderbar und
    kann in zwei angebundenen Shops dieselbe sein.
    """

    __tablename__ = "hoster_identities"
    __table_args__ = (
        UniqueConstraint(
            "integration_id", "external_subject_hash", name="uq_hoster_identities_subject"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    integration_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("hoster_integrations.id", ondelete="CASCADE"),
        nullable=False,
    )
    external_subject_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    external_subject_hint: Mapped[str | None] = mapped_column(String(16), nullable=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class HosterService(Base):
    """Ein gemieteter Service ueber seinen gesamten Lebenszyklus.

    `desired_state` ist der Wunsch des Shops, `status` der tatsaechlich erreichte
    Zustand in MSM. Beide auseinanderzuhalten ist der Grund, warum ein erneut
    gesendeter Auftrag keinen zweiten Server erzeugt.
    """

    __tablename__ = "hoster_services"
    __table_args__ = (
        CheckConstraint(
            "desired_state IN ('active', 'suspended', 'terminated')",
            name="ck_hoster_services_desired_state",
        ),
        CheckConstraint(
            "status IN ('pending', 'provisioning', 'ready', 'suspended', "
            "'failed', 'terminating', 'terminated')",
            name="ck_hoster_services_status",
        ),
        UniqueConstraint(
            "integration_id", "external_service_id", name="uq_hoster_services_external_id"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    integration_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("hoster_integrations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    external_service_id: Mapped[str] = mapped_column(String(128), nullable=False)
    identity_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("hoster_identities.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("hoster_products.id", ondelete="SET NULL"), nullable=True
    )
    server_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("servers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Welche globale Rolle dieser Vertrag dem Kunden tatsaechlich verschafft hat.
    #
    # Bewusst hier festgehalten und nicht bei Bedarf aus `product.role_id`
    # abgeleitet: das Produkt ist veraenderlich, die Vergabe ist ein Ereignis.
    # Wer beim Entzug nachsieht, was *heute* am Produkt steht, entzieht nach
    # einem Tarifwechsel die falsche Rolle und nach einer Produktaenderung gar
    # keine — der Kunde behielte ein KI-Kontingent ohne Vertrag. Diese Spalte
    # ist die einzige Stelle, an der steht, was zurueckzunehmen ist.
    granted_role_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("roles.id", ondelete="SET NULL"), nullable=True
    )
    desired_state: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    status_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    # Fruehestens ab diesem Zeitpunkt darf ein gekuendigter Service geloescht
    # werden. Eine Kuendigung vernichtet also nicht sofort alle Daten.
    terminate_after: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    identity: Mapped["HosterIdentity"] = relationship("HosterIdentity", lazy="joined")
    product: Mapped["HosterProduct | None"] = relationship("HosterProduct", lazy="joined")


class HosterHandoff(Base):
    """Kurzlebiger Einmal-Link aus dem Shop direkt in das MSM-Panel."""

    __tablename__ = "hoster_handoffs"
    __table_args__ = (UniqueConstraint("token_hash", name="uq_hoster_handoffs_token"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    integration_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("hoster_integrations.id", ondelete="CASCADE"), nullable=False
    )
    service_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("hoster_services.id", ondelete="CASCADE"), nullable=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Nur panelinterne Pfade. Kein offener Redirect.
    target_path: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )


class HosterWebhookDelivery(Base):
    """Persistente, wiederholbare Zustellung an den Shop.

    Bewusst mit `next_attempt_at` in der Datenbank statt nur im Speicher: ein
    Panel-Neustart waehrend eines Backoffs darf keine Zustellung verlieren.
    """

    __tablename__ = "hoster_webhook_deliveries"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'ok', 'failed')",
            name="ck_hoster_webhook_deliveries_status",
        ),
        # Der Scheduler fragt genau diese Kombination ab. Ohne den Index waere
        # jeder Lauf ein voller Tabellenscan.
        Index("ix_hoster_webhook_deliveries_pending", "status", "next_attempt_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    integration_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("hoster_integrations.id", ondelete="CASCADE"), nullable=False
    )
    service_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("hoster_services.id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    response_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(String(200), nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
