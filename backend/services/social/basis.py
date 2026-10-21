"""Gemeinsame Grundlagen der Social-Domänen.

Hier liegt nur, was jede Domäne braucht und was keinen Import aus einer
Domäne nach sich zieht: die Uhr und der Schlüssel der Gegenüber-Karte.
"""

from __future__ import annotations

from datetime import datetime, timezone


def _now() -> datetime:
    return datetime.now(timezone.utc)


#: Unter diesem Schlüssel liegt in `Session.info` die Karte aus
#: `SocialService._gegenueber_karte`.
_GEGENUEBER_ABLAGE = "msm_gegenueber"
_GEGENUEBER_FRIST = 30.0
