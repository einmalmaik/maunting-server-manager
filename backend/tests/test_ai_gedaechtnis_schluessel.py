"""Der Name eines Gedaechtniseintrags steht nicht mehr im Klartext.

Bis 26.09.2026 lag ``ai_memory_entries.key`` lesbar in der Tabelle
("zeitzone", "character_pairing") und verriet oft schon den Inhalt. Heute
steht dort Chiffrat, gesucht wird ueber einen HMAC-Index. Geprueft wird am
Rohwert in der Datenbank, und dass Suche, Eindeutigkeit und Altbestand
weiter stimmen.
"""

from __future__ import annotations

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from models import AiMemoryEntry, User
from services import ai_memory_service
from services.dis_client import DisClient
from tests.test_ai_memory_management import _allow, _remember, _user


def _roh(db: Session, spalte: str, zeilen_id: str):
    return db.execute(
        text(f"SELECT {spalte} FROM ai_memory_entries WHERE id = :id"), {"id": zeilen_id}
    ).scalar_one()


def _zeile(db: Session, name: str) -> AiMemoryEntry:
    (zeile,) = [r for r in db.query(AiMemoryEntry).all() if r.key == name]
    return zeile


def test_der_name_steht_als_chiffrat_in_der_tabelle(db: Session, regular_user: User) -> None:
    _allow(db, regular_user, "ai.memory.use")
    _remember(db, regular_user, "character_pairing", "Aria und Kael")

    zeile = _zeile(db, "character_pairing")
    roh = _roh(db, "key_encrypted", zeile.id)
    assert "character_pairing" not in roh
    assert DisClient.ist_verschluesselt(roh)
    assert len(_roh(db, "key_index", zeile.id)) == 64
    # Die Spalte heisst nicht mehr `key`: niemand liest dort aus Versehen
    # einen Klartext heraus, den es nicht mehr gibt.
    assert "key" not in {s["name"] for s in inspect(db.get_bind()).get_columns("ai_memory_entries")}


def test_derselbe_name_bei_zwei_benutzern_hat_verschiedene_indizes(db: Session, regular_user: User) -> None:
    """Sonst saehe man in der Tabelle, wer sich dasselbe gemerkt hat."""
    anderer = _user(db, "zweiter")
    _allow(db, regular_user, "ai.memory.use")
    _allow(db, anderer, "ai.memory.use")
    _remember(db, regular_user, "zeitzone", "Europe/Berlin")
    _remember(db, anderer, "zeitzone", "Europe/Vienna")

    indizes = {r.key_index for r in db.query(AiMemoryEntry).all()}
    assert len(indizes) == 2


def test_ueberschreiben_findet_den_eintrag_ueber_den_index(db: Session, regular_user: User) -> None:
    _allow(db, regular_user, "ai.memory.use")
    _remember(db, regular_user, "ram.bevorzugt", "8 GB")
    _remember(db, regular_user, "ram.bevorzugt", "16 GB")

    assert db.query(AiMemoryEntry).count() == 1
    assert ai_memory_service.list_entries(db, regular_user, "user", None)[0][1] == "16 GB"


def test_altbestand_wird_gefunden_statt_verdoppelt(db: Session, regular_user: User) -> None:
    """Eine Zeile von vor der Umstellung: Klartextname, kein Index.

    Wer sie ueberschreibt, bevor der Nachzug beim Start bei ihr war, muss sie
    treffen. Sonst stuende derselbe Name zweimal im Bereich.
    """
    _allow(db, regular_user, "ai.memory.use")
    _remember(db, regular_user, "zeitzone", "Europe/Berlin")
    zeile = _zeile(db, "zeitzone")
    db.execute(
        text("UPDATE ai_memory_entries SET key_encrypted = 'zeitzone', key_index = NULL WHERE id = :id"),
        {"id": zeile.id},
    )
    db.commit()
    db.expire_all()

    _remember(db, regular_user, "zeitzone", "Europe/Vienna")

    assert db.query(AiMemoryEntry).count() == 1
    assert DisClient.ist_verschluesselt(_roh(db, "key_encrypted", zeile.id))
    assert _roh(db, "key_index", zeile.id) is not None


def test_der_nachzug_beim_start_erfasst_jeden_bereich(db: Session, regular_user: User) -> None:
    _allow(db, regular_user, "ai.memory.use")
    _remember(db, regular_user, "a.eins", "1")
    _remember(db, regular_user, "b.zwei", "2")
    db.execute(text("UPDATE ai_memory_entries SET key_encrypted = 'alt', key_index = NULL"))
    db.execute(text("UPDATE ai_memory_entries SET key_encrypted = key_encrypted || id"))
    db.commit()
    db.expire_all()

    assert ai_memory_service.schluessel_nachziehen(db) == 1

    for zeile in db.query(AiMemoryEntry).all():
        assert DisClient.ist_verschluesselt(_roh(db, "key_encrypted", zeile.id))
        assert _roh(db, "key_index", zeile.id) is not None
    assert ai_memory_service.schluessel_nachziehen(db) == 0


def test_viele_namen_kosten_einen_sidecar_aufruf(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Einzeln entschluesselt kosteten 5.000 Namen rund 4 s."""
    _allow(db, regular_user, "ai.memory.use")
    for nummer in range(12):
        _remember(db, regular_user, f"eintrag.{nummer}", f"Wert {nummer}")
    db.expire_all()
    zeilen = db.query(AiMemoryEntry).all()

    einzeln = []
    echt = DisClient.decrypt
    monkeypatch.setattr(
        DisClient, "decrypt",
        staticmethod(lambda c, aad=None: einzeln.append(aad) or echt(c, aad=aad)),
    )
    ai_memory_service._schluessel_laden(zeilen)
    namen = {zeile.key for zeile in zeilen}

    assert namen == {f"eintrag.{n}" for n in range(12)}
    assert [aad for aad in einzeln if aad and ":key:" in aad] == []


def test_ein_sql_vergleich_auf_den_namen_scheitert_laut() -> None:
    """Mit einer schlichten Property ergab `AiMemoryEntry.key == "x"` still
    `False`, also eine leere Trefferliste, und niemand haette es gemerkt."""
    with pytest.raises(AttributeError, match="schluessel_bedingung"):
        AiMemoryEntry.key == "zeitzone"  # noqa: B015


def test_tote_metadaten_sind_weg(db: Session) -> None:
    pruefer = inspect(db.get_bind())
    assert "embedding_json" not in {s["name"] for s in pruefer.get_columns("ai_memory_entries")}
    assert "updated_at" not in {s["name"] for s in pruefer.get_columns("ai_memory_preferences")}
