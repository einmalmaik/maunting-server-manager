"""Was eine Benutzerloeschung blockiert, muss sie sagen — nicht abstuerzen.

Drei Fremdschluessel des Panels tragen ``ON DELETE RESTRICT`` und zeigen alle
auf denselben Benutzer. Kein Loeschpfad hat je nachgesehen: ``db.delete(user)``
lief in den Fremdschluessel, die ``IntegrityError`` fiel ungefangen aus
``db.commit()`` und wurde zu einer nackten HTTP 500 — der Account blieb
bestehen, ohne dass irgendwo stand, warum. Wer einmal ein Team gegruendet hatte,
war dauerhaft nicht mehr loeschbar.

Die Tests haengen an der scharfen Fremdschluesselpruefung aus ``conftest.py``.
Ohne sie wuerde SQLite jeden dieser Faelle stumm durchwinken und die Datei waere
gruen, ohne etwas zu belegen — die Zusage darueber steht ausdruecklich in
``test_schema_constraints.py``.
"""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import (
    HosterIntegration,
    Server,
    ServerCredentialBinding,
    Team,
    User,
    UserCredential,
)
from services.auth_service import AuthService


def _csrf(cookies: dict) -> dict:
    return {"X-CSRF-Token": cookies.get("__Secure-csrf_token", "")}


def _kandidat(db: Session) -> User:
    """Ein gewoehnlicher Benutzer, den der Owner gleich zu loeschen versucht."""
    user = AuthService.create_user(
        db, "loeschkandidat", "loeschkandidat@test.de", "UserPass123!"
    )
    user.email_verified = True
    db.commit()
    db.refresh(user)
    return user


def test_der_gruender_eines_teams_bekommt_eine_antwort_statt_eines_absturzes(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict
) -> None:
    """Der belegte Kern des Befunds, auf dem Weg, den ein Admin wirklich geht."""
    kandidat = _kandidat(db)
    db.add(Team(name="Crew", owner_user_id=kandidat.id, personal_for_user_id=None))
    db.commit()

    antwort = client.delete(
        f"/api/admin/users/{kandidat.id}",
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )

    assert antwort.status_code == 409
    # Der Name muss drinstehen: ohne ihn weiss der Betreiber nicht, was er
    # aufloesen soll, und die Meldung waere so wertlos wie die 500 davor.
    assert "Crew" in antwort.json()["detail"]
    assert db.query(User).filter(User.id == kandidat.id).first() is not None


def test_gebundene_zugangsdaten_nennen_den_grund_ihrer_sperre(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict
) -> None:
    """Das CASCADE des Credentials laeuft in das RESTRICT seiner Bindung."""
    kandidat = _kandidat(db)
    server = Server(
        name="cred-server", game_type="minecraft", install_dir="/tmp/cred-server"
    )
    db.add(server)
    db.flush()
    credential = UserCredential(
        user_id=kandidat.id,
        kind="github_token",
        label="standard",
        secret_encrypted="x",
    )
    db.add(credential)
    db.flush()
    db.add(
        ServerCredentialBinding(
            server_id=server.id, kind="github_token", credential_id=credential.id
        )
    )
    db.commit()

    antwort = client.delete(
        f"/api/admin/users/{kandidat.id}",
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )

    assert antwort.status_code == 409
    assert "Zugangsdaten" in antwort.json()["detail"]
    assert db.query(User).filter(User.id == kandidat.id).first() is not None


def test_das_dienstkonto_einer_hoster_anbindung_bleibt_bestehen(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict
) -> None:
    """Derselbe Defekt am dritten RESTRICT — gleicher Bauplan, gleiche Wirkung."""
    kandidat = _kandidat(db)
    db.add(
        HosterIntegration(
            name="Beispielshop",
            slug="beispielshop",
            service_user_id=kandidat.id,
            api_key_hash="a" * 64,
        )
    )
    db.commit()

    antwort = client.delete(
        f"/api/admin/users/{kandidat.id}",
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )

    assert antwort.status_code == 409
    assert "Beispielshop" in antwort.json()["detail"]
    assert db.query(User).filter(User.id == kandidat.id).first() is not None


def test_das_persoenliche_team_haelt_niemanden_fest(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict
) -> None:
    """Gegenprobe: die Vorpruefung darf nicht jeden Benutzer unloeschbar machen.

    Dieser Fall ginge auch ohne den Fix durch (auf SQLite greift das CASCADE
    ueber ``personal_for_user_id``). Er steht hier trotzdem, weil er die andere
    Haelfte der Zusage haelt: das Ein-Mann-Team gehoert nur diesem Benutzer und
    darf ihn nicht blockieren.
    """
    kandidat = _kandidat(db)
    db.add(
        Team(
            name=kandidat.username,
            owner_user_id=kandidat.id,
            personal_for_user_id=kandidat.id,
        )
    )
    db.commit()

    antwort = client.delete(
        f"/api/admin/users/{kandidat.id}",
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )

    assert antwort.status_code == 200
    assert db.query(User).filter(User.id == kandidat.id).first() is None
    assert db.query(Team).filter(Team.personal_for_user_id == kandidat.id).first() is None


def test_die_selbstloeschung_nimmt_denselben_weg(
    db: Session, regular_user: User
) -> None:
    """Der zweite Loeschpfad hatte dieselbe Luecke und wurde leicht uebersehen.

    `auth_service.delete_account_atomically` raeumt JwtBlacklist,
    EmailVerification, AuditLog und ServerPermission ab — Teams kamen darin nie
    vor. Ohne den Fix fliegt hier eine `IntegrityError` statt einer
    `HTTPException`.
    """
    db.add(Team(name="Crew", owner_user_id=regular_user.id, personal_for_user_id=None))
    db.commit()

    with pytest.raises(HTTPException) as fehler:
        AuthService.delete_account_atomically(db, regular_user)

    assert fehler.value.status_code == 409
    db.rollback()
    assert db.query(User).filter(User.id == regular_user.id).first() is not None


def _messenger_spuren(db: Session, user: User) -> User:
    """Was fast jedes Konto im Messenger hinterlässt: Anwesenheit beim
    Verbinden, Nutzungszeit, Meilensteine, Freundschaften, eine Story."""
    from datetime import datetime, timedelta, timezone

    from models import ChatStory, UserAchievement, UserActivityTime, UserFriend, UserPresence

    freund = AuthService.create_user(db, "freund_des_kandidaten", "freund@test.de", "UserPass123!")
    db.add_all(
        [
            UserPresence(user_id=user.id),
            UserActivityTime(user_id=user.id, category="general", seconds=60),
            UserAchievement(user_id=user.id, achievement_id="starter_first_step"),
            UserFriend(user_id=user.id, friend_id=freund.id, status="accepted"),
            UserFriend(user_id=freund.id, friend_id=user.id, status="accepted"),
            ChatStory(
                user_id=user.id,
                content="x",
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            ),
        ]
    )
    db.commit()
    return freund


def test_admin_loescht_ein_konto_mit_messenger_spuren(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict
) -> None:
    """Jede dieser Zeilen ließ `db.delete(user)` auf NULL setzen statt sie der
    Kaskade zu überlassen: 500, und das Konto blieb bestehen."""
    from models import UserFriend

    kandidat = _kandidat(db)
    freund = _messenger_spuren(db, kandidat)
    kandidat_id = kandidat.id

    antwort = client.delete(
        f"/api/admin/users/{kandidat_id}",
        cookies=owner_cookies,
        headers=_csrf(owner_cookies),
    )

    assert antwort.status_code == 200
    db.expire_all()
    assert db.query(User).filter(User.id == kandidat_id).first() is None
    assert db.query(User).filter(User.id == freund.id).first() is not None
    assert db.query(UserFriend).filter(UserFriend.friend_id == kandidat_id).count() == 0


def test_die_selbstloeschung_mit_messenger_spuren(db: Session, regular_user: User) -> None:
    _messenger_spuren(db, regular_user)
    user_id = regular_user.id

    AuthService.delete_account_atomically(db, regular_user)

    db.expire_all()
    assert db.query(User).filter(User.id == user_id).first() is None


def test_geloeschter_gruppeneigentuemer_nimmt_die_gruppe_nicht_mit(
    db: Session, regular_user: User
) -> None:
    """`chat_groups.owner_user_id` kaskadiert. Ohne Nachfolger verschwände die
    Gruppe für alle Mitglieder mit dem Konto ihres Eigentümers."""
    from models import ChatGroup, ChatGroupMember

    mitglied = AuthService.create_user(db, "gruppen_mitglied", "mitglied@test.de", "UserPass123!")
    admin = AuthService.create_user(db, "gruppen_admin", "gadmin@test.de", "UserPass123!")
    gruppe = ChatGroup(owner_user_id=regular_user.id)
    allein = ChatGroup(owner_user_id=regular_user.id)
    db.add_all([gruppe, allein])
    db.flush()
    db.add_all(
        [
            ChatGroupMember(group_id=gruppe.id, user_id=regular_user.id, role="owner"),
            ChatGroupMember(group_id=gruppe.id, user_id=mitglied.id, role="member"),
            ChatGroupMember(group_id=gruppe.id, user_id=admin.id, role="admin"),
            ChatGroupMember(group_id=allein.id, user_id=regular_user.id, role="owner"),
        ]
    )
    db.commit()
    gruppe_id, allein_id = gruppe.id, allein.id

    AuthService.delete_account_atomically(db, regular_user)

    db.expire_all()
    gruppe = db.query(ChatGroup).filter(ChatGroup.id == gruppe_id).first()
    assert gruppe is not None
    assert gruppe.owner_user_id == admin.id
    rollen = {m.user_id: m.role for m in db.query(ChatGroupMember).filter_by(group_id=gruppe_id)}
    assert rollen == {mitglied.id: "member", admin.id: "owner"}
    # Eine Gruppe, in der sonst niemand ist, fällt mit ihrem Eigentümer weg.
    assert db.query(ChatGroup).filter(ChatGroup.id == allein_id).first() is None
