"""Teams: gemeinsame Wissensbasis und gebuendelte Serverrechte.

Ein Team ist zweierlei:

1. Der Traeger von geteiltem KI-Wissen (Skills und Team-Memory). Genau dafuer
   existiert es ueberhaupt — zwei Mit-Administratoren desselben Servers sollen
   sich nicht zweimal dasselbe merken muessen.
2. Ein Buendel von Serverrechten, das der Gruender an seine Mitglieder
   weitergibt.

Der zweite Punkt ist der sicherheitskritische. Die Weitergabe ist **nach oben
gedeckelt**: ein Team kann nie mehr gewaehren, als sein Gruender selbst direkt
haelt. Durchgesetzt wird das nicht hier, sondern bei jeder einzelnen Pruefung in
`permission_service` — die Tabelle unten ist nur der Wunsch, nicht das Recht.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Team(Base):
    """Ein Team — entweder das persoenliche eines Benutzers oder ein echtes.

    ``personal_for_user_id`` traegt die Unterscheidung *und* die Eindeutigkeit:
    ist es gesetzt, handelt es sich um das Ein-Mann-Team dieses Benutzers, und
    die UNIQUE-Bedingung stellt sicher, dass es davon genau eines gibt. Ist es
    NULL, ist es ein echtes Team — und weil NULL-Werte in einer
    UNIQUE-Bedingung nicht kollidieren (Postgres wie SQLite), darf derselbe
    Benutzer beliebig viele echte Teams gruenden.

    Ein separates `is_personal`-Flag waere die naheliegendere Loesung gewesen,
    haette aber zwei Wahrheiten fuer dieselbe Aussage geschaffen, die
    auseinanderlaufen koennen.
    """

    __tablename__ = "teams"
    __table_args__ = (
        UniqueConstraint("personal_for_user_id", name="uq_teams_personal_for_user"),
        Index("ix_teams_owner", "owner_user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    # Der Gruender. Seine direkten Rechte sind die Obergrenze fuer alles, was
    # das Team weitergeben kann. Deshalb RESTRICT: verschwindet der Gruender,
    # verliert die Obergrenze ihren Bezugspunkt — das Team muss vorher
    # uebertragen oder geloescht werden.
    owner_user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    personal_for_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    members: Mapped[list["TeamMember"]] = relationship(
        "TeamMember", back_populates="team", cascade="all, delete-orphan"
    )
    invitations: Mapped[list["TeamInvitation"]] = relationship(
        "TeamInvitation", back_populates="team", cascade="all, delete-orphan"
    )
    server_grants: Mapped[list["TeamServerGrant"]] = relationship(
        "TeamServerGrant", back_populates="team", cascade="all, delete-orphan"
    )

    @property
    def is_personal(self) -> bool:
        return self.personal_for_user_id is not None


class TeamMember(Base):
    """Mitgliedschaft mit zwei Schaltern statt eines Rollensystems.

    Bewusst keine frei definierbaren Team-Rollen: fuer den Zweck — wer darf das
    gemeinsame Wissen veraendern — genuegen zwei Ja/Nein-Fragen. Ein zweites
    vollstaendiges Rollensystem neben dem des Betreibers waere doppelt so viel
    Oberflaeche fuer denselben Nutzen und doppelt so viele Stellen, an denen
    eine Rechtepruefung falsch sein kann.

    Serverrechte stehen bewusst **nicht** hier: die haengen am Team, nicht am
    einzelnen Mitglied. Wer im Team ist, bekommt die Server des Teams.
    """

    __tablename__ = "team_members"
    __table_args__ = (
        CheckConstraint("role IN ('owner', 'member')", name="ck_team_members_role"),
        UniqueConstraint("team_id", "user_id", name="uq_team_members_team_user"),
        Index("ix_team_members_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), default="member", nullable=False)
    # Darf Team-Skills anlegen und aendern — und erlaubt damit zugleich der KI,
    # im Namen dieses Mitglieds fuer das Team zu lernen.
    can_manage_skills: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_manage_memory: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    added_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    team: Mapped["Team"] = relationship("Team", back_populates="members")


class TeamInvitation(Base):
    """Eine ausgesprochene, noch nicht angenommene Einladung.

    Sie steht bewusst in einer **eigenen Tabelle** und nicht als Zustand an
    `TeamMember`. Eine Mitgliedschaft oeffnet zwei Tueren auf einmal: die
    Serverrechte des Teams und das gemeinsame KI-Gedaechtnis. Beide fragt der
    Code an vielen Stellen ab — in `permission_service` mehrfach als direkter
    Join auf `team_members`, hier ueber `membership`. Ein Zustandsfeld an der
    Mitgliedschaft muesste an jeder dieser Stellen mitgeprueft werden, und die
    erste vergessene waere das Loch, das diese Tabelle schliessen soll. Ohne
    Zeile in `team_members` ist jede bestehende Abfrage ohne Zutun richtig.

    Die beiden Schalter stehen hier schon, weil sie Teil des Angebots sind: wer
    annimmt, soll wissen, was er annimmt. Beim Beitritt wandern sie
    unveraendert in die Mitgliedschaft.
    """

    __tablename__ = "team_invitations"
    __table_args__ = (
        UniqueConstraint("team_id", "user_id", name="uq_team_invitations_team_user"),
        # Der Eingeladene fragt "wer will mich haben" — das ist der Lesepfad.
        Index("ix_team_invitations_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    can_manage_skills: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_manage_memory: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    invited_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    team: Mapped["Team"] = relationship("Team", back_populates="invitations")


class TeamServerGrant(Base):
    """Ein Recht, das das Team auf einem Server weitergeben *moechte*.

    Ausdruecklich ein Wunsch und keine Gewaehrung. Ob er wirkt, entscheidet
    `permission_service` bei jeder Pruefung neu, indem es nachsieht, ob der
    Gruender diesen Key auf diesem Server direkt haelt. Verliert er ihn, wird
    diese Zeile wirkungslos, ohne dass jemand aufraeumen muss.
    """

    __tablename__ = "team_server_grants"
    __table_args__ = (
        UniqueConstraint(
            "team_id", "server_id", "permission_key", name="uq_team_server_grants"
        ),
        # Die Rechtepruefung fragt genau diese Kombination ab.
        Index("ix_team_server_grants_lookup", "server_id", "permission_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    server_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("servers.id", ondelete="CASCADE"), nullable=False
    )
    permission_key: Mapped[str] = mapped_column(String(64), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    granted_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    team: Mapped["Team"] = relationship("Team", back_populates="server_grants")
