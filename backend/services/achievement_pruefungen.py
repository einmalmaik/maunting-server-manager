"""Errungenschaften, die sich am Datenbestand ablesen lassen.

Jede Prüfung bekommt die Sitzung, das Konto und die Kennungen, die es schon
hat, und sagt ja oder nein. Sie läuft beim Abruf der Liste — also auch für
alles, was vor dem Abzeichen geschah. Was sich nur im Moment der Aktion
erkennen lässt, meldet der Aktionspfad selbst (`AchievementService.melde`);
was nur das Gerät weiß, meldet der Client (`SELBST_GEMELDET`).

Bis 09/2026 prüfte der Server hier sieben von 104 Kennungen. Der Rest stand
im Katalog und war nicht zu erreichen.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from models import (
    AiAttachment,
    AiConversation,
    AiGuardianRepair,
    AiRun,
    AiSkill,
    AiToolResult,
    AiUsageEvent,
    AuditLog,
    Backup,
    ChatGroup,
    ChatGroupMember,
    ChatStory,
    OperationTask,
    PanelBackup,
    Server,
    ServerPermission,
    Team,
    TeamInvitation,
    TeamMember,
    TeamServerGrant,
    User,
    UserFriend,
    VaultEntry,
    VaultUserSetting,
)

Pruefung = Callable[[Session, User, set[str]], bool]


def _gibt(query) -> bool:
    return query.limit(1).first() is not None


def _audit(db: Session, user: User, *actions: str) -> bool:
    return _gibt(db.query(AuditLog.id).filter(AuditLog.user_id == user.id, AuditLog.action.in_(actions)))


def _auftrag(db: Session, user: User, *task_types: str) -> bool:
    return _gibt(
        db.query(OperationTask.id).filter(
            OperationTask.actor_user_id == user.id,
            OperationTask.status == "succeeded",
            OperationTask.task_type.in_(task_types),
        )
    )


def _eigene_server(db: Session, user: User):
    """Filter auf die Server des Kontos — wie bisher: Owner alle, sonst die freigegebenen."""
    if user.is_owner:
        return True
    return Server.id.in_(db.query(ServerPermission.server_id).filter(ServerPermission.user_id == user.id))


def _server_zahl(db: Session, user: User) -> int:
    if user.is_owner:
        return db.query(func.count(Server.id)).scalar() or 0
    return (
        db.query(func.count(func.distinct(ServerPermission.server_id)))
        .filter(ServerPermission.user_id == user.id)
        .scalar()
        or 0
    )


def _backups(db: Session, user: User):
    query = db.query(Backup.id)
    if not user.is_owner:
        query = query.join(ServerPermission, Backup.server_id == ServerPermission.server_id).filter(
            ServerPermission.user_id == user.id
        )
    return query


def _hat_tresor(db: Session, user: User) -> bool:
    # Dieselbe Frage wie `has_vault` im Tresordienst.
    return _gibt(
        db.query(VaultUserSetting.user_id).filter(
            VaultUserSetting.user_id == user.id,
            or_(VaultUserSetting.bucket_id.isnot(None), VaultUserSetting.kdf_salt.isnot(None)),
        )
    )


def _tresoreintrag(db: Session, user: User) -> bool:
    return _gibt(
        db.query(VaultEntry.id)
        .join(VaultUserSetting, VaultUserSetting.bucket_id == VaultEntry.bucket_id)
        .filter(VaultUserSetting.user_id == user.id, VaultEntry.is_deleted.is_(False))
    )


def _ip_freigabe(db: Session, user: User) -> bool:
    rows = (
        db.query(AuditLog.details)
        .filter(AuditLog.user_id == user.id, AuditLog.action == "postgres.instance.network")
        .all()
    )
    for (details,) in rows:
        try:
            if json.loads(details or "{}").get("allowed_cidrs"):
                return True
        except (ValueError, AttributeError):
            continue
    return False


def _laufzeit_72h(db: Session, user: User) -> bool:
    grenze = datetime.now(timezone.utc) - timedelta(hours=72)
    return _gibt(
        db.query(Server.id).filter(
            _eigene_server(db, user),
            Server.status == "running",
            Server.last_started_at.isnot(None),
            Server.last_started_at <= grenze,
        )
    )


# --- KI: Läufe und Protokoll überleben „Chat leeren", Nachrichten nicht ---


def _chatlaeufe(db: Session, user: User):
    """Läufe im eigenen Chat — nicht Wächter, nicht Worker, nicht das bloße Öffnen."""
    return (
        db.query(AiRun.id)
        .join(AiConversation, AiConversation.id == AiRun.conversation_id)
        .filter(AiRun.user_id == user.id, AiConversation.kind == "primary")
    )


def _audit_mit(db: Session, user: User, action: str, *fragmente: str) -> bool:
    # `details` ist JSON mit sortierten Schlüsseln und Standardtrennern.
    query = db.query(AuditLog.id).filter(AuditLog.user_id == user.id, AuditLog.action == action)
    for fragment in fragmente:
        query = query.filter(AuditLog.details.like(f"%{fragment}%"))
    return _gibt(query)


def _ki_vorschlag_ausgefuehrt(db: Session, user: User, *tools: str) -> bool:
    return any(
        _audit_mit(db, user, "ai.action.executed", '"succeeded": true', f'"tool": "{tool}"') for tool in tools
    )


def _werkzeug_im_chat(db: Session, user: User, *tools: str):
    return (
        db.query(AiToolResult.run_id)
        .join(AiConversation, AiConversation.id == AiToolResult.conversation_id)
        .filter(AiConversation.user_id == user.id, AiToolResult.tool_name.in_(tools))
    )


def _anhang(db: Session, user: User, bild: bool) -> bool:
    art = AiAttachment.media_type.like("image/%")
    return _gibt(
        db.query(AiAttachment.id).filter(
            AiAttachment.user_id == user.id,
            AiAttachment.status == "ready",
            AiAttachment.message_id.isnot(None),
            art if bild else ~art,
        )
    )


def _abgelehnt(db: Session, user: User) -> bool:
    # `ai.action.rejected` schreibt auch die Prüfung selbst, wenn ein Werkzeugaufruf
    # ungültig war — dort fehlt die `proposal_id`.
    return _audit_mit(db, user, "ai.action.rejected", '"proposal_id"') or _audit(
        db, user, "ai.action.approval.rejected"
    )


def _autonome_kette(db: Session, user: User) -> bool:
    return _gibt(
        db.query(AuditLog.correlation_id)
        .filter(
            AuditLog.user_id == user.id,
            AuditLog.action == "ai.action.confirmed",
            AuditLog.details.like('%"autonomous": true%'),
            AuditLog.correlation_id.isnot(None),
        )
        .group_by(AuditLog.correlation_id)
        .having(func.count(AuditLog.id) >= 3)
    )


def _ueberwacht(db: Session, user: User) -> bool:
    return any(
        _audit_mit(db, user, "ai.tool.read", f'"tool": "{tool}"')
        for tool in ("read_server_status", "read_server_capacity", "read_guardian_incidents", "read_server_logs")
    )


def _zeitreise(db: Session, user: User) -> bool:
    return _gibt(
        _werkzeug_im_chat(db, user, "control_region_camera", "analyze_region")
        .filter(AiToolResult.run_id.isnot(None))
        .group_by(AiToolResult.run_id)
        .having(func.count(AiToolResult.id) >= 3)
    )


# --- Messenger-Gruppen: nur, was der Server ohnehin sieht (Eigentümer, Mitglieder) ---


def _eigene_gruppen(db: Session, user: User):
    return db.query(ChatGroup.id).filter(ChatGroup.owner_user_id == user.id)


def _gruppe_mit_fremden(db: Session, user: User) -> bool:
    return _gibt(
        db.query(ChatGroupMember.id)
        .join(ChatGroup, ChatGroup.id == ChatGroupMember.group_id)
        .filter(ChatGroup.owner_user_id == user.id, ChatGroupMember.user_id != user.id)
    )


def _gruppenrollen(db: Session, user: User) -> bool:
    return _gibt(
        db.query(ChatGroupMember.id)
        .join(ChatGroup, ChatGroup.id == ChatGroupMember.group_id)
        .filter(
            ChatGroup.owner_user_id == user.id,
            ChatGroupMember.user_id != user.id,
            or_(ChatGroupMember.role.in_(("admin", "moderator")), ChatGroupMember.permissions.isnot(None)),
        )
    )


def _grosse_gruppe(db: Session, user: User) -> bool:
    return _gibt(
        db.query(ChatGroup.id)
        .join(ChatGroupMember, ChatGroupMember.group_id == ChatGroup.id)
        .filter(ChatGroup.owner_user_id == user.id)
        .group_by(ChatGroup.id)
        .having(func.count(ChatGroupMember.id) >= 5)
    )


# --- Panel-Teams: das persönliche Ein-Personen-Team zählt nirgends ---


def _geteilte_teams(db: Session):
    return db.query(Team.id).filter(Team.personal_for_user_id.is_(None))


def _team_beigetreten(db: Session, user: User) -> bool:
    return _gibt(
        db.query(TeamMember.id).filter(
            TeamMember.user_id == user.id,
            TeamMember.role == "member",
            TeamMember.team_id.in_(_geteilte_teams(db)),
        )
    )


def _team_gegruendet(db: Session, user: User) -> bool:
    return _gibt(_geteilte_teams(db).filter(Team.owner_user_id == user.id))


def _team_server(db: Session, user: User) -> bool:
    return _gibt(
        db.query(TeamServerGrant.id)
        .join(Team, Team.id == TeamServerGrant.team_id)
        .filter(
            Team.owner_user_id == user.id,
            Team.personal_for_user_id.is_(None),
            TeamServerGrant.permission_key == "server.view",
        )
    )


def _team_rechte_delegiert(db: Session, user: User) -> bool:
    if _gibt(db.query(TeamServerGrant.id).filter(TeamServerGrant.granted_by == user.id)):
        return True
    eigene_teams = db.query(TeamMember.team_id).filter(
        TeamMember.user_id == user.id, TeamMember.team_id.in_(_geteilte_teams(db))
    )
    kollegen = db.query(TeamMember.user_id).filter(
        TeamMember.team_id.in_(eigene_teams), TeamMember.user_id != user.id
    )
    return _gibt(
        db.query(ServerPermission.id).filter(
            ServerPermission.granted_by == user.id, ServerPermission.user_id.in_(kollegen)
        )
    )


def _team_rollen(db: Session, user: User) -> bool:
    # Teams kennen nur zwei Zusatzrechte; wer eines vergibt, verteilt Aufgaben.
    # Das Angebot zählt schon — annehmen muss es das Mitglied.
    eigene = _geteilte_teams(db).filter(Team.owner_user_id == user.id)
    mitglied = db.query(TeamMember.id).filter(
        TeamMember.team_id.in_(eigene),
        TeamMember.user_id != user.id,
        or_(TeamMember.can_manage_skills.is_(True), TeamMember.can_manage_memory.is_(True)),
    )
    angebot = db.query(TeamInvitation.id).filter(
        TeamInvitation.invited_by == user.id,
        or_(TeamInvitation.can_manage_skills.is_(True), TeamInvitation.can_manage_memory.is_(True)),
    )
    return _gibt(mitglied) or _gibt(angebot)


def _grosses_team(db: Session, user: User) -> bool:
    eigene_teams = db.query(TeamMember.team_id).filter(TeamMember.user_id == user.id)
    return _gibt(
        db.query(TeamMember.team_id)
        .filter(TeamMember.team_id.in_(eigene_teams), TeamMember.team_id.in_(_geteilte_teams(db)))
        .group_by(TeamMember.team_id)
        .having(func.count(TeamMember.id) >= 5)
    )


def _alle(*kennungen: str) -> Pruefung:
    """Sammelabzeichen: gilt, sobald alle genannten schon errungen sind."""
    return lambda _db, _user, schon: set(kennungen) <= schon


# Reihenfolge zählt: Sammelabzeichen stehen hinter dem, was sie sammeln.
PRUEFUNGEN: list[tuple[str, Pruefung]] = [
    # Starter
    ("starter_first_step", lambda db, user, schon: True),
    ("starter_security_first", lambda db, user, schon: bool(user.two_factor_enabled) or _hat_tresor(db, user)),
    ("starter_profile_setup", lambda db, user, schon: bool(user.avatar_url)),
    ("starter_timezone_set", lambda db, user, schon: bool(user.time_zone)),
    ("starter_vault_master", lambda db, user, schon: _tresoreintrag(db, user)),
    # Server
    ("server_architect", lambda db, user, schon: _server_zahl(db, user) >= 1),
    ("server_fleet_admiral", lambda db, user, schon: _server_zahl(db, user) >= 3),
    (
        "server_power_cycle",
        lambda db, user, schon: _auftrag(db, user, "server.lifecycle.restart")
        or (_auftrag(db, user, "server.lifecycle.stop") and _auftrag(db, user, "server.lifecycle.start")),
    ),
    ("server_blueprint_deployer", lambda db, user, schon: _auftrag(db, user, "server.provision")),
    ("server_custom_blueprint", lambda db, user, schon: _audit(db, user, "blueprints.import")),
    ("server_node_connector", lambda db, user, schon: _audit(db, user, "nodes.enrollment.approve")),
    ("server_high_availability", lambda db, user, schon: _laufzeit_72h(db, user)),
    (
        "server_auto_restart",
        lambda db, user, schon: _audit(db, user, "guardian.overrides.set")
        or _gibt(db.query(Server.id).filter(_eigene_server(db, user), Server.auto_restart.is_(True))),
    ),
    # Backup
    ("backup_guardian", lambda db, user, schon: _gibt(_backups(db, user))),
    (
        "backup_cron_master",
        lambda db, user, schon: _gibt(
            db.query(Server.id).filter(_eigene_server(db, user), Server.backup_interval_hours.isnot(None))
        ),
    ),
    ("backup_restorer", lambda db, user, schon: _audit(db, user, "server.backups.restore")),
    ("backup_cloud_offloader", lambda db, user, schon: _audit(db, user, "server.backups.upload_cloud")),
    (
        "backup_checksum_verifier",
        lambda db, user, schon: _gibt(_backups(db, user).filter(Backup.verified_at.isnot(None))),
    ),
    (
        "backup_panel_snapshot",
        lambda db, user, schon: bool(user.is_owner) and _gibt(db.query(PanelBackup.id)),
    ),
    # Sicherheit
    (
        "security_api_key_creator",
        lambda db, user, schon: _audit(db, user, "hoster.integration.created", "hoster.integration.key.rotated"),
    ),
    ("security_ip_allowlist", lambda db, user, schon: _ip_freigabe(db, user)),
    (
        "security_role_granularity",
        lambda db, user, schon: _gibt(
            db.query(ServerPermission.id).filter(
                ServerPermission.granted_by == user.id, ServerPermission.user_id != user.id
            )
        )
        or _audit(db, user, "roles.create", "roles.update", "user.roles.updated", "team.server.grants.set"),
    ),
    ("security_password_rotation", lambda db, user, schon: _audit(db, user, "auth.password.change")),
    # KI — bis 09/2026 reichte für „Erster Kontakt" das Öffnen des Chats:
    # die Unterhaltung legt schon der erste Abruf an.
    ("ai_first_contact", lambda db, user, schon: _gibt(_chatlaeufe(db, user))),
    ("ai_mastermind", lambda db, user, schon: _chatlaeufe(db, user).count() > 50),
    ("ai_hundred_prompts", lambda db, user, schon: _chatlaeufe(db, user).count() > 100),
    ("ai_collaborator", lambda db, user, schon: _audit_mit(db, user, "ai.action.confirmed", '"confirmed": true')),
    ("ai_proposal_denied", lambda db, user, schon: _abgelehnt(db, user)),
    (
        "ai_voice_dialog",
        lambda db, user, schon: _gibt(
            db.query(AiUsageEvent.id).filter(
                AiUsageEvent.user_id == user.id,
                AiUsageEvent.status == "completed",
                AiUsageEvent.realtime_text_input_tokens.isnot(None),
            )
        ),
    ),
    (
        "ai_deep_thinker",
        lambda db, user, schon: _gibt(
            _chatlaeufe(db, user).filter(
                AiRun.reasoning.is_(True),
                AiRun.status == "completed",
                or_(AiRun.reasoning_effort.is_(None), AiRun.reasoning_effort.in_(("high", "xhigh", "max"))),
            )
        ),
    ),
    (
        "ai_model_switcher",
        lambda db, user, schon: (
            _chatlaeufe(db, user)
            .filter(AiRun.provider_id.isnot(None))
            .with_entities(func.count(func.distinct(AiRun.provider_id)))
            .scalar()
            or 0
        )
        >= 2,
    ),
    ("ai_satellite_eye", lambda db, user, schon: _gibt(_werkzeug_im_chat(db, user, "analyze_region"))),
    ("ai_sightseeing_tour", lambda db, user, schon: _zeitreise(db, user)),
    ("ai_memory_keeper", lambda db, user, schon: _audit(db, user, "ai.memory.created")),
    (
        "ai_skill_user",
        lambda db, user, schon: _gibt(_werkzeug_im_chat(db, user, "read_skill"))
        or _gibt(db.query(AiSkill.id).filter(AiSkill.created_by == user.id)),
    ),
    (
        "ai_server_medic",
        lambda db, user, schon: _gibt(
            db.query(AiGuardianRepair.id).filter(
                AiGuardianRepair.user_id == user.id, AiGuardianRepair.phase == "erledigt"
            )
        )
        or _ki_vorschlag_ausgefuehrt(db, user, "propose_server_repair"),
    ),
    ("ai_calendar_assistant", lambda db, user, schon: _ki_vorschlag_ausgefuehrt(db, user, "propose_calendar_event_create")),
    ("ai_notes_scribe", lambda db, user, schon: _ki_vorschlag_ausgefuehrt(db, user, "propose_note_create", "propose_note_update")),
    ("ai_file_analyzer", lambda db, user, schon: _anhang(db, user, bild=False)),
    ("ai_vision_expert", lambda db, user, schon: _anhang(db, user, bild=True)),
    ("ai_autonomous_pilot", lambda db, user, schon: _autonome_kette(db, user)),
    # Social
    (
        "social_handshake",
        lambda db, user, schon: _gibt(
            db.query(UserFriend.id).filter(
                or_(UserFriend.user_id == user.id, UserFriend.friend_id == user.id),
                UserFriend.status == "accepted",
            )
        ),
    ),
    ("social_group_founder", lambda db, user, schon: _gibt(_eigene_gruppen(db, user))),
    ("social_invite_sharer", lambda db, user, schon: _gruppe_mit_fremden(db, user)),
    ("social_role_architect", lambda db, user, schon: _gruppenrollen(db, user)),
    (
        "social_status_story",
        lambda db, user, schon: _gibt(db.query(ChatStory.id).filter(ChatStory.user_id == user.id)),
    ),
    ("social_public_ambassador", lambda db, user, schon: user.social_privacy == "public"),
    ("social_squad_leader", lambda db, user, schon: _grosse_gruppe(db, user)),
    # Team
    ("team_member_joined", lambda db, user, schon: _team_beigetreten(db, user)),
    ("team_created", lambda db, user, schon: _team_gegruendet(db, user)),
    ("team_permission_delegate", lambda db, user, schon: _team_rechte_delegiert(db, user)),
    ("team_shared_server", lambda db, user, schon: _team_server(db, user)),
    ("team_role_assigned", lambda db, user, schon: _team_rollen(db, user)),
    ("team_five_collaborators", lambda db, user, schon: _grosses_team(db, user)),
    # Sammelabzeichen
    (
        "starter_onboarding_done",
        _alle(
            "starter_first_step",
            "starter_security_first",
            "starter_profile_setup",
            "starter_timezone_set",
            "starter_vault_master",
        ),
    ),
    (
        "ai_symbiosis",
        lambda db, user, schon: "ai_notes_scribe" in schon
        and bool({"ai_collaborator", "ai_server_medic", "ai_autonomous_pilot"} & schon)
        and _ueberwacht(db, user),
    ),
    ("security_fortress", lambda db, user, schon: bool(user.two_factor_enabled) and "starter_autolock" in schon),
    (
        "team_harmony",
        lambda db, user, schon: {"team_shared_server", "backup_guardian", "social_zero_knowledge"} <= schon
        and bool({"team_member_joined", "team_created"} & schon),
    ),
]
