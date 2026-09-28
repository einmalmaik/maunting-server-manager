"""Push an eine Mailbox statt an ein Konto.

Der Vordergrund lief seit `test_mailbox_abo.py` ueber die Mailbox: wer
abonniert hat, erfaehrt etwas, und der Server muss dafuer niemanden kennen.
WebPush war der letzte Weg, auf dem dieselbe Nachricht noch ueber ein Konto
ging — `push_subscriptions.user_id`. Damit wusste der Server bei jeder
Zustellung wieder, welches Konto Post bekommt, und zwar ausgerechnet fuer die
Mailboxen, die er sonst niemandem zuordnen kann.

Was hier festgehalten wird:

* **In der Zeile steht kein Konto.** Keine Spalte, kein Fremdschluessel. Das
  ist die eine Zusage, wegen der es diese Tabelle ueberhaupt gibt, und sie
  liesse sich mit einer beilaeufigen Spalte still zuruecknehmen.
* **In der Meldung steht kein Konto.** `prepare_push_dispatch` setzt
  `target_user_id` und `sender_user_id` in die Nutzlast; auf diesem Weg waere
  das der Rueckweg zu allem, was gerade abgebaut wurde.
* **Eintragen ist kein Selbstbedienungsladen.** Wer sich fuer eine fremde
  Mailbox eintragen koennte, bekaeme eine Meldung, sobald sich dort etwas
  regt — Verkehrsanalyse mit Klingelton.
* **Dieselbe Tuer wie beim Strom.** Waere Push die nachsichtigere, koennte man
  sich ueber eine Benachrichtigung sagen lassen, was der Strom verschweigt.
"""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from models import E2eeMailboxPush, PushSubscription, User
from services import webpush_service
from services.notification_service import NotificationService
from services.social_service import SocialService
from services.sync_event_service import SyncEventService


# ── Hilfen ──────────────────────────────────────────────────────────────────


# Echter P-256-Punkt aus dem Testvektor des RFC 8291, damit die Formpruefung
# beim Eintragen nicht an einer Attrappe scheitert.
PUNKT = "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
AUTH = "BTBZMqHH6r4Tts7J_aSIgg"
ENDPUNKT_A = "https://fcm.googleapis.com/fcm/send/geraet-a"
ENDPUNKT_B = "https://fcm.googleapis.com/fcm/send/geraet-b"

# Eine Kennung, die der Server nicht ausrechnen kann — der ganze Fall.
UNABLEITBAR = "d" * 64
TOKEN = "e" * 64


def _umschlag(marke: str) -> str:
    roh = b"N" * 12 + marke.encode("utf-8") + b"T" * 16
    return "sv-e2ee-group-v1:00112233445566ff." + base64.b64encode(roh).decode("ascii")


def _eintragen(db: Session, mailbox: str, endpunkt: str = ENDPUNKT_A) -> None:
    webpush_service.eintragen_mailbox(
        db, mailbox_id=mailbox, endpoint=endpunkt, p256dh=PUNKT, auth=AUTH
    )


@pytest.fixture
def versand(monkeypatch) -> list[tuple]:
    """Faengt ab, was in den Hintergrundfaden gegeben wird.

    Der echte Versand ginge ins Netz. Gemessen wird hier, **wer** ein Ziel
    geworden waere und **was** in der Nutzlast steht — beides entscheidet sich
    vor dem Faden.
    """
    aufrufe: list[tuple] = []

    def _merken(fn, *args, **kwargs):
        aufrufe.append(args)
        return None

    monkeypatch.setattr(webpush_service._versender, "submit", _merken)
    return aufrufe


@pytest.fixture(autouse=True)
def _leere_abonnenten():
    SyncEventService.clear_all_for_testing()
    yield
    SyncEventService.clear_all_for_testing()


# ── Die Zeile selbst ────────────────────────────────────────────────────────


def test_die_tabelle_kennt_kein_konto(db: Session) -> None:
    """Die eine Zusage, wegen der es diese Tabelle gibt.

    Eine beilaeufig ergaenzte Spalte wuerde sie still zuruecknehmen: der
    Server wuesste wieder, welches Konto Post in einer Mailbox bekommt, die er
    sonst niemandem zuordnen kann. Deshalb steht sie hier und nicht nur im
    Modulkopf.
    """
    spalten = {s["name"] for s in sa_inspect(db.bind).get_columns("e2ee_mailbox_push")}

    assert "user_id" not in spalten
    assert spalten == {
        "id",
        "mailbox_id",
        "endpoint_hash",
        "endpoint",
        "p256dh",
        "auth",
        "created_at",
    }
    assert sa_inspect(db.bind).get_foreign_keys("e2ee_mailbox_push") == []


def test_derselbe_browser_wird_uebernommen_nicht_verdoppelt(db: Session) -> None:
    # Gemeldet wird bei jeder Anmeldung, und die Schluessel eines Browsers
    # koennen sich dabei geaendert haben.
    _eintragen(db, UNABLEITBAR)
    _eintragen(db, UNABLEITBAR)

    zeilen = db.query(E2eeMailboxPush).filter_by(mailbox_id=UNABLEITBAR).all()
    assert len(zeilen) == 1


def test_zwei_geraete_sind_zwei_zeilen(db: Session) -> None:
    _eintragen(db, UNABLEITBAR, ENDPUNKT_A)
    _eintragen(db, UNABLEITBAR, ENDPUNKT_B)

    assert db.query(E2eeMailboxPush).filter_by(mailbox_id=UNABLEITBAR).count() == 2


def test_austragen_nimmt_nur_diesen_browser(db: Session) -> None:
    _eintragen(db, UNABLEITBAR, ENDPUNKT_A)
    _eintragen(db, UNABLEITBAR, ENDPUNKT_B)

    entfernt = webpush_service.austragen_mailboxen(db, ENDPUNKT_A)

    assert entfernt == 1
    uebrig = db.query(E2eeMailboxPush).filter_by(mailbox_id=UNABLEITBAR).all()
    assert [z.endpoint for z in uebrig] == [ENDPUNKT_B]


def test_austragen_kann_auf_einzelne_mailboxen_zielen(db: Session) -> None:
    # Der Weg, wenn jemand eine Gruppe verlaesst: der Browser bleibt, eine
    # Mailbox faellt weg.
    _eintragen(db, UNABLEITBAR)
    _eintragen(db, "f" * 64)

    webpush_service.austragen_mailboxen(db, ENDPUNKT_A, nur={UNABLEITBAR})

    assert webpush_service.mailboxen_von(db, ENDPUNKT_A) == {"f" * 64}


def test_austragen_ohne_adresse_raeumt_nichts(db: Session) -> None:
    """Eine leere Adresse darf nicht alles treffen.

    `endpunkt_abdruck("")` ist ein gueltiger SHA-256 — ohne die Pruefung darauf
    wuerde eine leere Angabe zu einer Abfrage, die zufaellig nichts findet, und
    eine versehentlich leere `nur`-Menge zu einer, die alles findet.
    """
    _eintragen(db, UNABLEITBAR)

    assert webpush_service.austragen_mailboxen(db, "") == 0
    assert webpush_service.austragen_mailboxen(db, ENDPUNKT_A, nur=set()) == 0
    assert db.query(E2eeMailboxPush).count() == 1


# ── Der Versand ─────────────────────────────────────────────────────────────


def test_zugestellt_wird_an_die_adressen_der_mailbox(db: Session, versand: list) -> None:
    _eintragen(db, UNABLEITBAR, ENDPUNKT_A)
    _eintragen(db, UNABLEITBAR, ENDPUNKT_B)
    _eintragen(db, "f" * 64, "https://fcm.googleapis.com/fcm/send/fremd")

    anzahl = webpush_service.sende_an_mailbox(db, UNABLEITBAR, {"title": "Neue Nachricht"})

    assert anzahl == 2
    ziele = {z[1] for z in versand[0][0]}
    assert ziele == {ENDPUNKT_A, ENDPUNKT_B}


def test_eine_unbekannte_mailbox_stellt_nichts_zu(db: Session, versand: list) -> None:
    assert webpush_service.sende_an_mailbox(db, UNABLEITBAR, {"title": "x"}) == 0
    assert versand == []


def test_der_absender_haelt_sich_selbst_heraus(db: Session, versand: list) -> None:
    """Der Ersatz fuer `is_outgoing_echo` auf einem Weg ohne Kennungen.

    Der absendende Browser steht in derselben Liste wie die Empfaenger, und es
    gibt hier nichts, woran der Server ihn erkennen koennte — ausser dem
    Abdruck, den er selbst mitschickt.
    """
    _eintragen(db, UNABLEITBAR, ENDPUNKT_A)
    _eintragen(db, UNABLEITBAR, ENDPUNKT_B)
    eigener = webpush_service.endpunkt_abdruck(ENDPUNKT_A)

    anzahl = webpush_service.sende_an_mailbox(
        db, UNABLEITBAR, {"title": "x"}, ausser_abdruck=eigener
    )

    assert anzahl == 1
    assert [z[1] for z in versand[0][0]] == [ENDPUNKT_B]


def test_ein_fremder_abdruck_haelt_niemanden_heraus(db: Session, versand: list) -> None:
    # Die Ausnahme ist kein Werkzeug, um anderen die Meldung zu nehmen: sie
    # trifft nur, wer die Adresse hat — und das ist der Browser selbst.
    _eintragen(db, UNABLEITBAR, ENDPUNKT_A)

    anzahl = webpush_service.sende_an_mailbox(
        db, UNABLEITBAR, {"title": "x"}, ausser_abdruck="9" * 64
    )

    assert anzahl == 1


def test_tote_adressen_fallen_aus_der_richtigen_tabelle(
    db: Session, owner_user: User, monkeypatch
) -> None:
    """Ein 410 raeumt die Mailbox-Zeile ab, nicht die kontogebundene.

    Beide Wege reichen `id`-Werte in denselben Hintergrundfaden. Ohne das
    Unterscheidungsmerkmal loeschte ein toter Mailbox-Browser eine
    gleichnummerige Zeile aus `push_subscriptions` — ein Konto bekaeme still
    nie wieder eine Benachrichtigung.
    """
    webpush_service.eintragen(db, owner_user, endpoint=ENDPUNKT_B, p256dh=PUNKT, auth=AUTH)
    _eintragen(db, UNABLEITBAR, ENDPUNKT_A)
    zeile = db.query(E2eeMailboxPush).one()
    konto_zeile = db.query(PushSubscription).one()

    monkeypatch.setattr(webpush_service, "_zustellen", lambda *a, **k: False)
    pem, oeff = webpush_service._neues_paar()
    webpush_service._zustellen_alle(
        [(zeile.id, ENDPUNKT_A, PUNKT, AUTH)], b"{}", pem, oeff, True
    )

    assert db.query(E2eeMailboxPush).count() == 0
    assert db.query(PushSubscription).filter_by(id=konto_zeile.id).count() == 1


# ── Was in der Meldung steht ────────────────────────────────────────────────


def test_die_meldung_nennt_niemanden() -> None:
    """Der Unterschied zu `prepare_push_dispatch`, und der ganze Grund dafuer.

    Die grosse Schwester setzt `target_user_id` und `sender_user_id` in die
    Nutzlast. Auf diesem Weg waere das der Rueckweg zu allem, was gerade
    abgebaut wurde: die Mailbox verraet niemanden, die Meldung darin dann aber
    doch.
    """
    nutzlast = NotificationService.prepare_mailbox_push()

    assert nutzlast is not None
    assert "target_user_id" not in nutzlast
    assert "sender_user_id" not in nutzlast
    assert not any("user" in k or "sender" in k for k in nutzlast)


def test_steuersignale_loesen_keine_meldung_aus() -> None:
    # Eine Lesequittung ist nichts, worueber ein Telefon klingelt.
    assert NotificationService.prepare_mailbox_push(is_control=True) is None
    assert NotificationService.prepare_mailbox_push(control_type="read_receipt") is None
    assert NotificationService.prepare_mailbox_push(control_type="delivery_receipt") is None
    assert NotificationService.prepare_mailbox_push(control_type="message") is not None


# ── Der Weg durch das Relais ────────────────────────────────────────────────


def test_relais_meldet_an_die_mailbox_und_nicht_an_ein_konto(
    db: Session, owner_user: User, versand: list, monkeypatch
) -> None:
    """Der eigentliche Zweck: eine Mailbox ohne Konto bekommt trotzdem Push."""
    SocialService.register_blind_mailbox(db, owner_user.id, UNABLEITBAR, TOKEN)
    _eintragen(db, UNABLEITBAR, ENDPUNKT_B)

    an_konten: list[int] = []
    monkeypatch.setattr(
        webpush_service,
        "sende_an_konto",
        lambda _db, uid, _n: an_konten.append(uid) or 0,
    )

    SocialService.relay_blind_envelope(
        db,
        blind_mailbox_id=UNABLEITBAR,
        ciphertext_envelope=_umschlag("push-1"),
        sender_user_id=owner_user.id,
        mailbox_token=TOKEN,
    )

    assert an_konten == []
    assert len(versand) == 1
    assert [z[1] for z in versand[0][0]] == [ENDPUNKT_B]


def test_relais_haelt_den_absender_heraus(
    db: Session, owner_user: User, versand: list
) -> None:
    SocialService.register_blind_mailbox(db, owner_user.id, UNABLEITBAR, TOKEN)
    _eintragen(db, UNABLEITBAR, ENDPUNKT_A)

    SocialService.relay_blind_envelope(
        db,
        blind_mailbox_id=UNABLEITBAR,
        ciphertext_envelope=_umschlag("push-2"),
        sender_user_id=owner_user.id,
        mailbox_token=TOKEN,
        push_ausnahme=webpush_service.endpunkt_abdruck(ENDPUNKT_A),
    )

    assert versand == []


def test_relais_meldet_keine_steuersignale(
    db: Session, owner_user: User, versand: list
) -> None:
    SocialService.register_blind_mailbox(db, owner_user.id, UNABLEITBAR, TOKEN)
    _eintragen(db, UNABLEITBAR, ENDPUNKT_B)

    SocialService.relay_blind_envelope(
        db,
        blind_mailbox_id=UNABLEITBAR,
        ciphertext_envelope=_umschlag("push-3"),
        sender_user_id=owner_user.id,
        mailbox_token=TOKEN,
        is_control=True,
        control_type="read_receipt",
    )

    assert versand == []


# ── Die Tuer: wer sich eintragen darf ───────────────────────────────────────


def _melde(client: TestClient, cookies: dict, csrf: str | None, eintraege: list[dict],
           endpunkt: str = ENDPUNKT_A) -> dict:
    antwort = client.post(
        "/api/social/e2ee/mailbox-push",
        json={"endpoint": endpunkt, "p256dh": PUNKT, "auth": AUTH, "eintraege": eintraege},
        cookies=cookies,
        headers={"X-CSRF-Token": csrf} if csrf else {},
    )
    return {"status": antwort.status_code, "koerper": antwort.json()}


def test_eine_fremde_mailbox_laesst_sich_nicht_eintragen(
    client: TestClient, db: Session, owner_cookies: dict, csrf_token: str | None
) -> None:
    """Der Kern der Schranke.

    Wer sich fuer eine fremde Mailbox eintragen koennte, bekaeme eine Meldung,
    sobald sich dort etwas regt. Das ist Verkehrsanalyse mit Klingelton — ohne
    ein einziges Byte Klartext.
    """
    antwort = _melde(client, owner_cookies, csrf_token, [{"mailbox_id": UNABLEITBAR}])

    assert antwort["status"] == 200
    assert antwort["koerper"]["count"] == 0
    assert db.query(E2eeMailboxPush).count() == 0


def test_ein_falsches_token_traegt_nicht_ein(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict,
    csrf_token: str | None
) -> None:
    SocialService.register_blind_mailbox(db, owner_user.id, UNABLEITBAR, TOKEN)

    antwort = _melde(
        client, owner_cookies, csrf_token,
        [{"mailbox_id": UNABLEITBAR, "mailbox_token": "0" * 64}],
    )

    assert antwort["koerper"]["count"] == 0
    assert db.query(E2eeMailboxPush).count() == 0


def test_mit_dem_nachweis_geht_es(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict,
    csrf_token: str | None
) -> None:
    SocialService.register_blind_mailbox(db, owner_user.id, UNABLEITBAR, TOKEN)

    antwort = _melde(
        client, owner_cookies, csrf_token,
        [{"mailbox_id": UNABLEITBAR, "mailbox_token": TOKEN}],
    )

    assert antwort["koerper"]["count"] == 1
    assert webpush_service.mailboxen_von(db, ENDPUNKT_A) == {UNABLEITBAR}


def test_was_nicht_mehr_genannt_wird_faellt_weg(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict,
    csrf_token: str | None
) -> None:
    """Der Fall „Gruppe verlassen".

    Bliebe die Zeile stehen, bekaeme das Geraet weiter Meldungen ueber
    Nachrichten, die es nicht mehr lesen kann — und der Server behielte eine
    Zuordnung, die es nicht mehr gibt.
    """
    zweite = "a" * 64
    SocialService.register_blind_mailbox(db, owner_user.id, UNABLEITBAR, TOKEN)
    SocialService.register_blind_mailbox(db, owner_user.id, zweite, TOKEN)
    _melde(
        client, owner_cookies, csrf_token,
        [
            {"mailbox_id": UNABLEITBAR, "mailbox_token": TOKEN},
            {"mailbox_id": zweite, "mailbox_token": TOKEN},
        ],
    )
    assert len(webpush_service.mailboxen_von(db, ENDPUNKT_A)) == 2

    _melde(client, owner_cookies, csrf_token, [{"mailbox_id": zweite, "mailbox_token": TOKEN}])

    assert webpush_service.mailboxen_von(db, ENDPUNKT_A) == {zweite}


def test_ein_anderer_browser_wird_dabei_nicht_abgeraeumt(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict,
    csrf_token: str | None
) -> None:
    # Das Abraeumen zielt auf eine Adresse, nicht auf eine Mailbox. Ohne diese
    # Einschraenkung naehme jede Meldung eines Geraets den anderen ihre Zeilen.
    SocialService.register_blind_mailbox(db, owner_user.id, UNABLEITBAR, TOKEN)
    _eintragen(db, UNABLEITBAR, ENDPUNKT_B)

    _melde(client, owner_cookies, csrf_token, [], endpunkt=ENDPUNKT_A)

    assert webpush_service.mailboxen_von(db, ENDPUNKT_B) == {UNABLEITBAR}


def test_ohne_anmeldung_geht_nichts(client: TestClient) -> None:
    antwort = client.post(
        "/api/social/e2ee/mailbox-push",
        json={"endpoint": ENDPUNKT_A, "p256dh": PUNKT, "auth": AUTH, "eintraege": []},
    )
    assert antwort.status_code in (401, 403)


def test_zu_viele_kennungen_fallen_am_schema_durch(
    client: TestClient, owner_cookies: dict, csrf_token: str | None
) -> None:
    antwort = client.post(
        "/api/social/e2ee/mailbox-push",
        json={
            "endpoint": ENDPUNKT_A,
            "p256dh": PUNKT,
            "auth": AUTH,
            "eintraege": [{"mailbox_id": f"{i:064d}"} for i in range(250)],
        },
        cookies=owner_cookies,
        headers={"X-CSRF-Token": csrf_token} if csrf_token else {},
    )
    assert antwort.status_code == 422


def test_ein_zusammengesetzter_abdruck_faellt_am_schema_durch(
    client: TestClient, owner_cookies: dict, csrf_token: str | None
) -> None:
    """`push_ausnahme` ist ein SHA-256, sonst nichts.

    Die Angabe entscheidet, wer **keine** Meldung bekommt. Waere sie frei
    formbar, waere sie ein Weg, Zeichen in eine Datenbankabfrage zu geben.
    """
    antwort = client.post(
        "/api/social/e2ee/relay",
        json={
            "blind_mailbox_id": UNABLEITBAR,
            "ciphertext_envelope": _umschlag("schema"),
            "push_ausnahme": "' OR 1=1 --",
        },
        cookies=owner_cookies,
        headers={"X-CSRF-Token": csrf_token} if csrf_token else {},
    )
    assert antwort.status_code == 422


# ── Dieselbe Tuer wie beim Strom ────────────────────────────────────────────


def test_strom_und_push_teilen_die_pruefung(db: Session, owner_user: User) -> None:
    """Eine Pruefung, zwei Tueren.

    Stuende sie zweimal da, waere die zweite Fassung irgendwann die
    nachsichtigere — und Push waere der Weg, ueber den man erfaehrt, was der
    Strom einem nicht sagt. Hier wird festgehalten, dass beide Router
    dieselbe Funktion rufen.
    """
    import inspect

    from routers import social as social_router
    from routers import sync_events as sync_router

    quelle_sse = inspect.getsource(sync_router.set_stream_mailboxes)
    quelle_push = inspect.getsource(social_router.subscribe_mailbox_push)

    assert "erlaubte_mailboxen" in quelle_sse
    assert "erlaubte_mailboxen" in quelle_push

    # Und die Funktion selbst tut, was beide von ihr erwarten.
    SocialService.register_blind_mailbox(db, owner_user.id, UNABLEITBAR, TOKEN)
    assert SocialService.erlaubte_mailboxen(db, owner_user.id, [(UNABLEITBAR, TOKEN)]) == [
        UNABLEITBAR
    ]
    assert SocialService.erlaubte_mailboxen(db, owner_user.id, [(UNABLEITBAR, None)]) == []
    assert SocialService.erlaubte_mailboxen(db, owner_user.id, [("z" * 64, None)]) == []
