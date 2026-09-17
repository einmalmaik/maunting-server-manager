"""Das Rechtevokabular der Gruppen: ein Name, eine Wirkung.

Der Rechte-Dialog schrieb eine Zeit lang `call_start`, geprueft wurde
`start_group_calls`. Ein gesetzter Haken blieb wirkungslos, und niemand merkte
es, weil beide Seiten fuer sich stimmig waren. Diese Tests halten die Bruecke
zwischen altem und neuem Namen fest und sorgen dafuer, dass ein dritter Name
gar nicht erst in die Datenbank kommt.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import User
from services.social_service import (
    GROUP_PERMISSION_ALIASES,
    GROUP_PERMISSIONS,
    SocialService,
)


# ── Aufloesung alter Namen ──────────────────────────────────────────────────


def test_expand_uebersetzt_alte_namen() -> None:
    assert SocialService.expand_group_permissions("call_start") == {"start_group_calls"}
    assert SocialService.expand_group_permissions("call_join") == {"join_group_calls"}
    assert SocialService.expand_group_permissions("call_share") == {"share_screen"}


def test_call_moderate_wird_zu_zwei_rechten() -> None:
    # Bewusst: wer frueher „moderieren" hatte, konnte beides. Die Aufspaltung
    # darf ihm nichts wegnehmen.
    assert SocialService.expand_group_permissions("call_moderate") == {
        "mute_in_calls",
        "kick_from_calls",
    }


def test_expand_vertraegt_leerzeichen_und_leere_eintraege() -> None:
    assert SocialService.expand_group_permissions(" send_messages , , call_join ") == {
        "send_messages",
        "join_group_calls",
    }


def test_expand_bei_none_ist_leer() -> None:
    assert SocialService.expand_group_permissions(None) == set()


def test_jeder_alias_zeigt_auf_bekannte_rechte() -> None:
    # Ein Alias, der auf einen Namen zeigt, den niemand prueft, waere genau der
    # Fehler, den diese Datei verhindern soll.
    for ziele in GROUP_PERMISSION_ALIASES.values():
        for ziel in ziele:
            assert ziel in GROUP_PERMISSIONS


# ── Schreibschutz ───────────────────────────────────────────────────────────


def test_unbekanntes_recht_wird_abgewiesen() -> None:
    with pytest.raises(HTTPException) as fehler:
        SocialService.assert_known_permissions("send_messages,call_stert")
    assert fehler.value.status_code == 422
    assert "call_stert" in fehler.value.detail


def test_bekanntes_recht_wird_kanonisch_gespeichert() -> None:
    assert (
        SocialService.assert_known_permissions("call_join,send_messages")
        == "join_group_calls,send_messages"
    )


def test_altes_und_neues_recht_nebeneinander_gibt_keine_dublette() -> None:
    assert (
        SocialService.assert_known_permissions("call_join,join_group_calls")
        == "join_group_calls"
    )


def test_none_bleibt_none() -> None:
    assert SocialService.assert_known_permissions(None) is None


# ── Wirkung an einer echten Gruppe ──────────────────────────────────────────


def _gruppe(db: Session, besitzer: User, mitglied: User, rechte: str | None):
    gruppe = SocialService.create_group(db, besitzer, "Rechtegruppe")
    SocialService.join_group_by_invite_code(db, mitglied, gruppe.invite_code)
    if rechte is not None:
        mitgliedschaft = SocialService.get_group_member(db, gruppe.id, mitglied.id)
        # Direkt an der Spalte vorbei am Schreibschutz: so sieht die Datenbank
        # nach einem Update von vor dieser Aenderung aus.
        mitgliedschaft.permissions = rechte
        db.commit()
    return gruppe


def test_alter_haken_wirkt_ohne_datenmigration(
    db: Session, owner_user: User, regular_user: User
) -> None:
    gruppe = _gruppe(db, owner_user, regular_user, "call_join")
    assert SocialService.has_group_permission(
        db, gruppe.id, regular_user.id, "join_group_calls"
    )


def test_call_moderate_gibt_stumm_und_rauswurf(
    db: Session, owner_user: User, regular_user: User
) -> None:
    gruppe = _gruppe(db, owner_user, regular_user, "call_moderate")
    assert SocialService.has_group_permission(db, gruppe.id, regular_user.id, "mute_in_calls")
    assert SocialService.has_group_permission(
        db, gruppe.id, regular_user.id, "kick_from_calls"
    )


def test_ohne_eintrag_kein_anrufrecht(
    db: Session, owner_user: User, regular_user: User
) -> None:
    gruppe = _gruppe(db, owner_user, regular_user, "")
    assert not SocialService.has_group_permission(
        db, gruppe.id, regular_user.id, "join_group_calls"
    )
    assert not SocialService.has_group_permission(
        db, gruppe.id, regular_user.id, "mute_in_calls"
    )


def test_eigentuemer_hat_anrufrechte_ohne_eintrag(
    db: Session, owner_user: User, regular_user: User
) -> None:
    # Er koennte sie sich ohnehin jederzeit selbst geben. Ein Eigentuemer, der
    # seinen eigenen Anruf nicht moderieren darf, waere nur eine Stolperfalle.
    gruppe = _gruppe(db, owner_user, regular_user, "")
    for recht in (
        "start_group_calls",
        "join_group_calls",
        "share_screen",
        "mute_in_calls",
        "kick_from_calls",
    ):
        assert SocialService.has_group_permission(db, gruppe.id, owner_user.id, recht)


def test_eigentuemer_bekommt_keine_nicht_anruf_rechte_geschenkt(
    db: Session, owner_user: User, regular_user: User
) -> None:
    # Die Abkuerzung gilt nur fuer Anrufe. Sonst waere sie eine stille
    # Rechteausweitung ueber das ganze Gruppensystem.
    gruppe = _gruppe(db, owner_user, regular_user, "")
    eigene = SocialService.get_group_member(db, gruppe.id, owner_user.id)
    eigene.permissions = ""
    db.commit()
    assert not SocialService.has_group_permission(
        db, gruppe.id, owner_user.id, "delete_messages"
    )


def test_kein_mitglied_hat_nichts(
    db: Session, owner_user: User, regular_user: User
) -> None:
    gruppe = _gruppe(db, owner_user, regular_user, "")
    assert not SocialService.has_group_permission(
        db, gruppe.id, 999_999, "join_group_calls"
    )


def test_rollenupdate_weist_unbekanntes_recht_ab(
    db: Session, owner_user: User, regular_user: User
) -> None:
    gruppe = _gruppe(db, owner_user, regular_user, None)
    with pytest.raises(HTTPException) as fehler:
        SocialService.update_member_role_permissions(
            db, gruppe.id, regular_user.id, "member", "join_group_kalls", owner_user
        )
    assert fehler.value.status_code == 422


def test_gruppenliste_meldet_die_anrufflaggen(
    db: Session, owner_user: User, regular_user: User
) -> None:
    gruppe = _gruppe(db, owner_user, regular_user, "call_moderate,call_join")
    eintrag = next(
        g for g in SocialService.list_user_groups(db, regular_user.id) if g["id"] == gruppe.id
    )
    assert eintrag["can_join_call"] is True
    assert eintrag["can_mute_others"] is True
    assert eintrag["can_kick_from_call"] is True
    assert eintrag["can_start_call"] is False
    assert eintrag["can_share_screen"] is False


def test_gruppenliste_meldet_rechte_kanonisch(
    db: Session, owner_user: User, regular_user: User
) -> None:
    # Die Oberflaeche soll nur ein Vokabular kennen, auch wenn in der Spalte
    # noch der alte Name steht.
    gruppe = _gruppe(db, owner_user, regular_user, "call_join")
    eintrag = next(
        g for g in SocialService.list_user_groups(db, regular_user.id) if g["id"] == gruppe.id
    )
    eigener = next(m for m in eintrag["members"] if m["user_id"] == regular_user.id)
    assert eigener["permissions"] == "join_group_calls"
