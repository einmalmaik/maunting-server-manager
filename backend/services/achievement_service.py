from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any
from sqlalchemy import func
from sqlalchemy.orm import Session

from models import (
    User,
    UserAchievement,
    UserActivityTime,
    UserFriend,
    Server,
    Backup,
    AiConversation,
    ServerPermission,
)
from services.sync_event_service import SyncEventService

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


ACHIEVEMENTS_CATALOG: list[dict[str, Any]] = [
    # --- Starter ---
    {
        "id": "starter_first_step",
        "title": "Erster Schritt",
        "description": "Erste erfolgreiche Anmeldung im MSM Control Panel.",
        "category": "starter",
        "points": 10,
        "icon": "award",
    },
    {
        "id": "starter_security_first",
        "title": "Sicherheitsbewusst",
        "description": "Zwei-Faktor-Authentisierung (2FA) oder biometrischen Tresor aktiviert.",
        "category": "starter",
        "points": 20,
        "icon": "shield-check",
    },
    # --- Server-Administration ---
    {
        "id": "server_architect",
        "title": "Weltenbauer",
        "description": "Den ersten Spielserver erfolgreich aufgesetzt.",
        "category": "servers",
        "points": 25,
        "icon": "server",
    },
    {
        "id": "server_fleet_admiral",
        "title": "Flottenadmiral",
        "description": "Mindestens 3 Server gleichzeitig verwaltet.",
        "category": "servers",
        "points": 50,
        "icon": "layers",
    },
    {
        "id": "backup_guardian",
        "title": "Eiserne Reserve",
        "description": "Ein vollständiges Server-Backup erfolgreich erstellt und gesichert.",
        "category": "servers",
        "points": 30,
        "icon": "archive",
    },
    {
        "id": "terminal_commander",
        "title": "Kommandozentrale",
        "description": "Administrative Befehle oder Server-Aktionen aktiv ausgeführt.",
        "category": "servers",
        "points": 35,
        "icon": "terminal",
    },
    # --- KI & Autonomie ---
    {
        "id": "ai_first_contact",
        "title": "Erster Kontakt",
        "description": "Erste Unterhaltung mit dem Singra KI-Assistenten geführt.",
        "category": "ai",
        "points": 15,
        "icon": "sparkles",
    },
    {
        "id": "ai_collaborator",
        "title": "Autonomer Partner",
        "description": "Einen KI-Aktionsvorschlag geprüft und freigegeben.",
        "category": "ai",
        "points": 30,
        "icon": "bot",
    },
    {
        "id": "ai_mastermind",
        "title": "Meisterstratege",
        "description": "Intensive KI-Zusammenarbeit mit über 50 Interaktionen.",
        "category": "ai",
        "points": 75,
        "icon": "cpu",
    },
    # --- Social & E2EE ---
    {
        "id": "social_handshake",
        "title": "Netzwerker",
        "description": "Erste Freundschaftsanfrage im Social Hub bestätigt.",
        "category": "social",
        "points": 20,
        "icon": "user-plus",
    },
    {
        "id": "social_zero_knowledge",
        "title": "Kryptographischer Pakt",
        "description": "Erste Zero-Knowledge E2EE-Nachricht über DIS verschlüsselt gesendet.",
        "category": "social",
        "points": 50,
        "icon": "lock",
    },
    # --- Aktive Interaktionszeit („Spielzeit“) ---
    {
        "id": "activity_hour_1",
        "title": "Eingearbeitet",
        "description": "Mindestens 1 Stunde aktive Systemzeit im Panel verbracht.",
        "category": "activity",
        "points": 25,
        "icon": "clock",
    },
    {
        "id": "activity_hour_10",
        "title": "Erfahrener Verwalter",
        "description": "10 Stunden aktive Interaktionszeit mit Servern, KI und Verwaltung.",
        "category": "activity",
        "points": 75,
        "icon": "timer",
    },
    {
        "id": "activity_hour_50",
        "title": "Prestige Administrator",
        "description": "Über 50 Stunden hochaktive Administrations- und Dialogzeit.",
        "category": "activity",
        "points": 150,
        "icon": "crown",
    },
]

ACHIEVEMENTS_BY_ID = {a["id"]: a for a in ACHIEVEMENTS_CATALOG}


class AchievementService:
    """Verwaltet Meilensteine, dynamische Seltenheit und aktive Nutzungszeit."""

    @classmethod
    def get_catalog(cls) -> list[dict[str, Any]]:
        return list(ACHIEVEMENTS_CATALOG)

    @classmethod
    def get_rarity_stats(cls, db: Session) -> dict[str, dict[str, Any]]:
        """Ermittelt die dynamische Seltenheit für alle Errungenschaften live aus der Datenbank."""
        total_users = db.query(func.count(User.id)).filter(User.is_active == True).scalar() or 1
        counts_raw = (
            db.query(UserAchievement.achievement_id, func.count(UserAchievement.id))
            .group_by(UserAchievement.achievement_id)
            .all()
        )
        counts = {aid: count for aid, count in counts_raw}

        result = {}
        for ach in ACHIEVEMENTS_CATALOG:
            aid = ach["id"]
            unlocked_count = counts.get(aid, 0)
            percentage = round((unlocked_count / total_users) * 100, 1)
            # Rarity Tiers
            if percentage > 50:
                tier = "common"
            elif percentage > 20:
                tier = "rare"
            elif percentage > 5:
                tier = "epic"
            else:
                tier = "legendary"

            result[aid] = {
                "unlocked_count": unlocked_count,
                "total_users": total_users,
                "percentage": percentage,
                "tier": tier,
                "rarity_text": f"Nur von {percentage}% aller Nutzer freigeschaltet",
            }
        return result

    @classmethod
    def get_user_achievements(cls, db: Session, user_id: int) -> list[dict[str, Any]]:
        """Liefert alle Errungenschaften inklusive Freischaltstatus und Rarity für einen Benutzer."""
        cls.check_automatic_achievements(db, user_id)
        rarity_map = cls.get_rarity_stats(db)
        unlocked_rows = (
            db.query(UserAchievement)
            .filter(UserAchievement.user_id == user_id)
            .all()
        )
        unlocked_map = {row.achievement_id: row.unlocked_at for row in unlocked_rows}

        results = []
        for ach in ACHIEVEMENTS_CATALOG:
            aid = ach["id"]
            rarity = rarity_map.get(
                aid,
                {"percentage": 0.0, "tier": "common", "rarity_text": "Noch nicht freigeschaltet"},
            )
            is_unlocked = aid in unlocked_map
            results.append({
                "id": aid,
                "title": ach["title"],
                "description": ach["description"],
                "category": ach["category"],
                "points": ach["points"],
                "icon": ach["icon"],
                "unlocked": is_unlocked,
                "unlocked_at": unlocked_map.get(aid),
                "global_unlocked_percentage": rarity["percentage"],
                "rarity_tier": rarity["tier"],
                "rarity_text": rarity["rarity_text"],
            })
        return results

    @classmethod
    def get_achievements_overview(cls, db: Session, user_id: int) -> dict[str, Any]:
        """Liefert die strukturierte Übersicht aller Errungenschaften samt Zähler und Prestige-Score."""
        achievements = cls.get_user_achievements(db, user_id)
        unlocked_count = sum(1 for a in achievements if a["unlocked"])
        earned_points = sum(a["points"] for a in achievements if a["unlocked"])
        return {
            "achievements": achievements,
            "total_unlocked": unlocked_count,
            "total_available": len(ACHIEVEMENTS_CATALOG),
            "prestige_score": earned_points,
        }

    @classmethod
    def get_user_stats(cls, db: Session, user_id: int) -> dict[str, Any]:
        """Ermittelt Gesamtpunkte, freigeschaltete Meilensteine und aktive Nutzungszeiten."""
        achievements = cls.get_user_achievements(db, user_id)
        total_points = sum(a["points"] for a in ACHIEVEMENTS_CATALOG)
        earned_points = sum(a["points"] for a in achievements if a["unlocked"])
        unlocked_count = sum(1 for a in achievements if a["unlocked"])

        # Nutzungszeiten
        times = (
            db.query(UserActivityTime)
            .filter(UserActivityTime.user_id == user_id)
            .all()
        )
        time_by_category = {t.category: t.seconds for t in times}
        total_seconds = sum(time_by_category.values())

        return {
            "total_achievements": len(ACHIEVEMENTS_CATALOG),
            "unlocked_achievements": unlocked_count,
            "total_points": total_points,
            "earned_points": earned_points,
            "active_time_seconds": total_seconds,
            "active_time_by_category": time_by_category,
            # Frontend aliases
            "achievements_unlocked": unlocked_count,
            "total_activity_seconds": total_seconds,
            "categories": time_by_category,
        }

    @classmethod
    def unlock_achievement(
        cls, db: Session, user_id: int, achievement_id: str, commit: bool = True
    ) -> bool:
        """Schaltet eine Errungenschaft frei, falls noch nicht errungen.

        Geteilter Pool: Jede Errungenschaft kann systemweit genau einmal errungen werden.
        """
        if achievement_id not in ACHIEVEMENTS_BY_ID:
            logger.warning("Unbekanntes Achievement: %s", achievement_id)
            return False

        existing = (
            db.query(UserAchievement)
            .filter_by(user_id=user_id, achievement_id=achievement_id)
            .first()
        )
        if existing:
            return False

        record = UserAchievement(
            user_id=user_id,
            achievement_id=achievement_id,
            unlocked_at=_now(),
        )
        db.add(record)
        if commit:
            db.commit()

        ach = ACHIEVEMENTS_BY_ID[achievement_id]
        # Benachrichtigung via SSE
        SyncEventService.publish(
            {
                "type": "achievement_unlocked",
                "achievement": {
                    "id": achievement_id,
                    "title": ach["title"],
                    "description": ach["description"],
                    "points": ach["points"],
                    "icon": ach["icon"],
                    "unlocked_at": record.unlocked_at.isoformat(),
                },
            },
            user_id=user_id,
        )
        logger.info("Achievement '%s' für User %d freigeschaltet", achievement_id, user_id)
        return True

    @classmethod
    def record_activity_time(
        cls, db: Session, user_id: int, category: str, seconds: int, commit: bool = True
    ) -> dict[str, Any]:
        """Erfasst aktive Interaktionszeit („Spielzeit“) und prüft Zeit-Meilensteine."""
        seconds = max(1, min(seconds, 3600))
        cat = category.strip()[:32] or "general"

        record = (
            db.query(UserActivityTime)
            .filter_by(user_id=user_id, category=cat)
            .first()
        )
        if not record:
            record = UserActivityTime(
                user_id=user_id,
                category=cat,
                seconds=seconds,
                last_active_at=_now(),
            )
            db.add(record)
        else:
            record.seconds += seconds
            record.last_active_at = _now()

        if commit:
            db.commit()

        # Prüfe Stundenmeilensteine über alle Kategorien
        total_seconds = (
            db.query(func.sum(UserActivityTime.seconds))
            .filter(UserActivityTime.user_id == user_id)
            .scalar()
            or 0
        )
        total_hours = total_seconds / 3600.0

        if total_hours >= 1.0:
            cls.unlock_achievement(db, user_id, "activity_hour_1", commit=commit)
        if total_hours >= 10.0:
            cls.unlock_achievement(db, user_id, "activity_hour_10", commit=commit)
        if total_hours >= 50.0:
            cls.unlock_achievement(db, user_id, "activity_hour_50", commit=commit)

        return {
            "category": cat,
            "category_seconds": record.seconds,
            "total_seconds": total_seconds,
            "total_hours": round(total_hours, 2),
        }

    @classmethod
    def check_automatic_achievements(cls, db: Session, user_id: int) -> None:
        """Prüft automatische Meilensteine anhand bestehender Daten."""
        user = db.query(User).filter_by(id=user_id).first()
        if not user:
            return

        # 1. Erster Schritt: Bei aktiver Session immer verdient
        cls.unlock_achievement(db, user_id, "starter_first_step", commit=False)

        # 2. 2FA aktiv
        if user.two_factor_enabled:
            cls.unlock_achievement(db, user_id, "starter_security_first", commit=False)

        # 3. Server-Anzahl (nur eigene oder freigegebene Server)
        if getattr(user, "is_owner", False):
            server_count = db.query(func.count(Server.id)).scalar() or 0
        else:
            server_count = db.query(func.count(func.distinct(ServerPermission.server_id))).filter_by(user_id=user_id).scalar() or 0

        if server_count >= 1:
            cls.unlock_achievement(db, user_id, "server_architect", commit=False)
        if server_count >= 3:
            cls.unlock_achievement(db, user_id, "server_fleet_admiral", commit=False)

        # 4. Backups vorhanden (nur eigene oder freigegebene)
        if getattr(user, "is_owner", False):
            backup_count = db.query(func.count(Backup.id)).scalar() or 0
        else:
            backup_count = (
                db.query(func.count(func.distinct(Backup.id)))
                .join(ServerPermission, Backup.server_id == ServerPermission.server_id)
                .filter(ServerPermission.user_id == user_id)
                .scalar()
                or 0
            )
        if backup_count >= 1:
            cls.unlock_achievement(db, user_id, "backup_guardian", commit=False)

        # 5. KI-Konversation
        conv_count = db.query(func.count(AiConversation.id)).filter_by(user_id=user_id).scalar() or 0
        if conv_count >= 1:
            cls.unlock_achievement(db, user_id, "ai_first_contact", commit=False)

        # 6. Freunde vorhanden
        friends_count = (
            db.query(func.count(UserFriend.id))
            .filter(
                ((UserFriend.user_id == user_id) | (UserFriend.friend_id == user_id))
                & (UserFriend.status == "accepted")
            )
            .scalar()
            or 0
        )
        if friends_count >= 1:
            cls.unlock_achievement(db, user_id, "social_handshake", commit=False)

        try:
            db.commit()
        except Exception:
            db.rollback()
