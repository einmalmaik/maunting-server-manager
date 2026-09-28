"""Schemas fuer Anrufe ueber LiveKit.

Loest `schemas/webrtc.py` ab. Die alten Signalisierungsrahmen (Join, Signal,
Leave, Ping, ICE-Server) gibt es nicht mehr: der Medienserver uebernimmt die
Aushandlung, das Panel gibt nur noch Zugangstoken aus.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

RAUM_MUSTER = r"^[a-zA-Z0-9_\-]+$"


class DirectCallResponse(BaseModel):
    """Einladung zu einem Zweiergespraech unter Freunden."""

    signaling_token: str = Field(..., min_length=16, max_length=128, pattern=RAUM_MUSTER)
    recipient_id: int
    expires_in: int = Field(..., ge=1)


class CallTokenRequest(BaseModel):
    art: Literal["direkt", "gruppe"]
    raum: str = Field(..., min_length=16, max_length=128, pattern=RAUM_MUSTER)
    group_id: int | None = None
    device_id: str | None = Field(None, max_length=128)
    device_type: str | None = Field(None, max_length=32)
    mode: Literal["audio", "video"] | None = "audio"


class CallTokenResponse(BaseModel):
    """Was der Browser braucht, um dem Raum beizutreten."""

    url: str
    token: str
    raum: str
    identity: str
    ttl: int


class ActiveCallPartner(BaseModel):
    user_id: int
    username: str
    avatar_url: str | None = None


class ActiveCallInfo(BaseModel):
    raum: str
    art: Literal["direkt", "gruppe"]
    group_id: int | None = None
    mode: Literal["audio", "video"] = "audio"
    device_id: str | None = None
    device_type: str | None = None
    started_at: float
    partner: ActiveCallPartner | None = None


class ActiveCallResponse(BaseModel):
    has_active_call: bool
    call: ActiveCallInfo | None = None


class CallLeaveRequest(BaseModel):
    raum: str | None = None
    device_id: str | None = None


class CallHeartbeatRequest(BaseModel):
    raum: str | None = None
    device_id: str | None = None


class CallParticipantCountResponse(BaseModel):
    raum: str
    teilnehmer: int


class CallKeyRelayRequest(BaseModel):
    """Der Raumschlüssel, verpackt für genau einen Empfänger.

    `ciphertext` ist ein hybrider Umschlag (`sv-e2ee-hybrid-...`) gegen den
    veröffentlichten Schlüssel des Empfängers. Das Panel reicht ihn durch und
    kann ihn nicht öffnen — es kennt den privaten Schlüssel nicht. Gespeichert
    wird nichts: der Umschlag geht als Ereignis hinaus und ist danach weg.
    """

    target_user_id: int
    ciphertext: str = Field(..., min_length=16, max_length=8192)


class CallMuteRequest(BaseModel):
    """Mikrofon eines Teilnehmers abschalten oder wieder freigeben.

    Als Feld statt als zwei Endpunkte, weil der Knopf im Anruffenster derselbe
    ist und die Oberfläche den gewünschten Zustand kennt, nicht die Richtung.
    """

    stumm: bool


class LivekitStatusResponse(BaseModel):
    modus: Literal["lokal", "extern"]
    url: str
    konfiguriert: bool
    erreichbar: bool
    fehler: str | None = None
    api_key_maskiert: str = ""
    raeume_aktiv: int = 0


class LivekitConfigUpdate(BaseModel):
    modus: Literal["lokal", "extern"]
    url: str | None = None
    # Leer heisst bei beiden „unveraendert lassen". Die Oberflaeche zeigt
    # Schluessel und Geheimnis nur maskiert an und schickt sie nie zurueck.
    api_key: str | None = None
    api_secret: str | None = None


class LivekitTestRequest(BaseModel):
    """Zugangsdaten zum Probelauf. Leere Felder meinen den gespeicherten Stand."""

    url: str
    api_key: str | None = None
    api_secret: str | None = None


class LivekitTestResponse(BaseModel):
    erreichbar: bool
    meldung: str
    raeume_aktiv: int = 0


class PendingGroupCallInfo(BaseModel):
    """Ein laufender Gruppenanruf — ohne zu sagen, wie die Gruppe heisst.

    `group_name` und `avatar_url` standen hier bis Stufe 6c und kamen aus
    `chat_groups.name`/`avatar_url`. Seit Stufe 6a sind diese Spalten leer, und
    ein Feld, das nur noch `None` traegt, ist eine offene Einladung, es wieder
    zu fuellen. Den Namen setzt der Client aus seinem versiegelten
    Namensspeicher ueber die `group_id`.
    """

    group_id: int
    room_token: str
    participant_count: int = 0


class PendingCallInfo(BaseModel):
    signaling_token: str
    caller_id: int
    caller_username: str
    caller_avatar_url: str | None = None
    mode: Literal["audio", "video"] = "audio"
    expires_in: int


class PendingCallResponse(BaseModel):
    has_pending_call: bool
    call: PendingCallInfo | None = None
    group_calls: list[PendingGroupCallInfo] = []

