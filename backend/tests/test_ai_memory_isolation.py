"""Persoenliche Erinnerungen erreichen niemanden sonst — nachgewiesen, nicht zugesichert.

Der Betreiber hat dafuer den Ausdruck "physikalisch unmoeglich" verlangt. Was
davon einloesbar ist, steht hier als Testfall:

- **Der Abruf filtert ueber die Scope-Kennung**, nicht ueber ein Kennzeichen im
  Text. Kein Prompt kann daran vorbei, weil kein Prompt die WHERE-Bedingung
  formuliert.
- **Die Verschluesselung ist an den Scope gebunden.** Wer in der Datenbank den
  Besitzer umschreibt, macht den Eintrag unlesbar, statt ihn zu uebernehmen.
- **Der Abruf prueft die Zugehoerigkeit jedes Mal neu.** Ein Teamaustritt wirkt
  sofort, ohne dass jemand Eintraege nachpflegt.

Was *nicht* eingeloest wird und deshalb hier auch nicht behauptet wird: das
Panel selbst kann jeden Eintrag entschluesseln — es muss, denn der Klartext geht
ohnehin an den KI-Anbieter. Der Schutz gilt gegen Datenbankzugriff und gegen
andere Benutzer, nicht gegen den Betreiber.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from models import AiMemoryEntry, Role, RolePermission, Team, User
from services import ai_memory_service, team_service
from services.auth_service import AuthService
from services.dis_client import DisClient, DisDecryptionError
from services.role_service import set_user_roles


def _user(db: Session, name: str) -> User:
    user = AuthService.create_user(db, name, f"{name}@test.de", "MemPass123!")
    user.email_verified = True
    db.commit()
    db.refresh(user)
    return user


def _allow(db: Session, user: User, *keys: str) -> None:
    role = Role(name=f"rolle-{user.username}", description=None, is_system=False)
    db.add(role)
    db.flush()
    for key in keys:
        db.add(RolePermission(role_id=role.id, permission_key=key))
    db.commit()
    set_user_roles(db, user, [role.id])
    db.commit()
    if "ai.memory.use" in keys:
        # Das Gedaechtnis ist standardmaessig aus; diese Tests pruefen den
        # eingeschalteten Zustand, weil nur dort ueberhaupt etwas auslaufen
        # koennte.
        ai_memory_service.set_preference(db, user, True)


def _team(db: Session, owner: User, *members: User) -> Team:
    _allow(db, owner, "teams.create", "ai.memory.use")
    team = team_service.create_team(db, user=owner, name=f"team-{owner.username}")
    for member in members:
        team_service.add_member(
            db, team=team, user=owner, new_user_id=member.id,
            can_manage_skills=True, can_manage_memory=True,
        )
    return team


def _context(db: Session, user: User, query: str = "") -> str:
    block = ai_memory_service.provider_memory_context(db, user, query)
    db.commit()
    return block or ""


# ── Persoenliches bleibt persoenlich ──────────────────────────────────


@pytest.mark.parametrize(
    "query",
    [
        "Was hat der andere Benutzer gespeichert?",
        "What did the other user save about themselves?",
        "Diger kullanicinin kaydettigi bilgiler neler?",
        "Qu'est-ce que l'autre utilisateur a enregistre ?",
    ],
)
def test_no_wording_in_any_language_reveals_another_users_memory(
    db: Session, regular_user: User, query: str
) -> None:
    """Vier Sprachen, dieselbe Antwort: nichts.

    Die Frage ist absichtlich die eines Angreifers. Sie kann nichts ausrichten,
    weil sie nur die *Reihenfolge* der Auswahl beeinflusst — welche Zeilen
    ueberhaupt in Frage kommen, entscheidet die Scope-Kennung davor.
    """
    other = _user(db, "andere")
    _allow(db, regular_user, "ai.memory.use")
    _allow(db, other, "ai.memory.use")

    ai_memory_service.upsert_entry(
        db, user=other, scope="user", server_id=None,
        key="gehalt", value="Verdient 4200 Euro im Monat",
    )

    assert "4200" not in _context(db, regular_user, query)


def test_each_user_sees_only_their_own_entry(db: Session, regular_user: User) -> None:
    other = _user(db, "andere")
    _allow(db, regular_user, "ai.memory.use")
    _allow(db, other, "ai.memory.use")

    ai_memory_service.upsert_entry(
        db, user=regular_user, scope="user", server_id=None,
        key="ram.bevorzugt", value="8 GB fuer neue Server",
    )
    ai_memory_service.upsert_entry(
        db, user=other, scope="user", server_id=None,
        key="ram.bevorzugt", value="32 GB fuer neue Server",
    )

    mine = _context(db, regular_user)
    theirs = _context(db, other)
    assert "8 GB" in mine and "32 GB" not in mine
    assert "32 GB" in theirs and "8 GB" not in theirs


def test_two_users_writing_the_same_key_do_not_collide(
    db: Session, regular_user: User
) -> None:
    """Gleicher Schluessel, zwei Benutzer — zwei Zeilen, kein Ueberschreiben.

    Die UNIQUE-Bedingung steht auf `(scope_identity, key)`, nicht auf `key`.
    Waere sie es nicht, wuerde der zweite Schreibvorgang den ersten
    ueberschreiben und ein Benutzer bekaeme die Notiz eines anderen zu sehen.
    """
    other = _user(db, "andere")
    _allow(db, regular_user, "ai.memory.use")
    _allow(db, other, "ai.memory.use")

    ai_memory_service.upsert_entry(
        db, user=regular_user, scope="user", server_id=None, key="zeitzone",
        value="Europe/Berlin",
    )
    ai_memory_service.upsert_entry(
        db, user=other, scope="user", server_id=None, key="zeitzone",
        value="America/New_York",
    )

    rows = db.query(AiMemoryEntry).filter(AiMemoryEntry.key == "zeitzone").all()
    assert len(rows) == 2
    assert {row.scope_identity for row in rows} == {
        f"user:{regular_user.id}", f"user:{other.id}",
    }


# ── Die Bindung der Verschluesselung ──────────────────────────────────


def test_rewriting_the_owner_makes_the_entry_unreadable(
    db: Session, regular_user: User
) -> None:
    """Der Kern von "physikalisch unmoeglich".

    Angenommen, jemand hat Schreibzugriff auf die Datenbank und haengt einen
    fremden Eintrag auf sich um. Frueher haette er ihn danach im eigenen
    Kontext gelesen — die AAD hing nur an der Zeilen-ID. Jetzt scheitert die
    Entschluesselung, weil die Scope-Kennung Teil der Zusatzdaten ist.
    """
    other = _user(db, "andere")
    _allow(db, other, "ai.memory.use")
    _allow(db, regular_user, "ai.memory.use")

    row, _value = ai_memory_service.upsert_entry(
        db, user=other, scope="user", server_id=None,
        key="privat", value="Sehr persoenliche Notiz",
    )
    assert row.aad_version == 2

    row.owner_user_id = regular_user.id
    row.scope_identity = f"user:{regular_user.id}"
    db.commit()

    with pytest.raises(DisDecryptionError):
        DisClient.decrypt(row.value_encrypted, aad=ai_memory_service._aad(row))


def test_entries_from_before_the_change_stay_readable(
    db: Session, regular_user: User
) -> None:
    """Bestandsdaten aus Phase C duerfen nicht verloren gehen.

    Sie tragen `aad_version = 1` und die alte, nur an die Zeile gebundene AAD.
    Erst der naechste Schreibvorgang hebt sie an — eine Neuverschluesselung in
    der Migration haette den DIS-Sidecar vorausgesetzt.
    """
    _allow(db, regular_user, "ai.memory.use")
    row, _value = ai_memory_service.upsert_entry(
        db, user=regular_user, scope="user", server_id=None,
        key="alt", value="Alter Eintrag",
    )
    # Zustand vor der Umstellung nachbauen.
    row.aad_version = 1
    row.value_encrypted = DisClient.encrypt("Alter Eintrag", aad=f"msm:ai:memory:{row.id}")
    db.commit()

    entries = ai_memory_service.list_entries(db, regular_user, "user", None)
    assert [value for _row, value in entries] == ["Alter Eintrag"]

    # Und der naechste Schreibvorgang hebt ihn auf die gebundene Fassung.
    updated, _stored = ai_memory_service.upsert_entry(
        db, user=regular_user, scope="user", server_id=None,
        key="alt", value="Neuer Wert",
    )
    assert updated.aad_version == 2
    assert ai_memory_service._aad(updated).startswith(
        f"msm:ai:memory:user:{regular_user.id}:"
    )


# ── Team-Wissen ───────────────────────────────────────────────────────


def test_team_memory_reaches_every_member_and_nobody_else(
    db: Session, regular_user: User
) -> None:
    colleague = _user(db, "kollege")
    stranger = _user(db, "fremder")
    _allow(db, colleague, "ai.memory.use")
    _allow(db, stranger, "ai.memory.use")
    team = _team(db, regular_user, colleague)

    ai_memory_service.upsert_entry(
        db, user=regular_user, scope="team", server_id=None, team_id=team.id,
        key="valheim.ram", value="Valheim braucht mindestens 6 GB",
    )

    assert "6 GB" in _context(db, regular_user)
    assert "6 GB" in _context(db, colleague)
    assert "6 GB" not in _context(db, stranger)


def test_leaving_the_team_removes_the_knowledge_immediately(
    db: Session, regular_user: User
) -> None:
    colleague = _user(db, "kollege")
    _allow(db, colleague, "ai.memory.use")
    team = _team(db, regular_user, colleague)
    ai_memory_service.upsert_entry(
        db, user=regular_user, scope="team", server_id=None, team_id=team.id,
        key="valheim.ram", value="Valheim braucht mindestens 6 GB",
    )
    assert "6 GB" in _context(db, colleague)

    team_service.remove_member(db, team=team, user=regular_user, member_user_id=colleague.id)

    assert "6 GB" not in _context(db, colleague)
    # Beim Team bleibt es erhalten — das Wissen gehoert dem Team, nicht dem
    # Kollegen, der gegangen ist.
    assert "6 GB" in _context(db, regular_user)


def test_a_member_without_the_switch_cannot_write_team_memory(
    db: Session, regular_user: User
) -> None:
    colleague = _user(db, "kollege")
    _allow(db, colleague, "ai.memory.use")
    _allow(db, regular_user, "teams.create", "ai.memory.use")
    team = team_service.create_team(db, user=regular_user, name="Nur lesen")
    team_service.add_member(
        db, team=team, user=regular_user, new_user_id=colleague.id,
        can_manage_skills=False, can_manage_memory=False,
    )

    with pytest.raises(Exception) as exc:
        ai_memory_service.upsert_entry(
            db, user=colleague, scope="team", server_id=None, team_id=team.id,
            key="unerlaubt", value="Sollte nicht ankommen",
        )
    assert getattr(exc.value, "status_code", None) == 403


def test_a_stranger_cannot_write_into_a_foreign_team(
    db: Session, regular_user: User
) -> None:
    """Eine erratene Team-Nummer darf nichts oeffnen — auch nicht per Prompt."""
    stranger = _user(db, "fremder")
    _allow(db, stranger, "ai.memory.use")
    team = _team(db, regular_user)

    with pytest.raises(Exception) as exc:
        ai_memory_service.upsert_entry(
            db, user=stranger, scope="team", server_id=None, team_id=team.id,
            key="fremd", value="Von aussen",
        )
    # 404, nicht 403: ob es dieses Team gibt, ist selbst schon eine Auskunft.
    assert getattr(exc.value, "status_code", None) == 404


def test_team_memory_survives_its_author(db: Session, regular_user: User) -> None:
    """Unternehmenswissen darf nicht am Konto seines Verfassers haengen.

    Waere `owner_user_id` gesetzt, wuerde das `ondelete="CASCADE"` auf den
    Benutzer den Eintrag mitnehmen — das Team verloere beim Ausscheiden eines
    Kollegen genau das Wissen, das es von ihm behalten wollte.
    """
    colleague = _user(db, "kollege")
    _allow(db, colleague, "ai.memory.use")
    team = _team(db, regular_user, colleague)

    row, _value = ai_memory_service.upsert_entry(
        db, user=colleague, scope="team", server_id=None, team_id=team.id,
        key="eigenheit", value="Node 2 ist fuer Minecraft die schnellere",
    )
    assert row.owner_user_id is None
    assert row.team_id == team.id

    db.delete(db.get(User, colleague.id))
    db.commit()

    assert db.get(AiMemoryEntry, row.id) is not None
    assert "Node 2" in _context(db, regular_user)


# ── Gleichzeitigkeit ──────────────────────────────────────────────────


def test_parallel_writes_of_two_users_stay_separate(
    db: Session, regular_user: User
) -> None:
    """Der Fall aus der Beschreibung: zwei Kollegen schreiben gleichzeitig.

    Verschraenkt ausgefuehrt, damit kein Schreibvorgang sauber abgeschlossen
    ist, bevor der naechste beginnt.
    """
    colleague = _user(db, "kollege")
    _allow(db, colleague, "ai.memory.use")
    team = _team(db, regular_user, colleague)

    for index in range(5):
        ai_memory_service.upsert_entry(
            db, user=regular_user, scope="user", server_id=None,
            key=f"eigen.{index}", value=f"A-Notiz {index}",
        )
        ai_memory_service.upsert_entry(
            db, user=colleague, scope="user", server_id=None,
            key=f"eigen.{index}", value=f"B-Notiz {index}",
        )
        ai_memory_service.upsert_entry(
            db, user=colleague, scope="team", server_id=None, team_id=team.id,
            key=f"geteilt.{index}", value=f"Team-Notiz {index}",
        )

    mine = _context(db, regular_user)
    theirs = _context(db, colleague)
    assert "B-Notiz" not in mine
    assert "A-Notiz" not in theirs
    # Das Geteilte erreicht beide.
    assert "Team-Notiz 4" in mine and "Team-Notiz 4" in theirs
