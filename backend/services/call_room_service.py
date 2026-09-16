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
import uuid
from dataclasses import dataclass

# Wie lange eine unbeantwortete Einladung gilt. Danach klingelt niemand mehr.
EINLADUNG_TTL_SEKUNDEN = 120.0
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
    _lock = threading.Lock()

    @classmethod
    def issue(cls, caller_id: int, recipient_id: int) -> str:
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
            )
        return token

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
            raum.angenommen = True
            raum.verlaengere(jetzt, EINLADUNG_TTL_SEKUNDEN)
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
            raum.verlaengere(jetzt, EINLADUNG_TTL_SEKUNDEN)
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
    _lock = threading.Lock()

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
