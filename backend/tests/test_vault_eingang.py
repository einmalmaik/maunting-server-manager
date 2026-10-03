"""Posteingang des Tresors (Kamera-Sicherung bei gesperrtem Tresor, 10/2026).

Der Server haelt dort nur Chiffrat, das ein Geraet ablegt und eine App beim
Entsperren abholt. Gepruefte Grenzen: nur der genannte Bucket des Kontos,
nach dem Zuruecksetzen 410 und nichts mehr da, Zahl und Speicher begrenzt,
fremde Konten sehen und loeschen nichts.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from models import VaultEingang
from services import vault_blob_service, vault_service
from tests.test_vault_blobs import BUCKET, _speicher_der_rolle, als, blob_dir, konten  # noqa: F401  (Fixtures)


def _ablegen(client, inhalt: str = "chiffrat", eingang_id: str | None = None):
    eingang_id = eingang_id or str(uuid.uuid4())
    return eingang_id, client.post("/api/vault/eingang", json={"id": eingang_id, "ciphertext": inhalt})


def test_ablegen_lesen_loeschen(als, konten):
    client = als(konten[0])
    eingang_id, antwort = _ablegen(client, "umschlag-1")
    assert antwort.status_code == 201, antwort.text

    liste = client.get("/api/vault/eingang").json()
    assert [(e["id"], e["ciphertext"]) for e in liste["eintraege"]] == [(eingang_id, "umschlag-1")]
    assert liste["weiter"] is None

    assert client.delete(f"/api/vault/eingang/{eingang_id}").status_code == 204
    assert client.get("/api/vault/eingang").json()["eintraege"] == []
    # Zwei Geraete uebernehmen zugleich: das zweite Loeschen ist kein Fehler.
    assert client.delete(f"/api/vault/eingang/{eingang_id}").status_code == 204


def test_fremdes_konto_sieht_und_loescht_nichts(als, konten, db):
    eingang_id, antwort = _ablegen(als(konten[0]))
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
    assert _ablegen(client)[1].status_code == 400
    assert client.get("/api/vault/eingang").status_code == 400


def test_nach_dem_zuruecksetzen_410_und_der_posteingang_ist_weg(als, konten, db):
    client = als(konten[0])
    assert _ablegen(client)[1].status_code == 201
    vault_service.tresor_zuruecksetzen(db, vault_service.tresor_konto(konten[0].id))
    db.commit()

    assert db.execute(text("SELECT count(*) FROM vault_eingang")).scalar() == 0
    antwort = _ablegen(client)[1]
    assert antwort.status_code == 410
    assert antwort.json()["detail"]["code"] == "VAULT_ZURUECKGESETZT"


def test_wiederholung_ist_kein_fehler_fremder_inhalt_schon(als, konten):
    client = als(konten[0])
    eingang_id, erst = _ablegen(client, "gleich")
    assert erst.status_code == 201
    assert _ablegen(client, "gleich", eingang_id)[1].status_code == 201
    assert _ablegen(client, "anders", eingang_id)[1].status_code == 409
    # Dieselbe Kennung aus einem anderen Bucket ueberschreibt nichts.
    assert _ablegen(als(konten[1]), "gleich", eingang_id)[1].status_code == 409
    assert [e["ciphertext"] for e in als(konten[0]).get("/api/vault/eingang").json()["eintraege"]] == ["gleich"]


def test_zaehlt_zum_speicher(als, konten, db):
    client = als(konten[0])
    _speicher_der_rolle(db, konten[0], 1000)
    assert _ablegen(client, "x" * 600)[1].status_code == 201
    antwort = _ablegen(client, "y" * 600)[1]
    assert antwort.status_code == 507
    assert antwort.json()["detail"]["code"] == "VAULT_SPEICHER_VOLL"
    assert client.get("/api/vault/speicher").json()["belegt"] == 600


def test_zahl_ist_begrenzt(als, konten, monkeypatch):
    monkeypatch.setattr(vault_blob_service, "EINGANG_HOECHSTENS", 2)
    client = als(konten[0])
    assert _ablegen(client)[1].status_code == 201
    assert _ablegen(client)[1].status_code == 201
    antwort = _ablegen(client)[1]
    assert antwort.status_code == 507
    assert antwort.json()["detail"]["code"] == "VAULT_EINGANG_VOLL"


def test_seiten(als, konten, monkeypatch):
    monkeypatch.setattr(vault_blob_service, "EINGANG_SEITE", 2)
    client = als(konten[0])
    ids = sorted(_ablegen(client)[0] for _ in range(5))
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
def test_nur_kurzes_druckbares_chiffrat(als, konten, inhalt):
    assert _ablegen(als(konten[0]), inhalt)[1].status_code == 422


def test_kennung_muss_eine_uuid_sein(als, konten):
    client = als(konten[0])
    antwort = client.post("/api/vault/eingang", json={"id": "../../etc", "ciphertext": "x"})
    assert antwort.status_code == 422
