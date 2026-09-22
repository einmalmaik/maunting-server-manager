"""Der Gruppenzustand: was der Server damit darf und was nicht.

Die eigenen Rollen einer Gruppe standen bis 09/2026 nirgends — der Dialog
meldete „Rolle erstellt" und legte sie auf React-State. Jetzt gibt es eine
Ablage dafuer, und die Zusage an den Betreiber lautet: verschluesselt, mit dem
Schluessel der Mitglieder, und der Server lernt nichts.

Diese Datei haelt die drei Zusagen fest, die man **am Server** pruefen kann:

* **Klartext kommt nicht hinein.** Nicht „sollte nicht", sondern wird an der
  Schemagrenze abgewiesen — mit derselben Pruefung, die auch eine Nachricht
  durchlaeuft.
* **Die Revision geht nie zurueck.** Zero-Knowledge schuetzt den Inhalt, nicht
  die Ablage: wer schreiben darf, koennte sonst einen alten Stand zuruecklegen
  und damit einen Rechteentzug ruecknehmen, ohne je etwas entschluesselt zu
  haben. Dieselbe Lehre wie beim Passwort-Tresor.
* **Wer schreiben darf, ist geprueft** — als zweites Schloss neben dem
  Gruppenschluessel, solange es die Mitgliederzeile noch gibt.

Was hier **nicht** geprueft werden kann: ob der Block echt ist. Die
Unterschrift liegt darin, nicht daneben, und pruefen koennen sie nur die
Mitglieder. Dafuer ist `frontend/src/services/gruppenKonfig.test.ts` zustaendig.
"""

from __future__ import annotations

import base64

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session

from models import ChatGroup, ChatGroupConfig, User
from schemas.social import ChatGroupConfigWrite
from services.social_service import SocialService


# ── Hilfen ──────────────────────────────────────────────────────────────────


def _blob(fuellung: int = 7) -> str:
    """Ein formal gueltiger Gruppenumschlag ohne Inhalt.

    Zusammengesetzt statt abgeschrieben: die Pruefung verlangt einen
    Schluessel-Kenner aus 16 Hexzeichen, mindestens 28 Byte Rumpf und einen
    Anfang, der nicht aus Null-Bytes besteht (ein Null-IV gilt als Bruch). Der
    Parameter macht zwei Blocks unterscheidbar, damit ein Test zeigen kann,
    dass wirklich der neue gespeichert wurde.
    """
    rumpf = bytes((fuellung + i) % 251 + 1 for i in range(40))
    return "sv-e2ee-group-v1:" + "a1b2c3d4e5f60789" + "." + base64.b64encode(rumpf).decode()


def _fremder(db: Session) -> User:
    """Jemand mit Konto, aber ohne etwas mit dieser Gruppe zu tun."""
    vorhanden = db.query(User).filter(User.username == "fremder").first()
    if vorhanden:
        return vorhanden
    from services.auth_service import AuthService

    user = AuthService.create_user(db, "fremder", "fremder@test.de", "FremdPass123!")
    user.email_verified = True
    db.commit()
    db.refresh(user)
    return user


def _gruppe(db: Session, besitzer: User, mitglied: User, rechte: str | None = None):
    gruppe = SocialService.create_group(db, besitzer, "Zustandsgruppe")
    SocialService.join_group_by_invite_code(db, mitglied, gruppe.invite_code)
    if rechte is not None:
        mitgliedschaft = SocialService.get_group_member(db, gruppe.id, mitglied.id)
        mitgliedschaft.permissions = rechte
        db.commit()
    return gruppe


# ── Klartext kommt nicht hinein ─────────────────────────────────────────────


def test_klartext_wird_an_der_schemagrenze_abgewiesen() -> None:
    # Genau der Fehler, der sonst niemandem auffaellt: alles funktioniert, nur
    # stehen die Rollennamen lesbar in der Datenbank.
    with pytest.raises(ValidationError):
        ChatGroupConfigWrite(blob='{"rollen": ["Moderator"]}', erwartete_revision=0)


def test_umschlag_ohne_schluesselkenner_wird_abgewiesen() -> None:
    nackt = base64.b64encode(bytes(range(1, 41))).decode()
    with pytest.raises(ValidationError):
        ChatGroupConfigWrite(blob="sv-e2ee-group-v1:" + nackt, erwartete_revision=0)


def test_gueltiger_umschlag_geht_durch() -> None:
    geschrieben = ChatGroupConfigWrite(blob=_blob(), erwartete_revision=0)
    assert geschrieben.blob == _blob()


def test_uebergrosser_block_wird_abgewiesen() -> None:
    # Ohne Obergrenze waere die Zeile keine Gruppenkonfiguration, sondern eine
    # Ablage, die jedes Mitglied beliebig fuellen kann — der Server kann den
    # Inhalt ja nicht beurteilen.
    riesig = _blob() + "A" * (256 * 1024)
    with pytest.raises(ValidationError):
        ChatGroupConfigWrite(blob=riesig, erwartete_revision=0)


# ── Die Revision geht nie zurueck ───────────────────────────────────────────


def test_erster_zustand_bekommt_revision_eins(
    db: Session, owner_user: User, regular_user: User
) -> None:
    gruppe = _gruppe(db, owner_user, regular_user)

    eintrag = SocialService.write_group_config(
        db, group_id=gruppe.id, blob=_blob(1), erwartete_revision=0, caller=owner_user
    )

    assert eintrag.revision == 1
    assert eintrag.blob == _blob(1)


def test_zweiter_zustand_zaehlt_weiter(
    db: Session, owner_user: User, regular_user: User
) -> None:
    gruppe = _gruppe(db, owner_user, regular_user)
    SocialService.write_group_config(
        db, group_id=gruppe.id, blob=_blob(1), erwartete_revision=0, caller=owner_user
    )

    zweiter = SocialService.write_group_config(
        db, group_id=gruppe.id, blob=_blob(2), erwartete_revision=1, caller=owner_user
    )

    assert zweiter.revision == 2
    assert zweiter.blob == _blob(2)


def test_alter_stand_wird_nicht_zurueckgespielt(
    db: Session, owner_user: User, regular_user: User
) -> None:
    """Der Kern: ein Rechteentzug laesst sich nicht blind ruecknehmen."""
    gruppe = _gruppe(db, owner_user, regular_user)
    SocialService.write_group_config(
        db, group_id=gruppe.id, blob=_blob(1), erwartete_revision=0, caller=owner_user
    )
    SocialService.write_group_config(
        db, group_id=gruppe.id, blob=_blob(2), erwartete_revision=1, caller=owner_user
    )

    with pytest.raises(HTTPException) as fehler:
        SocialService.write_group_config(
            db, group_id=gruppe.id, blob=_blob(1), erwartete_revision=1, caller=owner_user
        )

    assert fehler.value.status_code == 409
    assert fehler.value.detail["aktuelle_revision"] == 2
    # Und der neuere Stand steht noch.
    assert SocialService.get_group_config(db, gruppe.id, owner_user).blob == _blob(2)


def test_zweiter_gruender_auf_revision_null_bekommt_konflikt(
    db: Session, owner_user: User, regular_user: User
) -> None:
    # Zwei Geraete, die gleichzeitig den ersten Stand schreiben: eines gewinnt,
    # das andere liest neu. Kein Serverfehler.
    gruppe = _gruppe(db, owner_user, regular_user)
    SocialService.write_group_config(
        db, group_id=gruppe.id, blob=_blob(1), erwartete_revision=0, caller=owner_user
    )

    with pytest.raises(HTTPException) as fehler:
        SocialService.write_group_config(
            db, group_id=gruppe.id, blob=_blob(9), erwartete_revision=0, caller=owner_user
        )

    assert fehler.value.status_code == 409
    assert fehler.value.detail["aktuelle_revision"] == 1


def test_uebersprungene_revision_wird_abgewiesen(
    db: Session, owner_user: User, regular_user: User
) -> None:
    gruppe = _gruppe(db, owner_user, regular_user)
    SocialService.write_group_config(
        db, group_id=gruppe.id, blob=_blob(1), erwartete_revision=0, caller=owner_user
    )

    with pytest.raises(HTTPException) as fehler:
        SocialService.write_group_config(
            db, group_id=gruppe.id, blob=_blob(5), erwartete_revision=4, caller=owner_user
        )

    assert fehler.value.status_code == 409


# ── Wer schreiben darf ──────────────────────────────────────────────────────


def test_einfaches_mitglied_darf_nicht_schreiben(
    db: Session, owner_user: User, regular_user: User
) -> None:
    gruppe = _gruppe(db, owner_user, regular_user, rechte="send_messages")

    with pytest.raises(HTTPException) as fehler:
        SocialService.write_group_config(
            db, group_id=gruppe.id, blob=_blob(), erwartete_revision=0, caller=regular_user
        )

    assert fehler.value.status_code == 403


def test_mitglied_mit_manage_roles_darf_schreiben(
    db: Session, owner_user: User, regular_user: User
) -> None:
    # `manage_roles` steht bewusst nicht in den Moderationsrechten, faellt
    # Eigentuemern also nicht automatisch zu — und muss deshalb hier
    # ausdruecklich wirken.
    gruppe = _gruppe(db, owner_user, regular_user, rechte="send_messages,manage_roles")

    eintrag = SocialService.write_group_config(
        db, group_id=gruppe.id, blob=_blob(3), erwartete_revision=0, caller=regular_user
    )

    assert eintrag.revision == 1


def test_eigentuemer_darf_ohne_eingetragenes_recht(
    db: Session, owner_user: User, regular_user: User
) -> None:
    gruppe = _gruppe(db, owner_user, regular_user)
    assert SocialService.darf_gruppenzustand_schreiben(db, gruppe.id, owner_user.id) is True


def test_nichtmitglied_bekommt_vierhundertvier_statt_verboten(
    db: Session, owner_user: User, regular_user: User
) -> None:
    # 403 waere die Auskunft „diese Gruppe gibt es". Die gibt es nicht.
    gruppe = _gruppe(db, owner_user, regular_user)
    aussen = _fremder(db)

    with pytest.raises(HTTPException) as lesen:
        SocialService.get_group_config(db, gruppe.id, aussen)
    assert lesen.value.status_code == 404

    with pytest.raises(HTTPException) as schreiben:
        SocialService.write_group_config(
            db, group_id=gruppe.id, blob=_blob(), erwartete_revision=0, caller=aussen
        )
    assert schreiben.value.status_code == 404


# ── Lesen ───────────────────────────────────────────────────────────────────


def test_ohne_zustand_ist_die_antwort_leer(
    db: Session, owner_user: User, regular_user: User
) -> None:
    gruppe = _gruppe(db, owner_user, regular_user)
    assert SocialService.get_group_config(db, gruppe.id, owner_user) is None


def test_jedes_mitglied_darf_lesen(
    db: Session, owner_user: User, regular_user: User
) -> None:
    """Lesen heisst: den Block bekommen. Oeffnen kann ihn nur, wer den
    Gruppenschluessel hat — und das ist die eigentliche Schranke."""
    gruppe = _gruppe(db, owner_user, regular_user, rechte="send_messages")
    SocialService.write_group_config(
        db, group_id=gruppe.id, blob=_blob(4), erwartete_revision=0, caller=owner_user
    )

    gelesen = SocialService.get_group_config(db, gruppe.id, regular_user)

    assert gelesen is not None
    assert gelesen.blob == _blob(4)
    assert gelesen.revision == 1


def test_der_server_veraendert_den_block_nicht(
    db: Session, owner_user: User, regular_user: User
) -> None:
    gruppe = _gruppe(db, owner_user, regular_user)
    hingelegt = _blob(6)
    SocialService.write_group_config(
        db, group_id=gruppe.id, blob=hingelegt, erwartete_revision=0, caller=owner_user
    )

    db.expire_all()
    roh = db.query(ChatGroupConfig).filter(ChatGroupConfig.group_id == gruppe.id).first()

    assert roh.blob == hingelegt


# ── Aufraeumen ──────────────────────────────────────────────────────────────


def test_geloeschte_gruppe_hinterlaesst_keinen_block(
    db: Session, owner_user: User, regular_user: User
) -> None:
    """Sonst bliebe ein verschluesselter Rest liegen, den niemand mehr oeffnet."""
    gruppe = _gruppe(db, owner_user, regular_user)
    SocialService.write_group_config(
        db, group_id=gruppe.id, blob=_blob(), erwartete_revision=0, caller=owner_user
    )
    gruppen_id = gruppe.id

    db.query(ChatGroup).filter(ChatGroup.id == gruppen_id).delete()
    db.commit()

    assert (
        db.query(ChatGroupConfig).filter(ChatGroupConfig.group_id == gruppen_id).first()
        is None
    )
