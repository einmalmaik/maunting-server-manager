"""Die verschluesselte Einladungskarte einer Gruppe.

Bis hierher kam die Vorschau eines Einladungslinks aus `chat_groups.name`,
`description` und `avatar_url`. Der Server musste sie also kennen — und lieferte
sie **ohne Anmeldung** an jeden aus, der den Code hatte. Mit Stufe 6 wandern
genau diese Felder in den verschluesselten Block, und die Karte bliebe leer.

`invite_card` haelt sie stattdessen als Chiffretext. Der Schluessel faellt aus
dem Gruppengeheimnis und reist hinter der Raute im Link; dieser Server sieht ihn
nie und kann die Karte nicht lesen.

Was diese Datei festhaelt, ist deshalb nicht der Inhalt — den kennt das Backend
nicht —, sondern die Form und der Zugang:

* **Entweder verschluesselt oder im Klartext, nie beides.** Beides nebeneinander
  auszuliefern waere Verschluesselung als Zierde.
* **Wer einladen darf, darf die Karte setzen.** Dasselbe Recht, weil es dieselbe
  Handlung ist.
* **Form geprueft, Inhalt nie.** Der Server legt ab, was er gleich darauf ohne
  Anmeldung ausliefert; ein Klartext, der hier aus Versehen landet, waere ein
  stiller Rueckschritt.
"""

from __future__ import annotations

import base64

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from dependencies import get_current_user, verify_csrf
from main import app
from models import ChatGroup, User
from services.social_service import SocialService


@pytest.fixture
def als_owner(client: TestClient, owner_user: User):
    """Der Client, angemeldet als Eigentuemer — CSRF beiseite.

    Der CSRF-Schutz haengt an einem `__Secure-`-Cookie, das ueber schlichtes
    HTTP nicht gesetzt wird. Er ist hier nicht der Gegenstand; gemessen wird
    die Berechtigung dahinter.
    """
    app.dependency_overrides[get_current_user] = lambda: owner_user
    app.dependency_overrides[verify_csrf] = lambda: None
    try:
        yield client
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(verify_csrf, None)


def _karte(marke: str = "karte") -> str:
    """Ein formgueltiger Kartenumschlag. Was drinsteht, ist hier egal."""
    roh = b"N" * 12 + marke.encode("utf-8") + b"T" * 16
    return "sv-einladung-v1:" + base64.b64encode(roh).decode("ascii")


def _gruppe(db: Session, besitzer: User) -> ChatGroup:
    return SocialService.create_group(db, besitzer)


# ── Das Schema: Form ja, Inhalt nein ────────────────────────────────────────


def test_die_karte_muss_ein_umschlag_sein() -> None:
    """Der Server kann nicht hineinsehen — aber er kann auf die Huelle bestehen.

    Ohne diese Schranke landete ein Klartext in der Spalte, und der Server
    lieferte ihn ohne Anmeldung an jeden mit dem Code aus. Genau der Zustand,
    aus dem die Stufe herausfuehren soll, nur mit einem neuen Feldnamen.
    """
    from schemas.social import ChatGroupInviteCardUpdate

    with pytest.raises(ValueError):
        ChatGroupInviteCardUpdate(invite_card="Serverteam")
    with pytest.raises(ValueError):
        ChatGroupInviteCardUpdate(invite_card="sv-einladung-v1:zu-kurz")
    with pytest.raises(ValueError):
        ChatGroupInviteCardUpdate(invite_card="sv-einladung-v1:" + "!" * 60)


def test_leer_und_none_nehmen_die_karte_zurueck() -> None:
    """Beides heisst dasselbe, und beides muss gehen.

    Ein leerer String kommt aus einem Formular, `None` aus dem Code. Wuerde nur
    eines von beiden verstanden, bliebe eine zurueckgenommene Karte je nach
    Aufrufer stehen.
    """
    from schemas.social import ChatGroupInviteCardUpdate

    assert ChatGroupInviteCardUpdate(invite_card=None).invite_card is None
    assert ChatGroupInviteCardUpdate(invite_card="   ").invite_card is None


def test_eine_gueltige_karte_geht_durch() -> None:
    # Die Gegenprobe. Ohne sie waere die Schranke beliebig streng und niemandem
    # fiele auf, dass gar keine Karte mehr ankommt.
    from schemas.social import ChatGroupInviteCardUpdate

    wert = _karte()
    assert ChatGroupInviteCardUpdate(invite_card=wert).invite_card == wert


# ── Der Endpunkt: entweder oder ─────────────────────────────────────────────


def test_ohne_karte_gibt_es_keinen_klartext_mehr(client, db: Session, owner_user: User) -> None:
    """Der Altweg ist mit Stufe 6 zu Ende gegangen.

    Bis dahin fiel eine Gruppe ohne Karte auf `chat_groups.name` zurueck. Die
    Spalte ist geraeumt, und damit ist sie auch als Rueckfallebene weg — wer
    einlaedt, hinterlegt beim Teilen eine Karte. Was hier bleibt, sind die
    Zahlen: wer den Code hat, soll sehen, ob sich das Beitreten lohnt.
    """
    gruppe = _gruppe(db, owner_user)

    antwort = client.get(f"/api/social/groups/invite/{gruppe.invite_code}")

    assert antwort.status_code == 200
    daten = antwort.json()
    assert daten["name"] is None
    assert daten["description"] is None
    assert daten["avatar_url"] is None
    assert daten["invite_card"] is None
    assert daten["member_count"] >= 1


def test_mit_karte_verschwindet_der_klartext(client, db: Session, owner_user: User) -> None:
    """Die Zusage, an der die Stufe haengt.

    Beides nebeneinander auszuliefern waere Verschluesselung als Zierde: wer
    den Klartext danebenlegt, hat nichts verschlossen.

    Die Klartextspalten von Hand zu fuellen geht seit Stufe 6c nicht mehr: sie
    sind entfernt, nicht nur geleert. Was bleibt zu pruefen, ist die
    Antwortform — sie nennt `name`, `description` und `avatar_url` weiter, weil
    ein alter Client sie liest, und muss sie leer lassen, waehrend die Karte
    danebensteht.
    """
    gruppe = _gruppe(db, owner_user)
    gruppe.invite_card = _karte()
    db.commit()

    daten = client.get(f"/api/social/groups/invite/{gruppe.invite_code}").json()

    assert daten["invite_card"] == gruppe.invite_card
    assert daten["name"] is None
    assert daten["description"] is None
    assert daten["avatar_url"] is None
    # Die Zahlen bleiben: wer den Code hat, soll sehen, ob sich das Beitreten
    # gerade lohnt. Sie sagen nichts ueber Inhalte.
    assert daten["member_count"] >= 1


def test_die_karte_kommt_ohne_anmeldung_heraus(client, db: Session, owner_user: User) -> None:
    """Sie muss es — ein Eingeladener hat noch kein Konto.

    Genau deshalb steht der Schluessel nicht daneben, sondern hinter der Raute
    im Link, wo dieser Server ihn nie zu sehen bekommt.
    """
    gruppe = _gruppe(db, owner_user)
    gruppe.invite_card = _karte()
    db.commit()

    antwort = client.get(f"/api/social/groups/invite/{gruppe.invite_code}")

    assert antwort.status_code == 200
    assert antwort.json()["invite_card"] == gruppe.invite_card


# ── Wer die Karte setzen darf ───────────────────────────────────────────────


def test_der_besitzer_setzt_die_karte(als_owner, db: Session, owner_user: User) -> None:
    gruppe = _gruppe(db, owner_user)

    antwort = als_owner.put(
        f"/api/social/groups/{gruppe.id}/invite-card",
        json={"invite_card": _karte("neu")},
    )

    assert antwort.status_code == 200
    db.refresh(gruppe)
    assert gruppe.invite_card == _karte("neu")


def test_ein_fremdes_konto_kommt_nicht_an_die_karte(
    als_owner, db: Session, owner_user: User, regular_user: User
) -> None:
    """Der Missbrauchsfall.

    Die Karte ist das, was ein Eingeladener sieht. Wer sie setzen kann, ohne
    Mitglied zu sein, haengt fremden Einladungen ein eigenes Bild an — und die
    Gruppenkennung ist eine kleine Ganzzahl.

    Geantwortet wird 404 und nicht 403: ein 403 waere die Auskunft, dass es
    diese Gruppe gibt.
    """
    gruppe = SocialService.create_group(db, regular_user)

    antwort = als_owner.put(
        f"/api/social/groups/{gruppe.id}/invite-card",
        json={"invite_card": _karte("fremd")},
    )

    assert antwort.status_code == 404
    db.refresh(gruppe)
    assert gruppe.invite_card is None


def test_ohne_einladungsrecht_keine_karte(
    als_owner, db: Session, owner_user: User, regular_user: User
) -> None:
    """Dasselbe Recht wie fuer den Link, weil es dieselbe Handlung ist.

    Ein Mitglied ohne `invite_members` teilt keinen Link — es braucht also auch
    keine Karte zu setzen. Ein eigenes Recht daneben waere eine zweite Fassung
    derselben Frage, und die zweite Fassung ist irgendwann die nachsichtigere.
    """
    gruppe = SocialService.create_group(db, regular_user)
    SocialService.join_group_by_invite_code(db, owner_user, gruppe.invite_code)
    gruppe.default_permissions = "send_messages"
    db.commit()

    antwort = als_owner.put(
        f"/api/social/groups/{gruppe.id}/invite-card",
        json={"invite_card": _karte("ohne-recht")},
    )

    assert antwort.status_code == 403
    db.refresh(gruppe)
    assert gruppe.invite_card is None


def test_ein_mitglied_mit_einladungsrecht_darf(
    als_owner, db: Session, owner_user: User, regular_user: User
) -> None:
    """Die Gegenprobe zur Schranke darueber.

    Nicht auf Besitzer und Administratoren eingeschraenkt: wer einladen darf,
    teilt ohnehin einen Link. Eine abgewiesene Karte schuetzte dann nichts und
    kostete nur eine kaputte Vorschau.
    """
    gruppe = SocialService.create_group(db, regular_user)
    SocialService.join_group_by_invite_code(db, owner_user, gruppe.invite_code)
    gruppe.default_permissions = "send_messages,invite_members"
    db.commit()

    antwort = als_owner.put(
        f"/api/social/groups/{gruppe.id}/invite-card",
        json={"invite_card": _karte("mitglied")},
    )

    assert antwort.status_code == 200


def test_die_karte_laesst_sich_zuruecknehmen(als_owner, db: Session, owner_user: User) -> None:
    gruppe = _gruppe(db, owner_user)
    gruppe.invite_card = _karte()
    db.commit()

    antwort = als_owner.put(
        f"/api/social/groups/{gruppe.id}/invite-card", json={"invite_card": None}
    )

    assert antwort.status_code == 200
    db.refresh(gruppe)
    assert gruppe.invite_card is None
    # Und danach gibt es keine Vorschau mehr — keinen Klartext, der
    # zurueckkaeme. Das ist der Unterschied zu Stufe 3f: dort war das
    # Zuruecknehmen ein Rueckschritt in den Klartext, jetzt ist es schlicht
    # eine Einladung ohne Vorschau.
    daten = als_owner.get(f"/api/social/groups/invite/{gruppe.invite_code}").json()
    assert daten["name"] is None
    assert daten["invite_card"] is None


def test_ein_klartext_kommt_nicht_in_die_spalte(als_owner, db: Session, owner_user: User) -> None:
    """Dieselbe Formpruefung eine Ebene hoeher, am Produktionspfad.

    Ein Test nur auf dem Schema waere gruen, selbst wenn der Router das Feld an
    der Pruefung vorbei entgegennaehme.
    """
    gruppe = _gruppe(db, owner_user)

    antwort = als_owner.put(
        f"/api/social/groups/{gruppe.id}/invite-card",
        json={"invite_card": "Serverteam, offen fuer alle"},
    )

    assert antwort.status_code == 422
    db.refresh(gruppe)
    assert gruppe.invite_card is None


# ── Der Rauswurf erneuert den Code ──────────────────────────────────────────
#
# Wer hinausgeworfen wird, kennt den Einladungscode oft: er hatte das
# Einladungsrecht, oder der Link stand im Verlauf. Bis 09/2026 blieb der Code
# gueltig, und der Rauswurf war eine Formalie — ein Klick auf den alten Link,
# und er war wieder drin.


def _mit_mitglied(db: Session, besitzer: User, mitglied: User, rechte: str | None) -> ChatGroup:
    gruppe = SocialService.create_group(db, besitzer)
    SocialService.join_group_by_invite_code(db, mitglied, gruppe.invite_code)
    if rechte is not None:
        SocialService.get_group_member(db, gruppe.id, mitglied.id).permissions = rechte
        db.commit()
    return gruppe


def test_nach_dem_rauswurf_fuehrt_der_alte_link_nicht_mehr_hinein(
    db: Session, owner_user: User, regular_user: User
) -> None:
    gruppe = _mit_mitglied(db, owner_user, regular_user, "send_messages,invite_members")
    alter_code = gruppe.invite_code

    SocialService.kick_group_member(
        db, group_id=gruppe.id, target_user_id=regular_user.id, caller=owner_user
    )
    db.refresh(gruppe)

    assert gruppe.invite_code != alter_code
    with pytest.raises(HTTPException) as fehler:
        SocialService.join_group_by_invite_code(db, regular_user, alter_code)
    assert fehler.value.status_code == 404
    assert SocialService.get_group_member(db, gruppe.id, regular_user.id) is None


def test_der_rauswurf_nimmt_die_karte_mit(
    db: Session, owner_user: User, regular_user: User
) -> None:
    """Ihre gebundenen Daten nennen den alten Code — gegen den neuen oeffnet
    sie sich nicht mehr. Stehen bliebe nur ein Umschlag, den niemand lesen kann.
    """
    gruppe = _mit_mitglied(db, owner_user, regular_user, None)
    gruppe.invite_card = _karte()
    db.commit()

    SocialService.kick_group_member(
        db, group_id=gruppe.id, target_user_id=regular_user.id, caller=owner_user
    )
    db.refresh(gruppe)

    assert gruppe.invite_card is None


def test_der_rauswurf_meldet_den_neuen_code_zurueck(
    als_owner, db: Session, owner_user: User, regular_user: User
) -> None:
    # Sonst teilte der Einladende bis zum naechsten Laden einen toten Link.
    gruppe = _mit_mitglied(db, owner_user, regular_user, None)

    antwort = als_owner.delete(f"/api/social/groups/{gruppe.id}/members/{regular_user.id}")

    assert antwort.status_code == 200
    db.refresh(gruppe)
    assert antwort.json()["invite_code"] == gruppe.invite_code


def test_wer_nur_hinauswerfen_darf_bekommt_den_code_nicht(
    client: TestClient, db: Session, owner_user: User, regular_user: User, inactive_user: User
) -> None:
    """Dieselbe Schranke wie in der Gruppenliste (`darf_einladen`).

    Die Antwort des Rauswurfs darf kein zweiter Weg zum Code sein.
    """
    gruppe = _mit_mitglied(db, owner_user, regular_user, "send_messages,kick_members")
    SocialService.join_group_by_invite_code(db, inactive_user, gruppe.invite_code)

    app.dependency_overrides[get_current_user] = lambda: regular_user
    app.dependency_overrides[verify_csrf] = lambda: None
    try:
        antwort = client.delete(f"/api/social/groups/{gruppe.id}/members/{inactive_user.id}")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(verify_csrf, None)

    assert antwort.status_code == 200
    assert antwort.json()["invite_code"] is None
