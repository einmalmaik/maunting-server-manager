"""Service zur Verwaltung von Notizen, Aufgaben und Checklisten in MSM.

Unterstützt persönliche Notizen sowie mit Teams geteilte Notizen.

Sicherheitsinvarianten:
  - Benutzer sehen nur ihre eigenen Notizen sowie Notizen von Teams, in denen sie Mitglied sind.
  - Bearbeiten und Löschen ist nur dem Ersteller, dem Team-Owner oder Admins/Ownern gestattet.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any
import uuid

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from models.note import Note
from models.user import User
from services import team_service

_log = logging.getLogger("msm.notes")


def _iso_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class NotesService:
    @staticmethod
    def _find_note(db: Session, note_id_or_uid: str | int) -> Note | None:
        target = str(note_id_or_uid).strip()
        if target.isdigit():
            return db.scalar(
                select(Note).where((Note.id == int(target)) | (Note.note_uid == target))
            )
        return db.scalar(select(Note).where(Note.note_uid == target))

    @staticmethod
    def _format_note(note: Note, current_user: User) -> dict[str, Any]:
        can_edit = (note.user_id == current_user.id) or current_user.is_owner
        if note.note_type == "team" and note.team and note.team.owner_user_id == current_user.id:
            can_edit = True

        return {
            "id": note.id,
            "note_uid": note.note_uid,
            "user_id": note.user_id,
            "creator_name": note.user.username if note.user else None,
            "title": note.title,
            "content": note.content or "",
            "category": note.category or "personal",
            "color": note.color or "primary",
            "is_pinned": note.is_pinned,
            "is_archived": note.is_archived,
            "note_type": note.note_type,
            "team_id": note.team_id,
            "team_name": note.team.name if note.team else None,
            "can_edit": can_edit,
            "created_at": _iso_utc(note.created_at) if note.created_at else None,
            "updated_at": _iso_utc(note.updated_at) if note.updated_at else None,
        }

    @classmethod
    def get_notes(
        cls,
        db: Session,
        user: User,
        *,
        category: str | None = None,
        team_id: int | None = None,
        search: str | None = None,
        is_pinned: bool | None = None,
        is_archived: bool | None = False,
        sort_by: str = "updated_at",
        order: str = "desc",
    ) -> list[dict[str, Any]]:
        """Liest alle für den Benutzer sichtbaren Notizen."""
        user_teams = team_service.list_user_teams(db, user)
        user_team_ids = [t.id for t in user_teams]

        visibility_filters = [Note.user_id == user.id]
        if user_team_ids:
            visibility_filters.append(
                (Note.note_type == "team") & (Note.team_id.in_(user_team_ids))
            )

        query = select(Note).where(or_(*visibility_filters))

        if is_archived is not None:
            query = query.where(Note.is_archived == is_archived)

        if is_pinned is not None:
            query = query.where(Note.is_pinned == is_pinned)

        if category:
            cat_norm = category.strip().lower()
            if cat_norm != "all":
                query = query.where(Note.category == cat_norm)

        if team_id is not None:
            if team_id > 0:
                query = query.where(Note.team_id == team_id)
            elif team_id == 0:
                query = query.where(Note.note_type == "personal")

        if search:
            s = f"%{search.strip()}%"
            query = query.where(or_(Note.title.ilike(s), Note.content.ilike(s)))

        # Sortierung: Pinned Notizen immer zuerst, falls nicht anders angegeben
        sort_col = Note.updated_at
        if sort_by == "created_at":
            sort_col = Note.created_at
        elif sort_by == "title":
            sort_col = Note.title

        if order.lower() == "asc":
            query = query.order_by(Note.is_pinned.desc(), sort_col.asc())
        else:
            query = query.order_by(Note.is_pinned.desc(), sort_col.desc())

        rows = db.scalars(query).all()
        return [cls._format_note(n, user) for n in rows]

    @classmethod
    def get_note(cls, db: Session, user: User, note_id_or_uid: str | int) -> dict[str, Any]:
        """Liest eine einzelne Notiz ab und prüft Sichtbarkeitsrechte."""
        note = cls._find_note(db, note_id_or_uid)
        if not note:
            raise ValueError(f"Notiz '{note_id_or_uid}' wurde nicht gefunden.")

        # Sichtbarkeitsprüfung
        can_view = (note.user_id == user.id) or user.is_owner
        if note.note_type == "team" and note.team_id:
            user_teams = team_service.list_user_teams(db, user)
            if any(t.id == note.team_id for t in user_teams):
                can_view = True

        if not can_view:
            raise ValueError("Keine Berechtigung zum Lesen dieser Notiz.")

        return cls._format_note(note, user)

    @classmethod
    def create_note(
        cls,
        db: Session,
        user: User,
        *,
        title: str,
        content: str = "",
        category: str = "personal",
        color: str | None = "primary",
        is_pinned: bool = False,
        note_type: str = "personal",
        team_id: int | None = None,
    ) -> dict[str, Any]:
        """Erstellt eine neue Notiz."""
        norm_type = (note_type or "personal").lower().strip()
        if norm_type not in ("personal", "team"):
            norm_type = "personal"

        final_team_id = None
        if norm_type == "team" and team_id:
            user_teams = team_service.list_user_teams(db, user)
            if not any(t.id == team_id for t in user_teams) and not user.is_owner:
                raise ValueError(f"Sie sind kein Mitglied von Team {team_id}.")
            final_team_id = team_id

        note_uid = str(uuid.uuid4())
        note = Note(
            user_id=user.id,
            note_uid=note_uid,
            title=title.strip(),
            content=content or "",
            category=(category or "personal").strip().lower(),
            color=(color or "primary").strip().lower(),
            is_pinned=bool(is_pinned),
            is_archived=False,
            note_type=norm_type,
            team_id=final_team_id,
        )
        db.add(note)
        db.commit()
        db.refresh(note)
        return cls._format_note(note, user)

    @classmethod
    def update_note(
        cls,
        db: Session,
        user: User,
        note_id_or_uid: str | int,
        *,
        title: str | None = None,
        content: str | None = None,
        category: str | None = None,
        color: str | None = None,
        is_pinned: bool | None = None,
        is_archived: bool | None = None,
        note_type: str | None = None,
        team_id: int | None = None,
    ) -> dict[str, Any]:
        """Aktualisiert eine Notiz."""
        note = cls._find_note(db, note_id_or_uid)
        if not note:
            raise ValueError(f"Notiz '{note_id_or_uid}' wurde nicht gefunden.")

        can_edit = (note.user_id == user.id) or user.is_owner
        if note.note_type == "team" and note.team and note.team.owner_user_id == user.id:
            can_edit = True

        if not can_edit:
            raise ValueError("Keine Berechtigung zur Bearbeitung dieser Notiz.")

        if title is not None:
            note.title = title.strip()
        if content is not None:
            note.content = content
        if category is not None:
            note.category = category.strip().lower()
        if color is not None:
            note.color = color.strip().lower()
        if is_pinned is not None:
            note.is_pinned = is_pinned
        if is_archived is not None:
            note.is_archived = is_archived

        if note_type is not None:
            norm_type = note_type.strip().lower()
            if norm_type in ("personal", "team"):
                note.note_type = norm_type
                if norm_type == "personal":
                    note.team_id = None

        if team_id is not None:
            if team_id <= 0:
                note.team_id = None
                note.note_type = "personal"
            else:
                if not user.is_owner:
                    user_teams = team_service.list_user_teams(db, user)
                    if not any(t.id == team_id for t in user_teams):
                        raise ValueError(f"Sie sind kein Mitglied des Teams {team_id}.")
                note.team_id = team_id
                note.note_type = "team"

        db.commit()
        db.refresh(note)
        return cls._format_note(note, user)

    @classmethod
    def delete_note(cls, db: Session, user: User, note_id_or_uid: str | int) -> dict[str, Any]:
        """Löscht eine Notiz."""
        note = cls._find_note(db, note_id_or_uid)
        if not note:
            raise ValueError(f"Notiz '{note_id_or_uid}' wurde nicht gefunden.")

        can_delete = (note.user_id == user.id) or user.is_owner
        if note.note_type == "team" and note.team and note.team.owner_user_id == user.id:
            can_delete = True

        if not can_delete:
            raise ValueError("Keine Berechtigung zum Löschen dieser Notiz.")

        note_uid = note.note_uid
        db.delete(note)
        db.commit()
        return {"status": "deleted", "note_uid": note_uid, "id": note.id}

    @classmethod
    def toggle_pin(cls, db: Session, user: User, note_id_or_uid: str | int) -> dict[str, Any]:
        """Schaltet den Pin-Status um."""
        note = cls._find_note(db, note_id_or_uid)
        if not note:
            raise ValueError(f"Notiz '{note_id_or_uid}' wurde nicht gefunden.")

        can_edit = (note.user_id == user.id) or user.is_owner
        if note.note_type == "team" and note.team and note.team.owner_user_id == user.id:
            can_edit = True

        if not can_edit:
            raise ValueError("Keine Berechtigung zum Bearbeiten dieser Notiz.")

        note.is_pinned = not note.is_pinned
        db.commit()
        db.refresh(note)
        return cls._format_note(note, user)

    @classmethod
    def toggle_archive(cls, db: Session, user: User, note_id_or_uid: str | int) -> dict[str, Any]:
        """Schaltet den Archiv-Status um."""
        note = cls._find_note(db, note_id_or_uid)
        if not note:
            raise ValueError(f"Notiz '{note_id_or_uid}' wurde nicht gefunden.")

        can_edit = (note.user_id == user.id) or user.is_owner
        if note.note_type == "team" and note.team and note.team.owner_user_id == user.id:
            can_edit = True

        if not can_edit:
            raise ValueError("Keine Berechtigung zum Bearbeiten dieser Notiz.")

        note.is_archived = not note.is_archived
        db.commit()
        db.refresh(note)
        return cls._format_note(note, user)
