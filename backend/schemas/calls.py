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


class CallTokenResponse(BaseModel):
    """Was der Browser braucht, um dem Raum beizutreten."""

    url: str
    token: str
    raum: str
    identity: str
    ttl: int


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
