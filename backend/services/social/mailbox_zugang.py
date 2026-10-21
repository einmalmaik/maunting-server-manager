from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
import hashlib
import logging
import secrets

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import (
    User,
    UserFriend,
    E2eeBlindEnvelope,
    E2eeBlindMailbox,
    ChatGroup,
    ChatGroupMember,
    DirectChat,
)

logger = logging.getLogger(__name__)


class MailboxZugangMixin:
    @classmethod
    def assert_mailbox_participant(cls, db: Session, user_id: int, blind_mailbox_id: str) -> None:
        """Prüft, ob ein Konto zu einer blinden Mailbox gehört.

        Die Mailbox nennt ihre Teilnehmer nicht, das ist ihr Zweck. Geprüft wird
        deshalb andersherum: aus den Direktchats, Gruppen und Freundschaften
        dieses Kontos werden dieselben Kennungen abgeleitet, die auch der Client
        rechnet. Ist die gesuchte nicht darunter, gehört das Konto nicht dazu.

        Die eigene Geräte-Mailbox zählt mit, eine fremde nie: deren Kennung
        entsteht aus einer anderen Konto-Id und taucht in keiner der Ableitungen
        hier auf.
        """
        clean = (blind_mailbox_id or "").strip()
        if not clean:
            raise HTTPException(status_code=400, detail="Mailbox-ID fehlt.")

        if clean == cls.derive_user_device_mailbox_id(user_id):
            return

        for (gid,) in (
            db.query(ChatGroupMember.group_id).filter(ChatGroupMember.user_id == user_id).all()
        ):
            if cls.derive_group_blind_mailbox_id(gid) == clean:
                return

        friends = (
            db.query(UserFriend)
            .filter(
                or_(UserFriend.user_id == user_id, UserFriend.friend_id == user_id),
                UserFriend.status == "accepted",
            )
            .all()
        )
        for f in friends:
            other = f.friend_id if f.user_id == user_id else f.user_id
            if cls.derive_blind_mailbox_id(user_id, other) == clean:
                return

        # Zuletzt, weil am teuersten: der Direktchat. Bis Stufe 6b ein
        # Nachschlag in `direct_chats`, jetzt eine Rechnung.
        # `gegenueber_aus_mailbox` leitet die Kennung für jedes aktive Konto ab
        # und vergleicht — dieselbe Antwort, ohne dass jemand aufschreiben
        # müsste, wer mit wem schreibt.
        #
        # Die Zeile wird zusätzlich verlangt: eine abgeleitete Kennung passt
        # rechnerisch zu jedem Paar, auch zu einem, das nie miteinander zu tun
        # hatte. Ohne sie wäre jede Konto-Kombination eine offene Tür.
        gegenueber = cls.gegenueber_aus_mailbox(db, user_id, clean)
        if gegenueber is not None and cls._direktchat_zeile(db, user_id, gegenueber):
            return

        raise HTTPException(status_code=403, detail="Keine Berechtigung für diese Mailbox.")

    @staticmethod
    def _verifier_von(auth_token: str) -> str:
        """Was der Server von einem Token behält: seinen SHA-256, sonst nichts."""
        return hashlib.sha256(auth_token.strip().lower().encode("utf-8")).hexdigest()

    @classmethod
    def _mailbox_ist_ableitbar(cls, db: Session, mailbox_id: str) -> bool:
        """Gehört diese Kennung zu einer Mailbox, die der Server selbst ausrechnen kann?

        Gemeint sind die Altkennungen: `sha256("msm:group:<id>")`,
        `sha256("msm:devices:<id>")` und die in `direct_chats` hinterlegten. Sie
        sind aus kleinen Ganzzahlen nachrechenbar — und genau deshalb darf sie
        nicht der Nächstbeste mit einem Besitznachweis belegen. Täte er es,
        wäre das eine Sperre gegen die echten Mitglieder, ohne je etwas
        entschlüsselt zu haben.

        Eine Kennung aus einem Gruppengeheimnis taucht hier nie auf. Sie ist
        unableitbar, und das ist der Unterschied, an dem diese Prüfung hängt.

        Der Preis ist ein Durchlauf über die Gruppen- und Konto-Ids. Er läuft
        nur beim Registrieren einer noch leeren, unbeanspruchten Mailbox —
        nicht im Sende- oder Lesepfad.
        """
        if db.query(DirectChat).filter_by(blind_mailbox_id=mailbox_id).first():
            return True
        for (gid,) in db.query(ChatGroup.id).all():
            if cls.derive_group_blind_mailbox_id(gid) == mailbox_id:
                return True
        for (uid,) in db.query(User.id).all():
            if cls.derive_user_device_mailbox_id(uid) == mailbox_id:
                return True
        return False

    @classmethod
    def register_blind_mailbox(
        cls, db: Session, user_id: int, mailbox_id: str, auth_token: str
    ) -> None:
        """Hinterlegt den Besitznachweis einer Mailbox — der authentifizierte Übergang.

        Drei Regeln, und jede schließt eine Tür, die sonst offen stünde:

        - **Ein hinterlegter Nachweis wird nie überschrieben.** Derselbe Token
          ist idempotent, ein anderer ist 409. Sonst wäre ein übernommenes
          Panel-Konto ein Generalschlüssel für jede Mailbox — dieselbe Lehre
          wie beim Tresor.
        - **Eine Mailbox mit Umschlägen gehört nie dem Nächstbesten.** Wer für
          sie registrieren will, muss nach den heutigen Regeln dazugehören.
          Das ist der Weg für den Bestand: eine gewachsene Gruppe bekommt
          ihren Nachweis von einem echten Mitglied.
        - **Eine ableitbare Kennung trägt nie einen Nachweis**, auch nicht von
          einem Mitglied. Dort entscheidet die Teilnahme, und der Client
          registriert sie nie (`sichereBesitznachweis` in
          `gruppenSchluessel.ts`). Ein Nachweis dort sperrte nur die übrigen
          Teilnehmer aus.

          Was der Server hier nicht prüfen kann, sind Kennungen von Konten,
          Gruppen und Paaren, die es noch nicht gibt: `msm:devices:<n+1>` ist
          heute ausrechenbar, gehört aber noch niemandem. Belegt sie jemand,
          fällt der Nachweis in `assert_mailbox_token`, sobald ein echtes
          Konto die Kennung ohne ihn anfasst.

        Was übrig bleibt, ist der Normalfall der Stufe 3: eine unableitbare,
        leere Kennung aus einem frisch erzeugten Gruppengeheimnis. Die darf,
        wer angemeldet ist — dem Server ist sie nicht zuzuordnen, und sie
        enthält nichts, was jemandem gehören könnte.
        """
        clean = (mailbox_id or "").strip().lower()
        if not clean:
            raise HTTPException(status_code=400, detail="Mailbox-ID fehlt.")
        verifier = cls._verifier_von(auth_token)

        vorhanden = db.get(E2eeBlindMailbox, clean)
        if vorhanden is not None:
            if secrets.compare_digest(vorhanden.auth_verifier, verifier):
                return
            raise HTTPException(
                status_code=409, detail="Für diese Mailbox ist bereits ein Besitznachweis hinterlegt."
            )

        if cls._ist_ableitbar_fuer(db, user_id, clean):
            raise HTTPException(status_code=403, detail="Keine Berechtigung für diese Mailbox.")
        hat_umschlaege = (
            db.query(E2eeBlindEnvelope.id).filter(E2eeBlindEnvelope.blind_mailbox_id == clean).first()
            is not None
        )
        if hat_umschlaege:
            cls.assert_mailbox_participant(db, user_id, clean)

        db.add(
            E2eeBlindMailbox(
                mailbox_id=clean,
                auth_verifier=verifier,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )
        try:
            db.commit()
        except IntegrityError:
            # Zwei Geräte haben gleichzeitig registriert. Derselbe Token ist
            # kein Fehler, ein anderer schon.
            db.rollback()
            nun = db.get(E2eeBlindMailbox, clean)
            if nun is None or not secrets.compare_digest(nun.auth_verifier, verifier):
                raise HTTPException(
                    status_code=409,
                    detail="Für diese Mailbox ist bereits ein Besitznachweis hinterlegt.",
                )

    @classmethod
    def _ist_ableitbar_fuer(cls, db: Session, user_id: int, mailbox_id: str) -> bool:
        """Kann der Server diese Kennung ausrechnen, aus Sicht dieses Kontos?

        `_mailbox_ist_ableitbar` kennt Geräte-, Gruppen- und hinterlegte
        Direktchat-Kennungen. Dazu kommt das Paar aus diesem Konto und jedem
        anderen: ein Erstkontakt hat noch keine Zeile in `direct_chats`.
        """
        return (
            cls._mailbox_ist_ableitbar(db, mailbox_id)
            or cls.gegenueber_aus_mailbox(db, user_id, mailbox_id) is not None
        )

    @classmethod
    def assert_mailbox_token(
        cls, db: Session, mailbox_id: str, auth_token: str | None, user_id: int | None = None
    ) -> None:
        """Prüft den Besitznachweis, falls für diese Mailbox einer hinterlegt ist.

        Ohne hinterlegten Nachweis tut diese Prüfung nichts — der Bestand läuft
        weiter wie bisher und wird von `assert_mailbox_participant` gehalten.
        Ist einer hinterlegt, gilt er: ein fehlendes oder falsches Token ist
        403, und zwar bevor irgendetwas gelesen, geschrieben oder gelöscht
        wird.

        Verglichen wird mit `compare_digest`. Ein Vergleich, der beim ersten
        abweichenden Zeichen abbricht, verrät über viele Versuche den Verifier
        — und der ist hier das Einzige, was es zu erraten gibt.
        """
        clean = (mailbox_id or "").strip().lower()
        if not clean:
            return
        eintrag = db.get(E2eeBlindMailbox, clean)
        if eintrag is None:
            return
        if auth_token and secrets.compare_digest(eintrag.auth_verifier, cls._verifier_von(auth_token)):
            return
        # Ein Nachweis auf einer ausrechenbaren Kennung ist angemaßt: der
        # Client registriert dort nie (siehe `register_blind_mailbox`). Belegt
        # hat sie jemand, als es Konto, Gruppe oder Paar noch nicht gab, um
        # die späteren Teilnehmer auszusperren. Er fällt hier weg; ob der
        # Anfragende hinein darf, prüft danach wie immer die Teilnahme.
        if user_id is not None and cls._ist_ableitbar_fuer(db, user_id, clean):
            logger.warning("Angemaßten Besitznachweis auf einer ableitbaren Mailbox entfernt.")
            db.delete(eintrag)
            db.commit()
            return
        raise HTTPException(status_code=403, detail="Kein gültiger Besitznachweis für diese Mailbox.")

    @classmethod
    def hat_gueltigen_nachweis(
        cls, db: Session, mailbox_id: str, auth_token: str | None
    ) -> bool:
        """Liegt für diese Mailbox ein Nachweis vor, und stimmt das Token?

        Der Unterschied zu `assert_mailbox_token` ist der Fall „kein Nachweis
        hinterlegt": dort ist er ein stilles Ja (der Bestand läuft weiter),
        hier ein klares Nein. Denn wer hiermit fragt, will keine Schranke
        prüfen — er will wissen, ob der Nachweis eine **Eintrittskarte** ist.

        Gebraucht wird das für Mailboxen, die der Server nicht ausrechnen
        kann: dort gibt es keine Mitgliedschaft nachzuschlagen, und dann ist
        der Nachweis die einzige Berechtigung, die es überhaupt gibt. Ein
        stilles Ja wäre dort ein offenes Tor für jede erfundene Kennung.
        """
        clean = (mailbox_id or "").strip().lower()
        if not clean or not auth_token:
            return False
        eintrag = db.get(E2eeBlindMailbox, clean)
        if eintrag is None:
            return False
        return secrets.compare_digest(eintrag.auth_verifier, cls._verifier_von(auth_token))

    @classmethod
    def hat_zugang(cls, db: Session, user_id: int, mailbox_id: str, token: str | None) -> bool:
        """Darf dieses Konto diese eine Mailbox öffnen?

        **Die eine Tür.** Es gibt zwei Arten hineinzukommen, und beide stehen
        nur hier: das Konto gehört zur Mailbox (der Bestand), oder es legt den
        Besitznachweis vor (das Neue). Jede Route, die eine einzelne Mailbox
        aufschließt, fragt hier — und `erlaubte_mailboxen` fragt für Listen
        dasselbe.

        Der zweite Weg ist der Grund, warum Stufe 3 überhaupt funktionieren
        kann: sobald die Kennung aus einem Gruppengeheimnis fällt, **kann** der
        Server die Mitgliedschaft nicht mehr nachschlagen. Er weiß nicht, wem
        die Mailbox gehört, und soll es nicht wissen. Bliebe es beim
        Teilnehmerschloss allein, wäre die neue Kennung für jeden verschlossen,
        auch für ihre Besitzer.

        Der erste Weg bleibt, solange es ableitbare Kennungen gibt. Er ist
        zugleich die Schranke, die ein hinausgeworfenes Mitglied hält: das
        Gruppengeheimnis kennt es noch, den Nachweis kann es sich ausrechnen —
        erst MLS trägt die Zugehörigkeit kryptographisch.
        """
        mid = (mailbox_id or "").strip()
        if not mid:
            return False
        if cls.hat_gueltigen_nachweis(db, mid, token):
            return True
        try:
            cls.assert_mailbox_participant(db, user_id, mid)
            # Eine Mailbox mit hinterlegtem Nachweis öffnet sich nicht allein
            # durch Mitgliedschaft — sonst wäre der eine Weg die Hintertür
            # neben der verschlossenen Vordertür des anderen.
            cls.assert_mailbox_token(db, mid, token, user_id)
        except HTTPException:
            return False
        return True

    @classmethod
    def assert_mailbox_zugang(
        cls, db: Session, user_id: int, mailbox_id: str, token: str | None
    ) -> None:
        """`hat_zugang`, aber mit 403 statt `False`.

        **Eine Antwort für jeden Grund.** 403 ist, was
        `assert_mailbox_participant` schon immer für einen Fremden gab; dass
        auch der fehlende und der falsche Nachweis so antworten, ist der
        Zugewinn: vorher unterschied sich „kein Mitglied" (403 aus der
        Teilnehmerprüfung) nicht von „verschlossen" (403 aus der
        Nachweisprüfung) nur zufällig — jetzt kann es sich gar nicht mehr
        unterscheiden. Aus der Antwort ist nicht zu lesen, ob es diese Mailbox
        gibt, ob sie einen Nachweis trägt oder wer dazugehört.
        """
        if not cls.hat_zugang(db, user_id, mailbox_id, token):
            raise HTTPException(status_code=403, detail="Keine Berechtigung für diese Mailbox.")

    @classmethod
    def erlaubte_mailboxen(
        cls, db: Session, user_id: int, eintraege: Iterable[tuple[str, str | None]]
    ) -> list[str]:
        """Filtert eine Wunschliste von Mailboxen auf die erlaubten.

        Die Tür, durch die ein Client sagt „darüber will ich Bescheid wissen".
        Es gibt zwei davon in den Routern — den Echtzeitstrom und die
        Push-Adresse —, und sie müssen dieselbe Prüfung haben. Stünde sie
        zweimal da, wäre die zweite Fassung irgendwann die nachsichtigere, und
        Push wäre der Weg, über den man erfährt, was der Strom einem nicht sagt.

        Geprüft wird je Kennung mit `hat_zugang` — dieselbe Tür, durch die auch
        eine einzelne Mailbox aufgeht.

        **Was durchfällt, fällt still durch.** Die Antwort nennt nur, was
        erlaubt ist, nie warum etwas fehlt — eine einzelne Ablehnung wäre eine
        Auskunft darüber, welche Mailboxen es gibt.
        """
        erlaubt: list[str] = []
        for kennung, token in eintraege:
            mid = (kennung or "").strip()
            if mid and cls.hat_zugang(db, user_id, mid, token):
                erlaubt.append(mid)
        return erlaubt
