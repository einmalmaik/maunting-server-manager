"""Posteingang des Tresors (Kamera-Sicherung bei gesperrtem Tresor, 10/2026).

Der Server haelt dort nur Chiffrat, das der Hintergrund-Job eines Telefons
mit seinem Sicherungszugang ablegt und eine App beim Entsperren abholt. Gepruefte Grenzen: nur der genannte Bucket des Kontos,
nach dem Zuruecksetzen 410 und nichts mehr da, Zahl und Speicher begrenzt,
fremde Konten sehen und loeschen nichts.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from main import app
from models import VaultEingang
from services import vault_blob_service, vault_sicherung_service, vault_service
from tests.test_vault_blobs import BUCKET, _speicher_der_rolle, als, blob_dir, konten  # noqa: F401  (Fixtures)
from tests.test_vault_sicherungszugang import _familie_anlegen


@pytest.fixture
def telefon(db, konten, als):
    """``telefon(user)`` ist der Hintergrund-Job dieses Kontos: nur der Sicherungszugang, keine Sitzung."""
    clients: dict[int, TestClient] = {}

    def fuer(user) -> TestClient:
        if user.id not in clients:
            familie = f"fam-{user.id}"
            _familie_anlegen(db, user, familie)
            token = vault_sicherung_service.anlegen(
                db, vault_service.tresor_konto(user.id), familie, vault_sicherung_service.familie_index(familie), BUCKET[user.id]
            )
            db.commit()
            client = TestClient(app)
            client.headers["X-MSM-Sicherung"] = token
            clients[user.id] = client
        return clients[user.id]

    yield fuer
    for client in clients.values():
        client.close()


def _ablegen(client, inhalt: str = "chiffrat", eingang_id: str | None = None):
    eingang_id = eingang_id or str(uuid.uuid4())
    return eingang_id, client.post("/api/vault/sicherung/eingang", json={"id": eingang_id, "ciphertext": inhalt})


def test_ablegen_lesen_loeschen(als, konten, telefon):
    client = als(konten[0])
    eingang_id, antwort = _ablegen(telefon(konten[0]), "umschlag-1")
    assert antwort.status_code == 201, antwort.text

    liste = client.get("/api/vault/eingang").json()
    assert [(e["id"], e["ciphertext"]) for e in liste["eintraege"]] == [(eingang_id, "umschlag-1")]
    assert liste["weiter"] is None

    assert client.delete(f"/api/vault/eingang/{eingang_id}").status_code == 204
    assert client.get("/api/vault/eingang").json()["eintraege"] == []
    # Zwei Geraete uebernehmen zugleich: das zweite Loeschen ist kein Fehler.
    assert client.delete(f"/api/vault/eingang/{eingang_id}").status_code == 204


def test_fremdes_konto_sieht_und_loescht_nichts(als, konten, db, telefon):
    eingang_id, antwort = _ablegen(telefon(konten[0]))
    assert antwort.status_code == 201

    zwei = als(konten[1])
    assert zwei.get("/api/vault/eingang").json()["eintraege"] == []
    assert zwei.delete(f"/api/vault/eingang/{eingang_id}").status_code == 204
    assert db.get(VaultEingang, eingang_id, populate_existing=True) is not None

    # Den fremden Bucket nennen hilft nicht: er ist nicht der des Kontos.
    zwei.headers["X-MSM-Vault-Bucket"] = BUCKET[1]
    assert zwei.get("/api/vault/eingang").status_code == 410


def test_ohne_genannten_bucket_nichts(als, konten):
    client = als(konten[0])
    del client.headers["X-MSM-Vault-Bucket"]
    assert client.get("/api/vault/eingang").status_code == 400


def test_der_browser_legt_mit_sitzung_in_den_eigenen_bucket(als, konten, db):
    """Der Browser speichert bei gesperrtem Tresor ueber den Posteingang, mit seiner Sitzung."""
    eins = als(konten[0])
    eingang_id = str(uuid.uuid4())
    assert eins.post("/api/vault/eingang", json={"id": eingang_id, "ciphertext": "zugang-1"}).status_code == 201
    assert [e["id"] for e in eins.get("/api/vault/eingang").json()["eintraege"]] == [eingang_id]

    zwei = als(konten[1])
    zwei.headers["X-MSM-Vault-Bucket"] = BUCKET[konten[0].id]
    antwort = zwei.post("/api/vault/eingang", json={"id": str(uuid.uuid4()), "ciphertext": "fremd"})
    assert antwort.status_code == 410
    del zwei.headers["X-MSM-Vault-Bucket"]
    assert zwei.post("/api/vault/eingang", json={"id": str(uuid.uuid4()), "ciphertext": "x"}).status_code == 400
    assert db.query(VaultEingang).count() == 1


def test_nach_dem_zuruecksetzen_ist_der_posteingang_weg_und_das_telefon_draussen(als, konten, db, telefon):
    client = telefon(konten[0])
    assert _ablegen(client)[1].status_code == 201
    vault_service.tresor_zuruecksetzen(db, vault_service.tresor_konto(konten[0].id))
    db.commit()

    assert db.execute(text("SELECT count(*) FROM vault_eingang")).scalar() == 0
    # Das Zuruecksetzen nimmt den Sicherungszugang mit.
    antwort = _ablegen(client)[1]
    assert antwort.status_code == 401
    assert antwort.json()["detail"]["code"] == "VAULT_SICHERUNG_UNGUELTIG"
    # Die App, die den alten Tresor noch offen hat, bekommt 410.
    antwort = als(konten[0]).get("/api/vault/eingang")
    assert antwort.status_code == 410
    assert antwort.json()["detail"]["code"] == "VAULT_ZURUECKGESETZT"


def test_wiederholung_ist_kein_fehler_fremder_inhalt_schon(als, konten, telefon):
    client = telefon(konten[0])
    eingang_id, erst = _ablegen(client, "gleich")
    assert erst.status_code == 201
    assert _ablegen(client, "gleich", eingang_id)[1].status_code == 201
    assert _ablegen(client, "anders", eingang_id)[1].status_code == 409
    # Dieselbe Kennung aus einem anderen Bucket ueberschreibt nichts.
    assert _ablegen(telefon(konten[1]), "gleich", eingang_id)[1].status_code == 409
    assert [e["ciphertext"] for e in als(konten[0]).get("/api/vault/eingang").json()["eintraege"]] == ["gleich"]


def test_zaehlt_zum_speicher(als, konten, db, telefon):
    client = telefon(konten[0])
    _speicher_der_rolle(db, konten[0], 1000)
    assert _ablegen(client, "x" * 600)[1].status_code == 201
    antwort = _ablegen(client, "y" * 600)[1]
    assert antwort.status_code == 507
    assert antwort.json()["detail"]["code"] == "VAULT_SPEICHER_VOLL"
    assert als(konten[0]).get("/api/vault/speicher").json()["belegt"] == 600


def test_zahl_ist_begrenzt(konten, monkeypatch, telefon):
    monkeypatch.setattr(vault_blob_service, "EINGANG_HOECHSTENS", 2)
    client = telefon(konten[0])
    assert _ablegen(client)[1].status_code == 201
    assert _ablegen(client)[1].status_code == 201
    antwort = _ablegen(client)[1]
    assert antwort.status_code == 507
    assert antwort.json()["detail"]["code"] == "VAULT_EINGANG_VOLL"


def test_seiten(als, konten, monkeypatch, telefon):
    monkeypatch.setattr(vault_blob_service, "EINGANG_SEITE", 2)
    ids = sorted(_ablegen(telefon(konten[0]))[0] for _ in range(5))
    client = als(konten[0])
    gesehen: list[str] = []
    nach = None
    for _ in range(4):
        antwort = client.get("/api/vault/eingang", params={"nach": nach} if nach else {}).json()
        gesehen += [e["id"] for e in antwort["eintraege"]]
        nach = antwort["weiter"]
        if nach is None:
            break
    assert gesehen == ids


@pytest.mark.parametrize(
    "inhalt",
    ["", "x" * (16 * 1024 + 1), "Umlaut ä", "Zeile\nUmbruch"],
    ids=["leer", "zu-gross", "nicht-ascii", "steuerzeichen"],
)
def test_nur_kurzes_druckbares_chiffrat(konten, inhalt, telefon):
    assert _ablegen(telefon(konten[0]), inhalt)[1].status_code == 422


def test_kennung_muss_eine_uuid_sein(konten, telefon):
    antwort = telefon(konten[0]).post("/api/vault/sicherung/eingang", json={"id": "../../etc", "ciphertext": "x"})
    assert antwort.status_code == 422
