"""Regressionen fuer Transaktions- und Netzwerkgrenzen der Review."""
import socket
import threading
from unittest.mock import MagicMock

import pytest

from services import achievement_service, scheduler_service, webpush_service
from services.achievement_service import AchievementService
from services.chat_media_service import ChatMediaService
from services.social_service import SocialService


def test_achievement_erst_nach_commit_und_nie_nach_rollback(db, owner_user, monkeypatch):
    publish = MagicMock()
    monkeypatch.setattr(achievement_service.SyncEventService, "publish", publish)
    AchievementService.unlock_achievement(db, owner_user.id, "starter_first_step", commit=False)
    db.flush()
    publish.assert_not_called()
    db.rollback()
    db.commit()
    publish.assert_not_called()
    AchievementService.unlock_achievement(db, owner_user.id, "starter_first_step", commit=False)
    publish.assert_not_called()
    db.commit()
    assert publish.call_count == 1
    db.commit()
    assert publish.call_count == 1


def test_achievement_savepoint_ist_noch_keine_veroeffentlichung(db, owner_user, monkeypatch):
    publish = MagicMock()
    monkeypatch.setattr(achievement_service.SyncEventService, "publish", publish)
    with db.begin_nested():
        AchievementService.unlock_achievement(db, owner_user.id, "starter_first_step", commit=False)
    publish.assert_not_called()
    db.commit()
    assert publish.call_count == 1


def test_achievement_savepoint_rollback_behaelt_nur_aeussere_freischaltung(db, owner_user, monkeypatch):
    publish = MagicMock()
    monkeypatch.setattr(achievement_service.SyncEventService, "publish", publish)
    AchievementService.unlock_achievement(db, owner_user.id, "starter_first_step", commit=False)
    nested = db.begin_nested()
    AchievementService.unlock_achievement(db, owner_user.id, "starter_security_first", commit=False)
    nested.rollback()
    db.commit()
    assert publish.call_count == 1
    assert publish.call_args.args[0]["achievement"]["id"] == "starter_first_step"


@pytest.mark.parametrize("dns_failure", [True, False])
def test_push_dns_stoerung_oder_private_ip_loescht_nicht_und_sendet_nicht(monkeypatch, dns_failure):
    def resolve(*args, **kwargs):
        if dns_failure:
            raise socket.gaierror(socket.EAI_AGAIN, "synthetic DNS failure")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
    monkeypatch.setattr(webpush_service.socket, "getaddrinfo", resolve)
    client = MagicMock()
    assert webpush_service._zustellen(client, "https://fcm.googleapis.com/test", "", "", b"test", "", "") is True
    client.post.assert_not_called()
    assert webpush_service._zustellen(client, "https://example.invalid/test", "", "", b"test", "", "") is False
    client.post.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("rollback_fails", [True, False])
async def test_cleanup_thread_schliesst_session_auch_bei_fehlern(monkeypatch, rollback_fails):
    main_thread = threading.get_ident()
    worker_threads = []
    db = MagicMock()
    if rollback_fails:
        db.rollback.side_effect = RuntimeError("synthetic rollback failure")
    def session():
        worker_threads.append(threading.get_ident())
        return db
    monkeypatch.setattr(scheduler_service, "SessionLocal", session)
    first = MagicMock(side_effect=RuntimeError("synthetic cleanup failure"))
    following = MagicMock()
    monkeypatch.setattr(SocialService, "cleanup_expired_envelopes", first)
    monkeypatch.setattr(ChatMediaService, "cleanup_expired_media", following)
    monkeypatch.setattr(SocialService, "cleanup_expired_stories", following)
    if rollback_fails:
        with pytest.raises(RuntimeError, match="rollback failure"):
            await scheduler_service._e2ee_envelope_cleanup_task()
    else:
        await scheduler_service._e2ee_envelope_cleanup_task()
        assert following.call_count == 2
    assert worker_threads and worker_threads[0] != main_thread
    db.close.assert_called_once()


def test_achievement_meldefehler_wirft_keinen_erfolgreichen_commit_um(db, owner_user, monkeypatch):
    from models import UserAchievement
    publish = MagicMock(side_effect=[RuntimeError("synthetic delivery failure"), None])
    monkeypatch.setattr(achievement_service.SyncEventService, "publish", publish)
    for name in ("starter_first_step", "starter_security_first"):
        AchievementService.unlock_achievement(db, owner_user.id, name, commit=False)
    db.commit()
    assert db.query(UserAchievement).filter_by(user_id=owner_user.id).count() == 2
    assert publish.call_count == 2
    db.commit()
    assert publish.call_count == 2
