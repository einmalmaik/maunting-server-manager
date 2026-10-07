"""Gedächtnis v2, Stufe 4: der Abruf bei großen Gedächtnissen.

Was diese Tests festhalten:

* Passt alles, steht alles im Kopf — wie bisher —, und der Kopf hängt nicht
  an der Frage: zwei Fragen, derselbe Text, derselbe Zwischenspeicher.
* Passt nicht alles: im Kopf das Angeheftete, dann das Wichtige, dann das
  Neue; was die Frage trifft, kommt hinten dazu, und nur das.
* Eine Anfrage öffnet nicht mehr als Kopf und Kandidaten, egal wie groß der
  Bestand ist.
* Gebraucht ist, wen die Frage trifft — gezeigt zu werden ist kein Gebrauch.
* Der Wortindex folgt dem Text, kennt keine Füllwörter und keinen fremden
  Bereich; der Vektorspeicher sieht Neues, Vergessenes und Gelöschtes.
* Anheften ohne neue Fassung, die Suche der Verwaltung über den ganzen
  Bestand, das Nachziehen im Takt, der Schreiber bei großen Bereichen.

Das Modell ist ein Ersatz mit festen Achsen (`_rechner`). Alle Personen sind
erfunden.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import AiConversation, AiMemoryBegriff, AiMemoryEntry, AiMessage, Role, RolePermission, User
from services import ai_embedding_service, ai_gedaechtnis_abruf, ai_gedaechtnis_schreiber, ai_memory_service
from services.ai_context_service import build_provider_messages
from services.ai_limit_service import LIMIT_FIELDS, set_role_limit
from services.role_service import set_user_roles
from tests._einbettung import modell_ersetzen, ohne_modell
from tests._entschluesselung import mitzaehlen, sidecar_tot

#: Je Achse die Wörter, die auf ihr liegen — auch über Sprachen hinweg.
_ACHSEN = (("kaffee", "coffee"), ("katze", "cat"), ("berg", "mountain"))


def _rechner(texte: list[str]) -> list[list[float]]:
    """Ein Ersatzmodell: ein Stichwort, eine Achse; ohne Stichwort der Nullvektor."""
    vektoren = []
    for text in texte:
        klein = text.lower()
        vektor = [0.0] * ai_embedding_service.EMBEDDING_DIMENSIONS
        for achse, woerter in enumerate(_ACHSEN):
            if any(wort in klein for wort in woerter):
                vektor[achse] = 1.0
                break
        vektoren.append(vektor)
    return vektoren


def _freigeben(db: Session, user: User) -> None:
    role = Role(name=f"abruf-{user.id}-{uuid4().hex[:6]}", is_system=False)
    db.add(role)
    db.flush()
    db.add_all([
        RolePermission(role_id=role.id, permission_key="ai.memory.use"),
        RolePermission(role_id=role.id, permission_key="ai.chat.use"),
    ])
    set_role_limit(db, role.id, {feld: None for feld in LIMIT_FIELDS})
    db.commit()
    set_user_roles(db, user, [role.id])
    ai_memory_service.set_preference(db, user, True)


def _merken(db: Session, user: User, text: str, *, wichtigkeit: int = 3, alter_tage: int = 0) -> AiMemoryEntry:
    row, _ = ai_memory_service.erinnerung_anlegen(
        db, user=user, scope="user", text=text, wichtigkeit=wichtigkeit, origin="user",
    )
    if alter_tage:
        row.created_at = datetime.now(timezone.utc) - timedelta(days=alter_tage)
        db.commit()
    return row


def _fuellen(db: Session, user: User, anzahl: int) -> None:
    for nummer in range(anzahl):
        _merken(db, user, f"Notiz {nummer:03d} aus dem Alltag, {'Wortfüllung ' * 4}".strip())


def _grosser_bestand(db: Session, user: User) -> dict[str, AiMemoryEntry]:
    """40 Füllnotizen und vier, um die es geht — zu viel für ein Budget von 1.200."""
    zeilen = {
        "kaffee": _merken(db, user, "Jonas trinkt Kaffee schwarz, ohne Zucker.", wichtigkeit=2, alter_tage=30),
        "katze": _merken(db, user, "Jonas hat eine Katze namens Pixel.", wichtigkeit=2, alter_tage=30),
    }
    _fuellen(db, user, 40)
    zeilen["allergie"] = _merken(db, user, "Jonas ist gegen Erdnüsse allergisch.", wichtigkeit=5, alter_tage=60)
    zeilen["duzen"] = _merken(db, user, "Singra duzt Jonas.", wichtigkeit=1, alter_tage=90)
    zeilen["duzen"].angeheftet = True
    db.commit()
    return zeilen


# ── Kopf und Passendes ──────────────────────────────────────────────────────


def test_passt_alles_steht_alles_im_kopf_und_der_kopf_haengt_nicht_an_der_frage(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ohne_modell(monkeypatch)
    _freigeben(db, regular_user)
    for text in ("Jonas trinkt Kaffee schwarz.", "Jonas wohnt in Köln.", "Jonas spielt Schach."):
        _merken(db, regular_user, text)

    eins = ai_gedaechtnis_abruf.abrufen(db, regular_user, "Was trinke ich morgens?")
    zwei = ai_gedaechtnis_abruf.abrufen(db, regular_user, "Wo wohne ich?")

    assert eins.kopf == zwei.kopf
    assert all(text in eins.kopf for text in ("Kaffee schwarz", "Köln", "Schach"))
    assert "ausgelassen" not in eins.kopf
    assert eins.passend is None and zwei.passend is None


def test_ist_der_bestand_groesser_steht_vorn_das_wichtige_und_hinten_das_passende(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ohne_modell(monkeypatch)
    _freigeben(db, regular_user)
    _grosser_bestand(db, regular_user)

    kaffee = ai_gedaechtnis_abruf.abrufen(db, regular_user, "Wie trinke ich meinen Kaffee?", budget=1_200)
    katze = ai_gedaechtnis_abruf.abrufen(db, regular_user, "Wie heißt meine Katze?", budget=1_200)

    zeilen = kaffee.kopf.splitlines()
    # Angeheftet vor wichtig, obwohl es das Unwichtigste und Älteste ist.
    assert "duzt Jonas" in zeilen[0]
    assert "Erdnüsse" in zeilen[1]
    assert "[Hinweis]" in zeilen[-1] and "ausgelassen" in zeilen[-1]
    # Der Kopf ist für beide Fragen derselbe — er wird zwischengespeichert.
    assert katze.kopf == kaffee.kopf
    assert "Kaffee" not in kaffee.kopf
    assert kaffee.passend is not None and "Kaffee schwarz" in kaffee.passend
    assert katze.passend is not None and "Pixel" in katze.passend
    assert "Kaffee" not in katze.passend
    assert len(kaffee.kopf) + len(kaffee.passend) <= 1_200


def test_die_bedeutung_findet_auch_ohne_gemeinsames_wort(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    modell_ersetzen(monkeypatch, _rechner)
    _freigeben(db, regular_user)
    _grosser_bestand(db, regular_user)

    abruf = ai_gedaechtnis_abruf.abrufen(db, regular_user, "How do I take my coffee?", budget=1_200)

    assert abruf.passend is not None and "Kaffee schwarz" in abruf.passend


def test_hinten_steht_nur_was_die_frage_trifft(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Kein Auffüllen mit Ballast — jedes Zeichen dort kostet Token je Anfrage."""
    modell_ersetzen(monkeypatch, _rechner)
    _freigeben(db, regular_user)
    _grosser_bestand(db, regular_user)

    abruf = ai_gedaechtnis_abruf.abrufen(db, regular_user, "Wie spät ist es in Tokio?", budget=1_200)

    assert abruf.kopf is not None
    assert abruf.passend is None


def _schraeg(texte: list[str]) -> list[list[float]]:
    """Die Frage auf Achse 0, das Ziel mit 0,25 dazu, alles andere quer."""
    vektoren = []
    for text in texte:
        vektor = [0.0] * ai_embedding_service.EMBEDDING_DIMENSIONS
        if "Leuchtturm" in text:
            vektor[0], vektor[1] = 0.25, (1 - 0.25 ** 2) ** 0.5
        elif "Seezeichen" in text:
            vektor[0] = 1.0
        else:
            vektor[2] = 1.0
        vektoren.append(vektor)
    return vektoren


@pytest.mark.parametrize(("fuellung", "erwartet"), [(120, True), (40, False)])
def test_im_grossen_bereich_reicht_weniger_wenn_es_aus_dem_rauschen_ragt(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch, fuellung: int, erwartet: bool,
) -> None:
    """0,25 liegt unter 0,35 — trifft aber, wenn sonst alles bei null liegt.

    Gemessen an 100.000 Einträgen: über Sprachen hinweg stand die Antwort auf
    Platz 1 und fiel an der festen Schwelle heraus. Unter 100 Zeilen sagt das
    Rauschen nichts, dort gilt weiter 0,35.
    """
    modell_ersetzen(monkeypatch, _schraeg)
    _freigeben(db, regular_user)
    _merken(db, regular_user, "Jonas mag den Leuchtturm in Hörnum.", alter_tage=30)
    _fuellen(db, regular_user, fuellung)

    abruf = ai_gedaechtnis_abruf.abrufen(db, regular_user, "Seezeichen?", budget=1_200)

    assert ("Leuchtturm" in (abruf.passend or "")) is erwartet

def test_eine_anfrage_oeffnet_nur_kopf_und_kandidaten(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Der Aufwand hängt an zwei Deckeln, nicht am Bestand."""
    ohne_modell(monkeypatch)
    _freigeben(db, regular_user)
    _merken(db, regular_user, "Jonas trinkt Kaffee schwarz.", alter_tage=30)
    _fuellen(db, regular_user, 60)
    monkeypatch.setattr(ai_memory_service, "MAX_CONTEXT_ROWS", 5)
    monkeypatch.setattr(ai_gedaechtnis_abruf, "KANDIDATEN", 3)
    zaehler = mitzaehlen(monkeypatch)

    abruf = ai_gedaechtnis_abruf.abrufen(db, regular_user, "Kaffee im Alltag?")

    assert abruf.kopf is not None
    assert zaehler.texte <= 5 + 3


def test_gebraucht_ist_wen_die_frage_trifft(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ohne_modell(monkeypatch)
    _freigeben(db, regular_user)
    zeilen = _grosser_bestand(db, regular_user)
    zeilen["allergie"].use_count = 0
    db.commit()

    ai_gedaechtnis_abruf.abrufen(db, regular_user, "Bin ich allergisch, und wie trinke ich Kaffee?", budget=1_200)
    db.commit()
    db.expire_all()

    # Hinten getroffen und im Kopf getroffen: beides ist Gebrauch.
    assert db.get(AiMemoryEntry, zeilen["kaffee"].id).use_count == 1
    assert db.get(AiMemoryEntry, zeilen["allergie"].id).use_count == 1
    # Im Kopf gezeigt, aber nicht gefragt: kein Gebrauch.
    assert db.get(AiMemoryEntry, zeilen["duzen"].id).use_count == 0
    assert db.get(AiMemoryEntry, zeilen["katze"].id).use_count == 0


def test_der_kopf_steht_vor_dem_verlauf_und_das_passende_dahinter(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nur so bleibt der Präfix bis zum Verlauf zwischen zwei Fragen gleich."""
    ohne_modell(monkeypatch)
    _freigeben(db, regular_user)
    _merken(db, regular_user, "Jonas trinkt Kaffee schwarz.", wichtigkeit=1, alter_tage=30)
    _fuellen(db, regular_user, 120)
    gespraech = AiConversation(id=str(uuid4()), user_id=regular_user.id, title="Abruf")
    db.add(gespraech)
    db.commit()
    frueher = datetime.now(timezone.utc) - timedelta(minutes=5)
    for nummer, (rolle, inhalt) in enumerate((
        ("user", "Guten Morgen, Singra."),
        ("assistant", "Guten Morgen!"),
        ("user", "Wie trinke ich meinen Kaffee?"),
    )):
        db.add(AiMessage(
            id=str(uuid4()), conversation_id=gespraech.id, role=rolle, content=inhalt,
            status="complete", created_at=frueher + timedelta(minutes=nummer),
        ))
    db.commit()

    nachrichten = build_provider_messages(db, gespraech, "Wie trinke ich meinen Kaffee?")
    inhalte = [str(nachricht.get("content")) for nachricht in nachrichten]

    def stelle(probe: str) -> int:
        return next(nummer for nummer, inhalt in enumerate(inhalte) if probe in inhalt)

    kopf = stelle("Praeferenzdaten (Memory) — Daten")
    verlauf = stelle("Guten Morgen, Singra.")
    passend = stelle("Praeferenzdaten (Memory) passend zur Frage")
    assert kopf < verlauf < passend
    assert "Kaffee schwarz" in inhalte[passend]
    assert "Kaffee schwarz" not in inhalte[kopf]


def test_die_stimme_bekommt_den_kopf(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.ai_voice import realtime_session

    ohne_modell(monkeypatch)
    _freigeben(db, regular_user)
    _grosser_bestand(db, regular_user)

    text = realtime_session.gedaechtnis(db, regular_user)

    assert text.splitlines()[0].endswith("Singra duzt Jonas.")


# ── Wortindex ────────────────────────────────────────────────────────────────


def test_fuellwoerter_treffen_nichts() -> None:
    """„ist“ und „der“ stehen in fast jeder Erinnerung — sie träfen alles."""
    assert ai_memory_service._tokens("Was ist der Plan des Benutzers?") == {"plan"}


def test_der_wortindex_folgt_dem_text_und_geht_mit_der_zeile(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ohne_modell(monkeypatch)
    _freigeben(db, regular_user)
    row = _merken(db, regular_user, "Jonas trinkt Kaffee schwarz.")
    kennung = row.scope_identity

    def werte() -> list[int]:
        return sorted(
            wert for (wert,) in db.query(AiMemoryBegriff.begriff)
            .filter(AiMemoryBegriff.memory_id == row.id).all()
        )

    assert werte() == ai_memory_service.begriff_werte(kennung, {"jonas", "trinkt", "kaffee", "schwarz"})
    assert row.indiziert_am is not None

    ai_memory_service.erinnerung_aendern(db, user=regular_user, entry_id=row.id, text="Jonas trinkt Tee.")
    assert werte() == ai_memory_service.begriff_werte(kennung, {"jonas", "trinkt", "tee"})

    ai_memory_service.delete_entry(db, regular_user, row.id)
    assert db.query(AiMemoryBegriff).count() == 0


def test_ein_wort_trifft_nur_im_eigenen_bereich(
    db: Session, regular_user: User, owner_user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ohne_modell(monkeypatch)
    _freigeben(db, regular_user)
    _freigeben(db, owner_user)
    _merken(db, owner_user, "Lea trinkt Kaffee mit Hafermilch.")
    _fuellen(db, regular_user, 60)

    abruf = ai_gedaechtnis_abruf.abrufen(db, regular_user, "Wie trinkt man Kaffee?", budget=1_200)

    assert ai_memory_service.begriff_werte("user:1", ["kaffee"]) != ai_memory_service.begriff_werte("user:2", ["kaffee"])
    assert abruf.passend is None
    assert "Hafermilch" not in (abruf.kopf or "")


# ── Vektorspeicher ───────────────────────────────────────────────────────────


def test_der_vektorspeicher_sieht_neues_vergessenes_und_geloeschtes(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gefragt wird auf Englisch: getroffen wird allein über den Vektor."""
    modell_ersetzen(monkeypatch, _rechner)
    _freigeben(db, regular_user)
    _fuellen(db, regular_user, 40)

    def passend() -> str:
        return ai_gedaechtnis_abruf.abrufen(db, regular_user, "coffee?", budget=1_200).passend or ""

    assert passend() == ""
    row = _merken(db, regular_user, "Jonas trinkt Kaffee schwarz.", alter_tage=30)
    assert "Kaffee schwarz" in passend()

    ai_memory_service.erinnerung_vergessen(db, user=regular_user, entry_id=row.id)
    assert passend() == ""
    ai_memory_service.erinnerung_zurueckholen(db, user=regular_user, entry_id=row.id)
    assert "Kaffee schwarz" in passend()

    ai_memory_service.delete_entry(db, regular_user, row.id)
    assert passend() == ""
    bestand = ai_gedaechtnis_abruf._SPEICHER[row.scope_identity]
    assert row.id not in bestand.position
    assert len(bestand.ids) + len(bestand.ohne) == 40


# ── Anheften ─────────────────────────────────────────────────────────────────


def _csrf(cookies: dict) -> dict[str, str]:
    return {"X-CSRF-Token": cookies.get("__Secure-csrf_token", "")}


def test_anheften_stellt_vorn_ohne_neue_fassung(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ohne_modell(monkeypatch)
    _freigeben(db, regular_user)
    zeilen = _grosser_bestand(db, regular_user)
    kaffee = zeilen["kaffee"]

    antwort = client.post(
        f"/api/ai/memory/{kaffee.id}/anheften", json={"angeheftet": True},
        cookies=user_cookies, headers=_csrf(user_cookies),
    )

    assert antwort.status_code == 200, antwort.text
    assert antwort.json()["angeheftet"] is True
    assert antwort.json()["fassung"] == 1
    db.expire_all()
    kopf = ai_gedaechtnis_abruf.abrufen(db, regular_user, "", budget=1_200).kopf
    assert "Kaffee schwarz" in kopf.splitlines()[0] or "Kaffee schwarz" in kopf.splitlines()[1]
    liste = client.get("/api/ai/memory/personal", cookies=user_cookies).json()["entries"]
    assert {eintrag["id"] for eintrag in liste[:2]} == {kaffee.id, zeilen["duzen"].id}

    # Ohne CSRF nicht, und Vergessenes nicht.
    assert client.post(
        f"/api/ai/memory/{kaffee.id}/anheften", json={"angeheftet": False}, cookies=user_cookies,
    ).status_code == 403
    ai_memory_service.erinnerung_vergessen(db, user=regular_user, entry_id=zeilen["katze"].id)
    assert client.post(
        f"/api/ai/memory/{zeilen['katze'].id}/anheften", json={"angeheftet": True},
        cookies=user_cookies, headers=_csrf(user_cookies),
    ).status_code == 409


def test_eine_fremde_erinnerung_heftet_niemand_an(
    db: Session, regular_user: User, owner_user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException

    ohne_modell(monkeypatch)
    _freigeben(db, owner_user)
    fremd = _merken(db, owner_user, "Lea trinkt Kaffee mit Hafermilch.")

    with pytest.raises(HTTPException) as fehler:
        ai_memory_service.erinnerung_anheften(db, user=regular_user, entry_id=fremd.id, angeheftet=True)

    assert fehler.value.status_code == 404


# ── Suche der Verwaltung ─────────────────────────────────────────────────────


def test_die_suche_der_verwaltung_findet_ueber_die_seite_hinaus_und_zaehlt_nicht(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ohne_modell(monkeypatch)
    _freigeben(db, regular_user)
    kaffee = _merken(db, regular_user, "Jonas trinkt Kaffee schwarz.", alter_tage=30)
    _fuellen(db, regular_user, 12)
    monkeypatch.setattr(ai_memory_service, "PERSONAL_PAGE_SIZE", 5)

    erste = client.get("/api/ai/memory/personal", cookies=user_cookies).json()
    gesucht = client.get(
        "/api/ai/memory/personal", params={"suche": "Kaffee"}, cookies=user_cookies,
    ).json()

    assert kaffee.id not in [eintrag["id"] for eintrag in erste["entries"]]
    assert [eintrag["id"] for eintrag in gesucht["entries"]] == [kaffee.id]
    assert gesucht["total"] == 1
    db.expire_all()
    assert db.get(AiMemoryEntry, kaffee.id).use_count == 0


# ── Nachziehen und Schreiber ─────────────────────────────────────────────────


def _wie_vor_stufe_vier(db: Session, row: AiMemoryEntry) -> None:
    db.query(AiMemoryBegriff).filter(AiMemoryBegriff.memory_id == row.id).delete()
    row.indiziert_am = None
    row.embedding_bytes = None
    row.embedding_model = None
    db.commit()


def test_der_takt_zieht_den_bestand_nach(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    modell_ersetzen(monkeypatch, _rechner)
    _freigeben(db, regular_user)
    row = _merken(db, regular_user, "Jonas trinkt Kaffee schwarz.")
    _wie_vor_stufe_vier(db, row)

    assert ai_gedaechtnis_abruf.nachziehen() == 1

    db.expire_all()
    row = db.get(AiMemoryEntry, row.id)
    assert row.indiziert_am is not None
    assert ai_memory_service._stored_vector(row, ai_embedding_service.MODEL_TAG) is not None
    assert db.query(AiMemoryBegriff).filter(AiMemoryBegriff.memory_id == row.id).count() == 4
    assert ai_gedaechtnis_abruf.nachziehen() == 0


def test_ohne_sidecar_bleibt_alles_offen(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sonst stünden Zeilen als erledigt da, deren Wörter fehlen."""
    ohne_modell(monkeypatch)
    _freigeben(db, regular_user)
    row = _merken(db, regular_user, "Jonas trinkt Kaffee schwarz.")
    _wie_vor_stufe_vier(db, row)
    sidecar_tot(monkeypatch)

    assert ai_gedaechtnis_abruf.nachziehen() == 0

    db.expire_all()
    assert db.get(AiMemoryEntry, row.id).indiziert_am is None


def test_der_schreiber_laedt_einen_grossen_bereich_nicht_ganz(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ohne_modell(monkeypatch)
    _freigeben(db, regular_user)
    _merken(db, regular_user, "Jonas trinkt Kaffee schwarz.", alter_tage=30)
    _fuellen(db, regular_user, 20)
    monkeypatch.setattr(ai_gedaechtnis_schreiber, "MAX_BESTAND_JE_BEREICH", 5)
    zaehler = mitzaehlen(monkeypatch)
    bereich = ai_gedaechtnis_schreiber.Bereich(
        kennung="B1", scope="user", identity=f"user:{regular_user.id}", name="dein Gedächtnis",
        server_id=None, team_id=None, frei=None,
    )

    kandidaten = ai_gedaechtnis_schreiber._bestand(db, [bereich], ["Ich trinke Kaffee jetzt mit Milch."])

    assert len(kandidaten) <= 5
    assert any("Kaffee schwarz" in kandidat.text for kandidat in kandidaten)
    assert zaehler.texte <= 5
