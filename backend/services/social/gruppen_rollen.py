from __future__ import annotations

import logging

from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import (
    ChatGroup,
    ChatGroupMember,
)
from services.social.grenzen import (
    GROUP_CALL_PERMISSIONS,
    GROUP_MODERATION_PERMISSIONS,
    GROUP_PERMISSION_ALIASES,
    GROUP_PERMISSIONS,
)

logger = logging.getLogger(__name__)


class GruppenRollenMixin:
    @classmethod
    def get_group_member(cls, db: Session, group_id: int, user_id: int) -> ChatGroupMember | None:
        return (
            db.query(ChatGroupMember)
            .filter(ChatGroupMember.group_id == group_id, ChatGroupMember.user_id == user_id)
            .first()
        )

    @classmethod
    def expand_group_permissions(cls, raw: str | None) -> set[str]:
        """Parst eine gespeicherte Rechteliste und löst Altbezeichnungen auf.

        Der Rechte-Dialog hat eine Zeit lang eigene Schlüssel geschrieben
        (``call_start`` statt ``start_group_calls`` und so fort), die hier nie
        geprüft wurden — gesetzte Haken blieben wirkungslos. Statt die Daten zu
        migrieren, werden die alten Namen beim Lesen übersetzt: bestehende
        Gruppen behalten ihre Einstellung, und neue Daten sprechen nur noch das
        kanonische Vokabular.
        """
        gesetzt: set[str] = set()
        for teil in (raw or "").split(","):
            name = teil.strip()
            if not name:
                continue
            gesetzt.update(GROUP_PERMISSION_ALIASES.get(name, (name,)))
        return gesetzt

    @classmethod
    def effective_permissions(
        cls, member_permissions: str | None, group_default: str | None, role: str
    ) -> set[str]:
        """Was ein Mitglied tatsächlich darf — ohne Datenbank, ohne Seiteneffekt.

        Die eine Stelle, an der die Regel steht. ``has_group_permission``
        entscheidet damit den einzelnen Zugriff, ``list_user_groups`` rechnet
        damit die Marken für die Oberfläche aus. Ohne diese Funktion liefe
        beides auseinander, und genau dann zeigt die Oberfläche einen Knopf, den
        das Backend danach mit 403 beantwortet.

        ``member_permissions is None`` heißt „kein eigener Eintrag", und dann
        gilt die Vorgabe der Gruppe. Ein leerer String heißt dagegen
        „ausdrücklich nichts" und bleibt leer.

        Eigentümer und Administratoren bekommen die Anruf- und
        Moderationsrechte pauschal: sie verwalten die Rollen und könnten sie
        sich mit zwei Klicks selbst eintragen. Beim Beitreten ist das Absicht —
        wer einen Raum öffnen darf, darf ihn betreten, sonst bekäme der
        Startende einen Raum ohne Zutritt.
        """
        raw = member_permissions if member_permissions is not None else group_default
        gesetzt = cls.expand_group_permissions(raw)
        if role in ("owner", "admin"):
            gesetzt |= GROUP_CALL_PERMISSIONS
            gesetzt |= GROUP_MODERATION_PERMISSIONS
        return gesetzt

    @classmethod
    def has_group_permission(cls, db: Session, group_id: int, user_id: int, permission: str) -> bool:
        """Checks membership and effective group permission without mutating state."""
        member = cls.get_group_member(db, group_id, user_id)
        if not member:
            return False
        group_default: str | None = None
        if member.permissions is None:
            group = db.query(ChatGroup).filter(ChatGroup.id == group_id).first()
            group_default = group.default_permissions if group else None
        return permission in cls.effective_permissions(
            member.permissions, group_default, member.role
        )

    @classmethod
    def darf_einladen(cls, db: Session, group_id: int, user_id: int) -> bool:
        """Ob dieses Mitglied neue Leute in die Gruppe holen darf.

        Steht hier und nicht in ``effective_permissions``, weil Eigentümer und
        Administratoren ``invite_members`` nicht automatisch tragen — die
        Vorgabe erweitert für sie nur die Anruf- und Moderationsrechte. Ein
        Eigentümer, der niemanden in seine eigene Gruppe holen darf, wäre kein
        Schutz, sondern ein Rätsel.

        Der Aufrufer entscheidet damit, ob er den **Einladungscode** überhaupt
        zu sehen bekommt. Bis 09/2026 ging er an jedes Mitglied heraus, bei
        jedem Abruf der Gruppenliste: ``invite_members`` war damit nicht nur
        ungeprüft, es war strukturell unprüfbar. Wer den Code hat, kommt rein —
        also ist ihn nicht zu bekommen die einzige Schranke, die es geben kann.
        """
        member = cls.get_group_member(db, group_id, user_id)
        if not member:
            return False
        if member.role in ("owner", "admin"):
            return True
        return cls.has_group_permission(db, group_id, user_id, "invite_members")

    @classmethod
    def assert_known_permissions(cls, raw: str | None) -> str | None:
        """Weist unbekannte Rechtenamen ab, statt sie stumm zu speichern.

        Ein Tippfehler in einer Rechteliste blieb bisher folgenlos sichtbar: der
        Haken stand im Dialog, geprüft wurde er nie. Genau so entstand die
        Lücke zwischen ``call_start`` und ``start_group_calls``.
        """
        if raw is None:
            return None
        namen = [teil.strip() for teil in raw.split(",") if teil.strip()]
        unbekannt = sorted(
            n for n in namen if n not in GROUP_PERMISSIONS and n not in GROUP_PERMISSION_ALIASES
        )
        if unbekannt:
            raise HTTPException(
                status_code=422,
                detail=f"Unbekannte Berechtigung: {', '.join(unbekannt)}",
            )
        # Kanonisch und ohne Dubletten zurückschreiben.
        return ",".join(sorted(cls.expand_group_permissions(raw)))

    @classmethod
    def assert_group_call_permission(
        cls, db: Session, group_id: int, user_id: int, permission: str
    ) -> ChatGroupMember:
        member = cls.get_group_member(db, group_id, user_id)
        if not member or not cls.has_group_permission(db, group_id, user_id, permission):
            raise HTTPException(status_code=403, detail="Keine Berechtigung für diesen Gruppenanruf.")
        return member

    @classmethod
    def darf_gruppenzustand_schreiben(cls, db: Session, group_id: int, user_id: int) -> bool:
        """Eigentümer, Administratoren und wer ``manage_roles`` trägt.

        ``manage_roles`` steht bewusst **nicht** in ``GROUP_MODERATION_PERMISSIONS``
        und fällt Eigentümern deshalb nicht automatisch zu — hier wird es
        deswegen ausdrücklich neben der Rolle geprüft.
        """
        member = cls.get_group_member(db, group_id, user_id)
        if not member:
            return False
        if member.role in ("owner", "admin"):
            return True
        return cls.has_group_permission(db, group_id, user_id, "manage_roles")
