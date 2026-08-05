"""Expliziter Auslöser-Kontext für gemeinsam genutzte Backend-Services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID, uuid4

from models import User


ActorOrigin = Literal["direct", "ai", "external", "system"]


@dataclass(frozen=True)
class ActorContext:
    """Bindet eine Aktion an den bereits authentifizierten Principal.

    Auch AI- und externe Aufrufe benötigen einen konkreten MSM-Benutzer. Damit
    können spätere Adapter den zentralen RBAC-Check nicht durch einen namenlosen
    Worker umgehen.
    """

    user: User
    origin: ActorOrigin
    correlation_id: str

    @classmethod
    def for_user(
        cls,
        user: User,
        *,
        origin: ActorOrigin = "direct",
        correlation_id: str | UUID | None = None,
    ) -> "ActorContext":
        value = str(UUID(str(correlation_id))) if correlation_id else str(uuid4())
        return cls(user=user, origin=origin, correlation_id=value)
