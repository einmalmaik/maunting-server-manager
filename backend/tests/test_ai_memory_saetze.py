"""Gedächtnis v2, Stufe 1: Sätze statt Schlüssel.

Seit dem 06.10.2026 ist eine Erinnerung ein bis fünf Sätze ohne Namen, mit
Titel, Thema, Quelle und früheren Fassungen daneben. Diese Datei hält fest,
was daran neu ist:

- Was dazukam (Titel, Thema, Fassung), liegt verschlüsselt wie der Text.
- Das Kontingent der Rolle gilt auf jedem Weg, auf dem eine Erinnerung
  dazukommt — 100, eine andere Zahl, unbegrenzt oder gesperrt —, und es
  zählt nur, was gilt.
- Nichts wird still überschrieben: jede Änderung legt den Stand davor ab,
  und wer einen veralteten Stand ändert, bekommt 409.
- Was die KI vergisst, lässt sich 30 Tage lang zurückholen und ist danach weg.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from models import (
    AiMemoryEntry,
    AiMemoryTopic,
    AiMemoryVersion,
    Role,
    RolePermission,
    Server,
    ServerPermission,
    User,
)
from services import ai_limit_service, ai_memory_service
from services.ai_limit_service import LIMIT_FIELDS, set_role_limit
from services.auth_service import AuthService
from services.dis_client import DisClient
from services.role_service import set_user_roles


def _grenze(db: Session, role: Role, grenze: int | None) -> None:
    set_role_limit(db, role.id, {**{feld: None for feld in LIMIT_FIELDS}, "max_memory_entries": grenze})
    db.commit()


def _erlauben(db: Session, user: User, grenze: int | None = None) -> Role:
    """Gedächtnisrecht, eingeschaltet, mit diesem Kontingent (``None`` = unbegrenzt)."""
    role = Role(name=f"saetze-{user.id}", is_system=False)
    db.add(role)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_key="ai.memory.use"))
    _grenze(db, role, grenze)
    set_user_roles(db, user, [role.id])
    ai_memory_service.set_preference(db, user, True)
    return role


def _anlegen(db: Session, user: User, text_: str, **weitere) -> AiMemoryEntry:
    row, _ = ai_memory_service.erinnerung_anlegen(
        db, user=user, scope="user", text=text_, **weitere
    )
    return row


def _zweiter(db: Session) -> User:
    user = AuthService.create_user(db, "jonas", "jonas@test.de", "JonasPass123!")
    user.email_verified = True
    db.commit()
    return user


def _server(db: Session, user: User, name: str = "Werkstatt") -> Server:
    server = Server(name=name, game_type="dayz", install_dir=f"/tmp/{name}", status="stopped")
    db.add(server)
    db.commit()
    db.add(ServerPermission(user_id=user.id, server_id=server.id, permission_key="server.view"))
    db.commit()
    return server


def _csrf(cookies: dict) -> dict[str, str]:
    return {"X-CSRF-Token": cookies.get("__Secure-csrf_token", "")}


# ── Anlegen ─────────────────────────────────────────────────────────────


def test_eine_neue_erinnerung_ist_ein_satz_ohne_namen(db: Session, regular_user: User) -> None:
    """Kein Name, und alles Neue daneben liegt verschlüsselt.

    Titel und Thema sagen so viel wie der Text ("Scheidung", "Diagnose"). Ein
    Titel im Klartext wäre die Zusammenfassung dessen, was der Text schützt.
    """
    _erlauben(db, regular_user)
    satz = "Der Benutzer antwortet am liebsten auf Deutsch."

    row = _anlegen(db, regular_user, satz, titel="Sprache", thema="Vorlieben")

    roh = db.execute(text(
        "SELECT key_encrypted, key_index, value_encrypted, titel_encrypted, quelle, "
        "origin, status, fassung, wichtigkeit, thema_id FROM ai_memory_entries WHERE id = :id"
    ), {"id": row.id}).one()
    assert roh.key_encrypted is None and roh.key_index is None
    assert (roh.quelle, roh.origin, roh.status, roh.fassung, roh.wichtigkeit) == (
        "eingetragen", "user", "aktiv", 1, 3,
    )
    assert DisClient.ist_verschluesselt(roh.value_encrypted) and satz not in roh.value_encrypted
    assert DisClient.ist_verschluesselt(roh.titel_encrypted) and "Sprache" not in roh.titel_encrypted
    assert DisClient.decrypt(
        roh.titel_encrypted, aad=f"msm:ai:memory:titel:user:{regular_user.id}:{row.id}"
    ) == "Sprache"

    thema = db.execute(text(
        "SELECT id, name_encrypted, name_index FROM ai_memory_themen"
    )).one()
    assert thema.id == roh.thema_id
    assert DisClient.ist_verschluesselt(thema.name_encrypted)
    assert "Vorlieben" not in thema.name_encrypted and thema.name_index
    assert DisClient.decrypt(
        thema.name_encrypted, aad=f"msm:ai:memory:thema:user:{regular_user.id}:{thema.id}"
    ) == "Vorlieben"

    # Im Kontext steht der Satz selbst, ohne Namen davor.
    block = ai_memory_service.provider_memory_context(db, regular_user, query="Sprache?")
    assert f"[user/gesagt] {satz}" in block


def test_zugangsdaten_kommen_auch_nicht_ueber_titel_oder_thema_hinein(
    db: Session, regular_user: User
) -> None:
    _erlauben(db, regular_user)
    geheimnis = "api_key=" + "x" * 12

    for weitere in ({"titel": geheimnis}, {"thema": geheimnis}):
        with pytest.raises(HTTPException) as fehler:
            _anlegen(db, regular_user, "Ein harmloser Satz.", **weitere)
        assert fehler.value.status_code == 422
    assert db.query(AiMemoryEntry).count() == 0
    assert db.query(AiMemoryTopic).count() == 0


def test_ein_thema_ist_je_bereich_eins_gleich_wie_geschrieben(
    db: Session, regular_user: User
) -> None:
    """„Familie“ und „familie“ sind ein Thema — aber nur im selben Bereich.

    Ein Thema zu einer Servernotiz ist ein anderes als dasselbe Wort im
    allgemeinen Gedächtnis: es gehört einem anderen Bereich und fällt mit ihm.
    Die Profilansicht zeigt beide; gleichnamige fasst erst die Oberfläche
    zusammen.
    """
    _erlauben(db, regular_user)
    server = _server(db, regular_user)

    eins = _anlegen(db, regular_user, "Die Schwester heißt Mia.", thema="Familie")
    zwei = _anlegen(db, regular_user, "Der Bruder wohnt in Kiel.", thema="  familie ")
    drei, _ = ai_memory_service.erinnerung_anlegen(
        db, user=regular_user, scope="server", server_id=server.id,
        text="Auf diesem Server spielt die Familie samstags.", thema="Familie",
    )

    assert eins.thema_id == zwei.thema_id
    assert drei.thema_id != eins.thema_id
    assert db.query(AiMemoryTopic).count() == 2
    themen = ai_memory_service.personal_themen(db, regular_user)
    assert sorted((t.name, t.anzahl) for t in themen) == [("Familie", 1), ("Familie", 2)]

    seite = ai_memory_service.personal_entries(db, regular_user, themen=[eins.thema_id])
    assert {row.id for row, _ in seite.eintraege} == {eins.id, zwei.id}
    assert seite.themen == {eins.thema_id: "Familie"}


# ── Kontingent ──────────────────────────────────────────────────────────


def test_das_kontingent_zaehlt_nur_was_gilt(db: Session, regular_user: User) -> None:
    """Vergessenes belegt keinen Platz — und kommt nur zurück, wenn welcher frei ist."""
    role = _erlauben(db, regular_user, grenze=2)
    erste = _anlegen(db, regular_user, "Erster Satz.")
    _anlegen(db, regular_user, "Zweiter Satz.")

    with pytest.raises(ai_memory_service.MemoryScopeVoll) as voll:
        _anlegen(db, regular_user, "Dritter Satz.")
    assert voll.value.status_code == 409
    assert "2 von 2" in voll.value.detail
    db.rollback()

    ai_memory_service.erinnerung_vergessen(db, user=regular_user, entry_id=erste.id)
    dritte = _anlegen(db, regular_user, "Dritter Satz.")

    with pytest.raises(ai_memory_service.MemoryScopeVoll):
        ai_memory_service.erinnerung_zurueckholen(db, user=regular_user, entry_id=erste.id)
    db.rollback()

    ai_memory_service.delete_entry(db, regular_user, dritte.id)
    row, wert = ai_memory_service.erinnerung_zurueckholen(
        db, user=regular_user, entry_id=erste.id
    )
    assert (row.status, row.vergessen_am, wert) == ("aktiv", None, "Erster Satz.")

    # Gesperrt heißt gesperrt, auch für das Zurückholen.
    ai_memory_service.erinnerung_vergessen(db, user=regular_user, entry_id=erste.id)
    _grenze(db, role, 0)
    with pytest.raises(ai_memory_service.MemoryScopeVoll) as gesperrt:
        ai_memory_service.erinnerung_zurueckholen(db, user=regular_user, entry_id=erste.id)
    assert "kein Gedächtnis freigegeben" in gesperrt.value.detail


def test_unbegrenzt_heisst_unbegrenzt(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ohne Zahl gilt keine — auch nicht die feste Grenze der geteilten Bereiche.

    Bis zum 05.10.2026 wurde „Unbegrenzt“ still zu 100. Die feste Grenze ist
    hier auf drei gesetzt, damit der Test sie mit fünf Einträgen überschreitet.
    """
    monkeypatch.setattr(ai_limit_service, "MAX_SYSTEM_SCOPE_ENTRIES", 3)
    _erlauben(db, regular_user, grenze=None)

    for nummer in range(5):
        _anlegen(db, regular_user, f"Satz Nummer {nummer}.")

    assert ai_memory_service._bestand(db, f"user:{regular_user.id}") == 5


def test_eine_gesenkte_grenze_nennt_wie_viele_weichen_muessen(
    db: Session, regular_user: User
) -> None:
    role = _erlauben(db, regular_user, grenze=3)
    for nummer in range(3):
        _anlegen(db, regular_user, f"Satz Nummer {nummer}.")
    _grenze(db, role, 1)

    with pytest.raises(ai_memory_service.MemoryScopeVoll) as zu_voll:
        _anlegen(db, regular_user, "Noch einer.")

    assert "erlaubt sind 1" in zu_voll.value.detail
    assert "3 müssen weichen" in zu_voll.value.detail


# ── Ändern und frühere Fassungen ───────────────────────────────────────


def test_eine_aenderung_legt_den_stand_davor_ab(db: Session, regular_user: User) -> None:
    _erlauben(db, regular_user)
    row = _anlegen(db, regular_user, "Der Benutzer spielt abends.", titel="Spielzeit")

    row, wert = ai_memory_service.erinnerung_aendern(
        db, user=regular_user, entry_id=row.id, text="Der Benutzer spielt morgens.",
        erwartete_fassung=1,
    )

    assert (wert, row.fassung) == ("Der Benutzer spielt morgens.", 2)
    [fassung] = ai_memory_service.fassungen(db, user=regular_user, entry_id=row.id)
    assert (fassung.text, fassung.titel, fassung.grund, fassung.von) == (
        "Der Benutzer spielt abends.", "Spielzeit", "bearbeitet", "user",
    )
    roh = db.execute(text(
        "SELECT id, text_encrypted, titel_encrypted FROM ai_memory_versionen"
    )).one()
    assert "abends" not in roh.text_encrypted and "Spielzeit" not in roh.titel_encrypted
    assert DisClient.decrypt(
        roh.text_encrypted,
        aad=f"msm:ai:memory:version:text:user:{regular_user.id}:{row.id}:{roh.id}",
    ) == "Der Benutzer spielt abends."

    # Dieselbe Fassung ein zweites Mal: jemand anderes war schneller.
    with pytest.raises(HTTPException) as veraltet:
        ai_memory_service.erinnerung_aendern(
            db, user=regular_user, entry_id=row.id, text="Der Benutzer spielt mittags.",
            erwartete_fassung=1,
        )
    assert veraltet.value.status_code == 409

    # Derselbe Text ist keine Änderung und keine neue Fassung.
    row, _ = ai_memory_service.erinnerung_aendern(
        db, user=regular_user, entry_id=row.id, text="Der Benutzer spielt morgens.",
    )
    assert row.fassung == 2
    assert db.query(AiMemoryVersion).count() == 1


def test_eine_fruehere_fassung_kommt_zurueck_und_der_irrtum_bleibt_lesbar(
    db: Session, regular_user: User
) -> None:
    _erlauben(db, regular_user)
    row = _anlegen(db, regular_user, "Backups laufen um drei Uhr.")
    row, _ = ai_memory_service.erinnerung_aendern(
        db, user=regular_user, entry_id=row.id, text="Backups laufen um vier Uhr.",
    )
    [alt] = ai_memory_service.fassungen(db, user=regular_user, entry_id=row.id)

    row, wert = ai_memory_service.fassung_zurueckholen(
        db, user=regular_user, entry_id=row.id, fassung_id=alt.id, erwartete_fassung=2,
    )

    assert (wert, row.fassung) == ("Backups laufen um drei Uhr.", 3)
    juengste, aelteste = ai_memory_service.fassungen(db, user=regular_user, entry_id=row.id)
    assert (juengste.text, juengste.grund) == ("Backups laufen um vier Uhr.", "wiederhergestellt")
    assert aelteste.id == alt.id


def test_es_bleiben_die_juengsten_fassungen(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ai_memory_service, "MAX_VERSIONEN", 3)
    _erlauben(db, regular_user)
    row = _anlegen(db, regular_user, "Stand 0.")
    for nummer in range(1, 6):
        row, _ = ai_memory_service.erinnerung_aendern(
            db, user=regular_user, entry_id=row.id, text=f"Stand {nummer}.",
        )

    texte = [f.text for f in ai_memory_service.fassungen(db, user=regular_user, entry_id=row.id)]

    assert texte == ["Stand 4.", "Stand 3.", "Stand 2."]
    assert db.query(AiMemoryVersion).count() == 3


def test_jeder_erlaubte_wert_passt_in_seine_spalte(db: Session, regular_user: User) -> None:
    """Was ein CHECK erlaubt, lässt sich auch speichern.

    `grund` war zu kurz für „wiederhergestellt“, und erst das erste
    Zurückholen einer Fassung hat es gezeigt. Die meisten Arten, Quellen und
    Gründe schreibt erst die Pflege; hier steht jeder Wert einmal in
    PostgreSQL, bevor ihn jemand braucht.
    """
    _erlauben(db, regular_user)
    row = _anlegen(db, regular_user, "Backups laufen um drei Uhr.")
    for spalte, werte in (
        ("art", ai_memory_service.ARTEN),
        ("quelle", ai_memory_service.QUELLEN),
        ("status", ai_memory_service.ANSICHTEN),
    ):
        for wert in werte:
            setattr(row, spalte, wert)
            db.flush()
    for grund in ai_memory_service.GRUENDE:
        for von in ("user", "ai"):
            ai_memory_service._fassung_ablegen(
                db, row, "Backups laufen um zwei Uhr.", "Backups", grund=grund, von=von,
            )
    db.commit()

    assert {f.grund for f in db.query(AiMemoryVersion)} == set(ai_memory_service.GRUENDE)


def test_die_ki_ordnet_zu_aber_ueberschreibt_den_menschen_nicht(
    db: Session, regular_user: User
) -> None:
    """Was ein Mensch gesagt hat, ändert die KI nur ausdrücklich.

    Ein Thema oder eine Wichtigkeit zuzuordnen sagt dagegen nichts anderes als
    der Mensch — das darf die Pflege im Hintergrund ohne Rückfrage.
    """
    _erlauben(db, regular_user)
    row = _anlegen(db, regular_user, "Der Benutzer mag keine Abkürzungen.")

    with pytest.raises(HTTPException) as abgewiesen:
        ai_memory_service.erinnerung_aendern(
            db, user=regular_user, entry_id=row.id, text="Der Benutzer mag Abkürzungen.",
            von="ai", grund="aktualisiert",
        )
    assert abgewiesen.value.status_code == 409

    row, wert = ai_memory_service.erinnerung_aendern(
        db, user=regular_user, entry_id=row.id, thema="Stil", wichtigkeit=4,
        von="ai", grund="aktualisiert",
    )
    assert wert == "Der Benutzer mag keine Abkürzungen."
    assert (row.origin, row.wichtigkeit, row.fassung) == ("user", 4, 2)
    assert row.thema_id is not None
    assert db.query(AiMemoryVersion).count() == 0


def test_vergessenes_laesst_sich_nicht_aendern(db: Session, regular_user: User) -> None:
    _erlauben(db, regular_user)
    row = _anlegen(db, regular_user, "Ein Satz.")
    ai_memory_service.erinnerung_vergessen(db, user=regular_user, entry_id=row.id)

    with pytest.raises(HTTPException) as fehler:
        ai_memory_service.erinnerung_aendern(
            db, user=regular_user, entry_id=row.id, text="Ein anderer Satz.",
        )
    assert fehler.value.status_code == 409


# ── Vergessen ───────────────────────────────────────────────────────────


def test_vergessenes_gilt_nirgends_mehr_und_steht_in_der_eigenen_ansicht(
    db: Session, regular_user: User
) -> None:
    _erlauben(db, regular_user)
    weg = _anlegen(db, regular_user, "Der Benutzer wohnt in Hamburg.")
    _anlegen(db, regular_user, "Der Benutzer spielt Schach.")

    ai_memory_service.erinnerung_vergessen(db, user=regular_user, entry_id=weg.id)

    block = ai_memory_service.provider_memory_context(db, regular_user, query="Hamburg")
    assert "Hamburg" not in block and "Schach" in block
    treffer = ai_memory_service.search_entries(db, regular_user, query="Hamburg")
    assert weg.id not in {row.id for row, _wert, _score in treffer}
    assert ai_memory_service.personal_entries(db, regular_user).gesamt == 1

    vergessen = ai_memory_service.personal_entries(db, regular_user, status="vergessen")
    assert [(row.id, wert) for row, wert in vergessen.eintraege] == [
        (weg.id, "Der Benutzer wohnt in Hamburg.")
    ]
    assert vergessen.eintraege[0][0].vergessen_am is not None
    with pytest.raises(HTTPException) as unbekannt:
        ai_memory_service.personal_entries(db, regular_user, status="geloescht")
    assert unbekannt.value.status_code == 422


def test_nach_dreissig_tagen_ist_vergessenes_weg(db: Session, regular_user: User) -> None:
    """Die Frist ist die einzige Stelle, an der etwas von selbst verschwindet.

    Mit ihm gehen seine früheren Fassungen und ein Thema, unter dem danach
    nichts mehr steht. Ein Thema, das gerade erst entstanden ist, bleibt: sein
    Schreiber will es womöglich gleich benutzen.
    """
    _erlauben(db, regular_user)
    alt = _anlegen(db, regular_user, "Alter Satz.", thema="Altes")
    ai_memory_service.erinnerung_aendern(
        db, user=regular_user, entry_id=alt.id, text="Alter Satz, geändert.",
    )
    jung = _anlegen(db, regular_user, "Junger Satz.")
    bleibt = _anlegen(db, regular_user, "Geltender Satz.", thema="Bleibt")
    ai_memory_service.erinnerung_vergessen(db, user=regular_user, entry_id=alt.id)
    ai_memory_service.erinnerung_vergessen(db, user=regular_user, entry_id=jung.id)
    frisch = ai_memory_service.thema_fuer(
        db, identity=f"user:{regular_user.id}", owner_id=regular_user.id,
        server_id=None, team_id=None, name="Frisch",
    )
    jetzt = datetime.now(timezone.utc)
    db.query(AiMemoryEntry).filter(AiMemoryEntry.id == alt.id).update(
        {"vergessen_am": jetzt - timedelta(days=31)}
    )
    db.query(AiMemoryEntry).filter(AiMemoryEntry.id == jung.id).update(
        {"vergessen_am": jetzt - timedelta(days=29)}
    )
    db.query(AiMemoryTopic).filter(AiMemoryTopic.id == alt.thema_id).update(
        {"updated_at": jetzt - timedelta(hours=2)}
    )
    db.commit()
    alt_thema, frisch_id, bleibt_thema = alt.thema_id, frisch.id, bleibt.thema_id

    assert ai_memory_service.vergessene_aufraeumen(db) == 1

    assert {row.id for row in db.query(AiMemoryEntry).all()} == {jung.id, bleibt.id}
    assert db.query(AiMemoryVersion).count() == 0
    themen = {t.id for t in db.query(AiMemoryTopic).all()}
    assert themen == {frisch_id, bleibt_thema} and alt_thema not in themen


# ── Rechte ──────────────────────────────────────────────────────────────


def test_eine_fremde_erinnerung_gibt_es_fuer_niemand_sonst(
    db: Session, regular_user: User
) -> None:
    """Ob es eine fremde Erinnerung mit dieser Kennung gibt, sagt keine Antwort."""
    _erlauben(db, regular_user)
    fremder = _zweiter(db)
    _erlauben(db, fremder)
    row = _anlegen(db, regular_user, "Ein privater Satz.")

    for versuch in (
        lambda: ai_memory_service.erinnerung_aendern(
            db, user=fremder, entry_id=row.id, text="Übernommen."
        ),
        lambda: ai_memory_service.erinnerung_vergessen(db, user=fremder, entry_id=row.id),
        lambda: ai_memory_service.erinnerung_zurueckholen(db, user=fremder, entry_id=row.id),
        lambda: ai_memory_service.fassungen(db, user=fremder, entry_id=row.id),
        lambda: ai_memory_service.delete_entry(db, fremder, row.id),
        lambda: ai_memory_service.erinnerung_aendern(
            db, user=fremder, entry_id="keine-kennung", text="x"
        ),
    ):
        with pytest.raises(HTTPException) as fehler:
            versuch()
        assert fehler.value.status_code == 404
    assert db.get(AiMemoryEntry, row.id).status == "aktiv"


def test_endgueltig_loeschen_nimmt_die_fassungen_mit(db: Session, regular_user: User) -> None:
    _erlauben(db, regular_user)
    row = _anlegen(db, regular_user, "Erster Stand.")
    ai_memory_service.erinnerung_aendern(
        db, user=regular_user, entry_id=row.id, text="Zweiter Stand.",
    )

    ai_memory_service.delete_entry(db, regular_user, row.id)

    assert db.query(AiMemoryEntry).count() == 0
    assert db.query(AiMemoryVersion).count() == 0


# ── Altbestand neben Sätzen ─────────────────────────────────────────────


def test_saetze_ohne_namen_bekommen_keinen_namensindex(db: Session, regular_user: User) -> None:
    """Der Nachzug des Namensindex fasst nur Zeilen mit Namen an.

    Sonst bekäme jede namenlose Zeile den Index des leeren Namens, und die
    zweite scheiterte am UNIQUE — das Merken über den alten Weg ginge in dem
    Bereich nicht mehr.
    """
    _erlauben(db, regular_user)
    _anlegen(db, regular_user, "Erster Satz.")
    _anlegen(db, regular_user, "Zweiter Satz.")

    ai_memory_service.upsert_entry(
        db, user=regular_user, scope="user", server_id=None,
        key="sprache", value="Deutsch", origin="ai",
    )

    zeilen = db.query(AiMemoryEntry).all()
    mit_namen = [row for row in zeilen if row.key_encrypted is not None]
    ohne_namen = [row for row in zeilen if row.key_encrypted is None]
    assert [row.key for row in mit_namen] == ["sprache"]
    assert mit_namen[0].key_index is not None
    assert len(ohne_namen) == 2
    assert all(row.key_index is None for row in ohne_namen)


# ── Über die Routen ─────────────────────────────────────────────────────


def test_der_ganze_weg_ueber_die_routen(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict
) -> None:
    _erlauben(db, regular_user)
    kopf = _csrf(user_cookies)

    angelegt = client.post(
        "/api/ai/memory",
        json={"scope": "user", "text": "Der Benutzer arbeitet in Schichten.",
              "titel": "Arbeitszeit", "thema": "Arbeit"},
        cookies=user_cookies, headers=kopf,
    )
    assert angelegt.status_code == 201
    eintrag = angelegt.json()
    assert (eintrag["titel"], eintrag["thema"]["name"], eintrag["fassung"]) == (
        "Arbeitszeit", "Arbeit", 1,
    )
    kennung = eintrag["id"]

    veraltet = client.patch(
        f"/api/ai/memory/{kennung}", json={"text": "Neu.", "fassung": 7},
        cookies=user_cookies, headers=kopf,
    )
    assert veraltet.status_code == 409

    geaendert = client.patch(
        f"/api/ai/memory/{kennung}",
        json={"text": "Der Benutzer arbeitet nur noch tagsüber.", "titel": None, "fassung": 1},
        cookies=user_cookies, headers=kopf,
    )
    assert geaendert.status_code == 200
    # `titel: null` entfernt den Titel; das nicht genannte Thema bleibt.
    assert (geaendert.json()["titel"], geaendert.json()["thema"]["name"]) == (None, "Arbeit")
    assert geaendert.json()["fassung"] == 2

    fassungen = client.get(f"/api/ai/memory/{kennung}/fassungen", cookies=user_cookies)
    assert fassungen.status_code == 200
    [alt] = fassungen.json()
    assert (alt["text"], alt["titel"], alt["grund"]) == (
        "Der Benutzer arbeitet in Schichten.", "Arbeitszeit", "bearbeitet",
    )

    zurueck = client.post(
        f"/api/ai/memory/{kennung}/fassungen/{alt['id']}/zurueckholen",
        json={"fassung": 2}, cookies=user_cookies, headers=kopf,
    )
    assert zurueck.status_code == 200
    assert (zurueck.json()["value"], zurueck.json()["titel"]) == (
        "Der Benutzer arbeitet in Schichten.", "Arbeitszeit",
    )

    themen = client.get("/api/ai/memory/personal/themen", cookies=user_cookies)
    assert [(t["name"], t["anzahl"]) for t in themen.json()] == [("Arbeit", 1)]
    gefiltert = client.get(
        f"/api/ai/memory/personal?thema={themen.json()[0]['id']}", cookies=user_cookies
    )
    assert [e["id"] for e in gefiltert.json()["entries"]] == [kennung]
    leer = client.get(
        "/api/ai/memory/personal?thema=00000000-0000-0000-0000-000000000000",
        cookies=user_cookies,
    )
    assert leer.json()["entries"] == []

    weg = client.delete(f"/api/ai/memory/{kennung}", cookies=user_cookies, headers=kopf)
    assert weg.status_code == 204
    assert client.get(
        f"/api/ai/memory/{kennung}/fassungen", cookies=user_cookies
    ).status_code == 404


def test_vergessenes_ueber_die_route_ansehen_und_zurueckholen(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict
) -> None:
    _erlauben(db, regular_user, grenze=1)
    row = _anlegen(db, regular_user, "Der Benutzer fährt Rad.")
    ai_memory_service.erinnerung_vergessen(db, user=regular_user, entry_id=row.id)
    _anlegen(db, regular_user, "Der Benutzer läuft.")

    ansicht = client.get("/api/ai/memory/personal?status=vergessen", cookies=user_cookies)
    assert ansicht.status_code == 200
    [eintrag] = ansicht.json()["entries"]
    assert (eintrag["id"], eintrag["status"]) == (row.id, "vergessen")
    assert eintrag["vergessen_am"] is not None
    assert client.get(
        "/api/ai/memory/personal?status=irgendwas", cookies=user_cookies
    ).status_code == 422

    voll = client.post(
        f"/api/ai/memory/{row.id}/zurueckholen", cookies=user_cookies,
        headers=_csrf(user_cookies),
    )
    assert voll.status_code == 409
    assert "1 von 1" in voll.json()["detail"]

    neu_angelegt = client.post(
        "/api/ai/memory", json={"scope": "user", "text": "Der Benutzer schwimmt."},
        cookies=user_cookies, headers=_csrf(user_cookies),
    )
    assert neu_angelegt.status_code == 409


def test_der_export_enthaelt_titel_thema_und_fassungen_im_klartext(
    db: Session, regular_user: User
) -> None:
    """Auskunft heißt lesbar — auch für das, was mit Gedächtnis v2 dazukam."""
    from services import datenexport_service

    _erlauben(db, regular_user)
    row = _anlegen(db, regular_user, "Erster Stand.", titel="Überschrift", thema="Ordner")
    ai_memory_service.erinnerung_aendern(
        db, user=regular_user, entry_id=row.id, text="Zweiter Stand.",
    )

    paket = datenexport_service.exportieren(db, regular_user.id, mit_geheimnissen=False)

    tabellen = paket["tabellen"]
    [eintrag] = tabellen["ai_memory_entries"]
    assert (eintrag["value_encrypted"], eintrag["titel_encrypted"]) == (
        "Zweiter Stand.", "Überschrift",
    )
    assert [t["name_encrypted"] for t in tabellen["ai_memory_themen"]] == ["Ordner"]
    [fassung] = tabellen["ai_memory_versionen"]
    assert (fassung["text_encrypted"], fassung["titel_encrypted"]) == (
        "Erster Stand.", "Überschrift",
    )
    assert paket["manifest"]["unlesbare_werte"] == 0


# ── Befunde der Prüfung vom 06.10.2026 ──────────────────────────────────


def test_zwei_aenderungen_zugleich_verlieren_keinen_stand(
    db: Session, regular_user: User
) -> None:
    """Wer einen veralteten Stand ändert, bekommt 409 — auch im selben Augenblick.

    Bis 06.10.2026 las `_zeile` die Erinnerung aus der Sitzung, ohne Sperre.
    Hatte eine zweite Sitzung inzwischen geschrieben, bestand die alte
    `fassung` die Prüfung, und der Text der zweiten Sitzung war weg — auch
    aus den Fassungen.
    """
    import database as db_module

    _erlauben(db, regular_user)
    row = _anlegen(db, regular_user, "Backups laufen um drei Uhr.")
    gesehen = row.fassung

    zweite = db_module.SessionLocal()
    try:
        ai_memory_service.erinnerung_aendern(
            zweite, user=zweite.get(User, regular_user.id), entry_id=row.id,
            text="Backups laufen um vier Uhr.", erwartete_fassung=gesehen,
        )
    finally:
        zweite.close()

    with pytest.raises(HTTPException) as fehler:
        ai_memory_service.erinnerung_aendern(
            db, user=regular_user, entry_id=row.id,
            text="Backups laufen um fünf Uhr.", erwartete_fassung=gesehen,
        )
    assert fehler.value.status_code == 409
    db.rollback()

    seite = ai_memory_service.personal_entries(db, regular_user)
    assert [wert for _row, wert in seite.eintraege] == ["Backups laufen um vier Uhr."]
    assert [f.text for f in ai_memory_service.fassungen(
        db, user=regular_user, entry_id=row.id,
    )] == ["Backups laufen um drei Uhr."]


def test_ein_fremdes_thema_bleibt_verschlossen(db: Session, regular_user: User) -> None:
    """Die eigene Erinnerung zeigt keinen Themennamen aus einem fremden Bereich.

    Gestellt wird der Fall, gegen den die AAD steht: jemand mit
    Schreibzugriff auf die Datenbank hängt seine Erinnerung an das Thema
    eines anderen. Der Name des fremden Themas bleibt zu.
    """
    jonas = _zweiter(db)
    _erlauben(db, regular_user)
    _erlauben(db, jonas)
    fremd = _anlegen(db, jonas, "Jonas plant eine Reise.", thema="Geheimprojekt")
    eigen = _anlegen(db, regular_user, "Der Benutzer trinkt Tee.")
    db.execute(
        text("UPDATE ai_memory_entries SET thema_id = :thema WHERE id = :id"),
        {"thema": fremd.thema_id, "id": eigen.id},
    )
    db.commit()

    seite = ai_memory_service.personal_entries(db, regular_user)
    assert "Geheimprojekt" not in seite.themen.values()
    assert ai_memory_service.personal_themen(db, regular_user) == []
    db.expire_all()
    assert ai_memory_service.themennamen(
        db, [(fremd.thema_id, eigen.scope_identity)]
    ) == {}


def _beim_loeschen(db: Session, modell, aktion) -> None:
    """Führt ``aktion`` aus, kurz bevor diese Sitzung Zeilen von ``modell`` löscht.

    So entsteht genau die Lücke zwischen Auswahl und Löschen, in der ein
    anderer Schreiber zum Zug kommt.
    """
    from sqlalchemy import event

    erledigt: list[bool] = []

    def dazwischen(zustand) -> None:
        if (
            not erledigt and zustand.is_delete and zustand.bind_mapper is not None
            and zustand.bind_mapper.class_ is modell
        ):
            erledigt.append(True)
            aktion()

    event.listen(db, "do_orm_execute", dazwischen)
    try:
        ai_memory_service.vergessene_aufraeumen(db)
    finally:
        event.remove(db, "do_orm_execute", dazwischen)
    assert erledigt, "der Löschschritt kam nie"


def test_eine_zurueckgeholte_erinnerung_ueberlebt_das_aufraeumen(
    db: Session, regular_user: User
) -> None:
    import database as db_module

    _erlauben(db, regular_user)
    row = _anlegen(db, regular_user, "Backups laufen um drei Uhr.")
    ai_memory_service.erinnerung_vergessen(db, user=regular_user, entry_id=row.id)
    db.execute(
        text("UPDATE ai_memory_entries SET vergessen_am = :alt WHERE id = :id"),
        {"alt": datetime.now(timezone.utc) - timedelta(days=31), "id": row.id},
    )
    db.commit()

    def zurueckholen() -> None:
        zweite = db_module.SessionLocal()
        try:
            ai_memory_service.erinnerung_zurueckholen(
                zweite, user=zweite.get(User, regular_user.id), entry_id=row.id,
            )
        finally:
            zweite.close()

    _beim_loeschen(db, AiMemoryEntry, zurueckholen)

    db.expire_all()
    geblieben = db.get(AiMemoryEntry, row.id)
    assert geblieben is not None and geblieben.status == "aktiv"


def test_ein_wieder_benutztes_thema_ueberlebt_das_aufraeumen(
    db: Session, regular_user: User
) -> None:
    import database as db_module

    _erlauben(db, regular_user)
    alt = _anlegen(db, regular_user, "Der Benutzer fährt Rad.", thema="Alltag")
    thema_id = alt.thema_id
    ai_memory_service.delete_entry(db, regular_user, alt.id)
    db.execute(
        text("UPDATE ai_memory_themen SET updated_at = :alt WHERE id = :id"),
        {"alt": datetime.now(timezone.utc) - timedelta(hours=2), "id": thema_id},
    )
    db.commit()
    neu: list[str] = []

    def wieder_benutzen() -> None:
        zweite = db_module.SessionLocal()
        try:
            row, _ = ai_memory_service.erinnerung_anlegen(
                zweite, user=zweite.get(User, regular_user.id), scope="user",
                text="Der Benutzer geht zu Fuß zur Arbeit.", thema="alltag",
            )
            neu.append(row.id)
        finally:
            zweite.close()

    _beim_loeschen(db, AiMemoryTopic, wieder_benutzen)

    db.expire_all()
    assert db.get(AiMemoryTopic, thema_id) is not None
    assert db.get(AiMemoryEntry, neu[0]).thema_id == thema_id


def test_der_import_nennt_seine_quelle_und_vergessenes_haelt_ihn_nicht_auf(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict
) -> None:
    """Was über den Import kam, stand in der Ansicht als "Von Hand eingetragen".

    Und ein vergessener Satz hält einen neuen nicht auf. Bis Stufe 3 hing das
    am Namen: ein vergessener Eintrag unter demselben Namen hieß in der
    Vorschau "neu" und wurde dann als "vorhanden" übersprungen. Seither
    kommen Sätze ohne Namen; vergessen bleibt vergessen, neu ist neu.
    """
    _erlauben(db, regular_user)
    row = _anlegen(db, regular_user, "Der Benutzer trinkt Kaffee.")
    ai_memory_service.erinnerung_vergessen(db, user=regular_user, entry_id=row.id)

    antwort = client.post(
        "/api/ai/memory/import",
        json={"scope": "user", "items": [
            {"text": "Der Benutzer trinkt Kaffee."},
            {"text": "Der Benutzer spielt Schach im Verein."},
        ]},
        cookies=user_cookies, headers=_csrf(user_cookies),
    )

    assert antwort.status_code == 200, antwort.text
    assert (antwort.json()["imported_count"], antwort.json()["skipped_count"]) == (2, 0)
    db.expire_all()
    zeilen = db.query(AiMemoryEntry).filter(AiMemoryEntry.owner_user_id == regular_user.id).all()
    assert sorted((z.status, z.quelle) for z in zeilen if z.id != row.id) == [
        ("aktiv", "import"), ("aktiv", "import"),
    ]
    assert db.get(AiMemoryEntry, row.id).status == "vergessen"


def test_eine_einzelne_erinnerung_kommt_auch_vergessen_aber_nur_zu_ihr_selbst(
    client: TestClient, db: Session, regular_user: User, user_cookies: dict
) -> None:
    """Nach einem Konflikt holt die Ansicht die Erinnerung selbst.

    Die Liste ist nach dem letzten Gebrauch geordnet: nach dem Neuladen kann
    die Erinnerung auf einer anderen Seite stehen. Ist sie inzwischen
    vergessen, legt die Ansicht den Text neu an, statt ins Leere zu speichern
    — dafür muss sie den Status sehen.
    """
    _erlauben(db, regular_user)
    row = _anlegen(db, regular_user, "Backups laufen um drei Uhr.")

    eigene = client.get(f"/api/ai/memory/{row.id}", cookies=user_cookies)
    assert eigene.status_code == 200, eigene.text
    assert (eigene.json()["value"], eigene.json()["status"]) == ("Backups laufen um drei Uhr.", "aktiv")

    ai_memory_service.erinnerung_vergessen(db, user=regular_user, entry_id=row.id)
    vergessen = client.get(f"/api/ai/memory/{row.id}", cookies=user_cookies)
    assert vergessen.json()["status"] == "vergessen"

    fremder = _zweiter(db)
    _erlauben(db, fremder)
    fremde = _anlegen(db, fremder, "Ein privater Satz.")
    assert client.get(f"/api/ai/memory/{fremde.id}", cookies=user_cookies).status_code == 404
    # Die festen Pfade daneben treffen weiter ihre eigenen Routen.
    assert client.get("/api/ai/memory/preference", cookies=user_cookies).status_code == 200
    assert client.get("/api/ai/memory/personal", cookies=user_cookies).status_code == 200
