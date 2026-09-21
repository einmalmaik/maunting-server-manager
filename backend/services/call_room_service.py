"""Wer darf in welchen Anrufraum. Fluechtig, im Speicher, ohne Datenbank.

Loest `direct_call_service.DirectCallInviteService` ab. Der Unterschied ist
einer: ein Raum haelt jetzt eine Menge berechtigter Kennungen statt genau zweier
Rollen. Ein Zweiergespraech ist damit nur der haeufige Fall, und wer schon drin
ist, kann einen Freund nachholen.

Der Raumname ist zugleich das Einladungstoken. Beide Seiten kennen ihn, und nur
wer ihn und eine Berechtigung hat, bekommt ein LiveKit-Zugangstoken. Es bleibt
dabei, dass hier nichts in die Datenbank faellt: kein Anrufverlauf, keine Dauer,
keine Gegenstelle.
"""

from __future__ import annotations

import secrets
import threading
import time
from typing import Any, Literal
import uuid
from dataclasses import dataclass

# Wie lange eine unbeantwortete Einladung gilt. Danach klingelt niemand mehr.
EINLADUNG_TTL_SEKUNDEN = 60.0
# Obergrenze fuer einen laufenden Raum. Jede Tokenausgabe schiebt das Ende nach
# hinten, aber nie ueber diese Spanne ab Raumbeginn hinaus.
RAUM_MAX_LEBENSDAUER_SEKUNDEN = 2 * 60 * 60.0
# Wie lange ein abgelehntes oder abgebrochenes Token als solches erinnert wird.
GRABSTEIN_TTL_SEKUNDEN = EINLADUNG_TTL_SEKUNDEN * 2

# Gruppenraeume sind grosszuegiger, aber nicht unbegrenzt: ab einer gewissen
# Groesse ist ein Anruf kein Gespraech mehr, und der Sidecar soll nicht
# unbemerkt zum Konferenzsystem werden.
GRUPPE_MAX_TEILNEHMER = 16


@dataclass
class _Anrufraum:
    ersteller_id: int
    berechtigte: set[int]
    erstellt_am: float
    laeuft_ab: float = 0.0
    """Ende der Einladungsfrist bzw. des laufenden Gespraechs."""
    mode: str = "audio"
    angenommen: bool = False

    def gueltig(self, jetzt: float) -> bool:
        return jetzt < self.laeuft_ab and jetzt < self.erstellt_am + RAUM_MAX_LEBENSDAUER_SEKUNDEN

    def verlaengere(self, jetzt: float, spanne: float) -> None:
        deckel = self.erstellt_am + RAUM_MAX_LEBENSDAUER_SEKUNDEN
        self.laeuft_ab = min(deckel, max(self.laeuft_ab, jetzt + spanne))


class CallRoomService:
    """Fluechtige Berechtigung fuer Direkt-Anrufraeume."""

    _raeume: dict[str, _Anrufraum] = {}
    _verbraucht: dict[str, float] = {}
    _lock = threading.RLock()

    @classmethod
    def issue(cls, caller_id: int, recipient_id: int, mode: str = "audio") -> str:
        """Neuer Raum mit Anrufer und Angerufenem. Gibt den Raumnamen zurueck."""
        token = secrets.token_urlsafe(32)
        jetzt = time.time()
        with cls._lock:
            cls._aufraeumen(jetzt)
            cls._raeume[token] = _Anrufraum(
                ersteller_id=caller_id,
                berechtigte={caller_id, recipient_id},
                erstellt_am=jetzt,
                laeuft_ab=jetzt + EINLADUNG_TTL_SEKUNDEN,
                mode=mode,
            )
        return token

    @classmethod
    def get_pending_invitation(cls, user_id: int) -> dict[str, Any] | None:
        """Sucht nach einer offenen, noch nicht angenommenen Einladung für den Benutzer."""
        jetzt = time.time()
        with cls._lock:
            cls._aufraeumen(jetzt)
            for token, raum in list(cls._raeume.items()):
                if (
                    user_id in raum.berechtigte
                    and user_id != raum.ersteller_id
                    and not raum.angenommen
                    and not cls.is_consumed(token)
                    and raum.gueltig(jetzt)
                ):
                    return {
                        "signaling_token": token,
                        "caller_id": raum.ersteller_id,
                        "mode": raum.mode,
                        "expires_in": max(1, int(raum.laeuft_ab - jetzt)),
                        "created_at": raum.erstellt_am,
                    }
        return None

    @classmethod
    def authorize(cls, token: str, user_id: int) -> bool:
        """Darf dieser Benutzer in diesen Raum?

        Verlaengert die Frist, weil eine Tokenausgabe bedeutet, dass gerade
        jemand beitritt oder neu verbindet.
        """
        jetzt = time.time()
        with cls._lock:
            raum = cls._raeume.get(token)
            if raum is None or not raum.gueltig(jetzt):
                cls._raeume.pop(token, None)
                return False
            if user_id not in raum.berechtigte:
                return False
            # Nur als angenommen markieren, wenn nicht der Ersteller selbst beitritt
            if user_id != raum.ersteller_id:
                raum.angenommen = True
            spanne = (
                RAUM_MAX_LEBENSDAUER_SEKUNDEN
                if raum.angenommen
                else EINLADUNG_TTL_SEKUNDEN
            )
            raum.verlaengere(jetzt, spanne)
            return True

    @classmethod
    def is_known(cls, token: str) -> bool:
        jetzt = time.time()
        with cls._lock:
            raum = cls._raeume.get(token)
            if raum is None or not raum.gueltig(jetzt):
                cls._raeume.pop(token, None)
                return False
            return True

    @classmethod
    def ersteller(cls, token: str) -> int | None:
        with cls._lock:
            raum = cls._raeume.get(token)
            if raum is None or not raum.gueltig(time.time()):
                return None
            return raum.ersteller_id

    @classmethod
    def is_accepted(cls, token: str) -> bool:
        with cls._lock:
            raum = cls._raeume.get(token)
            if raum is None or not raum.gueltig(time.time()):
                return False
            return raum.angenommen

    @classmethod
    def berechtigte(cls, token: str) -> set[int]:
        with cls._lock:
            raum = cls._raeume.get(token)
            if raum is None or not raum.gueltig(time.time()):
                return set()
            return set(raum.berechtigte)

    @classmethod
    def add_participant(cls, token: str, inviter_id: int, new_user_id: int) -> bool:
        """Holt jemanden in ein laufendes Gespraech.

        Nur wer selbst berechtigt ist, darf einladen. Die Freundschaftspruefung
        liegt beim Aufrufer — sie braucht die Datenbank, dieser Dienst nicht.
        """
        jetzt = time.time()
        with cls._lock:
            raum = cls._raeume.get(token)
            if raum is None or not raum.gueltig(jetzt):
                cls._raeume.pop(token, None)
                return False
            if inviter_id not in raum.berechtigte:
                return False
            raum.berechtigte.add(new_user_id)
            spanne = (
                RAUM_MAX_LEBENSDAUER_SEKUNDEN
                if raum.angenommen
                else EINLADUNG_TTL_SEKUNDEN
            )
            raum.verlaengere(jetzt, spanne)
            return True

    @classmethod
    def touch(cls, token: str, spanne: float = RAUM_MAX_LEBENSDAUER_SEKUNDEN) -> bool:
        """Verlaengert einen laufenden Raum (z. B. bei Heartbeat)."""
        jetzt = time.time()
        with cls._lock:
            raum = cls._raeume.get(token)
            if raum is None or not raum.gueltig(jetzt):
                return False
            raum.verlaengere(jetzt, spanne)
            return True

    @classmethod
    def reject(cls, token: str, user_id: int) -> int | None:
        """Der Angerufene lehnt ab. Gibt die Kennung des Anrufers zurueck."""
        return cls._beenden(token, user_id, nur_ersteller=False)

    @classmethod
    def cancel(cls, token: str, user_id: int) -> int | None:
        """Der Anrufer legt auf, bevor abgenommen wurde."""
        return cls._beenden(token, user_id, nur_ersteller=True)

    @classmethod
    def _beenden(cls, token: str, user_id: int, nur_ersteller: bool) -> int | None:
        jetzt = time.time()
        with cls._lock:
            raum = cls._raeume.get(token)
            if raum is None or not raum.gueltig(jetzt):
                cls._raeume.pop(token, None)
                return None
            if nur_ersteller:
                if raum.ersteller_id != user_id:
                    return None
                gegenstelle = next(
                    (uid for uid in raum.berechtigte if uid != user_id), None
                )
            else:
                if user_id not in raum.berechtigte or user_id == raum.ersteller_id:
                    return None
                gegenstelle = raum.ersteller_id
            cls._raeume.pop(token, None)
            cls._verbraucht[token] = jetzt
            return gegenstelle

    @classmethod
    def is_consumed(cls, token: str) -> bool:
        """Wurde die Einladung ausdruecklich abgelehnt oder abgebrochen?"""
        with cls._lock:
            wann = cls._verbraucht.get(token)
            if wann is None:
                return False
            if time.time() - wann > GRABSTEIN_TTL_SEKUNDEN:
                cls._verbraucht.pop(token, None)
                return False
            return True

    @classmethod
    def clear_all_for_testing(cls) -> None:
        with cls._lock:
            cls._raeume.clear()
            cls._verbraucht.clear()

    @classmethod
    def _aufraeumen(cls, jetzt: float) -> None:
        for token, raum in list(cls._raeume.items()):
            if not raum.gueltig(jetzt):
                cls._raeume.pop(token, None)
        grenze = jetzt - GRABSTEIN_TTL_SEKUNDEN
        for token, wann in list(cls._verbraucht.items()):
            if wann < grenze:
                cls._verbraucht.pop(token, None)


@dataclass
class _Gruppenraum:
    group_id: int
    laeuft_ab: float
    max_teilnehmer: int


class GroupCallRoomRegistry:
    """Fluechtige Raumpolitik fuer Gruppenanrufe.

    Haelt nur, was bei der Tokenausgabe gebraucht wird: zu welcher Gruppe ein
    Raum gehoert und wie gross er werden darf. Nie in der Datenbank, nie ein
    Anrufverlauf.
    """

    _raeume: dict[str, _Gruppenraum] = {}
    _lock = threading.RLock()

    @classmethod
    def create(
        cls,
        group_id: int,
        ttl_seconds: float = RAUM_MAX_LEBENSDAUER_SEKUNDEN,
        max_peers: int = GRUPPE_MAX_TEILNEHMER,
    ) -> tuple[str, int]:
        if not 2 <= max_peers <= GRUPPE_MAX_TEILNEHMER:
            raise ValueError("Ein Gruppenraum fasst zwischen 2 und 16 Teilnehmer.")
        token = f"grp_{uuid.uuid4().hex}"
        with cls._lock:
            cls._aufraeumen(time.time())
            cls._raeume[token] = _Gruppenraum(
                group_id=group_id,
                laeuft_ab=time.time() + ttl_seconds,
                max_teilnehmer=max_peers,
            )
        return token, max_peers

    @classmethod
    def get(cls, token: str) -> tuple[int, int] | None:
        with cls._lock:
            raum = cls._raeume.get(token)
            if raum is None:
                return None
            if time.time() >= raum.laeuft_ab:
                cls._raeume.pop(token, None)
                return None
            return raum.group_id, raum.max_teilnehmer

    @classmethod
    def offene_raeume(cls) -> list[str]:
        """Alle noch gueltigen Raumnamen. Fuer die Frage „telefoniert die Gruppe gerade?"."""
        jetzt = time.time()
        with cls._lock:
            cls._aufraeumen(jetzt)
            return list(cls._raeume.keys())

    @classmethod
    def find_for_group(cls, group_id: int) -> str | None:
        """Findet den aktiven Raumtoken für eine Gruppe, falls vorhanden."""
        jetzt = time.time()
        with cls._lock:
            cls._aufraeumen(jetzt)
            for token, raum in list(cls._raeume.items()):
                if raum.group_id == group_id and jetzt < raum.laeuft_ab:
                    return token
        return None

    @classmethod
    def discard(cls, token: str) -> None:
        with cls._lock:
            cls._raeume.pop(token, None)

    @classmethod
    def clear_all_for_testing(cls) -> None:
        with cls._lock:
            cls._raeume.clear()

    @classmethod
    def _aufraeumen(cls, jetzt: float) -> None:
        for token, raum in list(cls._raeume.items()):
            if jetzt >= raum.laeuft_ab:
                cls._raeume.pop(token, None)


ACTIVE_CALL_TTL_SEKUNDEN = 90.0


@dataclass
class _AktiverAnruf:
    user_id: int
    raum: str
    art: Literal["direkt", "gruppe"]
    group_id: int | None
    group_name: str | None
    mode: Literal["audio", "video"]
    device_id: str | None
    device_type: str | None
    started_at: float
    last_heartbeat: float
    partner_id: int | None = None
    partner_username: str | None = None
    partner_avatar_url: str | None = None
    user_username: str | None = None
    user_avatar_url: str | None = None

    def gueltig(self, jetzt: float) -> bool:
        return (jetzt - self.last_heartbeat) < ACTIVE_CALL_TTL_SEKUNDEN

    def to_dict(self) -> dict[str, Any]:
        partner = None
        if self.partner_id is not None and self.partner_username is not None:
            partner = {
                "user_id": self.partner_id,
                "username": self.partner_username,
                "avatar_url": self.partner_avatar_url,
            }
        return {
            "raum": self.raum,
            "art": self.art,
            "group_id": self.group_id,
            "group_name": self.group_name,
            "mode": self.mode,
            "device_id": self.device_id,
            "device_type": self.device_type or "web",
            "started_at": self.started_at,
            "partner": partner,
        }


class UserActiveCallRegistry:
    """Verwaltet den aktuellen aktiven Anruf je Benutzer über Plattformen hinweg.

    Flüchtig im Arbeitsspeicher, ohne Datenbank. Ermöglicht geräteübergreifende
    Anruferkennung ("Du bist bereits in einem Anruf") und nahtlose Übergabe
    (Handoff / Transfer) von einem Gerät auf ein anderes.
    """

    _aktive: dict[int, _AktiverAnruf] = {}
    _lock = threading.RLock()

    @classmethod
    def register(
        cls,
        user_id: int,
        raum: str,
        art: Literal["direkt", "gruppe"],
        *,
        group_id: int | None = None,
        group_name: str | None = None,
        mode: Literal["audio", "video"] = "audio",
        device_id: str | None = None,
        device_type: str | None = None,
        partner_id: int | None = None,
        partner_username: str | None = None,
        partner_avatar_url: str | None = None,
        user_username: str | None = None,
        user_avatar_url: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any] | None, bool]:
        """Registriert oder aktualisiert den Anruf eines Benutzers.

        Rückgabe: (aktueller_anruf, vorheriger_anruf, ist_uebergabe).
        `ist_uebergabe` ist True, wenn derselbe Raum von einem anderen Gerät betreten wurde.
        """
        jetzt = time.time()
        with cls._lock:
            cls._aufraeumen(jetzt)
            vorher = cls._aktive.get(user_id)
            prev_dict: dict[str, Any] | None = None
            is_handoff = False

            if vorher is not None and vorher.gueltig(jetzt):
                prev_dict = vorher.to_dict()
                if vorher.raum == raum and vorher.device_id != device_id:
                    is_handoff = True

            neu = _AktiverAnruf(
                user_id=user_id,
                raum=raum,
                art=art,
                group_id=group_id,
                group_name=group_name,
                mode=mode,
                device_id=device_id,
                device_type=device_type or "web",
                started_at=vorher.started_at if (vorher and is_handoff) else jetzt,
                last_heartbeat=jetzt,
                partner_id=partner_id if partner_id is not None else (vorher.partner_id if vorher else None),
                partner_username=partner_username if partner_username is not None else (vorher.partner_username if vorher else None),
                partner_avatar_url=partner_avatar_url if partner_avatar_url is not None else (vorher.partner_avatar_url if vorher else None),
                user_username=user_username if user_username is not None else (vorher.user_username if vorher else None),
                user_avatar_url=user_avatar_url if user_avatar_url is not None else (vorher.user_avatar_url if vorher else None),
            )
            cls._aktive[user_id] = neu
            return neu.to_dict(), prev_dict, is_handoff

    @classmethod
    def get(cls, user_id: int) -> dict[str, Any] | None:
        jetzt = time.time()
        with cls._lock:
            cls._aufraeumen(jetzt)
            anruf = cls._aktive.get(user_id)
            if anruf is not None and anruf.gueltig(jetzt):
                return anruf.to_dict()
            if anruf is not None:
                cls._aktive.pop(user_id, None)

            # Zweite Chance: Ein Direkt-Gesprächspartner wartet noch im selben Raum
            for partner_uid, partner_anruf in list(cls._aktive.items()):
                if (
                    partner_anruf.art == "direkt"
                    and partner_anruf.partner_id == user_id
                    and partner_anruf.gueltig(jetzt)
                ):
                    if CallRoomService.is_accepted(partner_anruf.raum):
                        return {
                            "raum": partner_anruf.raum,
                            "art": "direkt",
                            "group_id": None,
                            "group_name": None,
                            "mode": partner_anruf.mode,
                            "device_id": None,
                            "device_type": "web",
                            "started_at": partner_anruf.started_at,
                            "partner": {
                                "user_id": partner_uid,
                                "username": partner_anruf.user_username or "Gesprächspartner",
                                "avatar_url": partner_anruf.user_avatar_url,
                            },
                        }
            return None

    @classmethod
    def heartbeat(cls, user_id: int, device_id: str | None = None) -> bool:
        jetzt = time.time()
        with cls._lock:
            anruf = cls._aktive.get(user_id)
            if anruf is None or not anruf.gueltig(jetzt):
                cls._aktive.pop(user_id, None)
                return False
            if device_id and anruf.device_id and anruf.device_id != device_id:
                return False
            anruf.last_heartbeat = jetzt
            return True

    @classmethod
    def leave(
        cls,
        user_id: int,
        raum: str | None = None,
        device_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Entfernt den aktiven Anruf eines Benutzers."""
        jetzt = time.time()
        with cls._lock:
            anruf = cls._aktive.get(user_id)
            if anruf is None:
                return None
            if raum and anruf.raum != raum:
                return None
            if device_id and anruf.device_id and anruf.device_id != device_id:
                return None
            cls._aktive.pop(user_id, None)
            return anruf.to_dict()

    @classmethod
    def remove_room(cls, raum: str) -> list[int]:
        """Entfernt alle Einträge zu einem Raum (z. B. wenn Raum beendet wird)."""
        betroffene: list[int] = []
        with cls._lock:
            for uid, anruf in list(cls._aktive.items()):
                if anruf.raum == raum:
                    cls._aktive.pop(uid, None)
                    betroffene.append(uid)
        return betroffene

    @classmethod
    def clear_all_for_testing(cls) -> None:
        with cls._lock:
            cls._aktive.clear()

    @classmethod
    def _aufraeumen(cls, jetzt: float) -> None:
        for uid, anruf in list(cls._aktive.items()):
            if not anruf.gueltig(jetzt):
                cls._aktive.pop(uid, None)

