from __future__ import annotations

import hashlib
import logging
import time

from fastapi import HTTPException
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from models import (
    User,
    UserFriend,
    E2eeBlindEnvelope,
    ChatGroupMember,
    DirectChat,
)
from services.social.basis import _GEGENUEBER_ABLAGE, _GEGENUEBER_FRIST, _now

logger = logging.getLogger(__name__)


class DirektchatMixin:
    @classmethod
    def derive_blind_mailbox_id(cls, user_a_id: int, user_b_id: int, salt: str = "") -> str:
        """Deterministische Hash-Berechnung der blinden E2EE-Mailbox-ID für zwei Benutzer.

        Identisch zur Formatdefinition in frontend/src/services/e2eeCrypto.ts und personal_proposals.py.
        """
        min_id = min(user_a_id, user_b_id)
        max_id = max(user_a_id, user_b_id)
        raw = f"msm:dm:{min_id}:{max_id}{f':{salt}' if salt else ''}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @classmethod
    def derive_user_device_mailbox_id(cls, user_id: int) -> str:
        """Deterministische Hash-Berechnung der blinden Geräte-Mailbox-ID für die Geräte eines Benutzers.

        Identisch zur Formatdefinition in frontend/src/services/e2eeCrypto.ts.
        """
        raw = f"msm:devices:{user_id}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @classmethod
    def derive_group_blind_mailbox_id(cls, group_id: int) -> str:
        """Deterministische Hash-Berechnung der blinden Gruppen-Mailbox-ID.

        Identisch zur Formatdefinition in frontend/src/services/e2eeCrypto.ts.
        """
        raw = f"msm:group:{group_id}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @classmethod
    def derive_legacy_direct_mailbox_id(cls, user_a_id: int, user_b_id: int) -> str:
        """Alte Ableitung der Mailbox-ID vor Einführung von msm:dm:<min>:<max>."""
        min_i, max_i = min(user_a_id, user_b_id), max(user_a_id, user_b_id)
        return hashlib.sha256(f"msm-e2ee-box:{min_i}:{max_i}:".encode("utf-8")).hexdigest()

    @classmethod
    def gegenueber_aus_mailbox(
        cls, db: Session, user_id: int, blind_mailbox_id: str
    ) -> int | None:
        """Wer steht diesem Konto in dieser abgeleiteten Mailbox gegenüber?

        Seit Stufe 6b steht in `direct_chats` kein Mensch mehr. Wo der Server
        früher `user_a_id`/`user_b_id` nachschlug, rechnet er jetzt: für jedes
        aktive Konto wird die Kennung abgeleitet, die dieses Paar ergäbe, und
        mit der gesuchten verglichen.

        **Das ist keine neue Auskunft, sondern dieselbe in teurer.** Die alten
        Kennungen `msm:dm:<min>:<max>` und `msm-e2ee-box:<min>:<max>:` sind aus
        zwei kleinen Ganzzahlen nachrechenbar; wer die Datenbank hat, konnte
        sie immer schon zuordnen. Was mit den Spalten verschwindet, ist die
        **Liste**: „zeig mir alle Gespräche von Konto 42" beantwortet keine
        Abfrage mehr. Und eine Kennung aus einem Chatgeheimnis (Stufe 3d)
        taucht hier gar nicht erst auf — sie ist unableitbar, und genau dorthin
        zieht jedes Gespräch um, sobald beide Seiten das Geheimnis haben.

        `None`, wenn die Kennung zu keinem Paar mit diesem Konto passt.
        """
        clean = (blind_mailbox_id or "").strip()
        if not clean:
            return None
        return cls._gegenueber_karte(db, user_id).get(clean)

    @classmethod
    def _gegenueber_karte(cls, db: Session, user_id: int) -> dict[str, int]:
        """Jede ableitbare Direktkennung dieses Kontos, je Kennung das Gegenüber.

        Gerechnet wird wie immer für jedes aktive Konto, aber einmal je Sitzung
        und nicht einmal je gefragter Mailbox. Ein Abo mit 200 Mailboxen
        rechnete bis 27.09.2026 zweihundertmal über alle Konten.

        Die Karte hängt an der Sitzung, lebt also höchstens eine Anfrage lang,
        und nie länger als `_GEGENUEBER_FRIST` Sekunden. Ändert die Sitzung
        selbst ein Konto, schreibt sie an der Einheit vorbei oder endet ihre
        Transaktion, fällt sie sofort weg (`_gegenueber_vergessen` und
        Nachbarn).
        """
        ablage = db.info.setdefault(_GEGENUEBER_ABLAGE, {})
        jetzt = time.monotonic()
        gemerkt = ablage.get(user_id)
        if gemerkt is not None and jetzt - gemerkt[0] < _GEGENUEBER_FRIST:
            return gemerkt[1]
        karte: dict[str, int] = {}
        for (kandidat,) in (
            db.query(User.id).filter(User.is_active == True, User.id != user_id).all()  # noqa: E712
        ):
            karte.setdefault(cls.derive_blind_mailbox_id(user_id, kandidat), kandidat)
            karte.setdefault(cls.derive_legacy_direct_mailbox_id(user_id, kandidat), kandidat)
        ablage[user_id] = (jetzt, karte)
        return karte

    @classmethod
    def _direktchat_zeile(cls, db: Session, user_a_id: int, user_b_id: int) -> DirectChat | None:
        """Die Gesprächszeile zweier Konten — gefunden über ihre Kennung.

        Der Ersatz für `filter(user_a_id=..., user_b_id=...)`. Beide Ids liegen
        dem Aufrufer in diesem Moment ohnehin vor; er rechnet daraus die
        Kennung und sucht danach. Die Zeile selbst nennt niemanden mehr.
        """
        return (
            db.query(DirectChat)
            .filter_by(blind_mailbox_id=cls.derive_blind_mailbox_id(user_a_id, user_b_id))
            .first()
        )

    @classmethod
    def is_blocked(cls, db: Session, user_a_id: int, user_b_id: int) -> bool:
        """Prüft, ob zwischen zwei Benutzern eine gegenseitige Blockierung vorliegt."""
        return (
            db.query(UserFriend)
            .filter(
                or_(
                    and_(UserFriend.user_id == user_a_id, UserFriend.friend_id == user_b_id),
                    and_(UserFriend.user_id == user_b_id, UserFriend.friend_id == user_a_id),
                ),
                UserFriend.status == "blocked",
            )
            .first()
        ) is not None

    @classmethod
    def resolve_mailbox_target(
        cls,
        db: Session,
        sender_user_id: int,
        blind_mailbox_id: str,
        is_control: bool = False,
        lege_chat_an: bool = True,
    ) -> tuple[int | None, DirectChat | None]:
        """Ermittelt das Ziel einer blinden Mailbox für den Absender.

        Liefert `(target_recipient_id, direct_chat)` — oder `(None, None)`,
        wenn der Server nicht weiss, wer gemeint ist. Das ist kein Fehler
        mehr, sondern der Normalfall: die Kennung **ist** die Adresse, und
        zugestellt wird über das Abo. Wirft HTTPException nur bei einer
        Kennung, die der Server auflösen kann und die der Absender nicht
        benutzen darf.

        Zwei Dinge standen hier bis 09/2026 und sind bewusst weg:

        - **Ein genanntes `recipient_id`.** Es sprang an der Auflösung vorbei
          und beantwortete dem Server die eine Frage, die er nicht stellen
          können soll.
        - **Der Gruppen-Nachschlag.** Er lieferte für *jede* Gruppennachricht
          die vollständige Mitgliederliste — der Server zählte bei jeder Zeile
          auf, wer in dieser Gruppe ist. Gruppen laufen jetzt über das
          Mailbox-Abo, und der Server erfährt nur, dass irgendwo etwas liegt.

        Was bleibt, ist der kontogebundene Direktchat-Weg. Er trägt die
        Erstaufnahme: `ensure_direct_chat` ist die Stelle, an der
        `can_message_user` über Blockierung und Privatsphäre entscheidet, und
        die gibt es erst mit MLS kryptographisch. Er fällt in Stufe 6.

        `lege_chat_an=False` schlägt nur nach und schreibt nichts. Gedacht für
        das flüchtige Tippsignal: es darf keine Zeile in `direct_chats`
        entstehen lassen, sonst legt ein „tippt gerade" in einem Gespräch, aus
        dem nie eine Nachricht wird, dauerhaft ab, dass diese beiden
        miteinander zu tun hatten.
        """
        clean_mailbox = blind_mailbox_id.strip()
        cls.assert_social_enabled(db)
        user_device_box = cls.derive_user_device_mailbox_id(sender_user_id)

        if clean_mailbox == user_device_box:
            return sender_user_id, None

        if is_control:
            ziel = cls._steuerziel_geraetemailbox(db, sender_user_id, clean_mailbox)
            if ziel is not None:
                return ziel, None

        # Der Nachschlag in `direct_chats` stand hier bis Stufe 6b: eine Zeile
        # je Gespräch, mit beiden Konto-Ids darin. Er war ein Abkürzung über
        # die Suche darunter, keine eigene Fähigkeit — dieselbe Kennung findet
        # die Kandidatensuche auch, nur ohne dass jemand aufschreiben müsste,
        # wer mit wem schreibt.
        #
        # Kandidatensuche über aktive Benutzer, einmal je Sitzung gerechnet.
        cand_id = cls.gegenueber_aus_mailbox(db, sender_user_id, clean_mailbox)
        if cand_id is not None:
            if cls.is_blocked(db, sender_user_id, cand_id):
                raise HTTPException(status_code=403, detail="Benutzer ist blockiert.")
            if not lege_chat_an:
                return cand_id, None
            chat = cls.ensure_direct_chat(db, sender_user_id, cand_id)
            return cand_id, chat

        return None, None

    @classmethod
    def _steuerziel_geraetemailbox(
        cls, db: Session, sender_user_id: int, clean_mailbox: str
    ) -> int | None:
        """Die Geräte-Mailbox eines Kontos, mit dem der Absender schon zu tun hat.

        Der Weg, auf dem ein Schlüssel sein Ziel erreicht, ohne durch die
        Mailbox des Gesprächs zu laufen. Bis 09/2026 ging er durch sie hindurch,
        und das hatte zwei Folgen: die Mailbox liess sich nicht mit einem
        Besitznachweis verschliessen (der Schlüssel dafür lag dahinter), und 99
        von 100 Umschlägen im Lesefenster waren Schlüsselzustellungen.

        Eng gefasst, und jede Einschränkung hat ihren Grund:

        - **Nur Steuerumschläge.** Eine Nachricht gehört nie in eine fremde
          Geräte-Mailbox; sonst wäre dieser Weg ein Chat am Gespräch vorbei, den
          kein Verlauf und keine Blockierung je zu sehen bekäme. Der Server kann
          nicht hineinsehen, aber er kann darauf bestehen, dass der Umschlag als
          Steuerung deklariert ist — und kein Client zeigt Steuerung je als Text.
        - **Nur an bestehende Beziehungen.** Nicht an jedes Konto: die Kennung
          ist aus einer kleinen Ganzzahl nachrechenbar, und ohne diese Schranke
          könnte jeder jedem in die Geräte-Mailbox schreiben.

        **Drei Quellen, und die zweite und dritte kamen mit Stufe 3e dazu.**
        Vorher zählten allein gemeinsame Gruppen — passend, solange hier nur
        Gruppenschlüssel liefen. Seit auch das Chatgeheimnis eines Direktchats
        diesen Weg nimmt, war das zu eng: zwei Freunde ohne gemeinsame Gruppe
        bekamen ein 403, das Geheimnis erreichte die Gegenseite nie, und ihr
        Chat wäre für immer auf der abgeleiteten Kennung stehen geblieben — der
        einen, die der Server selbst ausrechnen kann.

        Bewusst **nicht** dabei: ein Fremder mit öffentlichem Profil. Der darf
        mir eine erste Nachricht schreiben, und genau dabei entsteht die
        Chatzeile, die ihn ab dann hier hereinlässt. Ihn schon vorher
        zuzulassen, hiesse, jedem Unbekannten das Lesefenster zu öffnen, das
        mein Gerät bei *jedem* Gespräch abholt.

        Was damit möglich bleibt: wer mit mir in einer Gruppe ist oder mit mir
        schreibt, kann mein Lesefenster mit Steuerumschlägen füllen. Das konnte
        er vorher auch — über die Mailbox, die ich ohnehin lese.

        Liefert die Konto-Id des Empfängers oder `None`, wenn die Kennung zu
        keiner erreichbaren Geräte-Mailbox gehört.
        """
        kandidaten: set[int] = set()

        gruppen = [
            gid
            for (gid,) in db.query(ChatGroupMember.group_id)
            .filter(ChatGroupMember.user_id == sender_user_id)
            .all()
        ]
        if gruppen:
            kandidaten.update(
                uid
                for (uid,) in db.query(ChatGroupMember.user_id)
                .filter(
                    ChatGroupMember.group_id.in_(gruppen),
                    ChatGroupMember.user_id != sender_user_id,
                )
                .all()
            )

        for freundschaft in (
            db.query(UserFriend)
            .filter(
                or_(
                    UserFriend.user_id == sender_user_id,
                    UserFriend.friend_id == sender_user_id,
                ),
                UserFriend.status == "accepted",
            )
            .all()
        ):
            kandidaten.add(
                freundschaft.friend_id
                if freundschaft.user_id == sender_user_id
                else freundschaft.user_id
            )

        for uid in kandidaten:
            if cls.derive_user_device_mailbox_id(uid) == clean_mailbox:
                # Eine Blockierung schneidet auch diesen Weg ab. Sonst wäre er
                # die Hintertür, durch die ein Blockierter weiter zustellt.
                if cls.is_blocked(db, sender_user_id, uid):
                    return None
                return uid

        # Die dritte Quelle, ohne dass jemand aufschreibt, wer mit wem schreibt.
        #
        # Bis Stufe 6b standen hier die Konto-Ids aus `direct_chats`. Jetzt wird
        # es andersherum gerechnet: erst das Konto bestimmen, zu dem diese
        # Geräte-Mailbox gehört, dann fragen, ob es zwischen ihm und dem
        # Absender eine Gesprächszeile gibt. Die Zeile wird über die Kennung
        # gefunden, die das Paar ergäbe — beide Ids liegen in diesem Moment vor,
        # und der Server erfährt nichts, was er nicht ohnehin in Händen hält.
        #
        # Der Unterschied zur alten Fassung ist keiner in der Antwort, sondern
        # in der Frage: „gehören diese beiden zusammen?" lässt sich beantworten,
        # „mit wem gehört Konto 42 zusammen?" nicht mehr.
        for (uid,) in (
            db.query(User.id)
            .filter(User.is_active == True, User.id != sender_user_id)  # noqa: E712
            .all()
        ):
            if cls.derive_user_device_mailbox_id(uid) != clean_mailbox:
                continue
            if cls.is_blocked(db, sender_user_id, uid):
                return None
            return uid if cls._direktchat_zeile(db, sender_user_id, uid) else None
        return None

    @classmethod
    def can_message_user(cls, db: Session, sender_id: int, target_user_id: int) -> tuple[bool, str | None]:
        """Prüft Berechtigung zum Senden von Direktnachrichten zwischen zwei Benutzern.

        Regeln:
        1. Selbstgespräche nicht erlaubt.
        2. Blockierung verbietet jegliche Kommunikation.
        3. Existiert bereits ein Chat, darf geantwortet/weitergeschrieben werden (in beide Richtungen).
        4. Bestätigte Freunde dürfen sich immer schreiben.
        5. Nicht-Freunde dürfen schreiben, wenn der Empfänger ein öffentliches Profil hat.
        6. Ansonsten (Empfänger ist 'private' oder 'friends' ohne bestehenden Chat) verboten.
        """
        if sender_id == target_user_id:
            return False, "Selbstgespräche werden nicht unterstützt."

        # Prüfe auf Blockierung
        blocked = (
            db.query(UserFriend)
            .filter(
                or_(
                    and_(UserFriend.user_id == sender_id, UserFriend.friend_id == target_user_id),
                    and_(UserFriend.user_id == target_user_id, UserFriend.friend_id == sender_id),
                ),
                UserFriend.status == "blocked",
            )
            .first()
        )
        if blocked:
            return False, "Kommunikation nicht möglich: Benutzer ist blockiert."

        # Prüfe ob bereits ein DirectChat existiert (Antwort-Erlaubnis in beide
        # Richtungen). Gesucht wird über die Kennung, die dieses Paar ergibt —
        # seit Stufe 6b stehen in der Zeile keine Konto-Ids mehr.
        if cls._direktchat_zeile(db, sender_id, target_user_id):
            return True, None

        # Prüfe auch, ob schon Umschläge in der abgeleiteten Mailbox existieren
        derived_mid = cls.derive_blind_mailbox_id(sender_id, target_user_id)
        existing_envelope = (
            db.query(E2eeBlindEnvelope)
            .filter(E2eeBlindEnvelope.blind_mailbox_id == derived_mid)
            .first()
        )
        if existing_envelope:
            return True, None

        # Prüfe ob Freunde
        if cls.is_confirmed_friend(db, sender_id, target_user_id):
            return True, None

        # Empfänger laden
        target = db.query(User).filter_by(id=target_user_id).first()
        if not target or not target.is_active:
            return False, "Empfänger nicht gefunden oder inaktiv."

        target_privacy = getattr(target, "social_privacy", "friends")
        if target_privacy == "public":
            return True, None

        return False, "Dieser Benutzer nimmt Nachrichten nur von bestätigten Freunden an."

    @classmethod
    def ensure_direct_chat(cls, db: Session, sender_id: int, target_user_id: int) -> DirectChat:
        """Stellt sicher, dass ein DirectChat-Eintrag existiert, sofern die Berechtigung vorliegt."""
        can_msg, reason = cls.can_message_user(db, sender_id, target_user_id)
        if not can_msg:
            raise HTTPException(status_code=403, detail=reason or "Keine Berechtigung zum Senden einer Nachricht.")

        blind_mid = cls.derive_blind_mailbox_id(sender_id, target_user_id)
        chat = db.query(DirectChat).filter_by(blind_mailbox_id=blind_mid).first()
        if not chat:
            # Seit Stufe 6b ohne `user_a_id`, `user_b_id` und
            # `initiated_by_user_id`: die Zeile sagt „dieses Gespräch gibt es",
            # nicht „diese beiden führen es".
            chat = DirectChat(
                blind_mailbox_id=blind_mid,
                created_at=_now(),
                updated_at=_now(),
            )
            try:
                db.add(chat)
                db.commit()
                db.refresh(chat)
            except Exception:
                # Zwei gleichzeitige Erstnachrichten laufen in den eindeutigen
                # Riegel auf `blind_mailbox_id`. Der Verlierer nimmt die Zeile
                # des Gewinners.
                db.rollback()
                chat = db.query(DirectChat).filter_by(blind_mailbox_id=blind_mid).first()
                if not chat:
                    raise
        else:
            chat.updated_at = _now()
            db.commit()
        return chat
