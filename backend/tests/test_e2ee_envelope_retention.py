from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy.orm import Session

from models import E2eeBlindEnvelope, User, UserFriend
from services.social_service import SocialService, E2EE_ENVELOPE_RETENTION_DAYS


def _now() -> datetime:
    return datetime.now(timezone.utc)


def test_retention_days_constant():
    assert E2EE_ENVELOPE_RETENTION_DAYS == 30


def test_get_blind_envelopes_filters_expired(db: Session):
    box = "box-retention-test-1"
    now = _now()

    # Frisch (heute)
    env_fresh = E2eeBlindEnvelope(
        blind_mailbox_id=box,
        ciphertext_envelope="ciphertext-fresh",
        client_uuid="uuid-fresh",
        created_at=now,
    )
    # 10 Tage alt (gueltig)
    env_10d = E2eeBlindEnvelope(
        blind_mailbox_id=box,
        ciphertext_envelope="ciphertext-10d",
        client_uuid="uuid-10d",
        created_at=now - timedelta(days=10),
    )
    # 29 Tage alt (an der Grenze, gueltig)
    env_29d = E2eeBlindEnvelope(
        blind_mailbox_id=box,
        ciphertext_envelope="ciphertext-29d",
        client_uuid="uuid-29d",
        created_at=now - timedelta(days=29),
    )
    # 31 Tage alt (abgelaufen)
    env_31d = E2eeBlindEnvelope(
        blind_mailbox_id=box,
        ciphertext_envelope="ciphertext-31d",
        client_uuid="uuid-31d",
        created_at=now - timedelta(days=31),
    )
    # 60 Tage alt (abgelaufen)
    env_60d = E2eeBlindEnvelope(
        blind_mailbox_id=box,
        ciphertext_envelope="ciphertext-60d",
        client_uuid="uuid-60d",
        created_at=now - timedelta(days=60),
    )

    db.add_all([env_fresh, env_10d, env_29d, env_31d, env_60d])
    db.commit()

    res = SocialService.get_blind_envelopes(db, box)
    uuids = [e.client_uuid for e in res]

    assert "uuid-fresh" in uuids
    assert "uuid-10d" in uuids
    assert "uuid-29d" in uuids
    assert "uuid-31d" not in uuids
    assert "uuid-60d" not in uuids


def test_cleanup_expired_envelopes(db: Session):
    box = "box-cleanup-test"
    now = _now()

    env_fresh = E2eeBlindEnvelope(
        blind_mailbox_id=box,
        ciphertext_envelope="ciphertext-fresh",
        client_uuid="uuid-clean-fresh",
        created_at=now,
    )
    env_expired_1 = E2eeBlindEnvelope(
        blind_mailbox_id=box,
        ciphertext_envelope="ciphertext-exp-1",
        client_uuid="uuid-clean-exp1",
        created_at=now - timedelta(days=31),
    )
    env_expired_2 = E2eeBlindEnvelope(
        blind_mailbox_id=box,
        ciphertext_envelope="ciphertext-exp-2",
        client_uuid="uuid-clean-exp2",
        created_at=now - timedelta(days=45),
    )

    db.add_all([env_fresh, env_expired_1, env_expired_2])
    db.commit()

    deleted = SocialService.cleanup_expired_envelopes(db, days=30)
    assert deleted == 2

    # Verbleibende pruefen
    remaining = db.query(E2eeBlindEnvelope).filter_by(blind_mailbox_id=box).all()
    assert len(remaining) == 1
    assert remaining[0].client_uuid == "uuid-clean-fresh"


def test_sync_mailboxes_respects_30_days_retention(db: Session, clean_db):
    user_a = User(username="user_a", email="a@msm.local", password_hash="pw", is_active=True)
    user_b = User(username="user_b", email="b@msm.local", password_hash="pw", is_active=True)
    db.add_all([user_a, user_b])
    db.commit()
    db.refresh(user_a)
    db.refresh(user_b)

    friendship = UserFriend(user_id=user_a.id, friend_id=user_b.id, status="accepted")
    db.add(friendship)
    db.commit()

    box = SocialService.derive_blind_mailbox_id(user_a.id, user_b.id)
    now = _now()

    # 1 gueltiger Umschlag und 1 abgelaufener Umschlag
    env_valid = E2eeBlindEnvelope(
        blind_mailbox_id=box,
        ciphertext_envelope="valid",
        client_uuid="valid-sync",
        created_at=now - timedelta(days=5),
    )
    env_old = E2eeBlindEnvelope(
        blind_mailbox_id=box,
        ciphertext_envelope="old",
        client_uuid="old-sync",
        created_at=now - timedelta(days=35),
    )
    db.add_all([env_valid, env_old])
    db.commit()

    boxes = SocialService.sync_mailboxes(db, user_a)
    target_box = next((b for b in boxes if b["blind_mailbox_id"] == box), None)
    assert target_box is not None
    # Unread count darf NUR den gueltigen (<= 30 Tage) Umschlag zaehlen
    assert target_box["unread_count"] == 1
