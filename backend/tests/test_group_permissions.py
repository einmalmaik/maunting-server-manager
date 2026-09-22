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
    GROUP_ROLE_ONLY_PERMISSIONS,
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


# ── Alle wecken und anheften ────────────────────────────────────────────────
#
# Diese beiden Rechte sind anders als alle anderen: der Server kann sie nicht
# durchsetzen, weil er den Inhalt einer Nachricht nicht liest. Durchgesetzt
# werden sie vom **empfangenden** Geraet, und das befragt dafuer die Marke am
# *Absender* aus der Gruppenantwort. Laufen Marke und Pruefung auseinander,
# klingelt ein Handy bei jemandem, der das nicht duerfte — und kein
# 403 faengt es ab, weil nie ein Aufruf stattfindet.


def test_beide_rechte_stehen_im_vokabular() -> None:
    assert "mention_everyone" in GROUP_PERMISSIONS
    assert "pin_messages" in GROUP_PERMISSIONS


def test_tippfehler_im_neuen_recht_wird_abgewiesen() -> None:
    with pytest.raises(HTTPException) as fehler:
        SocialService.assert_known_permissions("mention_everybody")
    assert fehler.value.status_code == 422
    assert "mention_everybody" in fehler.value.detail


def test_mitglied_ohne_eintrag_darf_nicht_alle_wecken(
    db: Session, owner_user: User, regular_user: User
) -> None:
    # Der sichere Ausgangszustand: bestehende Gruppen bekommen nichts dazu.
    gruppe = _gruppe(db, owner_user, regular_user, "")
    assert not SocialService.has_group_permission(
        db, gruppe.id, regular_user.id, "mention_everyone"
    )
    assert not SocialService.has_group_permission(db, gruppe.id, regular_user.id, "pin_messages")


def test_vergebenes_recht_wirkt(db: Session, owner_user: User, regular_user: User) -> None:
    gruppe = _gruppe(db, owner_user, regular_user, "mention_everyone")
    assert SocialService.has_group_permission(
        db, gruppe.id, regular_user.id, "mention_everyone"
    )
    assert not SocialService.has_group_permission(db, gruppe.id, regular_user.id, "pin_messages")


def test_eigentuemer_darf_ohne_eintrag_wecken_und_anheften(
    db: Session, owner_user: User, regular_user: User
) -> None:
    # Dieselbe Begruendung wie bei den Anrufrechten: er kann sie sich mit zwei
    # Klicks selbst geben. Ein Eigentuemer, der seine eigene Gruppe nicht
    # erreicht, waere kein Schutz, sondern ein Raetsel.
    gruppe = _gruppe(db, owner_user, regular_user, "")
    eigene = SocialService.get_group_member(db, gruppe.id, owner_user.id)
    eigene.permissions = ""
    db.commit()
    assert SocialService.has_group_permission(db, gruppe.id, owner_user.id, "mention_everyone")
    assert SocialService.has_group_permission(db, gruppe.id, owner_user.id, "pin_messages")


def test_marke_je_mitglied_stimmt_mit_der_pruefung_ueberein(
    db: Session, owner_user: User, regular_user: User
) -> None:
    """Die eigentliche Zusicherung dieser Datei.

    Die Marke in der Gruppenantwort und ``has_group_permission`` muessen
    dasselbe sagen — fuer jedes Mitglied, in jeder Rolle, bei jedem Recht.
    Sie kommen aus derselben ``effective_permissions``; dieser Test haelt fest,
    dass das so bleibt.
    """
    for rechte in (None, "", "mention_everyone", "pin_messages", "send_messages"):
        gruppe = _gruppe(db, owner_user, regular_user, rechte)
        eintrag = next(
            g
            for g in SocialService.list_user_groups(db, regular_user.id)
            if g["id"] == gruppe.id
        )
        for mitglied in eintrag["members"]:
            for recht, marke in (
                ("mention_everyone", "can_mention_everyone"),
                ("pin_messages", "can_pin_messages"),
            ):
                assert mitglied[marke] is SocialService.has_group_permission(
                    db, gruppe.id, mitglied["user_id"], recht
                ), f"{marke} weicht ab bei {mitglied['role']} mit {rechte!r}"


def test_gruppenmarke_beschreibt_mich_selbst(
    db: Session, owner_user: User, regular_user: User
) -> None:
    # Die Marke an der Gruppe sagt „darf ich den Knopf sehen", die am Mitglied
    # „durfte dieser Absender". Beim Eigentuemer faellt beides zusammen, beim
    # Mitglied ohne Recht nicht — sonst waere die Unterscheidung zufaellig
    # richtig und niemand merkte ihr Fehlen.
    gruppe = _gruppe(db, owner_user, regular_user, "")
    meins = next(
        g for g in SocialService.list_user_groups(db, regular_user.id) if g["id"] == gruppe.id
    )
    assert meins["can_mention_everyone"] is False
    besitzer = next(m for m in meins["members"] if m["user_id"] == owner_user.id)
    assert besitzer["can_mention_everyone"] is True


# ── Nur-Rollen-Rechte gehoeren nicht an @everyone ───────────────────────────


def test_manage_roles_nicht_als_standardrecht(
    db: Session, owner_user: User, regular_user: User
) -> None:
    # Der Dialog bot es nie an, das Backend nahm es klaglos: ein PATCH auf die
    # Standardrechte machte jedes einfache Mitglied zum Rollenverwalter, und
    # damit zum Administrator. Der Haken war versteckt, die Regel fehlte.
    gruppe = _gruppe(db, owner_user, regular_user, None)
    with pytest.raises(HTTPException) as fehler:
        SocialService.update_group_default_permissions(
            db,
            group_id=gruppe.id,
            default_permissions="send_messages,manage_roles",
            caller=owner_user,
        )
    assert fehler.value.status_code == 422
    assert "manage_roles" in fehler.value.detail

    db.rollback()
    frisch = SocialService.get_group_member(db, gruppe.id, regular_user.id)
    assert frisch is not None
    assert not SocialService.has_group_permission(
        db, gruppe.id, regular_user.id, "manage_roles"
    )


def test_standardrechte_ohne_nur_rollen_recht_gehen_durch(
    db: Session, owner_user: User, regular_user: User
) -> None:
    # Die Schranke darf nur das eine Recht treffen, nicht den ganzen Vorgang.
    gruppe = _gruppe(db, owner_user, regular_user, None)
    aktualisiert = SocialService.update_group_default_permissions(
        db,
        group_id=gruppe.id,
        default_permissions="send_messages,attach_media",
        caller=owner_user,
    )
    assert aktualisiert.default_permissions == "attach_media,send_messages"


def test_nur_rollen_recht_bleibt_an_einer_rolle_erlaubt(
    db: Session, owner_user: User, regular_user: User
) -> None:
    # Genau dafuer ist es da. Verboten ist nur der Weg ueber @everyone.
    gruppe = _gruppe(db, owner_user, regular_user, None)
    SocialService.update_member_role_permissions(
        db,
        group_id=gruppe.id,
        target_user_id=regular_user.id,
        role="admin",
        permissions="send_messages,manage_roles",
        caller=owner_user,
    )
    assert SocialService.has_group_permission(
        db, gruppe.id, regular_user.id, "manage_roles"
    )


def test_jedes_nur_rollen_recht_ist_ein_bekanntes_recht() -> None:
    # Ein Eintrag, der auf einen Namen zeigt, den das Vokabular nicht kennt,
    # waere eine Schranke vor einer Tuer, die es nicht gibt.
    assert GROUP_ROLE_ONLY_PERMISSIONS <= GROUP_PERMISSIONS
