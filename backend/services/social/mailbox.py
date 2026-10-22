from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import logging
from typing import Any

from fastapi import HTTPException
from sqlalchemy import and_, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import (
    User,
    UserFriend,
    E2eeBlindEnvelope,
    ChatGroupMember,
)
from services.sync_event_service import SyncEventService
from services.achievement_service import AchievementService
from services.notification_service import NotificationService
from services import webpush_service
from services.social.basis import _now
from services.social.grenzen import (
    E2EE_ENVELOPE_RETENTION_DAYS,
    E2EE_RELAY_KIB_PRO_TAG,
    aktuelle_grenze,
)

logger = logging.getLogger(__name__)


class MailboxMixin:
    @classmethod
    def relay_blind_envelope(
        cls,
        db: Session,
        blind_mailbox_id: str,
        ciphertext_envelope: str,
        sender_user_id: int | None = None,
        client_uuid: str | None = None,
        is_control: bool = False,
        control_type: str | None = None,
        mailbox_token: str | None = None,
        push_ausnahme: str | None = None,
    ) -> E2eeBlindEnvelope:

        """Speichert einen blinden E2EE-Umschlag mit serverseitiger Berechtigungsprüfung.

        sender_user_id wird im SSE-Event mitgeliefert, damit Outgoing Echo
        Prevention greift. Eine Empfängerkennung nimmt diese Stelle nicht mehr
        entgegen — was der Server über das Ziel weiss, rechnet er selbst aus
        oder er weiss es nicht.
        client_uuid garantiert Idempotenz bei Netzwerk-Schwankungen und Retries.

        `push_ausnahme` ist der SHA-256 der eigenen Push-Adresse des Absenders.
        Auf dem kontogebundenen Weg braucht es ihn nicht — dort erkennt
        `is_outgoing_echo` den Absender an seiner Kennung. Auf dem Mailbox-Weg
        gibt es keine Kennung mehr, an der man ihn erkennen könnte, und er
        bekäme sonst die Meldung über seine eigene Nachricht.
        """
        clean_mailbox = blind_mailbox_id.strip()
        clean_envelope = ciphertext_envelope.strip()
        clean_client_uuid = client_uuid.strip() if client_uuid and client_uuid.strip() else None

        from schemas.social import validate_e2ee_envelope_format
        try:
            validate_e2ee_envelope_format(clean_envelope)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        #: Bleibt `None`, wenn der Server zu dieser Mailbox kein Konto kennt.
        #: Dann läuft die Zustellung über das Abo — er weiss schlicht nicht,
        #: wer gemeint ist, und das ist der Normalfall.
        target_recipient_id: int | None = None

        if sender_user_id:
            target_recipient_id, _ = cls.resolve_mailbox_target(
                db,
                sender_user_id=sender_user_id,
                blind_mailbox_id=clean_mailbox,
                is_control=is_control,
            )
            if target_recipient_id is None:
                # Kein kontogebundenes Ziel. Früher war das immer ein 403, und
                # das musste es auch sein: jede Mailbox war aus kleinen
                # Ganzzahlen nachrechenbar, eine unbekannte Kennung also
                # entweder ein Tippfehler oder ein Versuch.
                #
                # Jetzt sind es zwei Fälle, und beide sind legitim:
                #
                # - Eine Kennung aus einem Geheimnis. Der Server kann sie
                #   niemandem zuordnen; der Besitznachweis ist dort nicht eine
                #   zusätzliche Schranke, sondern die einzige Berechtigung, die
                #   es überhaupt gibt.
                # - Eine Gruppen-Altkennung. Seit der Gruppen-Nachschlag weg
                #   ist, landet auch sie hier — und für sie gibt es keinen
                #   Nachweis, sondern nur die Mitgliedschaft.
                #
                # `hat_zugang` ist genau diese eine Tür: Nachweis **oder**
                # Teilnahme, und eine Mailbox mit hinterlegtem Nachweis öffnet
                # sich nicht mehr allein durch Mitgliedschaft. Der Unterschied
                # zum nachsichtigen `assert_mailbox_token` bleibt: eine
                # erfundene Kennung ohne beides ist weiter 403.
                if not cls.hat_zugang(db, sender_user_id, clean_mailbox, mailbox_token):
                    raise HTTPException(status_code=403, detail="Keine Berechtigung für diese Mailbox.")

        # Idempotenz-Prüfung: Erst NACH erfolgreicher Autorisierung prüfen,
        # ob dieser Umschlag bereits mit dieser client_uuid existiert.
        #
        # Dieselbe Prüfung stand bis 09/2026 zusätzlich **vor** dem
        # Berechtigungsblock. Damit war sie ein Weg daran vorbei: die
        # Mailbox-Kennung ist `sha256("msm:dm:<min>:<max>")` und für jeden
        # ausrechenbar, und wer mit einer passenden client_uuid ankam, bekam den
        # gespeicherten Umschlag zurück, ohne dass je geprüft wurde, ob er zu
        # diesem Gespräch gehört. Aufgefallen ist es, weil der zugehörige Test
        # aus dem falschen Grund grün war (sein Umschlag war schon formal
        # ungültig und flog früher raus).
        if clean_client_uuid:
            existing = (
                db.query(E2eeBlindEnvelope)
                .filter(
                    E2eeBlindEnvelope.blind_mailbox_id == clean_mailbox,
                    E2eeBlindEnvelope.client_uuid == clean_client_uuid,
                )
                .first()
            )
            if existing:
                return existing

        # Wiedereinspielung: derselbe Chiffretext, irgendwo schon einmal
        # gesehen. Zwei echte Verschlüsselungen desselben Textes ergeben nie
        # dasselbe Byte — der Ratchet zieht je Nachricht einen neuen Schlüssel,
        # AES-GCM eine neue Nonce. Ein Treffer ist also eine Kopie, kein Zufall.
        #
        # Die Reihenfolge ist wichtig: Der Wiederholungsversuch eines legitimen
        # Absenders trägt dieselbe client_uuid und ist eine Zeile weiter oben
        # schon beantwortet. Stünde diese Prüfung davor, bekäme jeder
        # Netzwerk-Retry eine 409 statt der Bestätigung.
        envelope_hash = hashlib.sha256(clean_envelope.encode("utf-8")).hexdigest()
        replay_found = (
            db.query(E2eeBlindEnvelope.id)
            .filter(
                or_(
                    E2eeBlindEnvelope.ciphertext_sha256 == envelope_hash,
                    and_(
                        E2eeBlindEnvelope.ciphertext_sha256.is_(None),
                        E2eeBlindEnvelope.ciphertext_envelope == clean_envelope,
                    ),
                )
            )
            .first()
        )
        if replay_found:
            raise HTTPException(
                status_code=409,
                detail="Replay-Angriff erkannt: Dieser verschlüsselte Umschlag wurde bereits übertragen.",
            )

        # Erst hier gezählt: ein Retry mit derselben client_uuid ist oben schon
        # beantwortet und kostet kein zweites Mal.
        if sender_user_id:
            from limits import parse
            from middleware.rate_limit import limiter

            kib = len(clean_envelope) // 1024 + 1
            if not limiter.limiter.hit(
                parse(f"{aktuelle_grenze('E2EE_RELAY_KIB_PRO_TAG', E2EE_RELAY_KIB_PRO_TAG)}/day"), f"e2ee-relay-kib:{sender_user_id}", cost=kib
            ):
                raise HTTPException(
                    status_code=429,
                    detail="Tageslimit für Nachrichten erreicht. Bitte morgen erneut versuchen.",
                )

        envelope = E2eeBlindEnvelope(
            blind_mailbox_id=clean_mailbox,
            ciphertext_envelope=clean_envelope,
            ciphertext_sha256=envelope_hash,
            client_uuid=clean_client_uuid,
            created_at=_now(),
        )
        db.add(envelope)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            if clean_client_uuid:
                existing = (
                    db.query(E2eeBlindEnvelope)
                    .filter(
                        E2eeBlindEnvelope.blind_mailbox_id == clean_mailbox,
                        E2eeBlindEnvelope.client_uuid == clean_client_uuid,
                    )
                    .first()
                )
                if existing:
                    return existing
            raise

        msg_payload = {
            "type": "e2ee_blind_message",
            "blind_mailbox_id": clean_mailbox,
            "id": envelope.id,
            "client_uuid": envelope.client_uuid,
            "created_at": envelope.created_at.isoformat(),
            "sender_user_id": sender_user_id,
            # Nur, was der Server selbst herausgefunden hat. Auf dem
            # Mailbox-Weg steht hier `None`, und das ist die Wahrheit: er
            # weiss nicht, wer gemeint ist.
            "recipient_id": target_recipient_id,
            "is_control": is_control,
            "control_type": control_type,
        }

        # Wie der Umschlag angekündigt wurde — für die eine Log-Zeile weiter
        # unten. `konto` heißt: der Server konnte das Ziel auflösen und weckt
        # dessen offene Verbindung; `mailbox` heißt: nur die Abonnenten dieser
        # Kennung erfahren davon (`SyncEventService.publish(mailbox_id=…)`).
        weckweg = "konto" if target_recipient_id else "mailbox"
        push_raus = False

        if target_recipient_id and sender_user_id:
            SyncEventService.publish(msg_payload, user_id=target_recipient_id)
            if target_recipient_id != sender_user_id:
                SyncEventService.publish(msg_payload, user_id=sender_user_id)
                # Zwei getrennte Schritte, und das mit Absicht: der erste
                # entscheidet, ob überhaupt jemand etwas erfahren darf (Echo,
                # Steuersignal, offener Tab) und streicht jeden Inhalt aus dem
                # Payload. Der zweite stellt zu. Was `prepare_push_dispatch`
                # herausgibt, ist deshalb schon bereinigt — dort steht nie mehr
                # als „Neue Nachricht".
                #
                # `sende_an_konto` kehrt sofort zurück und schickt im
                # Hintergrund. Ein langsamer Push-Dienst darf den Absender hier
                # nicht warten lassen.
                nutzlast = NotificationService.prepare_push_dispatch(
                    target_user_id=target_recipient_id,
                    sender_user_id=sender_user_id,
                    title="Neue Nachricht",
                    is_e2ee=True,
                    is_control=is_control,
                    control_type=control_type,
                    has_active_foreground_connection=SyncEventService.has_active_subscribers(target_recipient_id),
                )
                if nutzlast:
                    push_raus = True
                    webpush_service.sende_an_konto(db, target_recipient_id, nutzlast)
        else:
            # Kein kontogebundenes Ziel — die Mailbox **ist** die Adresse.
            #
            # Daneben stand bis 09/2026 ein eigener Gruppenzweig: der Server
            # schlug die Mitgliederliste nach und stellte Zeile für Zeile an
            # jedes Konto einzeln zu. Das war die teuerste Auskunft im ganzen
            # Messenger — bei **jeder** Nachricht zählte er auf, wer in dieser
            # Gruppe ist. Gruppen nehmen jetzt denselben Weg wie alles andere.
            #
            # Und hier selbst stand ein `publish(msg_payload)` ohne jede
            # Angabe, und das ist systemweit: jeder verbundene Client erfuhr,
            # dass in dieser Mailbox etwas liegt, samt Kennung und laufender
            # Nummer. Erreichbar war der Zweig nur ohne `sender_user_id`, also
            # selten — aber ausgerechnet in einem Messenger, der niemandem
            # verraten soll, wer wann mit wem spricht, war das die falsche
            # Voreinstellung.
            #
            # Zugestellt wird jetzt an die Abonnenten dieser Mailbox. Wer sie
            # nicht abonniert hat, erfährt nichts — und abonnieren darf nur,
            # wer teilnimmt oder den Besitznachweis hat.
            SyncEventService.publish(msg_payload, mailbox_id=clean_mailbox)

            # Und derselbe Weg für den geschlossenen Tab. `sende_an_mailbox`
            # fragt keine Konten ab — es gibt hier keines zu fragen, und genau
            # deshalb steht es hier: ein `sende_an_konto` an dieser Stelle wäre
            # die eine Zeile, die dem Server wieder verriete, wer Post in einer
            # Mailbox bekommt, die er sonst niemandem zuordnen kann.
            nutzlast = NotificationService.prepare_mailbox_push(
                is_control=is_control, control_type=control_type
            )
            if nutzlast:
                push_raus = True
                webpush_service.sende_an_mailbox(
                    db,
                    clean_mailbox,
                    nutzlast,
                    ausser_abdruck=push_ausnahme,
                )

        # Eine Zeile je eingeliefertem Umschlag — der Anfang eines
        # Nachrichtenwegs. Ohne sie steht im Log nur `POST
        # /api/social/e2ee/relay 200`, und niemand sieht, ob der Umschlag ins
        # Konto ging oder in eine abonnierte Mailbox, wie er angekündigt wurde
        # und ob der Absender bekannt war. Das **Abholen** steht bewusst nicht
        # hier: der offene Chat liest seine Mailbox alle fünf Sekunden, und jede
        # dieser Abfragen trägt dieselben Umschläge erneut — eine Zeile je
        # Abruf wäre keine Spur, sondern Rauschen. Wann der Empfänger gelesen
        # hat, steht als `GET /api/social/e2ee/mailbox/… 200` im Zugriffslog
        # (`docker logs -t`).
        #
        # Chiffretext steht nie im Log, die Kennung nur gekürzt: sie ist ein
        # Abdruck, und ein volles Protokoll davon wäre eine Landkarte der
        # Gespräche.
        logger.info(
            "E2EE-Umschlag eingeliefert mailbox=%s env=%s weg=%s absender=%s steuer=%s ziel=%s push=%s",
            clean_mailbox[:12],
            envelope.id,
            weckweg,
            sender_user_id,
            is_control,
            target_recipient_id,
            push_raus,
        )

        if sender_user_id and not is_control:
            # Nach der Zustellung: das Abzeichen-Ereignis darf der Nachricht
            # nicht vorauslaufen. Der Server weiß ohnehin, wer einwirft
            # (Anmeldung, Tageslimit) — es verrät nichts über die Gegenseite.
            if AchievementService.melde(db, sender_user_id, "social_zero_knowledge", commit=True):
                # Der Commit ließ den Umschlag verfallen; der WebSocket-Weg
                # liest ihn erst, wenn die Sitzung schon zu ist.
                db.refresh(envelope)

        return envelope

    @classmethod
    def delete_blind_envelopes(
        cls,
        db: Session,
        blind_mailbox_id: str,
        client_uuid: str,
        user_id: int,
        mailbox_token: str | None = None,
    ) -> int:
        """Entfernt die Umschläge einer Nachricht aus einer blinden Mailbox.

        Eine gelöschte Nachricht muss auch hier verschwinden. Bliebe der
        Chiffretext liegen, hätte das Löschen nur die Anzeige auf zwei Geräten
        geändert: ein neu eingerichtetes Gerät holte die Mailbox von vorn und
        bekäme sie zurück.

        Eine Nachricht liegt als mehrere Umschläge da, einer je Zielgerät,
        auseinandergehalten durch `<kennung>#<geraet>`. Gelöscht wird deshalb
        die logische Kennung samt aller Gerätekopien — und nur sie, nie die
        Mailbox als Ganzes.

        Grenze, die bleibt: Wer zu einer Mailbox gehört, kann darin jeden
        Umschlag löschen, dessen Kennung er kennt. Der Server kann das nicht
        enger fassen, ohne zu wissen, wer welchen Umschlag geschrieben hat — und
        genau das weiß er absichtlich nicht. In einem Direktchat trifft das nur
        Kopien, die der Löschende ohnehin schon hat; in einer Gruppe kann ein
        Mitglied damit eine fremde Nachricht aus dem Relais nehmen, bevor andere
        sie abholen. Das ist der Preis der blinden Adressierung und die
        Alternative wäre, den Absender an den Umschlag zu schreiben.

        Nebenwirkung: Ein gelöschter Umschlag fällt aus der
        Wiedereinspielungsprüfung. Wer denselben Chiffretext erneut einliefert,
        beschädigt damit nur seine eigene Sitzung — der Ratchet-Kopf adressiert
        ein einzelnes Gerät, und andere Geräte werten ihn als fremd.
        """
        cls.assert_social_enabled(db)
        clean_mailbox = (blind_mailbox_id or "").strip()
        clean_uuid = (client_uuid or "").strip()
        if not clean_uuid:
            raise HTTPException(status_code=400, detail="client_uuid fehlt.")

        cls.assert_mailbox_zugang(db, user_id, clean_mailbox, mailbox_token)

        rows = (
            db.query(E2eeBlindEnvelope)
            .filter(
                E2eeBlindEnvelope.blind_mailbox_id == clean_mailbox,
                or_(
                    E2eeBlindEnvelope.client_uuid == clean_uuid,
                    E2eeBlindEnvelope.client_uuid.startswith(f"{clean_uuid}#", autoescape=True),
                ),
            )
            .all()
        )
        for row in rows:
            db.delete(row)
        db.commit()
        return len(rows)

    @classmethod
    def cleanup_expired_envelopes(
        cls, db: Session, days: int = E2EE_ENVELOPE_RETENTION_DAYS
    ) -> int:
        """Löscht blinde Umschläge, die älter als `days` Tage sind (Default: 30 Tage).

        Zero-Knowledge / Vorhaltefrist-Garantie:
        Der Relais-Server speichert Umschläge maximal 30 Tage.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        deleted = (
            db.query(E2eeBlindEnvelope)
            .filter(E2eeBlindEnvelope.created_at < cutoff)
            .delete(synchronize_session=False)
        )
        if deleted > 0:
            db.commit()
            logger.info(
                "E2EE-Vorhaltefrist: %d abgelaufene Umschläge (> %d Tage) bereinigt.",
                deleted,
                days,
            )
        return deleted

    @classmethod
    def get_blind_envelopes(
        cls,
        db: Session,
        blind_mailbox_id: str,
        since_id: int = 0,
        limit: int = 50,
        oldest_first: bool = False,
    ) -> list[E2eeBlindEnvelope]:
        """Holt blinde Umschläge aus einer Mailbox ab (maximal 30 Tage alt).

        Ohne `since_id` gibt es zwei Lesarten, und beide werden gebraucht:

        * **die neuesten** (Vorgabe) — für ein Fenster, das den laufenden
          Verkehr zeigt;
        * **die ältesten** (`oldest_first`) — für einen Abgleich, der Seite für
          Seite alles nachholt. Wer bei 0 beginnt und die neuesten bekommt,
          springt mit `since_id` über den Rest hinweg: die nächste Seite ist
          leer, und alles dazwischen ist verloren. Genau das passierte einem
          Gerät, das eine Weile nicht offen war (`fetchAlleE2eeEnvelopes`).
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=E2EE_ENVELOPE_RETENTION_DAYS)
        query = db.query(E2eeBlindEnvelope).filter(
            E2eeBlindEnvelope.blind_mailbox_id == blind_mailbox_id,
            E2eeBlindEnvelope.created_at >= cutoff,
        )
        if since_id > 0:
            query = query.filter(E2eeBlindEnvelope.id > since_id)
            return query.order_by(E2eeBlindEnvelope.id.asc()).limit(min(limit, 200)).all()

        total = query.count()
        fetch_limit = min(limit, 200)
        if total > fetch_limit and not oldest_first:
            sub = query.order_by(E2eeBlindEnvelope.id.desc()).limit(fetch_limit).subquery()
            from sqlalchemy import select
            return (
                db.query(E2eeBlindEnvelope)
                .filter(E2eeBlindEnvelope.id.in_(select(sub.c.id)))
                .order_by(E2eeBlindEnvelope.id.asc())
                .all()
            )
        return query.order_by(E2eeBlindEnvelope.id.asc()).limit(fetch_limit).all()

    @classmethod
    def sync_mailboxes(
        cls, db: Session, current_user: User, since_id: int = 0
    ) -> list[dict[str, Any]]:
        """Ermittelt alle blinden Mailboxen des Benutzers mit Umschlägen nach since_id.

        Zero-Knowledge Invariante:
        Es werden AUSSCHLIESSLICH Mailboxen zurückgegeben, an denen der Benutzer
        nachweislich beteiligt ist (ChatGroupMember oder bestätigter UserFriend).

        **Die Direktchats fehlen hier seit Stufe 6b.** Sie kamen aus
        `direct_chats.user_a_id`/`user_b_id` — der Abfrage „mit wem schreibt
        Konto 42?", die es nicht mehr geben soll. Was bleibt, sind die
        Gruppen, die Freunde und die eigene Geräte-Mailbox; alles andere
        abonniert der Client selbst über `erlaubte_mailboxen`, wo er seine
        Kennungen nennt und der Server nur prüft, statt sie aufzuzählen.
        """
        cls.assert_social_enabled(db)
        uid = current_user.id
        effective_since = max(0, since_id) if since_id is not None else 0

        # 2. Chat-Gruppen des Benutzers
        group_ids = [
            row[0]
            for row in db.query(ChatGroupMember.group_id)
            .filter(ChatGroupMember.user_id == uid)
            .all()
        ]
        group_mids = [
            hashlib.sha256(f"msm:group:{gid}".encode("utf-8")).hexdigest()
            for gid in group_ids
        ]

        # 3. Bestätigte Freunde (zur Absicherung vor persistiertem DirectChat)
        friends = (
            db.query(UserFriend)
            .filter(
                or_(UserFriend.user_id == uid, UserFriend.friend_id == uid),
                UserFriend.status == "accepted",
            )
            .all()
        )
        friend_ids = {f.friend_id if f.user_id == uid else f.user_id for f in friends}
        friend_mids = [
            cls.derive_blind_mailbox_id(uid, fid)
            for fid in friend_ids
        ]

        device_mid = cls.derive_user_device_mailbox_id(uid)
        all_mids = list(set(group_mids) | set(friend_mids) | {device_mid})
        if not all_mids:
            return []

        # 4. Aggregiere Mailbox-Statistiken über E2eeBlindEnvelope in Batches à 500
        cutoff = datetime.now(timezone.utc) - timedelta(days=E2EE_ENVELOPE_RETENTION_DAYS)
        results: list[dict[str, Any]] = []
        chunk_size = 500
        for i in range(0, len(all_mids), chunk_size):
            chunk = all_mids[i : i + chunk_size]
            rows = (
                db.query(
                    E2eeBlindEnvelope.blind_mailbox_id,
                    func.max(E2eeBlindEnvelope.id).label("max_envelope_id"),
                    func.count(E2eeBlindEnvelope.id).label("unread_count"),
                )
                .filter(
                    E2eeBlindEnvelope.blind_mailbox_id.in_(chunk),
                    E2eeBlindEnvelope.id > effective_since,
                    E2eeBlindEnvelope.created_at >= cutoff,
                )
                .group_by(E2eeBlindEnvelope.blind_mailbox_id)
                .all()
            )
            for mid, max_id, count in rows:
                results.append({
                    "blind_mailbox_id": mid,
                    "max_envelope_id": int(max_id),
                    "unread_count": int(count),
                })

        results.sort(key=lambda item: item["max_envelope_id"], reverse=True)
        return results

    @classmethod
    def broadcast_typing_signal(
        cls,
        blind_mailbox_id: str,
        status: str,
        sender_id: int,
        sender_username: str,
        db: Session | None = None,
    ) -> None:
        """Verteilt ein flüchtiges Tipp- oder Sprachaufnahme-Signal ohne Speicherung.

        Nimmt keine Empfängerkennung mehr entgegen. Bis 09/2026 war ein
        genanntes `recipient_id` der ganze Weg: es sprang an der
        Mailbox-Auflösung vorbei, und die einzige verbleibende Prüfung war die
        Blockierung. Jedes angemeldete Konto konnte damit jedem anderen
        „tippt gerade" schicken, mit einer frei erfundenen Kennung. Wer das
        Signal bekommt, entscheidet jetzt allein die Mailbox.
        """
        clean_mailbox = blind_mailbox_id.strip()

        target_recipient_id: int | None = None
        if db:
            try:
                target_recipient_id, _ = cls.resolve_mailbox_target(
                    db,
                    sender_user_id=sender_id,
                    blind_mailbox_id=clean_mailbox,
                    lege_chat_an=False,
                )
            except HTTPException:
                return

            if target_recipient_id and cls.is_blocked(db, sender_id, target_recipient_id):
                return

        payload = {
            "type": "e2ee_typing_signal",
            "blind_mailbox_id": clean_mailbox,
            "status": status,
            "sender_id": sender_id,
            "sender_username": sender_username,
        }

        if target_recipient_id:
            SyncEventService.publish(payload, user_id=target_recipient_id)
        else:
            # An die Abonnenten dieser Mailbox, an niemanden sonst.
            #
            # Hier stand ein `publish(payload)` ohne Angabe — systemweit. Und
            # anders als im Relais war dieser Zweig von aussen erreichbar:
            # `resolve_mailbox_target` kehrt bei einer Kennung, die zu nichts
            # gehört, ohne Ausnahme zurück (`None, [], None, None`), und
            # `broadcast_typing_signal` fängt nur `HTTPException`. Ein
            # Tippsignal auf eine erfundene Kennung ging damit an **jeden**
            # verbundenen Client — samt `sender_id` und `sender_username`.
            #
            # Das ist genau die Präsenzauskunft, die der Messenger sonst
            # sorgfältig auf die Gegenseite beschränkt.
            SyncEventService.publish(payload, mailbox_id=clean_mailbox)
