"""Der blinde Besitznachweis einer Mailbox.

Die Mailbox-Kennung war bisher aus zwei kleinen Ganzzahlen nachrechenbar, und
die einzige Schranke davor — `assert_mailbox_participant` — funktioniert nur,
**weil** der Server die Mitgliedschaften kennt. Wer die Mitgliedschaften
loswerden will, braucht zuerst eine Schranke, die ohne sie auskommt.

Diese Datei haelt fest, was der Nachweis leistet und was er ausdruecklich
**nicht** leistet:

* Er gilt an **jeder** Tuer. Es gibt zwei Autorisierungswege im Messenger —
  `assert_mailbox_participant` fuer Lesen und Loeschen, `resolve_mailbox_target`
  fuer Senden und Tippen — und der WebSocket ist ein dritter Eingang. Ein
  Schloss an einer von drei Tueren waere keines.
* Er ersetzt die Mitgliedspruefung **nicht**. In dieser Stufe steht er davor,
  nicht an ihrer Stelle: ein hinausgeworfenes Mitglied kennt das
  Gruppengeheimnis und koennte sich den Nachweis jederzeit selbst ausrechnen.
* Eine Mailbox mit Umschlaegen gehoert nie dem Naechstbesten — und eine leere,
  aber **ableitbare** ebenso wenig. Sonst waere das Registrieren eine Sperre
  gegen die echten Mitglieder.

Was hier nicht geprueft werden kann: dass die Kennung wirklich unratbar ist.
Sie entsteht im Browser aus dem Gruppengeheimnis; dafuer ist das Frontend
zustaendig.
"""

from __future__ import annotations

import hashlib

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session

from models import E2eeBlindEnvelope, E2eeBlindMailbox, User
from schemas.social import E2eeMailboxRegister
from services.social_service import SocialService


# ── Hilfen ──────────────────────────────────────────────────────────────────


def _kennung(wort: str) -> str:
    """Eine unableitbare Kennung: 64 Hexzeichen, die zu nichts gehoeren.

    Absichtlich **nicht** `sha256("msm:group:<id>")` — genau diese Form soll
    der Server als ableitbar erkennen und anders behandeln.
    """
    return hashlib.sha256(f"pruef-geheimnis:{wort}".encode()).hexdigest()


def _token(wort: str) -> str:
    return hashlib.sha256(f"pruef-token:{wort}".encode()).hexdigest()


def _fremder(db: Session) -> User:
    """Jemand mit Konto, aber ohne etwas mit dieser Mailbox zu tun."""
    vorhanden = db.query(User).filter(User.username == "fremder").first()
    if vorhanden:
        return vorhanden
    from services.auth_service import AuthService

    user = AuthService.create_user(db, "fremder", "fremder@test.de", "FremdPass123!")
    user.email_verified = True
    db.commit()
    db.refresh(user)
    return user


def _fremde_kekse(client: TestClient) -> dict:
    """Meldet `_fremder` an. Setzt voraus, dass es ihn schon gibt."""
    antwort = client.post(
        "/api/auth/login",
        json={"username": "fremder", "password": "FremdPass123!", "otp_code": None},
    )
    assert antwort.status_code == 200, antwort.text
    return dict(antwort.cookies)


def _gruppe(db: Session, besitzer: User, mitglied: User):
    gruppe = SocialService.create_group(db, besitzer, "Nachweisgruppe")
    SocialService.join_group_by_invite_code(db, mitglied, gruppe.invite_code)
    return gruppe


def _umschlag(db: Session, mailbox_id: str) -> None:
    """Legt einen Umschlag in die Mailbox, ohne den Relay-Weg zu benutzen."""
    db.add(
        E2eeBlindEnvelope(
            blind_mailbox_id=mailbox_id,
            ciphertext_envelope="sv-e2ee-group-v1:a1b2c3d4e5f60789.AAAA",
            client_uuid="vorhanden-1",
        )
    )
    db.commit()


# ── Die Schemagrenze ────────────────────────────────────────────────────────


def test_zu_kurze_kennung_wird_abgewiesen() -> None:
    with pytest.raises(ValidationError):
        E2eeMailboxRegister(mailbox_id="a" * 63, auth_token=_token("x"))


def test_nicht_hex_wird_abgewiesen() -> None:
    # 64 Zeichen, aber keine Hexzeichen. Ohne diese Pruefung waere das Feld
    # eine Ablage fuer beliebigen Text.
    with pytest.raises(ValidationError):
        E2eeMailboxRegister(mailbox_id="z" * 64, auth_token=_token("x"))


def test_grossbuchstaben_werden_kleingeschrieben() -> None:
    gross = _kennung("gross").upper()
    geprueft = E2eeMailboxRegister(mailbox_id=gross, auth_token=_token("gross").upper())
    assert geprueft.mailbox_id == gross.lower()
    assert geprueft.auth_token == _token("gross")


# ── Registrieren ────────────────────────────────────────────────────────────


def test_unableitbare_leere_kennung_darf_registriert_werden(
    db: Session, owner_user: User
) -> None:
    # Der Normalfall der Stufe 3: eine frisch aus dem Gruppengeheimnis
    # abgeleitete Kennung. Dem Server ist sie nicht zuzuordnen, und sie
    # enthaelt nichts, was jemandem gehoeren koennte.
    mid = _kennung("frisch")

    SocialService.register_blind_mailbox(db, owner_user.id, mid, _token("frisch"))

    eintrag = db.get(E2eeBlindMailbox, mid)
    assert eintrag is not None


def test_derselbe_nachweis_ist_idempotent(db: Session, owner_user: User) -> None:
    # Zwei Geraete desselben Kontos registrieren dieselbe Mailbox. Das darf
    # nicht am zweiten scheitern.
    mid = _kennung("doppelt")
    SocialService.register_blind_mailbox(db, owner_user.id, mid, _token("doppelt"))
    SocialService.register_blind_mailbox(db, owner_user.id, mid, _token("doppelt"))

    assert db.query(E2eeBlindMailbox).filter_by(mailbox_id=mid).count() == 1


def test_anderer_nachweis_wird_nie_ueberschrieben(db: Session, owner_user: User) -> None:
    # Sonst waere ein uebernommenes Panel-Konto ein Generalschluessel: wer sich
    # anmelden kann, legt einfach einen neuen Nachweis darueber.
    mid = _kennung("streit")
    SocialService.register_blind_mailbox(db, owner_user.id, mid, _token("erster"))

    with pytest.raises(HTTPException) as fehler:
        SocialService.register_blind_mailbox(db, owner_user.id, mid, _token("zweiter"))

    assert fehler.value.status_code == 409
    assert db.get(E2eeBlindMailbox, mid).auth_verifier == SocialService._verifier_von(
        _token("erster")
    )


def test_fremde_mailbox_mit_umschlaegen_ist_tabu(
    db: Session, owner_user: User, regular_user: User
) -> None:
    # Dieselbe Regel wie beim Tresor: ein Bucket mit Daten gehoert nie dem
    # Naechstbesten.
    mid = _kennung("belegt")
    _umschlag(db, mid)
    fremder = _fremder(db)

    with pytest.raises(HTTPException) as fehler:
        SocialService.register_blind_mailbox(db, fremder.id, mid, _token("belegt"))

    assert fehler.value.status_code == 403
    assert db.get(E2eeBlindMailbox, mid) is None


def test_leere_aber_ableitbare_gruppenmailbox_ist_fuer_fremde_tabu(
    db: Session, owner_user: User, regular_user: User
) -> None:
    # Der eigentliche Angriff: Gruppe 5 hat eine ausrechenbare Mailbox. Waere
    # sie nur leer genug, koennte ein Fremder sie mit einem Nachweis belegen
    # und die Gruppe aus ihrer eigenen Mailbox aussperren.
    gruppe = _gruppe(db, owner_user, regular_user)
    mid = SocialService.derive_group_blind_mailbox_id(gruppe.id)
    fremder = _fremder(db)

    with pytest.raises(HTTPException) as fehler:
        SocialService.register_blind_mailbox(db, fremder.id, mid, _token("uebernahme"))

    assert fehler.value.status_code == 403
    assert db.get(E2eeBlindMailbox, mid) is None


def test_mitglied_darf_die_eigene_gruppenmailbox_registrieren(
    db: Session, owner_user: User, regular_user: User
) -> None:
    # Der Weg fuer den Bestand: eine gewachsene Gruppe bekommt ihren Nachweis
    # von einem echten Mitglied.
    gruppe = _gruppe(db, owner_user, regular_user)
    mid = SocialService.derive_group_blind_mailbox_id(gruppe.id)

    SocialService.register_blind_mailbox(db, regular_user.id, mid, _token("bestand"))

    assert db.get(E2eeBlindMailbox, mid) is not None


def test_fremde_geraetemailbox_ist_tabu(db: Session, owner_user: User) -> None:
    # Die Geraete-Mailbox eines fremden Kontos ist ebenso ausrechenbar.
    fremder = _fremder(db)
    mid = SocialService.derive_user_device_mailbox_id(fremder.id)

    with pytest.raises(HTTPException) as fehler:
        SocialService.register_blind_mailbox(db, owner_user.id, mid, _token("geraet"))

    assert fehler.value.status_code == 403


def test_token_liegt_nie_im_klartext_in_der_ablage(db: Session, owner_user: User) -> None:
    mid = _kennung("geheim")
    SocialService.register_blind_mailbox(db, owner_user.id, mid, _token("geheim"))

    gespeichert = db.get(E2eeBlindMailbox, mid).auth_verifier
    assert gespeichert != _token("geheim")
    assert gespeichert == hashlib.sha256(_token("geheim").encode()).hexdigest()


# ── Pruefen ─────────────────────────────────────────────────────────────────


def test_ohne_hinterlegten_nachweis_aendert_sich_nichts(db: Session) -> None:
    # Der Bestand laeuft weiter: eine Mailbox ohne Nachweis verlangt keinen.
    SocialService.assert_mailbox_token(db, _kennung("unbekannt"), None)


def test_hinterlegter_nachweis_verlangt_ein_token(db: Session, owner_user: User) -> None:
    mid = _kennung("streng")
    SocialService.register_blind_mailbox(db, owner_user.id, mid, _token("streng"))

    with pytest.raises(HTTPException) as fehler:
        SocialService.assert_mailbox_token(db, mid, None)

    assert fehler.value.status_code == 403


def test_falsches_token_ist_403(db: Session, owner_user: User) -> None:
    mid = _kennung("falsch")
    SocialService.register_blind_mailbox(db, owner_user.id, mid, _token("richtig"))

    with pytest.raises(HTTPException) as fehler:
        SocialService.assert_mailbox_token(db, mid, _token("daneben"))

    assert fehler.value.status_code == 403


def test_richtiges_token_geht_durch(db: Session, owner_user: User) -> None:
    mid = _kennung("passt")
    SocialService.register_blind_mailbox(db, owner_user.id, mid, _token("passt"))

    SocialService.assert_mailbox_token(db, mid, _token("passt"))


def test_token_in_grossbuchstaben_geht_auch_durch(db: Session, owner_user: User) -> None:
    # Der Client schickt Hex; ob gross oder klein, darf keine Rolle spielen.
    mid = _kennung("schreibweise")
    SocialService.register_blind_mailbox(db, owner_user.id, mid, _token("schreibweise"))

    SocialService.assert_mailbox_token(db, mid, _token("schreibweise").upper())


# ── Der Nachweis ersetzt die Mitgliedspruefung nicht ────────────────────────


def test_richtiges_token_macht_aus_einem_fremden_kein_mitglied(
    db: Session, owner_user: User, regular_user: User
) -> None:
    """Die Kernzusage dieser Stufe, und die, die am leichtesten verlorengeht.

    Der Nachweis ist das **zweite** Schloss. Faellt beim Umbau die
    Mitgliedspruefung weg, weil „der Nachweis reicht ja", dann kommt jedes
    hinausgeworfene Mitglied mit dem Geheimnis zurueck, das es noch kennt.
    """
    gruppe = _gruppe(db, owner_user, regular_user)
    mid = SocialService.derive_group_blind_mailbox_id(gruppe.id)
    SocialService.register_blind_mailbox(db, owner_user.id, mid, _token("zwei-schloesser"))
    fremder = _fremder(db)

    # Das Token stimmt — der Fremde hat es sich besorgt.
    SocialService.assert_mailbox_token(db, mid, _token("zwei-schloesser"))

    # Und kommt trotzdem nicht hinein.
    with pytest.raises(HTTPException) as fehler:
        SocialService.assert_mailbox_participant(db, fremder.id, mid)
    assert fehler.value.status_code == 403


# ── An jeder Tuer ───────────────────────────────────────────────────────────


def _registriere_ueber_http(client: TestClient, cookies: dict, mid: str, token: str) -> None:
    antwort = client.post(
        "/api/social/e2ee/mailbox/register",
        json={"mailbox_id": mid, "auth_token": token},
        cookies=cookies,
        headers={"X-CSRF-Token": cookies.get("__Secure-csrf_token", "")},
    )
    assert antwort.status_code == 200, antwort.text


def test_lesen_ohne_nachweis_bleibt_verschlossen(
    client: TestClient, db: Session, owner_user: User, regular_user: User, owner_cookies: dict
) -> None:
    """Ohne Nachweis geht die Mailbox nicht auf — auch fuer ein Mitglied nicht.

    Seit 09/2026 antworten alle Gruende **gleich**: kein Mitglied, kein
    Nachweis, falscher Nachweis, Mailbox gibt es nicht. Aus der Antwort ist
    damit nicht zu lesen, ob es diese Mailbox gibt oder ob sie einen Nachweis
    traegt — sonst liesse sich von aussen abfragen, welche Gruppen es gibt.
    """
    gruppe = _gruppe(db, owner_user, regular_user)
    mid = SocialService.derive_group_blind_mailbox_id(gruppe.id)
    _registriere_ueber_http(client, owner_cookies, mid, _token("lesen"))

    ohne = client.get(f"/api/social/e2ee/mailbox/{mid}", cookies=owner_cookies)
    assert ohne.status_code == 403

    falsch = client.get(
        f"/api/social/e2ee/mailbox/{mid}",
        cookies=owner_cookies,
        headers={"X-Mailbox-Token": _token("daneben")},
    )
    assert falsch.status_code == 403
    # Und die Gegenprobe, auf die es ankommt: eine Kennung, die es gar nicht
    # gibt, antwortet genauso. Waeren die beiden unterscheidbar, liesse sich
    # die Existenz einer Mailbox abfragen.
    erfunden = client.get(f"/api/social/e2ee/mailbox/{'7' * 64}", cookies=owner_cookies)
    assert erfunden.status_code == ohne.status_code

    richtig = client.get(
        f"/api/social/e2ee/mailbox/{mid}",
        cookies=owner_cookies,
        headers={"X-Mailbox-Token": _token("lesen")},
    )
    assert richtig.status_code == 200


def test_senden_ohne_nachweis_ist_403(
    client: TestClient, db: Session, owner_user: User, regular_user: User, owner_cookies: dict
) -> None:
    # Die zweite Tuer: Senden laeuft ueber `resolve_mailbox_target`, nicht
    # ueber `assert_mailbox_participant`. Wer nur eine der beiden absichert,
    # hat nichts abgesichert.
    gruppe = _gruppe(db, owner_user, regular_user)
    mid = SocialService.derive_group_blind_mailbox_id(gruppe.id)
    _registriere_ueber_http(client, owner_cookies, mid, _token("senden"))

    nutzlast = {
        "blind_mailbox_id": mid,
        "ciphertext_envelope": "sv-e2ee-group-v1:a1b2c3d4e5f60789."
        + "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVowMTIzNDU2Nzg5",
        "client_uuid": "probe-1",
    }
    kopf = {"X-CSRF-Token": owner_cookies.get("__Secure-csrf_token", "")}

    ohne = client.post("/api/social/e2ee/relay", json=nutzlast, cookies=owner_cookies, headers=kopf)
    assert ohne.status_code == 403

    mit = client.post(
        "/api/social/e2ee/relay",
        json=nutzlast,
        cookies=owner_cookies,
        headers={**kopf, "X-Mailbox-Token": _token("senden")},
    )
    assert mit.status_code == 200, mit.text


def test_loeschen_ohne_nachweis_bleibt_verschlossen(
    client: TestClient, db: Session, owner_user: User, regular_user: User, owner_cookies: dict
) -> None:
    # Wie beim Lesen: eine Antwort fuer jeden Grund.
    gruppe = _gruppe(db, owner_user, regular_user)
    mid = SocialService.derive_group_blind_mailbox_id(gruppe.id)
    _registriere_ueber_http(client, owner_cookies, mid, _token("loeschen"))
    kopf = {"X-CSRF-Token": owner_cookies.get("__Secure-csrf_token", "")}

    ohne = client.delete(
        f"/api/social/e2ee/envelopes/{mid}?client_uuid=egal", cookies=owner_cookies, headers=kopf
    )
    assert ohne.status_code == 403

    mit = client.delete(
        f"/api/social/e2ee/envelopes/{mid}?client_uuid=egal",
        cookies=owner_cookies,
        headers={**kopf, "X-Mailbox-Token": _token("loeschen")},
    )
    assert mit.status_code == 200, mit.text


def test_tippen_ohne_nachweis_ist_403(
    client: TestClient, db: Session, owner_user: User, regular_user: User, owner_cookies: dict
) -> None:
    gruppe = _gruppe(db, owner_user, regular_user)
    mid = SocialService.derive_group_blind_mailbox_id(gruppe.id)
    _registriere_ueber_http(client, owner_cookies, mid, _token("tippen"))
    kopf = {"X-CSRF-Token": owner_cookies.get("__Secure-csrf_token", "")}

    ohne = client.post(
        "/api/social/e2ee/typing",
        json={"blind_mailbox_id": mid, "status": "typing"},
        cookies=owner_cookies,
        headers=kopf,
    )
    assert ohne.status_code == 403


def test_mailbox_ohne_nachweis_bleibt_ohne_kopfzeile_erreichbar(
    client: TestClient, db: Session, owner_user: User, regular_user: User, owner_cookies: dict
) -> None:
    # Der Bestand: solange niemand einen Nachweis hinterlegt hat, aendert sich
    # fuer die Mailbox nichts.
    gruppe = _gruppe(db, owner_user, regular_user)
    mid = SocialService.derive_group_blind_mailbox_id(gruppe.id)

    antwort = client.get(f"/api/social/e2ee/mailbox/{mid}", cookies=owner_cookies)
    assert antwort.status_code == 200


# ── Die Kennung aus dem Gruppengeheimnis (Stufe 3d) ─────────────────────────
#
# Bis hierher war jede Mailbox-Kennung aus kleinen Ganzzahlen nachrechenbar,
# und `assert_mailbox_participant` konnte deshalb nachschlagen, wer dazugehoert.
# Faellt die Kennung aus einem Gruppengeheimnis, **kann** der Server das nicht
# mehr — er weiss nicht, wem sie gehoert, und soll es nicht wissen. Dann ist
# der Besitznachweis keine zusaetzliche Schranke mehr, sondern die einzige
# Berechtigung, die es gibt.


UNABLEITBAR = "3" * 64


def test_eine_unableitbare_mailbox_oeffnet_sich_mit_dem_nachweis(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict
) -> None:
    """Der Kern von Stufe 3d: ohne diesen Weg waere die neue Kennung tot.

    Kein Mitglied, keine Gruppe, kein Direktchat fuehrt auf diese Kennung.
    Oeffnete sie sich nur ueber die Teilnehmerpruefung, koennte niemand sie
    lesen — auch ihre Besitzer nicht.
    """
    _registriere_ueber_http(client, owner_cookies, UNABLEITBAR, _token("neu"))
    _umschlag(db, UNABLEITBAR)

    antwort = client.get(
        f"/api/social/e2ee/mailbox/{UNABLEITBAR}",
        cookies=owner_cookies,
        headers={"X-Mailbox-Token": _token("neu")},
    )

    assert antwort.status_code == 200
    assert len(antwort.json()) == 1


def test_eine_unableitbare_mailbox_bleibt_ohne_nachweis_zu(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict
) -> None:
    # Und die Kehrseite. Waere sie ohne Nachweis offen, haette die ganze Stufe
    # nur die Adresse geaendert und keine Tuer zugemacht.
    _registriere_ueber_http(client, owner_cookies, UNABLEITBAR, _token("neu"))
    _umschlag(db, UNABLEITBAR)

    ohne = client.get(f"/api/social/e2ee/mailbox/{UNABLEITBAR}", cookies=owner_cookies)
    falsch = client.get(
        f"/api/social/e2ee/mailbox/{UNABLEITBAR}",
        cookies=owner_cookies,
        headers={"X-Mailbox-Token": _token("daneben")},
    )

    assert ohne.status_code == 403
    assert falsch.status_code == 403


def test_ein_fremdes_konto_kommt_mit_dem_nachweis_hinein(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict
) -> None:
    """Und das ist **kein** Fehler, sondern die Entscheidung dieser Stufe.

    Der Server kann nicht pruefen, wer zu einer Mailbox gehoert, die er nicht
    ausrechnen kann. Wer den Nachweis vorlegt, hat das Gruppengeheimnis — und
    wer das Gruppengeheimnis hat, konnte ohnehin jede Nachricht lesen. Die
    Schranke ist das Geheimnis, nicht das Konto.

    Was daraus folgt, steht schwarz auf weiss: ein hinausgeworfenes Mitglied
    kennt das Geheimnis noch. Dagegen hilft erst MLS, und bis dahin haelt die
    Teilnehmerpruefung die **alte**, ableitbare Kennung.
    """
    _registriere_ueber_http(client, owner_cookies, UNABLEITBAR, _token("neu"))
    _umschlag(db, UNABLEITBAR)
    _fremder(db)
    fremde_kekse = _fremde_kekse(client)

    antwort = client.get(
        f"/api/social/e2ee/mailbox/{UNABLEITBAR}",
        cookies=fremde_kekse,
        headers={"X-Mailbox-Token": _token("neu")},
    )

    assert antwort.status_code == 200


def test_ein_fremdes_konto_kommt_ohne_nachweis_nicht_hinein(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict
) -> None:
    _registriere_ueber_http(client, owner_cookies, UNABLEITBAR, _token("neu"))
    _umschlag(db, UNABLEITBAR)
    _fremder(db)
    fremde_kekse = _fremde_kekse(client)

    antwort = client.get(
        f"/api/social/e2ee/mailbox/{UNABLEITBAR}", cookies=fremde_kekse
    )

    assert antwort.status_code == 403


def test_die_alte_kennung_bleibt_fuer_mitglieder_offen(
    client: TestClient, db: Session, owner_user: User, regular_user: User, owner_cookies: dict
) -> None:
    """Der Umzug darf den Bestand nicht mitnehmen.

    Waehrend des Umzugs liegen Nachrichten in beiden Mailboxen, und die
    Mitglieder bekommen das Geheimnis nicht gleichzeitig. Verschloesse sich die
    alte Kennung mit, saehe ein Mitglied ohne Geheimnis gar nichts mehr — der
    Fehler, der am 22.09.2026 schon einmal gemessen wurde.
    """
    gruppe = _gruppe(db, owner_user, regular_user)
    alt = SocialService.derive_group_blind_mailbox_id(gruppe.id)
    _umschlag(db, alt)
    # Der Nachweis liegt auf der **neuen** Kennung, nicht auf der alten.
    _registriere_ueber_http(client, owner_cookies, UNABLEITBAR, _token("neu"))

    antwort = client.get(f"/api/social/e2ee/mailbox/{alt}", cookies=owner_cookies)

    assert antwort.status_code == 200
    assert len(antwort.json()) == 1


def test_loeschen_geht_in_der_unableitbaren_mailbox(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict
) -> None:
    # Eine geloeschte Nachricht muss auch dort verschwinden. Ginge das nicht,
    # bliebe der Chiffretext liegen, waehrend die Anzeige „geloescht" sagt.
    _registriere_ueber_http(client, owner_cookies, UNABLEITBAR, _token("neu"))
    _umschlag(db, UNABLEITBAR)
    kopf = {"X-CSRF-Token": owner_cookies.get("__Secure-csrf_token", "")}

    antwort = client.delete(
        f"/api/social/e2ee/envelopes/{UNABLEITBAR}?client_uuid=vorhanden-1",
        cookies=owner_cookies,
        headers={**kopf, "X-Mailbox-Token": _token("neu")},
    )

    assert antwort.status_code == 200
    assert antwort.json()["deleted"] == 1


def test_loeschen_ohne_nachweis_geht_dort_nicht(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict
) -> None:
    _registriere_ueber_http(client, owner_cookies, UNABLEITBAR, _token("neu"))
    _umschlag(db, UNABLEITBAR)
    kopf = {"X-CSRF-Token": owner_cookies.get("__Secure-csrf_token", "")}

    antwort = client.delete(
        f"/api/social/e2ee/envelopes/{UNABLEITBAR}?client_uuid=vorhanden-1",
        cookies=owner_cookies,
        headers=kopf,
    )

    assert antwort.status_code == 403
    assert db.query(E2eeBlindEnvelope).filter_by(blind_mailbox_id=UNABLEITBAR).count() == 1
