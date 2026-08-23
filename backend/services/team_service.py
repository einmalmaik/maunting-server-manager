"""Teams anlegen, besetzen und mit Servern verbinden.

Die Rechtepruefung selbst steht bewusst nicht hier, sondern in
`permission_service`. Dieser Dienst schreibt nur den *Wunsch* des Gruenders;
ob er wirkt, entscheidet die Pruefung bei jedem Zugriff neu. Beides zu trennen
ist der Grund, warum ein entzogenes Recht nicht nachgepflegt werden muss.

Die eine Zusage, die dieser Dienst selbst traegt: **eine Mitgliedschaft
entsteht nie ohne den Betroffenen — und sie waechst auch nicht ohne ihn.** Der
Gruender laedt ein, beitreten muss der Eingeladene; was ein Mitglied am
gemeinsamen Wissen darf, nimmt der Gruender jederzeit allein zurueck, erweitern
kann er es nur mit dessen Zustimmung — siehe `invite_member`, `update_member`
und `accept_invitation`. Gehen darf der Eingeladene jederzeit allein.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import Team, TeamInvitation, TeamMember, TeamServerGrant, User
from services import audit_service, permission_service


MAX_TEAMS_PER_OWNER = 20
MAX_MEMBERS_PER_TEAM = 100


def _now() -> datetime:
    return datetime.now(timezone.utc)


def personal_team(db: Session, user: User) -> Team:
    """Das Ein-Mann-Team des Benutzers, bei Bedarf angelegt.

    Es entsteht erst, wenn es gebraucht wird — nicht bei der Registrierung.
    Damit bekommt kein Bestandsbenutzer eine Migration, die Zeilen fuer Konten
    anlegt, die die KI nie benutzen werden.

    Der Zweck ist ein sprachlicher und zugleich ein struktureller: Skills gibt
    es ausschliesslich teambezogen. Ohne dieses Team haette ein Benutzer ohne
    Kollegen keinen Ort, an dem die KI etwas lernen koennte.
    """
    existing = db.query(Team).filter(Team.personal_for_user_id == user.id).first()
    if existing is not None:
        return existing

    team = Team(
        name=user.username,
        owner_user_id=user.id,
        personal_for_user_id=user.id,
    )
    db.add(team)
    try:
        db.flush()
    except IntegrityError:
        # Zwei gleichzeitige Anfragen desselben Benutzers. Die UNIQUE-Bedingung
        # auf `personal_for_user_id` hat den Verlierer abgewiesen — das Team des
        # Gewinners ist genauso gut.
        db.rollback()
        found = db.query(Team).filter(Team.personal_for_user_id == user.id).first()
        if found is None:
            raise
        return found

    db.add(TeamMember(
        team_id=team.id, user_id=user.id, role="owner",
        can_manage_skills=True, can_manage_memory=True, added_by=user.id,
    ))
    db.flush()
    return team


def user_team_ids(db: Session, user: User) -> list[int]:
    """Alle Teams, in denen der Benutzer Mitglied ist — persoenliche und echte.

    Grundlage fuer das Lesen von Team-Wissen. Bewusst ohne das persoenliche
    Team automatisch anzulegen: Lesen darf nichts schreiben, sonst erzeugt eine
    einzige Chatanfrage ohne Zutun des Benutzers eine Zeile.
    """
    rows = db.query(TeamMember.team_id).filter(TeamMember.user_id == user.id).all()
    return [row[0] for row in rows]


def list_user_teams(db: Session, user: User) -> list[Team]:
    ids = user_team_ids(db, user)
    if not ids:
        return []
    return db.query(Team).filter(Team.id.in_(ids)).order_by(Team.name).all()


def membership(db: Session, team_id: int, user_id: int) -> TeamMember | None:
    return (
        db.query(TeamMember)
        .filter(TeamMember.team_id == team_id, TeamMember.user_id == user_id)
        .first()
    )


def get_team_for_member(db: Session, team_id: int, user: User) -> Team:
    """Laedt ein Team, das der Benutzer sehen darf.

    Ein Nichtmitglied bekommt 404 statt 403: ob es ein Team mit dieser Nummer
    ueberhaupt gibt, geht ihn nichts an.
    """
    team = db.get(Team, team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Team nicht gefunden")
    if membership(db, team_id, user.id) is None and not user.is_owner:
        raise HTTPException(status_code=404, detail="Team nicht gefunden")
    return team


def assert_team_owner(db: Session, team: Team, user: User) -> None:
    if user.is_owner:
        return
    member = membership(db, team.id, user.id)
    if member is None or member.role != "owner":
        raise HTTPException(status_code=403, detail="Nur der Teamgruender darf das")


def _assert_name_is_free(
    db: Session, *, owner_user_id: int, name: str, team_id: int | None = None
) -> None:
    """Ein Gruender fuehrt keine zwei echten Teams desselben Namens.

    Das ist keine Ordnungsliebe, sondern die Voraussetzung dafuer, dass
    `learning_team` ein Team ueber seinen Namen ansprechen kann: sind zwei
    Kandidaten namensgleich, nennt die Rueckfrage zur Unterscheidung den
    Gruender — und der unterscheidet nur, wenn er innerhalb seiner eigenen
    Teams eindeutig ist.

    Ueber alle Gruender hinweg duerfen Namen sich dagegen wiederholen: eine
    Absage "Name schon vergeben" wuerde verraten, dass es irgendwo ein Team
    dieses Namens gibt.
    """
    gesucht = name.casefold()
    rows = (
        db.query(Team.id, Team.name)
        .filter(
            Team.owner_user_id == owner_user_id,
            Team.personal_for_user_id.is_(None),
        )
        .all()
    )
    for row_id, row_name in rows:
        if row_id != team_id and row_name.casefold() == gesucht:
            raise HTTPException(
                status_code=409, detail="Du hast bereits ein Team mit diesem Namen"
            )


def create_team(db: Session, *, user: User, name: str) -> Team:
    """Legt ein echtes Team an. Der Gruender ist automatisch Mitglied.

    Er bekommt beide Schalter: wer ein Team gruendet, soll dessen Wissen auch
    pflegen koennen, ohne sich das erst selbst zu erlauben.
    """
    if not permission_service.has_global_permission(db, user, "teams.create"):
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    safe_name = name.strip()
    if not 2 <= len(safe_name) <= 64:
        raise HTTPException(status_code=422, detail="Teamname ist ungueltig")
    _assert_name_is_free(db, owner_user_id=user.id, name=safe_name)

    owned = (
        db.query(Team)
        .filter(Team.owner_user_id == user.id, Team.personal_for_user_id.is_(None))
        .count()
    )
    if owned >= MAX_TEAMS_PER_OWNER:
        raise HTTPException(status_code=409, detail="Zu viele Teams")

    team = Team(name=safe_name, owner_user_id=user.id, personal_for_user_id=None)
    db.add(team)
    db.flush()
    db.add(TeamMember(
        team_id=team.id, user_id=user.id, role="owner",
        can_manage_skills=True, can_manage_memory=True, added_by=user.id,
    ))
    audit_service.record_privileged_action(
        db, user_id=user.id, action="team.created", target_type="team",
        target_id=team.id, details={"name": safe_name}, origin="direct",
    )
    db.commit()
    db.refresh(team)
    return team


def rename_team(db: Session, *, team: Team, user: User, name: str) -> Team:
    assert_team_owner(db, team, user)
    if team.is_personal:
        raise HTTPException(status_code=409, detail="Das persoenliche Team bleibt wie es ist")
    safe_name = name.strip()
    if not 2 <= len(safe_name) <= 64:
        raise HTTPException(status_code=422, detail="Teamname ist ungueltig")
    _assert_name_is_free(
        db, owner_user_id=team.owner_user_id, name=safe_name, team_id=team.id
    )
    team.name = safe_name
    team.updated_at = _now()
    db.commit()
    db.refresh(team)
    return team


def delete_team(db: Session, *, team: Team, user: User) -> None:
    assert_team_owner(db, team, user)
    if team.is_personal:
        # Ohne persoenliches Team haette das Lernen keinen Ort mehr. Es ist
        # kein Besitz, den man aufgeben kann, sondern Teil des Kontos.
        raise HTTPException(status_code=409, detail="Das persoenliche Team bleibt bestehen")
    audit_service.record_privileged_action(
        db, user_id=user.id, action="team.deleted", target_type="team",
        target_id=team.id, details={"name": team.name}, origin="direct",
    )
    db.delete(team)
    db.commit()


def invitation(db: Session, team_id: int, user_id: int) -> TeamInvitation | None:
    return (
        db.query(TeamInvitation)
        .filter(TeamInvitation.team_id == team_id, TeamInvitation.user_id == user_id)
        .first()
    )


def open_invitations(db: Session, user: User) -> list[TeamInvitation]:
    """Die Einladungen, ueber die dieser Benutzer noch entscheiden muss."""
    return (
        db.query(TeamInvitation)
        .filter(TeamInvitation.user_id == user.id)
        .order_by(TeamInvitation.created_at)
        .all()
    )


def team_invitations(db: Session, team_id: int) -> list[TeamInvitation]:
    return (
        db.query(TeamInvitation)
        .filter(TeamInvitation.team_id == team_id)
        .order_by(TeamInvitation.created_at)
        .all()
    )


def invite_member(
    db: Session, *, team: Team, user: User, new_user_id: int,
    can_manage_skills: bool, can_manage_memory: bool,
) -> TeamInvitation:
    """Spricht eine Einladung aus — und macht noch **niemanden** zum Mitglied.

    Vorher trug diese Funktion den Benutzer direkt ein. Damit konnte jeder mit
    `teams.create` einen beliebigen anderen still in sein Team ziehen, und das
    oeffnete mehr als eine Mitgliederliste: das Team-Wissen des Gruenders
    fliesst ab diesem Moment in jeden KI-Lauf des Hinzugefuegten, und was
    dessen KI fuers Team lernt — die Anweisung dazu steht im Systemprompt —
    landet im Team des Fremden und ist dort im Klartext lesbar. Wer jemanden
    hineinzieht, liest also mit und schreibt mit.

    Die Entscheidung darueber gehoert dem, dessen Daten sie oeffnet. Deshalb
    entsteht hier nur eine Zeile in `team_invitations`; die Mitgliedschaft legt
    `accept_invitation` an. Die KI verliert dadurch nichts: sie liest und
    schreibt weiterhin in jedes Team, dem ihr Benutzer wirklich angehoert.

    Eine bereits offene Einladung wird ueberschrieben statt abgelehnt: nur so
    kann der Gruender ein zu grosszuegiges Angebot zuruecknehmen, ohne dass es
    dafuer einen eigenen Weg braucht.
    """
    assert_team_owner(db, team, user)
    if team.is_personal:
        # Das persoenliche Team ist per Definition eines. Wer teilen will,
        # gruendet ein echtes — sonst waere "persoenlich" nur noch ein Name.
        raise HTTPException(status_code=409, detail="Das persoenliche Team bleibt allein")
    target = db.get(User, new_user_id)
    if target is None or not target.is_active:
        raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")
    if membership(db, team.id, new_user_id) is not None:
        raise HTTPException(status_code=409, detail="Benutzer ist bereits im Team")

    offen = invitation(db, team.id, new_user_id)
    if offen is None:
        # Offene Einladungen zaehlen mit: sonst griffe die Obergrenze erst beim
        # Beitritt, und ein Team koennte beliebig viele Menschen gleichzeitig
        # anschreiben.
        belegt = (
            db.query(TeamMember).filter(TeamMember.team_id == team.id).count()
            + db.query(TeamInvitation).filter(TeamInvitation.team_id == team.id).count()
        )
        if belegt >= MAX_MEMBERS_PER_TEAM:
            raise HTTPException(status_code=409, detail="Team ist voll")
        offen = TeamInvitation(team_id=team.id, user_id=new_user_id)
        db.add(offen)

    offen.can_manage_skills = can_manage_skills
    offen.can_manage_memory = can_manage_memory
    offen.invited_by = user.id
    audit_service.record_privileged_action(
        db, user_id=user.id, action="team.member.invited", target_type="team",
        target_id=team.id,
        details={"member_user_id": new_user_id}, origin="direct",
    )
    try:
        db.commit()
    except IntegrityError as exc:
        # Zwei gleichzeitige Einladungen an denselben Benutzer.
        db.rollback()
        raise HTTPException(status_code=409, detail="Einladung besteht bereits") from exc
    db.refresh(offen)
    return offen


def accept_invitation(db: Session, *, user: User, team_id: int) -> TeamMember:
    """Der Eingeladene nimmt an — beitretend oder aufsteigend.

    Die beiden Schalter kommen aus der Einladung und nicht aus dem Aufruf: was
    angenommen wird, muss dasselbe sein, was angeboten wurde.

    Ist der Annehmende schon Mitglied, war die Einladung ein Angebot aus
    `update_member`: der Gruender wollte einen Schalter anheben, und genau das
    kann nur der Betroffene selbst. Dann entsteht keine zweite Zeile — die
    vorhandene traegt danach, was angeboten wurde.
    """
    einladung = invitation(db, team_id, user.id)
    if einladung is None:
        raise HTTPException(status_code=404, detail="Einladung nicht gefunden")
    team = db.get(Team, team_id)
    if team is None:
        # Das Team ist zwischen Einladung und Annahme verschwunden. Die Zeile
        # kann nur ohne Fremdschluesselpruefung ueberlebt haben; sie soll den
        # Benutzer nicht weiter behelligen.
        db.delete(einladung)
        db.commit()
        raise HTTPException(status_code=404, detail="Einladung nicht gefunden")

    bestehend = membership(db, team_id, user.id)
    if bestehend is not None:
        bestehend.can_manage_skills = einladung.can_manage_skills
        bestehend.can_manage_memory = einladung.can_manage_memory
        db.delete(einladung)
        # Kein "joined": beigetreten ist hier niemand. Dieselbe Aktion und
        # dieselben Felder wie in `update_member` — nur steht als Handelnder
        # der, dem die Schalter gehoeren.
        audit_service.record_privileged_action(
            db, user_id=user.id, action="team.member.updated", target_type="team",
            target_id=team_id,
            details={
                "member_user_id": user.id,
                "can_manage_skills": bestehend.can_manage_skills,
                "can_manage_memory": bestehend.can_manage_memory,
            },
            origin="direct",
        )
        db.commit()
        db.refresh(bestehend)
        return bestehend

    # Die Obergrenze gilt nur dem Beitritt: eine Anhebung belegt keinen Platz,
    # den das Mitglied nicht laengst haette.
    if db.query(TeamMember).filter(TeamMember.team_id == team_id).count() >= MAX_MEMBERS_PER_TEAM:
        raise HTTPException(status_code=409, detail="Team ist voll")

    member = TeamMember(
        team_id=team_id, user_id=user.id, role="member",
        can_manage_skills=einladung.can_manage_skills,
        can_manage_memory=einladung.can_manage_memory,
        added_by=einladung.invited_by,
    )
    db.add(member)
    db.delete(einladung)
    audit_service.record_privileged_action(
        db, user_id=user.id, action="team.member.joined", target_type="team",
        target_id=team_id,
        details={"member_user_id": user.id}, origin="direct",
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Benutzer ist bereits im Team") from exc
    db.refresh(member)
    return member


def decline_invitation(db: Session, *, user: User, team_id: int) -> None:
    einladung = invitation(db, team_id, user.id)
    if einladung is None:
        raise HTTPException(status_code=404, detail="Einladung nicht gefunden")
    db.delete(einladung)
    db.commit()


def update_member(
    db: Session, *, team: Team, user: User, member_user_id: int,
    can_manage_skills: bool, can_manage_memory: bool,
) -> TeamMember:
    """Setzt die Schalter eines Mitglieds — herunter sofort, herauf als Angebot.

    Zuruecknehmen darf der Gruender allein: er entzieht, was er selbst gegeben
    hat. **Anheben darf er nicht allein.** Ein nachtraeglich eingeschalteter
    Schalter macht sein Team zum Lernziel der KI des Betroffenen: hat der kein
    eigenes verwaltbares Team, ist das fremde ab dann der einzige Kandidat in
    `learning_team`, und was seine KI "fuers Team" merkt, landet dort und ist
    dort im Klartext lesbar. Genau diese Tuer schliesst `invite_member` seit dem
    23.08.2026 — hier stand sie mit einem Zwischenschritt wieder offen: harmlos
    einladen (beide Schalter aus), annehmen lassen, danach den Schalter setzen.

    Deshalb derselbe Weg wie beim Beitritt: die Anhebung wird zu einer
    Einladung, die derselbe Mensch annimmt. Bis dahin bleibt die Zeile, wie sie
    war; der Gruender sieht sein Angebot unter den offenen Einladungen. Ein
    neuer Wunsch ueberholt das alte Angebot — auch dann, wenn er gar nichts
    mehr anheben will, denn sonst bliebe eine zurueckgenommene Anhebung
    annehmbar.

    Wer ueber sich selbst entscheidet, hat damit zugestimmt; bis hierher kommt
    ohnehin nur, wer das Team verwaltet.
    """
    assert_team_owner(db, team, user)
    member = membership(db, team.id, member_user_id)
    if member is None:
        raise HTTPException(status_code=404, detail="Mitglied nicht gefunden")

    # Das `and` ist die ganze Regel: was aus ist, bleibt aus; was an ist, darf
    # aus werden.
    neu_skills = member.can_manage_skills and can_manage_skills
    neu_memory = member.can_manage_memory and can_manage_memory
    if member_user_id == user.id:
        neu_skills, neu_memory = can_manage_skills, can_manage_memory
    angehoben = (
        (can_manage_skills and not neu_skills) or (can_manage_memory and not neu_memory)
    )
    member.can_manage_skills = neu_skills
    member.can_manage_memory = neu_memory

    offen = invitation(db, team.id, member_user_id)
    if angehoben:
        if offen is None:
            offen = TeamInvitation(team_id=team.id, user_id=member_user_id)
            db.add(offen)
        offen.can_manage_skills = can_manage_skills
        offen.can_manage_memory = can_manage_memory
        offen.invited_by = user.id
    elif offen is not None:
        # Der Wunsch von jetzt ueberholt das Angebot von vorhin.
        db.delete(offen)

    audit_service.record_privileged_action(
        db, user_id=user.id, action="team.member.updated", target_type="team",
        target_id=team.id,
        details={
            "member_user_id": member_user_id,
            # Was wirklich in der Zeile steht, nicht was verlangt wurde. Seit
            # der Zustimmungspflicht sind das zwei verschiedene Dinge, und ein
            # Protokoll, das den Wunsch als Tatsache fuehrt, waere genau dort
            # falsch, wo jemand spaeter nachsieht, wer was durfte.
            "can_manage_skills": member.can_manage_skills,
            "can_manage_memory": member.can_manage_memory,
        },
        origin="direct",
    )
    if angehoben:
        audit_service.record_privileged_action(
            db, user_id=user.id, action="team.member.invited", target_type="team",
            target_id=team.id,
            details={"member_user_id": member_user_id}, origin="direct",
        )
    db.commit()
    db.refresh(member)
    return member


def remove_member(db: Session, *, team: Team, user: User, member_user_id: int) -> None:
    """Entlaesst ein Mitglied — oder laesst eines selbst gehen.

    Der Austritt aus eigenem Antrieb braucht bewusst nicht die Zustimmung des
    Gruenders. Sonst stuende das Gegenstueck zur Einladung schief: wer selbst
    zustimmen muss, um beizutreten, muss auch ohne fremde Zustimmung gehen
    koennen. Der Gruender bleibt ausgenommen — sein Konto ist die Obergrenze
    fuer alles, was das Team weitergibt.
    """
    if member_user_id != user.id:
        assert_team_owner(db, team, user)
    if member_user_id == team.owner_user_id:
        raise HTTPException(status_code=409, detail="Der Gruender bleibt im Team")
    member = membership(db, team.id, member_user_id)
    if member is None:
        raise HTTPException(status_code=404, detail="Mitglied nicht gefunden")
    offen = invitation(db, team.id, member_user_id)
    if offen is not None:
        # Neben einer Mitgliedschaft kann nur ein Angebot aus `update_member`
        # stehen — `invite_member` weist ein Mitglied ab. Es gehoert zu einer
        # Zeile, die es gleich nicht mehr gibt: bliebe es liegen, waere seine
        # Annahme ein Wiedereintritt, und der Gruender erfuehre davon nichts.
        db.delete(offen)
    audit_service.record_privileged_action(
        db, user_id=user.id, action="team.member.removed", target_type="team",
        target_id=team.id, details={"member_user_id": member_user_id},
        origin="direct",
    )
    db.delete(member)
    db.commit()


def set_server_grants(
    db: Session, *, team: Team, user: User, server_id: int, keys: list[str],
) -> list[str]:
    """Legt fest, welche Rechte das Team auf einem Server weitergeben soll.

    Hier steht die **zweite** Sperre gegen Rechteausweitung. Die erste ist die
    Pruefung zur Laufzeit in `permission_service`; sie allein wuerde genuegen.
    Diese hier existiert, damit ein Gruender nicht stillschweigend eine Zeile
    anlegen kann, die spaeter wirksam wuerde, sobald er das Recht selbst
    erhaelt — und damit die Oberflaeche sofort sagt, was nicht geht.

    Geprueft wird gegen `direct_server_permission`, nicht gegen
    `has_server_permission`: was jemand selbst nur ueber ein Team hat, darf er
    nicht weiterreichen.
    """
    from services.permission_catalog import SERVER_KEYS

    assert_team_owner(db, team, user)
    if team.is_personal:
        raise HTTPException(status_code=409, detail="Das persoenliche Team teilt keine Server")

    desired = {key for key in keys if key in SERVER_KEYS}
    for key in sorted(desired):
        if not permission_service.direct_server_permission(db, user, server_id, key):
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Du haelst \"{key}\" auf diesem Server nicht selbst und "
                    "kannst es deshalb nicht an das Team weitergeben."
                ),
            )
    # `server.view` ist die Voraussetzung fuer alles Weitere. Ohne sie sieht das
    # Mitglied den Server nicht einmal in der Liste, haette aber Rechte auf ihm
    # — ein Zustand, den die Oberflaeche nicht darstellen kann.
    if desired and "server.view" not in desired:
        raise HTTPException(
            status_code=422,
            detail="Ohne \"server.view\" ergeben die uebrigen Rechte keinen Sinn",
        )

    existing = (
        db.query(TeamServerGrant)
        .filter(TeamServerGrant.team_id == team.id, TeamServerGrant.server_id == server_id)
        .all()
    )
    existing_by_key = {row.permission_key: row for row in existing}
    for key, row in existing_by_key.items():
        if key not in desired:
            db.delete(row)
    for key in desired:
        if key not in existing_by_key:
            db.add(TeamServerGrant(
                team_id=team.id, server_id=server_id, permission_key=key,
                granted_by=user.id,
            ))
    audit_service.record_privileged_action(
        db, user_id=user.id, action="team.server.grants.set", target_type="team",
        target_id=team.id,
        details={"server_id": server_id, "keys": sorted(desired)}, origin="direct",
    )
    db.commit()
    return sorted(desired)


def team_server_keys(db: Session, team_id: int, server_id: int) -> list[str]:
    rows = (
        db.query(TeamServerGrant.permission_key)
        .filter(TeamServerGrant.team_id == team_id, TeamServerGrant.server_id == server_id)
        .all()
    )
    return sorted(row[0] for row in rows)


def team_server_ids(db: Session, team_id: int) -> list[int]:
    rows = (
        db.query(TeamServerGrant.server_id)
        .filter(TeamServerGrant.team_id == team_id)
        .distinct()
        .all()
    )
    return sorted(row[0] for row in rows)


SCHALTER = {"skills": TeamMember.can_manage_skills, "memory": TeamMember.can_manage_memory}


def learning_teams(db: Session, user: User, *, schalter: str) -> list[Team]:
    """Die echten Teams, in die dieser Benutzer schreiben darf — die Kandidaten.

    ``schalter`` entscheidet, welcher der beiden Mitgliedsschalter gilt. Das war
    vorher fest `can_manage_skills`, obwohl beide Erinnerungswerkzeuge dieselbe
    Funktion benutzten: ein Mitglied mit `memory=True, skills=False` bekam sein
    Teamwissen still ins persoenliche Gedaechtnis geschrieben, und umgekehrt
    endete derselbe Satz in einem 403 aus `_assert_may_write`.

    Das persoenliche Team steht bewusst nicht in der Liste. Es ist kein Ziel,
    unter dem man waehlt, sondern der Rueckfall, wenn es keines gibt.
    """
    spalte = SCHALTER.get(schalter)
    if spalte is None:
        raise ValueError(f"Unbekannter Schalter: {schalter}")
    rows = (
        db.query(Team)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .filter(
            TeamMember.user_id == user.id,
            Team.personal_for_user_id.is_(None),
            spalte.is_(True),
        )
        .order_by(Team.name)
        .all()
    )
    return rows


def _mit_gruender(name: str, gruender: str) -> str:
    """Die eine Form, in der ein mehrdeutiger Teamname seinen Gruender traegt.

    Sie steht hier und nicht an ihren Aufrufstellen, weil beide Seiten
    desselben Gespraechs sie treffen muessen: `learning_team` bietet sie in der
    Rueckfrage an und nimmt sie als `wunsch` wieder entgegen, und wer ein Team
    sonst benennt (`ansprechbarer_name`), benennt es in genau dieser Form. Zwei
    Schreibweisen waeren zwei Namen fuer dasselbe Team, und der eine davon
    waehlte nichts aus.
    """
    return f"{name} ({gruender})"


def _gruendernamen(db: Session, teams: list[Team]) -> dict[int, str]:
    """Der Gruendername je Team-ID — in einer Abfrage, nicht einer je Team."""
    namen = dict(
        db.query(User.id, User.username)
        .filter(User.id.in_({team.owner_user_id for team in teams}))
        .all()
    )
    return {
        team.id: str(namen.get(team.owner_user_id, team.owner_user_id))
        for team in teams
    }


def _kandidatennamen(db: Session, kandidaten: list[Team]) -> list[str]:
    """Namen, unter denen die Kandidaten unterscheidbar sind.

    Teamnamen sind nur je Gruender eindeutig — zwei Gruender duerfen dasselbe
    Wort waehlen, und `learning_team` spricht ein Team allein ueber seinen
    Namen an. Hiessen in der Rueckfrage beide schlicht "Alpha", waere jede
    Antwort mehrdeutig: dieselbe Frage kaeme erneut, und Team-Lernen waere fuer
    diesen Benutzer dauerhaft unmoeglich. Deshalb bekommt nur der mehrdeutige
    Name den Gruender dazu — der eindeutige bleibt, wie der Benutzer ihn kennt.

    Der Gruendername verraet nichts: der Benutzer ist Mitglied beider Teams und
    sieht ihn ohnehin in der Mitgliederliste.
    """
    haeufig = Counter(team.name.casefold() for team in kandidaten)
    if all(anzahl == 1 for anzahl in haeufig.values()):
        return [team.name for team in kandidaten]
    gruender = _gruendernamen(db, kandidaten)
    return [
        team.name if haeufig[team.name.casefold()] == 1
        else _mit_gruender(team.name, gruender[team.id])
        for team in kandidaten
    ]


def ansprechbarer_name(db: Session, user: User, team: Team) -> str:
    """Der Name, unter dem dieser Benutzer genau *dieses* Team meint.

    Wer ein Team benennt, ohne es auszuwaehlen — die volle Absage in
    `ai_memory_service._bereichsname`, ein Suchtreffer —, braucht denselben
    Namen, den `learning_team` danach wieder annimmt. Der blosse Teamname ist
    dafuer zu wenig: er ist nur je Gruender eindeutig (`_assert_name_is_free`),
    und ist der Benutzer in zwei Teams desselben Namens, benennt er beide. Was
    dann folgt, ist keine Sackgasse mehr, sondern ein Treffer im falschen Team
    — Schluessel sind bewusst stabil und wiederholen sich ueber Teams hinweg,
    es gibt dort also etwas zu treffen.

    Verglichen wird gegen die **echten** Teams des Benutzers. Das persoenliche
    steht in keiner Auswahl (`learning_teams`) und macht deshalb keinen Namen
    mehrdeutig, auch wenn es zufaellig so heisst.
    """
    gleichnamig = [
        row for row in list_user_teams(db, user)
        if not row.is_personal and row.name.casefold() == team.name.casefold()
    ]
    if len(gleichnamig) < 2:
        return team.name
    return _mit_gruender(team.name, _gruendernamen(db, [team])[team.id])


def learning_team(
    db: Session, user: User, *, schalter: str = "skills", wunsch: str | None = None
) -> tuple[Team | None, str | None]:
    """Wohin die KI schreibt, wenn sie etwas fuer das Team lernt.

    Gibt entweder ein Team zurueck oder einen Grund, warum die KI nachfragen
    muss. Die Regel folgt dem Alltag statt einer Einstellung:

    - genau ein echtes Team, in dem der Benutzer verwalten darf → dorthin
    - keines → das persoenliche Team, damit ueberhaupt gelernt wird
    - mehrere → ``wunsch`` entscheidet; ohne ihn eine Rueckfrage

    ``wunsch`` ist ein **Auswahlmittel, keine Berechtigung.** Die Kandidatenliste
    stammt aus der Datenbank; der Name aus dem Prompt darf nur einen Eintrag
    daraus treffen. Trifft er keinen, gibt es dieselbe Rueckfrage wie ohne ihn —
    ausdruecklich ohne den Hinweis, ob es ein Team dieses Namens ueberhaupt gibt.
    Vorher war der Fall eine Sackgasse: die Rueckfrage kam, aber kein Werkzeug
    nahm die Antwort entgegen, und das Modell fragte erneut. Sind zwei
    Kandidaten namensgleich, nennt die Rueckfrage zusaetzlich den Gruender —
    dieser zusammengesetzte Name waehlt dann genauso aus wie ein eindeutiger.

    Der zweite Rueckgabewert ist bewusst Text fuer das Modell, nicht ein Code:
    er landet direkt im Werkzeugergebnis und soll dort verstanden werden.
    """
    kandidaten = learning_teams(db, user, schalter=schalter)
    if len(kandidaten) == 1:
        return kandidaten[0], None
    if not kandidaten:
        return personal_team(db, user), None

    namen = _kandidatennamen(db, kandidaten)
    if wunsch is not None:
        gesucht = wunsch.strip().casefold()
        # Die Form mit Gruender waehlt **immer** aus, auch wo das Angebot oben
        # mit dem blossen Namen auskommt. `ansprechbarer_name` misst die
        # Mehrdeutigkeit an allen Teams des Benutzers, dieser Schalter nur an
        # seinen Kandidaten — der Name aus einem Suchtreffer ist deshalb
        # manchmal genauer als noetig. Nachsichtig lesen, streng auswaehlen:
        # ein zu genauer Name darf nicht ins Leere laufen.
        gruender = _gruendernamen(db, kandidaten)
        treffer = [
            team for team, name in zip(kandidaten, namen)
            if gesucht in (
                team.name.casefold(),
                name.casefold(),
                _mit_gruender(team.name, gruender[team.id]).casefold(),
            )
        ]
        if len(treffer) == 1:
            return treffer[0], None
    return None, (
        "Der Benutzer ist in mehreren Teams: "
        f"{', '.join(namen)}. Frage nach, welches gemeint ist, und rufe das "
        "Werkzeug erneut mit team=\"<Name>\" auf."
    )


def can_manage_team_skills(db: Session, user: User, team_id: int) -> bool:
    member = membership(db, team_id, user.id)
    return member is not None and member.can_manage_skills


def can_manage_team_memory(db: Session, user: User, team_id: int) -> bool:
    member = membership(db, team_id, user.id)
    return member is not None and member.can_manage_memory
