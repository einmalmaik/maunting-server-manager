"""Schemas der Hoster-Anbindung.

Alle Lese-Antworten sind bewusst secret-frei: API-Key und Webhook-Secret
erscheinen ausschliesslich als `*_configured`-Flag und als nicht umkehrbarer
Hinweis auf die letzten vier Zeichen.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


# ── Panel-Verwaltung ───────────────────────────────────────────────────────


class HosterIntegrationWrite(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    slug: str = Field(..., min_length=1, max_length=64)
    enabled: bool = True
    service_user_id: int = Field(..., ge=1)
    webhook_url: str | None = Field(None, max_length=2048)
    terminate_grace_days: int = Field(7, ge=0, le=365)


class HosterIntegrationUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=128)
    enabled: bool | None = None
    service_user_id: int | None = Field(None, ge=1)
    webhook_url: str | None = Field(None, max_length=2048)
    terminate_grace_days: int | None = Field(None, ge=0, le=365)


class HosterIntegrationResponse(BaseModel):
    id: int
    name: str
    slug: str
    enabled: bool
    service_user_id: int
    webhook_url: str | None
    terminate_grace_days: int
    api_key_hint: str | None
    webhook_secret_configured: bool
    webhook_secret_hint: str | None
    created_at: datetime
    updated_at: datetime


class HosterSecretResponse(BaseModel):
    """Einmalige Ausgabe eines frisch erzeugten Geheimnisses."""

    value: str
    hint: str


class HosterProductWrite(BaseModel):
    external_product_key: str = Field(..., min_length=1, max_length=128)
    game_type: str = Field(..., min_length=1, max_length=64)
    ram_limit_mb: int | None = Field(None, ge=512, le=4_194_304)
    cpu_limit_percent: int | None = Field(None, ge=10, le=3_200)
    disk_limit_gb: int | None = Field(None, ge=1, le=1_048_576)
    node_id: int | None = Field(None, ge=1)
    backup_interval_hours: int | None = Field(None, ge=1, le=8_760)
    enabled: bool = True


class HosterProductResponse(BaseModel):
    id: int
    integration_id: int
    external_product_key: str
    game_type: str
    ram_limit_mb: int | None
    cpu_limit_percent: int | None
    disk_limit_gb: int | None
    node_id: int | None
    backup_interval_hours: int | None
    enabled: bool


class HosterServiceResponse(BaseModel):
    """Vertragszustand — ohne Node, Ports, Pfade oder Kundendaten."""

    external_service_id: str
    desired_state: str
    status: str
    status_code: str | None
    server_id: int | None
    task_id: str | None
    correlation_id: str
    terminate_after: datetime | None
    updated_at: datetime


class HosterDeliveryResponse(BaseModel):
    id: int
    event_type: str
    status: str
    attempt: int
    response_code: int | None
    error: str | None
    correlation_id: str
    created_at: datetime
    sent_at: datetime | None


# ── Externe API ────────────────────────────────────────────────────────────


class HosterDesiredStateRequest(BaseModel):
    """Der gewuenschte Zustand eines Vertrags.

    Der Shop uebermittelt bewusst keine internen MSM-Details: keine Node-ID,
    keine Ports, keine Pfade. MSM loest das technische Ziel selbst auf.
    """

    desired_state: str = Field(..., pattern=r"^(active|suspended|terminated)$")
    external_subject: str = Field(..., min_length=1, max_length=128)
    product_key: str | None = Field(None, min_length=1, max_length=128)
    email: str | None = Field(None, max_length=255)


class HosterHandoffRequest(BaseModel):
    external_service_id: str = Field(..., min_length=1, max_length=128)
    target_path: str | None = Field(None, max_length=128)


class HosterHandoffResponse(BaseModel):
    """Der Klartext-Token wird genau einmal ausgegeben und nie gespeichert."""

    url: str
    expires_at: datetime
