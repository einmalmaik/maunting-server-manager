"""Pydantic schemas for WebRTC blinded signaling and ICE server configuration.

Implements zero-metadata blinded signaling schemas (R4).
Strictly validates client actions and server events using Pydantic v2.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union
from pydantic import BaseModel, Field, model_validator


# ============================================================================
# Client-to-Server Action Messages
# ============================================================================

class WebRtcJoinMessage(BaseModel):
    """Initial join handshake frame sent by client upon opening WebSocket."""
    action: Literal["join"]
    token: str = Field(
        ...,
        min_length=16,
        max_length=128,
        pattern=r"^[a-zA-Z0-9_\-]+$",
        description="Blinded rendezvous session token (hex or base64url)",
    )


class WebRtcSignalMessage(BaseModel):
    """Relay frame carrying client-side encrypted SDP or ICE candidate ciphertext."""
    action: Literal["signal"]
    data: str | None = Field(
        default=None,
        min_length=1,
        max_length=65536,
        description="Encrypted signaling payload (sv-sig-v1:...)",
    )
    payload: str | None = Field(
        default=None,
        min_length=1,
        max_length=65536,
        description="Alias for data field for seamless cross-client compatibility",
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_data_and_payload(cls, values: Any) -> Any:
        if isinstance(values, dict):
            data_val = values.get("data")
            payload_val = values.get("payload")
            if not data_val and payload_val:
                values["data"] = payload_val
            elif not payload_val and data_val:
                values["payload"] = data_val
            if not values.get("data"):
                raise ValueError("Signal message must contain non-empty 'data' or 'payload'")
        return values

    @property
    def signal_data(self) -> str:
        val = self.data or self.payload
        if not val:
            raise ValueError("Signal message must contain either 'data' or 'payload'")
        return val


class WebRtcLeaveMessage(BaseModel):
    """Explicit leave / hangup frame sent by client."""
    action: Literal["leave"]
    reason: str | None = Field(default="hangup", max_length=64)


class WebRtcPingMessage(BaseModel):
    """Keepalive ping frame sent by client."""
    action: Literal["ping"]


# Discriminated union of incoming client frames
WebRtcClientMessage = Annotated[
    Union[WebRtcJoinMessage, WebRtcSignalMessage, WebRtcLeaveMessage, WebRtcPingMessage],
    Field(discriminator="action"),
]


# ============================================================================
# Server-to-Client Event Messages
# ============================================================================

class WebRtcJoinedEvent(BaseModel):
    """Sent to connecting peer confirming successful join."""
    event: Literal["joined"] = "joined"
    role: Literal["initiator", "receiver"]
    peer_count: int = Field(..., ge=1, le=16)


class WebRtcPeerJoinedEvent(BaseModel):
    """Sent to initiator when receiver joins the room."""
    event: Literal["peer_joined"] = "peer_joined"
    peer_count: int = Field(..., ge=2, le=16)


class WebRtcSignalEvent(BaseModel):
    """Relayed signal message carrying encrypted SDP/ICE to the other peer."""
    event: Literal["signal"] = "signal"
    data: str = Field(..., description="Encrypted signaling payload")


class WebRtcPeerLeftEvent(BaseModel):
    """Sent to remaining peer when partner disconnects or hangs up."""
    event: Literal["peer_left"] = "peer_left"
    reason: str | None = Field(default=None, max_length=64)


class WebRtcErrorEvent(BaseModel):
    """Sent when an error or violation occurs."""
    event: Literal["error"] = "error"
    error: str = Field(..., max_length=64)
    code: int = Field(default=4000)
    message: str | None = Field(default=None, max_length=256)
    detail: str | None = Field(default=None, max_length=256)

    @model_validator(mode="before")
    @classmethod
    def _normalize_message_and_detail(cls, values: Any) -> Any:
        if isinstance(values, dict):
            msg = values.get("message")
            det = values.get("detail")
            if not msg and det:
                values["message"] = det
            elif not det and msg:
                values["detail"] = msg
        return values


class WebRtcPongEvent(BaseModel):
    """Heartbeat response frame."""
    event: Literal["pong"] = "pong"


# ============================================================================
# ICE Server Configuration
# ============================================================================

class WebRtcIceServerConfig(BaseModel):
    """STUN / TURN server configuration for WebRTC peer connection."""
    urls: list[str] | str
    username: str | None = None
    credential: str | None = None


class WebRtcIceServersResponse(BaseModel):
    """Response containing ICE servers for WebRTC peer connection."""
    ice_servers: list[WebRtcIceServerConfig] = Field(default_factory=list)
    ttl: int = Field(default=86400, description="Cache TTL in seconds")


class DirectCallResponse(BaseModel):
    """Backend-issued invitation used for a friend-to-friend call."""

    signaling_token: str = Field(..., min_length=16, max_length=128)
    recipient_id: int
    expires_in: int = Field(..., ge=1)
